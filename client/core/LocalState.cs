using System.Text.Json;
using System.Text.Json.Serialization;

namespace BatterMC.Core;

public sealed class HashCacheEntry
{
    public long Size { get; set; }
    public long MTimeTicks { get; set; }
    public string Sha1 { get; set; } = "";
}

/// <summary>
/// 启动器对本地安装状态的记账。玩家不该手改，但删掉也只是导致一次全量重算。
/// </summary>
public sealed class LocalState
{
    /// <summary>上次成功同步完成的整合包版本。</summary>
    public string? InstalledPackVersion { get; set; }

    /// <summary>上次同步完成时间。</summary>
    public DateTimeOffset? LastSync { get; set; }

    /// <summary>
    /// 文件哈希缓存：相对路径 → (大小, 修改时间, SHA-1)。
    /// 大小和修改时间都没变就直接用缓存的哈希，否则重算。
    /// 整合包 7800+ 个文件，没有这个缓存每次启动要多花几十秒。
    /// </summary>
    public Dictionary<string, HashCacheEntry> Hashes { get; set; } = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>
    /// 启动器自己投放过的文件（相对路径）。prune 只清理这里面的东西 ——
    /// 玩家自己往 mods 里放的模组我们没装过，也就没资格替他删。
    ///
    /// 跟 <see cref="Hashes"/> 分开存：那个是可以随时丢弃重建的缓存，
    /// resetVerification 会清空它；这份是记账，清空了就再也认不出哪些文件是我们的。
    /// </summary>
    public HashSet<string> InstalledFiles { get; set; } = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>已经投放过的 Seed 文件，避免玩家删掉后又被重新投放。</summary>
    public List<string> SeededFiles { get; set; } = new();

    /// <summary>
    /// Seed 文件最后一次由发布端强制投放的内容修订：相对路径 → manifest SHA-1。
    ///
    /// 语义不是“永远跟服务器一致”，而是“每个新修订强制同步一次”：
    /// - 首次安装下载；
    /// - 玩家之后可自由修改/删除；
    /// - 服务器将该 Seed 文件发布为新的 SHA-1 时，再强制同步一次。
    /// </summary>
    public Dictionary<string, string> SeedRevisions { get; set; } = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>最后一次由官方控制面下发的游戏文件根地址，用于离线 manifest 缓存。</summary>
    public string? LastFilesBaseUrl { get; set; }

    /// <summary>最后一次下发的 Minecraft 本体镜像根地址。控制面连不上时照样用得上。</summary>
    public string? LastMirrorBaseUrl { get; set; }

    /// <summary>
    /// 最后一次写过显卡偏好的 java 路径。
    ///
    /// JRE 升级会换目录，记着上一个才能把旧条目清掉——否则玩家注册表里会攒一堆
    /// 指向早就删了的 java.exe 的设置。
    /// </summary>
    public string? GpuPreferenceTarget { get; set; }

    [JsonIgnore] private string? _file;

    private static readonly JsonSerializerOptions Opts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true,
        WriteIndented = false,
    };

    private static readonly CoreJsonContext Context = new(Opts);

    public static LocalState Load(string file)
    {
        try
        {
            if (File.Exists(file))
            {
                var s = JsonSerializer.Deserialize(File.ReadAllText(file), Context.LocalState);
                if (s is not null)
                {
                    s._file = file;
                    s.Hashes = new Dictionary<string, HashCacheEntry>(s.Hashes, StringComparer.OrdinalIgnoreCase);
                    s.SeedRevisions = new Dictionary<string, string>(
                        s.SeedRevisions ?? new Dictionary<string, string>(),
                        StringComparer.OrdinalIgnoreCase);

                    s.InstalledFiles = new HashSet<string>(
                        s.InstalledFiles ?? new HashSet<string>(),
                        StringComparer.OrdinalIgnoreCase);
                    // 老客户端升级上来还没有这份记账：拿哈希缓存兜底，
                    // 那里面的键正好就是启动器同步过的每一个清单文件。
                    if (s.InstalledFiles.Count == 0 && s.Hashes.Count > 0)
                        foreach (var key in s.Hashes.Keys) s.InstalledFiles.Add(key);
                    return s;
                }
            }
        }
        catch (Exception ex)
        {
            Log.Warn($"state.json 读取失败，将重新全量校验：{ex.Message}");
        }
        return new LocalState { _file = file };
    }

    public void Save(string? file = null)
    {
        var target = file ?? _file;
        if (target is null) return;
        Directory.CreateDirectory(Path.GetDirectoryName(target)!);
        AtomicFile.WriteAllText(target, JsonSerializer.Serialize(this, Context.LocalState));
    }

    public bool HasSeeded(string relative) =>
        SeededFiles.Contains(relative, StringComparer.OrdinalIgnoreCase);

    public void MarkSeeded(string relative)
    {
        if (!HasSeeded(relative)) SeededFiles.Add(relative);
    }

    public bool TryGetSeedRevision(string relative, out string revision)
        => SeedRevisions.TryGetValue(relative, out revision!);

    public void MarkSeedRevision(string relative, string revision)
    {
        if (string.IsNullOrWhiteSpace(revision)) return;
        SeedRevisions[relative] = revision;
        MarkSeeded(relative); // 保留旧客户端/旧 state 语义，便于向后兼容。
    }
}
