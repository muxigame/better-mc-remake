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

    /// <summary>可选内容开关引起的改名：从旧文件名到新文件名，相对游戏目录。</summary>
    public List<(string From, string To)> Renames { get; } = new();
    public Dictionary<string, string> SeedRevisionTargets { get; } = new(StringComparer.OrdinalIgnoreCase);
    public long Bytes => Downloads.Sum(d => Math.Max(0, d.ExpectedSize));
    public bool IsEmpty => Downloads.Count == 0 && Deletions.Count == 0 && Renames.Count == 0;
}

/// <summary>
/// 整合包文件同步。
///
/// 三种策略：
///   Managed  — 服务器说了算，哈希不符就覆盖，玩家删了补回来
///   Seed     — 每个服务端 SHA 修订强制投放一次，之后重新交还给玩家
///   Optional — 跟 Managed 一样同步下来，玩家自己开关；关掉只是改名成 .disabled，不删文件
///
/// 外加 prune：清单指定的目录里，清掉"启动器装过、但清单里已经没有"的文件 ——
/// 比如整合包移除了某个模组，得把它从玩家机器上收回来。
/// 玩家自己放进去的文件不在记账里，一概不碰。
/// config 默认不 prune —— 模组会在运行时自己生成配置文件，删了会天天重建。
/// </summary>
public sealed class SyncEngine
{
    private readonly LauncherPaths _paths;
    private readonly LocalState _state;
    private readonly LauncherSettings _settings;
    private readonly Downloader _downloader;

    public string? LastManifestUrl { get; private set; }
    public string? LastManifestError { get; private set; }
    public string? LastFilesBaseUrl { get; private set; }
    public string? LastMirrorBaseUrl { get; private set; }

    public SyncEngine(LauncherPaths paths, LocalState state, LauncherSettings settings, Downloader downloader)
    {
        _paths = paths;
        _state = state;
        _settings = settings;
        _downloader = downloader;
    }

    public async Task<PackManifest> FetchManifestAsync(string baseUrl, CancellationToken ct)
    {
        var urls = ManifestCandidates(baseUrl).ToArray();
        Exception? lastError = null;

        foreach (var url in urls)
        {
            Log.Info($"请求更新控制面：{url}");
            try
            {
                var controlJson = await _downloader.GetStringAsync(url, ct).ConfigureAwait(false);
                var control = ManifestControl.FromJson(controlJson)
                    ?? throw new InvalidOperationException("更新控制面响应为空");
                if (string.IsNullOrWhiteSpace(control.ManifestUrl))
                    throw new InvalidOperationException("更新控制面没有返回 manifestUrl");
                if (string.IsNullOrWhiteSpace(control.FilesBaseUrl))
                    throw new InvalidOperationException("更新控制面没有返回 filesBaseUrl");

                Log.Info($"拉取真实清单：{control.ManifestUrl}");
                var json = await _downloader.GetStringAsync(control.ManifestUrl, ct).ConfigureAwait(false);
                var manifest = PackManifest.FromJson(json)
                    ?? throw new InvalidOperationException("清单内容为空");
                manifest.Launcher = control.Launcher;
                AtomicFile.WriteAllText(_paths.ManifestCacheFile, json);
                LastManifestUrl = control.ManifestUrl;
                LastFilesBaseUrl = control.FilesBaseUrl.TrimEnd('/');
                _state.LastFilesBaseUrl = LastFilesBaseUrl;
                LastMirrorBaseUrl = string.IsNullOrWhiteSpace(control.MirrorBaseUrl)
                    ? null : control.MirrorBaseUrl!.TrimEnd('/');
                _state.LastMirrorBaseUrl = LastMirrorBaseUrl;
                LastManifestError = null;
                Log.Info($"清单版本 {manifest.Pack.Version}，{manifest.Files.Count} 个文件；来源 {control.ManifestUrl}");
                return manifest;
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                lastError = ex;
                LastManifestError = ex.Message;
                Log.Warn($"更新源失败 {url}：{ex.Message}");
            }
        }

        if (lastError is null)
            lastError = new InvalidOperationException("没有可用的更新清单地址");

        Log.Warn($"所有在线更新源均失败（{lastError.Message}），尝试使用上次缓存");
        if (File.Exists(_paths.ManifestCacheFile))
        {
            var cached = PackManifest.FromJson(File.ReadAllText(_paths.ManifestCacheFile));
            if (cached is not null)
            {
                LastManifestUrl = "cache";
                LastFilesBaseUrl = _state.LastFilesBaseUrl;
                LastMirrorBaseUrl = _state.LastMirrorBaseUrl;
                Log.Warn($"离线模式：使用缓存的清单 {cached.Pack.Version}");
                return cached;
            }
        }
        throw lastError;
    }

    private static IEnumerable<string> ManifestCandidates(string configured)
    {
        static string ManifestUrl(string value)
        {
            var trimmed = value.Trim().TrimEnd('/');
            if (trimmed.EndsWith("/api/v1/manifest", StringComparison.OrdinalIgnoreCase))
                return trimmed;
            if (trimmed.EndsWith("/api/v1", StringComparison.OrdinalIgnoreCase))
                return trimmed + "/manifest";
            return trimmed + "/api/v1/manifest";
        }

        var primary = ManifestUrl(string.IsNullOrWhiteSpace(configured)
            ? LauncherSettings.OfficialUpdateBaseUrl
            : configured);
        yield return primary;

        var official = ManifestUrl(LauncherSettings.OfficialUpdateBaseUrl);
        if (!primary.Equals(official, StringComparison.OrdinalIgnoreCase))
            yield return official;
    }

    /// <summary>
    /// 校验本地文件，算出需要下载和删除的清单。
    /// 哈希计算并行跑，结果统一回写缓存（LocalState 本身不是线程安全的）。
    /// </summary>
    public SyncPlan Plan(PackManifest manifest, IProgress<SyncStatus>? progress, CancellationToken ct)
    {
        var plan = new SyncPlan();
        var baseUrl = (LastFilesBaseUrl ?? _state.LastFilesBaseUrl)
            ?.TrimEnd('/')
            ?? throw new InvalidOperationException("更新控制面未提供 filesBaseUrl");
        var enabled = new HashSet<string>(_settings.EnabledOptional, StringComparer.OrdinalIgnoreCase);

        // 并行校验，所以用并发集合；ConcurrentDictionary 当作 set 用
        var expected = new ConcurrentDictionary<string, byte>(StringComparer.OrdinalIgnoreCase);
        var freshHashes = new ConcurrentDictionary<string, HashCacheEntry>(StringComparer.OrdinalIgnoreCase);
        var downloads = new ConcurrentBag<DownloadItem>();
        var deletions = new ConcurrentBag<string>();
        var renames = new ConcurrentBag<(string From, string To)>();
        var seedRevisionTargets = new ConcurrentDictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        var seedBaselines = new ConcurrentDictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        var done = 0;
        var total = manifest.Files.Count;

        Parallel.ForEach(manifest.Files,
            new ParallelOptions { CancellationToken = ct, MaxDegreeOfParallelism = Environment.ProcessorCount },
            file =>
            {
                var isOptional = file.Policy == FilePolicy.Optional;
                var wanted = !isOptional || enabled.Contains(file.Path);

                // 禁用的可选内容不删，只是带着 .disabled 后缀躺在原地，随时能切回来。
                var relative = OptionalContent.PathFor(file.Path, wanted);
                var absolute = _paths.ResolveGameFile(relative);

                // 两个名字都是"清单认可的文件"，prune 一个都不许碰
                expected.TryAdd(file.Path, 0);
                if (isOptional) expected.TryAdd(file.Path + OptionalContent.DisabledSuffix, 0);

                if (isOptional)
                {
                    // 开关状态变了：改名就行，不用重新下载
                    var otherRel = OptionalContent.PathFor(file.Path, !wanted);
                    var otherAbs = _paths.ResolveGameFile(otherRel);
                    if (!File.Exists(absolute) && File.Exists(otherAbs))
                    {
                        renames.Add((otherRel, relative));
                        // 内容对不对按改名后的那份算，免得白下一遍
                        if (NeedsDownload(file, otherAbs, freshHashes))
                            downloads.Add(MakeItem(file, absolute, baseUrl));
                    }
                    else
                    {
                        if (File.Exists(absolute) && File.Exists(otherAbs)) deletions.Add(otherRel);
                        if (NeedsDownload(file, absolute, freshHashes))
                            downloads.Add(MakeItem(file, absolute, baseUrl));
                    }
                }
                else if (file.Policy == FilePolicy.Seed)
                {
                    // Seed = 每个服务端修订强制同步一次，而不是永久 Managed。
                    // 第一次切换到 revision 模型时，如果本地已有文件，只记录当前 SHA 为基线，
                    // 不覆盖玩家已经修改过的配置；以后 manifest SHA 变化才强制同步一次。
                    if (_state.TryGetSeedRevision(file.Path, out var appliedRevision))
                    {
                        if (!appliedRevision.Equals(file.Sha1, StringComparison.OrdinalIgnoreCase))
                        {
                            if (NeedsDownload(file, absolute, freshHashes))
                            {
                                downloads.Add(MakeItem(file, absolute, baseUrl));
                                seedRevisionTargets[file.Path] = file.Sha1;
                            }
                            else
                            {
                                // 本地内容已经等于新修订，只更新记账即可。
                                seedBaselines[file.Path] = file.Sha1;
                            }
                        }
                    }
                    else if (_state.HasSeeded(file.Path) || File.Exists(absolute))
                    {
                        // 老 state 或现有安装：当前服务端版本作为基线，不做一次性全覆盖。
                        seedBaselines[file.Path] = file.Sha1;
                    }
                    else
                    {
                        // 真正的首次安装。
                        downloads.Add(MakeItem(file, absolute, baseUrl));
                        seedRevisionTargets[file.Path] = file.Sha1;
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
        plan.Renames.AddRange(renames);
        foreach (var (path, revision) in seedRevisionTargets)
            plan.SeedRevisionTargets[path] = revision;
        foreach (var (path, revision) in seedBaselines)
            _state.MarkSeedRevision(path, revision);

        // 清单里的每一个文件都是启动器投放的，记下来。
        // prune 只认这份记账：没装过的东西不归我们管。
        foreach (var file in manifest.Files) _state.InstalledFiles.Add(file.Path);

        // --- prune：清掉"我们装过、但清单里已经没有"的文件 ---
        // 玩家自己往 mods 里放的本地模组一律不碰。它们大多是小地图、光影这类纯客户端
        // 模组，并不会让他连不上服务器；就算真会，那也是他自己的选择，不该由启动器
        // 悄悄删掉别人的文件来替他决定。
        foreach (var dir in manifest.Prune)
        {
            ct.ThrowIfCancellationRequested();
            var abs = _paths.ResolveGameFile(dir);
            if (!Directory.Exists(abs)) continue;

            foreach (var f in Directory.EnumerateFiles(abs, "*", SearchOption.AllDirectories))
            {
                var rel = Path.GetRelativePath(_paths.GameDir, f).Replace('\\', '/');
                if (expected.ContainsKey(rel)) continue;
                if (rel.EndsWith(".part", StringComparison.OrdinalIgnoreCase))
                {
                    // 清单里还要这个文件，说明这是上次没下完的残片 —— 留着给断点续传。
                    // 清掉的话玩家取消一次下载，下次就得从头再来一遍。
                    var pending = rel[..^".part".Length];
                    if (expected.ContainsKey(pending)) continue;
                    // 目标都不在清单里了，残片就是纯垃圾
                    plan.Deletions.Add(rel);
                    continue;
                }

                // 可选内容被关掉时带着 .disabled 后缀，按本名查记账
                var canonical = rel.EndsWith(OptionalContent.DisabledSuffix, StringComparison.OrdinalIgnoreCase)
                    ? rel[..^OptionalContent.DisabledSuffix.Length]
                    : rel;
                if (!_state.InstalledFiles.Contains(rel) && !_state.InstalledFiles.Contains(canonical)) continue;

                plan.Deletions.Add(rel);
            }
        }

        Log.Info($"同步计划：下载 {plan.Downloads.Count} 个（{Human(plan.Bytes)}），"
               + $"删除 {plan.Deletions.Count} 个，改名 {plan.Renames.Count} 个");
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
            ? $"{baseUrl}/{Uri.EscapeDataString(file.Path).Replace("%2F", "/", StringComparison.Ordinal)}"
            : file.Url!,
        TargetPath = absolute,
        ExpectedSize = file.Size,
        ExpectedSha1 = file.Sha1,
        Display = file.Path,
    };

    /// <summary>执行同步计划。先删、再改名、最后下载，避免磁盘吃紧也避免白下。</summary>
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
                    _state.InstalledFiles.Remove(rel);
                }
                catch (Exception ex) { Log.Warn($"删除失败 {rel}：{ex.Message}"); }
            }
        }

        // 改名排在下载前面：可选内容切换状态时目标名字要先腾出来，
        // 内容真有问题才会跟着来一次下载覆盖。
        if (plan.Renames.Count > 0)
        {
            progress?.Report(SyncStatus.Of("切换可选内容", $"{plan.Renames.Count} 个"));
            foreach (var (from, to) in plan.Renames)
            {
                try
                {
                    var src = _paths.ResolveGameFile(from);
                    var dst = _paths.ResolveGameFile(to);
                    if (!File.Exists(src)) continue;
                    if (File.Exists(dst)) File.Delete(dst);
                    Directory.CreateDirectory(Path.GetDirectoryName(dst)!);
                    File.Move(src, dst);
                    Log.Info($"改名：{from} -> {to}");
                }
                catch (Exception ex) { Log.Warn($"改名失败 {from}：{ex.Message}"); }
            }
        }

        if (plan.Downloads.Count > 0)
        {
            var total = plan.Bytes;
            var reporter = new InlineProgress<DownloadProgress>(p => progress?.Report(new SyncStatus(
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
                    // 记账键统一用清单里的路径：可选内容禁用时文件名带 .disabled，
                    // 按物理名字记会和校验时的查法对不上，白白每次重算哈希。
                    var rel = d.Display;
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

        foreach (var (path, revision) in plan.SeedRevisionTargets)
            _state.MarkSeedRevision(path, revision);
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
