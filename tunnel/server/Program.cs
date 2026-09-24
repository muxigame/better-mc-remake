using System.Buffers.Binary;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using BatterMC.Protocol;

namespace MuxiTunnel.Server;

/// <summary>
/// 服务端侧隧道工具。和 Minecraft 跑在同一台机器上，由 start.bat 在 Minecraft
/// 之前拉起。
///
/// 它是所有通道的共同终点：客户端无论走 IPv6 直连、路由器映射还是中继，最终
/// 都连到这里，再由它在本机把流量接进 Minecraft。这样 Minecraft 只看得到一条
/// 本地连接，公网那一段的建链、保活、换线全在两个端侧工具之间完成。
/// </summary>
internal static class Program
{
    private static readonly TimeSpan LocalDialTimeout = TimeSpan.FromSeconds(10);

    private static async Task<int> Main(string[] args)
    {
        Console.OutputEncoding = Encoding.UTF8;

        var listenPort = int.Parse(Value(args, "--port") ?? "25540");
        var target = Value(args, "--target") ?? "127.0.0.1:25565";
        var token = Value(args, "--token") ?? Environment.GetEnvironmentVariable("MUXI_TUNNEL_TOKEN");
        if (string.IsNullOrWhiteSpace(token))
        {
            Console.Error.WriteLine("缺少共享密钥：用 --token 或环境变量 MUXI_TUNNEL_TOKEN 提供");
            return 2;
        }

        var parts = target.Split(':');
        var targetHost = parts[0];
        var targetPort = parts.Length > 1 ? int.Parse(parts[1]) : 25565;
        var sessions = new SessionTable();

        // 双栈监听：IPv6 直连和 IPv4 中继回来的流量都落在同一个端口上。
        var listener = new TcpListener(IPAddress.IPv6Any, listenPort);
        listener.Server.SetSocketOption(SocketOptionLevel.IPv6, SocketOptionName.IPv6Only, false);
        listener.Start(128);

        Log($"隧道服务端已监听 [::]:{listenPort}（双栈）");
        Log($"本地目标 {targetHost}:{targetPort}");
        Log($"心跳 {TunnelProtocol.HeartbeatInterval.TotalSeconds:0}s，判死 {TunnelProtocol.HeartbeatTimeout.TotalSeconds:0}s");

        using var lifetime = new CancellationTokenSource();
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; lifetime.Cancel(); };

        // 控制面可选：没配就退化成"客户端必须知道固定地址"，配了才能应对地址轮换。
        var controlPlane = Value(args, "--control-plane")
                           ?? Environment.GetEnvironmentVariable("MUXI_CONTROL_PLANE");
        if (!string.IsNullOrWhiteSpace(controlPlane))
        {
            var serverId = Value(args, "--server-id") ?? "default";
            var reflector = Value(args, "--reflector") ?? "110.42.51.3,191.40.37.147";
            Log($"控制面 {controlPlane}（serverId={serverId}），每 30s 上报一次");

            // 打洞服务先起来：它探到的出口候选要随 register 一起上报，客户端才知道
            // 该往哪打。探不到就退化成只有隧道和中转，不影响玩家进服。
            var punch = new PunchService(controlPlane, serverId, token, reflector,
                targetHost, targetPort);
            var control = new ControlPlane(controlPlane, serverId, token, listenPort, targetPort,
                () => punch.Candidates);
            // 接线要在开跑之前：探测只花几秒，晚接一步就可能漏掉第一次通知，
            // 那之后要白等整整一个上报周期。
            punch.CandidatesChanged += control.Nudge;
            _ = punch.RunAsync(lifetime.Token);
            Log($"打洞反射器 {reflector}");

            // 把 QUIC 这一侧的一次性开销全部提前付掉：共享证书 + msquic 首次初始化。
            //
            // 这两样都发生在"打洞成功之后、监听起来之前"那个缝里，而那一刻对端已经在
            // 发 QUIC Initial 了——晚就绪就意味着它第一个包被丢，然后要等 msquic 约
            // 一秒的 PTO 重传。实测：重启后第一次会话的握手 1142ms，之后每次 66~85ms，
            // 而且**只预热证书不够**（证书自己只要 71~134ms），差值来自 msquic 加载
            // dll、建 registration/configuration 这些进程级初始化。
            //
            // 所以这里真的去起一个监听器再关掉，把那条路径完整走一遍。
            _ = Task.Run(async () =>
            {
                try
                {
                    var thumb = QuicTunnel.SharedEphemeralCertificate.Thumbprint;
                    // 用完就关。留着常驻试过一次，疑似导致后续 listener 收不到 inbound 流。
                    //
                    // 每次打洞都要在新端口上新建一个 QuicListener（端口是打通的那个洞
                    // 的本地端口，没法复用），而实测每次都要约 1 秒——对端那一刻已经
                    // 在发 Initial 了，丢掉之后要等 msquic 自己的 PTO。用完就关的预热
                    // 只能消掉一部分：关掉之后 msquic 的进程级状态会回冷，下一次打洞
                    // 又要重新付。留一个活的在手上，让它一直是热的。
                    await using var warm = await QuicTunnel
                        .ListenAsync(0, QuicTunnel.SharedEphemeralCertificate,
                            System.Net.Sockets.AddressFamily.InterNetwork, lifetime.Token)
                        .ConfigureAwait(false);
                    Log($"QUIC 已预热（证书指纹 {thumb[..8]}）");
                }
                catch (Exception error) when (error is not OperationCanceledException)
                {
                    Log($"QUIC 预热失败（不影响后续，只是第一次会话会慢约一秒）：{error.Message}");
                }
            });

            _ = control.RunAsync(lifetime.Token);
        }
        else
        {
            Log("未配置控制面：客户端只能靠清单里写死的地址找过来");
        }

        try
        {
            while (!lifetime.IsCancellationRequested)
            {
                var client = await listener.AcceptTcpClientAsync(lifetime.Token).ConfigureAwait(false);
                _ = HandleAsync(client, token, sessions, targetHost, targetPort, lifetime.Token);
            }
        }
        catch (OperationCanceledException) { }
        listener.Stop();
        Log("已停止");
        return 0;
    }

    private static async Task HandleAsync(
        TcpClient client, string masterToken, SessionTable sessions,
        string targetHost, int targetPort, CancellationToken ct)
    {
        var remote = client.Client.RemoteEndPoint?.ToString() ?? "?";
        try
        {
            client.NoDelay = true;
            var stream = client.GetStream();

            var preamble = new byte[5];
            await stream.ReadExactlyAsync(preamble, ct).ConfigureAwait(false);
            if (!TunnelProtocol.PreambleMatches(preamble))
            {
                Log($"{remote} 前导字节不匹配，断开");
                client.Dispose();
                return;
            }

            var (frame, payload) = await TunnelProtocol.ReadFrameAsync(stream, ct).ConfigureAwait(false);
            switch (frame)
            {
                case TunnelProtocol.Frame.Hello:
                    await ControlAsync(client, stream, masterToken, sessions, remote, ct).ConfigureAwait(false);
                    return;
                case TunnelProtocol.Frame.OpenStream:
                    await StreamAsync(client, stream, sessions, payload, targetHost, targetPort,
                        remote, ct).ConfigureAwait(false);
                    return;
                default:
                    Log($"{remote} 开场帧非法 {frame}，断开");
                    client.Dispose();
                    return;
            }
        }
        catch (Exception error) when (error is IOException or SocketException
                                          or OperationCanceledException or InvalidDataException)
        {
            client.Dispose();
        }
    }

    /// <summary>控制连接：验身份、发会话号，然后一直心跳到玩家退出。</summary>
    private static async Task ControlAsync(
        TcpClient client, NetworkStream stream, string masterToken, SessionTable sessions,
        string remote, CancellationToken ct)
    {
        using (client)
        {
            var nonce = RandomNumberGenerator.GetBytes(32);
            await TunnelProtocol.WriteFrameAsync(stream, TunnelProtocol.Frame.Welcome, nonce, ct)
                .ConfigureAwait(false);

            var (frame, signature) = await TunnelProtocol.ReadFrameAsync(stream, ct).ConfigureAwait(false);
            // 每次会话现算：进程一跑好几天，密钥换代时不重启也得认新的。
            var accepted = TunnelProtocol.AcceptedClientTokens(masterToken, DateTimeOffset.UtcNow)
                .Select(TunnelProtocol.DeriveSecret).ToArray();
            if (frame != TunnelProtocol.Frame.Hello ||
                !accepted.Any(candidate => TunnelProtocol.VerifySignature(candidate, nonce, signature)))
            {
                Log($"{remote} 鉴权失败");
                await TunnelProtocol.WriteFrameAsync(stream, TunnelProtocol.Frame.Reject,
                    Encoding.UTF8.GetBytes("鉴权失败"), ct).ConfigureAwait(false);
                return;
            }

            var session = sessions.Create();
            var sessionBytes = new byte[8];
            BinaryPrimitives.WriteUInt64BigEndian(sessionBytes, session);
            await TunnelProtocol.WriteFrameAsync(stream, TunnelProtocol.Frame.Welcome, sessionBytes, ct)
                .ConfigureAwait(false);
            Log($"{remote} 控制连接已建立，会话 {session:x16}");

            try
            {
                while (!ct.IsCancellationRequested)
                {
                    using var idle = CancellationTokenSource.CreateLinkedTokenSource(ct);
                    idle.CancelAfter(TunnelProtocol.HeartbeatTimeout);
                    var (beat, _) = await TunnelProtocol.ReadFrameAsync(stream, idle.Token)
                        .ConfigureAwait(false);
                    if (beat == TunnelProtocol.Frame.Ping)
                    {
                        sessions.Touch(session);
                        await TunnelProtocol.WriteFrameAsync(stream, TunnelProtocol.Frame.Pong, ReadOnlyMemory<byte>.Empty, ct)
                            .ConfigureAwait(false);
                    }
                }
            }
            catch (Exception error) when (error is IOException or SocketException
                                              or OperationCanceledException)
            {
                // 玩家退出或链路断开，都走这里
            }
            finally
            {
                sessions.Drop(session);
                Log($"{remote} 控制连接结束，会话 {session:x16}");
            }
        }
    }

    /// <summary>数据连接：校验会话号，接通本机 Minecraft，然后纯字节对拷。</summary>
    private static async Task StreamAsync(
        TcpClient client, NetworkStream stream, SessionTable sessions, byte[] payload,
        string targetHost, int targetPort, string remote, CancellationToken ct)
    {
        using (client)
        {
            if (payload.Length != 8 ||
                !sessions.IsLive(BinaryPrimitives.ReadUInt64BigEndian(payload)))
            {
                await TunnelProtocol.WriteFrameAsync(stream, TunnelProtocol.Frame.Reject,
                    Encoding.UTF8.GetBytes("会话无效"), ct).ConfigureAwait(false);
                Log($"{remote} 数据连接会话无效");
                return;
            }

            // StreamReady 必须在读客户端开场**之前**发。
            //
            // 客户端开数据连接的约定是"等 StreamReady 再说话"，而下面的开场嗅探要
            // 先读客户端的字节——两边互等，客户端 10 秒后超时。这个死锁是加带宽
            // 通道时引入的：嗅探被插在了发 StreamReady 前面。
            //
            // 提前发的代价是 StreamReady 不再含有"本机 Minecraft 也活着"的意思，
            // 只表示"会话认了，你说吧"。这不算损失：客户端拿到流之后本来就要做一次
            // 真实的状态查询，那才是够格的可达性判据。P2P 那条路径也是客户端先说话，
            // 现在两边一致了。
            await TunnelProtocol.WriteFrameAsync(stream, TunnelProtocol.Frame.StreamReady,
                ReadOnlyMemory<byte>.Empty, ct).ConfigureAwait(false);

            // 带宽测试走同一条流，只是开场多一个标记。做在这里是为了能和 P2P 做
            // 同机同时刻的对照：同一条线路上 UDP 跑 1 KB/s 而 TCP 正常，才能把锅
            // 定在"运营商掐 UDP"上，而不是笼统地说"这条线路不行"。
            var scratch = new byte[64];
            int? bulk;
            int headLength;
            try
            {
                (bulk, headLength) = await PunchService
                    .TryReadBulkAsync(stream, scratch, ct).ConfigureAwait(false);
            }
            catch (Exception error) when (error is not OperationCanceledException)
            {
                Log($"{remote} 读开场失败：{error.Message}");
                return;
            }
            if (headLength <= 0) return;
            if (bulk is { } want)
            {
                Log($"{remote} 带宽测试：回 {want / 1024} KB");
                try { await PunchService.ServeBulkAsync(stream, want, ct).ConfigureAwait(false); }
                catch (Exception error) when (error is not OperationCanceledException)
                {
                    Log($"{remote} 带宽测试中断：{error.Message}");
                }
                return;
            }

            using var game = new TcpClient();
            try
            {
                using var dial = CancellationTokenSource.CreateLinkedTokenSource(ct);
                dial.CancelAfter(LocalDialTimeout);
                await game.ConnectAsync(targetHost, targetPort, dial.Token).ConfigureAwait(false);
            }
            catch (Exception error)
            {
                // 这时候 StreamReady 已经发出去了，再塞一个 Reject 只会混进玩家的
                // 字节流里被当成 Minecraft 报文。直接关掉，客户端读到 EOF 自会换线。
                Log($"{remote} 接本机 {targetHost}:{targetPort} 失败：{error.Message}");
                return;
            }

            game.NoDelay = true;
            // 探测开场读走的字节是玩家的握手包，先补给 Minecraft
            await game.GetStream().WriteAsync(scratch.AsMemory(0, headLength), ct)
                .ConfigureAwait(false);

            // 带收尾的对拷，理由见 StreamBridge。
            await StreamBridge.RunAsync(stream, game.GetStream(), StreamBridge.DefaultLinger, ct)
                .ConfigureAwait(false);
        }
    }

    private static string? Value(string[] args, string name)
    {
        var index = Array.FindIndex(args, a => a.Equals(name, StringComparison.OrdinalIgnoreCase));
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
    }

    internal static void Log(string message) =>
        Console.WriteLine($"[{DateTime.Now:HH:mm:ss.fff}] {message}");
}

/// <summary>
/// 活跃会话表。数据连接必须带一个控制连接给过的会话号，否则任何人扫到这个
/// 端口都能把流量灌进 Minecraft。
/// </summary>
internal sealed class SessionTable
{
    private readonly Dictionary<ulong, DateTime> _sessions = new();
    private readonly object _gate = new();

    public ulong Create()
    {
        Span<byte> raw = stackalloc byte[8];
        RandomNumberGenerator.Fill(raw);
        var id = BinaryPrimitives.ReadUInt64BigEndian(raw);
        lock (_gate) _sessions[id] = DateTime.UtcNow;
        return id;
    }

    public void Touch(ulong id)
    {
        lock (_gate) if (_sessions.ContainsKey(id)) _sessions[id] = DateTime.UtcNow;
    }

    public bool IsLive(ulong id)
    {
        lock (_gate)
        {
            if (!_sessions.TryGetValue(id, out var seen)) return false;
            return DateTime.UtcNow - seen < TunnelProtocol.HeartbeatTimeout;
        }
    }

    public void Drop(ulong id)
    {
        lock (_gate) _sessions.Remove(id);
    }
}
