using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;
using BatterMC.Protocol;

namespace BatterMC.Core;

/// <summary>Fail-closed importer for release-audited DH 3.3.3 public terrain only.</summary>
public static class PublicTerrainCache
{
    private const long MaxDatabase = 512L * 1024 * 1024;
    private const string CacheRoot = "Distant_Horizons_server_data";
    private const string DemEntry = "assets/muxi_flight/terrain/iwo-elevation-centimetres-i32le.bin.gz";

    public static void ValidateDistribution(PackManifest manifest)
    {
        if (manifest.PublicTerrainCaches.Count == 0) return;
        foreach (var file in manifest.Files)
            if (file.Path.Replace('\\', '/').Trim('/').Equals(CacheRoot, StringComparison.OrdinalIgnoreCase)
                || file.Path.Replace('\\', '/').TrimStart('/').StartsWith(CacheRoot + "/", StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Player terrain cache must never be a manifest file");
        foreach (var prune in manifest.Prune)
        {
            var value = prune.Replace('\\', '/').Trim('/');
            if (value.Length == 0 || value == "." || value.Equals(CacheRoot, StringComparison.OrdinalIgnoreCase)
                || value.StartsWith(CacheRoot + "/", StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Player terrain cache must never be pruned");
        }
        foreach (var overlay in manifest.Overlays)
            if (overlay.Path.Replace('\\', '/').Trim('/').Equals(CacheRoot, StringComparison.OrdinalIgnoreCase)
                || overlay.Path.Replace('\\', '/').TrimStart('/').StartsWith(CacheRoot + "/", StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Player terrain cache must never receive config overlays");
        foreach (var cache in manifest.PublicTerrainCaches)
            if (!manifest.Files.Any(f => f.Path == cache.PackagePath && f.Policy == FilePolicy.Managed))
                throw new InvalidDataException("Public terrain archive requires a Managed download entry");
    }

    public static string Install(LauncherPaths paths, PublicTerrainCacheSpec spec)
    {
        // Exact release identity, never a search for a similarly named installed mod.
        if (spec.Dimension != "muxi_minigames:flight_iwo_half" || spec.MetresPerBlock != 2
            || spec.RoomSpacing != 49152 || spec.RoomSlot != 0)
            throw new InvalidDataException("Unsupported public terrain identity/room");
        Component(spec.ServerFolderKey);
        if (!Regex.IsMatch(spec.HashedSeed, "^[A-Za-z0-9_-]{1,80}$"))
            throw new InvalidDataException("Invalid seed key");
        var package = SafeGameFile(paths, spec.PackagePath);
        var generator = SafeGameFile(paths, spec.GeneratorPath);
        var dh = SafeGameFile(paths, spec.DhPath);
        CheckHash(package, spec.PackageSha256);
        CheckHash(generator, spec.GeneratorSha256);
        CheckHash(dh, spec.DhSha256);
        using (var jar = ZipFile.OpenRead(generator))
        {
            var entry = jar.GetEntry(DemEntry) ?? throw new InvalidDataException("Generator DEM missing");
            if (entry.Length > 32 * 1024 * 1024) throw new InvalidDataException("DEM allocation limit");
            using var input = entry.Open();
            CheckDigest(Convert.ToHexStringLower(SHA256.HashData(input)), spec.DemSha256);
        }
        using var archive = ZipFile.OpenRead(package);
        if (archive.Entries.Count != 2 || archive.Entries.Count(e => e.FullName == "manifest.json") != 1
            || archive.Entries.Count(e => e.FullName == "terrain.sqlite") != 1)
            throw new InvalidDataException("Unexpected terrain archive members");
        var manifestEntry = archive.GetEntry("manifest.json")!;
        var database = archive.GetEntry("terrain.sqlite")!;
        if (manifestEntry.Length > 65536 || database.Length > MaxDatabase || database.Length < 100)
            throw new InvalidDataException("Terrain archive allocation limit");
        using var manifestStream = manifestEntry.Open();
        using var document = JsonDocument.Parse(manifestStream);
        var m = document.RootElement;
        if (m.GetProperty("format").GetInt32() != 1 || !m.GetProperty("terrainOnly").GetBoolean()
            || m.GetProperty("coordinateFrame").GetString() != "absolute-world-blocks"
            || m.GetProperty("dimension").GetString() != spec.Dimension
            || m.GetProperty("metresPerBlock").GetInt32() != spec.MetresPerBlock
            || m.GetProperty("roomSpacing").GetInt32() != spec.RoomSpacing
            || m.GetProperty("hashedSeed").GetString() != spec.HashedSeed
            || m.GetProperty("generatorSha256").GetString() != spec.GeneratorSha256
            || m.GetProperty("demSha256").GetString() != spec.DemSha256
            || m.GetProperty("dhSha256").GetString() != spec.DhSha256
            || m.GetProperty("databaseBytes").GetInt64() != database.Length
            || m.GetProperty("supportedSlots").GetArrayLength() != 1
            || m.GetProperty("supportedSlots")[0].GetInt32() != 0)
            throw new InvalidDataException("Terrain package/deployment identity mismatch");
        var expected = m.GetProperty("databaseSha256").GetString()!;
        DigestSyntax(expected);
        var relative = CacheRoot + "/" + spec.ServerFolderKey + "/" + spec.HashedSeed + "@"
            + spec.Dimension.Replace(":", "@@") + "/DistantHorizons.sqlite";
        var destination = SafeGameFile(paths, relative);
        // Also preserve sidecars when the DB itself is absent (recovery or another process).
        if (Exists(destination) || new[] { "-wal", "-shm", "-journal" }.Any(s => Exists(destination + s)))
            return "existing player cache preserved";
        var volume = new DriveInfo(Path.GetPathRoot(destination)!);
        if (volume.AvailableFreeSpace < Math.Max(2L * 1024 * 1024 * 1024, database.Length * 2))
            throw new IOException("Insufficient terrain staging reserve");
        var parent = Path.GetDirectoryName(destination)!;
        Directory.CreateDirectory(parent);
        NoLinks(paths.GameDir, destination);
        var staged = Path.Combine(parent, ".public-terrain-" + Guid.NewGuid().ToString("N") + ".tmp");
        try
        {
            using (var input = database.Open())
            using (var output = new FileStream(staged, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                var buffer = new byte[65536]; long total = 0; int count;
                while ((count = input.Read(buffer, 0, buffer.Length)) > 0)
                {
                    total += count;
                    if (total > database.Length) throw new InvalidDataException("Inflated database exceeds declared size");
                    output.Write(buffer, 0, count);
                }
                if (total != database.Length) throw new InvalidDataException("Truncated database");
                output.Flush(true);
            }
            CheckHash(staged, expected);
            using (var input = File.OpenRead(staged))
            {
                var header = new byte[16]; input.ReadExactly(header);
                if (!header.AsSpan().SequenceEqual("SQLite format 3\0"u8))
                    throw new InvalidDataException("Not a SQLite database");
            }
            NoLinks(paths.GameDir, destination);
            if (new[] { "-wal", "-shm", "-journal" }.Any(s => Exists(destination + s)))
                return "existing player cache sidecar preserved";
            // Same-directory rename, no overwrite, even if another importer won the race.
            try { File.Move(staged, destination, overwrite: false); }
            catch (IOException) when (Exists(destination)) { return "existing player cache preserved"; }
            return "public terrain installed";
        }
        finally { if (File.Exists(staged)) File.Delete(staged); }
    }

    private static bool Exists(string path) => File.Exists(path) || Directory.Exists(path);
    private static void Component(string value)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Length > 180 || value is "." or ".."
            || value.Any(c => char.IsControl(c) || "<>:\"/\\|?*".Contains(c))
            || value.EndsWith('.') || value.EndsWith(' ')
            || Regex.IsMatch(value, "^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\\.|$)", RegexOptions.IgnoreCase))
            throw new InvalidDataException("Unsafe DH folder key");
    }
    private static string SafeGameFile(LauncherPaths paths, string relative)
    {
        if (relative.Contains(':') || Path.IsPathRooted(relative)) throw new InvalidDataException("Absolute/stream path rejected");
        var full = paths.ResolveGameFile(relative);
        NoLinks(paths.GameDir, full);
        return full;
    }
    private static void NoLinks(string root, string full)
    {
        for (var current = full; current is not null; current = Path.GetDirectoryName(current))
        {
            if (Exists(current) && (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Terrain paths cannot traverse links/junctions");
            if (current.Equals(Path.GetFullPath(root), StringComparison.OrdinalIgnoreCase)) return;
        }
        throw new IOException("Terrain path escaped game directory");
    }
    private static void DigestSyntax(string expected)
    {
        if (expected is null || !Regex.IsMatch(expected, "^[a-f0-9]{64}$")) throw new InvalidDataException("Missing SHA-256 binding");
    }
    private static void CheckDigest(string actual, string expected)
    {
        DigestSyntax(expected);
        if (actual != expected) throw new InvalidDataException("Terrain artifact SHA-256 mismatch");
    }
    private static void CheckHash(string path, string expected)
    {
        using var input = File.OpenRead(path);
        CheckDigest(Convert.ToHexStringLower(SHA256.HashData(input)), expected);
    }
}
