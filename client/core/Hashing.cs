using System.Security.Cryptography;

namespace BatterMC.Core;

public static class Hashing
{
    /// <summary>
    /// 带缓存的 SHA-1。文件大小和修改时间都和缓存一致就直接复用，
    /// 否则实算并回写缓存。这是"1.7G 的包每次启动只花 1 秒校验"的关键。
    /// </summary>
    public static string Sha1Cached(string absolutePath, string relativeKey, LocalState state)
    {
        var fi = new FileInfo(absolutePath);
        if (!fi.Exists) return "";

        if (state.Hashes.TryGetValue(relativeKey, out var cached)
            && cached.Size == fi.Length
            && cached.MTimeTicks == fi.LastWriteTimeUtc.Ticks
            && cached.Sha1.Length == 40)
        {
            return cached.Sha1;
        }

        var sha1 = Sha1File(absolutePath);
        state.Hashes[relativeKey] = new HashCacheEntry
        {
            Size = fi.Length,
            MTimeTicks = fi.LastWriteTimeUtc.Ticks,
            Sha1 = sha1,
        };
        return sha1;
    }

    public static string Sha1File(string path)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 20, FileOptions.SequentialScan);
        using var sha = SHA1.Create();
        return Convert.ToHexStringLower(sha.ComputeHash(stream));
    }

    public static string Sha256File(string path)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 20, FileOptions.SequentialScan);
        using var sha = SHA256.Create();
        return Convert.ToHexStringLower(sha.ComputeHash(stream));
    }
}
