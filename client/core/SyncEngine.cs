using System.Collections.Concurrent;
using BatterMC.Protocol;

namespace BatterMC.Core;

public sealed record SyncStatus(string Phase, string Detail, double Fraction, long BytesDone, long BytesTotal)
{
    public static SyncStatus Of(string phase, string detail = "", double fraction = -1)
        => new(phase, detail, fraction, 0, 0);
}

public sealed class SyncPlan
{
    public List<DownloadItem> Downloads { get; } = new();
    public List<string> Deletions { get; } = new();
    public List<string> SeedTargets { get; } = new();
    public long Bytes => Downloads.Sum(d => Math.Max(0, d.ExpectedSize));
    public bool IsEmpty => Downloads.Count == 0 && Deletions.Count == 0;
}

/// <summary>
/// 整合包文件同步。
///
/// 三种策略：
///   Managed  — 服务器说了算，哈希不符就覆盖，玩家删了补回来
///   Seed     — 只投放一次，之后是玩家的
///   Optional — 玩家勾了才装，取消勾选就删掉
///
/// 外加 prune：清单指定的目录里，不在清单上的文件一律删除。
/// mods 必须 prune，否则玩家私自加的模组会让他连不上服务器。
/// config 默认不 prune —— 模组会在运行时自己生成配置文件，删了会天天重建。
/// </summary>
public sealed class SyncEngine
{
    private readonly LauncherPaths _paths;
    private readonly LocalState _state;
    private readonly LauncherSettings _settings;
    private readonly Downloader _downloader;

    public SyncEngine(LauncherPaths paths, LocalState state, LauncherSettings settings, Downloader downloader)
    {
        _paths = paths;
        _state = state;
        _settings = settings;
        _downloader = downloader;
    }

    public async Task<PackManifest> FetchManifestAsync(string baseUrl, CancellationToken ct)
    {
        var url = baseUrl.TrimEnd('/') + "/api/v1/manifest";
        Log.Info($"拉取清单：{url}");
        try
        {
            var json = await _downloader.GetStringAsync(url, ct).ConfigureAwait(false);
            var manifest = PackManifest.FromJson(json)
                ?? throw new InvalidOperationException("清单内容为空");
            AtomicFile.WriteAllText(_paths.ManifestCacheFile, json);
            Log.Info($"清单版本 {manifest.Pack.Version}，{manifest.Files.Count} 个文件");
            return manifest;
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            Log.Warn($"拉取清单失败（{ex.Message}），尝试使用上次缓存");
            if (File.Exists(_paths.ManifestCacheFile))
            {
                var cached = PackManifest.FromJson(File.ReadAllText(_paths.ManifestCacheFile));
                if (cached is not null)
                {
                    Log.Warn($"离线模式：使用缓存的清单 {cached.Pack.Version}");
                    return cached;
                }
            }
            throw;
        }
    }

    /// <summary>
    /// 校验本地文件，算出需要下载和删除的清单。
    /// 哈希计算并行跑，结果统一回写缓存（LocalState 本身不是线程安全的）。
    /// </summary>
    public SyncPlan Plan(PackManifest manifest, IProgress<SyncStatus>? progress, CancellationToken ct)
    {
        var plan = new SyncPlan();
        var baseUrl = _settings.UpdateBaseUrl.TrimEnd('/');
        var enabled = new HashSet<string>(_settings.EnabledOptional, StringComparer.OrdinalIgnoreCase);

        // 并行校验，所以用并发集合；ConcurrentDictionary 当作 set 用
        var expected = new ConcurrentDictionary<string, byte>(StringComparer.OrdinalIgnoreCase);
        var freshHashes = new ConcurrentDictionary<string, HashCacheEntry>(StringComparer.OrdinalIgnoreCase);
        var downloads = new ConcurrentBag<DownloadItem>();
        var deletions = new ConcurrentBag<string>();
        var seeded = new ConcurrentBag<string>();

        var done = 0;
        var total = manifest.Files.Count;

        Parallel.ForEach(manifest.Files,
            new ParallelOptions { CancellationToken = ct, MaxDegreeOfParallelism = Environment.ProcessorCount },
            file =>
            {
                var absolute = _paths.ResolveGameFile(file.Path);
                var isOptional = file.Policy == FilePolicy.Optional;
                var wanted = !isOptional || enabled.Contains(file.Path);

                if (wanted) expected.TryAdd(file.Path, 0);

                if (isOptional && !wanted)
                {
                    if (File.Exists(absolute)) deletions.Add(file.Path);
                }
                else if (file.Policy == FilePolicy.Seed)
                {
                    // 只投放一次。玩家事后删掉是玩家的自由，不再补。
                    if (!File.Exists(absolute) && !_state.HasSeeded(file.Path))
                    {
                        downloads.Add(MakeItem(file, absolute, baseUrl));
                        seeded.Add(file.Path);
                    }
                }
                else
                {
                    if (NeedsDownload(file, absolute, freshHashes))
                        downloads.Add(MakeItem(file, absolute, baseUrl));
                }

                var n = Interlocked.Increment(ref done);
                if (n % 64 == 0 || n == total)
                    progress?.Report(new SyncStatus("校验文件", $"{n}/{total}", (double)n / Math.Max(1, total), 0, 0));
            });

        foreach (var (k, v) in freshHashes) _state.Hashes[k] = v;

        plan.Downloads.AddRange(downloads.OrderBy(d => d.Display, StringComparer.OrdinalIgnoreCase));
        plan.Deletions.AddRange(deletions);
        plan.SeedTargets.AddRange(seeded);

        // --- prune：清掉清单里没有的文件 ---
        foreach (var dir in manifest.Prune)
        {
            ct.ThrowIfCancellationRequested();
            var abs = _paths.ResolveGameFile(dir);
            if (!Directory.Exists(abs)) continue;

            foreach (var f in Directory.EnumerateFiles(abs, "*", SearchOption.AllDirectories))
            {
                var rel = Path.GetRelativePath(_paths.GameDir, f).Replace('\\', '/');
                if (expected.ContainsKey(rel)) continue;
                if (rel.EndsWith(".part", StringComparison.OrdinalIgnoreCase)) { plan.Deletions.Add(rel); continue; }
                // 玩家手动禁用的模组（.jar.disabled）也算多余文件，一并清掉
                plan.Deletions.Add(rel);
            }
        }

        Log.Info($"同步计划：下载 {plan.Downloads.Count} 个（{Human(plan.Bytes)}），删除 {plan.Deletions.Count} 个");
        return plan;
    }

    private bool NeedsDownload(ManagedFile file, string absolute, ConcurrentDictionary<string, HashCacheEntry> fresh)
    {
        var fi = new FileInfo(absolute);
        if (!fi.Exists) return true;
        if (file.Size > 0 && fi.Length != file.Size) return true;
        if (string.IsNullOrEmpty(file.Sha1)) return false;

        if (_state.Hashes.TryGetValue(file.Path, out var cached)
            && cached.Size == fi.Length
            && cached.MTimeTicks == fi.LastWriteTimeUtc.Ticks
            && cached.Sha1.Length == 40)
        {
            return !cached.Sha1.Equals(file.Sha1, StringComparison.OrdinalIgnoreCase);
        }

        string actual;
        try { actual = Hashing.Sha1File(absolute); }
        catch (IOException) { return true; }

        fresh[file.Path] = new HashCacheEntry
        {
            Size = fi.Length,
            MTimeTicks = fi.LastWriteTimeUtc.Ticks,
            Sha1 = actual,
        };
        return !actual.Equals(file.Sha1, StringComparison.OrdinalIgnoreCase);
    }

    private static DownloadItem MakeItem(ManagedFile file, string absolute, string baseUrl) => new()
    {
        Url = string.IsNullOrWhiteSpace(file.Url)
            ? $"{baseUrl}/files/{Uri.EscapeDataString(file.Path).Replace("%2F", "/", StringComparison.Ordinal)}"
            : file.Url!,
        TargetPath = absolute,
        ExpectedSize = file.Size,
        ExpectedSha1 = file.Sha1,
        Display = file.Path,
    };

    /// <summary>执行同步计划。先删后下，避免磁盘吃紧。</summary>
    public async Task ApplyAsync(SyncPlan plan, IProgress<SyncStatus>? progress, CancellationToken ct)
    {
        if (plan.Deletions.Count > 0)
        {
            progress?.Report(SyncStatus.Of("清理", $"{plan.Deletions.Count} 个多余文件"));
            foreach (var rel in plan.Deletions)
            {
                try
                {
                    var abs = _paths.ResolveGameFile(rel);
                    if (File.Exists(abs))
                    {
                        File.SetAttributes(abs, FileAttributes.Normal);
                        File.Delete(abs);
                        Log.Info($"删除：{rel}");
                    }
                    _state.Hashes.Remove(rel);
                }
                catch (Exception ex) { Log.Warn($"删除失败 {rel}：{ex.Message}"); }
            }
        }

        if (plan.Downloads.Count > 0)
        {
            var total = plan.Bytes;
            var reporter = new Progress<DownloadProgress>(p => progress?.Report(new SyncStatus(
                "下载",
                $"{p.FilesDone}/{p.FilesTotal}  {Human(p.BytesDone)} / {Human(p.BytesTotal)}",
                p.BytesTotal > 0 ? Math.Clamp((double)p.BytesDone / p.BytesTotal, 0, 1) : -1,
                p.BytesDone, p.BytesTotal)));

            await _downloader.DownloadAllAsync(plan.Downloads, reporter, ct).ConfigureAwait(false);

            // 下完的文件重新记账，下次启动就能走缓存
            foreach (var d in plan.Downloads)
            {
                try
                {
                    var rel = Path.GetRelativePath(_paths.GameDir, d.TargetPath).Replace('\\', '/');
                    var fi = new FileInfo(d.TargetPath);
                    if (fi.Exists && !string.IsNullOrEmpty(d.ExpectedSha1))
                        _state.Hashes[rel] = new HashCacheEntry
                        {
                            Size = fi.Length,
                            MTimeTicks = fi.LastWriteTimeUtc.Ticks,
                            Sha1 = d.ExpectedSha1!,
                        };
                }
                catch { }
            }
        }

        foreach (var s in plan.SeedTargets) _state.MarkSeeded(s);
    }

    public static string Human(long bytes)
    {
        if (bytes < 1024) return $"{bytes} B";
        string[] units = { "KB", "MB", "GB", "TB" };
        double v = bytes;
        var i = -1;
        do { v /= 1024; i++; } while (v >= 1024 && i < units.Length - 1);
        return $"{v:0.#} {units[i]}";
    }
}
