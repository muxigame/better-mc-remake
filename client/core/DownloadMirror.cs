namespace BatterMC.Core;

/// <summary>
/// 把 Minecraft 本体的下载重定向到我们自己的 OSS。
///
/// 实测 Mojang 和 NeoForge 的源在国内又慢又不稳：光资源对象就有 3911 个、787 MB，
/// 而且一部分玩家根本连不上，卡在这一步进不了游戏。所以全部走我们的镜像。
///
/// 映射规则是 {镜像根}/{主机名}{路径}，例如
///   https://resources.download.minecraft.net/ab/abcdef…
///   → {镜像根}/resources.download.minecraft.net/ab/abcdef…
/// 一眼能看出某个对象对应上游的哪个文件，补内容和排查都不用查表。
///
/// 镜像上没有的对象会自动回落到上游，所以镜像可以一点一点补，
/// 补到一半也不会让任何人装不上。
/// </summary>
public sealed class DownloadMirror
{
    /// <summary>
    /// 只镜像这几个上游主机。
    ///
    /// 必须是白名单而不是"除了我们自己的域名都镜像"：整合包文件本来就放在我们的
    /// OSS 上，再套一层就会拼出一个不存在的地址。
    ///
    /// Java 运行时不在这里——Adoptium 是带查询串的 API，按路径镜像会撞车。
    /// 它走清单里的 java.url，那条路本来就支持指向任意地址。
    /// </summary>
    private static readonly HashSet<string> Upstream = new(StringComparer.OrdinalIgnoreCase)
    {
        "resources.download.minecraft.net",
        "libraries.minecraft.net",
        "piston-data.mojang.com",
        "piston-meta.mojang.com",
        "launchermeta.mojang.com",
        "launcher.mojang.com",
        "maven.neoforged.net",
    };

    private readonly string _base;

    public DownloadMirror(string baseUrl) => _base = baseUrl.TrimEnd('/');

    public string Base => _base;

    /// <summary>返回镜像地址；这个地址不该走镜像时返回 null。</summary>
    public string? Rewrite(string url)
    {
        if (string.IsNullOrWhiteSpace(url)) return null;
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri)) return null;
        if (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps) return null;
        if (!Upstream.Contains(uri.Host)) return null;
        // 带查询串的地址按路径镜像会撞车，宁可不碰
        if (!string.IsNullOrEmpty(uri.Query)) return null;
        // OSS 把路径里的 + 当成空格解码。Maven 上真有带 + 的构件
        // （sponge-mixin-0.15.2+mixin.0.8.7.jar），不转义就是 404、回落上游。
        return $"{_base}/{uri.Host}{uri.AbsolutePath.Replace("+", "%2B")}";
    }

    /// <summary>给镜像补内容的工具用：列出所有需要镜像的上游主机。</summary>
    public static IReadOnlyCollection<string> MirroredHosts => Upstream;
}
