using System.Buffers.Binary;
using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using BatterMC.Core;
using BatterMC.Protocol;

namespace BatterMC.Launcher;

/// <summary>
/// 线路诊断：在玩家自己的网络上跑一遍真实的选路，并可选地经本地代理打一次
/// 真正的 Minecraft 状态查询。
///
/// 和 --verify 的区别是这里只关心「连不连得上、走的哪条线」，不碰整合包文件，
/// 所以玩家反馈「进不去服务器」时可以直接让他跑这一条，把输出发回来。
/// </summary>
internal static class RouteDiagnostics
{
    /// <summary>1.21.1 的协议号。状态查询不校验版本，填错也只影响服务端日志。</summary>
    private const int ProtocolVersion = 767;

    public static async Task<int> RunAsync(
        string[] args, LauncherPaths paths, LauncherSettings settings, CancellationToken ct)
    {
        var candidatesFile = ValueOf(args, "--candidates");
        var handshakeHost = ValueOf(args, "--handshake-host");
        var doPing = args.Any(a => a.Equals("--ping", StringComparison.OrdinalIgnoreCase));

        // 优先控制面：地址会轮换，写死的清单撑不过几小时。
        var controlPlane = ValueOf(args, "--control-plane");
        List<ServerEntry> servers;
        if (controlPlane is not null)
        {
            var serverId = ValueOf(args, "--server-id") ?? "default";
            var snapshot = await ControlPlaneClient
                .FetchAsync(controlPlane, serverId, TimeSpan.FromSeconds(10), ct)
                .ConfigureAwait(false);
            if (snapshot is null || snapshot.Candidates.Count == 0)
            {
                Console.WriteLine("控制面没给出任何候选，无法诊断。");
                return 2;
            }
            Console.WriteLine($"  控制面    {controlPlane}  在线={snapshot.Online}  " +
                              $"新鲜度={snapshot.AgeSeconds:0.#}s  我的公网地址={snapshot.YourIp}");
            servers =
            [
                new ServerEntry
                {
                    Name = "Batter MC 5", Host = "minecraft.muxigame.com", Port = 25565,
                    Primary = true, Forced = true, Routes = snapshot.Candidates,
                },
            ];
        }
        else
        {
            servers = candidatesFile is not null
                ? LoadServers(candidatesFile)
                : await FetchServersAsync(paths, settings, ct).ConfigureAwait(false);
        }

        if (servers.Count == 0)
        {
            Console.WriteLine("清单里没有任何服务器条目，无法诊断。");
            return 2;
        }

        Console.WriteLine();
        Console.WriteLine("线路诊断");
        Console.WriteLine("  候选来源  " + (controlPlane ?? candidatesFile ?? settings.UpdateBaseUrl));
        foreach (var server in servers)
        {
            Console.WriteLine($"  服务器    {server.Name}  primary={server.Primary}  "
                              + $"候选 {server.Routes.Count} 条");
        }
        Console.WriteLine();

        RouteSelection selection;
        try
        {
            var progress = new Progress<string>(detail => Console.WriteLine("  · " + detail));
            selection = await MinecraftRouteProxy.SelectRouteAsync(servers, progress, ct)
                .ConfigureAwait(false);
        }
        catch (InvalidOperationException error)
        {
            Console.WriteLine();
            Console.WriteLine("选路失败：" + error.Message);
            return 2;
        }

        Console.WriteLine();
        Console.WriteLine($"{"候选",-18}{"生效类型",-12}{"入口",-44}{"握手",8}  说明");
        Console.WriteLine(new string('-', 104));
        foreach (var route in selection.Reachable)
        {
            // Classify 只在候选声明为 Auto 时参与排序；显式声明的类型（尤其是
            // Relay/TcpTunnel）不能按 IP 反推，否则中转会被误显示成"直连"。
            var declared = route.Candidate.Kind;
            var effective = declared == RouteKind.Auto
                ? MinecraftRouteProxy.Classify(route.Endpoint.Address)
                : declared;
            var note = declared == RouteKind.Auto
                ? $"Auto 按地址判定为 {effective}"
                : effective is RouteKind.TcpTunnel or RouteKind.Relay
                    ? "中转：握手 RTT 只反映到中转节点这一段，不含第二跳"
                    : "";
            Console.WriteLine($"{route.Candidate.Id,-18}{effective,-12}"
                              + $"{route.Endpoint,-44}{route.Latency.TotalMilliseconds,6:0}ms  {note}");
        }

        var selected = selection.Selected;
        var selectedKind = selected.Candidate.Kind == RouteKind.Auto
            ? MinecraftRouteProxy.Classify(selected.Endpoint.Address)
            : selected.Candidate.Kind;
        Console.WriteLine();
        Console.WriteLine($"选中 {selected.Candidate.Id}（{selectedKind}） {selected.Endpoint} "
                          + $"{selected.Latency.TotalMilliseconds:0}ms");
        Console.WriteLine($"可用 {selection.Reachable.Count} 条；"
                          + (selectedKind is RouteKind.TcpTunnel or RouteKind.Relay
                              ? "只剩中转可用，流量会走服务器"
                              : "有直连可用，不需要中转"));

        if (args.Any(a => a.Equals("--tunnel", StringComparison.OrdinalIgnoreCase)))
        {
            var baseUrl = controlPlane ?? settings.UpdateBaseUrl;
            var serverId = ValueOf(args, "--server-id") ?? "default";
            // 凭据三种来源，优先级和启动游戏时一致：命令行 > 本地配置 > 控制面现取。
            var tunnelToken = ValueOf(args, "--tunnel") is { } inline
                              && !inline.StartsWith("--", StringComparison.Ordinal)
                ? inline
                : !string.IsNullOrWhiteSpace(settings.TunnelToken)
                    ? settings.TunnelToken
                    : await ControlPlaneClient
                        .FetchClientTokenAsync(baseUrl, serverId, TimeSpan.FromSeconds(8), ct)
                        .ConfigureAwait(false);
            if (string.IsNullOrWhiteSpace(tunnelToken))
            {
                Console.WriteLine();
                Console.WriteLine("拿不到隧道凭据，无法诊断隧道。");
                return 2;
            }

            var tunnelPort = int.TryParse(ValueOf(args, "--tunnel-port"), out var tp) ? tp : 25540;

            // 保持隧道那一套单独留给 --tunnel-hold：它要证明控制连接扛得住服务端
            // 60s 空闲超时。这里走的是玩家点「开始游戏」时一模一样的入口，两边
            // 共用一个方法，免得诊断说通了而实际启动是另一条代码路径。
            if (args.Any(a => a.Equals("--tunnel-hold", StringComparison.OrdinalIgnoreCase)))
                return await TunnelAsync(selection, tunnelToken!, tunnelPort, ct).ConfigureAwait(false);

            Console.WriteLine();
            Console.WriteLine("按启动游戏时的顺序建立连接（打洞 → 隧道 → 直连/中转）…");
            await using var live = await MinecraftRouteProxy
                .StartAsync(servers, null, ct).ConfigureAwait(false);
            var liveProgress = new Progress<string>(detail => Console.WriteLine("  · " + detail));
            try
            {
                var best = await live.EstablishBestAsync(
                    tunnelToken, baseUrl, serverId,
                    ValueOf(args, "--reflector") ?? settings.PunchReflector,
                    tunnelPort, liveProgress, ct).ConfigureAwait(false);
                Console.WriteLine();
                Console.WriteLine($"  走的是 {best.Kind}  {best.Route.Candidate.Label ?? best.Route.Candidate.Id}");
                Console.WriteLine($"    服务端版本 {best.Status.Version}");
                Console.WriteLine($"    在线人数   {best.Status.Online}/{best.Status.Max}");
                Console.WriteLine($"    往返       {best.Status.Elapsed.TotalMilliseconds:0}ms");
                Console.WriteLine();
                Console.WriteLine(best.Kind == RouteKind.UdpTunnel
                    ? "P2P 直连，游戏流量不经过我们的服务器。"
                    : best.Kind is RouteKind.Relay or RouteKind.TcpTunnel
                        ? "走的是中转，会吃我们服务器的带宽。"
                        : "直连线路，不吃中转带宽。");
                return 0;
            }
            catch (Exception error) when (error is not OperationCanceledException)
            {
                Console.WriteLine();
                Console.WriteLine("所有线路都没能建立：" + error.Message);
                return 2;
            }
        }

        if (!doPing) return 0;

        Console.WriteLine();
        Console.WriteLine("经本地代理做一次真实 Minecraft 状态查询…");
        await using var proxy = await MinecraftRouteProxy.StartAsync(servers, null, ct)
            .ConfigureAwait(false);
        Console.WriteLine($"  本地代理 {proxy.LocalAddress}");

        // 游戏握手里带的就是代理地址，这里照搬，才能发现服务端按 hostname 分流时的问题。
        var announced = handshakeHost ?? "127.0.0.1";
        try
        {
            var status = await StatusAsync(proxy.LocalPort, announced, ct).ConfigureAwait(false);
            Console.WriteLine($"  握手 ServerAddress = \"{announced}\"");
            Console.WriteLine($"  服务端版本 {status.Version}");
            Console.WriteLine($"  在线人数   {status.Players}");
            Console.WriteLine($"  MOTD       {status.Motd}");
            Console.WriteLine($"  往返       {status.ElapsedMs:0}ms");
            Console.WriteLine();
            Console.WriteLine("整条链路贯通：游戏 → 本地代理 → " + selected.Endpoint);
            return 0;
        }
        catch (Exception error) when (error is IOException or SocketException or JsonException
                                          or InvalidDataException)
        {
            Console.WriteLine("  状态查询失败：" + error.Message);
            return 2;
        }
    }

    /// <summary>
    /// 验证端侧隧道：逐条候选去连服务端侧工具，第一条握手成功的就挂住，然后在
    /// 这条隧道里开一条数据连接做真实的 Minecraft 状态查询。
    ///
    /// 这里刻意在隧道建好之后停一会儿再发数据——就是为了证明控制连接不会像裸
    /// TCP 那样被 Minecraft 的 60 秒空闲超时掐掉，玩家读条几分钟也活着。
    /// </summary>
    private static async Task<int> TunnelAsync(
        RouteSelection selection, string token, int tunnelPort, CancellationToken ct)
    {
        Console.WriteLine();
        Console.WriteLine($"建立端侧隧道（服务端工具端口 {tunnelPort}）…");

        TunnelClient? tunnel = null;
        foreach (var route in selection.Reachable)
        {
            var kind = MinecraftRouteProxy.EffectiveKind(route);
            var endpoint = new IPEndPoint(route.Endpoint.Address, tunnelPort);
            Console.WriteLine($"  尝试 {kind} {endpoint}");
            try
            {
                tunnel = await TunnelClient
                    .ConnectAsync(endpoint, token, TimeSpan.FromSeconds(8), ct)
                    .ConfigureAwait(false);
                Console.WriteLine($"  隧道已建立，会话 {tunnel.Session:x16}");
                break;
            }
            catch (Exception error) when (error is not OperationCanceledException)
            {
                Console.WriteLine($"    失败：{error.Message}");
            }
        }

        if (tunnel is null)
        {
            Console.WriteLine();
            Console.WriteLine("所有候选都没能建立隧道。");
            return 2;
        }

        await using (tunnel)
        {
            const int holdSeconds = 75;
            Console.WriteLine($"  保持隧道 {holdSeconds}s（超过服务端 60s 空闲超时）…");
            for (var elapsed = 0; elapsed < holdSeconds; elapsed += 15)
            {
                await Task.Delay(TimeSpan.FromSeconds(15), ct).ConfigureAwait(false);
                Console.WriteLine($"    {elapsed + 15}s 隧道仍然存活 = {tunnel.IsAlive}");
                if (!tunnel.IsAlive)
                {
                    Console.WriteLine("  隧道在保持期间掉了。");
                    return 2;
                }
            }

            Console.WriteLine("  开数据连接并做真实 Minecraft 状态查询…");
            await using var data = await tunnel.OpenStreamAsync(ct).ConfigureAwait(false);
            var status = await MinecraftPing
                .QueryAsync(data, "127.0.0.1", 25565, ct).ConfigureAwait(false);
            Console.WriteLine($"    服务端版本 {status.Version}");
            Console.WriteLine($"    在线人数   {status.Online}/{status.Max}");
            Console.WriteLine($"    MOTD       {status.Motd}");
            Console.WriteLine($"    往返       {status.Elapsed.TotalMilliseconds:0}ms");
            Console.WriteLine();
            Console.WriteLine($"隧道贯通：游戏 → 客户端工具 → {tunnel.Endpoint} → 服务端工具 → 本机 Minecraft");
            return 0;
        }
    }

    private static string? ValueOf(string[] args, string name)
    {
        var index = Array.FindIndex(args,
            a => a.Equals(name, StringComparison.OrdinalIgnoreCase));
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
    }

    /// <summary>
    /// 候选文件就是一份最小的清单：{"servers":[ … ]}。复用清单的源生成上下文，
    /// 免得为诊断单开一条反射序列化路径把裁剪配置搞复杂。
    /// </summary>
    private static List<ServerEntry> LoadServers(string path)
    {
        var manifest = PackManifest.FromJson(File.ReadAllText(path));
        return manifest?.Servers ?? [];
    }

    private static async Task<List<ServerEntry>> FetchServersAsync(
        LauncherPaths paths, LauncherSettings settings, CancellationToken ct)
    {
        using var downloader = new Downloader();
        var state = LocalState.Load(paths.StateFile);
        var sync = new SyncEngine(paths, state, settings, downloader);
        var manifest = await sync.FetchManifestAsync(settings.UpdateBaseUrl, ct).ConfigureAwait(false);
        return manifest.Servers;
    }

    private readonly record struct ServerStatus(
        string Version, string Players, string Motd, double ElapsedMs);

    private static async Task<ServerStatus> StatusAsync(int localPort, string announced, CancellationToken ct)
    {
        using var client = new TcpClient();
        await client.ConnectAsync("127.0.0.1", localPort, ct).ConfigureAwait(false);
        client.NoDelay = true;
        var stream = client.GetStream();
        var watch = Stopwatch.StartNew();

        var handshake = new List<byte>();
        WriteVarInt(handshake, ProtocolVersion);
        WriteString(handshake, announced);
        var port = new byte[2];
        BinaryPrimitives.WriteUInt16BigEndian(port, 25565);
        handshake.AddRange(port);
        WriteVarInt(handshake, 1); // next state: status
        await stream.WriteAsync(Frame(0x00, handshake.ToArray()), ct).ConfigureAwait(false);
        await stream.WriteAsync(Frame(0x00, []), ct).ConfigureAwait(false);

        var length = await ReadVarIntAsync(stream, ct).ConfigureAwait(false);
        var body = new byte[length];
        await stream.ReadExactlyAsync(body, ct).ConfigureAwait(false);
        watch.Stop();

        var offset = 0;
        var packetId = ReadVarInt(body, ref offset);
        if (packetId != 0x00) throw new InvalidDataException($"意外的包 ID 0x{packetId:x2}");
        var jsonLength = ReadVarInt(body, ref offset);
        var json = Encoding.UTF8.GetString(body, offset, jsonLength);

        using var document = JsonDocument.Parse(json);
        var root = document.RootElement;
        var version = root.TryGetProperty("version", out var v) && v.TryGetProperty("name", out var vn)
            ? vn.GetString() ?? "?" : "?";
        var online = root.TryGetProperty("players", out var p) && p.TryGetProperty("online", out var po)
            ? po.GetInt32() : -1;
        var max = p.ValueKind == JsonValueKind.Object && p.TryGetProperty("max", out var pm)
            ? pm.GetInt32() : -1;
        var motd = root.TryGetProperty("description", out var d)
            ? (d.ValueKind == JsonValueKind.String ? d.GetString() : d.TryGetProperty("text", out var dt)
                ? dt.GetString() : d.GetRawText())
            : "";
        return new ServerStatus(version, $"{online}/{max}",
            (motd ?? "").Replace("\n", " ").Trim(), watch.Elapsed.TotalMilliseconds);
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

    private static async Task<int> ReadVarIntAsync(NetworkStream stream, CancellationToken ct)
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
