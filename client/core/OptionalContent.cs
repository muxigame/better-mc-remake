using BatterMC.Protocol;

namespace BatterMC.Core;

/// <summary>
/// 可选内容的启用/禁用。
///
/// 禁用**不删文件**，只在文件名后面加 <c>.disabled</c> —— FML 只认 <c>.jar</c>，
/// 改个名字它就不加载了。这样开关是一次改名：随时切、不重下、断网也能切，
/// 玩家也不会因为手滑取消勾选就丢掉几百兆。
///
/// 「哪些内容是可选的」由服务端清单说了算（<see cref="ManagedFile.IsOptional"/>），
/// 跟着同步走；玩家只决定每一项开还是关。多数可选项默认关，
/// <see cref="ManagedFile.OptionalDefaultOn"/> 的默认开。
/// </summary>
public static class OptionalContent
{
    public const string DisabledSuffix = ".disabled";

    /// <summary>该文件此刻应该叫什么（相对游戏目录）。</summary>
    public static string PathFor(string manifestPath, bool enabled)
        => enabled ? manifestPath : manifestPath + DisabledSuffix;

    public static bool IsEnabled(IEnumerable<string> enabledPaths, string manifestPath)
        => enabledPaths.Contains(manifestPath, StringComparer.OrdinalIgnoreCase);

    /// <summary>
    /// 这个文件此刻该不该生效。非可选文件永远生效；默认关的看玩家勾没勾（enabled），
    /// 默认开的看玩家关没关（disabled）。两个集合都要忽略大小写。
    /// </summary>
    public static bool IsWanted(ManagedFile file, ISet<string> enabled, ISet<string> disabled)
        => !file.IsOptional || (file.OptionalDefaultOn ? !disabled.Contains(file.Path) : enabled.Contains(file.Path));

    public static bool IsWanted(ManagedFile file, LauncherSettings settings)
        => IsWanted(file,
            new HashSet<string>(settings.EnabledOptional, StringComparer.OrdinalIgnoreCase),
            new HashSet<string>(settings.DisabledOptional, StringComparer.OrdinalIgnoreCase));

    /// <summary>只给默认关的可选项用的旧入口：没有"关掉的默认开启项"。</summary>
    public static int Apply(LauncherPaths paths, IEnumerable<ManagedFile> files, IEnumerable<string> enabledPaths)
        => Apply(paths, files, enabledPaths, []);

    /// <summary>
    /// 立刻把磁盘上的可选内容调整成玩家要的状态。只改名，不下载也不删除。
    /// 文件还没装上的（玩家从没同步过）跳过，等下次同步下下来时自然落在正确的名字上。
    /// </summary>
    public static int Apply(LauncherPaths paths, IEnumerable<ManagedFile> files,
        IEnumerable<string> enabledPaths, IEnumerable<string> disabledPaths)
    {
        var enabled = new HashSet<string>(enabledPaths, StringComparer.OrdinalIgnoreCase);
        var disabled = new HashSet<string>(disabledPaths, StringComparer.OrdinalIgnoreCase);
        var changed = 0;

        foreach (var file in files.Where(f => f.IsOptional))
        {
            var on = IsWanted(file, enabled, disabled);
            var want = paths.ResolveGameFile(PathFor(file.Path, on));
            var other = paths.ResolveGameFile(PathFor(file.Path, !on));

            try
            {
                if (File.Exists(want))
                {
                    // 两个名字同时存在只可能是上次切换中途挂了，留正确的那个
                    if (File.Exists(other)) { File.Delete(other); changed++; }
                    continue;
                }
                if (!File.Exists(other)) continue;

                Directory.CreateDirectory(Path.GetDirectoryName(want)!);
                File.Move(other, want);
                changed++;
                Log.Info($"可选内容 {(on ? "启用" : "禁用")}：{file.Path}");
            }
            catch (Exception ex)
            {
                Log.Warn($"切换可选内容失败 {file.Path}：{ex.Message}");
            }
        }
        return changed;
    }
}
