using System.Text.Json;

namespace BatterMC.Core;

/// <summary>
/// 补齐 Minecraft 本体：144 个库、资源索引 + 资源对象、vanilla 客户端 jar。
///
/// 这些全部从 Mojang / NeoForge 官方 CDN 拉，不经过我们的更新服务器 ——
/// 它们自带 sha1 和体积，而且是全世界最稳的镜像。
/// 更新服务器只需要托管整合包自己的那 1.2G（mods / config / 资源包）。
/// </summary>
public static class VanillaInstaller
{
    private const string AssetCdn = "https://resources.download.minecraft.net";

    public sealed record Plan(List<DownloadItem> Items, long Bytes)
    {
        public bool IsEmpty => Items.Count == 0;
    }

    /// <summary>算出还缺什么。库和客户端 jar 走哈希缓存校验，资源对象只看存在和大小
    /// （资源是按内容哈希命名的，名字对上基本就不会错，4000 个文件逐个算 sha1 太慢）。</summary>
    public static async Task<Plan> PlanAsync(
        VersionJson version, LauncherPaths paths, LocalState state,
        Downloader downloader, IProgress<string>? status, CancellationToken ct)
    {
        var items = new List<DownloadItem>();

        // --- 客户端 jar ---
        var clientJar = Path.Combine(paths.VersionsDir, version.Id, version.Id + ".jar");
        if (NeedsFile(clientJar, version.ClientJarSize, version.ClientJarSha1, $"versions/{version.Id}/{version.Id}.jar", state)
            && !string.IsNullOrEmpty(version.ClientJarUrl))
        {
            items.Add(new DownloadItem
            {
                Url = version.ClientJarUrl!,
                TargetPath = clientJar,
                ExpectedSize = version.ClientJarSize,
                ExpectedSha1 = version.ClientJarSha1,
                Display = $"{version.Id}.jar",
            });
        }

        // --- 库 ---
        status?.Report("校验 Minecraft 运行库…");
        foreach (var lib in version.Libraries)
        {
            var target = Path.Combine(paths.LibrariesDir, lib.Path.Replace('/', Path.DirectorySeparatorChar));
            if (!NeedsFile(target, lib.Size, lib.Sha1, "libraries/" + lib.Path, state)) continue;
            if (string.IsNullOrEmpty(lib.Url))
            {
                Log.Warn($"库缺失且没有下载地址：{lib.Name}");
                continue;
            }
            items.Add(new DownloadItem
            {
                Url = lib.Url,
                TargetPath = target,
                ExpectedSize = lib.Size,
                ExpectedSha1 = lib.Sha1,
                Display = Path.GetFileName(lib.Path),
            });
        }

        // --- 资源索引 ---
        status?.Report("校验游戏资源…");
        var indexPath = Path.Combine(paths.AssetsDir, "indexes", version.AssetIndex.Id + ".json");
        if (!File.Exists(indexPath) ||
            (version.AssetIndex.Size > 0 && new FileInfo(indexPath).Length != version.AssetIndex.Size))
        {
            Log.Info($"下载资源索引 {version.AssetIndex.Id}");
            Directory.CreateDirectory(Path.GetDirectoryName(indexPath)!);
            var bytes = await downloader.GetBytesAsync(version.AssetIndex.Url, ct).ConfigureAwait(false);
            AtomicFile.WriteAllBytes(indexPath, bytes);
        }

        // --- 资源对象 ---
        if (File.Exists(indexPath))
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(indexPath));
            if (doc.RootElement.TryGetProperty("objects", out var objects))
            {
                var objectsDir = Path.Combine(paths.AssetsDir, "objects");
                foreach (var obj in objects.EnumerateObject())
                {
                    ct.ThrowIfCancellationRequested();
                    var hash = obj.Value.GetProperty("hash").GetString()!;
                    var size = obj.Value.TryGetProperty("size", out var s) ? s.GetInt64() : 0;
                    var prefix = hash[..2];
                    var target = Path.Combine(objectsDir, prefix, hash);

                    var fi = new FileInfo(target);
                    if (fi.Exists && (size == 0 || fi.Length == size)) continue;

                    items.Add(new DownloadItem
                    {
                        Url = $"{AssetCdn}/{prefix}/{hash}",
                        TargetPath = target,
                        ExpectedSize = size,
                        ExpectedSha1 = hash, // 资源文件名就是它的 sha1
                        Display = obj.Name,
                    });
                }
            }
        }

        return new Plan(items, items.Sum(i => Math.Max(0, i.ExpectedSize)));
    }

    private static bool NeedsFile(string absolute, long size, string sha1, string cacheKey, LocalState state)
    {
        var fi = new FileInfo(absolute);
        if (!fi.Exists) return true;
        if (size > 0 && fi.Length != size) return true;
        if (string.IsNullOrEmpty(sha1)) return false;
        return !Hashing.Sha1Cached(absolute, cacheKey, state).Equals(sha1, StringComparison.OrdinalIgnoreCase);
    }
}
