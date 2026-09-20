using System.Diagnostics;
using System.Text;

namespace BatterMC.Core;

/// <summary>
/// 跑一遍 NeoForge 官方安装器，把那几个「下不到、只能本地生成」的产物补出来。
///
/// 为什么非做不可：版本 JSON 的 libraries 里**没有**
///   net/neoforged/neoforge/&lt;v&gt;/neoforge-&lt;v&gt;-universal.jar   ← neoforge 这个 mod 本身
///   net/neoforged/neoforge/&lt;v&gt;/neoforge-&lt;v&gt;-client.jar      ← 打过补丁的 Minecraft
///   net/minecraft/client/&lt;mc&gt;-&lt;neoform&gt;/client-*-extra.jar    ← 从本体 jar 拆出来的资源
///
/// 它们是安装器用 binarypatcher 在本地从原版 client.jar 生成的。
/// 原版 jar 不允许再分发，所以任何启动器都必须在玩家机器上跑一次安装器
/// —— PCL、HMCL、官方启动器都是这么干的。
///
/// 不补这一步，FML 能启动，但 minecraft 和 neoforge 这两个「模组」是缺的，
/// 于是每一个模组都会报 "Missing or unsupported mandatory dependencies"。
/// </summary>
public static class NeoForgeInstaller
{
    private const string MavenBase = "https://maven.neoforged.net/releases/net/neoforged/neoforge";

    /// <summary>安装器生成的那几个文件都在不在。</summary>
    public static bool IsInstalled(VersionJson version, LauncherPaths paths, out string missing)
    {
        missing = "";
        var required = RequiredArtifacts(version, paths);
        if (required.Count == 0)
        {
            // 拿不到 --fml.* 版本号，说明不是 NeoForge 的版本，不需要这一步
            return true;
        }

        var absent = required.Where(p => !File.Exists(p)).ToList();
        if (absent.Count == 0) return true;

        missing = string.Join("、", absent.Select(Path.GetFileName));
        return false;
    }

    private static List<string> RequiredArtifacts(VersionJson version, LauncherPaths paths)
    {
        var result = new List<string>();
        var nf = version.NeoForgeVersion;
        if (!string.IsNullOrEmpty(nf))
        {
            var dir = Path.Combine(paths.LibrariesDir, "net", "neoforged", "neoforge", nf);
            result.Add(Path.Combine(dir, $"neoforge-{nf}-universal.jar"));
            result.Add(Path.Combine(dir, $"neoforge-{nf}-client.jar"));
        }

        var mc = version.McVersion;
        var neoform = version.NeoFormVersion;
        if (!string.IsNullOrEmpty(mc) && !string.IsNullOrEmpty(neoform))
        {
            var coord = $"{mc}-{neoform}";
            var dir = Path.Combine(paths.LibrariesDir, "net", "minecraft", "client", coord);
            result.Add(Path.Combine(dir, $"client-{coord}-extra.jar"));
            result.Add(Path.Combine(dir, $"client-{coord}-slim.jar"));
        }

        return result;
    }

    public static async Task InstallAsync(
        VersionJson version, LauncherPaths paths, JavaInstall java,
        string? installerUrlOverride, Downloader downloader,
        IProgress<DownloadProgress>? progress, IProgress<string>? status, CancellationToken ct)
    {
        var nf = version.NeoForgeVersion
                 ?? throw new InvalidOperationException("版本 JSON 里没有 --fml.neoForgeVersion，无法确定 NeoForge 版本");

        var url = string.IsNullOrWhiteSpace(installerUrlOverride)
            ? $"{MavenBase}/{nf}/neoforge-{nf}-installer.jar"
            : installerUrlOverride!;

        Directory.CreateDirectory(paths.TempDir);
        var installerJar = Path.Combine(paths.TempDir, $"neoforge-{nf}-installer.jar");

        status?.Report($"下载 NeoForge {nf} 安装器");
        Log.Info($"下载 NeoForge 安装器：{url}");
        await downloader.DownloadAllAsync(new[]
        {
            new DownloadItem { Url = url, TargetPath = installerJar, Display = $"NeoForge {nf} 安装器" },
        }, progress, ct).ConfigureAwait(false);

        PrepareGameDir(version, paths);

        status?.Report($"安装 NeoForge {nf}（本地生成，需要一两分钟）");
        Log.Info($"运行安装器：{java.Path} -jar {installerJar} --install-client {paths.GameDir}");

        var psi = new ProcessStartInfo
        {
            FileName = java.Path,
            WorkingDirectory = paths.TempDir,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        psi.ArgumentList.Add("-jar");
        psi.ArgumentList.Add(installerJar);
        psi.ArgumentList.Add("--install-client");
        psi.ArgumentList.Add(paths.GameDir);

        using var process = new Process { StartInfo = psi, EnableRaisingEvents = true };
        var tail = new Queue<string>();

        void OnLine(string? line)
        {
            if (line is null) return;
            lock (tail) { tail.Enqueue(line); while (tail.Count > 40) tail.Dequeue(); }

            // 安装器输出几百行，只把有意义的进度转出去
            if (line.StartsWith("Processor:", StringComparison.Ordinal)
                || line.StartsWith("Considering", StringComparison.Ordinal)
                || line.Contains("Successfully installed", StringComparison.Ordinal))
                status?.Report(line.Trim());

            Log.Debug("[installer] " + line);
        }

        process.OutputDataReceived += (_, e) => OnLine(e.Data);
        process.ErrorDataReceived += (_, e) => OnLine(e.Data);

        process.Start();
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        await process.WaitForExitAsync(ct).ConfigureAwait(false);

        string[] tailLines;
        lock (tail) tailLines = tail.ToArray();

        if (process.ExitCode != 0)
        {
            Log.Error("NeoForge 安装器输出：\n" + string.Join('\n', tailLines));
            throw new InvalidOperationException(
                $"NeoForge 安装器失败（退出码 {process.ExitCode}）。" +
                $"安装器日志：{Path.Combine(paths.TempDir, "installer.log")}");
        }

        if (!IsInstalled(version, paths, out var stillMissing))
        {
            Log.Error("NeoForge 安装器输出：\n" + string.Join('\n', tailLines));
            throw new InvalidOperationException($"安装器跑完了但文件还是缺：{stillMissing}");
        }

        Log.Info($"NeoForge {nf} 安装完成");
        try { File.Delete(installerJar); } catch { }
    }

    /// <summary>
    /// 安装器对游戏目录有两个硬性要求。
    /// </summary>
    private static void PrepareGameDir(VersionJson version, LauncherPaths paths)
    {
        // 1. 它要往 launcher_profiles.json 里写一条启动配置（"Injecting profile"），
        //    文件不存在会直接报错。内容我们用不上，给个空壳就行。
        var profiles = Path.Combine(paths.GameDir, "launcher_profiles.json");
        if (!File.Exists(profiles))
        {
            AtomicFile.WriteAllText(profiles, """{"profiles":{},"settings":{},"version":3}""");
            Log.Debug("创建了空的 launcher_profiles.json 供安装器写入");
        }

        // 2. 它需要原版 client.jar 放在 versions/<mc>/<mc>.jar。
        //    我们已经从官方 CDN 下过同一个 jar 了（校验过 SHA-1），
        //    直接复制过去，省掉安装器再下一次 26 MB。
        var mc = version.McVersion;
        if (string.IsNullOrEmpty(mc)) return;

        var vanillaJar = Path.Combine(paths.VersionsDir, mc, mc + ".jar");
        if (File.Exists(vanillaJar)) return;

        var ourJar = Path.Combine(paths.VersionsDir, version.Id, version.Id + ".jar");
        if (!File.Exists(ourJar)) return;

        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(vanillaJar)!);
            File.Copy(ourJar, vanillaJar);
            Log.Debug($"复用已下载的本体 jar 供安装器使用：{vanillaJar}");
        }
        catch (Exception ex)
        {
            Log.Warn($"复制本体 jar 失败，安装器会自己重新下载：{ex.Message}");
        }
    }
}
