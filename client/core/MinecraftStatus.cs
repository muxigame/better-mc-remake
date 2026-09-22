using System.Buffers.Binary;
using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;

namespace BatterMC.Core;

/// <summary>一次 Server List Ping 的结果。</summary>
public sealed record MinecraftStatus(
    string Version, int Online, int Max, string Motd, TimeSpan Elapsed);

/// <summary>
/// Minecraft 状态查询（握手 + Status Request）。
///
/// 用它而不是「TCP 连得上就算通」：中转节点、运营商劫持页、错配的端口转发
/// 都能让 TCP 握手成功，但后面根本没有 Minecraft。只有真的问出版本号，
/// 才能确认这条线路通到的是我们的服务器。
/// </summary>
public static class MinecraftPing
{
    /// <summary>1.21.1 的协议号。状态查询不校验版本，填错只影响服务端日志。</summary>
    public const int ProtocolVersion = 767;

    /// <summary>
    /// 对 <paramref name="endpoint"/> 做一次状态查询。
    /// <paramref name="announcedHost"/> 是握手包里写的服务器地址：客户端经本地
    /// 代理进服时游戏写的是 127.0.0.1，这里要能照样传进来，才测得出服务端按
    /// hostname 分流时的问题。
    /// </summary>
    public static async Task<MinecraftStatus> QueryAsync(
        IPEndPoint endpoint, string announcedHost, TimeSpan timeout, CancellationToken ct)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
        deadline.CancelAfter(timeout);
        using var client = new TcpClient(endpoint.AddressFamily);
        await client.ConnectAsync(endpoint, deadline.Token).ConfigureAwait(false);
        client.NoDelay = true;
        return await QueryAsync(client.GetStream(), announcedHost, endpoint.Port, deadline.Token)
            .ConfigureAwait(false);
    }

    /// <summary>
    /// 在一条已经接通的流上做状态查询。隧道的数据连接就是这么用的：外面那层
    /// 由端侧工具负责，这里只管说 Minecraft 的话。
    /// </summary>
    public static async Task<MinecraftStatus> QueryAsync(
        Stream stream, string announcedHost, int announcedPort, CancellationToken ct)
    {
        var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
        var watch = Stopwatch.StartNew();

        var handshake = new List<byte>();
        WriteVarInt(handshake, ProtocolVersion);
        WriteString(handshake, announcedHost);
        var port = new byte[2];
        BinaryPrimitives.WriteUInt16BigEndian(port, (ushort)announcedPort);
        handshake.AddRange(port);
        WriteVarInt(handshake, 1); // next state: status
        await stream.WriteAsync(Frame(0x00, handshake.ToArray()), deadline.Token).ConfigureAwait(false);
        await stream.WriteAsync(Frame(0x00, []), deadline.Token).ConfigureAwait(false);

        var length = await ReadVarIntAsync(stream, deadline.Token).ConfigureAwait(false);
        if (length is <= 0 or > 4 * 1024 * 1024)
            throw new InvalidDataException($"状态响应长度异常：{length}");
        var body = new byte[length];
        await stream.ReadExactlyAsync(body, deadline.Token).ConfigureAwait(false);
        watch.Stop();

        deadline.Dispose();

        var offset = 0;
        var packetId = ReadVarInt(body, ref offset);
        if (packetId != 0x00) throw new InvalidDataException($"意外的包 ID 0x{packetId:x2}");
        var jsonLength = ReadVarInt(body, ref offset);
        var json = Encoding.UTF8.GetString(body, offset, jsonLength);
        return Parse(json, watch.Elapsed);
    }

    private static MinecraftStatus Parse(string json, TimeSpan elapsed)
    {
        using var document = JsonDocument.Parse(json);
        var root = document.RootElement;

        var version = "?";
        if (root.TryGetProperty("version", out var v) && v.TryGetProperty("name", out var vn))
            version = vn.GetString() ?? "?";

        int online = -1, max = -1;
        if (root.TryGetProperty("players", out var p) && p.ValueKind == JsonValueKind.Object)
        {
            if (p.TryGetProperty("online", out var po) && po.TryGetInt32(out var o)) online = o;
            if (p.TryGetProperty("max", out var pm) && pm.TryGetInt32(out var m)) max = m;
        }

        var motd = "";
        if (root.TryGetProperty("description", out var d))
        {
            motd = d.ValueKind switch
            {
                JsonValueKind.String => d.GetString() ?? "",
                JsonValueKind.Object when d.TryGetProperty("text", out var dt) => dt.GetString() ?? "",
                _ => d.GetRawText(),
            };
        }
        return new MinecraftStatus(version, online, max, motd.Replace("\n", " ").Trim(), elapsed);
    }

    private static byte[] Frame(int packetId, byte[] payload)
    {
        var body = new List<byte>();
        WriteVarInt(body, packetId);
        body.AddRange(payload);
        var framed = new List<byte>();
        WriteVarInt(framed, body.Count);
        framed.AddRange(body);
        return framed.ToArray();
    }

    private static void WriteVarInt(List<byte> sink, int value)
    {
        var unsigned = (uint)value;
        while (true)
        {
            if ((unsigned & ~0x7Fu) == 0)
            {
                sink.Add((byte)unsigned);
                return;
            }
            sink.Add((byte)((unsigned & 0x7F) | 0x80));
            unsigned >>= 7;
        }
    }

    private static void WriteString(List<byte> sink, string value)
    {
        var raw = Encoding.UTF8.GetBytes(value);
        WriteVarInt(sink, raw.Length);
        sink.AddRange(raw);
    }

    private static int ReadVarInt(byte[] buffer, ref int offset)
    {
        var result = 0;
        for (var shift = 0; shift < 35; shift += 7)
        {
            var current = buffer[offset++];
            result |= (current & 0x7F) << shift;
            if ((current & 0x80) == 0) return result;
        }
        throw new InvalidDataException("VarInt 过长");
    }

    private static async Task<int> ReadVarIntAsync(Stream stream, CancellationToken ct)
    {
        var result = 0;
        var single = new byte[1];
        for (var shift = 0; shift < 35; shift += 7)
        {
            await stream.ReadExactlyAsync(single, ct).ConfigureAwait(false);
            result |= (single[0] & 0x7F) << shift;
            if ((single[0] & 0x80) == 0) return result;
        }
        throw new InvalidDataException("VarInt 过长");
    }
}
