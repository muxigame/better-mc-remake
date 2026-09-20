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

    /// <summary>已经投放过的 Seed 文件，避免玩家删掉后又被重新投放。</summary>
    public List<string> SeededFiles { get; set; } = new();

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
}
