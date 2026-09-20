using System.Text;

namespace BatterMC.Core;

/// <summary>
/// 写文件时先写临时文件再改名。防止同步过程中断电/杀进程留下半个 jar，
/// 那种半截文件会让 NeoForge 在下次启动时直接崩在 mod 扫描阶段。
/// </summary>
public static class AtomicFile
{
    public static void WriteAllText(string path, string content)
        => WriteAllBytes(path, new UTF8Encoding(false).GetBytes(content));

    public static void WriteAllBytes(string path, byte[] bytes)
    {
        var dir = Path.GetDirectoryName(path)!;
        Directory.CreateDirectory(dir);
        var tmp = path + ".tmp-" + Guid.NewGuid().ToString("N")[..8];
        try
        {
            File.WriteAllBytes(tmp, bytes);
            Replace(tmp, path);
        }
        finally
        {
            if (File.Exists(tmp)) { try { File.Delete(tmp); } catch { } }
        }
    }

    /// <summary>把已经写好的临时文件就位。</summary>
    public static void Replace(string tempPath, string targetPath)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(targetPath)!);
        for (var attempt = 0; ; attempt++)
        {
            try
            {
                if (File.Exists(targetPath))
                {
                    // 清掉只读属性，否则 Move 会失败
                    try { File.SetAttributes(targetPath, FileAttributes.Normal); } catch { }
                }
                File.Move(tempPath, targetPath, overwrite: true);
                return;
            }
            catch (IOException) when (attempt < 5)
            {
                // 杀毒软件扫描中或者上一局游戏还攥着这个 jar，退让重试
                Thread.Sleep(120 * (attempt + 1));
            }
        }
    }
}
