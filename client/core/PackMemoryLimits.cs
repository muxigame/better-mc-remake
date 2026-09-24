using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace BatterMC.Core;

/// <summary>整合包自己声明的内存区间，单位 MB。0 表示没读到。</summary>
public readonly record struct PackMemoryLimits(int MinMb, int MaxMb)
{
    public static readonly PackMemoryLimits Unknown = new(0, 0);

    /// <summary>
    /// 读 config/memorysettings.json —— memorysettings 模组就是靠它判断要不要弹
    /// "More memory allocated than recommended for this pack" 那个警告屏的。
    /// 下限（minimumClient）仍当堆的下限用；上限（maximumClient）不再限制堆，
    /// 反过来由启动器按这次定下的堆写回去，见 <see cref="WriteMaximum"/>。
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

    /// <summary>
    /// 把 maximumClient 改成这次实际给的堆。以前是反过来读它当上限，整合包写的 10000 MB
    /// 把大内存机器也卡死了；现在堆按物理内存定，这个数跟着写，模组永远不会觉得"分多了"。
    /// 文件不存在不建，数值没变不写，其余键原样保留。
    /// </summary>
    public static void WriteMaximum(LauncherPaths paths, int heapMb)
    {
        var file = paths.ResolveGameFile("config/memorysettings.json");
        if (!File.Exists(file)) return;
        try
        {
            if (JsonNode.Parse(File.ReadAllText(file)) is not JsonObject root
                || root["maximumClient"] is not JsonObject group) return;
            if (group["maximumClient"] is JsonValue current
                && current.TryGetValue<int>(out var mb) && mb == heapMb) return;
            group["maximumClient"] = heapMb;
            // 不转义非 ASCII，缩进两格，和整合包里的原文件一个样子；不带 BOM
            File.WriteAllText(file, root.ToJsonString(new JsonSerializerOptions
            {
                WriteIndented = true,
                Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
            }));
        }
        catch (Exception ex)
        {
            Log.Warn($"写整合包内存上限失败：{ex.Message}");
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
