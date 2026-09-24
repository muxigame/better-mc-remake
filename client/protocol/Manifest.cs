using System.Text.Json;
using System.Text.Json.Serialization;

namespace BatterMC.Protocol;

/// <summary>
/// 同步策略：决定启动器如何对待整合包里的某一个文件。
/// </summary>
[JsonConverter(typeof(JsonStringEnumConverter<FilePolicy>))]
public enum FilePolicy
{
    /// <summary>服务器说了算。每次启动校验哈希，不一致就覆盖；玩家删掉会补回来。mods / 核心 config 用这个。</summary>
    Managed = 0,

    /// <summary>
    /// 每个服务端内容修订投放一次。首次安装会下载；玩家之后可自由修改或删除；
    /// 当 manifest 中该文件的 SHA-1 变化时，再强制同步一次新修订，然后重新交还给玩家。
    /// config / shaderpacks / options.txt 等“默认内容但允许玩家修改”的文件用这个。
    /// </summary>
    Seed = 1,

    /// <summary>服务器提供但默认不装。玩家可在设置里勾选。额外光影 / 可选资源包用这个。</summary>
    Optional = 2,
}

/// <summary>硬配置覆盖所支持的文件格式。</summary>
[JsonConverter(typeof(JsonStringEnumConverter<OverlayFormat>))]
public enum OverlayFormat
{
    /// <summary>key=value 逐行文本。options.txt、iris.properties、*.properties。</summary>
    Properties = 0,
    /// <summary>JSON / JSON5（注释会被保留的尽力而为处理见实现）。</summary>
    Json = 1,
    /// <summary>TOML。NeoForge 模组配置几乎都是这个，按行做外科手术式替换以保留注释。</summary>
    Toml = 2,
}

public sealed class PackInfo
{
    /// <summary>稳定的包标识，用于本地目录与缓存键。</summary>
    public string Id { get; set; } = "battermc5remake";
    /// <summary>展示名。</summary>
    public string Name { get; set; } = "BatterMC5Remake";
    /// <summary>整合包版本，例如 "1.3"。与本地 state.json 比对决定是否需要同步。</summary>
    public string Version { get; set; } = "0.0";
    public DateTimeOffset Released { get; set; } = DateTimeOffset.UtcNow;
    /// <summary>更新说明，启动器首页会显示。</summary>
    public string? Changelog { get; set; }
}

public sealed class MinecraftInfo
{
    public string Version { get; set; } = "1.21.1";
    public string Loader { get; set; } = "neoforge";
    public string LoaderVersion { get; set; } = "21.1.250";
    /// <summary>版本 JSON 在包内的相对路径（相对于游戏目录）。</summary>
    public string VersionJson { get; set; } = "versions/BatterMC5Remake/BatterMC5Remake.json";
    /// <summary>版本 id，即 versions 下的文件夹名。</summary>
    public string VersionId { get; set; } = "BatterMC5Remake";

    /// <summary>
    /// NeoForge 安装器的下载地址。留空则用官方 maven。
    /// 想让玩家不依赖 maven.neoforged.net 就把安装器托管到自己的服务器上。
    /// </summary>
    public string? InstallerUrl { get; set; }
}

public sealed class JavaRequirement
{
    /// <summary>需要的主版本号。1.21.1 = 21。</summary>
    public int Major { get; set; } = 21;
    /// <summary>可接受的最低完整版本。</summary>
    public string Min { get; set; } = "21.0.0";
    /// <summary>自动下载时抓取 jre 还是 jdk。</summary>
    public string Image { get; set; } = "jre";
    /// <summary>Adoptium API 的发行方标识。</summary>
    public string Distribution { get; set; } = "temurin";

    /// <summary>
    /// 自建的 JRE 压缩包地址。填了就优先用它，不走 Adoptium。
    /// 国内玩家直连 Adoptium 经常超时，把 zip 放到自己的更新服务器上最稳。
    /// </summary>
    public string? Url { get; set; }
    /// <summary>自建 zip 的 SHA-256，用于校验。</summary>
    public string? Sha256 { get; set; }
    public long Size { get; set; }
}

public sealed class ServerEntry
{
    public string Name { get; set; } = "";
    public string Host { get; set; } = "";
    public int Port { get; set; } = 25565;
    /// <summary>主服务器：「开始游戏」按钮直连的目标。</summary>
    public bool Primary { get; set; }
    /// <summary>强制写入 servers.dat，玩家在游戏内删掉也会被写回。</summary>
    public bool Forced { get; set; } = true;

    /// <summary>
    /// 同一台 Minecraft 服务器的可选入口。客户端会并行探测全部入口并把游戏接到
    /// 最优的一个；为空时自动把 Host/Port 当作兼容入口。
    /// </summary>
    public List<RouteCandidate> Routes { get; set; } = new();
}

/// <summary>候选线路类型。数值顺序也是默认偏好顺序。</summary>
[JsonConverter(typeof(JsonStringEnumConverter<RouteKind>))]
public enum RouteKind
{
    Auto = 0,
    LanDirect = 10,
    Ipv6Direct = 20,
    Ipv4Direct = 30,
    PortMapped = 40,
    UdpTunnel = 50,
    TcpTunnel = 60,
    Relay = 70,
}

/// <summary>
/// 由控制面或静态清单下发的一个可连接入口。本版数据面支持 TCP；UdpTunnel/QUIC
/// 类型预留给后续加密隧道 sidecar，不能被当前 TCP 代理误当成裸 MC 端口。
/// </summary>
public sealed class RouteCandidate
{
    public string Id { get; set; } = "";
    public string? Label { get; set; }
    public RouteKind Kind { get; set; } = RouteKind.Auto;
    public string Transport { get; set; } = "tcp";
    public string Host { get; set; } = "";
    public int Port { get; set; } = 25565;
    /// <summary>同类型内的人工优先级，越小越优先。</summary>
    public int Priority { get; set; }
    /// <summary>单次 TCP 握手探测超时；0 使用客户端默认值。</summary>
    public int ProbeTimeoutMs { get; set; }
}

/// <summary>启动器自更新信息。</summary>
public sealed class LauncherRelease
{
    public string Version { get; set; } = "0.0.0";
    /// <summary>新版 exe 的下载地址（可为相对于服务器根的路径）。</summary>
    public string Url { get; set; } = "";
    public string Sha256 { get; set; } = "";
    public long Size { get; set; }
    public string? Notes { get; set; }
    /// <summary>为 true 时低于该版本的启动器拒绝继续，必须更新。</summary>
    public bool Mandatory { get; set; }
    /// <summary>低于此版本必须更新；空值表示不按版本门槛停用旧版。</summary>
    public string? MinSupportedVersion { get; set; }
    /// <summary>单独停用的缺陷版本；不影响其它仍受支持的旧版本。</summary>
    public List<string> BlockedVersions { get; set; } = new();
    public string? UpdateReason { get; set; }
}

/// <summary>
/// 更新控制面返回值。客户端永远只请求 muxigame 域名获取这个对象；
/// 真正的 manifest 与文件存储地址可以随时由服务端切换到 OSS/COS/R2。
/// </summary>
public sealed class ManifestControl
{
    private static readonly ManifestJsonContext Context =
        new(new JsonSerializerOptions(PackManifest.JsonOptions));

    public string ManifestUrl { get; set; } = "";
    public string FilesBaseUrl { get; set; } = "";

    /// <summary>
    /// Minecraft 本体（资源对象、运行库、客户端 jar）的镜像根地址。
    /// 为空表示直连 Mojang/NeoForge。服务端可以随时切换或关掉，不用发客户端。
    /// </summary>
    public string? MirrorBaseUrl { get; set; }
    public LauncherRelease? Launcher { get; set; }

    public static ManifestControl? FromJson(string json) =>
        JsonSerializer.Deserialize(json, Context.ManifestControl);
}

public sealed class ManagedFile
{
    /// <summary>相对于游戏目录的正斜杠路径，例如 "mods/create-1.21.1.jar"。</summary>
    public string Path { get; set; } = "";
    public long Size { get; set; }
    /// <summary>小写十六进制 SHA-1。</summary>
    public string Sha1 { get; set; } = "";
    public FilePolicy Policy { get; set; } = FilePolicy.Managed;
    /// <summary>自定义下载地址；为空时由客户端拼 {baseUrl}/files/{Path}。</summary>
    public string? Url { get; set; }
    /// <summary>Optional 文件在界面上的分组标签，例如 "光影"。</summary>
    public string? Group { get; set; }
    /// <summary>给玩家看的说明。</summary>
    public string? Label { get; set; }
    /// <summary>
    /// 默认开启的可选项。清单里这种文件的 <see cref="Policy"/> 仍写 Managed：
    /// 1.1.34 及更早的启动器不认这个字段，照旧当必装；它们要是看到 Optional，
    /// 会把没勾选的文件改名成 .disabled，整合包一发全员被关掉。
    /// </summary>
    public bool OptionalDefaultOn { get; set; }

    /// <summary>玩家能在「可选内容」里开关的文件。</summary>
    [JsonIgnore]
    public bool IsOptional => Policy == FilePolicy.Optional || OptionalDefaultOn;
}

/// <summary>
/// 键级硬配置。服务器点名的键每次启动强制写入，没点名的键原样保留玩家的值。
/// </summary>
public sealed class ConfigOverlaySpec
{
    /// <summary>相对于游戏目录的路径。</summary>
    public string Path { get; set; } = "";
    public OverlayFormat Format { get; set; } = OverlayFormat.Properties;
    /// <summary>
    /// 要强制的键 → 值。键用 '/' 分隔层级：
    /// JSON 的 "quality/fog_quality"，TOML 的 "client/fogDistance"（最后一段是键名，前面是表名）。
    /// Properties 直接用键名本身。
    /// </summary>
    public Dictionary<string, JsonElement> Enforce { get; set; } = new();
    /// <summary>
    /// 一次性下发的键 → 值：服务器点名一个新值就写一次，之后玩家改成什么都不再纠正。
    ///
    /// 和 Enforce 的区别是「谁说了算」：Enforce 每次启动强制拉回服务器的值，
    /// 这个只负责把默认值推到位，主权仍归玩家。适合视场角这种
    /// 想改默认、又不该剥夺玩家选择权的设置。
    /// 记账在 state.json 的 overlaySeeds 里，键是「路径#键名」。
    /// </summary>
    public Dictionary<string, JsonElement> SeedKeys { get; set; } = new();

    /// <summary>
    /// 列表型键里要摘掉的条目：键名 → 要移除的元素。只对 Properties 生效，
    /// 用于 options.txt 这种「值本身是一个 JSON 数组」的键（resourcePacks）。
    /// 整键强制会把玩家自己选的资源包一起抹掉，所以只点名删指定条目。
    /// </summary>
    public Dictionary<string, List<string>> RemoveFromList { get; set; } = new();

    /// <summary>文件不存在时是否创建。properties/json 可以创建，toml 建议交给模组自己生成。</summary>
    public bool CreateIfMissing { get; set; } = true;
}

public sealed class PackManifest
{
    public int Schema { get; set; } = 1;
    public PackInfo Pack { get; set; } = new();
    public MinecraftInfo Minecraft { get; set; } = new();
    public JavaRequirement Java { get; set; } = new();
    public List<ServerEntry> Servers { get; set; } = new();
    public LauncherRelease? Launcher { get; set; }
    public List<ManagedFile> Files { get; set; } = new();

    /// <summary>
    /// 需要清理的目录（相对路径，不带尾斜杠）。这些目录下不在 Files 里的文件会被删除，
    /// 用来干掉玩家自己塞进来的模组。不在此列表内的目录一律不碰。
    /// </summary>
    public List<string> Prune { get; set; } = new();

    public List<ConfigOverlaySpec> Overlays { get; set; } = new();

    /// <summary>启动器首页公告。</summary>
    public string? Notice { get; set; }

    public static JsonSerializerOptions JsonOptions { get; } = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        WriteIndented = true,
        // 中文公告和更新日志直接写字符，不要变成一堆 \uXXXX
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    private static readonly ManifestJsonContext Context = new(JsonOptions);

    public string ToJson() => JsonSerializer.Serialize(this, Context.PackManifest);

    public static PackManifest? FromJson(string json) =>
        JsonSerializer.Deserialize(json, Context.PackManifest);
}
