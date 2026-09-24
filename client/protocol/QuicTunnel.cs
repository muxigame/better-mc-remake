using System.Net;
using System.Net.Quic;
using System.Runtime.Versioning;
using System.Net.Security;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;

namespace BatterMC.Protocol;

/// <summary>
/// 打洞成功之后的数据面：在同一个本地端口上起 QUIC。
///
/// 为什么是 QUIC 而不是自己写可靠层：Minecraft 说的是 TCP，装进 UDP 就得自己
/// 做重传、排序、拥塞控制。这些东西写错的表现是"偶尔卡一下"，在游戏里极难复现
/// 也极难定位。QUIC 这几样都已经调好了，还白送 TLS 加密和多路复用——多条 MC
/// 连接可以共用一条隧道，不用再为每条连接重新打洞。
///
/// 关键技巧是**复用打洞时那个本地端口**：NAT 映射是按 (本地端口, 协议) 记的，
/// 所以裸 socket 打通之后立刻关掉、让 QUIC 绑同一个端口，映射还在，握手就直接
/// 穿过刚打好的洞。中间不能拖太久，运营商 NAT 的 UDP 映射老化只有几十秒。
///
/// 认证不靠证书：两端是自签证书，没有 CA 可验。身份由共享密钥的 HMAC 握手在
/// QUIC 流里完成（见 <see cref="TunnelProtocol"/>）。TLS 负责加密，密钥负责认证，
/// 各管一段。
/// </summary>
[SupportedOSPlatform("windows")]
[SupportedOSPlatform("linux")]
[SupportedOSPlatform("macos")]
public static class QuicTunnel
{
    /// <summary>
    /// msquic 的实际加载情况，出问题时不用猜。
    ///
    /// Windows 上 .NET 只在 msquic 报告自己用 SChannel 时才检查"系统版本 ≥ 10.0.20145"，
    /// 而 Win10 达不到那个版本——所以我们随包带的是 OpenSSL 编译的 msquic，那道门禁
    /// 整段跳过。万一带错了版本（比如被系统目录里的 SChannel 版顶掉），这行日志会
    /// 直接说出来，不会再表现成一句没头没尾的"本机不支持 QUIC"。
    ///
    /// 走反射是因为这些字段 .NET 没公开。读不到就算了，只是少一条诊断信息。
    /// </summary>
    public static string DescribeSupport()
    {
        if (!IsSupported) return "QUIC 不可用：" + (Reflect("NotSupportedReason") ?? "原因未知");
        var backend = Reflect("UsesSChannelBackend") switch
        {
            "True" => "SChannel（依赖系统 TLS，Win10 上不可用）",
            "False" => "OpenSSL（随包自带，不看系统版本）",
            _ => "未知",
        };
        return $"QUIC 可用，msquic {Reflect("MsQuicLibraryVersion") ?? "?"}，后端 {backend}";
    }

    // 这里读的是 .NET 自己的内部字段，裁剪分析看不懂；本方法只用于诊断，
    // 读不到就返回 null，不影响任何功能。
    [System.Diagnostics.CodeAnalysis.UnconditionalSuppressMessage("Trimming", "IL2026",
        Justification = "仅用于诊断输出，失败时降级为 null")]
    [System.Diagnostics.CodeAnalysis.UnconditionalSuppressMessage("Trimming", "IL2075",
        Justification = "仅用于诊断输出，失败时降级为 null")]
    private static string? Reflect(string name)
    {
        try
        {
            var type = typeof(System.Net.Quic.QuicConnection).Assembly
                .GetType("System.Net.Quic.MsQuicApi");
            var value = type?.GetProperty(name,
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static)
                ?.GetValue(null);
            return value?.ToString();
        }
        catch { return null; }
    }

    public const string Alpn = "muxi-tunnel/1";

    /// <summary>
    /// 空闲超时就是"多久收不到对端任何包就判这条连接死了"。
    ///
    /// 保活开着的时候，连接在链路活着时**永远不会空闲**——每个保活包都会换回一个 ACK。
    /// 所以这个值管的其实只有一件事：链路真断了之后多久才发现。原先是 10 分钟，意味着
    /// 洞塌了（NAT 映射被回收、蜂窝换了出口）之后，这条 QUIC 还要"活"10 分钟：
    /// IsAlive 照报 true，玩家掉线重连时新开的流全灌进黑洞，每次都要等 Minecraft 自己
    /// 30 秒超时，而保温又被正在进行的连接挡着不探活——等于这 10 分钟里进不了服。
    ///
    /// 30 秒和 Minecraft 自己的读超时对齐：链路断得比这更久，游戏那条连接本来也保不住。
    /// 双方各报一个值，QUIC 取较小的那个，所以只改客户端就对所有 agent 生效。
    /// </summary>
    public static readonly TimeSpan IdleTimeout = TimeSpan.FromSeconds(30);

    /// <summary>
    /// 保活间隔。空闲超时的六分之一：连丢五个保活才会误判，而每个保活只有几十字节。
    /// 它同时也在替我们续 NAT 映射（运营商那边 UDP 映射实测约 30 秒老化）。
    /// </summary>
    public static readonly TimeSpan KeepAliveInterval = TimeSpan.FromSeconds(5);

    public static bool IsSupported => QuicConnection.IsSupported && QuicListener.IsSupported;

    /// <summary>
    /// 进程内共用的那一张自签证书。
    ///
    /// 服务端侧原先**每次打洞之后**都现生成一张（PunchService 的 ServeAsync 里
    /// <c>using var certificate = CreateEphemeralCertificate()</c>），实测 71~134ms。
    /// 这段 CPU 正好卡在"打洞成功"和"QUIC 监听起来"之间，而对端那一刻已经在发
    /// QUIC Initial 了——晚就绪一毫秒都是它第一个包被丢、然后等约一秒 PTO 重传的风险。
    ///
    /// 有效期一年，长驻进程用得完；换进程自然换证书。身份校验本来就在应用层的
    /// HMAC 握手里做，证书只是 TLS 必须有一张，复用它不降低任何安全性。
    ///
    /// 注意：这张证书**不要 Dispose**。它是进程级共享的，谁 Dispose 谁就把后面
    /// 所有会话的 TLS 弄坏。
    /// </summary>
    private static readonly Lazy<X509Certificate2> SharedCertificate =
        new(CreateEphemeralCertificate, LazyThreadSafetyMode.ExecutionAndPublication);

    /// <summary>进程内共用的自签证书，见 <see cref="SharedCertificate"/>。别 Dispose。</summary>
    public static X509Certificate2 SharedEphemeralCertificate => SharedCertificate.Value;

    /// <summary>
    /// 自签证书。没有 CA，也不需要——校验在应用层做。
    /// 不落盘，省得多一个要轮换的东西。
    ///
    /// 想复用请走 <see cref="SharedEphemeralCertificate"/>：这个方法每次调用都要
    /// 花 71~134ms 做 RSA-2048 加一次 PFX 往返。
    /// </summary>
    public static X509Certificate2 CreateEphemeralCertificate()
    {
        using var key = RSA.Create(2048);
        var request = new CertificateRequest(
            "CN=muxi-tunnel", key, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        request.CertificateExtensions.Add(
            new X509BasicConstraintsExtension(false, false, 0, false));
        request.CertificateExtensions.Add(new X509EnhancedKeyUsageExtension(
            [new Oid("1.3.6.1.5.5.7.3.1")], false)); // serverAuth
        var now = DateTimeOffset.UtcNow;
        using var certificate = request.CreateSelfSigned(now.AddMinutes(-5), now.AddYears(1));
        // 必须导出成 PFX 再读回来：Windows 上 msquic 走 Schannel，而 Schannel 用不了
        // CreateSelfSigned 直接产出的那种内存密钥，握手会以 TLS UserCanceled 告终。
        // 也不能带 EphemeralKeySet——同样的原因，Schannel 需要一个真正的密钥容器。
        return X509CertificateLoader.LoadPkcs12(
            certificate.Export(X509ContentType.Pfx), null,
            X509KeyStorageFlags.Exportable);
    }

    /// <summary>
    /// 被连的一侧：在 <paramref name="localPort"/> 上等对端的 QUIC 握手。
    ///
    /// <paramref name="family"/> 要和打洞时那个 socket 的地址族一致。实测 msquic
    /// 在 Windows 上绑 <c>[::]</c> 再接 IPv4 客户端会在握手阶段回 TLS
    /// <c>UserCanceled</c>，所以这里按实际族绑，不依赖双栈。
    /// </summary>
    public static async Task<QuicListener> ListenAsync(
        int localPort, X509Certificate2 certificate,
        System.Net.Sockets.AddressFamily family, CancellationToken ct)
    {
        var any = family == System.Net.Sockets.AddressFamily.InterNetworkV6
            ? IPAddress.IPv6Any
            : IPAddress.Any;
        var options = new QuicListenerOptions
        {
            ListenEndPoint = new IPEndPoint(any, localPort),
            ApplicationProtocols = [new SslApplicationProtocol(Alpn)],
            ConnectionOptionsCallback = (_, _, _) => ValueTask.FromResult(
                new QuicServerConnectionOptions
                {
                    DefaultStreamErrorCode = 0x0A,
                    DefaultCloseErrorCode = 0x0B,
                    IdleTimeout = IdleTimeout,
                    // 监听侧也发 keepalive：只靠发起侧单边维持，对面一旦哑了就只能
                    // 干等空闲超时，而那段时间玩家正在读条、链路上本来就没有数据。
                    KeepAliveInterval = KeepAliveInterval,
                    // QUIC 的流额度是**累计**的，不显式给足的话客户端开不了几条就被拒。
                    // 一局游戏里并发的流不多（每条 Minecraft 连接一条，外加保温探活），
                    // 但累计数会随时间增长，所以给一个宽裕的值。实测不设这个的后果是
                    // 第三条流被 QUIC_STATUS_ABORTED，而保温据此误判隧道已死。
                    MaxInboundBidirectionalStreams = 256,
                    ServerAuthenticationOptions = new SslServerAuthenticationOptions
                    {
                        ApplicationProtocols = [new SslApplicationProtocol(Alpn)],
                        ServerCertificate = certificate,
                        ClientCertificateRequired = false,
                    },
                }),
        };
        return await QuicListener.ListenAsync(options, ct).ConfigureAwait(false);
    }

    /// <summary>发起的一侧：从 <paramref name="localPort"/> 连到打洞确认的对端地址。</summary>
    public static async Task<QuicConnection> ConnectAsync(
        int localPort, IPEndPoint peer, CancellationToken ct)
    {
        var options = new QuicClientConnectionOptions
        {
            // 必须和打洞用的是同一个本地端口，否则 NAT 映射对不上，等于白打
            LocalEndPoint = new IPEndPoint(
                peer.AddressFamily == System.Net.Sockets.AddressFamily.InterNetworkV6
                    ? IPAddress.IPv6Any
                    : IPAddress.Any,
                localPort),
            RemoteEndPoint = peer,
            DefaultStreamErrorCode = 0x0A,
            DefaultCloseErrorCode = 0x0B,
            IdleTimeout = IdleTimeout,
            KeepAliveInterval = KeepAliveInterval,
            MaxInboundBidirectionalStreams = 0,
            ClientAuthenticationOptions = new SslClientAuthenticationOptions
            {
                ApplicationProtocols = [new SslApplicationProtocol(Alpn)],
                TargetHost = "muxi-tunnel",
                // 自签证书，没有可验的信任链；真正的身份校验由流内的 HMAC 握手完成
                RemoteCertificateValidationCallback = (_, _, _, _) => true,
            },
        };
        return await QuicConnection.ConnectAsync(options, ct).ConfigureAwait(false);
    }
}
