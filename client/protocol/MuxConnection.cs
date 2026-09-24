using System.Buffers.Binary;
using System.Collections.Concurrent;
using System.Threading.Channels;

namespace BatterMC.Protocol;

/// <summary>
/// 在**一条**双工流上分出多条逻辑流。
///
/// 为什么需要：常规 TCP 隧道是"每条 Minecraft 连接开一条新 TCP"，因为服务端侧有
/// 一个公网可达的监听端口，想连几次连几次。打洞出来的连接没有这个条件——我们手上
/// 只有一条已经连通的 socket，断了就得重新打一次洞（几秒起步，还不一定成功）。
/// 所以玩家的游戏连接、带宽探测、保温探活都得挤在这一条上。
///
/// 流控用的是"每条流一个有界队列"，队列满了读循环就停下来，背压顺着整条连接往回传。
/// 这意味着一条流堵住会拖慢其它流（队头阻塞），没有做每流独立窗口。之所以接受这个
/// 取舍：实际并发的流最多两三条（游戏连接 + 偶尔的探测），而每流窗口要维护信用值、
/// 窗口更新帧和一堆边界情况，写错的表现是"偶尔卡住"，在游戏里极难复现。
/// </summary>
public sealed class MuxConnection : IAsyncDisposable
{
    /// <summary>单帧最多带多少数据。帧头限制是 65535，留出 4 字节流号还有余量。</summary>
    private const int MaxChunk = 32 * 1024;

    /// <summary>每条流最多缓冲多少个分片再开始背压。</summary>
    private const int StreamQueueDepth = 64;

    private readonly Stream _wire;
    private readonly SemaphoreSlim _writeGate = new(1, 1);
    private readonly ConcurrentDictionary<uint, MuxStream> _streams = new();
    private readonly Channel<MuxStream> _incoming =
        Channel.CreateUnbounded<MuxStream>(new UnboundedChannelOptions { SingleReader = true });
    private readonly CancellationTokenSource _lifetime = new();
    private readonly Task _reader;
    private readonly bool _initiator;
    private int _nextId;
    private volatile Exception? _fault;

    /// <param name="initiator">
    /// 谁负责分配流号。两边同时开流会撞号，所以由发起方独占分配——这条连接上
    /// 只有客户端会开流（服务端侧只接），不需要奇偶分段那套。
    /// </param>
    public MuxConnection(Stream wire, bool initiator)
    {
        _wire = wire;
        _initiator = initiator;
        _reader = ReadLoopAsync(_lifetime.Token);
    }

    public bool IsAlive => _fault is null && !_lifetime.IsCancellationRequested;

    /// <summary>开一条新的逻辑流。只有发起方能调。</summary>
    public async Task<Stream> OpenAsync(CancellationToken ct)
    {
        if (!_initiator) throw new InvalidOperationException("这一侧不负责开流");
        ThrowIfFaulted();
        var id = (uint)Interlocked.Increment(ref _nextId);
        var stream = new MuxStream(this, id);
        _streams[id] = stream;
        await SendAsync(TunnelProtocol.Frame.MuxOpen, id, ReadOnlyMemory<byte>.Empty, ct)
            .ConfigureAwait(false);
        return stream;
    }

    /// <summary>等对方开一条流过来。</summary>
    public async Task<Stream> AcceptAsync(CancellationToken ct)
    {
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct, _lifetime.Token);
        try
        {
            return await _incoming.Reader.ReadAsync(linked.Token).ConfigureAwait(false);
        }
        catch (ChannelClosedException)
        {
            ThrowIfFaulted();
            throw new IOException("复用连接已关闭");
        }
    }

    private void ThrowIfFaulted()
    {
        if (_fault is not null) throw new IOException("复用连接已断开：" + _fault.Message, _fault);
    }

    /// <summary>写一帧。整条连接共用一把写锁，否则两条流的分片会交错成乱码。</summary>
    internal async Task SendAsync(
        TunnelProtocol.Frame frame, uint id, ReadOnlyMemory<byte> body, CancellationToken ct)
    {
        var payload = new byte[4 + body.Length];
        BinaryPrimitives.WriteUInt32BigEndian(payload, id);
        body.CopyTo(payload.AsMemory(4));

        await _writeGate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            await TunnelProtocol.WriteFrameAsync(_wire, frame, payload, ct).ConfigureAwait(false);
        }
        finally { _writeGate.Release(); }
    }

    internal async Task SendDataAsync(uint id, ReadOnlyMemory<byte> data, CancellationToken ct)
    {
        while (!data.IsEmpty)
        {
            var take = Math.Min(data.Length, MaxChunk);
            await SendAsync(TunnelProtocol.Frame.MuxData, id, data[..take], ct).ConfigureAwait(false);
            data = data[take..];
        }
    }

    private async Task ReadLoopAsync(CancellationToken ct)
    {
        try
        {
            while (!ct.IsCancellationRequested)
            {
                var (frame, payload) = await TunnelProtocol.ReadFrameAsync(_wire, ct).ConfigureAwait(false);
                if (payload.Length < 4) throw new InvalidDataException($"复用帧过短：{frame}");
                var id = BinaryPrimitives.ReadUInt32BigEndian(payload);
                var body = payload.AsMemory(4);

                switch (frame)
                {
                    case TunnelProtocol.Frame.MuxOpen:
                        // 对方开流。发起方不该收到这个——收到说明两边角色配错了。
                        if (_initiator) throw new InvalidDataException("发起方收到了开流请求");
                        var opened = new MuxStream(this, id);
                        _streams[id] = opened;
                        await _incoming.Writer.WriteAsync(opened, ct).ConfigureAwait(false);
                        break;

                    case TunnelProtocol.Frame.MuxData:
                        // 流号不认识就丢掉：对方还没收到我们的关闭帧时会有这种在途数据，
                        // 属于正常竞态，不是错误。
                        if (_streams.TryGetValue(id, out var target))
                            await target.PushAsync(body, ct).ConfigureAwait(false);
                        break;

                    case TunnelProtocol.Frame.MuxClose:
                        if (_streams.TryRemove(id, out var closing)) closing.RemoteClosed();
                        break;

                    default:
                        throw new InvalidDataException($"复用连接上出现意外帧：{frame}");
                }
            }
        }
        catch (Exception error)
        {
            _fault = error is OperationCanceledException
                ? new IOException("复用连接已关闭")
                : error;
        }
        finally
        {
            _incoming.Writer.TryComplete();
            foreach (var s in _streams.Values) s.RemoteClosed();
            _streams.Clear();
        }
    }

    internal void Forget(uint id) => _streams.TryRemove(id, out _);

    public async ValueTask DisposeAsync()
    {
        await _lifetime.CancelAsync().ConfigureAwait(false);
        try { await _reader.ConfigureAwait(false); } catch { }
        _lifetime.Dispose();
        _writeGate.Dispose();
        await _wire.DisposeAsync().ConfigureAwait(false);
    }

    /// <summary>一条逻辑流。对调用方就是普通的双工 Stream。</summary>
    private sealed class MuxStream(MuxConnection owner, uint id) : Stream
    {
        private readonly Channel<ReadOnlyMemory<byte>> _queue =
            Channel.CreateBounded<ReadOnlyMemory<byte>>(new BoundedChannelOptions(StreamQueueDepth)
            {
                SingleReader = true,
                SingleWriter = true,
                FullMode = BoundedChannelFullMode.Wait,
            });

        private ReadOnlyMemory<byte> _leftover;
        private bool _closedLocally;

        internal ValueTask PushAsync(ReadOnlyMemory<byte> data, CancellationToken ct)
            // 必须拷贝：payload 数组会被读循环复用，不拷的话排队期间内容会被下一帧覆盖。
            => _queue.Writer.WriteAsync(data.ToArray(), ct);

        internal void RemoteClosed() => _queue.Writer.TryComplete();

        public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken ct = default)
        {
            if (_leftover.IsEmpty)
            {
                try
                {
                    if (!await _queue.Reader.WaitToReadAsync(ct).ConfigureAwait(false)) return 0;
                    if (!_queue.Reader.TryRead(out _leftover)) return 0;
                }
                catch (ChannelClosedException) { return 0; }
            }
            var take = Math.Min(buffer.Length, _leftover.Length);
            _leftover[..take].CopyTo(buffer);
            _leftover = _leftover[take..];
            return take;
        }

        public override ValueTask WriteAsync(ReadOnlyMemory<byte> buffer, CancellationToken ct = default)
            => new(owner.SendDataAsync(id, buffer, ct));

        protected override void Dispose(bool disposing)
        {
            if (disposing && !_closedLocally)
            {
                _closedLocally = true;
                owner.Forget(id);
                // 关闭帧尽力而为：连接已经断了的话没人需要这一帧。
                _ = owner.SendAsync(TunnelProtocol.Frame.MuxClose, id,
                        ReadOnlyMemory<byte>.Empty, CancellationToken.None)
                    .ContinueWith(t => _ = t.Exception, TaskScheduler.Default);
                _queue.Writer.TryComplete();
            }
            base.Dispose(disposing);
        }

        public override int Read(byte[] buffer, int offset, int count)
            => ReadAsync(buffer.AsMemory(offset, count)).AsTask().GetAwaiter().GetResult();
        public override void Write(byte[] buffer, int offset, int count)
            => WriteAsync(buffer.AsMemory(offset, count)).AsTask().GetAwaiter().GetResult();
        public override void Flush() { }
        public override Task FlushAsync(CancellationToken ct) => Task.CompletedTask;
        public override bool CanRead => true;
        public override bool CanWrite => true;
        public override bool CanSeek => false;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
    }
}
