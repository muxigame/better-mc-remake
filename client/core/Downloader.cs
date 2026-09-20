using System.Net;
using System.Net.Http.Headers;

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
    private readonly int _parallel;

    public Downloader(HttpClient? http = null, int parallel = 8)
    {
        _parallel = Math.Clamp(parallel, 1, 32);
        _http = http ?? BuildClient();
    }

    public static HttpClient BuildClient(TimeSpan? timeout = null)
    {
        var handler = new SocketsHttpHandler
        {
            AutomaticDecompression = DecompressionMethods.All,
            PooledConnectionLifetime = TimeSpan.FromMinutes(5),
            MaxConnectionsPerServer = 32,
            ConnectTimeout = TimeSpan.FromSeconds(20),
        };
        var c = new HttpClient(handler) { Timeout = timeout ?? TimeSpan.FromMinutes(10) };
        c.DefaultRequestHeaders.UserAgent.ParseAdd("BatterMC5Remake-Launcher/1.0");
        return c;
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

        long bytesTotal = items.Sum(i => Math.Max(0, i.ExpectedSize));
        long bytesDone = 0;
        int filesDone = 0;
        string current = items[0].Display;
        var errors = new List<Exception>();
        var gate = new SemaphoreSlim(_parallel);

        void Report() => progress?.Report(new DownloadProgress(
            Volatile.Read(ref filesDone), items.Count,
            Interlocked.Read(ref bytesDone), bytesTotal,
            Volatile.Read(ref current)));

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

        if (errors.Count > 0)
            throw new AggregateException($"有 {errors.Count} 个文件下载失败", errors);
    }

    private async Task DownloadOneAsync(DownloadItem item, Action<long> onBytes, CancellationToken ct)
    {
        const int maxAttempts = 3;
        var part = item.TargetPath + ".part";
        Directory.CreateDirectory(Path.GetDirectoryName(item.TargetPath)!);

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

                using var req = new HttpRequestMessage(HttpMethod.Get, item.Url);
                if (resumeFrom > 0) req.Headers.Range = new RangeHeaderValue(resumeFrom, null);

                using var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct).ConfigureAwait(false);

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
            catch (OperationCanceledException) { throw; }
            catch (Exception ex) when (attempt < maxAttempts)
            {
                Log.Warn($"下载重试 {attempt}/{maxAttempts}：{item.Display} — {ex.Message}");
                // 校验失败说明 part 不可信，删掉从头下
                try { if (File.Exists(part)) File.Delete(part); } catch { }
                if (reportedThisAttempt != 0) onBytes(-reportedThisAttempt);
                await Task.Delay(TimeSpan.FromMilliseconds(400 * Math.Pow(2, attempt)), ct).ConfigureAwait(false);
            }
        }
    }

    public async Task<byte[]> GetBytesAsync(string url, CancellationToken ct)
        => await _http.GetByteArrayAsync(url, ct).ConfigureAwait(false);

    public async Task<string> GetStringAsync(string url, CancellationToken ct)
        => await _http.GetStringAsync(url, ct).ConfigureAwait(false);

    public HttpClient Http => _http;

    public void Dispose() => _http.Dispose();
}
