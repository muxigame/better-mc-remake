using System.Text.Json;
using System.Text.RegularExpressions;

namespace BatterMC.Core;

/// <summary>
/// options.txt 里那几个"启动器说了算"的键。
///
/// 全屏是个双向的东西：启动器勾了要全屏，游戏里按 F11 又能改回来，而 Minecraft
/// 把结果写回 options.txt。只靠命令行 <c>--fullscreen</c> 管不全 —— 那个参数只能
/// 打开全屏，关不掉：options.txt 里若是 true，游戏启动时会自己再切回全屏。
/// 所以启动前把这个键写成启动器里的值，启动器也从这个键读回游戏里的改动。
///
/// 整合包里的 Sodium Extras 另记着一份全屏状态（<see cref="SodiumExtrasPath"/> 里的
/// fullscreen = WINDOWED / BORDERLESS / FULLSCREEN），而且它接管了 F11：按下时不看窗口
/// 现在是什么样，只按自己记的状态切到"下一个"。两份对不上 —— 启动器开了全屏、它还记着
/// WINDOWED —— 进游戏后第一下 F11 就什么都不做。所以两份一起写。读回时哪个文件新听哪个：
/// F11 当场只写它那份，视频设置里的全屏开关只写 options.txt。
/// </summary>
public static class GameOptions
{
    public const string OptionsPath = "options.txt";
    public const string SodiumExtrasPath = "config/sodiumextras-client.toml";
    private const string FullscreenKey = "fullscreen";
    private const string SodiumExtrasTable = "embeddiumextras.general";
    private const string SodiumExtrasKey = "embeddiumextras/general/fullscreen";

    /// <summary>读游戏当前的全屏设置。两份都没有时返回 null。</summary>
    public static bool? ReadFullscreen(LauncherPaths paths)
    {
        var options = ReadOptionsFullscreen(paths);
        var mode = ReadSodiumExtrasMode(paths);
        bool? extras = mode is null ? null : mode != "WINDOWED";

        if (options is null) return extras;
        if (extras is null || extras == options) return options;
        var optionsTime = File.GetLastWriteTimeUtc(paths.ResolveGameFile(OptionsPath));
        var extrasTime = File.GetLastWriteTimeUtc(paths.ResolveGameFile(SodiumExtrasPath));
        return extrasTime > optionsTime ? extras : options;
    }

    /// <summary>
    /// 把全屏设置写进 options.txt 和 Sodium Extras 的配置。每份只动全屏这一个键，
    /// 玩家其它设置原样保留；文件还没生成的就不替它建。
    /// </summary>
    public static bool ApplyFullscreen(LauncherPaths paths, bool fullscreen)
    {
        var changed = ApplyOptions(paths, fullscreen);
        changed |= ApplySodiumExtras(paths, fullscreen);
        if (changed) Log.Info($"全屏启动 -> {(fullscreen ? "开" : "关")}");
        return changed;
    }

    private static bool ApplyOptions(LauncherPaths paths, bool fullscreen)
    {
        var file = paths.ResolveGameFile(OptionsPath);
        if (!File.Exists(file)) return false;

        var enforce = new Dictionary<string, JsonElement>(StringComparer.Ordinal)
        {
            [FullscreenKey] = Literal(fullscreen ? "true" : "false"),
        };

        var original = File.ReadAllText(file);
        var updated = ConfigOverlay.ApplyProperties(original, enforce, out _);
        if (string.Equals(original, updated, StringComparison.Ordinal)) return false;

        AtomicFile.WriteAllText(file, updated);
        return true;
    }

    private static bool ApplySodiumExtras(LauncherPaths paths, bool fullscreen)
    {
        var file = paths.ResolveGameFile(SodiumExtrasPath);
        if (!File.Exists(file)) return false;

        // 无边框也是全屏的一种，玩家自己选的就留着
        var current = ReadSodiumExtrasMode(paths);
        var desired = !fullscreen ? "WINDOWED" : current == "BORDERLESS" ? "BORDERLESS" : "FULLSCREEN";
        if (current == desired) return false;

        var enforce = new Dictionary<string, JsonElement>(StringComparer.Ordinal)
        {
            [SodiumExtrasKey] = Literal("\"" + desired + "\""),
        };

        var original = File.ReadAllText(file);
        var updated = ConfigOverlay.ApplyToml(original, enforce, out _);
        if (string.Equals(original, updated, StringComparison.Ordinal)) return false;

        AtomicFile.WriteAllText(file, updated);
        return true;
    }

    private static bool? ReadOptionsFullscreen(LauncherPaths paths)
    {
        var file = paths.ResolveGameFile(OptionsPath);
        if (!File.Exists(file)) return null;
        try
        {
            foreach (var raw in File.ReadAllLines(file))
            {
                var line = raw.Trim();
                var idx = line.IndexOf(':');
                if (idx <= 0) continue;
                if (!line[..idx].Trim().Equals(FullscreenKey, StringComparison.OrdinalIgnoreCase)) continue;
                var value = line[(idx + 1)..].Trim();
                return value.Equals("true", StringComparison.OrdinalIgnoreCase);
            }
        }
        catch (Exception ex) { Log.Warn($"读取 options.txt 失败：{ex.Message}"); }
        return null;
    }

    // 表头只认键名字符：多行数组里的 [ "a", "b" ] 同样是方括号开头结尾，不能当成表头
    private static readonly Regex TomlTable = new(@"^\[\s*([A-Za-z0-9_.\-]+)\s*\]$", RegexOptions.Compiled);
    private static readonly Regex TomlMode = new(@"^fullscreen\s*=\s*""(\w+)""", RegexOptions.Compiled);

    /// <summary>Sodium Extras 记着的模式，大写；文件或键不在、值不认识时返回 null。</summary>
    private static string? ReadSodiumExtrasMode(LauncherPaths paths)
    {
        var file = paths.ResolveGameFile(SodiumExtrasPath);
        if (!File.Exists(file)) return null;
        try
        {
            var table = "";
            foreach (var raw in File.ReadAllLines(file))
            {
                var line = raw.Trim();
                var header = TomlTable.Match(line);
                if (header.Success) { table = header.Groups[1].Value; continue; }
                if (table != SodiumExtrasTable) continue;
                var m = TomlMode.Match(line);
                if (!m.Success) continue;
                var mode = m.Groups[1].Value.ToUpperInvariant();
                return mode is "WINDOWED" or "BORDERLESS" or "FULLSCREEN" ? mode : null;
            }
        }
        catch (Exception ex) { Log.Warn($"读取 {SodiumExtrasPath} 失败：{ex.Message}"); }
        return null;
    }

    // 裁剪后的单文件 exe 不能用反射版序列化，走 JsonDocument
    private static JsonElement Literal(string json)
    {
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement.Clone();
    }
}
