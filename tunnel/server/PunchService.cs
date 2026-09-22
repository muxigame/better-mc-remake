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
    /// 出口候选的保鲜间隔。
    ///
    /// NAT 的 UDP 映射是靠流量续命的，运营商那边通常几十秒就老化。只在启动时探一次
    /// 的话，探到的 (IP, 端口) 过几分钟就失效了，而我们还在一直把它报给客户端——
    /// 玩家照着这个地址打，包落到一个早就不存在的映射上，两边各发几百个包互相收到 0 个。
    /// 定期重探既让映射一直活着，也保证报上去的地址是当前有效的那个。
    /// </summary>
    private static readonly TimeSpan RefreshInterval = TimeSpan.FromSeconds(20);

    /// <summary>保鲜时的探测轮数。只是续命，不需要首次那么多轮。</summary>
    private const int RefreshRounds = 2;

    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(10) };
    private readonly string _baseUrl;
    private readonly string _serverId;
    private readonly byte[] _controlSecret;
    private readonly string _masterToken;
    private readonly string _reflectorHost;
    private readonly string _targetHost;
    private readonly int _targetPort;

    private Socket? _socket;
    private volatile string[] _candidates = [];
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
        _reflectorHost = reflectorHost;
        _targetHost = targetHost;
        _targetPort = targetPort;
    }

    /// <summary>当前这个打洞 socket 的全部出口候选，供 register 一并上报。</summary>
    public IReadOnlyList<string> Candidates => _candidates;

    public async Task RunAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            if (_socket is null && !await PrepareSocketAsync(ct).ConfigureAwait(false))
            {
                try { await Task.Delay(TimeSpan.FromSeconds(30), ct).ConfigureAwait(false); }
                catch (OperationCanceledException) { return; }
                continue;
            }

            try
            {
                var requests = await PollAsync(ct).ConfigureAwait(false);
                foreach (var request in requests)
                    await HandleAsync(request, ct).ConfigureAwait(false);

                // 没有打洞任务时才保鲜，免得和正在进行的打洞抢同一个 socket。
                if (requests.Count == 0 && DateTimeOffset.UtcNow - _refreshed > RefreshInterval)
                    await RefreshCandidatesAsync(ct).ConfigureAwait(false);
            }
            catch (Exception error) when (error is not OperationCanceledException)
            {
                Program.Log($"打洞轮询失败：{error.Message}");
            }

            try { await Task.Delay(PollInterval, ct).ConfigureAwait(false); }
            catch (OperationCanceledException) { return; }
        }
    }

    private async Task<bool> PrepareSocketAsync(CancellationToken ct)
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
                return false;
            }
            _socket = socket;
            _candidates = discovery.Candidates.Select(x => x.ToString()).ToArray();
            _findable = !discovery.AddressDependent;
            _refreshed = DateTimeOffset.UtcNow;
            Program.Log("本机 NAT 类型：" + (discovery.AddressDependent
                ? "地址相关型 NAT（换个目标就换映射端口）——对端需要在邻近端口扫描才能命中"
                : "端点无关型 NAT（对所有目标同一个映射）——对端照着这个地址直接打就行"));
            Program.Log($"打洞就绪，本机出口候选 {_candidates.Length} 个：{string.Join("，", _candidates)}");
            return true;
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            Program.Log($"准备打洞 socket 失败：{error.Message}");
            socket.Close();
            return false;
        }
    }

    /// <summary>重探一次出口，顺带把 NAT 映射续上。地址变了就记一笔，下次 register 会报新的。</summary>
    private async Task RefreshCandidatesAsync(CancellationToken ct)
    {
        var socket = _socket;
        if (socket is null) return;
        _refreshed = DateTimeOffset.UtcNow;
        try
        {
            var discovery = await NatDiscovery
                .DiscoverAsync(socket, _reflectorHost, ReflectorPorts, RefreshRounds, ct)
                .ConfigureAwait(false);
            if (discovery.Candidates.Count == 0) return;

            var fresh = discovery.Candidates.Select(x => x.ToString()).ToArray();
            if (!fresh.SequenceEqual(_candidates))
                Program.Log($"出口候选已变化：{string.Join("，", fresh)}");
            _candidates = fresh;
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            Program.Log($"出口保鲜失败（不影响已有线路）：{error.Message}");
        }
    }

    private readonly record struct PunchRequest(
        ulong Session, IReadOnlyList<IPEndPoint> Candidates, DateTimeOffset PunchAt);

    private async Task<List<PunchRequest>> PollAsync(CancellationToken ct)
    {
        var timestamp = (DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0)
            .ToString("R", System.Globalization.CultureInfo.InvariantCulture);
        using var hmac = new HMACSHA256(_controlSecret);
        var signature = Convert.ToHexString(hmac.ComputeHash(Encoding.ASCII.GetBytes(timestamp)))
            .ToLowerInvariant();

        using var request = new HttpRequestMessage(HttpMethod.Get,
            $"{_baseUrl}/api/v1/tunnel/punch/pending?server_id={Uri.EscapeDataString(_serverId)}");
        request.Headers.Add("X-Muxi-Signature", signature);
        request.Headers.Add("X-Muxi-Timestamp", timestamp);

        using var response = await _http.SendAsync(request, ct).ConfigureAwait(false);
        var result = new List<PunchRequest>();
        if (!response.IsSuccessStatusCode) return result;

        using var document = JsonDocument.Parse(
            await response.Content.ReadAsStringAsync(ct).ConfigureAwait(false));
        if (!document.RootElement.TryGetProperty("punchRequests", out var list)) return result;

        foreach (var item in list.EnumerateArray())
        {
            if (!item.TryGetProperty("sessionId", out var sid)) continue;
            if (!ulong.TryParse(sid.GetString(), System.Globalization.NumberStyles.HexNumber,
                    System.Globalization.CultureInfo.InvariantCulture, out var session)) continue;
            var peers = new List<IPEndPoint>();
            if (item.TryGetProperty("candidates", out var candidates))
                foreach (var candidate in candidates.EnumerateArray())
                    if (IPEndPoint.TryParse(candidate.GetString() ?? "", out var endpoint))
                        peers.Add(endpoint);
            var at = item.TryGetProperty("punchAt", out var punchAt)
                     && punchAt.TryGetDouble(out var seconds)
                ? DateTimeOffset.FromUnixTimeMilliseconds((long)(seconds * 1000))
                : DateTimeOffset.UtcNow;
            if (peers.Count > 0) result.Add(new PunchRequest(session, peers, at));
        }
        return result;
    }

    private async Task HandleAsync(PunchRequest request, CancellationToken ct)
    {
        var socket = _socket;
        if (socket is null) return;
        var localPort = ((IPEndPoint)socket.LocalEndPoint!).Port;

        Program.Log($"收到打洞请求 会话 {request.Session:x16}，对端候选 {request.Candidates.Count} 个");
        // 和隧道握手同源：客户端手里是控制面派生的 token，不是 master。
        //
        // 打洞只认当前这一代：UdpPuncher 的签和验共用一个密钥，收不下两代。
        // 客户端是在连接前几秒才去取的，所以只有恰好跨在换代那一瞬间才会对不上，
        // 而那一下也只是打洞失败退回隧道——隧道那边是认两代的，玩家不会掉线。
        var punchSecret = PunchProtocol.DeriveSecret(
            TunnelProtocol.DeriveClientToken(_masterToken, DateTimeOffset.UtcNow));
        var outcome = await UdpPuncher.PunchAsync(
            socket, request.Candidates, request.Session, punchSecret,
            request.PunchAt, TimeSpan.FromSeconds(10),
            sweepPorts: _findable,
            message => Program.Log("打洞：" + message), ct).ConfigureAwait(false);

        // 无论成败这个 socket 都不再复用：成功了要把端口让给 QUIC，失败了 NAT 上
        // 已经留下一堆指向错误地址的映射，换一个干净的更省事。
        _socket = null;
        _candidates = [];
        socket.Close();

        if (!outcome.Success)
        {
            Program.Log($"打洞未成功（发出 {outcome.Sent}，收到对方 {outcome.PunchesHeard}），客户端会回退到中转");
            return;
        }

        _ = ServeAsync(localPort, outcome.Peer!, ct);
    }

    /// <summary>打通之后在同一个本地端口起 QUIC，等客户端连进来。</summary>
    private async Task ServeAsync(int localPort, IPEndPoint peer, CancellationToken ct)
    {
        try
        {
            using var certificate = QuicTunnel.CreateEphemeralCertificate();
            await using var listener = await QuicTunnel
                .ListenAsync(localPort, certificate, AddressFamily.InterNetwork, ct)
                .ConfigureAwait(false);
            Program.Log($"P2P 监听已就绪 :{localPort}，等待 {peer} 接入");

            using var accept = CancellationTokenSource.CreateLinkedTokenSource(ct);
            accept.CancelAfter(TimeSpan.FromSeconds(20));
            await using var connection = await listener.AcceptConnectionAsync(accept.Token)
                .ConfigureAwait(false);
            Program.Log($"P2P 连接已建立 {connection.RemoteEndPoint}");

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
    {
        await using (stream)
        {
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
            await Task.WhenAll(
                Pump(stream, gameStream, ct),
                Pump(gameStream, stream, ct)).ConfigureAwait(false);
        }
    }

    private static async Task Pump(Stream from, Stream to, CancellationToken ct)
    {
        try { await from.CopyToAsync(to, 64 * 1024, ct).ConfigureAwait(false); }
        catch (Exception error) when (error is IOException or SocketException
                                          or OperationCanceledException or QuicException) { }
    }
}
