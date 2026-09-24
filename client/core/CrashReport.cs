using System.IO.Compression;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Microsoft.Win32;

namespace BatterMC.Core;

/// <summary>一次异常退出。启动器在游戏退出时记下来，玩家点"上传日志"时再打包。</summary>
public sealed record GameCrash(int ExitCode, string? Hint, string? Summary, DateTimeOffset StartedAt, DateTimeOffset ExitedAt);

/// <summary>
/// 崩溃日志打包，替代整合包原来带的 Crash Assistant。
///
/// 它的问题：游戏一崩就弹自己的窗口，按钮带倒计时关不掉；日志传到 mclo.gs、元数据发给
/// 第三方；还夹着盗版提示和「模组被改过」提示（我们的整合包本来就会被启动器改，
/// 每个玩家都会被报成改过）。现在由启动器来做：游戏异常退出时问一句，玩家点了才传，
/// 传到我们自己的控制面、按账号存，玩家中心能看到。
///
/// 包里只放这次游戏的日志，每个文件留尾部、封顶，文件里的用户目录、计算机名、
/// 令牌之类先抹掉。电脑环境（系统、CPU、内存、显卡）单独成一个文件，玩家能关。
/// </summary>
public static class CrashReport
{
    private const long LatestLogCap = 8 * 1024 * 1024;
    private const long OutputLogCap = 2 * 1024 * 1024;
    private const long LauncherLogCap = 2 * 1024 * 1024;
    private const long CrashFileCap = 4 * 1024 * 1024;

    /// <summary>这次游戏运行期间生成的崩溃报告（crash-reports/crash-*.txt），没有返回 null。</summary>
    public static string? FindCrashReport(LauncherPaths paths, DateTimeOffset since)
        => Newest(Path.Combine(paths.GameDir, "crash-reports"), "crash-*.txt", since);

    /// <summary>这次运行的 JVM 崩溃文件（-XX:ErrorFile 指到了启动器的 crash 目录）。</summary>
    public static string? FindJvmCrash(LauncherPaths paths, DateTimeOffset since)
        => Newest(paths.CrashDir, "hs_err_*.log", since);

    private static string? Newest(string dir, string pattern, DateTimeOffset since)
    {
        try
        {
            if (!Directory.Exists(dir)) return null;
            return new DirectoryInfo(dir).GetFiles(pattern)
                // 文件系统时间精度和时钟都可能差一点，留几秒余量
                .Where(f => f.LastWriteTimeUtc >= since.UtcDateTime.AddSeconds(-5))
                .OrderByDescending(f => f.LastWriteTimeUtc)
                .FirstOrDefault()?.FullName;
        }
        catch { return null; }
    }

    /// <summary>
    /// 从 Minecraft 崩溃报告里摘一句话：Description 那行加第一行异常。
    /// 例如 "Rendering overlay — java.lang.NullPointerException: ..."
    /// </summary>
    public static string? Summarize(string? crashReportPath)
    {
        if (crashReportPath is null) return null;
        try
        {
            string? description = null, exception = null;
            foreach (var line in ReadTail(crashReportPath, 256 * 1024).Split('\n').Take(200))
            {
                var t = line.Trim();
                if (description is null && t.StartsWith("Description:", StringComparison.Ordinal))
                    description = t["Description:".Length..].Trim();
                else if (description is not null && exception is null && t.Length > 0
                         && (t.Contains("Exception", StringComparison.Ordinal) || t.Contains("Error", StringComparison.Ordinal)))
                    exception = t;
                if (description is not null && exception is not null) break;
            }
            var text = string.Join(" — ", new[] { description, exception }.Where(s => !string.IsNullOrEmpty(s)));
            return text.Length == 0 ? null : Redact(text.Length > 500 ? text[..500] : text);
        }
        catch { return null; }
    }

    /// <summary>
    /// 打包。<paramref name="summary"/> 写进 report.json，服务端列表只看它；
    /// <paramref name="environment"/> 为 null 表示玩家关掉了"附带电脑环境"。
    /// </summary>
    public static byte[] Build(LauncherPaths paths, GameCrash? crash, JsonObject summary, JsonObject? environment)
    {
        var since = crash?.StartedAt ?? DateTimeOffset.Now.AddDays(-1);
        using var buffer = new MemoryStream();
        using (var zip = new ZipArchive(buffer, ZipArchiveMode.Create, leaveOpen: true))
        {
            void AddText(string name, string text)
            {
                var entry = zip.CreateEntry(name, CompressionLevel.Optimal);
                using var writer = new StreamWriter(entry.Open(), new UTF8Encoding(false));
                writer.Write(text);
            }
            void AddFile(string name, string? path, long cap)
            {
                if (path is null || !File.Exists(path)) return;
                try { AddText(name, Redact(ReadTail(path, cap))); }
                catch (Exception ex) { Log.Warn($"打包 {name} 失败：{ex.Message}"); }
            }

            AddFile("crash-report.txt", FindCrashReport(paths, since), CrashFileCap);
            AddFile("hs_err.log", FindJvmCrash(paths, since), CrashFileCap);
            AddFile("latest.log", Path.Combine(paths.GameDir, "logs", "latest.log"), LatestLogCap);
            AddFile("game-output.log", Path.Combine(paths.LogDir, "game.log"), OutputLogCap);
            AddFile("launcher.log", Log.FilePath, LauncherLogCap);
            if (environment is not null)
                AddText("environment.json", Redact(environment.ToJsonString(new JsonSerializerOptions { WriteIndented = true })));

            summary["environmentIncluded"] = environment is not null;
            AddText("report.json", summary.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
        }
        return buffer.ToArray();
    }

    /// <summary>读文件尾部。游戏还在写的日志也要能读，所以按共享方式打开。</summary>
    public static string ReadTail(string path, long cap)
    {
        using var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        var skipped = Math.Max(0, fs.Length - cap);
        fs.Seek(skipped, SeekOrigin.Begin);
        using var reader = new StreamReader(fs, Encoding.UTF8, detectEncodingFromByteOrderMarks: true);
        var text = reader.ReadToEnd();
        if (skipped == 0) return text;
        // 从半行里开始的那一截不要
        var firstBreak = text.IndexOf('\n');
        if (firstBreak >= 0) text = text[(firstBreak + 1)..];
        return $"[前面 {skipped / 1024 / 1024.0:0.0} MB 已省略，只保留最后 {cap / 1024 / 1024} MB]\n" + text;
    }

    // C:\Users\张三\...、C:/Users/ADMINI~1/... 都算用户目录。用户名本身可能就是真名。
    private static readonly Regex ProfilePath = new(@"(?i)\b[A-Z]:(\\\\|\\|/)+Users(\\\\|\\|/)+[^\\/\r\n""'<>|:*?]+",
        RegexOptions.Compiled);
    // hs_err 会把环境变量整个打出来；Java 系统属性里也有
    private static readonly Regex IdentityLines = new(
        @"(?im)^(\s*(?:USERNAME|COMPUTERNAME|USERDOMAIN(?:_ROAMINGPROFILE)?|LOGONSERVER|USERPROFILE|HOMEPATH|OneDrive\w*|user\.name|user\.home)\s*[=:]\s*).*$",
        RegexOptions.Compiled);
    private static readonly Regex Tokens = new(
        @"(?i)((?:--accessToken|access_token|refresh_token|id_token)[""'\s,:=]+)[A-Za-z0-9._~+/=-]{8,}|(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}",
        RegexOptions.Compiled);

    public static string Redact(string text)
    {
        text = ProfilePath.Replace(text, "%USERPROFILE%");
        text = IdentityLines.Replace(text, "$1<已隐去>");
        text = Tokens.Replace(text, m => (m.Groups[1].Success ? m.Groups[1].Value : m.Groups[2].Value) + "<已隐去>");
        // 太短的计算机名（比如 TOP）会把日志里的普通单词也抹掉，只处理像样的名字
        var machine = Environment.MachineName;
        if (machine.Length >= 5) text = Regex.Replace(text, $@"(?i)\b{Regex.Escape(machine)}\b", "<计算机名>");
        return text;
    }

    /// <summary>
    /// 排查崩溃真正用得上的电脑环境：系统、CPU、内存、显卡和驱动、屏幕、Java 与启动参数。
    /// 不碰文件列表、进程列表、网络之类的东西。
    /// </summary>
    public static JsonObject CollectEnvironment(LauncherSettings settings, string? javaPath, string? javaVersion)
    {
        var env = new JsonObject
        {
            ["os"] = RuntimeInformation.OSDescription,
            ["osVersion"] = ReadRegistry(@"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "DisplayVersion"),
            ["osBuild"] = Environment.OSVersion.Version.ToString(),
            ["architecture"] = RuntimeInformation.OSArchitecture.ToString(),
            ["cpu"] = ReadRegistry(@"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString")?.Trim(),
            ["cpuThreads"] = Environment.ProcessorCount,
            ["memoryTotalMb"] = TotalMemoryMb(),
            ["memoryAvailableMb"] = AvailableMemoryMb(),
            ["gpus"] = Gpus(),
            ["java"] = new JsonObject { ["path"] = javaPath, ["version"] = javaVersion },
            ["launcher"] = new JsonObject
            {
                ["maxMemoryMb"] = settings.MaxMemoryMb,
                ["extraJvmArgs"] = settings.ExtraJvmArgs,
                ["window"] = $"{settings.WindowWidth}x{settings.WindowHeight}",
                ["fullscreen"] = settings.Fullscreen,
                ["gpuPreference"] = settings.Gpu,
                ["shaderPack"] = settings.ShaderPack,
                ["enabledOptional"] = new JsonArray(settings.EnabledOptional.Select(x => (JsonNode)x).ToArray()),
                ["disabledOptional"] = new JsonArray(settings.DisabledOptional.Select(x => (JsonNode)x).ToArray()),
            },
        };
        return env;
    }

    private static JsonArray Gpus()
    {
        var list = new JsonArray();
        try
        {
            using var baseKey = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64);
            using var root = baseKey.OpenSubKey(@"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}");
            if (root is null) return list;
            foreach (var name in root.GetSubKeyNames().Where(n => n.Length == 4 && n.All(char.IsDigit)))
            {
                using var sub = root.OpenSubKey(name);
                if (sub?.GetValue("DriverDesc") is not string desc || string.IsNullOrWhiteSpace(desc)) continue;
                list.Add(new JsonObject
                {
                    ["name"] = desc.Trim(),
                    ["driver"] = sub.GetValue("DriverVersion") as string,
                    ["driverDate"] = sub.GetValue("DriverDate") as string,
                });
            }
        }
        catch { }
        return list;
    }

    private static string? ReadRegistry(string key, string value)
    {
        try
        {
            using var baseKey = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64);
            using var sub = baseKey.OpenSubKey(key);
            return sub?.GetValue(value)?.ToString();
        }
        catch { return null; }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MemoryStatusEx
    {
        public uint Length, MemoryLoad;
        public ulong TotalPhys, AvailPhys, TotalPageFile, AvailPageFile, TotalVirtual, AvailVirtual, AvailExtendedVirtual;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GlobalMemoryStatusEx(ref MemoryStatusEx status);

    private static long? TotalMemoryMb() => Memory() is { } m ? (long)(m.TotalPhys / 1024 / 1024) : null;
    private static long? AvailableMemoryMb() => Memory() is { } m ? (long)(m.AvailPhys / 1024 / 1024) : null;

    private static MemoryStatusEx? Memory()
    {
        var status = new MemoryStatusEx { Length = (uint)Marshal.SizeOf<MemoryStatusEx>() };
        try { return GlobalMemoryStatusEx(ref status) ? status : null; }
        catch { return null; }
    }
}
