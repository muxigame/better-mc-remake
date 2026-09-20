using System.Diagnostics;
using System.IO.Compression;
using System.Text.Json;
using System.Text.RegularExpressions;
using BatterMC.Protocol;
using Microsoft.Win32;

namespace BatterMC.Core;

public sealed record JavaInstall(string Path, int Major, string Version, string Source)
{
    /// <summary>不带控制台窗口的那个。</summary>
    public string JavawPath
    {
        get
        {
            var w = System.IO.Path.Combine(System.IO.Path.GetDirectoryName(Path) ?? "", "javaw.exe");
            return File.Exists(w) ? w : Path;
        }
    }
    public override string ToString() => $"Java {Version} ({Source})";
}

/// <summary>
/// Java 的发现、校验和按需下载。
/// 不随包分发 JRE —— 那会让启动器从几 MB 变成上百 MB；
/// 而是先把机器上已有的 Java 全部扫出来，实在没有再下。
/// </summary>
public static partial class JavaManager
{
    [GeneratedRegex(@"^JAVA_VERSION=""?([0-9._+\-]+)", RegexOptions.Multiline)]
    private static partial Regex ReleaseVersionRegex();

    [GeneratedRegex(@"version ""?([0-9][0-9._+\-]*)""?")]
    private static partial Regex CliVersionRegex();

    /// <summary>扫出机器上所有能用的 Java，按版本从高到低排。</summary>
    public static List<JavaInstall> Discover(LauncherPaths paths)
    {
        var found = new Dictionary<string, JavaInstall>(StringComparer.OrdinalIgnoreCase);

        void Consider(string? javaExe, string source)
        {
            if (string.IsNullOrWhiteSpace(javaExe)) return;
            try
            {
                javaExe = System.IO.Path.GetFullPath(javaExe);
                if (!File.Exists(javaExe) || found.ContainsKey(javaExe)) return;
                var info = Probe(javaExe, source);
                if (info is not null) found[javaExe] = info;
            }
            catch { }
        }

        void ConsiderHome(string? home, string source)
        {
            if (string.IsNullOrWhiteSpace(home)) return;
            Consider(System.IO.Path.Combine(home, "bin", "java.exe"), source);
        }

        void ScanContainer(string dir, string source)
        {
            try
            {
                if (!Directory.Exists(dir)) return;
                foreach (var sub in Directory.EnumerateDirectories(dir))
                {
                    ConsiderHome(sub, source);
                    // 有些安装器多套一层，例如 <厂商>\<版本>\jdk-21\bin
                    foreach (var sub2 in Directory.EnumerateDirectories(sub))
                        ConsiderHome(sub2, source);
                }
            }
            catch { }
        }

        // 1. 启动器自己下的
        ScanContainer(paths.RuntimeDir, "启动器自带");

        // 2. JAVA_HOME
        ConsiderHome(Environment.GetEnvironmentVariable("JAVA_HOME"), "JAVA_HOME");

        // 3. 常见安装位置
        var user = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var pf = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
        var pf86 = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86);
        var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);

        ScanContainer(System.IO.Path.Combine(user, ".jdks"), "IDE 安装");
        foreach (var root in new[] { pf, pf86 })
        {
            if (string.IsNullOrEmpty(root)) continue;
            foreach (var vendor in new[]
            {
                "Java", "Eclipse Adoptium", "Eclipse Foundation", "AdoptOpenJDK",
                "Zulu", "Amazon Corretto", "Microsoft", "BellSoft", "Semeru",
                "RedHat", "JetBrains Runtime", "Android", "Temurin",
            })
                ScanContainer(System.IO.Path.Combine(root, vendor), vendor);
        }
        ScanContainer(System.IO.Path.Combine(local, "Programs", "Eclipse Adoptium"), "Adoptium");
        ScanContainer(System.IO.Path.Combine(local, "Programs", "Microsoft", "jdk"), "Microsoft");

        // 4. 其他启动器下过的 Java（PCL / HMCL）——玩家机器上八成已经有了
        foreach (var drive in new[] { "C", "D", "E" })
        {
            ScanContainer($@"{drive}:\Program Files\Java", "Java");
            ScanContainer($@"{drive}:\PCL\Java", "PCL");
            ScanContainer($@"{drive}:\HMCL\java", "HMCL");
        }
        ScanContainer(System.IO.Path.Combine(user, "AppData", "Roaming", ".minecraft", "runtime"), "官方启动器");
        ScanContainer(System.IO.Path.Combine(local, "Packages"), "微软商店");

        // 5. 注册表
        foreach (var hive in new[] { RegistryHive.LocalMachine, RegistryHive.CurrentUser })
        foreach (var view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
        {
            foreach (var keyPath in new[]
            {
                @"SOFTWARE\JavaSoft\JDK", @"SOFTWARE\JavaSoft\JRE",
                @"SOFTWARE\JavaSoft\Java Development Kit", @"SOFTWARE\JavaSoft\Java Runtime Environment",
                @"SOFTWARE\Eclipse Adoptium\JDK", @"SOFTWARE\Eclipse Adoptium\JRE",
                @"SOFTWARE\Azul Systems\Zulu", @"SOFTWARE\Amazon\Corretto",
            })
            {
                try
                {
                    using var baseKey = RegistryKey.OpenBaseKey(hive, view);
                    using var key = baseKey.OpenSubKey(keyPath);
                    if (key is null) continue;
                    foreach (var name in key.GetSubKeyNames())
                    {
                        using var v = key.OpenSubKey(name);
                        ConsiderHome(v?.GetValue("JavaHome") as string, "注册表");
                        using var hotspot = v?.OpenSubKey("hotspot\\MSI");
                        ConsiderHome(hotspot?.GetValue("Path") as string, "注册表");
                    }
                }
                catch { }
            }
        }

        // 6. PATH
        foreach (var dir in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'))
        {
            if (string.IsNullOrWhiteSpace(dir)) continue;
            try { Consider(System.IO.Path.Combine(dir.Trim(), "java.exe"), "PATH"); } catch { }
        }

        return found.Values
            .OrderByDescending(j => j.Major)
            .ThenByDescending(j => j.Version, StringComparer.OrdinalIgnoreCase)
            .ToList();
    }

    /// <summary>
    /// 读一个 Java 的版本。优先解析 release 文件（不用起进程，快得多），
    /// 失败才退回跑 java -version。
    /// </summary>
    public static JavaInstall? Probe(string javaExe, string source = "手动指定")
    {
        try
        {
            var home = System.IO.Path.GetDirectoryName(System.IO.Path.GetDirectoryName(javaExe));
            if (home is not null)
            {
                var release = System.IO.Path.Combine(home, "release");
                if (File.Exists(release))
                {
                    var m = ReleaseVersionRegex().Match(File.ReadAllText(release));
                    if (m.Success)
                    {
                        var ver = m.Groups[1].Value.Trim('"');
                        return new JavaInstall(javaExe, ParseMajor(ver), ver, source);
                    }
                }
            }

            var psi = new ProcessStartInfo(javaExe, "-version")
            {
                RedirectStandardError = true,
                RedirectStandardOutput = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            using var p = Process.Start(psi);
            if (p is null) return null;
            var text = p.StandardError.ReadToEnd() + p.StandardOutput.ReadToEnd();
            if (!p.WaitForExit(6000)) { try { p.Kill(true); } catch { } return null; }

            var cm = CliVersionRegex().Match(text);
            if (!cm.Success) return null;
            var version = cm.Groups[1].Value;
            return new JavaInstall(javaExe, ParseMajor(version), version, source);
        }
        catch { return null; }
    }

    /// <summary>"21.0.4" -> 21；"1.8.0_402" -> 8。</summary>
    public static int ParseMajor(string version)
    {
        var parts = version.Split('.', '_', '+', '-');
        if (parts.Length == 0) return 0;
        if (!int.TryParse(parts[0], out var first)) return 0;
        if (first == 1 && parts.Length > 1 && int.TryParse(parts[1], out var second)) return second;
        return first;
    }

    /// <summary>
    /// 选一个能用的 Java：先看玩家手动指定的，再看扫描结果里主版本号相符的。
    /// 找不到返回 null，由调用方决定是否下载。
    /// </summary>
    public static JavaInstall? Select(LauncherPaths paths, LauncherSettings settings, JavaRequirement req, out string reason)
    {
        if (!string.IsNullOrWhiteSpace(settings.JavaPath))
        {
            var manual = Probe(settings.JavaPath!, "手动指定");
            if (manual is null)
            {
                reason = $"手动指定的 Java 无法识别：{settings.JavaPath}";
                Log.Warn(reason);
            }
            else if (manual.Major != req.Major)
            {
                reason = $"手动指定的是 Java {manual.Major}，本包需要 Java {req.Major}";
                Log.Warn(reason);
            }
            else { reason = ""; return manual; }
        }

        var all = Discover(paths);
        Log.Info($"扫描到 {all.Count} 个 Java：" + string.Join("、", all.Take(6).Select(j => j.Version)));

        var match = all.FirstOrDefault(j => j.Major == req.Major);
        if (match is not null) { reason = ""; return match; }

        reason = all.Count == 0
            ? $"没有在这台机器上找到任何 Java，本包需要 Java {req.Major}"
            : $"已装的 Java 是 {string.Join("、", all.Select(j => j.Major).Distinct())}，本包需要 Java {req.Major}";
        return null;
    }

    /// <summary>
    /// 下载并解压一个 JRE 到 runtime\。
    /// 优先用清单里自建的地址（国内直连 Adoptium 常年超时），没有才问 Adoptium API。
    /// </summary>
    public static async Task<JavaInstall> DownloadAsync(
        LauncherPaths paths, JavaRequirement req, Downloader downloader,
        IProgress<DownloadProgress>? progress, CancellationToken ct)
    {
        string url; string? sha256 = null; long size = 0; string label;

        if (!string.IsNullOrWhiteSpace(req.Url))
        {
            url = req.Url!;
            sha256 = req.Sha256;
            size = req.Size;
            label = $"Java {req.Major}（整合包自带源）";
            Log.Info($"从整合包源下载 Java：{url}");
        }
        else
        {
            var api = $"https://api.adoptium.net/v3/assets/latest/{req.Major}/hotspot" +
                      $"?architecture=x64&image_type={req.Image}&os=windows&vendor=eclipse";
            Log.Info($"询问 Adoptium：{api}");
            var json = await downloader.GetStringAsync(api, ct).ConfigureAwait(false);
            using var doc = JsonDocument.Parse(json);
            var first = doc.RootElement.EnumerateArray().FirstOrDefault();
            if (first.ValueKind != JsonValueKind.Object)
                throw new InvalidOperationException($"Adoptium 没有返回 Java {req.Major} 的 Windows x64 构建");

            var pkg = first.GetProperty("binary").GetProperty("package");
            url = pkg.GetProperty("link").GetString()!;
            sha256 = pkg.TryGetProperty("checksum", out var c) ? c.GetString() : null;
            size = pkg.TryGetProperty("size", out var s) ? s.GetInt64() : 0;
            var semver = first.TryGetProperty("version", out var v) && v.TryGetProperty("semver", out var sv)
                ? sv.GetString() : req.Major.ToString();
            label = $"Java {semver}（Adoptium）";
        }

        Directory.CreateDirectory(paths.TempDir);
        var zip = System.IO.Path.Combine(paths.TempDir, $"java-{req.Major}.zip");

        await downloader.DownloadAllAsync(new[]
        {
            new DownloadItem { Url = url, TargetPath = zip, ExpectedSize = size, Display = label },
        }, progress, ct).ConfigureAwait(false);

        if (!string.IsNullOrWhiteSpace(sha256))
        {
            var actual = Hashing.Sha256File(zip);
            if (!actual.Equals(sha256, StringComparison.OrdinalIgnoreCase))
            {
                try { File.Delete(zip); } catch { }
                throw new InvalidOperationException($"Java 压缩包校验失败：期望 {sha256}，实得 {actual}");
            }
        }

        var target = System.IO.Path.Combine(paths.RuntimeDir, $"java-{req.Major}");
        if (Directory.Exists(target))
        {
            try { Directory.Delete(target, recursive: true); }
            catch (Exception ex) { Log.Warn($"旧 runtime 目录删除失败：{ex.Message}"); }
        }
        Directory.CreateDirectory(target);

        Log.Info($"解压 Java 到 {target}");
        ZipFile.ExtractToDirectory(zip, target, overwriteFiles: true);
        try { File.Delete(zip); } catch { }

        var javaExe = Directory
            .EnumerateFiles(target, "java.exe", SearchOption.AllDirectories)
            .FirstOrDefault(p => System.IO.Path.GetFileName(System.IO.Path.GetDirectoryName(p)) == "bin")
            ?? throw new InvalidOperationException("压缩包里没找到 bin\\java.exe");

        var install = Probe(javaExe, "启动器自带")
            ?? throw new InvalidOperationException("下载的 Java 无法识别版本");

        Log.Info($"Java 就绪：{install}");
        return install;
    }
}
