using System.Numerics;
using System.Text.RegularExpressions;
using BatterMC.Protocol;

namespace BatterMC.Core;

/// <summary>
/// 客户端版本比较。真正的完整客户端安装由 Tauri updater 负责，
/// 保证 Tauri 主程序、.NET sidecar 和前端资源作为一个签名 NSIS 包同步更新。
/// </summary>
public static class SelfUpdater
{
    private static readonly Regex VersionPattern = new(
        @"^[vV]?(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?(?:\.(0|[1-9][0-9]*))?(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$",
        RegexOptions.CultureInvariant);

    /// <summary>SemVer 优先级：忽略 build metadata，正确比较预发布版本。</summary>
    public static bool IsNewer(string candidate, string current)
        => Compare(candidate, current) > 0;

    public static int Compare(string candidate, string current)
    {
        static (BigInteger[] Core, string[] Pre) Parse(string v)
        {
            var match = VersionPattern.Match(v);
            if (!match.Success) throw new FormatException($"客户端版本号无效：{v}");
            var core = Enumerable.Range(1, 3).Select(i =>
                match.Groups[i].Success ? BigInteger.Parse(match.Groups[i].Value) : BigInteger.Zero).ToArray();
            var pre = match.Groups[4].Success ? match.Groups[4].Value.Split('.') : Array.Empty<string>();
            if (pre.Any(p => p.All(char.IsAsciiDigit) && p.Length > 1 && p[0] == '0'))
                throw new FormatException($"预发布版本号不能包含前导零：{v}");
            return (core, pre);
        }

        var (a, aPre) = Parse(candidate);
        var (b, bPre) = Parse(current);
        for (var i = 0; i < 3; i++)
            if (a[i] != b[i]) return a[i].CompareTo(b[i]);
        if (aPre.Length == 0 || bPre.Length == 0)
            return (aPre.Length == 0 ? 1 : 0).CompareTo(bPre.Length == 0 ? 1 : 0);
        for (var i = 0; i < Math.Min(aPre.Length, bPre.Length); i++)
        {
            var aNumber = aPre[i].All(char.IsAsciiDigit);
            var bNumber = bPre[i].All(char.IsAsciiDigit);
            var an = aNumber ? BigInteger.Parse(aPre[i]) : BigInteger.Zero;
            var bn = bNumber ? BigInteger.Parse(bPre[i]) : BigInteger.Zero;
            var comparison = aNumber && bNumber ? an.CompareTo(bn)
                : aNumber != bNumber ? (aNumber ? -1 : 1)
                : string.Compare(aPre[i], bPre[i], StringComparison.Ordinal);
            if (comparison != 0) return comparison;
        }
        return aPre.Length.CompareTo(bPre.Length);
    }

    public static bool IsRequired(LauncherRelease release, string current)
        => IsNewer(release.Version, current) && (
            release.Mandatory ||
            (!string.IsNullOrWhiteSpace(release.MinSupportedVersion) && IsNewer(release.MinSupportedVersion, current)) ||
            release.BlockedVersions.Any(v => Compare(v, current) == 0));
}
