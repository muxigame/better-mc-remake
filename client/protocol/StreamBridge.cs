using System.Net.Quic;
using System.Net.Sockets;

namespace BatterMC.Protocol;

/// <summary>
/// 两条流之间的双向对拷，带收尾。
///
/// 原先两端都是"各拷各的，两个方向都结束才算完"，而且一个方向读到 EOF 之后不告诉
/// 另一头。后果实测撞上了：玩家那边的 Minecraft 断开，"游戏→隧道"方向结束，但隧道
/// 对面谁也不知道——服务端那条到 Minecraft 的连接继续开着，要等 Minecraft 自己 30 秒
/// 读超时才断（这期间玩家在服务器上还算"在线"）；客户端这边那条转发一直挂在连接表里，
/// 后台打好的 P2P 因为"还有游戏连接在用"永远切不过去。
///
/// 现在：任一方向结束，就把半关闭传给另一端（QUIC 流 CompleteWrites，TCP 发 FIN），
/// 另一方向最多再给 <c>linger</c> 把剩下的数据带完，到点就整条拆掉。半关闭传不过去
/// 的流（复用流只有整条关闭）或者对端是还不会收尾的旧版本，也会在宽限到期后被拆掉。
/// </summary>
public static class StreamBridge
{
    /// <summary>一个方向结束后，另一方向最多再等多久。</summary>
    public static readonly TimeSpan DefaultLinger = TimeSpan.FromSeconds(3);

    public static async Task RunAsync(Stream a, Stream b, TimeSpan linger, CancellationToken ct)
    {
        var aToB = CopyAsync(a, b, ct);
        var bToA = CopyAsync(b, a, ct);
        var first = await Task.WhenAny(aToB, bToA).ConfigureAwait(false);
        // 读完了哪一头，就告诉被写的那一头"我这边不会再有数据了"。
        HalfClose(first == aToB ? b : a);
        await Task.WhenAny(Task.WhenAll(aToB, bToA), Task.Delay(linger, ct)).ConfigureAwait(false);
    }

    private static async Task CopyAsync(Stream from, Stream to, CancellationToken ct)
    {
        try
        {
            await from.CopyToAsync(to, 64 * 1024, ct).ConfigureAwait(false);
            await to.FlushAsync(ct).ConfigureAwait(false);
        }
        catch (Exception error) when (error is IOException or SocketException
                                          or OperationCanceledException or ObjectDisposedException
                                          or InvalidOperationException
                                          || IsQuicException(error)) { }
    }

    private static bool IsQuicException(Exception error) =>
        (OperatingSystem.IsWindows() || OperatingSystem.IsLinux() || OperatingSystem.IsMacOS())
        && error is QuicException;

    /// <summary>只关写方向。做不到的流类型什么都不做，交给调用方到点整条拆。</summary>
    public static void HalfClose(Stream stream)
    {
        try
        {
            if ((OperatingSystem.IsWindows() || OperatingSystem.IsLinux() || OperatingSystem.IsMacOS())
                && stream is QuicStream quic)
                quic.CompleteWrites();
            else if (stream is NetworkStream network)
                network.Socket.Shutdown(SocketShutdown.Send);
        }
        catch (Exception error) when (error is IOException or SocketException
                                          or ObjectDisposedException or InvalidOperationException
                                          || IsQuicException(error)) { }
    }
}
