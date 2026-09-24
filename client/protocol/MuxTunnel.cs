using System.Net;

namespace BatterMC.Protocol;

/// <summary>
/// 把一条多路复用连接包装成隧道。打洞出来的 TCP 走这条。
///
/// 和 QUIC 那条的区别只在数据面：QUIC 自带多流，TCP 只有一条字节流，多路复用
/// 由 <see cref="MuxConnection"/> 补上。对上层来说两者都是"能开流的隧道"。
/// </summary>
public sealed class MuxTunnel(MuxConnection mux, IPEndPoint peer, string kind) : IMuxiTunnel
{
    public bool IsAlive => mux.IsAlive;

    public string Describe => $"P2P 直连（{kind}） {peer}";

    public Task<Stream> OpenStreamAsync(CancellationToken ct) => mux.OpenAsync(ct);

    public ValueTask DisposeAsync() => mux.DisposeAsync();
}
