using System.Text.Json;
using System.Text.RegularExpressions;

namespace BatterMC.Core;

public sealed class LibraryEntry
{
    public required string Name { get; init; }
    public required string Path { get; init; }
    public required string Url { get; init; }
    public string Sha1 { get; init; } = "";
    public long Size { get; init; }
    /// <summary>group:artifact:classifier —— 用来去重。</summary>
    public required string DedupKey { get; init; }
}

public sealed class AssetIndexRef
{
    public required string Id { get; init; }
    public required string Url { get; init; }
    public string Sha1 { get; init; } = "";
    public long Size { get; init; }
}

/// <summary>
/// 解析 versions\&lt;id&gt;\&lt;id&gt;.json。
/// 我们用的是自包含版本（inheritsFrom 已经展开、144 个库全部合并好），
/// 所以不需要再去拉 vanilla 的 json。
/// </summary>
public sealed partial class VersionJson
{
    public required string Id { get; init; }
    public required string MainClass { get; init; }
    public required AssetIndexRef AssetIndex { get; init; }
    public required List<LibraryEntry> Libraries { get; init; }
    public required List<object> JvmArgs { get; init; }
    public required List<object> GameArgs { get; init; }
    public string? ClientJarUrl { get; init; }
    public string ClientJarSha1 { get; init; } = "";
    public long ClientJarSize { get; init; }
    public string VersionType { get; init; } = "release";

    /// <summary>
    /// 从 --fml.* 参数里读出来的加载器坐标。
    /// NeoForge 的 neoforge-*-universal.jar / -client.jar 和 Minecraft 的
    /// client-*-extra.jar 并不在 libraries 列表里 —— FML 靠 -DlibraryDirectory
    /// 加上这几个版本号自己拼路径去找。所以要判断「装没装」就得先拿到它们。
    /// </summary>
    public string? NeoForgeVersion { get; init; }
    public string? McVersion { get; init; }
    public string? NeoFormVersion { get; init; }

    public static VersionJson Load(string path)
    {
        using var doc = JsonDocument.Parse(File.ReadAllText(path));
        var root = doc.RootElement;

        if (root.TryGetProperty("inheritsFrom", out var inh) && inh.ValueKind == JsonValueKind.String)
            throw new InvalidOperationException(
                $"版本 JSON 还带着 inheritsFrom=\"{inh.GetString()}\"，需要的是已经合并好的自包含版本。");

        var libs = new List<LibraryEntry>();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        if (root.TryGetProperty("libraries", out var libArray))
        {
            foreach (var lib in libArray.EnumerateArray())
            {
                if (!lib.TryGetProperty("name", out var nameEl)) continue;
                var name = nameEl.GetString()!;

                if (lib.TryGetProperty("rules", out var rules) && !RulesAllow(rules, Features.None))
                    continue;

                if (!lib.TryGetProperty("downloads", out var dl) ||
                    !dl.TryGetProperty("artifact", out var art)) continue;

                var parts = name.Split(':');
                var dedup = parts.Length >= 2
                    ? parts[0] + ":" + parts[1] + (parts.Length > 3 ? ":" + parts[3] : "")
                    : name;

                // 合并出来的清单里 NeoForge 的条目在前，vanilla 的在后，
                // 首次出现优先即可（本包 11 组重复项版本号完全一致，怎么选都一样）。
                if (!seen.Add(dedup)) continue;

                libs.Add(new LibraryEntry
                {
                    Name = name,
                    Path = art.GetProperty("path").GetString()!,
                    Url = art.TryGetProperty("url", out var u) ? u.GetString() ?? "" : "",
                    Sha1 = art.TryGetProperty("sha1", out var s) ? s.GetString() ?? "" : "",
                    Size = art.TryGetProperty("size", out var sz) ? sz.GetInt64() : 0,
                    DedupKey = dedup,
                });
            }
        }

        var ai = root.GetProperty("assetIndex");
        var assetIndex = new AssetIndexRef
        {
            Id = ai.GetProperty("id").GetString()!,
            Url = ai.GetProperty("url").GetString()!,
            Sha1 = ai.TryGetProperty("sha1", out var ash) ? ash.GetString() ?? "" : "",
            Size = ai.TryGetProperty("size", out var asz) ? asz.GetInt64() : 0,
        };

        string? clientUrl = null; string clientSha = ""; long clientSize = 0;
        if (root.TryGetProperty("downloads", out var dls) && dls.TryGetProperty("client", out var cl))
        {
            clientUrl = cl.TryGetProperty("url", out var cu) ? cu.GetString() : null;
            clientSha = cl.TryGetProperty("sha1", out var cs) ? cs.GetString() ?? "" : "";
            clientSize = cl.TryGetProperty("size", out var csz) ? csz.GetInt64() : 0;
        }

        var args = root.TryGetProperty("arguments", out var a) ? a : default;
        var gameArgs = ReadArgList(args, "game");

        string? FmlArg(string name)
        {
            for (var i = 0; i < gameArgs.Count - 1; i++)
            {
                if (gameArgs[i] is JsonElement key && key.ValueKind == JsonValueKind.String
                    && key.GetString() == name
                    && gameArgs[i + 1] is JsonElement value && value.ValueKind == JsonValueKind.String)
                    return value.GetString();
            }
            return null;
        }

        return new VersionJson
        {
            NeoForgeVersion = FmlArg("--fml.neoForgeVersion") ?? FmlArg("--fml.forgeVersion"),
            McVersion = FmlArg("--fml.mcVersion"),
            NeoFormVersion = FmlArg("--fml.neoFormVersion"),
            Id = root.GetProperty("id").GetString()!,
            MainClass = root.GetProperty("mainClass").GetString()!,
            AssetIndex = assetIndex,
            Libraries = libs,
            JvmArgs = ReadArgList(args, "jvm"),
            GameArgs = gameArgs,
            ClientJarUrl = clientUrl,
            ClientJarSha1 = clientSha,
            ClientJarSize = clientSize,
            VersionType = root.TryGetProperty("type", out var t) ? t.GetString() ?? "release" : "release",
        };
    }

    /// <summary>参数数组原样留着（字符串或带 rules 的对象），展开时再按 feature 过滤。</summary>
    private static List<object> ReadArgList(JsonElement args, string key)
    {
        var result = new List<object>();
        if (args.ValueKind != JsonValueKind.Object || !args.TryGetProperty(key, out var arr)) return result;
        foreach (var el in arr.EnumerateArray()) result.Add(el.Clone());
        return result;
    }

    // ------------------------------------------------------------------ rules

    public readonly record struct Features(bool CustomResolution, bool QuickPlayMultiplayer, bool Demo)
    {
        public static Features None => new(false, false, false);

        public bool Get(string name) => name switch
        {
            "has_custom_resolution" => CustomResolution,
            "is_quick_play_multiplayer" => QuickPlayMultiplayer,
            "has_quick_plays_support" => QuickPlayMultiplayer,
            "is_demo_user" => Demo,
            _ => false,
        };
    }

    [GeneratedRegex(@"^10\.")]
    private static partial Regex Win10Regex();

    /// <summary>
    /// 标准 Mojang 规则求值：有 rules 就默认拒绝，逐条匹配，最后命中的那条说了算。
    /// 例如 -Xss1M 带 {"os":{"arch":"x86"}}，我们是 x64，就不能加上。
    /// </summary>
    public static bool RulesAllow(JsonElement rules, Features features)
    {
        if (rules.ValueKind != JsonValueKind.Array) return true;
        var allowed = false;
        var any = false;

        foreach (var rule in rules.EnumerateArray())
        {
            any = true;
            if (!RuleMatches(rule, features)) continue;
            var action = rule.TryGetProperty("action", out var a) ? a.GetString() : "allow";
            allowed = action == "allow";
        }
        return any ? allowed : true;
    }

    private static bool RuleMatches(JsonElement rule, Features features)
    {
        if (rule.TryGetProperty("os", out var os))
        {
            if (os.TryGetProperty("name", out var n))
            {
                var want = n.GetString();
                if (!string.Equals(want, "windows", StringComparison.OrdinalIgnoreCase)) return false;
            }
            if (os.TryGetProperty("arch", out var arch))
            {
                // 清单里出现的 arch 只有 "x86"（32 位）。我们只发 x64。
                var want = arch.GetString();
                var actual = Environment.Is64BitOperatingSystem ? "x64" : "x86";
                if (!string.Equals(want, actual, StringComparison.OrdinalIgnoreCase)) return false;
            }
            if (os.TryGetProperty("version", out var ver))
            {
                var pattern = ver.GetString();
                if (!string.IsNullOrEmpty(pattern))
                {
                    try
                    {
                        if (!Regex.IsMatch(Environment.OSVersion.Version.ToString(), pattern,
                                RegexOptions.None, TimeSpan.FromSeconds(1)))
                            return false;
                    }
                    catch { /* 正则有问题就不当作拒绝条件 */ }
                }
            }
        }

        if (rule.TryGetProperty("features", out var feats) && feats.ValueKind == JsonValueKind.Object)
        {
            foreach (var f in feats.EnumerateObject())
            {
                var required = f.Value.ValueKind == JsonValueKind.True;
                if (features.Get(f.Name) != required) return false;
            }
        }

        return true;
    }

    /// <summary>把参数数组展开成实际命令行，替换 ${...} 占位符。</summary>
    public static List<string> Expand(
        IEnumerable<object> args, Features features, IReadOnlyDictionary<string, string> vars)
    {
        var result = new List<string>();

        void AddValue(JsonElement value)
        {
            if (value.ValueKind == JsonValueKind.String)
                result.Add(Substitute(value.GetString() ?? "", vars));
            else if (value.ValueKind == JsonValueKind.Array)
                foreach (var v in value.EnumerateArray())
                    result.Add(Substitute(v.GetString() ?? "", vars));
        }

        foreach (var raw in args)
        {
            if (raw is not JsonElement el) continue;

            if (el.ValueKind == JsonValueKind.String) { AddValue(el); continue; }
            if (el.ValueKind != JsonValueKind.Object) continue;

            if (el.TryGetProperty("rules", out var rules) && !RulesAllow(rules, features)) continue;
            if (el.TryGetProperty("value", out var value)) AddValue(value);
        }

        return result;
    }

    public static string Substitute(string input, IReadOnlyDictionary<string, string> vars)
    {
        if (!input.Contains("${", StringComparison.Ordinal)) return input;
        foreach (var (k, v) in vars)
            input = input.Replace("${" + k + "}", v, StringComparison.Ordinal);
        return input;
    }
}
