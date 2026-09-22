namespace BatterMC.Protocol;

/// <summary>
/// 一条已经建立的端侧隧道。
///
/// 两种实现：TCP 隧道（连到服务端侧工具的监听端口，走 IPv6 直连或中转）和 QUIC
/// 隧道（打洞打通之后在同一个本地端口上起）。代理层只认这个接口，换哪种实现都
/// 不影响游戏那一侧。
/// </summary>
public interface IMuxiTunnel : IAsyncDisposable
{
    /// <summary>隧道还活着吗。断了就该换线而不是继续往里塞数据。</summary>
    bool IsAlive { get; }

    /// <summary>给日志和界面看的对端标识。</summary>
    string Describe { get; }

    /// <summary>
    /// 开一条数据流。返回的流已经接通服务端那侧的 Minecraft，之后就是裸字节。
    /// 调用方负责 Dispose，底层连接随之释放。
    /// </summary>
    Task<Stream> OpenStreamAsync(CancellationToken ct);
}

/// <summary>
/// 把一条流和它底下的资源绑在一起：调用方 Dispose 流的时候，连接也一并关掉。
/// 没有这层的话，TCP 隧道每开一条流就会漏一个 TcpClient。
/// </summary>
public sealed class OwnedStream(Stream inner, IDisposable owner) : Stream
{
    public override bool CanRead => inner.CanRead;
    public override bool CanSeek => false;
    public override bool CanWrite => inner.CanWrite;
    public override long Length => throw new NotSupportedException();
    public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
    public override void Flush() => inner.Flush();
    public override Task FlushAsync(CancellationToken ct) => inner.FlushAsync(ct);
    public override int Read(byte[] buffer, int offset, int count) => inner.Read(buffer, offset, count);
    public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken ct = default)
        => inner.ReadAsync(buffer, ct);
    public override void Write(byte[] buffer, int offset, int count) => inner.Write(buffer, offset, count);
    public override ValueTask WriteAsync(ReadOnlyMemory<byte> buffer, CancellationToken ct = default)
        => inner.WriteAsync(buffer, ct);
    public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
    public override void SetLength(long value) => throw new NotSupportedException();

    protected override void Dispose(bool disposing)
    {
        if (disposing) { try { inner.Dispose(); } catch { } try { owner.Dispose(); } catch { } }
        base.Dispose(disposing);
    }
}
