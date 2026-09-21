using System.Text.Json;
using System.Text.Json.Serialization;

namespace BatterMC.Core;

/// <summary>玩家可以改的东西。服务器不碰这个文件。</summary>
public sealed class LauncherSettings
{
    public const string OfficialUpdateBaseUrl = "https://mc.muxigame.com";

    public string Username { get; set; } = "";

    /// <summary>手动指定的 java 可执行文件。留空则自动探测 / 自动下载。</summary>
    public string? JavaPath { get; set; }

    /// <summary>最大堆，MB。0 = 按物理内存自动决定。</summary>
    public int MaxMemoryMb { get; set; }

    /// <summary>额外 JVM 参数，空格分隔。高级选项，出问题先清空。</summary>
    public string ExtraJvmArgs { get; set; } = "";

    public int WindowWidth { get; set; } = 1280;
    public int WindowHeight { get; set; } = 720;
    public bool Fullscreen { get; set; }

    /// <summary>启动后直连主服务器，跳过多人列表。</summary>
    public bool AutoJoinServer { get; set; } = true;

    /// <summary>更新服务器根地址。</summary>
    public string UpdateBaseUrl { get; set; } = OfficialUpdateBaseUrl;

    /// <summary>Muxi Account 的 OAuth/OIDC 服务根地址。与整合包更新服务解耦。</summary>
    public string AuthBaseUrl { get; set; } = "https://account.muxigame.com";

    /// <summary>游戏启动后是否保留启动器窗口。</summary>
    public bool KeepLauncherOpen { get; set; }

    /// <summary>玩家勾选启用的 Optional 文件（清单里的相对路径）。</summary>
    public List<string> EnabledOptional { get; set; } = new();

    /// <summary>跳过文件校验直接启动。仅用于救急，界面上会红字警告。</summary>
    public bool SkipVerify { get; set; }

    /// <summary>自定义游戏目录。留空 = 启动器目录下的 Better MC Remake [FORGE]\。</summary>
    public string? GameDirOverride { get; set; }

    [JsonIgnore] public string? LoadedFrom { get; private set; }

    private static readonly JsonSerializerOptions Opts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true,
        WriteIndented = true,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    private static readonly CoreJsonContext Context = new(Opts);

    public static LauncherSettings Load(string file)
    {
        try
        {
            if (File.Exists(file))
            {
                var s = JsonSerializer.Deserialize(File.ReadAllText(file), Context.LauncherSettings);
                if (s is not null) { s.LoadedFrom = file; return s; }
            }
        }
        catch (Exception ex)
        {
            Log.Warn($"settings.json 读取失败，使用默认值：{ex.Message}");
        }
        return new LauncherSettings { LoadedFrom = file };
    }

    public void Save(string file)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(file)!);
        AtomicFile.WriteAllText(file, JsonSerializer.Serialize(this, Context.LauncherSettings));
    }

    /// <summary>
    /// 自动内存：物理内存的一半，夹在 4G–8G 之间，并且至少给系统留 4G。
    /// 405 个模组的包低于 4G 基本必崩，高于 8G 对 G1 反而是负担。
    /// </summary>
    public int EffectiveMaxMemoryMb()
    {
        if (MaxMemoryMb > 0) return MaxMemoryMb;
        long totalMb;
        try { totalMb = (long)(GC.GetGCMemoryInfo().TotalAvailableMemoryBytes / (1024 * 1024)); }
        catch { totalMb = 8192; }
        if (totalMb <= 0) totalMb = 8192;
        var half = totalMb / 2;
        var value = Math.Clamp(half, 4096, 8192);
        var leaveForSystem = totalMb - 4096;
        if (leaveForSystem > 2048 && value > leaveForSystem) value = leaveForSystem;
        return (int)Math.Max(2048, value);
    }
}
