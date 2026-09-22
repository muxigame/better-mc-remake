using System.Runtime.InteropServices;
using System.Text;
using BatterMC.Core;

namespace BatterMC.Launcher;

/// <summary>
/// 命令行诊断模式。
///
///   --verify    只检查不动手：拉清单、算出要下载/删除什么、找 Java，然后打印
///   --install   真的执行一遍完整准备流程，但不启动游戏
///   --selftest  跑一遍内部逻辑自检（离线 UUID、硬配置改写、NBT、规则求值）
///
/// sidecar 默认没有控制台，所以先挂到调用方的控制台上。
/// 出问题时让玩家跑一条命令把结果发过来，比让他描述「打不开」有用得多。
/// </summary>
internal static partial class ConsoleTools
{
    [LibraryImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool AttachConsole(int processId);

    [LibraryImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool AllocConsole();

    private const int AttachParentProcess = -1;

    public static bool TryHandle(string[] args, LauncherPaths paths)
    {
        var verify = args.Any(a => a.Equals("--verify", StringComparison.OrdinalIgnoreCase));
        var install = args.Any(a => a.Equals("--install", StringComparison.OrdinalIgnoreCase));
        var selftest = args.Any(a => a.Equals("--selftest", StringComparison.OrdinalIgnoreCase));
        var launch = args.Any(a => a.Equals("--launch", StringComparison.OrdinalIgnoreCase));
        if (!verify && !install && !selftest && !launch) return false;

        // 只有在输出没被重定向时才去抢一个控制台。
        // 如果调用方已经把 stdout 接到管道上（脚本里 `exe --verify > out.txt`），
        // AttachConsole 反而会把输出从管道抢走，什么都收不到。
        if (!Console.IsOutputRedirected)
        {
            if (!AttachConsole(AttachParentProcess)) AllocConsole();
            Console.SetOut(new StreamWriter(Console.OpenStandardOutput()) { AutoFlush = true });
        }
        try { Console.OutputEncoding = Encoding.UTF8; } catch { }

        if (selftest)
        {
            Environment.ExitCode = SelfTest.Run();
            Console.WriteLine();
            return true;
        }

        // 同时落一份文件，方便玩家直接把报告发过来
        var reportPath = Path.Combine(paths.DataDir, "verify-report.txt");
        StreamWriter? report = null;
        try
        {
            Directory.CreateDirectory(paths.DataDir);
            report = new StreamWriter(reportPath, false, new UTF8Encoding(false)) { AutoFlush = true };
            Console.SetOut(new TeeWriter(Console.Out, report));
        }
        catch { }

        Log.Line += (level, message) =>
        {
            if (level != "DEBUG") Console.WriteLine($"  [{level}] {message}");
        };

        try
        {
            RunAsync(paths, install || launch, launch).GetAwaiter().GetResult();
        }
        catch (Exception ex)
        {
            Console.WriteLine();
            Console.WriteLine("失败：" + ex.Message);
            if (ex.InnerException is not null) Console.WriteLine("  内层：" + ex.InnerException.Message);
            Environment.ExitCode = 1;
        }

        Console.WriteLine();
        Console.WriteLine("报告已保存：" + reportPath);
        try { report?.Dispose(); } catch { }
        return true;
    }

    /// <summary>同时写控制台和文件。</summary>
    private sealed class TeeWriter(TextWriter a, TextWriter b) : TextWriter
    {
        public override Encoding Encoding => Encoding.UTF8;
        public override void Write(char value) { a.Write(value); b.Write(value); }
        public override void Write(string? value) { a.Write(value); b.Write(value); }
        public override void WriteLine(string? value) { a.WriteLine(value); b.WriteLine(value); }
        public override void Flush() { a.Flush(); b.Flush(); }
    }

    private static async Task RunAsync(LauncherPaths paths, bool install, bool launch = false)
    {
        var settings = LauncherSettings.Load(paths.SettingsFile);
        var state = LocalState.Load(paths.StateFile);

        Console.WriteLine();
        Console.WriteLine("BatterMC5Remake 启动器 " + GameLauncher.ThisVersion());
        Console.WriteLine("  根目录     " + paths.Root);
        Console.WriteLine("  游戏目录   " + paths.GameDir);
        Console.WriteLine("  更新服务器 " + settings.UpdateBaseUrl);
        Console.WriteLine("  模式       " + (launch ? "准备并启动游戏"
                                          : install ? "完整安装（不启动游戏）"
                                          : "只检查不改动"));
        Console.WriteLine();

        using var downloader = new Downloader();
        var ctx = new PipelineContext { Paths = paths, Settings = settings, State = state };

        if (install)
        {
            var pipeline = new LaunchPipeline(ctx, downloader);
            var lastPhase = "";
            pipeline.Status += s =>
            {
                if (s.Phase == lastPhase) return;
                lastPhase = s.Phase;
                Console.WriteLine($"→ {s.Phase}  {s.Detail}");
            };
            await pipeline.PrepareAsync(false, CancellationToken.None).ConfigureAwait(false);

            Console.WriteLine();
            Console.WriteLine("准备完成。");
            Console.WriteLine("  整合包   " + ctx.Manifest!.Pack.Name + " " + ctx.Manifest.Pack.Version);
            Console.WriteLine("  版本     " + ctx.Version!.Id + "（" + ctx.Version.Libraries.Count + " 个运行库）");
            Console.WriteLine("  Java     " + ctx.Java);
            Console.WriteLine("  堆上限   " + settings.EffectiveMaxMemoryMb() + " MB");

            if (!launch) return;

            if (!OfflineAuth.IsValidUsername(settings.Username))
                throw new InvalidOperationException(
                    $"settings.json 里的用户名不合法：\"{settings.Username}\"（要 3–16 位字母数字下划线）");

            // This CLI is an offline diagnostic tool; only numeric platform UID
            // is accepted. End-user authentication happens in the GUI launcher.
            if (!long.TryParse(settings.Username, System.Globalization.NumberStyles.None,
                System.Globalization.CultureInfo.InvariantCulture, out var uid))
                throw new InvalidOperationException("游戏登录名必须为平台 UID，请通过启动器登录账户");
            var session = GameSession.OfflineUid(uid);
            Console.WriteLine();
            Console.WriteLine($"启动游戏：{session.Username}  UUID {session.UuidDashed}");
            Console.WriteLine();

            var gameLauncher = new GameLauncher(paths, settings);
            gameLauncher.GameWindowReady += () => Console.WriteLine("  >> 游戏窗口已就绪");
            gameLauncher.GameOutput += line =>
            {
                // 只回显关键里程碑，否则几千行模组加载日志会把控制台刷爆
                if (line.Contains("Loading ", StringComparison.Ordinal) && line.Contains(" mods", StringComparison.Ordinal)
                    || line.Contains("Backend library", StringComparison.Ordinal)
                    || line.Contains("Setting user:", StringComparison.Ordinal)
                    || line.Contains("Game took", StringComparison.Ordinal)
                    || line.Contains("/ERROR]", StringComparison.Ordinal)
                    || line.Contains("/FATAL]", StringComparison.Ordinal))
                    Console.WriteLine("  | " + line);
            };

            var result = await gameLauncher
                .LaunchAsync(ctx.Version!, ctx.Java!, ctx.Manifest!, session, null, CancellationToken.None)
                .ConfigureAwait(false);

            Console.WriteLine();
            Console.WriteLine($"游戏退出，退出码 {result.ExitCode}");
            if (result.CrashHint is not null) Console.WriteLine("  " + result.CrashHint);
            Console.WriteLine("  游戏日志 " + result.LogFile);
            Environment.ExitCode = result.ExitCode == 0 ? 0 : 1;
            return;
        }

        // ── 只检查 ──
        var sync = new SyncEngine(paths, state, settings, downloader);
        var manifest = await sync.FetchManifestAsync(settings.UpdateBaseUrl, CancellationToken.None).ConfigureAwait(false);
        ctx.Manifest = manifest;

        Console.WriteLine($"清单 {manifest.Pack.Name} {manifest.Pack.Version}"
                          + $"（{manifest.Files.Count} 个文件，{manifest.Overlays.Count} 个强制配置）");
        Console.WriteLine($"  本地已安装 {state.InstalledPackVersion ?? "（无）"}");
        Console.WriteLine();

        var plan = sync.Plan(manifest, null, CancellationToken.None);
        Console.WriteLine($"需要下载 {plan.Downloads.Count} 个（{SyncEngine.Human(plan.Bytes)}）");
        foreach (var d in plan.Downloads.Take(8)) Console.WriteLine("    + " + d.Display);
        if (plan.Downloads.Count > 8) Console.WriteLine($"    … 还有 {plan.Downloads.Count - 8} 个");

        Console.WriteLine($"需要删除 {plan.Deletions.Count} 个");
        foreach (var d in plan.Deletions.Take(8)) Console.WriteLine("    - " + d);
        if (plan.Deletions.Count > 8) Console.WriteLine($"    … 还有 {plan.Deletions.Count - 8} 个");

        Console.WriteLine();
        var java = JavaManager.Select(paths, settings, manifest.Java, out var reason);
        Console.WriteLine(java is not null
            ? $"Java：{java}\n      {java.Path}"
            : $"Java：没有合适的（{reason}），启动时会自动下载");

        Console.WriteLine();
        Console.WriteLine($"堆上限：{settings.EffectiveMaxMemoryMb()} MB");
        Console.WriteLine($"日志：{Log.FilePath}");
    }
}
