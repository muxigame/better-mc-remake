using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Text;

namespace BatterMC.Protocol;

/// <summary>
/// 端侧隧道工具之间的线路协议。
///
/// 两端各跑一个工具：服务端侧守在 Minecraft 机器上，客户端侧随启动器发布。
/// Minecraft 完全不参与这条链路——它只在数据连接真正到来时，被服务端侧工具
/// 用一条本机连接接进来。所以控制连接可以在玩家读条期间一直挂着，不会被
/// Minecraft 的空闲超时干掉。
///
/// 连接分两种：
///   控制连接  启动游戏前建立，靠心跳保活，顺带维持 NAT 映射
///   数据连接  游戏每建立一次连接就新开一条，带上控制连接给的会话号
///
/// 不做多路复用：一条数据连接对应一条游戏连接，少一层缓冲和乱序的坑。
/// </summary>
public static class TunnelProtocol
{
    /// <summary>"MUXI" —— 让错连到这个端口的扫描器立刻被拒，不至于挂着。</summary>
    public const uint Magic = 0x4D555849;
    public const byte Version = 1;

    /// <summary>心跳间隔。要远小于常见 NAT 映射老化时间（很多家用路由 30–60s）。</summary>
    public static readonly TimeSpan HeartbeatInterval = TimeSpan.FromSeconds(20);

    /// <summary>超过这个时间没收到对端任何帧就判定链路已死。</summary>
    public static readonly TimeSpan HeartbeatTimeout = TimeSpan.FromSeconds(70);

    public enum Frame : byte
    {
        /// <summary>控制连接握手：客户端 → 服务端。</summary>
        Hello = 1,
        /// <summary>握手应答，带会话号。</summary>
        Welcome = 2,
        /// <summary>心跳。</summary>
        Ping = 3,
        Pong = 4,
        /// <summary>数据连接开场：客户端 → 服务端，带会话号。</summary>
        OpenStream = 5,
        /// <summary>服务端已接上本地 Minecraft，之后是裸字节转发。</summary>
        StreamReady = 6,
        /// <summary>拒绝，后跟一段 UTF-8 原因。</summary>
        Reject = 7,
    }

    /// <summary>
    /// 鉴权用的是「共享密钥 + 服务端下发的随机数」算 HMAC，不是把密钥本身发出去。
    /// 隧道跑在公网上，密钥不能明文过线。
    /// </summary>
    public static byte[] Sign(byte[] secret, byte[] nonce)
    {
        using var hmac = new HMACSHA256(secret);
        return hmac.ComputeHash(nonce);
    }

    public static bool VerifySignature(byte[] secret, byte[] nonce, byte[] signature)
    {
        var expected = Sign(secret, nonce);
        return CryptographicOperations.FixedTimeEquals(expected, signature);
    }

    public static byte[] DeriveSecret(string token) =>
        SHA256.HashData(Encoding.UTF8.GetBytes("muxi-tunnel-v1:" + token));

    /// <summary>客户端 token 的换代周期，必须和控制面的 CLIENT_TOKEN_PERIOD 一致。</summary>
    public const long ClientTokenPeriodSeconds = 86400;

    /// <summary>
    /// 从 master 派生出发给客户端的 token。
    ///
    /// 客户端不能持有 master：那同时是服务端侧工具调控制面 /register 的凭据，
    /// 泄露一份就能把全体玩家的候选入口改指到别人机器上。派生出来的这个只够连
    /// 那台机器上的 Minecraft，而那本来就是公开可进的。
    /// </summary>
    public static string DeriveClientToken(string masterToken, DateTimeOffset now, int offset = 0)
    {
        var bucket = now.ToUnixTimeSeconds() / ClientTokenPeriodSeconds + offset;
        using var hmac = new HMACSHA256(DeriveSecret(masterToken));
        return Convert.ToHexString(
            hmac.ComputeHash(Encoding.ASCII.GetBytes($"muxi-client-v1|{bucket}")))
            .ToLowerInvariant();
    }

    /// <summary>
    /// 当前认的客户端 token：本周期加上一周期。跨周期那一刻玩家手里可能还是上一代，
    /// 多认一代就不会在换代瞬间被踢下线。
    /// </summary>
    public static string[] AcceptedClientTokens(string masterToken, DateTimeOffset now) =>
        [DeriveClientToken(masterToken, now), DeriveClientToken(masterToken, now, -1)];

    // ---------------------------------------------------------------- 帧编解码

    public static async Task WriteFrameAsync(
        Stream stream, Frame frame, ReadOnlyMemory<byte> payload, CancellationToken ct)
    {
        if (payload.Length > ushort.MaxValue)
            throw new ArgumentOutOfRangeException(nameof(payload), "帧体过长");
        var header = new byte[3];
        header[0] = (byte)frame;
        BinaryPrimitives.WriteUInt16BigEndian(header.AsSpan(1), (ushort)payload.Length);
        await stream.WriteAsync(header, ct).ConfigureAwait(false);
        if (!payload.IsEmpty) await stream.WriteAsync(payload, ct).ConfigureAwait(false);
        await stream.FlushAsync(ct).ConfigureAwait(false);
    }

    public static async Task<(Frame Frame, byte[] Payload)> ReadFrameAsync(
        Stream stream, CancellationToken ct)
    {
        var header = new byte[3];
        await stream.ReadExactlyAsync(header, ct).ConfigureAwait(false);
        var length = BinaryPrimitives.ReadUInt16BigEndian(header.AsSpan(1));
        var payload = length == 0 ? [] : new byte[length];
        if (length > 0) await stream.ReadExactlyAsync(payload, ct).ConfigureAwait(false);
        return ((Frame)header[0], payload);
    }

    /// <summary>每条连接开头都要先对上这 5 个字节，省得把别的协议当自己人。</summary>
    public static byte[] Preamble()
    {
        var preamble = new byte[5];
        BinaryPrimitives.WriteUInt32BigEndian(preamble, Magic);
        preamble[4] = Version;
        return preamble;
    }

    public static bool PreambleMatches(byte[] candidate) =>
        candidate.Length == 5
        && BinaryPrimitives.ReadUInt32BigEndian(candidate) == Magic
        && candidate[4] == Version;
}
