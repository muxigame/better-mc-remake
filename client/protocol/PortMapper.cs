using System.Diagnostics;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Text;
using System.Text.RegularExpressions;

namespace BatterMC.Protocol;

/// <summary>
/// 问路由器要一个入站端口：PCP → NAT-PMP → UPnP IGD，谁先给就用谁。
///
/// 为什么值得做：拿到入站端口意味着**完全不用打洞**。对端直接连过来，没有约定时刻、
/// 没有端口预测、没有 NAT 表压力，成功即满带宽。按打洞那套方程（见
/// <see cref="P2PTags.PredictStep"/>）辛苦争取的东西，这里一次请求就能拿到。
///
/// 为什么绝不能依赖它：
///
/// <list type="bullet">
/// <item>本机（MC 服务端）实测过——锐捷会老老实实把映射建好、<c>Enabled=1</c> 查得到，
///   但公网仍然进不来，因为上游 <c>100.64.0.1</c> 是运营商级 NAT，拦在更外面。
///   **映射"成功"和"能进来"是两件事**，所以下面拿到映射之后必须另外验证可达性，
///   不能拿返回值当结论。</item>
/// <item><c>GetExternalIPAddress</c> 返回的不是 WAN 口自身的地址，据此判断"是否在
///   CGNAT 后面"会误判（踩过一次）。</item>
/// <item>这类协议只和**第一跳**说话。玩家家里是双层 NAT 时，映射只建在内层那台上，
///   外层那层不知情。</item>
/// </list>
///
/// 所以它在路径里的位置是：**多产出一条候选**，成功就省掉一整轮打洞，失败就当没这回事
/// 继续打洞。任何一处都不允许出现"映射失败就放弃直连"的逻辑。
///
/// 三种协议都试，因为分布是碎的：PCP 是现在的标准（RFC 6887），NAT-PMP 是它的前身
/// （RFC 6886，同一个 5351 端口，报文头一个字节就能区分），UPnP IGD 最老但国内家用
/// 路由器上最常见。前两个是几十字节的 UDP，代价可以忽略，所以先试它们。
/// </summary>
public static class PortMapper
{
    /// <summary>PCP 和 NAT-PMP 共用的端口。版本号在报文第一个字节里区分。</summary>
    private const int PcpPort = 5351;

    private const byte NatPmpVersion = 0;
    private const byte PcpVersion = 2;

    private const byte OpcodeMap = 1;

    /// <summary>
    /// 请求的租约时长。
    ///
    /// 给两小时而不是"永久"：路由器重启或表满时会把长租约的条目留成僵尸，而我们
    /// 每次会话都会重新申请。真正要紧的是它比一局游戏长。
    /// </summary>
    private static readonly TimeSpan Lease = TimeSpan.FromHours(2);

    /// <summary>单个协议的等待时长。路由器就在一跳之外，回不来就是不支持。</summary>
    private static readonly TimeSpan Attempt = TimeSpan.FromMilliseconds(700);

    public sealed record Result(
        string Protocol, IPEndPoint? External, int InternalPort, string Detail)
    {
        public bool Mapped => External is not null;
    }

    /// <summary>
    /// 依次试三种协议，返回第一个成功的映射。
    ///
    /// 全都失败也只是返回一个 <c>Mapped=false</c> 的结果——这是正常情况，不是错误，
    /// 调用方照常继续走打洞。整个过程最多花一秒多。
    /// </summary>
    public static async Task<Result> TryMapAsync(
        int internalPort, ProtocolType protocol, Action<string>? log, CancellationToken ct)
    {
        var gateways = DefaultGateways();
        if (gateways.Count == 0)
            return new Result("none", null, internalPort, "找不到默认网关");

        foreach (var gateway in gateways)
        {
            foreach (var version in new[] { PcpVersion, NatPmpVersion })
            {
                var name = version == PcpVersion ? "PCP" : "NAT-PMP";
                try
                {
                    var mapped = await TryPcpAsync(gateway, internalPort, protocol, version, ct)
                        .ConfigureAwait(false);
                    if (mapped is not null)
                    {
                        log?.Invoke($"{name} 向 {gateway} 申请到入站端口 {mapped}");
                        return new Result(name, mapped, internalPort, $"网关 {gateway}");
                    }
                }
                catch (Exception error) when (error is not OperationCanceledException)
                {
                    log?.Invoke($"{name} 向 {gateway} 请求失败：{error.Message}");
                }
            }
        }

        try
        {
            var igd = await TryIgdAsync(internalPort, protocol, log, ct).ConfigureAwait(false);
            if (igd is not null)
            {
                log?.Invoke($"UPnP IGD 申请到入站端口 {igd}");
                return new Result("UPnP", igd, internalPort, "IGD AddPortMapping");
            }
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            log?.Invoke("UPnP IGD 请求失败：" + error.Message);
        }

        return new Result("none", null, internalPort, "三种协议都没给出映射");
    }

    /// <summary>
    /// PCP / NAT-PMP 的 MAP 请求。两者报文不同但只差一层封装，所以合在一处。
    ///
    /// NAT-PMP（RFC 6886 §3.3）请求 12 字节：版本 0、opcode(1=UDP,2=TCP)、保留 2、
    /// 内部端口 2、建议外部端口 2、租约秒数 4。响应 16 字节，含结果码和外部端口。
    ///
    /// PCP（RFC 6887 §11.1）请求 60 字节：版本 2、R|opcode、保留 2、租约 4、
    /// 客户端地址 16（IPv4 映射成 v6 形式），再跟 36 字节的 MAP 负载（nonce 12、
    /// 协议 1、保留 3、内部端口 2、建议外部端口 2、建议外部地址 16）。
    /// </summary>
    private static async Task<IPEndPoint?> TryPcpAsync(
        IPAddress gateway, int internalPort, ProtocolType protocol, byte version,
        CancellationToken ct)
    {
        using var socket = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
        socket.Bind(new IPEndPoint(IPAddress.Any, 0));
        var target = new IPEndPoint(gateway, PcpPort);

        byte[] request;
        if (version == NatPmpVersion)
        {
            request = new byte[12];
            request[0] = NatPmpVersion;
            request[1] = (byte)(protocol == ProtocolType.Tcp ? 2 : 1);
            WriteUInt16(request.AsSpan(4), (ushort)internalPort);
            WriteUInt16(request.AsSpan(6), (ushort)internalPort);
            WriteUInt32(request.AsSpan(8), (uint)Lease.TotalSeconds);
        }
        else
        {
            // 客户端地址要填我们和网关通信时用的那个本地地址，不能填 0——有些实现
            // 会据此判断请求是不是被转发过，填错直接回 ADDRESS_MISMATCH。
            var local = LocalAddressTowards(gateway);
            if (local is null) return null;

            request = new byte[60];
            request[0] = PcpVersion;
            request[1] = OpcodeMap;                     // R=0（请求）
            WriteUInt32(request.AsSpan(4), (uint)Lease.TotalSeconds);
            MapV4Into(local, request.AsSpan(8, 16));
            Random.Shared.NextBytes(request.AsSpan(24, 12));   // nonce
            request[36] = (byte)(protocol == ProtocolType.Tcp ? 6 : 17);
            WriteUInt16(request.AsSpan(40), (ushort)internalPort);
            WriteUInt16(request.AsSpan(42), (ushort)internalPort);
            // 建议外部地址留全零 = 由网关自己挑
        }

        await socket.SendToAsync(request, SocketFlags.None, target, ct).ConfigureAwait(false);

        var buffer = new byte[128];
        SocketReceiveFromResult got;
        try
        {
            using var timer = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timer.CancelAfter(Attempt);
            got = await socket.ReceiveFromAsync(
                    buffer, SocketFlags.None, new IPEndPoint(IPAddress.Any, 0), timer.Token)
                .ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested) { return null; }

        var reply = buffer.AsSpan(0, got.ReceivedBytes);
        if (reply.Length < 12 || reply[0] != version) return null;

        if (version == NatPmpVersion)
        {
            // 响应里 opcode 是请求值 + 128
            if (reply.Length < 16 || reply[1] != (byte)((protocol == ProtocolType.Tcp ? 2 : 1) + 128))
                return null;
            if (ReadUInt16(reply[2..]) != 0) return null;         // 结果码非 0 = 被拒
            var external = ReadUInt16(reply[10..]);
            var ip = await ExternalAddressNatPmpAsync(socket, target, ct).ConfigureAwait(false);
            return ip is null ? null : new IPEndPoint(ip, external);
        }

        // PCP 响应：版本 2、R=1|opcode、保留、结果码在第 3 字节
        if (reply.Length < 60 || reply[1] != (OpcodeMap | 0x80) || reply[3] != 0) return null;
        var pcpExternalPort = ReadUInt16(reply[42..]);
        var pcpExternalIp = ReadV4From(reply.Slice(44, 16));
        return pcpExternalIp is null ? null : new IPEndPoint(pcpExternalIp, pcpExternalPort);
    }

    /// <summary>NAT-PMP 的外部地址要单独问一次（opcode 0）。PCP 直接在 MAP 响应里给。</summary>
    private static async Task<IPAddress?> ExternalAddressNatPmpAsync(
        Socket socket, IPEndPoint target, CancellationToken ct)
    {
        await socket.SendToAsync(new byte[2], SocketFlags.None, target, ct).ConfigureAwait(false);
        var buffer = new byte[64];
        try
        {
            using var timer = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timer.CancelAfter(Attempt);
            var got = await socket.ReceiveFromAsync(
                    buffer, SocketFlags.None, new IPEndPoint(IPAddress.Any, 0), timer.Token)
                .ConfigureAwait(false);
            var reply = buffer.AsSpan(0, got.ReceivedBytes);
            if (reply.Length < 12 || reply[0] != NatPmpVersion || reply[1] != 128) return null;
            if (ReadUInt16(reply[2..]) != 0) return null;
            return new IPAddress(reply.Slice(8, 4).ToArray());
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested) { return null; }
    }

    /// <summary>
    /// UPnP IGD：SSDP 发现 → 取设备描述 → SOAP AddPortMapping → 读回外部地址。
    ///
    /// 只做最小实现，够拿到一个端口就行。不缓存、不续租——每次会话重新申请，租约
    /// 比一局游戏长即可。
    /// </summary>
    private static async Task<IPEndPoint?> TryIgdAsync(
        int internalPort, ProtocolType protocol, Action<string>? log, CancellationToken ct)
    {
        var location = await DiscoverIgdAsync(ct).ConfigureAwait(false);
        if (location is null) return null;

        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(3) };
        var description = await http.GetStringAsync(location, ct).ConfigureAwait(false);

        // WANIPConnection 优先，没有再退 WANPPPConnection（PPPoE 拨号的路由器用后者）
        var control = ControlUrl(description, location,
            "urn:schemas-upnp-org:service:WANIPConnection:1")
            ?? ControlUrl(description, location,
                "urn:schemas-upnp-org:service:WANPPPConnection:1");
        if (control is null) return null;

        var serviceType = control.Value.ServiceType;
        var local = LocalAddressTowards(location.Host is { } h && IPAddress.TryParse(h, out var gw)
            ? gw : IPAddress.Any);
        if (local is null) return null;

        var proto = protocol == ProtocolType.Tcp ? "TCP" : "UDP";
        var add = $"""
            <u:AddPortMapping xmlns:u="{serviceType}">
            <NewRemoteHost></NewRemoteHost>
            <NewExternalPort>{internalPort}</NewExternalPort>
            <NewProtocol>{proto}</NewProtocol>
            <NewInternalPort>{internalPort}</NewInternalPort>
            <NewInternalClient>{local}</NewInternalClient>
            <NewEnabled>1</NewEnabled>
            <NewPortMappingDescription>Batter MC</NewPortMappingDescription>
            <NewLeaseDuration>{(int)Lease.TotalSeconds}</NewLeaseDuration>
            </u:AddPortMapping>
            """;
        var added = await SoapAsync(http, control.Value.Url, serviceType, "AddPortMapping", add, ct)
            .ConfigureAwait(false);
        if (added is null)
        {
            // 有的路由器拒绝非零租约。去掉租约（= 永久）再试一次，这是最常见的兼容问题。
            var permanent = add.Replace($"<NewLeaseDuration>{(int)Lease.TotalSeconds}</NewLeaseDuration>",
                "<NewLeaseDuration>0</NewLeaseDuration>");
            added = await SoapAsync(http, control.Value.Url, serviceType, "AddPortMapping", permanent, ct)
                .ConfigureAwait(false);
            if (added is null) return null;
            log?.Invoke("UPnP：带租约被拒，改用永久映射");
        }

        var ipReply = await SoapAsync(http, control.Value.Url, serviceType, "GetExternalIPAddress",
                $"<u:GetExternalIPAddress xmlns:u=\"{serviceType}\"></u:GetExternalIPAddress>", ct)
            .ConfigureAwait(false);
        if (ipReply is null) return null;
        var match = Regex.Match(ipReply, @"<NewExternalIPAddress>\s*([^<\s]+)\s*</NewExternalIPAddress>");
        if (!match.Success || !IPAddress.TryParse(match.Groups[1].Value, out var external)) return null;

        // 注意：这个地址是**路由器自己认为**的外部地址。在 CGNAT 后面它可能是
        // 100.64/10 里的私有地址，甚至是个完全对不上的值——实测本机就是这样。
        // 所以这里只负责如实返回，判断"能不能进来"是调用方的事。
        return new IPEndPoint(external, internalPort);
    }

    private static async Task<Uri?> DiscoverIgdAsync(CancellationToken ct)
    {
        const string search = """
            M-SEARCH * HTTP/1.1
            HOST: 239.255.255.250:1900
            MAN: "ssdp:discover"
            MX: 1
            ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1

            """;
        using var socket = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
        socket.Bind(new IPEndPoint(IPAddress.Any, 0));
        var payload = Encoding.ASCII.GetBytes(search.ReplaceLineEndings("\r\n"));
        await socket.SendToAsync(payload, SocketFlags.None,
            new IPEndPoint(IPAddress.Parse("239.255.255.250"), 1900), ct).ConfigureAwait(false);

        var buffer = new byte[2048];
        var watch = Stopwatch.StartNew();
        // 多等几个回应：一个网段里可能有电视盒子之类也应 SSDP，要挑出真正的网关设备。
        while (watch.Elapsed < TimeSpan.FromSeconds(2) && !ct.IsCancellationRequested)
        {
            try
            {
                using var timer = CancellationTokenSource.CreateLinkedTokenSource(ct);
                timer.CancelAfter(TimeSpan.FromSeconds(2) - watch.Elapsed);
                var got = await socket.ReceiveFromAsync(
                        buffer, SocketFlags.None, new IPEndPoint(IPAddress.Any, 0), timer.Token)
                    .ConfigureAwait(false);
                var text = Encoding.ASCII.GetString(buffer, 0, got.ReceivedBytes);
                var match = Regex.Match(text, @"(?im)^LOCATION:\s*(\S+)\s*$");
                if (match.Success && Uri.TryCreate(match.Groups[1].Value, UriKind.Absolute, out var uri))
                    return uri;
            }
            catch (OperationCanceledException) when (!ct.IsCancellationRequested) { return null; }
            catch (SocketException) { return null; }
        }
        return null;
    }

    private static (string Url, string ServiceType)? ControlUrl(
        string description, Uri location, string serviceType)
    {
        var index = description.IndexOf(serviceType, StringComparison.OrdinalIgnoreCase);
        if (index < 0) return null;
        var match = Regex.Match(description[index..], @"<controlURL>\s*([^<\s]+)\s*</controlURL>",
            RegexOptions.IgnoreCase);
        if (!match.Success) return null;
        var raw = match.Groups[1].Value;
        var url = raw.StartsWith("http", StringComparison.OrdinalIgnoreCase)
            ? raw
            : new Uri(location, raw).ToString();
        return (url, serviceType);
    }

    private static async Task<string?> SoapAsync(
        HttpClient http, string url, string serviceType, string action, string body,
        CancellationToken ct)
    {
        var envelope = $"""
            <?xml version="1.0"?>
            <s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
            <s:Body>{body}</s:Body>
            </s:Envelope>
            """;
        using var content = new StringContent(envelope, Encoding.UTF8, "text/xml");
        content.Headers.Add("SOAPACTION", $"\"{serviceType}#{action}\"");
        using var request = new HttpRequestMessage(HttpMethod.Post, url) { Content = content };
        using var reply = await http.SendAsync(request, ct).ConfigureAwait(false);
        var text = await reply.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
        return reply.IsSuccessStatusCode ? text : null;
    }

    /// <summary>所有网卡上的默认网关。多网卡机器（本机就是）要每个都问一遍。</summary>
    private static List<IPAddress> DefaultGateways()
    {
        var found = new List<IPAddress>();
        foreach (var nic in NetworkInterface.GetAllNetworkInterfaces())
        {
            if (nic.OperationalStatus != OperationalStatus.Up) continue;
            if (nic.NetworkInterfaceType == NetworkInterfaceType.Loopback) continue;
            foreach (var gateway in nic.GetIPProperties().GatewayAddresses)
            {
                var address = gateway.Address;
                if (address is null) continue;
                if (address.AddressFamily != AddressFamily.InterNetwork) continue;
                if (address.Equals(IPAddress.Any)) continue;
                if (!found.Contains(address)) found.Add(address);
            }
        }
        return found;
    }

    /// <summary>
    /// 我们和某个目标通信时会用哪个本地地址。
    ///
    /// 用一个未连接的 UDP socket "connect" 一下让内核选路：不发任何包，只是问路由表。
    /// 多网卡机器上不能拿"第一个网卡的地址"充数——填错 PCP 会回 ADDRESS_MISMATCH，
    /// UPnP 会把映射建到一个不存在的内网主机上。
    /// </summary>
    private static IPAddress? LocalAddressTowards(IPAddress target)
    {
        if (target.Equals(IPAddress.Any)) return null;
        try
        {
            using var probe = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
            probe.Connect(new IPEndPoint(target, 9));
            return (probe.LocalEndPoint as IPEndPoint)?.Address;
        }
        catch (SocketException) { return null; }
    }

    private static void MapV4Into(IPAddress v4, Span<byte> destination)
    {
        // ::ffff:a.b.c.d
        destination.Clear();
        destination[10] = 0xFF;
        destination[11] = 0xFF;
        v4.GetAddressBytes().CopyTo(destination[12..]);
    }

    private static IPAddress? ReadV4From(ReadOnlySpan<byte> mapped)
    {
        if (mapped.Length != 16) return null;
        if (mapped[10] != 0xFF || mapped[11] != 0xFF) return null;
        return new IPAddress(mapped[12..].ToArray());
    }

    private static ushort ReadUInt16(ReadOnlySpan<byte> data)
        => (ushort)((data[0] << 8) | data[1]);

    private static void WriteUInt16(Span<byte> data, ushort value)
    {
        data[0] = (byte)(value >> 8);
        data[1] = (byte)value;
    }

    private static void WriteUInt32(Span<byte> data, uint value)
    {
        data[0] = (byte)(value >> 24);
        data[1] = (byte)(value >> 16);
        data[2] = (byte)(value >> 8);
        data[3] = (byte)value;
    }
}
