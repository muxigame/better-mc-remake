using System.Net;
using System.Net.Quic;
using System.Net.Sockets;
using System.Runtime.Versioning;
using System.Text.Json;
using BatterMC.Protocol;

namespace BatterMC.Core;

/// <summary>
/// 打洞得来的隧道。
///
/// 存在的理由只有一个：省服务器带宽。中转线路本身又快又稳，但玩家的游戏流量全
/// 从我们自己的服务器过，那笔带宽是实打实的成本。这条路让数据在两端之间直连，
/// 服务器只承担几十个字节的信令。
///
/// 流程：
///   1. 用一个 UDP socket 向反射器的多个端口各问几次，把**全部**出口候选收齐
///      （这条家宽在运营商 NAT 后面且有多个出口，只报一个的话对端约一半概率打空）
///   2. 把候选交给控制面，换回对端候选和一个约定打洞时刻
///   3. 到点两边同时朝对方所有候选猛发，任意一对先通就用哪一对
///   4. 关掉裸 socket，让 QUIC 绑同一个本地端口——NAT 映射按本地端口记账，
///      所以握手直接穿过刚打好的洞
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class P2PTunnel : IMuxiTunnel
{
    /// <summary>反射器端口。多问几个才能发现自己的多个出口身份。</summary>
    private static readonly int[] ReflectorPorts = [45701, 45702, 45703];
    private const int DiscoveryRounds = 6;

    private readonly QuicConnection _connection;
    private readonly IPEndPoint _peer;

    private P2PTunnel(QuicConnection connection, IPEndPoint peer)
    {
        _connection = connection;
        _peer = peer;
    }

    public bool IsAlive => _connection.RemoteEndPoint is not null;
    public string Describe => $"P2P 直连 {_peer}";

    public async Task<Stream> OpenStreamAsync(CancellationToken ct)
        => await _connection.OpenOutboundStreamAsync(QuicStreamType.Bidirectional, ct)
            .ConfigureAwait(false);

    /// <summary>
    /// 走完整套流程。任何一步失败都抛，调用方据此回退到隧道或中转——打洞本来就
    /// 是尽力而为，打不通不该让玩家进不去游戏。
    /// </summary>
    public static async Task<P2PTunnel> EstablishAsync(
        string controlPlaneBaseUrl, string serverId, string token, string reflectorHost,
        IProgress<string>? progress, CancellationToken ct)
    {
        Log.Info(QuicTunnel.DescribeSupport());
        if (!QuicTunnel.IsSupported)
            throw new InvalidOperationException(
                "无法使用 P2P 直连：" + QuicTunnel.DescribeSupport());

        var socket = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
        int localPort;
        try
        {
            socket.Bind(new IPEndPoint(IPAddress.Any, 0));
            localPort = ((IPEndPoint)socket.LocalEndPoint!).Port;

            progress?.Report("正在探测本机公网出口");
            var discovery = await NatDiscovery
                .DiscoverAsync(socket, reflectorHost, ReflectorPorts, DiscoveryRounds, ct)
                .ConfigureAwait(false);
            if (discovery.Candidates.Count == 0)
                throw new InvalidOperationException(
                    $"探测不到自己的公网出口（发出 {discovery.Sent} 个包，回应 {discovery.Replies} 个）");
            Log.Info("本机 NAT 类型：" + (discovery.AddressDependent
                ? "地址相关型 NAT（换个目标就换映射端口）——对端需要在邻近端口扫描才能命中"
                : "端点无关型 NAT（对所有目标同一个映射）——对端照着这个地址直接打就行"));
            Log.Info($"本机出口候选 {discovery.Candidates.Count} 个：" +
                     string.Join("，", discovery.Candidates));

            progress?.Report($"与服务器交换连接信息（本机 {discovery.Candidates.Count} 个出口）");
            var rendezvous = await ExchangeAsync(
                controlPlaneBaseUrl, serverId, discovery.Candidates, ct).ConfigureAwait(false);
            if (rendezvous.PeerCandidates.Count == 0)
                throw new InvalidOperationException("服务器侧没有可用的打洞候选");

            var secret = PunchProtocol.DeriveSecret(token);
            progress?.Report($"正在打洞（对端 {rendezvous.PeerCandidates.Count} 个候选）");
            var outcome = await UdpPuncher.PunchAsync(
                socket, rendezvous.PeerCandidates, rendezvous.Session, secret,
                rendezvous.PunchAt, TimeSpan.FromSeconds(10),
                sweepPorts: !discovery.AddressDependent,
                message => Log.Info("打洞：" + message), ct).ConfigureAwait(false);
            if (!outcome.Success)
                throw new InvalidOperationException(
                    $"打洞未成功（发出 {outcome.Sent} 个包，收到对方 {outcome.PunchesHeard} 个）");

            var peer = outcome.Peer!;
            progress?.Report($"打洞成功，正在建立加密隧道");
            // 交接必须快：运营商 NAT 的 UDP 映射老化只有几十秒，socket 一关就得
            // 立刻让 QUIC 顶上，中间拖久了洞就塌了。
            socket.Close();
            var connection = await QuicTunnel.ConnectAsync(localPort, peer, ct).ConfigureAwait(false);
            Log.Info($"P2P 隧道已建立 {peer}，本地端口 {localPort}");
            return new P2PTunnel(connection, peer);
        }
        catch
        {
            try { socket.Close(); } catch { }
            throw;
        }
    }

    private readonly record struct Rendezvous(
        ulong Session, IReadOnlyList<IPEndPoint> PeerCandidates, DateTimeOffset PunchAt);

    private static async Task<Rendezvous> ExchangeAsync(
        string baseUrl, string serverId, IReadOnlyList<IPEndPoint> mine, CancellationToken ct)
    {
        var session = (ulong)Random.Shared.NextInt64();
        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(10) };
        var url = $"{baseUrl.TrimEnd('/')}/api/v1/tunnel/punch?server_id={Uri.EscapeDataString(serverId)}";
        // 手工拼这个 JSON 而不是用 PostAsJsonAsync：后者走反射序列化，在裁剪后的
        // 单文件发布里会在运行时找不到类型。仓库其他地方用源生成上下文是同一个原因。
        var body = new System.Text.StringBuilder()
            .Append("{\"sessionId\":\"").Append(session.ToString("x16")).Append("\",\"candidates\":[");
        for (var i = 0; i < mine.Count; i++)
        {
            if (i > 0) body.Append(',');
            body.Append('"').Append(JsonEncodedText.Encode(mine[i].ToString())).Append('"');
        }
        body.Append("]}");
        using var content = new StringContent(body.ToString(), System.Text.Encoding.UTF8, "application/json");
        using var response = await http.PostAsync(url, content, ct).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();

        using var document = JsonDocument.Parse(
            await response.Content.ReadAsStringAsync(ct).ConfigureAwait(false));
        var root = document.RootElement;

        var peers = new List<IPEndPoint>();
        if (root.TryGetProperty("peerCandidates", out var list) && list.ValueKind == JsonValueKind.Array)
            foreach (var item in list.EnumerateArray())
                if (IPEndPoint.TryParse(item.GetString() ?? "", out var endpoint)) peers.Add(endpoint);

        var punchAt = root.TryGetProperty("punchAt", out var at) && at.TryGetDouble(out var seconds)
            ? DateTimeOffset.FromUnixTimeMilliseconds((long)(seconds * 1000))
            : DateTimeOffset.UtcNow.AddSeconds(1);
        return new Rendezvous(session, peers, punchAt);
    }

    public async ValueTask DisposeAsync()
    {
        try { await _connection.CloseAsync(0).ConfigureAwait(false); } catch { }
        await _connection.DisposeAsync().ConfigureAwait(false);
        Log.Info($"P2P 隧道 {_peer} 已关闭");
    }
}
