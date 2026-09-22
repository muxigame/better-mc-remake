using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;

namespace BatterMC.Core;

/// <summary>磁盘上的登录态。</summary>
public sealed class AccountSession
{
    /// <summary>访问令牌，上游只给一小时。留着是为了刚登录完就重启时省一次往返。</summary>
    public string AccessToken { get; set; } = "";

    /// <summary>刷新令牌，上游给 30 天。真正要留住的是这个。</summary>
    public string RefreshToken { get; set; } = "";

    public DateTimeOffset SavedAt { get; set; }
}

/// <summary>
/// 把登录令牌落盘，让玩家不用每次开启动器都重登一次。
///
/// 令牌等价于账号本身，不能明文躺在 exe 旁边——启动器默认是便携模式，那个目录
/// 可能在 U 盘上、可能被同步到网盘。这里用 DPAPI 按当前 Windows 用户加密：
/// 换台机器、换个用户，文件都解不开，只会被当成"没登录"。
///
/// 没有引第三方包，直接调 crypt32。启动器是自包含单文件 + 裁剪发布，少一个依赖
/// 少一份出问题的可能。
/// </summary>
public static class AccountStore
{
    private const int CryptprotectUiForbidden = 0x1;

    /// <summary>
    /// 附加熵。不是密钥——DPAPI 的密钥来自用户登录凭据——只是把这份密文限定在
    /// 本启动器的用途上，别的程序就算拿到文件也不能用自己的 DPAPI 调用解开。
    /// </summary>
    private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("muxi-launcher-account-v1");

    private static readonly JsonSerializerOptions Opts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true,
        WriteIndented = false,
    };

    private static readonly CoreJsonContext Context = new(Opts);

    public static void Save(string file, AccountSession session)
    {
        var plain = JsonSerializer.SerializeToUtf8Bytes(session, Context.AccountSession);
        var cipher = Protect(plain);
        Array.Clear(plain);
        if (cipher is null)
        {
            Log.Warn("登录态加密失败，本次不落盘");
            return;
        }
        AtomicFile.WriteAllBytes(file, cipher);
    }

    /// <summary>读不出来一律当成没登录：这个文件坏了最多多登一次，不该让启动器起不来。</summary>
    public static AccountSession? Load(string file)
    {
        try
        {
            if (!File.Exists(file)) return null;
            var cipher = File.ReadAllBytes(file);
            var plain = Unprotect(cipher);
            if (plain is null) return null;
            try
            {
                var session = JsonSerializer.Deserialize(plain, Context.AccountSession);
                return string.IsNullOrEmpty(session?.RefreshToken) ? null : session;
            }
            finally { Array.Clear(plain); }
        }
        catch (Exception ex)
        {
            Log.Warn($"读取登录态失败，按未登录处理：{ex.Message}");
            return null;
        }
    }

    public static void Clear(string file)
    {
        try { if (File.Exists(file)) File.Delete(file); }
        catch (Exception ex) { Log.Warn($"清除登录态失败：{ex.Message}"); }
    }

    // ── DPAPI ────────────────────────────────────────────────────────────────

    [StructLayout(LayoutKind.Sequential)]
    private struct DataBlob
    {
        public int Size;
        public IntPtr Data;
    }

    [DllImport("crypt32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CryptProtectData(ref DataBlob input, string? description,
        ref DataBlob entropy, IntPtr reserved, IntPtr prompt, int flags, out DataBlob output);

    [DllImport("crypt32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CryptUnprotectData(ref DataBlob input, IntPtr description,
        ref DataBlob entropy, IntPtr reserved, IntPtr prompt, int flags, out DataBlob output);

    [DllImport("kernel32.dll")]
    private static extern IntPtr LocalFree(IntPtr handle);

    private static byte[]? Protect(byte[] plain) => Transform(plain, protect: true);

    private static byte[]? Unprotect(byte[] cipher) => Transform(cipher, protect: false);

    private static byte[]? Transform(byte[] bytes, bool protect)
    {
        var input = default(DataBlob);
        var entropy = default(DataBlob);
        var output = default(DataBlob);
        try
        {
            input = Alloc(bytes);
            entropy = Alloc(Entropy);
            var ok = protect
                ? CryptProtectData(ref input, null, ref entropy, IntPtr.Zero, IntPtr.Zero,
                    CryptprotectUiForbidden, out output)
                : CryptUnprotectData(ref input, IntPtr.Zero, ref entropy, IntPtr.Zero, IntPtr.Zero,
                    CryptprotectUiForbidden, out output);
            if (!ok) return null;

            var result = new byte[output.Size];
            Marshal.Copy(output.Data, result, 0, output.Size);
            return result;
        }
        catch (Exception ex)
        {
            Log.Warn($"DPAPI 调用失败：{ex.Message}");
            return null;
        }
        finally
        {
            Free(ref input);
            Free(ref entropy);
            if (output.Data != IntPtr.Zero) LocalFree(output.Data);
        }
    }

    private static DataBlob Alloc(byte[] bytes)
    {
        var blob = new DataBlob { Size = bytes.Length, Data = Marshal.AllocHGlobal(bytes.Length) };
        Marshal.Copy(bytes, 0, blob.Data, bytes.Length);
        return blob;
    }

    private static void Free(ref DataBlob blob)
    {
        if (blob.Data == IntPtr.Zero) return;
        // 明文在非托管内存里待过，先抹掉再还回去
        for (var i = 0; i < blob.Size; i++) Marshal.WriteByte(blob.Data, i, 0);
        Marshal.FreeHGlobal(blob.Data);
        blob.Data = IntPtr.Zero;
    }
}
