namespace BatterMC.Core;

/// <summary>
/// 客户端版本比较。真正的完整客户端安装由 Tauri updater 负责，
/// 保证 Tauri 主程序、.NET sidecar 和前端资源作为一个签名 NSIS 包同步更新。
/// </summary>
public static class SelfUpdater
{
    /// <summary>语义化版本比较，逐段按数字比。</summary>
    public static bool IsNewer(string candidate, string current)
    {
        static int[] Parse(string v) =>
            v.Split('.', '-', '+')
             .Select(p => int.TryParse(p, out var n) ? n : 0)
             .ToArray();

        var a = Parse(candidate);
        var b = Parse(current);
        for (var i = 0; i < Math.Max(a.Length, b.Length); i++)
        {
            var x = i < a.Length ? a[i] : 0;
            var y = i < b.Length ? b[i] : 0;
            if (x != y) return x > y;
        }
        return false;
    }
}
