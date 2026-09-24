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
        // 连接已经吃掉了一部分预算，把剩下的交给流那一层——两段共用一个总上限，
        // 不要各给一份，否则最坏等待会变成两倍。
        return await QueryAsync(
                client.GetStream(), announcedHost, endpoint.Port, timeout, deadline.Token)
            .ConfigureAwait(false);
    }

    /// <summary>
    /// 在一条已经接通的流上做状态查询。隧道的数据连接就是这么用的：外面那层
    /// 由端侧工具负责，这里只管说 Minecraft 的话。
    /// </summary>
    /// <param name="timeout">
    /// 必须有，而且必须在这里兜住，不能指望调用方的 <paramref name="ct"/>。
    ///
    /// 这个重载原先建了 linked CTS 却从不 CancelAfter，于是"流能开、写能进、对面
    /// 永远不回"的隧道会让下面的 ReadExactlyAsync 无限挂着。配上 QUIC 那边
    /// KeepAliveInterval=15s（连接永不 idle 超时），挂住就是真的永远。最要命的是
    /// 保温探活那个调用点：它的 ct 是整个会话的生命周期令牌，只在代理 Dispose 时
    /// 才取消——探活一挂，strikes 永远停在 0，线路真死了也不会换，界面上一切正常
    /// 而玩家连不进去。
    ///
    /// 探活的定义就是**有界**等待；无界的探活不是探活，只是把故障挪了个地方藏起来。
    /// </param>
    public static async Task<MinecraftStatus> QueryAsync(
        Stream stream, string announcedHost, int announcedPort, TimeSpan timeout,
        CancellationToken ct)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
        deadline.CancelAfter(timeout);
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

        var offset = 0;
        var packetId = ReadVarInt(body, ref offset);
        if (packetId != 0x00) throw new InvalidDataException($"意外的包 ID 0x{packetId:x2}");
        var jsonLength = ReadVarInt(body, ref offset);
        var json = Encoding.UTF8.GetString(body, offset, jsonLength);
        return Parse(json, watch.Elapsed);
    }

    /// <summary>
    /// 以服务器的身份回一次话：状态查询回 <paramref name="motd"/>，要登录的直接断开，
    /// 断开原因写 <paramref name="kickReason"/>。
    ///
    /// 本地代理还没连上服务器时用它。游戏的多人列表和快速加入都只认本地代理的地址，
    /// 代理要是一声不吭把连接关掉，玩家只能看到"连接中断"，不知道是自己网络的问题、
    /// 服务器在重启，还是该等一等；这里直接把原因告诉他，他点返回就能去玩单机。
    /// </summary>
    public static async Task ServeStandInAsync(
        Stream stream, string motd, string kickReason, TimeSpan timeout, CancellationToken ct)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
        deadline.CancelAfter(timeout);

        var handshake = await ReadPacketAsync(stream, deadline.Token).ConfigureAwait(false);
        var offset = 0;
        if (ReadVarInt(handshake, ref offset) != 0x00) return;
        var protocol = ReadVarInt(handshake, ref offset);
        // 不能写成 offset += ReadVarInt(..., ref offset)：复合赋值先取 offset 的旧值，
        // ReadVarInt 对它的推进会被覆盖掉。
        var hostLength = ReadVarInt(handshake, ref offset);
        offset += hostLength + 2; // 地址串 + 端口
        var next = ReadVarInt(handshake, ref offset);

        if (next == 1)
        {
            await ReadPacketAsync(stream, deadline.Token).ConfigureAwait(false); // Status Request
            var status = new List<byte>();
            // 回游戏自己的协议号：填别的会被显示成"版本不兼容"，把真正的说明盖掉。
            WriteString(status, Json(writer =>
            {
                writer.WriteStartObject("version");
                writer.WriteString("name", "1.21.1");
                writer.WriteNumber("protocol", protocol);
                writer.WriteEndObject();
                writer.WriteStartObject("players");
                writer.WriteNumber("max", 0);
                writer.WriteNumber("online", 0);
                writer.WriteEndObject();
                writer.WriteStartObject("description");
                writer.WriteString("text", motd);
                writer.WriteEndObject();
            }));
            await stream.WriteAsync(Frame(0x00, status.ToArray()), deadline.Token).ConfigureAwait(false);

            // 多人列表接着会发 Ping 量延迟，原样回 Pong。只查状态的调用方问完就关，那也正常。
            byte[] ping;
            try { ping = await ReadPacketAsync(stream, deadline.Token).ConfigureAwait(false); }
            catch (EndOfStreamException) { return; }
            if (ping.Length == 9 && ping[0] == 0x01)
                await stream.WriteAsync(Frame(0x01, ping[1..]), deadline.Token).ConfigureAwait(false);
            return;
        }

        // 2 = 登录，3 = 转服（1.20.5 起），都在登录阶段断开。先把 Login Start 读掉，
        // 没读的字节留在缓冲里再关连接会变成 RST，游戏那边就只剩一句"连接被重置"。
        try { await ReadPacketAsync(stream, deadline.Token).ConfigureAwait(false); }
        catch (EndOfStreamException) { return; }
        var reason = new List<byte>();
        WriteString(reason, Json(writer => writer.WriteString("text", kickReason)));
        await stream.WriteAsync(Frame(0x00, reason.ToArray()), deadline.Token).ConfigureAwait(false);
        await stream.FlushAsync(deadline.Token).ConfigureAwait(false);
    }

    private static string Json(Action<Utf8JsonWriter> body)
    {
        using var buffer = new MemoryStream();
        using (var writer = new Utf8JsonWriter(buffer))
        {
            writer.WriteStartObject();
            body(writer);
            writer.WriteEndObject();
        }
        return Encoding.UTF8.GetString(buffer.ToArray());
    }

    private static async Task<byte[]> ReadPacketAsync(Stream stream, CancellationToken ct)
    {
        var length = await ReadVarIntAsync(stream, ct).ConfigureAwait(false);
        // 握手、Login Start、Ping 都只有几十字节；超出这个量级的不是游戏在说话。
        if (length is <= 0 or > 32 * 1024)
            throw new InvalidDataException($"包长度异常：{length}");
        var body = new byte[length];
        await stream.ReadExactlyAsync(body, ct).ConfigureAwait(false);
        return body;
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
