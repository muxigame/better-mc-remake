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

    /// <summary>只在本地不存在时投放一次。之后玩家随便改，启动器永不覆盖。options.txt / 键位 / 个人偏好用这个。</summary>
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
