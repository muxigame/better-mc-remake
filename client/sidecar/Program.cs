using System.Text;
using BatterMC.Core;

namespace BatterMC.Launcher;

internal static class Program
{
    private static async Task<int> Main(string[] args)
    {
        Console.InputEncoding = Encoding.UTF8;
        Console.OutputEncoding = Encoding.UTF8;

        if (args.Any(a => a.Equals("--selftest", StringComparison.OrdinalIgnoreCase)))
            return SelfTest.Run();

        var cliGameDir = ArgValue(args, "--game-dir");

        // 先定位启动器数据目录，再读取玩家保存的游戏目录。
        // 旧 WinForms 版直接 Resolve(cliGameDir)，导致界面里保存的 GameDirOverride 永远不生效。
        var bootstrapPaths = LauncherPaths.Resolve();
        var settings = LauncherSettings.Load(bootstrapPaths.SettingsFile);
        var paths = LauncherPaths.Resolve(cliGameDir ?? settings.GameDirOverride);

        paths.EnsureDataDir();
        Log.Init(paths.LogDir);
        Log.Info($"BatterMC Tauri sidecar {GameLauncher.ThisVersion()}");
        Log.Info($"根目录 {paths.Root}");
        Log.Info($"游戏目录 {paths.GameDir}");

        if (ConsoleTools.TryHandle(args, paths))
        {
            Log.Close();
            return Environment.ExitCode;
        }

        var state = LocalState.Load(paths.StateFile);
        using var host = new RpcHost(paths, settings, state);

        try
        {
            string? line;
            while ((line = await Console.In.ReadLineAsync().ConfigureAwait(false)) is not null)
            {
                if (string.IsNullOrWhiteSpace(line)) continue;
                host.Dispatch(line);
            }
            host.Shutdown();
            return 0;
        }
        catch (Exception ex)
        {
            Log.Error("sidecar 主循环失败", ex);
            return 1;
        }
        finally
        {
            Log.Close();
        }
    }

    private static string? ArgValue(string[] args, string name)
    {
        var i = Array.FindIndex(args, a => string.Equals(a, name, StringComparison.OrdinalIgnoreCase));
        return i >= 0 && i + 1 < args.Length ? args[i + 1] : null;
    }
}
