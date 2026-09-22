using BatterMC.Protocol;

namespace BatterMC.Core;

/// <summary>
/// 可选内容的启用/禁用。
///
/// 禁用**不删文件**，只在文件名后面加 <c>.disabled</c> —— FML 只认 <c>.jar</c>，
/// 改个名字它就不加载了。这样开关是一次改名：随时切、不重下、断网也能切，
/// 玩家也不会因为手滑取消勾选就丢掉几百兆。
///
/// 「哪些内容是可选的」由服务端清单说了算（<see cref="FilePolicy.Optional"/>），
/// 跟着同步走；玩家只决定每一项开还是关。
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
    /// 立刻把磁盘上的可选内容调整成玩家要的状态。只改名，不下载也不删除。
    /// 文件还没装上的（玩家从没同步过）跳过，等下次同步下下来时自然落在正确的名字上。
    /// </summary>
    public static int Apply(LauncherPaths paths, IEnumerable<ManagedFile> files, IEnumerable<string> enabledPaths)
    {
        var enabled = new HashSet<string>(enabledPaths, StringComparer.OrdinalIgnoreCase);
        var changed = 0;

        foreach (var file in files.Where(f => f.Policy == FilePolicy.Optional))
        {
            var on = enabled.Contains(file.Path);
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
