using System.Diagnostics;
using BatterMC.Protocol;

namespace BatterMC.Core;

/// <summary>
/// 用真实大流量压一遍隧道，回答"这条链路扛不扛得住进世界"。
///
/// 存在的理由见 <see cref="TunnelProtocol.BulkMagic"/>：小包验证对大流量毫无预测力，
/// 实测栽过。
/// </summary>
public static class TunnelProbe
{
    /// <summary>测试量。够大到能触发路径上的 MTU/丢包问题，又小到不值一提。</summary>
    public const int DefaultBytes = 2 * 1024 * 1024;

    /// <summary>
    /// 总超时。没有数据时 ReadAsync 会一直挂着，循环里的早失败根本够不到它，
    /// 所以靠这个把最坏等待兜住。2 MB / 12s = 170 KB/s，是进世界能接受的下限。
    /// </summary>
    public static readonly TimeSpan DefaultTimeout = TimeSpan.FromSeconds(12);

    public sealed record Result(long Bytes, TimeSpan Elapsed)
    {
        public double MbytesPerSecond => Elapsed.TotalSeconds <= 0
            ? 0
            : Bytes / 1024.0 / 1024.0 / Elapsed.TotalSeconds;
    }

    /// <summary>
    /// 在隧道里开一条流拉 <paramref name="bytes"/> 字节。拉不满或超时都会抛，
    /// 由调用方决定降级。
    /// </summary>
    public static async Task<Result> MeasureAsync(
        IMuxiTunnel tunnel, int bytes, TimeSpan timeout, CancellationToken ct)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
        deadline.CancelAfter(timeout);

        var stream = await tunnel.OpenStreamAsync(deadline.Token).ConfigureAwait(false);
        Result result;
        try
        {
            result = await MeasureOnAsync(stream, bytes, timeout, deadline, ct).ConfigureAwait(false);
        }
        catch
        {
            // 测不过的时候**不能**原地 await DisposeAsync。
            //
            // QUIC 流的正常关闭要等对端确认，而这里恰恰是"对端的包过不来"的情形。实测蜂窝
            // 上撞到过：带宽测试 3 秒就提前放弃了，之后光是关这条流又挂了 7 秒，验收一格
            // 变成 10.3 秒，玩家多等 7 秒才轮到重试。先硬中止，再扔后台收。
            AbortQuietly(stream);
            _ = Task.Run(async () =>
            {
                try { await stream.DisposeAsync().AsTask().WaitAsync(TimeSpan.FromSeconds(3)).ConfigureAwait(false); }
                catch { }
            });
            throw;
        }
        // 成功路径照常关：链路是好的，关闭很快。
        await stream.DisposeAsync().ConfigureAwait(false);
        return result;
    }

    private static void AbortQuietly(Stream stream)
    {
        try
        {
            if ((OperatingSystem.IsWindows() || OperatingSystem.IsLinux() || OperatingSystem.IsMacOS())
                && stream is System.Net.Quic.QuicStream quic)
                quic.Abort(System.Net.Quic.QuicAbortDirection.Both, 0x0A);
        }
        catch { }
    }

    private static async Task<Result> MeasureOnAsync(
        Stream stream, int bytes, TimeSpan timeout, CancellationTokenSource deadline, CancellationToken ct)
    {
        await stream.WriteAsync(TunnelProtocol.BulkRequest(bytes), deadline.Token).ConfigureAwait(false);
        await stream.FlushAsync(deadline.Token).ConfigureAwait(false);

        var watch = Stopwatch.StartNew();
        var buffer = new byte[64 * 1024];
        long received = 0;
        var abortedEarly = false;

        // 逐 100ms 记一格到达量。
        //
        // 只有总量和平均值的时候，"一路稀稀拉拉"和"头两格就来完之后彻底断"是同一个
        // 数字，而这两种对应完全不同的病：前者是限速或窗口太小，后者是连接卡死。
        // 实测被这个坑过——拿着"8 KB/s"这个平均值查了很久限速，而形状一看就知道
        // 该往哪边查。
        var bins = new long[(int)(timeout.TotalMilliseconds / 100) + 2];

        // 早失败必须由定时器来判，不能只在每次读返回之后检查。
        //
        // 实测踩过：一条 3 KB/s 的路径上数据是大间隔小批量地来，ReadAsync 会长时间
        // 阻塞，循环里的检查根本轮不到执行，5 秒的早失败被整个跨过去，最后由 12 秒
        // 超时兜底——玩家白等 12 秒。定时器不受读阻塞影响，到点就看进度、不够就掐。
        using var early = new Timer(_ =>
        {
            if (!SkipEarlyAbort && Interlocked.Read(ref received) < EarlyCheckBytes)
            {
                abortedEarly = true;
                try { deadline.Cancel(); } catch (ObjectDisposedException) { }
            }
        }, null, EarlyCheckAfter, Timeout.InfiniteTimeSpan);

        try
        {
            while (received < bytes)
            {
                var read = await stream.ReadAsync(buffer, deadline.Token).ConfigureAwait(false);
                if (read <= 0) break;
                Interlocked.Add(ref received, read);
                var bin = (int)(watch.Elapsed.TotalMilliseconds / 100);
                if (bin >= 0 && bin < bins.Length) bins[bin] += read;
            }
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            // 超时。这里**必须**把已经拿到多少报出来，不能只说"操作已取消"：
            //   拿到 0 字节        → 大包被整个丢掉，典型 MTU 黑洞
            //   拿到几百 KB 然后停 → 有流量但被限速，典型运营商对 UDP 的 P2P 抑制
            // 两者的修法完全不同，没有这个数字就只能猜。
            watch.Stop();
            throw new IOException(Describe(
                Interlocked.Read(ref received), bytes, watch.Elapsed,
                abortedEarly ? "开头就明显带不动，提前放弃" : "超时", bins));
        }
        watch.Stop();

        if (received < bytes)
            throw new IOException(Describe(received, bytes, watch.Elapsed, "提前中断", bins));
        return new Result(received, watch.Elapsed);
    }

    /// <summary>早失败的门槛：这么久之后还不到这么多字节就不用再等了。</summary>
    /// <summary>
    /// 早失败的判定点。3 秒 / 128 KB 换算过来是 43 KB/s，而一条健康的线路
    /// （对端实测 1.2 MB/s）在 3 秒时已经拿到 3 MB 以上，差着两个数量级，
    /// 不会误伤。卡在这个门槛下面的路径，进世界时的区块传输必然带不动。
    /// </summary>
    private static readonly TimeSpan EarlyCheckAfter = TimeSpan.FromSeconds(3);
    private const long EarlyCheckBytes = 128 * 1024;

    /// <summary>
    /// 诊断开关：MUXI_PROBE_FULL=1 时不提前放弃，把整个超时窗口跑满。
    ///
    /// 为什么需要：提前放弃是为了玩家——一条带不动的路不该让人白等 12 秒。但排查的
    /// 时候它会把关键证据掐掉：分不清"一直这么慢"和"卡了几秒之后其实会恢复"，而
    /// 后者意味着门槛设错了而不是链路不行。
    /// </summary>
    private static bool SkipEarlyAbort =>
        Environment.GetEnvironmentVariable("MUXI_PROBE_FULL") == "1";

    /// <summary>把逐 100ms 的到达量压成一行字符画，一眼看出形状。</summary>
    private static string Shape(long[] bins)
    {
        var last = Array.FindLastIndex(bins, x => x > 0);
        if (last < 0) return "";
        var chars = new char[last + 1];
        for (var i = 0; i <= last; i++)
        {
            var kb = bins[i] / 1024.0;
            chars[i] = kb switch
            {
                0 => '_',
                < 4 => '.',
                < 32 => '-',
                < 128 => '=',
                _ => '#',
            };
        }
        return new string(chars);
    }

    private static string Describe(
        long received, int wanted, TimeSpan elapsed, string why, long[] bins)
    {
        var rate = elapsed.TotalSeconds > 0
            ? received / 1024.0 / elapsed.TotalSeconds
            : 0;
        var detail = received == 0
            ? "一个字节都没过来——大包很可能被整条路径丢弃"
            : $"{elapsed.TotalSeconds:0.0}s 内只有 {rate:0} KB/s，带不动进世界时的区块传输";
        var shape = Shape(bins);
        return $"带宽测试{why}：拿到 {received / 1024} KB / {wanted / 1024} KB，{detail}"
               + (shape.Length > 0 ? $"，逐 100ms 到达 [{shape}]" : "");
    }
}
