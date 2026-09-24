using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Text;

namespace BatterMC.Protocol;

/// <summary>
/// 打洞握手包。
///
/// 这些包会被发到十几个候选地址上，其中大部分是打不通的——NAT 把它们丢掉就好。
/// 但打通的那一个会被对端收到，所以必须能证明"这是我们自己人"：包体带 HMAC，
/// 用的是和隧道同一把共享密钥。否则任何扫到这个端口的人都能冒充对端，把玩家
/// 引到别处去。
///
/// 包做得很小（53 字节）：打洞阶段要在一两秒内朝所有候选反复发，包越小越不容易
/// 因为 MTU 或限速被丢。
/// </summary>
public static class PunchProtocol
{
    /// <summary>"MXP1"。前 4 字节不对就直接丢，省得把别的协议当自己人。</summary>
    public const uint Magic = 0x4D585031;

    public const int PacketSize = 4 + 1 + 8 + 8 + 32;

    public enum Kind : byte
    {
        /// <summary>朝对方候选地址发的探路包。</summary>
        Punch = 1,
        /// <summary>收到 Punch 后的回应，原样带回对方的 nonce。</summary>
        Ack = 2,
        /// <summary>通路建立后的保活，维持 NAT 映射。</summary>
        Keepalive = 3,

        /// <summary>
        /// TCP 打洞成功后的自我介绍：nonce 里放角色号。
        ///
        /// UDP 那边靠源地址就能认出对端，TCP 不行——两边同时拨号会打通**好几条**
        /// 连接（对端多个候选、每个还带一串预测端口，都可能通），还可能因为 hairpin
        /// 连到自己身上。所以每条连接上都得先互报身份：MAC 对不上的是外人，
        /// 角色和自己一样的是自己连自己。
        /// </summary>
        TcpHello = 4,

        /// <summary>
        /// 发起方点名："这么多条里，我们就用这一条"。
        ///
        /// 有它才能收敛。没有的话两边各自"挑一条"，挑的不是同一条就谁也读不到谁。
        /// </summary>
        TcpSelect = 5,

        /// <summary>
        /// 请对端朝这个洞按指定参数推一段数据（洞压测，见 <see cref="HoleLoadTest"/>）。
        ///
        /// 参数编码在 nonce 里，见 <see cref="EncodeLoadPlan"/>。
        /// </summary>
        LoadRequest = 6,

        /// <summary>
        /// 压测数据包。前 21 字节和别的包一样带 MAC，后面是填充到指定长度的垃圾。
        ///
        /// 只有这一种包长度可变，所以它不走 <see cref="Parse"/>，走
        /// <see cref="ParseLoad"/>——不能为了它把定长校验放松掉，那是挡外人的第一道门。
        /// </summary>
        Load = 7,

        /// <summary>收端回报收到多少个，好让发端也能把结论记进自己的日志。</summary>
        LoadReport = 8,

        /// <summary>
        /// 压测做完了，对端可以立刻收摊。
        ///
        /// 为什么值得专门发一个包：应答方本来只能靠"等一会儿没人再问"来判断结束，
        /// 那个等待（250ms）直接压在 QUIC 监听起来之前。而请求方是知道自己什么时候
        /// 问完的——它一说，应答方就能马上放开端口。
        ///
        /// 实测代价很具体：应答方晚就绪几百毫秒，请求方的第一个 QUIC Initial 就落在
        /// 监听起来之前被丢掉，然后要等 msquic 约 1 秒的 PTO 重传，建立时延里那一格
        /// "QUIC 握手"就变成 1.1 秒（8ms RTT 的链路上）。
        /// </summary>
        LoadDone = 9,
    }

    public readonly record struct Packet(Kind Type, ulong Session, ulong Nonce);

    public static byte[] Build(Kind kind, ulong session, ulong nonce, byte[] secret)
    {
        var buffer = new byte[PacketSize];
        BinaryPrimitives.WriteUInt32BigEndian(buffer, Magic);
        buffer[4] = (byte)kind;
        BinaryPrimitives.WriteUInt64BigEndian(buffer.AsSpan(5), session);
        BinaryPrimitives.WriteUInt64BigEndian(buffer.AsSpan(13), nonce);
        using var hmac = new HMACSHA256(secret);
        var mac = hmac.ComputeHash(buffer, 0, 21);
        mac.AsSpan(0, 32).CopyTo(buffer.AsSpan(21));
        return buffer;
    }

    /// <summary>校验并解析。任何一步不对都返回 null，调用方直接忽略该包。</summary>
    public static Packet? Parse(ReadOnlySpan<byte> data, byte[] secret)
    {
        if (data.Length != PacketSize) return null;
        if (BinaryPrimitives.ReadUInt32BigEndian(data) != Magic) return null;

        using var hmac = new HMACSHA256(secret);
        var expected = hmac.ComputeHash(data[..21].ToArray());
        if (!CryptographicOperations.FixedTimeEquals(expected, data[21..].ToArray())) return null;

        var kind = (Kind)data[4];
        if (kind is not (Kind.Punch or Kind.Ack or Kind.Keepalive
            or Kind.TcpHello or Kind.TcpSelect
            or Kind.LoadRequest or Kind.LoadReport or Kind.LoadDone)) return null;
        return new Packet(
            kind,
            BinaryPrimitives.ReadUInt64BigEndian(data[5..]),
            BinaryPrimitives.ReadUInt64BigEndian(data[13..]));
    }

    /// <summary>
    /// 造一个压测数据包：头部和普通包一样，后面填充到 <paramref name="totalSize"/>。
    ///
    /// 填充不进 MAC。这没问题——MAC 要挡的是"外人冒充对端把玩家引到别处"，而填充
    /// 里没有任何会被采信的信息，改它只等于改了一包垃圾的内容。把几百个包的
    /// 1.4 KB 填充都算进 HMAC 才是真问题：压测要跑满线路，算力得省给发包。
    /// </summary>
    public static byte[] BuildLoad(ulong session, uint sequence, byte[] secret, int totalSize)
    {
        if (totalSize < PacketSize) totalSize = PacketSize;
        var buffer = new byte[totalSize];
        BinaryPrimitives.WriteUInt32BigEndian(buffer, Magic);
        buffer[4] = (byte)Kind.Load;
        BinaryPrimitives.WriteUInt64BigEndian(buffer.AsSpan(5), session);
        BinaryPrimitives.WriteUInt64BigEndian(buffer.AsSpan(13), sequence);
        using var hmac = new HMACSHA256(secret);
        var mac = hmac.ComputeHash(buffer, 0, 21);
        mac.AsSpan(0, 32).CopyTo(buffer.AsSpan(21));
        return buffer;
    }

    /// <summary>
    /// 校验并解析压测数据包。长度可变，所以只要求"不短于定长包"。
    /// </summary>
    public static (ulong Session, uint Sequence)? ParseLoad(ReadOnlySpan<byte> data, byte[] secret)
    {
        if (data.Length < PacketSize) return null;
        if (BinaryPrimitives.ReadUInt32BigEndian(data) != Magic) return null;
        if ((Kind)data[4] != Kind.Load) return null;

        using var hmac = new HMACSHA256(secret);
        var expected = hmac.ComputeHash(data[..21].ToArray());
        if (!CryptographicOperations.FixedTimeEquals(expected, data[21..53].ToArray())) return null;
        return (BinaryPrimitives.ReadUInt64BigEndian(data[5..]),
                (uint)BinaryPrimitives.ReadUInt64BigEndian(data[13..]));
    }

    /// <summary>
    /// 把压测参数塞进 nonce 的 8 个字节：包长 16 位 + 包数 24 位 + 速率(KB/s) 24 位。
    ///
    /// 借 nonce 而不是加新字段，是为了让压测请求和别的打洞包完全同构——同一个收包
    /// 循环、同一套校验，不必为它单开一条解析路径。
    /// </summary>
    public static ulong EncodeLoadPlan(int packetSize, int count, int rateKbPerSecond)
        => ((ulong)(uint)Math.Clamp(packetSize, PacketSize, 65535) << 48)
           | ((ulong)(uint)Math.Clamp(count, 0, 0xFFFFFF) << 24)
           | (uint)Math.Clamp(rateKbPerSecond, 0, 0xFFFFFF);

    public static (int PacketSize, int Count, int RateKbPerSecond) DecodeLoadPlan(ulong nonce)
        => ((int)(nonce >> 48), (int)((nonce >> 24) & 0xFFFFFF), (int)(nonce & 0xFFFFFF));

    public static byte[] DeriveSecret(string token) =>
        SHA256.HashData(Encoding.UTF8.GetBytes("muxi-punch-v1:" + token));
}
