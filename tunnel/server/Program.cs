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
            _ = punch.RunAsync(lifetime.Token);
            Log($"打洞反射器 {reflector}");

            _ = new ControlPlane(controlPlane, serverId, token, listenPort, targetPort,
                () => punch.Candidates).RunAsync(lifetime.Token);
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

            using var game = new TcpClient();
            try
            {
                using var dial = CancellationTokenSource.CreateLinkedTokenSource(ct);
                dial.CancelAfter(LocalDialTimeout);
                await game.ConnectAsync(targetHost, targetPort, dial.Token).ConfigureAwait(false);
            }
            catch (Exception error)
            {
                await TunnelProtocol.WriteFrameAsync(stream, TunnelProtocol.Frame.Reject,
                    Encoding.UTF8.GetBytes("本地 Minecraft 未就绪"), ct).ConfigureAwait(false);
                Log($"{remote} 接本机 {targetHost}:{targetPort} 失败：{error.Message}");
                return;
            }

            game.NoDelay = true;
            await TunnelProtocol.WriteFrameAsync(stream, TunnelProtocol.Frame.StreamReady, ReadOnlyMemory<byte>.Empty, ct)
                .ConfigureAwait(false);

            var gameStream = game.GetStream();
            var up = Pump(stream, gameStream, ct);
            var down = Pump(gameStream, stream, ct);
            await Task.WhenAll(up, down).ConfigureAwait(false);
        }
    }

    private static async Task Pump(Stream from, Stream to, CancellationToken ct)
    {
        try
        {
            await from.CopyToAsync(to, 64 * 1024, ct).ConfigureAwait(false);
        }
        catch (Exception error) when (error is IOException or SocketException
                                          or OperationCanceledException) { }
    }

    private static string? Value(string[] args, string name)
    {
        var index = Array.FindIndex(args, a => a.Equals(name, StringComparison.OrdinalIgnoreCase));
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
    }

    internal static void Log(string message) =>
        Console.WriteLine($"[{DateTime.Now:HH:mm:ss}] {message}");
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
