using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using BatterMC.Protocol;

namespace MuxiTunnel.Server;

/// <summary>
/// 向控制面上报本机当前可被连到的地址。
///
/// 必须有这一步：这台机器拿到的 IPv6 是临时地址，隐私扩展开着就会轮换，RA 前缀
/// 还可能因为运营商重拨整体更换。地址写死在整合包清单里今天能连，过几小时全员
/// 失联。只有这台机器自己知道它此刻是什么地址。
/// </summary>
internal sealed class ControlPlane
{
    private static readonly TimeSpan RegisterInterval = TimeSpan.FromSeconds(30);

    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(15) };
    private readonly string _baseUrl;
    private readonly string _serverId;
    private readonly byte[] _secret;
    private readonly int _tunnelPort;
    private readonly int _mcPort;
    private readonly Func<IReadOnlyList<string>> _udpCandidates;
    private readonly SemaphoreSlim _wake = new(0, 1);

    /// <summary>
    /// 向路由器申请到的入站映射，没申请到就是 null。
    ///
    /// 这是**机会性**的一条路，不是依赖：拿到就多一条候选、省掉整轮打洞；拿不到
    /// （本机就拿不到，上游是运营商级 NAT）就当没这回事，打洞照跑。
    /// 详见 <see cref="PortMapper"/>。
    /// </summary>
    private volatile string? _mapped;

    /// <summary>
    /// 端口映射的重申间隔。租约给的是两小时，提前很多续，免得撞上路由器重启清表。
    /// </summary>
    private static readonly TimeSpan MapInterval = TimeSpan.FromMinutes(20);

    public ControlPlane(string baseUrl, string serverId, string token, int tunnelPort, int mcPort,
        Func<IReadOnlyList<string>> udpCandidates)
    {
        _udpCandidates = udpCandidates;
        _baseUrl = baseUrl.TrimEnd('/');
        _serverId = serverId;
        _tunnelPort = tunnelPort;
        _mcPort = mcPort;
        _secret = SHA256.HashData(Encoding.UTF8.GetBytes("muxi-tunnel-v1:" + token));
    }

    public async Task RunAsync(CancellationToken ct)
    {
        _ = MapPortLoopAsync(ct);
        while (!ct.IsCancellationRequested)
        {
            try
            {
                var endpoints = LocalEndpoints();
                var response = await RegisterAsync(endpoints, ct).ConfigureAwait(false);
                Program.Log($"已上报 {endpoints.Count} 个地址 + {_udpCandidates().Count} 个打洞候选，服务端看到我的公网 IP 是 " +
                            $"{response.ObservedIp}{(response.PunchRequests.Count > 0 ? $"，待打洞 {response.PunchRequests.Count} 个" : "")}");
                foreach (var punch in response.PunchRequests)
                    _ = PunchBackAsync(punch, ct);
            }
            // HttpClient 超时抛的是 TaskCanceledException，而它是 OperationCanceledException
            // 的子类。原来的过滤器把它当成"我们自己要退出"放了过去，异常直接冲出整个
            // while 循环——**一次控制面抖动就让这条循环永久停摆，直到进程重启**。
            // 实测过一次：重启控制面容器之后 agent 就再也不上报了，控制面那边显示
            // "服务端在线=False"，而 agent 进程活得好好的、端口映射循环还在转。
            // 只有 ct 真的被取消时才该退出。
            catch (Exception error) when (!Cancellation.IsShutdown(error, ct))
            {
                Program.Log($"上报失败（不影响已建立的隧道）：{error.Message}");
            }

            try { await _wake.WaitAsync(RegisterInterval, ct).ConfigureAwait(false); }
            catch (OperationCanceledException) { return; }
        }
    }

    /// <summary>
    /// 催一次上报，不等下一个周期。
    ///
    /// 打洞候选是异步探出来的，刚启动那几秒还没有。傻等 30s 的代价是实打实的：
    /// 实测 agent 22:38:13 探完，22:38:40 才轮到上报，而客户端 22:38:39 发来请求，
    /// 拿到的是"服务器侧没有可用的打洞候选"，直接退回中转——只差一秒。
    /// </summary>
    public void Nudge()
    {
        try { _wake.Release(); }
        catch (SemaphoreFullException) { /* 已经排着一次了，不用再催 */ }
    }

    /// <summary>
    /// 挑出值得对外公布的地址。
    ///
    /// 回环和链路本地地址对任何人都没用；已废弃（Deprecated）的临时地址正在退役，
    /// 公布出去等于给客户端一条马上要失效的线路。内网地址保留——同局域网的玩家
    /// 用得上，客户端那边按 LanDirect 排在最前。
    /// </summary>
    /// <summary>虚拟网卡的常见名字。公布它们的地址只会让客户端白白探测。</summary>
    private static readonly string[] VirtualHints =
    [
        "hyper-v", "virtual", "vmware", "virtualbox", "vethernet", "wsl", "docker",
        "tap-", "tun", "meta", "mihomo", "clash", "wintun", "zerotier", "tailscale",
        "loopback", "teredo", "isatap",
    ];

    /// <summary>
    /// 分类别限额，而不是一刀切取前 N 个。
    ///
    /// 这台机器上 IPv6 地址就有六七个（两个前缀 × 稳定/临时/DHCP），一刀切会把
    /// 内网地址整个挤掉，同局域网的玩家就用不上最快的那条路了。
    /// </summary>
    private const int MaxStableV6 = 3;
    private const int MaxTemporaryV6 = 1;
    private const int MaxLan = 2;

    private static bool LooksVirtual(NetworkInterface nic)
    {
        var haystack = (nic.Name + " " + nic.Description).ToLowerInvariant();
        return VirtualHints.Any(hint => haystack.Contains(hint, StringComparison.Ordinal));
    }

    /// <summary>
    /// 这些 IPv4 段绝不能公布：
    ///   198.18.0.0/15  基准测试段，clash/mihomo 的 TUN 默认就用它。玩家自己机器上
    ///                  往往也有同一个地址，公布出去等于让他连自己的 TUN。
    ///   100.64.0.0/10  运营商级 NAT，同理会和玩家侧撞车。
    ///   169.254.0.0/16 APIPA，没配到地址时的占位。
    /// </summary>
    private static bool IsUnpublishableV4(IPAddress address)
    {
        var b = address.GetAddressBytes();
        if (b[0] == 169 && b[1] == 254) return true;
        if (b[0] == 198 && b[1] is 18 or 19) return true;
        if (b[0] == 100 && b[1] is >= 64 and <= 127) return true;
        return false;
    }

    /// <summary>
    /// 挑出值得对外公布的地址。
    ///
    /// 回环和链路本地地址对任何人都没用；已废弃（Deprecated）的临时地址正在退役，
    /// 公布出去等于给客户端一条马上要失效的线路。内网地址保留——同局域网的玩家
    /// 用得上——但同一网段只留一个，这台机器上光 10.77.0.x 就绑了十几个别名。
    ///
    /// 排序上稳定后缀的 IPv6 排在临时后缀前面：临时地址会轮换，客户端拿到的候选
    /// 活得越久越好。
    /// </summary>
    private static List<string> LocalEndpoints()
    {
        var stable = new List<string>();
        var temporary = new List<string>();
        var lan = new List<string>();
        var seenSubnets = new HashSet<string>(StringComparer.Ordinal);

        foreach (var nic in NetworkInterface.GetAllNetworkInterfaces())
        {
            if (nic.OperationalStatus != OperationalStatus.Up) continue;
            if (nic.NetworkInterfaceType == NetworkInterfaceType.Loopback) continue;
            if (nic.NetworkInterfaceType == NetworkInterfaceType.Tunnel) continue;
            if (LooksVirtual(nic)) continue;

            foreach (var info in nic.GetIPProperties().UnicastAddresses)
            {
                var address = info.Address;
                if (IPAddress.IsLoopback(address)) continue;

                if (address.AddressFamily == AddressFamily.InterNetworkV6)
                {
                    if (address.IsIPv6LinkLocal || address.IsIPv6SiteLocal ||
                        address.IsIPv6Multicast || address.IsIPv6Teredo) continue;
                    if (info.AddressPreferredLifetime == 0) continue;

                    var text = Clean(address);
                    if (info.SuffixOrigin == SuffixOrigin.Random) temporary.Add(text);
                    else stable.Add(text);
                }
                else if (address.AddressFamily == AddressFamily.InterNetwork)
                {
                    if (IsUnpublishableV4(address)) continue;
                    var b = address.GetAddressBytes();
                    // 同一个 /24 只留一个，别把十几个别名全灌给客户端
                    if (!seenSubnets.Add($"{b[0]}.{b[1]}.{b[2]}")) continue;
                    lan.Add(Clean(address));
                }
            }
        }

        return stable.Take(MaxStableV6)
            .Concat(temporary.Take(MaxTemporaryV6))
            .Concat(lan.Take(MaxLan))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();
    }

    /// <summary>
    /// 定期向路由器申请入站端口映射。
    ///
    /// 只在**外部端口和隧道端口相同**时才上报。原因很实在：客户端建隧道时拨的是
    /// <c>tunnelPort</c>，不是候选自带的 port（见 MinecraftRouteProxy 里
    /// EstablishTunnelAsync 那行 <c>new IPEndPoint(route.Endpoint.Address, tunnelPort)</c>）。
    /// 路由器给了别的外部端口时，上报这条候选只会让客户端去拨一个空端口然后白等
    /// 8 秒超时——那比没有这条候选更糟。
    ///
    /// 失败不记为错误：拿不到映射是常态（本机在运营商级 NAT 后面，实测映射会"成功"
    /// 但公网进不来）。只打一行日志，好让我们攒到真实分布。
    /// </summary>
    private async Task MapPortLoopAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try
            {
                var result = await PortMapper.TryMapAsync(
                        _tunnelPort, ProtocolType.Tcp,
                        message => Program.Log("端口映射：" + message), ct)
                    .ConfigureAwait(false);

                if (result.External is { } external && external.Port == _tunnelPort)
                {
                    if (_mapped != external.ToString())
                    {
                        _mapped = external.ToString();
                        Program.Log($"端口映射：{result.Protocol} 拿到 {external}，已作为候选上报"
                                    + "（能否真被连到由客户端探测决定，不能当成一定通）");
                        Nudge();
                    }
                }
                else
                {
                    if (result.External is { } other)
                        Program.Log($"端口映射：{result.Protocol} 给的是 {other}，"
                                    + $"外部端口和隧道端口 {_tunnelPort} 不一致，不上报");
                    else
                        Program.Log("端口映射：" + result.Detail + "，继续走打洞");
                    _mapped = null;
                }
            }
            // HttpClient 超时抛的是 TaskCanceledException，而它是 OperationCanceledException
            // 的子类。原来的过滤器把它当成"我们自己要退出"放了过去，异常直接冲出整个
            // while 循环——**一次控制面抖动就让这条循环永久停摆，直到进程重启**。
            // 实测过一次：重启控制面容器之后 agent 就再也不上报了，控制面那边显示
            // "服务端在线=False"，而 agent 进程活得好好的、端口映射循环还在转。
            // 只有 ct 真的被取消时才该退出。
            catch (Exception error) when (!Cancellation.IsShutdown(error, ct))
            {
                Program.Log("端口映射探测失败（不影响打洞）：" + error.Message);
            }

            try { await Task.Delay(MapInterval, ct).ConfigureAwait(false); }
            catch (OperationCanceledException) { return; }
        }
    }

    private static string Clean(IPAddress address)
    {
        var text = address.ToString();
        var scope = text.IndexOf('%');
        return scope >= 0 ? text[..scope] : text;
    }

    private readonly record struct RegisterResponse(
        string ObservedIp, IReadOnlyList<PunchTarget> PunchRequests);

    internal readonly record struct PunchTarget(string Endpoint, string ObservedIp);

    private async Task<RegisterResponse> RegisterAsync(List<string> endpoints, CancellationToken ct)
    {
        var payload = JsonSerializer.SerializeToUtf8Bytes(new
        {
            serverId = _serverId,
            tunnelPort = _tunnelPort,
            mcPort = _mcPort,
            endpoints,
            // 打洞用的出口候选，和上面那组 TCP 入口地址是两回事
            udpCandidates = _udpCandidates(),
            // 路由器给的入站映射（PCP/NAT-PMP/UPnP）。没有就是 null，控制面会跳过。
            mappedEndpoint = _mapped,
        });
        var stamp = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;
        var timestamp = stamp.ToString("R", System.Globalization.CultureInfo.InvariantCulture);

        using var hmac = new HMACSHA256(_secret);
        var signed = new byte[Encoding.ASCII.GetByteCount(timestamp) + payload.Length];
        Encoding.ASCII.GetBytes(timestamp, signed);
        payload.CopyTo(signed.AsSpan(Encoding.ASCII.GetByteCount(timestamp)));
        var signature = Convert.ToHexString(hmac.ComputeHash(signed)).ToLowerInvariant();

        using var request = new HttpRequestMessage(HttpMethod.Post, $"{_baseUrl}/api/v1/tunnel/register")
        {
            Content = new ByteArrayContent(payload),
        };
        request.Content.Headers.ContentType = new("application/json");
        request.Headers.Add("X-Muxi-Signature", signature);
        request.Headers.Add("X-Muxi-Timestamp", timestamp);

        using var response = await _http.SendAsync(request, ct).ConfigureAwait(false);
        var body = await response.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
            throw new InvalidOperationException($"HTTP {(int)response.StatusCode}：{body}");

        using var document = JsonDocument.Parse(body);
        var root = document.RootElement;
        var observed = root.TryGetProperty("observedIp", out var ip) ? ip.GetString() ?? "?" : "?";
        var punches = new List<PunchTarget>();
        if (root.TryGetProperty("punchRequests", out var list) && list.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in list.EnumerateArray())
            {
                punches.Add(new PunchTarget(
                    item.TryGetProperty("endpoint", out var e) ? e.GetString() ?? "" : "",
                    item.TryGetProperty("observedIp", out var o) ? o.GetString() ?? "" : ""));
            }
        }
        return new RegisterResponse(observed, punches);
    }

    /// <summary>
    /// 对客户端登记的外部地址发起回连。
    ///
    /// 双方必须几乎同时向对方发包，NAT 才会各自放行对端的入向流量；只有一边发起
    /// 的话，先到的那个包会被对方 NAT 当成未知连接丢掉。所以这里不关心连不连得上，
    /// 发出去本身就是目的——真正的会话由客户端那侧接手建立。
    /// </summary>
    private static async Task PunchBackAsync(PunchTarget target, CancellationToken ct)
    {
        if (!IPEndPoint.TryParse(target.Endpoint, out var endpoint)) return;
        try
        {
            using var socket = new TcpClient(endpoint.AddressFamily);
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
            deadline.CancelAfter(TimeSpan.FromSeconds(3));
            await socket.ConnectAsync(endpoint, deadline.Token).ConfigureAwait(false);
            Program.Log($"打洞回连 {endpoint} 竟然直接连通了");
        }
        catch (Exception error) when (error is SocketException or OperationCanceledException)
        {
            // 预期之内：这一下的作用是在本侧 NAT 打开映射，成不成功都不影响
            Program.Log($"打洞回连 {endpoint} 已发出");
        }
    }
}
