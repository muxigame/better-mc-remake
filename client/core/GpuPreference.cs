using Microsoft.Win32;

namespace BatterMC.Core;

/// <summary>玩家在设置里选的显卡策略。数值就是 Windows 那边的取值，别改。</summary>
public enum GpuChoice
{
    /// <summary>交给 Windows 决定。双显卡笔记本上它经常挑集成显卡。</summary>
    Auto = 0,
    /// <summary>节能，也就是集成显卡。</summary>
    PowerSaving = 1,
    /// <summary>高性能，也就是独立显卡。默认值。</summary>
    HighPerformance = 2,
}

/// <summary>一块显示适配器。</summary>
public sealed record GpuAdapter(string Name, long VramBytes, string Vendor)
{
    /// <summary>
    /// 像不像独立显卡。
    ///
    /// **这个判断是会影响结果的。** 界面上按名字选卡，而 Windows 那边只认"高性能"和
    /// "节能"两个档位，所以必须由我们决定哪块卡对应哪个档。判反了，玩家点了独显却被
    /// 写成节能档，正好是我们要修的那个毛病。
    ///
    /// 排序上它是第一关键字，显存是第二关键字——双显卡笔记本上核显的独占显存通常只有
    /// 一两百兆，独显是几个 G，两条判据会一致。
    /// </summary>
    public bool LooksDiscrete
    {
        get
        {
            var name = Name.ToLowerInvariant();
            // Intel 的独显只有 Arc 系列，其余都是核显
            if (Vendor.Contains("Intel", StringComparison.OrdinalIgnoreCase) || name.Contains("intel"))
                return name.Contains("arc");
            // AMD 的核显显存是从内存里切的，通常很小；独显自带大显存
            if (name.Contains("radeon(tm) graphics") || name.Contains("vega") && VramBytes < 2L * 1024 * 1024 * 1024)
                return false;
            return VramBytes >= 1024L * 1024 * 1024;
        }
    }

    public string VramText => VramBytes <= 0
        ? "显存未知"
        : $"{VramBytes / 1024.0 / 1024 / 1024:0.#} GB 显存";
}

/// <summary>
/// 显卡检测，以及"这个程序该用哪块显卡"的设置。
///
/// 双显卡笔记本上，Java 起来的游戏经常被 Windows 丢给集成显卡跑——玩家看到的是
/// 帧数怎么调都上不去，而不是任何报错。Windows 从 1803 起提供了按程序指定显卡的
/// 机制（设置 → 显示 → 图形），注册表键就是这里写的这个，系统自带的那个界面写的
/// 也是它。
///
/// 我们不自己判断哪块是独显：只告诉 Windows 要"高性能"，由它去解析。自己判断反而
/// 容易在 Arc、APU、外接显卡坞这些情况上翻车。
/// </summary>
public static class GpuPreference
{
    private const string PreferenceKey = @"Software\Microsoft\DirectX\UserGpuPreferences";

    /// <summary>显示适配器在注册表里的类 GUID。</summary>
    private const string AdapterClassKey =
        @"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}";

    /// <summary>
    /// 串流和远程桌面装的虚拟显示器。它们也算显示适配器，但对游戏没用，
    /// 列出来只会让玩家以为自己有两块卡。
    /// </summary>
    private static readonly string[] VirtualHints =
    [
        "virtual", "rayLink", "gameviewer", "parsec", "idd ", "mirror",
        "remote", "meta ", "citrix", "vmware", "basic display",
    ];

    /// <summary>
    /// 列出这台机器上真实的显示适配器。
    ///
    /// 走注册表而不是 WMI：WMI 的 <c>Win32_VideoController.AdapterRAM</c> 是 32 位有符号，
    /// 8 GB 的卡会报成 4095 MB，拿它判断独显必错。注册表里的
    /// <c>HardwareInformation.qwMemorySize</c> 是准的。
    /// </summary>
    public static List<GpuAdapter> Detect()
    {
        var found = new List<GpuAdapter>();
        try
        {
            using var baseKey = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64);
            using var root = baseKey.OpenSubKey(AdapterClassKey);
            if (root is null) return found;

            foreach (var name in root.GetSubKeyNames())
            {
                // 只有 0000、0001 这种四位编号是适配器，其余是 Configuration/Properties
                if (name.Length != 4 || !name.All(char.IsDigit)) continue;
                using var sub = root.OpenSubKey(name);
                if (sub?.GetValue("DriverDesc") is not string desc || string.IsNullOrWhiteSpace(desc)) continue;

                var vram = ReadVram(sub);
                var vendor = sub.GetValue("ProviderName") as string ?? "";

                // 虚拟适配器没有显存，加上名字判据双保险
                var haystack = (desc + " " + vendor).ToLowerInvariant();
                if (vram <= 0 && VirtualHints.Any(h => haystack.Contains(h, StringComparison.Ordinal))) continue;
                if (vram <= 0 && !haystack.Contains("intel") && !haystack.Contains("amd")
                    && !haystack.Contains("nvidia")) continue;

                if (found.Any(g => g.Name.Equals(desc, StringComparison.OrdinalIgnoreCase))) continue;
                found.Add(new GpuAdapter(desc.Trim(), vram, vendor.Trim()));
            }
        }
        catch (Exception ex)
        {
            Log.Warn($"读取显卡列表失败：{ex.Message}");
        }
        Rank(found);
        return found;
    }

    /// <summary>
    /// 把排在最前的那块定为"高性能"档。先按独显判据，再按独占显存兜底。
    ///
    /// 单独抽出来是因为这个顺序会真正决定往注册表里写哪个档位，必须能单测——
    /// 排反了玩家点独显会被写成节能档。
    /// </summary>
    public static void Rank(List<GpuAdapter> adapters) => adapters.Sort((a, b) =>
    {
        var byKind = b.LooksDiscrete.CompareTo(a.LooksDiscrete);
        return byKind != 0 ? byKind : b.VramBytes.CompareTo(a.VramBytes);
    });

    private static long ReadVram(RegistryKey key)
    {
        if (key.GetValue("HardwareInformation.qwMemorySize") is long q && q > 0) return q;
        // 老驱动只有 32 位那个，同样会在 4 GB 处截断，只能当个下限用
        var legacy = key.GetValue("HardwareInformation.MemorySize");
        return legacy switch
        {
            int i when i > 0 => i,
            byte[] b when b.Length >= 4 => BitConverter.ToUInt32(b, 0),
            _ => 0,
        };
    }

    /// <summary>
    /// 告诉 Windows 这个程序该用哪块显卡。
    ///
    /// 键名是可执行文件的完整路径，必须和真正启动的那个 exe 一字不差——我们用的是
    /// 自动下载的 JRE，它的路径会随版本变，所以每次启动前都重新写一遍。
    ///
    /// 只对之后启动的进程生效，所以这一步必须在 Process.Start 之前。
    /// </summary>
    public static bool Apply(string exePath, GpuChoice choice)
    {
        if (string.IsNullOrWhiteSpace(exePath)) return false;
        try
        {
            using var key = Registry.CurrentUser.CreateSubKey(PreferenceKey, writable: true);
            if (key is null) return false;
            if (choice == GpuChoice.Auto)
            {
                key.DeleteValue(exePath, throwOnMissingValue: false);
                return true;
            }
            key.SetValue(exePath, $"GpuPreference={(int)choice};", RegistryValueKind.String);
            return true;
        }
        catch (Exception ex)
        {
            // 写不进去不该挡着玩家进游戏，顶多是显卡还得他自己去系统设置里挑
            Log.Warn($"设置显卡偏好失败（不影响启动）：{ex.Message}");
            return false;
        }
    }

    /// <summary>读回当前生效的设置，用来验证真的写进去了。</summary>
    public static GpuChoice? Read(string exePath)
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(PreferenceKey);
            if (key?.GetValue(exePath) is not string raw) return null;
            var marker = raw.IndexOf("GpuPreference=", StringComparison.OrdinalIgnoreCase);
            if (marker < 0) return null;
            var digits = new string(raw[(marker + 14)..].TakeWhile(char.IsDigit).ToArray());
            return int.TryParse(digits, out var value) && Enum.IsDefined(typeof(GpuChoice), value)
                ? (GpuChoice)value
                : null;
        }
        catch { return null; }
    }

    /// <summary>
    /// 清掉我们之前给别的路径写过的偏好。
    ///
    /// JRE 升级会换目录，不清的话玩家注册表里会攒一堆指向已经删掉的 java.exe 的条目。
    /// 我们只动自己写过的那一条，别人的（显卡驱动面板、系统设置里手动加的）不碰。
    /// </summary>
    public static void Forget(string exePath)
    {
        if (string.IsNullOrWhiteSpace(exePath)) return;
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(PreferenceKey, writable: true);
            key?.DeleteValue(exePath, throwOnMissingValue: false);
        }
        catch { }
    }

    /// <summary>这台机器是不是双显卡。只有双显卡时"选哪块"才有意义。</summary>
    public static bool IsHybrid(IReadOnlyList<GpuAdapter> adapters) => adapters.Count >= 2;

    /// <summary>
    /// 某块卡对应 Windows 的哪个档位。
    ///
    /// Windows 只有"高性能"和"节能"两档，不能按名字点名某块卡，所以排最前的那块算
    /// 高性能，其余算节能。三块卡以上（核显 + 独显 + 外接显卡坞）时 Windows 本身也
    /// 只有这两档，没法再细分。
    /// </summary>
    public static GpuChoice ChoiceFor(IReadOnlyList<GpuAdapter> adapters, GpuAdapter adapter)
        => adapters.Count > 0 && ReferenceEquals(adapters[0], adapter)
            ? GpuChoice.HighPerformance
            : GpuChoice.PowerSaving;
}
