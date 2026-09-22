using System.Buffers.Binary;
using System.Net;
using System.Net.Sockets;
using System.Text;
using BatterMC.Protocol;

namespace BatterMC.Core;

/// <summary>
/// 客户端侧隧道工具。和服务端侧的 muxi-tunnel-server 配对。
///
/// 控制连接在玩家点「开始游戏」时就建好，靠心跳一直挂着——它终结在我们自己的
/// 工具上，Minecraft 不在这条链路里，所以不受服务端 60 秒空闲超时的约束，
/// 读条几分钟也不会被断。游戏真正连上来时才用 <see cref="OpenStreamAsync"/>
/// 开一条数据连接。
/// </summary>
public sealed class TunnelClient : IMuxiTunnel
{
    private readonly TcpClient _control;
    private readonly NetworkStream _stream;
    private readonly byte[] _secret;
    private readonly CancellationTokenSource _lifetime = new();
    private readonly SemaphoreSlim _writeGate = new(1, 1);
    private readonly Task _heartbeat;

    public IPEndPoint Endpoint { get; }
    public ulong Session { get; }

    /// <summary>心跳失败或控制连接断开时触发，调用方据此换线。</summary>
    public event Action<string>? Lost;

    public bool IsAlive => !_lifetime.IsCancellationRequested && _control.Connected;

    public string Describe => $"TCP 隧道 {Endpoint} 会话 {Session:x16}";

    private TunnelClient(TcpClient control, NetworkStream stream, byte[] secret,
        IPEndPoint endpoint, ulong session)
    {
        _control = control;
        _stream = stream;
        _secret = secret;
        Endpoint = endpoint;
        Session = session;
        _heartbeat = HeartbeatAsync(_lifetime.Token);
    }

    /// <summary>建立控制连接并完成鉴权。失败会抛，调用方换下一条候选。</summary>
    public static async Task<TunnelClient> ConnectAsync(
        IPEndPoint endpoint, string token, TimeSpan timeout, CancellationToken ct)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
        deadline.CancelAfter(timeout);

        var control = new TcpClient(endpoint.AddressFamily);
        try
        {
            await control.ConnectAsync(endpoint, deadline.Token).ConfigureAwait(false);
            control.NoDelay = true;
            var stream = control.GetStream();
            var secret = TunnelProtocol.DeriveSecret(token);

            await stream.WriteAsync(TunnelProtocol.Preamble(), deadline.Token).ConfigureAwait(false);
            await TunnelProtocol.WriteFrameAsync(stream, TunnelProtocol.Frame.Hello,
                ReadOnlyMemory<byte>.Empty, deadline.Token).ConfigureAwait(false);

            var (frame, nonce) = await TunnelProtocol.ReadFrameAsync(stream, deadline.Token)
                .ConfigureAwait(false);
            if (frame == TunnelProtocol.Frame.Reject)
                throw new InvalidOperationException("服务端拒绝：" + Encoding.UTF8.GetString(nonce));
            if (frame != TunnelProtocol.Frame.Welcome || nonce.Length != 32)
                throw new InvalidDataException($"握手应答异常：{frame}");

            var signature = TunnelProtocol.Sign(secret, nonce);
            await TunnelProtocol.WriteFrameAsync(stream, TunnelProtocol.Frame.Hello, signature,
                deadline.Token).ConfigureAwait(false);

            var (result, payload) = await TunnelProtocol.ReadFrameAsync(stream, deadline.Token)
                .ConfigureAwait(false);
            if (result == TunnelProtocol.Frame.Reject)
                throw new InvalidOperationException("鉴权失败：" + Encoding.UTF8.GetString(payload));
            if (result != TunnelProtocol.Frame.Welcome || payload.Length != 8)
                throw new InvalidDataException($"会话应答异常：{result}");

            var session = BinaryPrimitives.ReadUInt64BigEndian(payload);
            Log.Info($"隧道已建立 {endpoint} 会话 {session:x16}");
            return new TunnelClient(control, stream, secret, endpoint, session);
        }
        catch
        {
            control.Dispose();
            throw;
        }
    }

    /// <summary>
    /// 开一条数据连接。返回的 socket 已经接通服务端那侧的 Minecraft，之后就是
    /// 裸字节，调用方直接对拷即可。
    /// </summary>
    public async Task<Stream> OpenStreamAsync(CancellationToken ct)
    {
        var data = new TcpClient(Endpoint.AddressFamily);
        try
        {
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
            deadline.CancelAfter(TimeSpan.FromSeconds(10));
            await data.ConnectAsync(Endpoint, deadline.Token).ConfigureAwait(false);
            data.NoDelay = true;
            var stream = data.GetStream();

            var session = new byte[8];
            BinaryPrimitives.WriteUInt64BigEndian(session, Session);
            await stream.WriteAsync(TunnelProtocol.Preamble(), deadline.Token).ConfigureAwait(false);
            await TunnelProtocol.WriteFrameAsync(stream, TunnelProtocol.Frame.OpenStream, session,
                deadline.Token).ConfigureAwait(false);

            var (frame, payload) = await TunnelProtocol.ReadFrameAsync(stream, deadline.Token)
                .ConfigureAwait(false);
            if (frame == TunnelProtocol.Frame.Reject)
                throw new InvalidOperationException("数据连接被拒：" + Encoding.UTF8.GetString(payload));
            if (frame != TunnelProtocol.Frame.StreamReady)
                throw new InvalidDataException($"数据连接应答异常：{frame}");
            // 流被 Dispose 时底下的 TcpClient 一并关掉，否则每开一条流漏一个
            return new OwnedStream(stream, data);
        }
        catch
        {
            data.Dispose();
            throw;
        }
    }

    private async Task HeartbeatAsync(CancellationToken ct)
    {
        try
        {
            while (!ct.IsCancellationRequested)
            {
                await Task.Delay(TunnelProtocol.HeartbeatInterval, ct).ConfigureAwait(false);
                await _writeGate.WaitAsync(ct).ConfigureAwait(false);
                try
                {
                    await TunnelProtocol.WriteFrameAsync(_stream, TunnelProtocol.Frame.Ping,
                        ReadOnlyMemory<byte>.Empty, ct).ConfigureAwait(false);
                }
                finally { _writeGate.Release(); }

                using var wait = CancellationTokenSource.CreateLinkedTokenSource(ct);
                wait.CancelAfter(TunnelProtocol.HeartbeatTimeout);
                var (frame, _) = await TunnelProtocol.ReadFrameAsync(_stream, wait.Token)
                    .ConfigureAwait(false);
                if (frame != TunnelProtocol.Frame.Pong)
                    throw new InvalidDataException($"心跳应答异常：{frame}");
            }
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested) { }
        catch (Exception error)
        {
            Log.Warn($"隧道 {Endpoint} 心跳中断：{error.Message}");
            Lost?.Invoke(error.Message);
        }
    }

    public async ValueTask DisposeAsync()
    {
        await _lifetime.CancelAsync().ConfigureAwait(false);
        try { await _heartbeat.ConfigureAwait(false); } catch { }
        _control.Dispose();
        _lifetime.Dispose();
        _writeGate.Dispose();
        Log.Info($"隧道 {Endpoint} 已关闭");
    }
}
