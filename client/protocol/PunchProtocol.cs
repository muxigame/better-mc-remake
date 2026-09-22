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
        if (kind is not (Kind.Punch or Kind.Ack or Kind.Keepalive)) return null;
        return new Packet(
            kind,
            BinaryPrimitives.ReadUInt64BigEndian(data[5..]),
            BinaryPrimitives.ReadUInt64BigEndian(data[13..]));
    }

    public static byte[] DeriveSecret(string token) =>
        SHA256.HashData(Encoding.UTF8.GetBytes("muxi-punch-v1:" + token));
}
