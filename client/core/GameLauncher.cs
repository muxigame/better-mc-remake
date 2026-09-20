using System.Diagnostics;
using System.Text;
using BatterMC.Protocol;

namespace BatterMC.Core;

public sealed record LaunchResult(int ExitCode, string LogFile, string? CrashHint);

public sealed class GameLauncher
{
    private readonly LauncherPaths _paths;
    private readonly LauncherSettings _settings;

    public GameLauncher(LauncherPaths paths, LauncherSettings settings)
    {
        _paths = paths;
        _settings = settings;
    }

    /// <summary>游戏窗口出现（GL 上下文就绪）时触发，UI 用它决定什么时候隐藏启动器。</summary>
    public event Action? GameWindowReady;
    /// <summary>游戏自己的 stdout/stderr。</summary>
    public event Action<string>? GameOutput;

    public async Task<LaunchResult> LaunchAsync(
        VersionJson version, JavaInstall java, PackManifest manifest, GameSession session,
        string? quickPlayEndpoint, CancellationToken ct)
    {
        var versionDir = Path.Combine(_paths.VersionsDir, version.Id);
        var nativesDir = Path.Combine(versionDir, version.Id + "-natives");
        Directory.CreateDirectory(nativesDir);
        Directory.CreateDirectory(_paths.CrashDir);

        var primary = manifest.Servers.FirstOrDefault(s => s.Primary) ?? manifest.Servers.FirstOrDefault();
        var directEndpoint = primary is not null && !string.IsNullOrWhiteSpace(primary.Host)
            ? $"{primary.Host}:{primary.Port}"
            : null;
        var joinEndpoint = quickPlayEndpoint ?? directEndpoint;
        var quickJoin = _settings.AutoJoinServer && !string.IsNullOrWhiteSpace(joinEndpoint);

        var features = new VersionJson.Features(
            CustomResolution: !_settings.Fullscreen,
            QuickPlayMultiplayer: quickJoin,
            Demo: false);

        var classpath = BuildClasspath(version, versionDir);

        var vars = new Dictionary<string, string>(StringComparer.Ordinal)
        {
            ["auth_player_name"] = session.Username,
            ["version_name"] = version.Id,
            ["game_directory"] = _paths.GameDir,
            ["assets_root"] = _paths.AssetsDir,
            ["assets_index_name"] = version.AssetIndex.Id,
            ["auth_uuid"] = session.UuidPlain,
            ["auth_access_token"] = session.AccessToken,
            ["clientid"] = "0",
            ["auth_xuid"] = "0",
            ["user_type"] = "msa",
            ["version_type"] = version.VersionType,
            ["natives_directory"] = nativesDir,
            ["launcher_name"] = "BatterMC5Remake",
            ["launcher_version"] = ThisVersion(),
            ["classpath"] = classpath,
            ["classpath_separator"] = ";",
            ["library_directory"] = _paths.LibrariesDir,
            ["resolution_width"] = _settings.WindowWidth.ToString(),
            ["resolution_height"] = _settings.WindowHeight.ToString(),
            ["quickPlayMultiplayer"] = quickJoin ? joinEndpoint! : "",
            ["quickPlayPath"] = "",
        };

        var args = new List<string>();
        args.AddRange(MemoryAndGcArgs());
        args.AddRange(DiagnosticArgs());
        args.AddRange(VersionJson.Expand(version.JvmArgs, features, vars));
        args.AddRange(ExtraArgs());
        args.Add(version.MainClass);
        args.AddRange(VersionJson.Expand(version.GameArgs, features, vars));

        if (_settings.Fullscreen) args.Add("--fullscreen");

        var gameLog = Path.Combine(_paths.LogDir, "game.log");
        var psi = new ProcessStartInfo
        {
            // 用 java.exe 而不是 javaw.exe：我们要接管 stdout/stderr，
            // 崩溃时才能留下完整输出。窗口用 CreateNoWindow 藏掉。
            FileName = java.Path,
            WorkingDirectory = _paths.GameDir,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        foreach (var a in args) psi.ArgumentList.Add(a);

        LogCommandLine(java, args, classpath);

        using var process = new Process { StartInfo = psi, EnableRaisingEvents = true };
        await using var writer = new StreamWriter(
            new FileStream(gameLog, FileMode.Create, FileAccess.Write, FileShare.ReadWrite),
            new UTF8Encoding(false)) { AutoFlush = true };

        var windowSignalled = false;
        var tail = new Queue<string>();

        void OnLine(string? line)
        {
            if (line is null) return;
            lock (writer) writer.WriteLine(line);

            lock (tail)
            {
                tail.Enqueue(line);
                while (tail.Count > 80) tail.Dequeue();
            }

            if (!windowSignalled && IsWindowReadyLine(line))
            {
                windowSignalled = true;
                try { GameWindowReady?.Invoke(); } catch { }
            }
            try { GameOutput?.Invoke(line); } catch { }
        }

        process.OutputDataReceived += (_, e) => OnLine(e.Data);
        process.ErrorDataReceived += (_, e) => OnLine(e.Data);

        var sw = Stopwatch.StartNew();
        process.Start();
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();

        Log.Info($"游戏已启动，PID {process.Id}");

        try
        {
            await process.WaitForExitAsync(ct).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            try { if (!process.HasExited) process.Kill(entireProcessTree: true); } catch { }
            throw;
        }

        sw.Stop();
        var exit = process.ExitCode;
        Log.Info($"游戏退出，退出码 {exit}，运行 {sw.Elapsed.TotalSeconds:0} 秒");

        string[] tailLines;
        lock (tail) tailLines = tail.ToArray();

        return new LaunchResult(exit, gameLog, exit == 0 ? null : DiagnoseCrash(exit, tailLines, sw.Elapsed));
    }

    private static bool IsWindowReadyLine(string line)
        => line.Contains("Backend library: LWJGL", StringComparison.Ordinal)
        || line.Contains("Narrator library for x64 successfully loaded", StringComparison.Ordinal)
        || line.Contains("Created: ", StringComparison.Ordinal) && line.Contains("minecraft:textures", StringComparison.Ordinal);

    private string BuildClasspath(VersionJson version, string versionDir)
    {
        var parts = new List<string>(version.Libraries.Count + 1);
        foreach (var lib in version.Libraries)
            parts.Add(Path.Combine(_paths.LibrariesDir, lib.Path.Replace('/', Path.DirectorySeparatorChar)));
        // 本体 jar 排最后，BootstrapLauncher 通过 -DignoreList 把它排除在模块层之外
        parts.Add(Path.Combine(versionDir, version.Id + ".jar"));
        return string.Join(';', parts);
    }

    private IEnumerable<string> MemoryAndGcArgs()
    {
        var mb = _settings.EffectiveMaxMemoryMb();
        Log.Info($"堆上限 {mb} MB");
        yield return $"-Xmx{mb}M";
        // 不设 -Xms：让 G1 自己长。预分配整块堆在 Windows 上会拖慢启动，
        // 而且真正的启动慢从来不是堆的问题。
        yield return "-XX:+UnlockExperimentalVMOptions";
        yield return "-XX:+UseG1GC";
        yield return "-XX:G1NewSizePercent=20";
        yield return "-XX:G1ReservePercent=20";
        yield return "-XX:MaxGCPauseMillis=50";
        yield return "-XX:G1HeapRegionSize=32M";
        yield return "-XX:+DisableExplicitGC";
        // 405 个模组，元空间实测占到 520M，给足避免反复扩容
        yield return "-XX:MetaspaceSize=256M";
    }

    private IEnumerable<string> DiagnosticArgs()
    {
        var stamp = DateTime.Now.ToString("yyyyMMdd-HHmmss");
        yield return $"-XX:ErrorFile={Path.Combine(_paths.CrashDir, $"hs_err_{stamp}_%p.log")}";
        yield return "-XX:+HeapDumpOnOutOfMemoryError";
        yield return $"-XX:HeapDumpPath={_paths.CrashDir}";
        // 中文日志不乱码
        yield return "-Dfile.encoding=UTF-8";
        yield return "-Dstdout.encoding=UTF-8";
        yield return "-Dstderr.encoding=UTF-8";
        yield return "-Dsun.stdout.encoding=UTF-8";
        yield return "-Dsun.stderr.encoding=UTF-8";
    }

    private IEnumerable<string> ExtraArgs()
    {
        if (string.IsNullOrWhiteSpace(_settings.ExtraJvmArgs)) yield break;
        foreach (var a in SplitArgs(_settings.ExtraJvmArgs))
            yield return a;
    }

    /// <summary>按空格拆分但尊重引号，玩家写 -Dfoo="a b" 不会被拆坏。</summary>
    public static IEnumerable<string> SplitArgs(string input)
    {
        var sb = new StringBuilder();
        var quoted = false;
        foreach (var c in input)
        {
            if (c == '"') { quoted = !quoted; continue; }
            if (char.IsWhiteSpace(c) && !quoted)
            {
                if (sb.Length > 0) { yield return sb.ToString(); sb.Clear(); }
                continue;
            }
            sb.Append(c);
        }
        if (sb.Length > 0) yield return sb.ToString();
    }

    private void LogCommandLine(JavaInstall java, List<string> args, string classpath)
    {
        // 完整命令行写进启动器日志，但 classpath 有 144 项、上万字符，
        // 单独存一份文件，日志里只留摘要
        var cpFile = Path.Combine(_paths.LogDir, "classpath.txt");
        try { File.WriteAllText(cpFile, classpath.Replace(";", Environment.NewLine)); } catch { }

        var shown = args.Select(a => a == classpath ? $"<classpath: {classpath.Count(c => c == ';') + 1} 项，见 classpath.txt>" : a);
        Log.Info($"Java：{java.Path}（{java.Version}）");
        Log.Debug("命令行：" + java.Path + " " + string.Join(' ', shown));
    }

    /// <summary>
    /// 把退出码和日志尾巴翻译成人话。
    /// 这里的每一条都来自实际踩过的坑。
    /// </summary>
    public static string DiagnoseCrash(int exitCode, IReadOnlyList<string> tail, TimeSpan uptime)
    {
        var text = string.Join('\n', tail);

        if (text.Contains("java.lang.OutOfMemoryError", StringComparison.Ordinal))
            return "内存不足。到设置里把内存上限调高，或者关掉一些后台程序。";

        if (text.Contains("Mixin apply failed", StringComparison.Ordinal) ||
            text.Contains("MixinApplyError", StringComparison.Ordinal))
            return "模组冲突（Mixin 注入失败）。多半是有文件损坏，试试设置里的「强制校验全部文件」。";

        if (text.Contains("Missing or unsupported mandatory dependencies", StringComparison.Ordinal) ||
            text.Contains("Incompatible mods found", StringComparison.Ordinal))
            return "模组依赖不完整。试试设置里的「强制校验全部文件」重新同步。";

        if (text.Contains("Failed to create window", StringComparison.Ordinal) ||
            text.Contains("GLFW error", StringComparison.Ordinal) ||
            text.Contains("Couldn't initialize GLFW", StringComparison.Ordinal))
            return "显卡初始化失败。更新显卡驱动，或者确认没有在远程桌面里启动游戏。";

        if (text.Contains("UnsupportedClassVersionError", StringComparison.Ordinal))
            return "Java 版本不对。本包需要 Java 21，到设置里重新选一个。";

        return exitCode switch
        {
            0 => "正常退出。",
            1 => "游戏自己报错退出了，看下面的日志尾部。",
            -1 or 255 when uptime < TimeSpan.FromMinutes(5) =>
                "进程被外部强制结束了。常见原因是第三方「内存优化 / 加速」工具在清理进程内存，" +
                "或者杀毒软件拦了 Java。这个启动器本身不做任何内存优化。",
            -1073741819 => "访问冲突（0xC0000005），通常是显卡驱动问题。更新驱动后重试。",
            -1073740791 => "堆栈溢出（0xC0000409）。",
            _ => $"异常退出码 {exitCode}。完整日志见 {"logs/game.log"}。",
        };
    }

    public static string ThisVersion()
        => typeof(GameLauncher).Assembly.GetName().Version?.ToString(3) ?? "1.0.0";
}
