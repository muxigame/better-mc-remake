using System.Text.Json;

namespace BatterMC.Core;

/// <summary>整合包自己声明的内存区间，单位 MB。0 表示没读到。</summary>
public readonly record struct PackMemoryLimits(int MinMb, int MaxMb)
{
    public static readonly PackMemoryLimits Unknown = new(0, 0);

    /// <summary>
    /// 读 config/memorysettings.json —— memorysettings 模组就是靠它判断要不要弹
    /// "More memory allocated than recommended for this pack" 那个警告屏的。
    /// 启动器读同一份数值，就不会给出一个进游戏必挨骂的堆大小。
    /// </summary>
    public static PackMemoryLimits Read(LauncherPaths paths)
    {
        var file = paths.ResolveGameFile("config/memorysettings.json");
        if (!File.Exists(file)) return Unknown;
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(file));
            return new PackMemoryLimits(
                Nested(doc.RootElement, "minimumClient"),
                Nested(doc.RootElement, "maximumClient"));
        }
        catch (Exception ex)
        {
            Log.Warn($"读取整合包内存区间失败：{ex.Message}");
            return Unknown;
        }
    }

    // 这个文件的结构是 { "maximumClient": { "desc:": "...", "maximumClient": 10000 } }
    private static int Nested(JsonElement root, string key)
        => root.TryGetProperty(key, out var group)
            && group.ValueKind == JsonValueKind.Object
            && group.TryGetProperty(key, out var value)
            && value.TryGetInt32(out var mb)
            && mb > 0
            ? mb : 0;
}
