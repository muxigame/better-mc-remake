using System.Diagnostics;
using System.Text;
using BatterMC.Protocol;

namespace BatterMC.Core;

/// <summary>
/// 启动器自更新。
///
/// Windows 上运行中的 exe 不能被覆盖，所以套路是：
/// 下新版到临时文件 → 写一个批处理 → 退出自己 → 批处理等进程消失后换文件并重新拉起。
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

    /// <summary>下载新版并就位。成功返回 true，调用方随后应立即退出进程。</summary>
    public static async Task<bool> ApplyAsync(
        LauncherRelease release, LauncherPaths paths, LauncherSettings settings,
        Downloader downloader, IProgress<DownloadProgress>? progress, CancellationToken ct)
    {
        var currentExe = Environment.ProcessPath;
        if (string.IsNullOrEmpty(currentExe))
        {
            Log.Warn("拿不到当前 exe 路径，跳过自更新");
            return false;
        }

        var url = release.Url.StartsWith("http", StringComparison.OrdinalIgnoreCase)
            ? release.Url
            : settings.UpdateBaseUrl.TrimEnd('/') + "/" + release.Url.TrimStart('/');

        var updateDir = Path.Combine(paths.DataDir, "update");
        Directory.CreateDirectory(updateDir);
        var newExe = Path.Combine(updateDir, "BatterMC5Remake.new.exe");

        Log.Info($"下载启动器 {release.Version}：{url}");
        await downloader.DownloadAllAsync(new[]
        {
            new DownloadItem
            {
                Url = url,
                TargetPath = newExe,
                ExpectedSize = release.Size,
                Display = $"启动器 {release.Version}",
            },
        }, progress, ct).ConfigureAwait(false);

        if (!string.IsNullOrWhiteSpace(release.Sha256))
        {
            var actual = Hashing.Sha256File(newExe);
            if (!actual.Equals(release.Sha256, StringComparison.OrdinalIgnoreCase))
            {
                try { File.Delete(newExe); } catch { }
                throw new InvalidOperationException($"启动器校验失败：期望 {release.Sha256}，实得 {actual}");
            }
        }

        var script = Path.Combine(updateDir, "apply-update.cmd");
        var pid = Environment.ProcessId;

        // /W 让 ping 当计时器用，比 timeout 更不挑环境
        var cmd = $"""
            @echo off
            chcp 65001 >nul
            echo 正在更新 BatterMC5Remake 启动器...
            :wait
            tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul
            if not errorlevel 1 (
              ping -n 2 127.0.0.1 >nul
              goto wait
            )
            ping -n 2 127.0.0.1 >nul
            move /Y "{newExe}" "{currentExe}" >nul
            if errorlevel 1 (
              echo 更新失败，请手动把 "{newExe}" 覆盖到 "{currentExe}"
              pause
              exit /b 1
            )
            start "" "{currentExe}"
            del "%~f0"
            """;

        File.WriteAllText(script, cmd, new UTF8Encoding(false));

        Process.Start(new ProcessStartInfo
        {
            FileName = "cmd.exe",
            Arguments = $"/c \"{script}\"",
            UseShellExecute = false,
            CreateNoWindow = true,
        });

        Log.Info("更新脚本已启动，退出当前进程");
        return true;
    }
}
