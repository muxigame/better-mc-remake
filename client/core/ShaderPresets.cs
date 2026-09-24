using System.Text.Json;

namespace BatterMC.Core;

/// <summary>启动器看到的光影预设：包名 + 是否启用。空包名表示关闭光影。</summary>
public readonly record struct ShaderSelection(bool Enabled, string Pack)
{
    public static readonly ShaderSelection Off = new(false, "");

    /// <summary>界面和设置里统一用一个字符串表示：空串 = 无光影。</summary>
    public string AsSettingValue() => Enabled && Pack.Length > 0 ? Pack : "";
}

/// <summary>
/// 光影预设的唯一真相是 <c>config/iris.properties</c> —— 玩家在游戏里换光影，改的就是它。
/// 启动器读它来显示当前光影，写它来落地启动预设，不另存一份会和游戏打架的副本。
///
/// settings.json 里那份 <see cref="LauncherSettings.ShaderPack"/> 只是"玩家点名要的预设"：
/// iris.properties 是 Seed 文件，服务端发新版本时会被整份重投，玩家的选择会被冲掉，
/// 所以每次启动同步完之后要按这份记录再写一次。
/// </summary>
public static class ShaderPresets
{
    public const string ConfigPath = "config/iris.properties";
    private const string ShaderPackKey = "shaderPack";
    private const string EnableKey = "enableShaders";

    /// <summary>新装默认不开光影：这包 400 多个模组本来就重，着色器编译还要再等一轮。</summary>
    public const string DefaultPack = "";

    /// <summary>
    /// 玩家在游戏里自己换/关了光影，就以他的选择为准。
    ///
    /// 必须在同步整合包**之前**问这个问题：iris.properties 是 Seed 文件，
    /// 同步可能把它整份重投成出厂值（出厂值是开着光影的），重投之后再读，
    /// 就分不清「玩家自己关的」和「刚被重投成开的」了。
    ///
    /// 启动器 UI 里换光影走的是另一条路：那边设置和文件是一起写的，
    /// 所以这里读到的仍然等于记住的值，不会把 UI 的选择顶掉。
    /// </summary>
    /// <returns>需要改记录时返回玩家当前实际用的那个，否则返回 null。</returns>
    public static string? AdoptPlayerChoice(string? remembered, ShaderSelection onDisk)
    {
        var actual = onDisk.AsSettingValue();
        return string.Equals(actual, remembered, StringComparison.Ordinal) ? null : actual;
    }

    /// <summary>读出游戏当前实际用的光影。文件不存在或没写过就是关闭。</summary>
    public static ShaderSelection Read(LauncherPaths paths)
    {
        var file = paths.ResolveGameFile(ConfigPath);
        if (!File.Exists(file)) return ShaderSelection.Off;
        try { return Parse(File.ReadAllText(file)); }
        catch (Exception ex)
        {
            Log.Warn($"读取光影配置失败：{ex.Message}");
            return ShaderSelection.Off;
        }
    }

    public static ShaderSelection Parse(string content)
    {
        var enabled = false;
        var pack = "";
        foreach (var raw in content.Split('\n'))
        {
            var line = raw.Trim();
            if (line.Length == 0 || line[0] is '#' or '!') continue;
            var idx = line.IndexOf('=');
            if (idx <= 0) continue;
            var key = line[..idx].Trim();
            var value = line[(idx + 1)..].Trim();
            if (key.Equals(EnableKey, StringComparison.OrdinalIgnoreCase))
                enabled = value.Equals("true", StringComparison.OrdinalIgnoreCase);
            else if (key.Equals(ShaderPackKey, StringComparison.OrdinalIgnoreCase))
                pack = value;
        }
        return enabled && pack.Length > 0 ? new ShaderSelection(true, pack) : ShaderSelection.Off;
    }

    /// <summary>
    /// 把预设写进 iris.properties。只动那两个键，其它设置（阴影距离、色彩空间等）原样保留。
    /// pack 为空串 = 关闭光影，这时保留 shaderPack 的原值，玩家再打开就还是上次那个。
    /// </summary>
    public static bool Apply(LauncherPaths paths, string? pack)
    {
        var file = paths.ResolveGameFile(ConfigPath);
        if (!File.Exists(file))
        {
            Log.Info($"光影配置还不存在，跳过写入（等游戏自己生成）：{ConfigPath}");
            return false;
        }

        var wanted = (pack ?? "").Trim();
        var enforce = new Dictionary<string, JsonElement>(StringComparer.Ordinal)
        {
            [EnableKey] = Json(wanted.Length > 0),
        };
        if (wanted.Length > 0) enforce[ShaderPackKey] = Json(wanted);

        var original = File.ReadAllText(file);
        var updated = ConfigOverlay.ApplyProperties(original, enforce, out _);
        if (string.Equals(original, updated, StringComparison.Ordinal)) return false;

        AtomicFile.WriteAllText(file, updated);
        Log.Info($"光影预设 -> {(wanted.Length > 0 ? wanted : "无光影")}");
        return true;
    }

    /// <summary>列出游戏目录里可选的光影包。Iris 把每个包的设置存成同名 .txt，不算包本身。</summary>
    public static List<string> Available(LauncherPaths paths)
    {
        var dir = Path.Combine(paths.GameDir, "shaderpacks");
        var found = new List<string>();
        if (!Directory.Exists(dir)) return found;
        try
        {
            foreach (var entry in Directory.EnumerateFileSystemEntries(dir))
            {
                var name = Path.GetFileName(entry);
                if (Directory.Exists(entry)) found.Add(name);
                else if (name.EndsWith(".zip", StringComparison.OrdinalIgnoreCase)) found.Add(name);
            }
        }
        catch (Exception ex) { Log.Warn($"列出光影包失败：{ex.Message}"); }
        found.Sort(StringComparer.CurrentCultureIgnoreCase);
        return found;
    }

    // 走 JsonDocument.Parse 而不是 JsonSerializer.SerializeToElement：
    // sidecar 是裁剪过的单文件 exe，反射版序列化会报 IL2026，运行时可能找不到类型。
    private static JsonElement Json(bool value) => Literal(value ? "true" : "false");

    private static JsonElement Json(string value) =>
        Literal("\"" + JsonEncodedText.Encode(value) + "\"");

    private static JsonElement Literal(string json)
    {
        using var doc = JsonDocument.Parse(json);
        return doc.RootElement.Clone();
    }
}
