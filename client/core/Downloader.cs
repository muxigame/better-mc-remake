using System.Net;
using System.Net.Http.Headers;
using System.Diagnostics;

namespace BatterMC.Core;

public sealed class DownloadItem
{
    public required string Url { get; init; }
    public required string TargetPath { get; init; }
    public long ExpectedSize { get; init; }
    /// <summary>期望的 SHA-1（小写十六进制）。为空则不校验。</summary>
    public string? ExpectedSha1 { get; init; }
    /// <summary>展示用的名字。</summary>
    public string Display { get; init; } = "";
}

public sealed record DownloadProgress(int FilesDone, int FilesTotal, long BytesDone, long BytesTotal, string Current);

public sealed class Downloader : IDisposable
{
    private readonly HttpClient _http;
    private readonly int _parallelCap;

    /// <summary>设了就优先走镜像，镜像没有的自动回落上游。为 null 则一律直连上游。</summary>
    public DownloadMirror? Mirror { get; set; }

    public Downloader(HttpClient? http = null, int parallel = 32)
    {
        _parallelCap = Math.Clamp(parallel, 1, 32);
        _http = http ?? BuildClient();
    }

    public static HttpClient BuildClient(TimeSpan? timeout = null)
    {
        var handler = new SocketsHttpHandler
        {
            AutomaticDecompression = DecompressionMethods.All,
            PooledConnectionLifetime = TimeSpan.FromMinutes(5),
            MaxConnectionsPerServer = 64,
            EnableMultipleHttp2Connections = true,
            ConnectTimeout = TimeSpan.FromSeconds(20),
        };
        var c = new HttpClient(handler) { Timeout = timeout ?? TimeSpan.FromMinutes(10) };
        // Prefer one multiplexed HTTP/2 connection where the origin supports it,
        // while still falling back cleanly for Maven/CDN endpoints that only do HTTP/1.1.
        c.DefaultRequestVersion = HttpVersion.Version20;
        c.DefaultVersionPolicy = HttpVersionPolicy.RequestVersionOrLower;
        c.DefaultRequestHeaders.UserAgent.ParseAdd("BatterMC5Remake-Launcher/1.0");
        return c;
    }

    private int EffectiveParallelism(IReadOnlyList<DownloadItem> items)
    {
        if (items.Count <= 1) return Math.Max(1, items.Count);
        var total = items.Sum(item => Math.Max(0, item.ExpectedSize));
        var average = total / Math.Max(1, items.Count);

        // Thousands of tiny Minecraft/pack objects are RTT-bound, not bandwidth-bound.
        // Large archives need far less fan-out and benefit from lower disk contention.
        var recommended = average switch
        {
            <= 256 * 1024 => 32,
            <= 2 * 1024 * 1024 => 16,
            _ => 8,
        };
        return Math.Clamp(Math.Min(_parallelCap, recommended), 1, items.Count);
    }

    /// <summary>
    /// 批量下载。失败自动重试 3 次（指数退避），支持断点续传，
    /// 全部写到 .part 再原子改名，中途中断不会留下半个文件。
    /// </summary>
    public async Task DownloadAllAsync(
        IReadOnlyList<DownloadItem> items,
        IProgress<DownloadProgress>? progress,
        CancellationToken ct)
    {
        if (items.Count == 0) return;
        items = DistinctTargets(items);

        long bytesTotal = items.Sum(i => Math.Max(0, i.ExpectedSize));
        long bytesDone = 0;
        int filesDone = 0;
        string current = items[0].Display;
        var errors = new List<Exception>();
        var parallel = EffectiveParallelism(items);
        var gate = new SemaphoreSlim(parallel);
        Log.Info($"下载调度：{items.Count} 个文件，{parallel} 路并发");

        long lastReportMs = 0;

        void Report(bool force = false)
        {
            if (progress is null) return;
            var now = Environment.TickCount64;
            if (!force)
            {
                var previous = Interlocked.Read(ref lastReportMs);
                if (now - previous < 100) return;
                if (Interlocked.CompareExchange(ref lastReportMs, now, previous) != previous) return;
            }
            else
            {
                Interlocked.Exchange(ref lastReportMs, now);
            }
            progress.Report(new DownloadProgress(
                Volatile.Read(ref filesDone), items.Count,
                Interlocked.Read(ref bytesDone), bytesTotal,
                Volatile.Read(ref current)));
        }

        Report(force: true);

        var tasks = items.Select(async item =>
        {
            await gate.WaitAsync(ct).ConfigureAwait(false);
            try
            {
                Volatile.Write(ref current, item.Display);
                await DownloadOneAsync(item, delta =>
                {
                    Interlocked.Add(ref bytesDone, delta);
                    Report();
                }, ct).ConfigureAwait(false);
                Interlocked.Increment(ref filesDone);
                Report();
            }
            catch (OperationCanceledException) { throw; }
            catch (Exception ex)
            {
                lock (errors) errors.Add(new InvalidOperationException($"下载失败：{item.Display} <- {item.Url}", ex));
            }
            finally { gate.Release(); }
        }).ToArray();

        await Task.WhenAll(tasks).ConfigureAwait(false);
        Report(force: true);

        if (errors.Count > 0)
            throw new AggregateException($"有 {errors.Count} 个文件下载失败", errors);
    }

    /// <summary>
    /// 同一个目标只留一项。两个任务写同一个 .part，必有一个因为文件被占用而失败，
    /// 失败那个再去问上游——国内连不上上游，整次安装就栽在一个本来已经下好的文件上。
    /// </summary>
    private static IReadOnlyList<DownloadItem> DistinctTargets(IReadOnlyList<DownloadItem> items)
    {
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var unique = new List<DownloadItem>(items.Count);
        foreach (var item in items)
            if (seen.Add(Path.GetFullPath(item.TargetPath))) unique.Add(item);
        if (unique.Count != items.Count)
            Log.Info($"下载清单里有 {items.Count - unique.Count} 个重复目标，已合并");
        return unique;
    }

    private async Task DownloadOneAsync(DownloadItem item, Action<long> onBytes, CancellationToken ct)
    {
        var part = item.TargetPath + ".part";
        Directory.CreateDirectory(Path.GetDirectoryName(item.TargetPath)!);

        var upstream = item.Url;
        var mirrored = Mirror?.Rewrite(upstream);
        var url = mirrored ?? upstream;
        var usedMirror = mirrored is not null;
        // 有镜像时镜像、上游交替着试，各两次。
        var maxAttempts = mirrored is null ? 3 : 4;

        for (var attempt = 1; ; attempt++)
        {
            long resumeFrom = 0;
            // 本次尝试已经上报过的字节数。失败重来时要把它减回去，
            // 否则进度条会因为重试而虚高甚至超过 100%。
            long reportedThisAttempt = 0;
            void Report(long delta) { reportedThisAttempt += delta; onBytes(delta); }
            try
            {
                if (File.Exists(part))
                {
                    var len = new FileInfo(part).Length;
                    // 只有已知总长度且 part 更短时才续传，否则重来
                    if (item.ExpectedSize > 0 && len > 0 && len < item.ExpectedSize) resumeFrom = len;
                    else if (len > 0) File.Delete(part);
                }

                using var req = new HttpRequestMessage(HttpMethod.Get, url);
                if (resumeFrom > 0) req.Headers.Range = new RangeHeaderValue(resumeFrom, null);

                using var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct).ConfigureAwait(false);

                // 镜像还没补上这个对象。这不是故障，不该消耗重试次数，直接改走上游。
                if (usedMirror && (resp.StatusCode == HttpStatusCode.NotFound
                                   || resp.StatusCode == HttpStatusCode.Forbidden))
                {
                    Log.Info($"镜像缺少 {item.Display}，回落上游");
                    usedMirror = false;
                    mirrored = null;
                    url = upstream;
                    attempt--;
                    if (File.Exists(part)) { try { File.Delete(part); } catch { } }
                    continue;
                }

                if (resumeFrom > 0 && resp.StatusCode != HttpStatusCode.PartialContent)
                {
                    // 服务器不认 Range，从头来
                    resumeFrom = 0;
                    if (File.Exists(part)) File.Delete(part);
                }
                resp.EnsureSuccessStatusCode();

                var mode = resumeFrom > 0 ? FileMode.Append : FileMode.Create;
                await using (var fs = new FileStream(part, mode, FileAccess.Write, FileShare.None, 1 << 20, useAsync: true))
                await using (var src = await resp.Content.ReadAsStreamAsync(ct).ConfigureAwait(false))
                {
                    if (resumeFrom > 0) Report(resumeFrom);
                    var buffer = new byte[1 << 16];
                    int read;
                    while ((read = await src.ReadAsync(buffer, ct).ConfigureAwait(false)) > 0)
                    {
                        await fs.WriteAsync(buffer.AsMemory(0, read), ct).ConfigureAwait(false);
                        Report(read);
                    }
                }

                if (item.ExpectedSize > 0)
                {
                    var actual = new FileInfo(part).Length;
                    if (actual != item.ExpectedSize)
                        throw new IOException($"大小不符：期望 {item.ExpectedSize}，实得 {actual}");
                }

                if (!string.IsNullOrEmpty(item.ExpectedSha1))
                {
                    var actual = Hashing.Sha1File(part);
                    if (!actual.Equals(item.ExpectedSha1, StringComparison.OrdinalIgnoreCase))
                        throw new IOException($"SHA-1 不符：期望 {item.ExpectedSha1}，实得 {actual}");
                }

                AtomicFile.Replace(part, item.TargetPath);
                return;
            }
            // HttpClient 自己超时抛的也是 TaskCanceledException，那种要重试，不是用户取消
            catch (OperationCanceledException) when (ct.IsCancellationRequested) { throw; }
            catch (Exception ex) when (attempt < maxAttempts)
            {
                Log.Warn($"下载重试 {attempt}/{maxAttempts}：{item.Display} — {ex.Message}");
                // 镜像和上游交替着试。以前是镜像一出错就把剩下的机会全押给上游，
                // 国内很多玩家根本连不上上游，镜像抖一下就等于判了失败。
                if (mirrored is not null)
                {
                    usedMirror = !usedMirror;
                    url = usedMirror ? mirrored : upstream;
                }
                // 校验失败说明 part 不可信，删掉从头下
                try { if (File.Exists(part)) File.Delete(part); } catch { }
                if (reportedThisAttempt != 0) onBytes(-reportedThisAttempt);
                await Task.Delay(TimeSpan.FromMilliseconds(400 * Math.Pow(2, attempt)), ct).ConfigureAwait(false);
            }
        }
    }

    public Task<byte[]> GetBytesAsync(string url, CancellationToken ct)
        => ViaMirrorAsync(url, u => _http.GetByteArrayAsync(u, ct), ct);

    public Task<string> GetStringAsync(string url, CancellationToken ct)
        => ViaMirrorAsync(url, u => _http.GetStringAsync(u, ct), ct);

    /// <summary>
    /// 问一下远端这个文件多大。拿不到就返回 0 —— 调用方据此降级成"不可续传"，不该因此失败。
    /// </summary>
    public async Task<long> TryGetLengthAsync(string url, CancellationToken ct)
    {
        try
        {
            return await ViaMirrorAsync(url, async u =>
            {
                using var req = new HttpRequestMessage(HttpMethod.Head, u);
                using var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct).ConfigureAwait(false);
                resp.EnsureSuccessStatusCode();
                return resp.Content.Headers.ContentLength ?? 0;
            }, ct).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            Log.Info($"拿不到 {url} 的长度：{ex.Message}");
            return 0;
        }
    }

    /// <summary>
    /// Maven 风格的 .sha1 伴生文件。内容通常就是 40 个十六进制字符，
    /// 有些仓库后面还跟着文件名，所以只取开头那段。拿不到返回 null。
    /// </summary>
    public async Task<string?> TryGetSha1Async(string url, CancellationToken ct)
    {
        try
        {
            var text = await ViaMirrorAsync(url + ".sha1", u => _http.GetStringAsync(u, ct), ct).ConfigureAwait(false);
            var digest = new string(text.Trim().TakeWhile(Uri.IsHexDigit).ToArray());
            return digest.Length == 40 ? digest : null;
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            Log.Info($"拿不到 {url} 的 sha1：{ex.Message}");
            return null;
        }
    }

    /// <summary>
    /// 单个小文件也照样先走镜像，失败了再问上游；上游也不通而镜像刚才不是"没有这个文件"，
    /// 就再给镜像一次机会——资源索引就是走这里，它下不来整个安装都进行不下去。
    /// </summary>
    private async Task<T> ViaMirrorAsync<T>(string url, Func<string, Task<T>> fetch, CancellationToken ct)
    {
        var mirrored = Mirror?.Rewrite(url);
        if (mirrored is null) return await fetch(url).ConfigureAwait(false);

        Exception mirrorError;
        try { return await fetch(mirrored).ConfigureAwait(false); }
        catch (Exception ex) when (!ct.IsCancellationRequested)
        {
            Log.Info($"镜像取不到 {mirrored}，回落上游：{ex.Message}");
            mirrorError = ex;
        }

        try { return await fetch(url).ConfigureAwait(false); }
        catch (Exception ex) when (!ct.IsCancellationRequested && !IsMissing(mirrorError))
        {
            Log.Info($"上游也取不到 {url}：{ex.Message}，再试一次镜像");
        }
        await Task.Delay(TimeSpan.FromSeconds(1), ct).ConfigureAwait(false);
        return await fetch(mirrored).ConfigureAwait(false);
    }

    private static bool IsMissing(Exception ex) =>
        ex is HttpRequestException { StatusCode: HttpStatusCode.NotFound or HttpStatusCode.Forbidden };

    public HttpClient Http => _http;

    public void Dispose() => _http.Dispose();
}
