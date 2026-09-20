namespace BatterMC.Core;

/// <summary>
/// 启动器的目录布局。默认便携：所有数据放在 exe 旁边。
/// 如果 exe 所在目录不可写（例如被装进 Program Files），自动退回 %LOCALAPPDATA%。
/// </summary>
public sealed class LauncherPaths
{
    public string Root { get; }
    /// <summary>游戏目录，相当于 .minecraft。</summary>
    public string GameDir { get; }
    /// <summary>启动器自己的数据。</summary>
    public string DataDir => Path.Combine(Root, "launcher");
    /// <summary>自动下载的 Java 运行时。</summary>
    public string RuntimeDir => Path.Combine(Root, "runtime");

    public string SettingsFile => Path.Combine(DataDir, "settings.json");
    public string StateFile => Path.Combine(DataDir, "state.json");
    public string ManifestCacheFile => Path.Combine(DataDir, "manifest.cache.json");
    public string LogDir => Path.Combine(DataDir, "logs");
    public string CrashDir => Path.Combine(DataDir, "crash");
    public string TempDir => Path.Combine(DataDir, "temp");

    public string AssetsDir => Path.Combine(GameDir, "assets");
    public string LibrariesDir => Path.Combine(GameDir, "libraries");
    public string VersionsDir => Path.Combine(GameDir, "versions");

    private LauncherPaths(string root, string? gameDirOverride)
    {
        Root = root;
        GameDir = string.IsNullOrWhiteSpace(gameDirOverride)
            ? Path.Combine(root, "Better MC Remake [FORGE]")
            : Path.GetFullPath(gameDirOverride);
    }

    public static LauncherPaths Resolve(string? gameDirOverride = null)
    {
        var exeDir = AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        var root = IsWritable(exeDir)
            ? exeDir
            : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "BatterMC5Remake");
        return new LauncherPaths(root, gameDirOverride);
    }

    /// <summary>显式指定根目录（测试 / 命令行用）。</summary>
    public static LauncherPaths At(string root, string? gameDirOverride = null)
        => new(Path.GetFullPath(root), gameDirOverride);

    /// <summary>建齐所有目录。真的要装游戏时才调，别在只跑自检的时候调。</summary>
    public void EnsureCreated()
    {
        foreach (var d in new[] { Root, GameDir, DataDir, RuntimeDir, LogDir, CrashDir, TempDir })
            Directory.CreateDirectory(d);
    }

    /// <summary>
    /// 只建启动器自己记日志需要的那两个目录。
    /// 进程一起来就调这个，免得 --selftest 之类的命令在发布目录里撒下一堆空文件夹。
    /// </summary>
    public void EnsureDataDir()
    {
        Directory.CreateDirectory(DataDir);
        Directory.CreateDirectory(LogDir);
    }

    /// <summary>把清单里的正斜杠相对路径解析成游戏目录下的绝对路径，并拦截目录穿越。</summary>
    public string ResolveGameFile(string relative)
    {
        if (string.IsNullOrWhiteSpace(relative))
            throw new ArgumentException("空路径", nameof(relative));
        var normalized = relative.Replace('\\', '/').TrimStart('/');
        if (normalized.Split('/').Any(seg => seg is ".." or "." or ""))
            throw new InvalidOperationException($"清单里有非法路径：{relative}");
        var full = Path.GetFullPath(Path.Combine(GameDir, normalized.Replace('/', Path.DirectorySeparatorChar)));
        var gameRoot = Path.GetFullPath(GameDir) + Path.DirectorySeparatorChar;
        if (!full.StartsWith(gameRoot, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException($"路径逃逸出游戏目录：{relative}");
        return full;
    }

    private static bool IsWritable(string dir)
    {
        try
        {
            Directory.CreateDirectory(dir);
            var probe = Path.Combine(dir, ".write-probe-" + Guid.NewGuid().ToString("N")[..8]);
            File.WriteAllText(probe, "x");
            File.Delete(probe);
            return true;
        }
        catch { return false; }
    }
}
