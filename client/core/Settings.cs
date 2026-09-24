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

    /// <summary>muxi 账户 的 OAuth/OIDC 服务根地址。与整合包更新服务解耦。</summary>
    public string AuthBaseUrl { get; set; } = "https://account.muxigame.com";

    /// <summary>游戏启动后是否保留启动器窗口。</summary>
    public bool KeepLauncherOpen { get; set; }

    /// <summary>玩家勾选启用的 Optional 文件（清单里的相对路径）。</summary>
    public List<string> EnabledOptional { get; set; } = new();

    /// <summary>
    /// 玩家关掉的「默认开启」可选项（<see cref="BatterMC.Protocol.ManagedFile.OptionalDefaultOn"/>）。
    /// 默认开的记关了哪些、默认关的记开了哪些：玩家没碰过的项永远跟着清单的默认值走。
    /// </summary>
    public List<string> DisabledOptional { get; set; } = new();

    /// <summary>上传崩溃日志时附带电脑环境（系统、CPU、内存、显卡与驱动、Java）。弹窗里的开关，记住上次的选择。</summary>
    public bool CrashReportEnvironment { get; set; } = true;

    /// <summary>跳过文件校验直接启动。仅用于救急，界面上会红字警告。</summary>
    public bool SkipVerify { get; set; }

    /// <summary>
    /// 端侧隧道的共享密钥。为空时退化成直连校验：仍然会在启动游戏前确认线路
    /// 真的能问到服务端版本号，只是不经隧道。
    /// </summary>
    public string? TunnelToken { get; set; }

    /// <summary>服务端侧隧道工具的监听端口。</summary>
    public int TunnelPort { get; set; } = 25540;

    /// <summary>
    /// 打洞用的反射器地址，逗号分隔。客户端靠它得知自己在公网上的出口地址。
    ///
    /// 必须是两台**不同 IP** 的服务器：同一个 socket 问两台，答案一致说明是端点
    /// 无关型 NAT（对端照着打就行），不一致说明换个目标就换映射端口，对端必须在
    /// 邻近端口扫描才能命中。只问一台是区分不出来的。
    ///
    /// 只收发几十字节，放在哪台服务器都行。
    /// </summary>
    public string PunchReflector { get; set; } = "110.42.51.3,191.40.37.147";

    /// <summary>
    /// 玩家点名要的光影预设：包名，空串 = 无光影，null = 还没选过（首次安装落默认值）。
    /// 真正生效的值在 config/iris.properties 里，这份只是启动时用来把它写回去 ——
    /// 那个文件是 Seed 策略，服务端发新版会整份重投，不记一份玩家的选择就会被冲掉。
    /// </summary>
    public string? ShaderPack { get; set; }

    /// <summary>自定义游戏目录。留空 = 启动器目录下的 Better MC Remake [FORGE]\。</summary>
    public string? GameDirOverride { get; set; }

    /// <summary>
    /// 用哪块显卡跑游戏："performance"（独显）/ "power"（核显）/ "auto"（交给 Windows）。
    ///
    /// 默认独显。双显卡笔记本上 Windows 经常把 Java 丢给核显，玩家只会看到帧数上不去，
    /// 不会有任何报错，自己也想不到去系统设置里改。
    /// </summary>
    public string Gpu { get; set; } = "performance";

    public GpuChoice EffectiveGpuChoice() => Gpu?.Trim().ToLowerInvariant() switch
    {
        "power" => GpuChoice.PowerSaving,
        "auto" => GpuChoice.Auto,
        _ => GpuChoice.HighPerformance,
    };

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
    ///
    /// 玩家手填的值同样要夹 —— 在 12G 的机器上填 16G，游戏一进去就被整合包的
    /// memorysettings 拦下来弹警告屏，更糟的是堆挤掉原生内存，JVM 会直接 abort。
    /// 上限取"物理内存留够系统"和"整合包自己声明的阈值"里更小的那个。
    /// </summary>
    public int EffectiveMaxMemoryMb(PackMemoryLimits limits = default)
    {
        long totalMb;
        try { totalMb = (long)(GC.GetGCMemoryInfo().TotalAvailableMemoryBytes / (1024 * 1024)); }
        catch { totalMb = 8192; }
        if (totalMb <= 0) totalMb = 8192;

        // 系统自己要用，堆不能顶到物理内存
        var ceiling = Math.Max(2048, totalMb - 2048);
        if (limits.MaxMb > 0) ceiling = Math.Min(ceiling, limits.MaxMb);
        var floor = Math.Max(2048, (long)limits.MinMb);
        if (floor > ceiling) floor = ceiling;

        if (MaxMemoryMb > 0) return (int)Math.Clamp(MaxMemoryMb, floor, ceiling);

        var half = totalMb / 2;
        var value = Math.Clamp(half, 4096, 8192);
        var leaveForSystem = totalMb - 4096;
        if (leaveForSystem > 2048 && value > leaveForSystem) value = leaveForSystem;
        return (int)Math.Clamp(value, floor, ceiling);
    }
}
