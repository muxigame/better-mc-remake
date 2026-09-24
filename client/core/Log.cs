using System.Text;

namespace BatterMC.Core;

/// <summary>
/// 极简日志。写文件 + 回调给 UI。没有第三方依赖，启动零开销。
/// </summary>
public static class Log
{
    private static readonly object Gate = new();
    private static StreamWriter? _writer;

    /// <summary>每写一行都会触发，UI 用它把日志实时推到界面上。</summary>
    public static event Action<string, string>? Line;

    public static string? FilePath { get; private set; }

    public static void Init(string logDir)
    {
        try
        {
            Directory.CreateDirectory(logDir);
            Rotate(logDir);
            FilePath = Path.Combine(logDir, "launcher.log");
            var fs = new FileStream(FilePath, FileMode.Create, FileAccess.Write, FileShare.ReadWrite);
            lock (Gate)
            {
                _writer?.Dispose();
                _writer = new StreamWriter(fs, new UTF8Encoding(false)) { AutoFlush = true };
            }
            Info($"日志：{FilePath}");
        }
        catch { /* 日志本身失败不应该拖垮启动器 */ }
    }

    /// <summary>保留最近 5 份历史日志，方便回溯上一次启动。</summary>
    private static void Rotate(string logDir)
    {
        try
        {
            var current = Path.Combine(logDir, "launcher.log");
            if (File.Exists(current))
            {
                var stamp = File.GetLastWriteTime(current).ToString("yyyyMMdd-HHmmss");
                File.Move(current, Path.Combine(logDir, $"launcher-{stamp}.log"), overwrite: true);
            }
            var old = new DirectoryInfo(logDir).GetFiles("launcher-*.log")
                .OrderByDescending(f => f.LastWriteTimeUtc).Skip(5);
            foreach (var f in old) { try { f.Delete(); } catch { } }
        }
        catch { }
    }

    public static void Info(string msg) => Write("INFO", msg);
    public static void Warn(string msg) => Write("WARN", msg);
    public static void Error(string msg) => Write("ERROR", msg);
    public static void Error(string msg, Exception ex) => Write("ERROR", msg + " :: " + ex);
    public static void Debug(string msg) => Write("DEBUG", msg);

    private static void Write(string level, string msg)
    {
        // 带毫秒。秒级精度在排查连接建立时完全不够用——打洞、预热、QUIC 握手这几步
        // 加起来才几百毫秒到两秒，秒级时间戳会把它们压成同一个刻度，看不出时间花在哪。
        var line = $"[{DateTime.Now:HH:mm:ss.fff}] [{level}] {msg}";
        lock (Gate)
        {
            try { _writer?.WriteLine(line); } catch { }
        }
        try { Line?.Invoke(level, msg); } catch { }
    }

    public static void Close()
    {
        lock (Gate) { try { _writer?.Flush(); _writer?.Dispose(); } catch { } _writer = null; }
    }
}
