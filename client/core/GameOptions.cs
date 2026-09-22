using System.Text.Json;

namespace BatterMC.Core;

/// <summary>
/// options.txt 里那几个"启动器说了算"的键。
///
/// 全屏是个双向的东西：启动器勾了要全屏，游戏里按 F11 又能改回来，而 Minecraft
/// 把结果写回 options.txt。只靠命令行 <c>--fullscreen</c> 管不全 —— 那个参数只能
/// 打开全屏，关不掉：options.txt 里若是 true，游戏启动时会自己再切回全屏。
/// 所以启动前把这个键写成启动器里的值，启动器也从这个键读回游戏里的改动。
/// </summary>
public static class GameOptions
{
    public const string OptionsPath = "options.txt";
    private const string FullscreenKey = "fullscreen";

    /// <summary>读游戏当前的全屏设置。文件还不存在或没写过这个键时返回 null。</summary>
    public static bool? ReadFullscreen(LauncherPaths paths)
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

    /// <summary>把全屏设置写进 options.txt。只动这一个键，玩家其它设置原样保留。</summary>
    public static bool ApplyFullscreen(LauncherPaths paths, bool fullscreen)
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
        Log.Info($"全屏启动 -> {(fullscreen ? "开" : "关")}");
        return true;
    }

    // 裁剪后的单文件 exe 不能用反射版序列化，走 JsonDocument
    private static JsonElement Literal(string json)
    {
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement.Clone();
    }
}
