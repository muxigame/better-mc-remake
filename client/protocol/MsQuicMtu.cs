using System.Buffers.Binary;
using System.Diagnostics;
using System.Diagnostics.CodeAnalysis;
using System.Net.Quic;
using System.Reflection;
using System.Runtime.ExceptionServices;
using System.Runtime.InteropServices;

namespace BatterMC.Protocol;

/// <summary>
/// 把每条 QUIC 连接的包长锁在 <see cref="Mtu"/>，不让 msquic 自己往上探。
///
/// msquic 默认从 1248 起步，握手完成后不断试探更大的包，最大到 1500（UDP 载荷 1472）。
/// 试探包过去了，它就认定这条路能走这么大的包，而且**再也不往回退**。实测的线路恰好是
/// 这种：家宽到服务端，建立时 1452 字节的包全通、1472 字节丢 99%，偶尔一个大包漏过去；
/// 连接用了几十分钟后，同一条连接上 1300 字节的数据秒到，1400 字节以上一个字节都过不来。
/// 小包（保活、ACK、状态查询）照常来回，所以连接一直"活着"，谁都察觉不到——只有游戏
/// 数据停住，30 秒后 Minecraft 两头各报一次超时。那天"打洞成功、玩着玩着卡住"出现了好几次。
///
/// 1280 是 IPv6 规定每条链路都必须能过的最小 MTU，按 IPv4 算 UDP 载荷 1252，比那条线实测
/// 能过的 1300+ 还留了余量。代价是包数多一成左右，游戏流量感觉不到。
///
/// 为什么必须一条条连接去设：
/// <list type="bullet">
/// <item>全局设置不管用。实测把 msquic 全局设置改成 1280、读回来也是 1280，新建的连接照样是
///   1248~1500——.NET 建的连接不从全局继承这两项。</item>
/// <item>握手时告诉对端的 max_udp_payload_size 取的是本机网卡 MTU，不是这里的上限，所以一头
///   锁不住另一头：服务器往玩家的包要在 agent 那一侧锁，玩家往服务器的要在启动器这一侧锁。</item>
/// <item>msquic 只在"对端验证完成"那一刻按连接当时的设置算一次上限，之后再改不生效。所以
///   监听侧要在连接选项回调里设（那时还没握完），发起侧要在连接启动之前设。</item>
/// </list>
/// .NET 没有开放这个设置，这里拿 .NET 内部的连接句柄直接调 msquic。拿不到就不锁，
/// 连接照常建，只记一条原因。
/// </summary>
[System.Runtime.Versioning.SupportedOSPlatform("windows")]
[System.Runtime.Versioning.SupportedOSPlatform("linux")]
[System.Runtime.Versioning.SupportedOSPlatform("macos")]
public static class MsQuicMtu
{
    /// <summary>锁定的 MTU。起步值和上限都是它，于是没有试探空间。</summary>
    public const ushort Mtu = 1280;

    private const uint ApiVersion = 2;
    private const uint ParamGlobalSettings = 0x01000005;
    private const uint ParamConnSettings = 0x05000004;

    // QUIC_SETTINGS 里的位置（msquic 2.x 的 msquic.h，只追加不改动）：IsSetFlags 第 22、23 位，
    // 字段在第 102、104 字节。第一次用之前先读一份全局设置核对这两处像不像 MTU。
    private const int IsSetMinimumMtu = 22;
    private const int IsSetMaximumMtu = 23;
    private const int MinimumMtuOffset = 102;
    private const int MaximumMtuOffset = 104;

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int OpenVersionFn(uint version, out IntPtr api);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int SetParamFn(IntPtr handle, uint param, uint length, IntPtr buffer);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int GetParamFn(IntPtr handle, uint param, ref uint length, IntPtr buffer);

    private sealed record Api(SetParamFn SetParam, GetParamFn GetParam, int SettingsSize);

    private static readonly Lazy<(Api? Api, string Status)> Native = new(Open);

    /// <summary>锁定机制本身能不能用，给日志用。</summary>
    public static string Status => Native.Value.Status;

    /// <summary>
    /// 给一条还没完成握手的连接锁包长。监听侧在连接选项回调里调，发起侧见
    /// <see cref="ConnectAsync"/>。锁不上返回 false，连接照常用。
    /// </summary>
    public static bool Cap(QuicConnection connection)
    {
        var api = Native.Value.Api;
        if (api is null || HandleOf(connection) is not { } handle) return false;
        var settings = new byte[api.SettingsSize];
        BinaryPrimitives.WriteUInt64LittleEndian(settings, (1UL << IsSetMinimumMtu) | (1UL << IsSetMaximumMtu));
        BinaryPrimitives.WriteUInt16LittleEndian(settings.AsSpan(MinimumMtuOffset), Mtu);
        BinaryPrimitives.WriteUInt16LittleEndian(settings.AsSpan(MaximumMtuOffset), Mtu);
        var buffer = Marshal.AllocHGlobal(settings.Length);
        try
        {
            Marshal.Copy(settings, 0, buffer, settings.Length);
            return api.SetParam(handle, ParamConnSettings, (uint)settings.Length, buffer) >= 0;
        }
        finally { Marshal.FreeHGlobal(buffer); }
    }

    /// <summary>读一条连接实际在用的包长上下限，自检和诊断用。读不到返回 null。</summary>
    public static (int Min, int Max)? Read(QuicConnection connection)
    {
        var api = Native.Value.Api;
        if (api is null || HandleOf(connection) is not { } handle) return null;
        var bytes = Get(api.GetParam, handle, ParamConnSettings);
        if (bytes is null) return null;
        return (BinaryPrimitives.ReadUInt16LittleEndian(bytes.AsSpan(MinimumMtuOffset)),
                BinaryPrimitives.ReadUInt16LittleEndian(bytes.AsSpan(MaximumMtuOffset)));
    }

    /// <summary>
    /// 发起连接，并在连接启动之前锁住包长。
    ///
    /// <see cref="QuicConnection.ConnectAsync"/> 内部是"建连接对象 → 校验选项 → 启动握手"一口气
    /// 做完的，中间没有口子，而 msquic 在握手完成那一刻就按当时的设置定下了上限。所以这里用
    /// 反射把这三步拆开，在启动之前插进去锁。拆不开（.NET 内部改了）就退回公开接口，
    /// 这一侧不锁——服务器往玩家那个方向由 agent 锁，那才是出事的方向。
    /// </summary>
    [UnconditionalSuppressMessage("Trimming", "IL2075", Justification = "反射失败时退回公开接口")]
    [UnconditionalSuppressMessage("Trimming", "IL2026", Justification = "反射失败时退回公开接口")]
    public static async Task<QuicConnection> ConnectAsync(QuicClientConnectionOptions options, CancellationToken ct)
    {
        var type = typeof(QuicConnection);
        var ctor = type.GetConstructor(BindingFlags.NonPublic | BindingFlags.Instance, Type.EmptyTypes);
        var finish = type.GetMethod("FinishConnectAsync", BindingFlags.NonPublic | BindingFlags.Instance,
            [typeof(QuicClientConnectionOptions), typeof(CancellationToken)]);
        var validate = typeof(QuicConnectionOptions).GetMethod("Validate",
            BindingFlags.NonPublic | BindingFlags.Instance, [typeof(string)]);
        if (Native.Value.Api is null || ctor is null || finish is null || validate is null
            || finish.ReturnType != typeof(ValueTask))
        {
            return await QuicConnection.ConnectAsync(options, ct).ConfigureAwait(false);
        }

        // 和公开接口一样：先校验、补默认值（握手超时等都是在这一步填上的）
        try { validate.Invoke(options, [nameof(options)]); }
        catch (TargetInvocationException wrapped) when (wrapped.InnerException is { } inner)
        {
            ExceptionDispatchInfo.Throw(inner);
        }
        var connection = (QuicConnection)ctor.Invoke(null);
        try
        {
            Cap(connection);
            using var handshake = CancellationTokenSource.CreateLinkedTokenSource(ct);
            if (options.HandshakeTimeout > TimeSpan.Zero) handshake.CancelAfter(options.HandshakeTimeout);
            try
            {
                await ((ValueTask)finish.Invoke(connection, [options, handshake.Token])!).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (!ct.IsCancellationRequested && handshake.IsCancellationRequested)
            {
                throw new QuicException(QuicError.ConnectionTimeout, null, "QUIC 握手超时");
            }
            return connection;
        }
        catch (TargetInvocationException wrapped) when (wrapped.InnerException is { } inner)
        {
            await connection.DisposeAsync().ConfigureAwait(false);
            ExceptionDispatchInfo.Throw(inner);
            throw;
        }
        catch
        {
            await connection.DisposeAsync().ConfigureAwait(false);
            throw;
        }
    }

    [UnconditionalSuppressMessage("Trimming", "IL2075", Justification = "拿不到句柄就不锁")]
    private static IntPtr? HandleOf(QuicConnection connection)
    {
        try
        {
            var field = typeof(QuicConnection).GetField("_handle", BindingFlags.NonPublic | BindingFlags.Instance);
            return field?.GetValue(connection) is SafeHandle { IsInvalid: false, IsClosed: false } safe
                ? safe.DangerousGetHandle()
                : null;
        }
        catch { return null; }
    }

    private static (Api?, string) Open()
    {
        try
        {
            // 必须是 .NET 实际在用的那一份 msquic。按文件名加载会走程序目录，而框架依赖部署时
            // .NET 加载的是框架目录里的那份，同一进程里就会有两个 msquic。所以先让 .NET 把它的
            // 加载起来，再从已加载模块里按完整路径拿到同一份。
            if (!QuicConnection.IsSupported) return (null, "QUIC 不可用");
            var loaded = Process.GetCurrentProcess().Modules.Cast<ProcessModule>()
                .Where(m => m.ModuleName.Equals("msquic.dll", StringComparison.OrdinalIgnoreCase)
                            || m.ModuleName.StartsWith("libmsquic", StringComparison.Ordinal))
                .Select(m => m.FileName)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToList();
            if (loaded.Count != 1)
                return (null, loaded.Count == 0 ? "进程里找不到 msquic" : "进程里加载了多份 msquic");
            var open = Marshal.GetDelegateForFunctionPointer<OpenVersionFn>(
                NativeLibrary.GetExport(NativeLibrary.Load(loaded[0]), "MsQuicOpenVersion"));
            // 不调 MsQuicClose：这份引用一直留着，库不会在我们还要用的时候被卸掉。
            if (open(ApiVersion, out var table) < 0 || table == IntPtr.Zero) return (null, "打不开 msquic");
            var setParam = Marshal.GetDelegateForFunctionPointer<SetParamFn>(Marshal.ReadIntPtr(table, 3 * IntPtr.Size));
            var getParam = Marshal.GetDelegateForFunctionPointer<GetParamFn>(Marshal.ReadIntPtr(table, 4 * IntPtr.Size));

            var global = Get(getParam, IntPtr.Zero, ParamGlobalSettings);
            if (global is null) return (null, "读不到 msquic 设置");
            var min = BinaryPrimitives.ReadUInt16LittleEndian(global.AsSpan(MinimumMtuOffset));
            var max = BinaryPrimitives.ReadUInt16LittleEndian(global.AsSpan(MaximumMtuOffset));
            // 默认值各版本不一样（实测起步 1288 或 1248），上限 1500。只要像 MTU 就说明位置没读错。
            if (min is < 1248 or > 1500 || max < min || max > 9000)
                return (null, $"msquic 设置布局和预期不符（{min}/{max}）");
            return (new Api(setParam, getParam, global.Length), $"包长锁定 {Mtu}");
        }
        catch (Exception error)
        {
            return (null, "包长锁定不可用：" + error.Message);
        }
    }

    private static byte[]? Get(GetParamFn getParam, IntPtr handle, uint param)
    {
        uint length = 0;
        getParam(handle, param, ref length, IntPtr.Zero);
        if (length < MaximumMtuOffset + 2 || length > 4096) return null;
        var buffer = Marshal.AllocHGlobal((int)length);
        try
        {
            if (getParam(handle, param, ref length, buffer) < 0) return null;
            var bytes = new byte[length];
            Marshal.Copy(buffer, bytes, 0, (int)length);
            return bytes;
        }
        finally { Marshal.FreeHGlobal(buffer); }
    }
}
