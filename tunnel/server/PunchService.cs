using System.Net;
using System.Net.Quic;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using BatterMC.Protocol;

namespace MuxiTunnel.Server;

/// <summary>
/// 服务端侧的打洞服务。
///
/// 为什么值得做：这台机器在运营商级 NAT 后面，入站彻底不通，而很多玩家没有
/// IPv6。对他们来说唯一的路就是中转，而中转流量全算在我们自己的服务器带宽上。
/// 打洞让数据在两端直连，服务器只承担几十字节的信令。
///
/// 一个打洞 socket 只服务一次会话：打通之后这个本地端口要交给 QUIC，于是再开
/// 一个新的 socket、重新探候选，供下一个玩家用。
/// </summary>
internal sealed class PunchService
{
    private static readonly int[] ReflectorPorts = [45701, 45702, 45703];
    private const int DiscoveryRounds = 6;

    /// <summary>轮询间隔。必须明显小于控制面的打洞提前量，否则取到时已经错过约定时刻。</summary>
    private static readonly TimeSpan PollInterval = TimeSpan.FromSeconds(1);

    /// <summary>
    /// UDP 出口候选的保鲜间隔。
    ///
    /// NAT 的 UDP 映射是靠流量续命的，运营商那边通常几十秒就老化。只在启动时探一次
    /// 的话，探到的 (IP, 端口) 过几分钟就失效了，而我们还在一直把它报给客户端——
    /// 玩家照着这个地址打，包落到一个早就不存在的映射上，两边各发几百个包互相收到 0 个。
    /// 定期重探既让映射一直活着，也保证报上去的地址是当前有效的那个。
    ///
    /// UDP 这一侧不需要更勤：映射是端点无关的，端口不会自己往上爬。
    ///
    /// 间隔按**每个 socket 自己上次探测的时刻**算，不按"上次公布"算。有了备用 socket
    /// 之后这两者不再是一回事：备用的探完之后可能要放二三十秒才被顶上去。实测踩过：
    /// 按公布时刻算，每次顶替都把计时清零，一整轮试验里保鲜一次都没跑，一个探完
    /// 41.7 秒才用上的 socket 映射早已老化（这台 NAT 约 30 秒），公布出去的端口是
    /// 死的，两边各发几千个包互相收到 0。15 秒给映射寿命留足一半余量。
    /// </summary>
    private static readonly TimeSpan RefreshInterval = TimeSpan.FromSeconds(15);

    /// <summary>
    /// TCP 候选单独保鲜的间隔，比 UDP 勤得多。
    ///
    /// 这个值直接决定对端预测端口的难度：候选放得越久，对方开打时我们的映射已经
    /// 往上爬了越多（实测 20 个/秒）。20 秒加上轮询和约定时刻的延迟，端口能漂出
    /// 400 多个——对端窗口开到 384 也追不上。压到 8 秒，最坏漂移约 260。代价只是
    /// 每 8 秒两次反射器连接，微不足道。
    ///
    /// 真正把漂移压到个位数的是开打前那次对表（见 <see cref="TcpRefreshBefore"/>）；
    /// 这里的 8 秒是它万一没对上时的兜底。
    /// </summary>
    private static readonly TimeSpan TcpRefreshInterval = TimeSpan.FromSeconds(8);

    /// <summary>保鲜时的探测轮数。只是续命，不需要首次那么多轮。</summary>
    private const int RefreshRounds = 2;

    /// <summary>
    /// 开打前多久做那次对表。必须和客户端侧的同名常量一致，否则两边不在同一个窗口里。
    /// </summary>
    private static readonly TimeSpan TcpRefreshBefore = TimeSpan.FromMilliseconds(900);

    /// <summary>
    /// 超时要盖过一次长轮询（<see cref="LongPollWait"/>）再加上正常往返。原先 10 秒，
    /// 对长轮询来说刚好会在服务端返回前被自己掐掉。
    /// </summary>
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(20) };

    /// <summary>
    /// 长轮询单次挂多久。
    ///
    /// 原先每秒问一次，请求进队列后最坏要等一个轮询周期才被取走——控制面因此只能给 4 秒
    /// 的约定提前量，旧客户端每次都要干等这 4 秒。挂在长轮询上，请求一到控制面就把我们
    /// 唤醒，取件只剩一次往返。上限要照顾保鲜：映射约 30 秒老化、每 15 秒保鲜，而保鲜只在
    /// 两次长轮询之间做，挂 8 秒最坏也就推迟到 23 秒。
    /// </summary>
    private const int LongPollWait = 8;

    /// <summary>上一轮是不是真的长轮询了（服务端认这个参数）。是的话下一轮不必再自己睡。</summary>
    private bool _longPolled;
    private readonly string _baseUrl;
    private readonly string _serverId;
    private readonly byte[] _controlSecret;
    private readonly string _masterToken;
    private readonly string _reflectorHost;
    private readonly string _targetHost;
    private readonly int _targetPort;

    private Socket? _socket;
    private volatile string[] _candidates = [];
    /// <summary>TCP 打洞用的本地端口。整个进程固定一个，靠 SO_REUSEADDR 反复绑。</summary>
    private readonly int _tcpPort;
    private volatile string[] _tcpCandidates = [];
    /// <summary>
    /// 本机 TCP 映射是不是地址相关（对称型）。要随候选一起报给对端——打洞的分工
    /// 由两侧类型的组合决定，见 <see cref="P2PTags"/>。
    /// </summary>
    private volatile bool _tcpAddressDependent;

    /// <summary>出口候选刚探出来 / 变了。控制面据此立刻补一次上报。</summary>
    public event Action? CandidatesChanged;
    /// <summary>本机地址能否被对端提前知道——决定要不要由我们来扫端口。</summary>
    private volatile bool _findable;
    private DateTimeOffset _refreshed = DateTimeOffset.MinValue;

    public PunchService(string baseUrl, string serverId, string token, string reflectorHost,
        string targetHost, int targetPort)
    {
        _baseUrl = baseUrl.TrimEnd('/');
        _serverId = serverId;
        _controlSecret = SHA256.HashData(Encoding.UTF8.GetBytes("muxi-tunnel-v1:" + token));
        _masterToken = token;
        _tcpPort = ReserveTcpPort();
        _reflectorHost = reflectorHost;
        _targetHost = targetHost;
        _targetPort = targetPort;
    }

    /// <summary>
    /// 当前的全部出口候选，供 register 一并上报。
    ///
    /// TCP 候选带 "t:" 前缀混在同一个列表里——控制面只是原样转发字符串，
    /// 不用为此加接口字段；客户端按前缀分流。
    /// </summary>
    public IReadOnlyList<string> Candidates =>
        [.. _candidates,
         .. _spareCandidates.Select(x => P2PTags.UdpAlternate + x),
         .. (TcpPunchEnabled ? _tcpCandidates : [])
             .Select(x => P2PTags.TcpTagFor(_tcpAddressDependent) + x)];

    /// <summary>
    /// 备用 socket 的出口，随主候选一起上报（带 <see cref="P2PTags.UdpAlternate"/> 前缀）。
    /// 单独存一份字符串数组而不是在上报时去读 <see cref="_spare"/>：上报在另一条线程上，
    /// 而 _spare 是个多字的结构体，跨线程读可能读到一半。
    /// </summary>
    private volatile string[] _spareCandidates = [];

    /// <summary>TCP 打洞开关，理由见 <see cref="P2PTags.TcpPunchEnabled"/>。</summary>
    private static bool TcpPunchEnabled => P2PTags.TcpPunchEnabled;

    public async Task RunAsync(CancellationToken ct)
    {
        if (TcpPunchEnabled) _ = RefreshTcpLoopAsync(ct);
        else Program.Log("TCP 打洞默认关闭（MUXI_TCP_PUNCH=1 可开），只做 UDP 打洞");
        while (!ct.IsCancellationRequested)
        {
            if (_socket is null && !await PrepareSocketAsync(ct).ConfigureAwait(false))
            {
                try { await Task.Delay(TimeSpan.FromSeconds(30), ct).ConfigureAwait(false); }
                catch (OperationCanceledException) { return; }
                continue;
            }

            // 每轮先当作没长轮询过：这一轮在轮询之前就出了异常的话，必须照旧睡满间隔，
            // 不能带着上一轮的 true 空转。
            _longPolled = false;
            try
            {
                // 备用 socket 随时备着：一取走当前这个，备用的立刻顶上并上报，控制面上
                // 那份候选就不会出现"空着"或"指向一个已经被占用的端口"的窗口。
                if (_spare is null) await PrepareSpareAsync(ct).ConfigureAwait(false);

                var requests = await PollAsync(ct).ConfigureAwait(false);
                foreach (var request in requests)
                {
                    // 每个请求一个 socket，各自在后台打，轮询循环一刻不停。
                    //
                    // 原先是在这里 await 整个打洞（打洞 10 秒预算 + 压测 + 收尾），期间
                    // 不轮询；同一批里的第二个请求更惨——第一个处理完把 _socket 置空，
                    // 第二个进来看到 null 直接 return，连一行日志都没有。两个玩家前后
                    // 十几秒内点开始游戏，后一个必然打洞失败。
                    // 同时在打的会话封个顶。每个会话对对称型对端都要扫端口，N 个一起上就是
                    // N 倍的包量和 conntrack 条目——这台路由器被暴力拨号打瘫过。满了就跳过，
                    // 而且不去动公布的 socket：换掉它却不服务这个请求，只会让下一个人对不上。
                    if (!_sessions.Wait(0))
                    {
                        Program.Log($"会话 {request.Session:x16} 到了但已有 {MaxConcurrentSessions} 个在打，跳过（客户端会重试或走别的线路）");
                        continue;
                    }
                    var taken = TakeSocket();
                    if (taken is null)
                    {
                        _sessions.Release();
                        Program.Log($"会话 {request.Session:x16} 到了但没有就绪的打洞 socket，跳过（客户端会走别的线路）");
                        continue;
                    }
                    _ = HandleAsync(request, taken.Value, ct);
                    // 没有备用可顶时当场补一个，别让下一个请求落空。
                    if (_socket is null) await PrepareSocketAsync(ct).ConfigureAwait(false);
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
                Program.Log($"打洞轮询失败：{error.Message}");
            }

            // 保鲜单独一段，不管这一轮轮询成没成功都要做。
            //
            // 原先它挂在轮询后面、同一个 try 里：控制面抖一下（HTTP 10 秒超时）就跳过，
            // 连续三次就超过这台 NAT 约 30 秒的映射寿命——而上报走另一条连接，照样把
            // 已经过期的候选报出去。保鲜只碰公布的和备用的 socket，正在打洞的那些早已
            // 从 _socket/_spare 上摘下来了，不会抢。
            try { await RefreshCandidatesAsync(ct).ConfigureAwait(false); }
            catch (Exception error) when (!Cancellation.IsShutdown(error, ct))
            {
                Program.Log($"出口保鲜失败：{error.Message}");
            }

            // 长轮询本身就挂过了，不必再睡；服务端是旧版本（不认 wait）时照旧每秒一次。
            if (_longPolled) continue;
            try { await Task.Delay(PollInterval, ct).ConfigureAwait(false); }
            catch (OperationCanceledException) { return; }
        }
    }

    /// <summary>一个已经探过出口、可以直接拿去打洞的 socket。</summary>
    internal readonly record struct ReadySocket(
        Socket Socket, string[] Candidates, bool Findable, DateTimeOffset ProbedAt);

    /// <summary>
    /// 备用的就绪 socket。取走当前 socket 时它立刻顶上，见 <see cref="TakeSocket"/>。
    /// 只在轮询循环里读写，不需要加锁。
    /// </summary>
    private ReadySocket? _spare;

    /// <summary>新开一个 socket 并探清它的出口。探不到返回 null。</summary>
    private async Task<ReadySocket?> ProbeSocketAsync(CancellationToken ct)
    {
        var socket = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
        try
        {
            socket.Bind(new IPEndPoint(IPAddress.Any, 0));
            var discovery = await NatDiscovery
                .DiscoverAsync(socket, _reflectorHost, ReflectorPorts, DiscoveryRounds, ct)
                .ConfigureAwait(false);
            if (discovery.Candidates.Count == 0)
            {
                Program.Log($"探测不到本机公网出口（发出 {discovery.Sent}，回应 {discovery.Replies}），打洞暂不可用");
                socket.Close();
                return null;
            }
            return new ReadySocket(socket,
                discovery.Candidates.Select(x => x.ToString()).ToArray(),
                !discovery.AddressDependent, DateTimeOffset.UtcNow);
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            Program.Log($"准备打洞 socket 失败：{error.Message}");
            socket.Close();
            return null;
        }
    }

    /// <summary>换备用 socket，同时更新对外报的那份备用候选；候选变了就催一次上报。</summary>
    private void SetSpare(ReadySocket? spare)
    {
        _spare = spare;
        var fresh = spare?.Candidates ?? [];
        if (fresh.SequenceEqual(_spareCandidates)) return;
        _spareCandidates = fresh;
        CandidatesChanged?.Invoke();
    }

    private async Task<bool> PrepareSocketAsync(CancellationToken ct)
    {
        // 有备用就直接顶上，不必现探。
        var ready = _spare ?? await ProbeSocketAsync(ct).ConfigureAwait(false);
        _spare = null;
        _spareCandidates = [];
        if (ready is null) return false;
        Publish(ready.Value);

        // TCP 出口只在第一次（还没有值时）探，而且放到后台：其中一台反射器会被本机代理
        // 接管，连不通要等满 8 秒，不能卡住轮询循环。之后由 RefreshTcpLoopAsync 保鲜。
        if (TcpPunchEnabled && _tcpCandidates.Length == 0)
            _ = Task.Run(async () =>
            {
            try
            {
                _tcpCandidates = TcpPuncher
                    .WithoutProxied(await DiscoverTcpAsync(ct).ConfigureAwait(false),
                        ready.Value.Candidates.Select(IPEndPoint.Parse).ToList())
                    .Select(x => x.ToString()).ToArray();
                // 同一个本地端口问不同反射器拿到不同端口 = 地址相关型。
                _tcpAddressDependent = _tcpCandidates.Distinct(StringComparer.Ordinal).Count() > 1;
                if (_tcpCandidates.Length > 0)
                    Program.Log($"TCP 出口候选 {_tcpCandidates.Length} 个："
                                + $"{string.Join("，", _tcpCandidates)}"
                                + (_tcpAddressDependent ? "（地址相关）" : "（端点无关）"));
            }
            catch (Exception error) when (error is not OperationCanceledException)
            {
                Program.Log($"TCP 出口探测失败（不影响 UDP 打洞）：{error.Message}");
            }
            }, ct);
        return true;
    }

    /// <summary>把一个就绪 socket 设成对外公布的那个，并立刻催一次上报。</summary>
    private void Publish(ReadySocket ready)
    {
        _socket = ready.Socket;
        _candidates = ready.Candidates;
        _findable = ready.Findable;
        // 记它**探测**的时刻，不是公布的时刻，见 RefreshInterval。
        _refreshed = ready.ProbedAt;
        Program.Log("本机 NAT 类型：" + (!ready.Findable
            ? "地址相关型 NAT（换个目标就换映射端口）——对端需要在邻近端口扫描才能命中"
            : "端点无关型 NAT（对所有目标同一个映射）——对端照着这个地址直接打就行"));
        Program.Log($"打洞就绪，本机出口候选 {_candidates.Length} 个：{string.Join("，", _candidates)}");
        CandidatesChanged?.Invoke();
    }

    private async Task PrepareSpareAsync(CancellationToken ct)
    {
        var spare = await ProbeSocketAsync(ct).ConfigureAwait(false);
        SetSpare(spare);
        if (spare is { } ready)
            Program.Log($"备用打洞 socket 就绪，出口 {string.Join("，", ready.Candidates)}（随主候选一起上报）");
    }

    /// <summary>
    /// 取走当前公布的 socket 交给一次打洞，备用的立刻顶上并上报。没有备用就把公布的候选
    /// 清空——宁可让客户端看到"暂时没有候选"去走别的线路，也不能让它照着一个已经被
    /// 别人占用的端口打满十秒。
    /// </summary>
    private ReadySocket? TakeSocket()
    {
        if (_socket is not { } socket) return null;
        var taken = new ReadySocket(socket, _candidates, _findable, _refreshed);
        _socket = null;
        _candidates = [];
        if (_spare is { } spare)
        {
            _spare = null;
            _spareCandidates = [];
            Publish(spare);
        }
        else
        {
            CandidatesChanged?.Invoke();
        }
        return taken;
    }

    /// <summary>
    /// TCP 候选的保鲜，单独一条循环。
    ///
    /// 绝不能放回轮询循环里。TCP 探测要去连反射器，而其中一台的流量会被本机代理
    /// 接管，连不通就是 8 秒起步——实测把上报间隔从 8~9 秒拖成了两次 30 秒的空档，
    /// 期间候选一直是陈旧的，打洞请求也可能被拖过约定时刻。轮询循环必须一直转。
    /// </summary>
    private async Task RefreshTcpLoopAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try
            {
                var udp = _candidates
                    .Select(x => IPEndPoint.TryParse(x, out var ep) ? ep : null)
                    .Where(x => x is not null).Select(x => x!).ToList();
                var fresh = TcpPuncher
                    .WithoutProxied(await DiscoverTcpAsync(ct).ConfigureAwait(false), udp)
                    .Select(x => x.ToString()).ToArray();
                if (fresh.Length > 0 && !fresh.SequenceEqual(_tcpCandidates))
                {
                    _tcpCandidates = fresh;
                    // 类型也要跟着重算。漏掉这一步的话，上报出去的前缀会是启动那一刻
                    // 的判断——线路换了出口策略（实测本机路由器改一下负载模式，TCP
                    // 就从对称变锥形）之后，对端会按错的类型去分工。
                    _tcpAddressDependent = fresh.Distinct(StringComparer.Ordinal).Count() > 1;
                    CandidatesChanged?.Invoke();
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
                Program.Log($"TCP 出口保鲜失败（不影响 UDP 打洞）：{error.Message}");
            }

            try { await Task.Delay(TcpRefreshInterval, ct).ConfigureAwait(false); }
            catch (OperationCanceledException) { return; }
        }
    }

    /// <summary>重探一次出口，顺带把 NAT 映射续上。地址变了就记一笔，下次 register 会报新的。</summary>
    /// <summary>
    /// 给到期的 socket 保鲜：公布的那个和备用的那个各按自己的年龄算，见 <see cref="RefreshInterval"/>。
    /// 每次轮询都会调，没到期就什么都不做。
    /// </summary>
    private async Task RefreshCandidatesAsync(CancellationToken ct)
    {
        var socket = _socket;
        if (socket is not null && DateTimeOffset.UtcNow - _refreshed > RefreshInterval)
            await RefreshPublishedAsync(socket, ct).ConfigureAwait(false);
        if (_spare is { } spare && DateTimeOffset.UtcNow - spare.ProbedAt > RefreshInterval)
            await RefreshSpareAsync(spare, ct).ConfigureAwait(false);
    }

    private async Task RefreshPublishedAsync(Socket socket, CancellationToken ct)
    {
        _refreshed = DateTimeOffset.UtcNow;
        try
        {
            var discovery = await NatDiscovery
                .DiscoverAsync(socket, _reflectorHost, ReflectorPorts, RefreshRounds, ct)
                .ConfigureAwait(false);
            if (discovery.Candidates.Count == 0) return;

            var fresh = discovery.Candidates.Select(x => x.ToString()).ToArray();
            var changed = !fresh.SequenceEqual(_candidates);
            if (changed) Program.Log($"出口候选已变化：{string.Join("，", fresh)}");
            _candidates = fresh;
            if (changed) CandidatesChanged?.Invoke();
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            Program.Log($"出口保鲜失败（不影响已有线路）：{error.Message}");
        }
    }

    /// <summary>备用那个也要续命：映射几十秒不用就老化，顶上去的时候探到的地址早已作废。</summary>
    private async Task RefreshSpareAsync(ReadySocket spare, CancellationToken ct)
    {
        try
        {
            var discovery = await NatDiscovery
                .DiscoverAsync(spare.Socket, _reflectorHost, ReflectorPorts, RefreshRounds, ct)
                .ConfigureAwait(false);
            if (discovery.Candidates.Count > 0)
                SetSpare(spare with
                {
                    Candidates = discovery.Candidates.Select(x => x.ToString()).ToArray(),
                    ProbedAt = DateTimeOffset.UtcNow,
                });
            else
                throw new InvalidOperationException("反射器没有回应");
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            Program.Log($"备用 socket 保鲜失败，丢掉重备：{error.Message}");
            try { spare.Socket.Close(); } catch { }
            SetSpare(null);
        }
    }

    private readonly record struct PunchRequest(
        ulong Session, IReadOnlyList<IPEndPoint> Candidates, DateTimeOffset PunchAt,
        IReadOnlyList<IPEndPoint> TcpCandidates, bool PeerTcpEndpointIndependent);

    private async Task<List<PunchRequest>> PollAsync(CancellationToken ct)
    {
        var timestamp = (DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0)
            .ToString("R", System.Globalization.CultureInfo.InvariantCulture);
        using var hmac = new HMACSHA256(_controlSecret);
        var signature = Convert.ToHexString(hmac.ComputeHash(Encoding.ASCII.GetBytes(timestamp)))
            .ToLowerInvariant();

        using var request = new HttpRequestMessage(HttpMethod.Get,
            $"{_baseUrl}/api/v1/tunnel/punch/pending?server_id={Uri.EscapeDataString(_serverId)}&wait={LongPollWait}");
        request.Headers.Add("X-Muxi-Signature", signature);
        request.Headers.Add("X-Muxi-Timestamp", timestamp);

        _longPolled = false;
        using var response = await _http.SendAsync(request, ct).ConfigureAwait(false);
        var result = new List<PunchRequest>();
        if (!response.IsSuccessStatusCode)
        {
            // 以前这里直接 return，鉴权失败或接口改名都表现为"一切正常但永远没有
            // 打洞请求"，查起来极其费劲。宁可刷日志也要让它可见。
            Program.Log($"打洞轮询被拒：HTTP {(int)response.StatusCode}");
            return result;
        }

        using var document = JsonDocument.Parse(
            await response.Content.ReadAsStringAsync(ct).ConfigureAwait(false));
        // 旧控制面不认 wait 参数、立即返回，也不带这个字段——那样就照旧每秒问一次。
        _longPolled = document.RootElement.TryGetProperty("longPoll", out var longPoll)
                      && longPoll.TryGetDouble(out var waited) && waited > 0;
        if (!document.RootElement.TryGetProperty("punchRequests", out var list)) return result;

        foreach (var item in list.EnumerateArray())
        {
            if (!item.TryGetProperty("sessionId", out var sid)) continue;
            if (!ulong.TryParse(sid.GetString(), System.Globalization.NumberStyles.HexNumber,
                    System.Globalization.CultureInfo.InvariantCulture, out var session)) continue;
            var peers = new List<IPEndPoint>();
            var tcpPeers = new List<IPEndPoint>();
            // 对端只要有一条带端点无关前缀就按端点无关算：同侧候选类型必然一致，
            // 混进来的老前缀条目只说明对端版本旧。
            var peerTcpEim = false;
            if (item.TryGetProperty("candidates", out var candidates))
                foreach (var candidate in candidates.EnumerateArray())
                {
                    var text = candidate.GetString() ?? "";
                    var isTcp = P2PTags.TryStripTcp(text, out text, out var eim);
                    if (IPEndPoint.TryParse(text, out var endpoint))
                    {
                        if (isTcp) { tcpPeers.Add(endpoint); peerTcpEim |= eim; }
                        else peers.Add(endpoint);
                    }
                }
            // 优先用相对值：它是"从你读到这条响应起再过多少毫秒"，不需要两边的钟一致。
            // 绝对的 punchAt 只作为旧控制面的兜底——拿它减本机 UtcNow，对时误差会
            // 原封不动变成打洞时刻的偏差，而 TCP 打洞对这个偏差零容忍（端口顺序分配，
            // 一秒就是 20 个端口）。
            var at = item.TryGetProperty("punchInMs", out var inMs)
                     && inMs.TryGetDouble(out var delayMs)
                ? DateTimeOffset.UtcNow + TimeSpan.FromMilliseconds(delayMs)
                : item.TryGetProperty("punchAt", out var punchAt)
                  && punchAt.TryGetDouble(out var seconds)
                    ? DateTimeOffset.FromUnixTimeMilliseconds((long)(seconds * 1000))
                    : DateTimeOffset.UtcNow;
            if (peers.Count > 0 || tcpPeers.Count > 0)
                result.Add(new PunchRequest(session, peers, at, tcpPeers, peerTcpEim));
        }
        return result;
    }

    /// <summary>同时在打的会话上限，见轮询循环里的说明。</summary>
    private const int MaxConcurrentSessions = 4;
    private readonly SemaphoreSlim _sessions = new(MaxConcurrentSessions, MaxConcurrentSessions);

    /// <summary>
    /// TCP 打洞一次只打一个会话：所有会话共用同一个 <see cref="_tcpPort"/>，几个带
    /// SO_REUSEADDR 的监听同时挂在一个端口上时，入站连接落到谁手里是不确定的——A 打通
    /// 的连接会被 B 接走、验明不是自己的会话后关掉。只在 MUXI_TCP_PUNCH=1 时有意义。
    /// </summary>
    private readonly SemaphoreSlim _tcpGate = new(1, 1);

    private async Task HandleAsync(PunchRequest request, ReadySocket ready, CancellationToken ct)
    {
        try { await HandleCoreAsync(request, ready, ct).ConfigureAwait(false); }
        // 现在是后台跑的，没人 await 它：异常不在这里接住就悄无声息地没了。
        catch (Exception error) when (!Cancellation.IsShutdown(error, ct))
        {
            Program.Log($"会话 {request.Session:x16} 打洞异常：{error.Message}");
            try { ready.Socket.Close(); } catch { }
        }
        finally
        {
            _sessions.Release();
        }
    }

    private async Task HandleCoreAsync(PunchRequest request, ReadySocket ready, CancellationToken ct)
    {
        var socket = ready.Socket;
        var localPort = ((IPEndPoint)socket.LocalEndPoint!).Port;

        Program.Log($"收到打洞请求 会话 {request.Session:x16}，"
                    + $"对端 UDP [{string.Join("，", request.Candidates)}]，"
                    + $"对端 TCP {request.TcpCandidates.Count} 个；"
                    + $"本机这一刻 UDP [{string.Join("，", ready.Candidates)}]"
                    + $"（{(DateTimeOffset.UtcNow - ready.ProbedAt).TotalSeconds:0} 秒前探的）");
        // 和隧道握手同源：客户端手里是控制面派生的 token，不是 master。
        //
        // 打洞只认当前这一代：UdpPuncher 的签和验共用一个密钥，收不下两代。
        // 客户端是在连接前几秒才去取的，所以只有恰好跨在换代那一瞬间才会对不上，
        // 而那一下也只是打洞失败退回隧道——隧道那边是认两代的，玩家不会掉线。
        var punchSecret = PunchProtocol.DeriveSecret(
            TunnelProtocol.DeriveClientToken(_masterToken, DateTimeOffset.UtcNow));
        // TCP 和 UDP 在同一个约定时刻一起打。两条路各有各的死法——UDP 可能被
        // 运营商限速（实测有线路只跑 3 KB/s），TCP 可能被 NAT 的顺序分配打偏——
        // 事先谁也说不准，同时开打最省时间，谁先通就用谁。
        //
        // 打通一条就**立刻**开张，绝不等另一条。这里踩过一次：UDP 23:03:19 就通了，
        // 但代码在 await TCP 的结果，QUIC 监听拖到 23:03:35 才起——客户端那边
        // 10 秒的握手超时早就到了，明明打通了却连不上。
        // MUXI_NO_TCP_PUNCH=1 时完全不做 TCP 打洞。用来做对照实验：TCP 那边两侧
        // 加起来几百个并发 connect，和 UDP 打洞抢同一张 NAT 表，实测能把表打爆并
        // 连累 UDP。跨 NAT 的 UDP 双向收不到包时，先关掉它再测一次最省事。
        var noTcp = !TcpPunchEnabled
                    || Environment.GetEnvironmentVariable("MUXI_NO_TCP_PUNCH") == "1";
        var tcpTask = request.TcpCandidates.Count > 0 && !noTcp
            ? PunchTcpSerializedAsync(request, punchSecret, ct)
            : Task.FromResult(new TcpPuncher.Outcome(null, null, TimeSpan.Zero, 0));

        // TCP 那条自己管自己：通了就地接流，不通就记一笔，都不挡 UDP。
        var tcpServed = tcpTask.ContinueWith(t =>
        {
            if (t.IsCompletedSuccessfully && t.Result.Success)
                _ = ServeMuxAsync(t.Result.Socket!, t.Result.Peer!, ct);
            else if (t.IsFaulted)
                Program.Log($"TCP 打洞异常：{t.Exception?.GetBaseException().Message}");
        }, TaskScheduler.Default);

        // MUXI_NO_UDP_PUNCH=1 时跳过 UDP 那条腿。理由和客户端侧的同名开关一致：
        // UDP 两秒就通，TCP 那条腿永远来不及跑完，改动就永远验证不了。
        var noUdp = Environment.GetEnvironmentVariable("MUXI_NO_UDP_PUNCH") == "1";
        var outcome = request.Candidates.Count > 0 && !noUdp
            ? await UdpPuncher.PunchAsync(
                socket, request.Candidates, request.Session, punchSecret,
                request.PunchAt, TimeSpan.FromSeconds(10),
                sweepPorts: ready.Findable,
                message => Program.Log("UDP 打洞：" + message), ct,
                awaitPeerChoice: true).ConfigureAwait(false)
            : new UdpPuncher.Outcome(null, TimeSpan.Zero, 0, 0, []);

        // 客户端在建 QUIC 之前会先用裸 UDP 压一下这个洞（见 HoleLoadTest），我们是
        // 应答方。必须夹在打洞成功和关 socket 之间：端口还是这个 socket 的，QUIC
        // 监听起不来，所以这一段被刻意做得很短，推完约定轮数立刻返回。
        // 打通的可能是诱饵端口（本机映射不可预测时会多开，见 UdpPuncher.DecoySockets）。
        // 映射按本地端口记账，预热和 QUIC 监听都必须落在赢的那一个上。
        var live = outcome.Winner ?? socket;
        var livePort = live.LocalEndPoint is IPEndPoint live4 ? live4.Port : localPort;
        if (outcome.Success)
        {
            try
            {
                await HoleLoadTest.RespondAsync(
                        live, outcome.Peer!, request.Session, punchSecret,
                        message => Program.Log(message), ct, outcome.PeerLoadRequest,
                        outcome.PeerFinished)
                    .ConfigureAwait(false);
            }
            catch (Exception error) when (error is not OperationCanceledException)
            {
                Program.Log($"洞压测应答异常，跳过：{error.Message}");
            }
        }

        // 无论成败这个 socket 都不再复用：成功了要把端口让给 QUIC，失败了 NAT 上
        // 已经留下一堆指向错误地址的映射，换一个干净的更省事。它在取走的那一刻就已经
        // 不是 _socket 了（见 TakeSocket），这里只管关自己手上的。
        try { live.Close(); } catch { }
        if (!ReferenceEquals(live, socket)) { try { socket.Close(); } catch { } }

        // UDP 通了就马上起 QUIC 监听，这一步不能排在 TCP 后面。
        if (outcome.Success) _ = ServeAsync(livePort, outcome.Peer!, ct);
        else Program.Log($"UDP 打洞未成功（发出 {outcome.Sent}、收到 {outcome.PunchesHeard}）");

        // 结果汇总放到后台，纯粹为了留一行日志，不挡这次 poll 的下一个请求。
        _ = tcpServed.ContinueWith(_ =>
        {
            var tcpOk = tcpTask.IsCompletedSuccessfully && tcpTask.Result.Success;
            if (!outcome.Success && !tcpOk)
                Program.Log("打洞未成功，客户端会回退到中转");
            else
                Program.Log($"打洞结果：UDP {(outcome.Success ? "通" : "不通")}，"
                            + $"TCP {(tcpOk ? "通" : "不通")}");
        }, TaskScheduler.Default);
    }

    /// <summary>打通之后在同一个本地端口起 QUIC，等客户端连进来。</summary>
    private async Task ServeAsync(int localPort, IPEndPoint peer, CancellationToken ct)
    {
        try
        {
            // 用进程级共享的那张，别每次现生成：71~134ms 的 RSA 正好卡在对端已经
            // 在发 QUIC Initial 的那一刻。也别 Dispose 它。
            var certificate = QuicTunnel.SharedEphemeralCertificate;
            await using var listener = await QuicTunnel
                .ListenAsync(localPort, certificate, AddressFamily.InterNetwork, ct)
                .ConfigureAwait(false);
            Program.Log($"P2P 监听已就绪 :{localPort}，等待 {peer} 接入");

            using var accept = CancellationTokenSource.CreateLinkedTokenSource(ct);
            accept.CancelAfter(TimeSpan.FromSeconds(20));
            await using var connection = await listener.AcceptConnectionAsync(accept.Token)
                .ConfigureAwait(false);
            // 包长锁没锁住要写进日志：没锁住的连接跑久了大包过不去，游戏会卡住而连接看着活着
            Program.Log($"P2P 连接已建立 {connection.RemoteEndPoint}，" +
                        (MsQuicMtu.Read(connection) is { } mtu
                            ? mtu.Max == MsQuicMtu.Mtu ? $"包长锁定 {mtu.Max}" : $"包长 {mtu.Min}~{mtu.Max}（没锁住）"
                            : "包长上限读不到"));

            while (!ct.IsCancellationRequested)
            {
                var stream = await connection.AcceptInboundStreamAsync(ct).ConfigureAwait(false);
                _ = ForwardAsync(stream, ct);
            }
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            Program.Log($"P2P 服务结束：{error.Message}");
        }
    }

    /// <summary>一条 QUIC 流对应一条 Minecraft 连接，纯字节对拷。</summary>
    private async Task ForwardAsync(QuicStream stream, CancellationToken ct)
        => await ForwardStreamAsync(stream, ct).ConfigureAwait(false);

    /// <summary>
    /// 一条逻辑流对应一条 Minecraft 连接。QUIC 的流和 TCP 复用出来的流在这里
    /// 是一回事——都是双工字节流，开场可能是带宽测试请求，否则原样转给 Minecraft。
    /// </summary>
    private async Task ForwardStreamAsync(Stream stream, CancellationToken ct)
    {
        await using (stream)
        {
            var scratch = new byte[64];
            int? bulk;
            int headLength;
            try { (bulk, headLength) = await TryReadBulkAsync(stream, scratch, ct).ConfigureAwait(false); }
            catch (Exception error) when (error is not OperationCanceledException)
            {
                Program.Log($"P2P 流读开场失败：{error.Message}");
                return;
            }
            if (headLength <= 0) return;
            if (bulk is { } want)
            {
                Program.Log($"带宽测试：回 {want / 1024} KB");
                try { await ServeBulkAsync(stream, want, ct).ConfigureAwait(false); }
                catch (Exception error) when (error is not OperationCanceledException)
                {
                    Program.Log($"带宽测试中断：{error.Message}");
                }
                return;
            }

            using var game = new TcpClient();
            try
            {
                using var dial = CancellationTokenSource.CreateLinkedTokenSource(ct);
                dial.CancelAfter(TimeSpan.FromSeconds(10));
                await game.ConnectAsync(_targetHost, _targetPort, dial.Token).ConfigureAwait(false);
            }
            catch (Exception error)
            {
                Program.Log($"P2P 流接本机 Minecraft 失败：{error.Message}");
                return;
            }
            game.NoDelay = true;
            var gameStream = game.GetStream();
            // 探测开场时读走的那几个字节是玩家的握手包，必须先补给 Minecraft
            await gameStream.WriteAsync(scratch.AsMemory(0, headLength), ct).ConfigureAwait(false);
            // 带收尾的对拷：玩家那头断开要把 FIN 传给 Minecraft，否则它要等 30 秒读超时
            // 才放人，这期间玩家在服务器上还算"在线"。见 StreamBridge。
            await StreamBridge.RunAsync(stream, gameStream, StreamBridge.DefaultLinger, ct)
                .ConfigureAwait(false);
        }
    }


    /// <summary>
    /// 看这条流开头是不是带宽测试请求。
    ///
    /// 不是的话把已经读出来的字节原样交回去（<paramref name="leftover"/>），由调用方
    /// 先写给 Minecraft 再接着对拷——不能把它们吞掉，那是玩家的握手包。
    /// </summary>
    internal static async Task<(int? Bulk, int HeadLength)> TryReadBulkAsync(
        Stream stream, Memory<byte> scratch, CancellationToken ct)
    {
        var magic = TunnelProtocol.BulkMagic.Length;
        var head = scratch[..(magic + 4)];
        // 实际读到多少必须回传：不是带宽请求时这些字节是玩家的握手包，一个都不能丢，
        // 也不能凭空补零——读不满就只回读到的那部分。
        var got = await stream.ReadAtLeastAsync(head, head.Length, false, ct).ConfigureAwait(false);
        if (got < head.Length || !head.Span[..magic].SequenceEqual(TunnelProtocol.BulkMagic))
            return (null, got);
        var want = System.Buffers.Binary.BinaryPrimitives.ReadInt32BigEndian(head.Span[magic..]);
        return (want is > 0 and <= TunnelProtocol.BulkMaxBytes ? want : null, got);
    }

    /// <summary>回等量数据。内容不重要，重要的是真的把这么多字节推过去。</summary>
    internal static async Task ServeBulkAsync(Stream stream, int bytes, CancellationToken ct)
    {
        var chunk = new byte[64 * 1024];
        Random.Shared.NextBytes(chunk);
        var left = bytes;
        while (left > 0 && !ct.IsCancellationRequested)
        {
            var take = Math.Min(left, chunk.Length);
            await stream.WriteAsync(chunk.AsMemory(0, take), ct).ConfigureAwait(false);
            left -= take;
        }
        await stream.FlushAsync(ct).ConfigureAwait(false);
    }


    /// <summary>
    /// TCP 打洞：开打前最后一次对表，然后开打。
    ///
    /// 这个方法原先不存在——对表只有客户端那一半。客户端在开打前 900ms 带着
    /// <c>side=client</c> 去 <c>/punch/tcp-refresh</c> 存自己的映射、取对端的，而这里
    /// 从来没有人带 <c>side=agent</c> 去存，于是客户端每次读到的都是空，
    /// <c>narrowWindow</c> 永远是 false，那段窄窗口逻辑**一次都没有生效过**。
    /// 日志里那行"对端没赶上这次对表"是必然出现，不是偶发。
    ///
    /// 单边的对表没有任何意义：这次交换的全部价值在于把两侧的映射同时压到几百毫秒
    /// 以内的新鲜度，少一边就等于没做。
    /// </summary>
    private async Task<TcpPuncher.Outcome> PunchTcpSerializedAsync(
        PunchRequest request, byte[] punchSecret, CancellationToken ct)
    {
        // 前一个会话还占着 TCP 端口就不打了：排队等到它结束，约定时刻早过了，打也白打。
        if (!await _tcpGate.WaitAsync(0, ct).ConfigureAwait(false))
        {
            Program.Log($"会话 {request.Session:x16} 的 TCP 打洞跳过：另一个会话正占着 TCP 端口");
            return new TcpPuncher.Outcome(null, null, TimeSpan.Zero, 0);
        }
        try { return await PunchTcpAsync(request, punchSecret, ct).ConfigureAwait(false); }
        finally { _tcpGate.Release(); }
    }

    private async Task<TcpPuncher.Outcome> PunchTcpAsync(
        PunchRequest request, byte[] punchSecret, CancellationToken ct)
    {
        var punchAt = request.PunchAt + TcpPuncher.LeadTime;
        var peers = request.TcpCandidates;
        var fresh = false;

        var wait = punchAt - TcpRefreshBefore - DateTimeOffset.UtcNow;
        if (wait > TimeSpan.Zero)
            await Task.Delay(wait, ct).ConfigureAwait(false);

        try
        {
            var mine = await DiscoverTcpAsync(ct).ConfigureAwait(false);
            var swapped = await RefreshTcpAsync(request.Session, mine, ct).ConfigureAwait(false);
            if (swapped.Count > 0)
            {
                peers = swapped;
                fresh = true;
                Program.Log($"TCP 打洞：开打前对表成功，对端最新映射 {string.Join("，", swapped)}");
            }
            else
            {
                Program.Log("TCP 打洞：对端没赶上这次对表，只能用几秒前的旧候选");
            }
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            Program.Log("TCP 打洞：对表失败，退回旧候选：" + error.Message);
        }

        // 分工由两侧映射类型决定，见 P2PTags.ShouldPredictPeerPorts。这一侧是接受方，
        // 所以在"两边都地址相关"时**不**预测——让发起方去猜，搜索空间从端口对 2³²
        // 降回单个端口 2¹⁶。原先两侧都硬写 true，等于要求一次配对的巧合。
        var predict = P2PTags.ShouldPredictPeerPorts(
            _tcpAddressDependent, request.PeerTcpEndpointIndependent, initiator: false);
        Program.Log($"TCP 打洞：本机{(_tcpAddressDependent ? "地址相关" : "端点无关")}、"
                    + $"对端{(request.PeerTcpEndpointIndependent ? "端点无关" : "地址相关")}"
                    + $" → {(predict ? "由我预测对端端口" : "照对端公布的端口直接连，不预测")}");

        return await TcpPuncher.PunchAsync(
                _tcpPort, peers, request.Session, punchSecret,
                punchAt, TimeSpan.FromSeconds(10) - TcpPuncher.LeadTime,
                predictPorts: predict, initiator: false, narrowWindow: fresh,
                message => Program.Log("TCP 打洞：" + message), ct)
            .ConfigureAwait(false);
    }

    /// <summary>
    /// 把自己此刻的 TCP 映射报给控制面，同时取回对端此刻的。和客户端那一半同构。
    ///
    /// 谁先到谁先存，后到的立刻拿到对方的值；先到的再读一次（不带候选）就能读回来。
    /// 服务端只保留几秒，过期即弃——陈旧的映射比没有更坏，会把拨号预算全花在
    /// 必然打空的端口上。
    /// </summary>
    private async Task<List<IPEndPoint>> RefreshTcpAsync(
        ulong session, IReadOnlyList<IPEndPoint> mine, CancellationToken ct)
    {
        var url = $"{_baseUrl}/api/v1/tunnel/punch/tcp-refresh?session={session:x16}&side=agent";
        var body = "{\"candidates\":[" + string.Join(",", mine.Select(x => $"\"{x}\"")) + "]}";

        for (var attempt = 0; attempt < 3; attempt++)
        {
            using var content = new StringContent(
                attempt == 0 ? body : "{}", Encoding.UTF8, "application/json");
            using var reply = await _http.PostAsync(url, content, ct).ConfigureAwait(false);
            if (!reply.IsSuccessStatusCode) return [];
            using var document = JsonDocument.Parse(
                await reply.Content.ReadAsStringAsync(ct).ConfigureAwait(false));
            var list = new List<IPEndPoint>();
            if (document.RootElement.TryGetProperty("peerCandidates", out var peers))
                foreach (var item in peers.EnumerateArray())
                    if (IPEndPoint.TryParse(item.GetString() ?? "", out var endpoint))
                        list.Add(endpoint);
            if (list.Count > 0) return list;
            // 对端可能比我们晚几十毫秒到，等一下再读一次
            try { await Task.Delay(TimeSpan.FromMilliseconds(120), ct).ConfigureAwait(false); }
            catch (OperationCanceledException) { return []; }
        }
        return [];
    }

    /// <summary>先占一个端口号拿到值再放开，之后所有 TCP 操作都绑这个号（靠 SO_REUSEADDR）。</summary>
    private static int ReserveTcpPort()
    {
        using var probe = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
        probe.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.ReuseAddress, true);
        probe.Bind(new IPEndPoint(IPAddress.Any, 0));
        return ((IPEndPoint)probe.LocalEndPoint!).Port;
    }

    /// <summary>问 TCP 反射器自己的映射。必须用 TCP 的反射器，UDP 那套映射对不上。</summary>
    private async Task<List<IPEndPoint>> DiscoverTcpAsync(CancellationToken ct)
    {
        var found = new List<IPEndPoint>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var host in _reflectorHost.Split([',', ';', ' '],
                     StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            foreach (var port in TcpReflectorPorts)
            {
                var seenAs = await TcpPuncher.DiscoverAsync(host, port, _tcpPort, ct)
                    .ConfigureAwait(false);
                if (seenAs is not null && seen.Add(seenAs.ToString())) found.Add(seenAs);
                if (found.Count >= 4) return found;
            }
        }
        return found;
    }

    private static readonly int[] TcpReflectorPorts = [45711, 45712];

    /// <summary>TCP 打通之后：在这条连接上复用出多条逻辑流，逐条接到本机 Minecraft。</summary>
    private async Task ServeMuxAsync(Socket socket, IPEndPoint peer, CancellationToken ct)
    {
        try
        {
            await using var mux = new MuxConnection(
                new NetworkStream(socket, ownsSocket: true), initiator: false);
            Program.Log($"P2P(TCP) 连接已建立 {peer}");
            while (!ct.IsCancellationRequested && mux.IsAlive)
            {
                var stream = await mux.AcceptAsync(ct).ConfigureAwait(false);
                _ = ForwardStreamAsync(stream, ct);
            }
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            Program.Log($"P2P(TCP) 服务结束：{error.Message}");
        }
    }
}
