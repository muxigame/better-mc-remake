using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

namespace BatterMC.Core;

public sealed record GameSession(string Username, string UuidDashed, string UuidPlain, string AccessToken)
{
    public static GameSession Offline(string username)
    {
        var uuid = OfflineAuth.OfflineUuid(username);
        return new GameSession(username, uuid, uuid.Replace("-", ""), "0");
    }
}

public static partial class OfflineAuth
{
    [GeneratedRegex(@"^[A-Za-z0-9_]{3,16}$")]
    private static partial Regex ValidName();

    /// <summary>Minecraft 的用户名规则：3–16 位，只允许字母数字下划线。</summary>
    public static bool IsValidUsername(string? name)
        => !string.IsNullOrWhiteSpace(name) && ValidName().IsMatch(name);

    /// <summary>
    /// 离线 UUID，必须和服务端算出来的一模一样，否则玩家进服会变成新号、丢背包丢进度。
    ///
    /// 服务端用的是 Java 的 UUID.nameUUIDFromBytes(("OfflinePlayer:" + name).getBytes(UTF_8))，
    /// 也就是 RFC 4122 的 version 3（MD5）UUID。
    ///
    /// 注意：这里绝对不能用 new Guid(byte[])。.NET 的 Guid 二进制布局前三段是小端，
    /// 直接塞进去会把字节顺序搞反，得到一个和服务端对不上的 UUID。
    /// 所以下面按大端手工拼十六进制。
    /// </summary>
    public static string OfflineUuid(string username)
    {
        var bytes = MD5.HashData(Encoding.UTF8.GetBytes("OfflinePlayer:" + username));

        bytes[6] = (byte)((bytes[6] & 0x0F) | 0x30); // version 3
        bytes[8] = (byte)((bytes[8] & 0x3F) | 0x80); // IETF variant

        var hex = Convert.ToHexStringLower(bytes);
        return $"{hex[..8]}-{hex[8..12]}-{hex[12..16]}-{hex[16..20]}-{hex[20..]}";
    }
}
