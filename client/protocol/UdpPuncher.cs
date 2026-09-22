using System.Collections.Concurrent;
using System.Net;
using System.Net.Sockets;

namespace BatterMC.Protocol;

/// <summary>
/// UDP 打洞。两端在约定时刻同时朝对方的**全部**候选地址猛发探路包。
///
/// 原理：NAT 只放行"本端先发过包的那个对端地址"回来的流量。所以单边发起没用
/// ——先到的那个包在对方 NAT 上没有对应映射，会被当成未知连接丢掉。两边同时发，
/// 各自的出站包在自己这侧开出映射，随后对方的包就能进来了。
///
/// 成功判据是收到 <see cref="PunchProtocol.Kind.Ack"/>：那说明"我发出去的包到了
/// 对面"且"对面的回包也到了我这里"，双向都通。只收到对方的 Punch 不算——那只
/// 证明单向。
/// </summary>
public static class UdpPuncher
{
    /// <summary>发包间隔。密一点能抢在对方 NAT 映射老化之前，但也别把线路打满。</summary>
    private static readonly TimeSpan SendInterval = TimeSpan.FromMilliseconds(50);

    /// <summary>
    /// 自己这侧成功之后，还要继续替对方回 Ack 的时间。
    ///
    /// 两侧极少同时成功：先收到 Ack 的那一方如果立刻收摊，对方的探路包就没人
    /// 应答，会一直打到预算耗尽然后判失败——一边以为通了、一边以为没通。
    /// </summary>
    private static readonly TimeSpan AckGrace = TimeSpan.FromSeconds(2);

    /// <summary>
    /// 端口扫描的窗口与节奏。
    ///
    /// 为什么需要：对端如果是地址相关型（对称型）NAT，它报给我们的映射是它探反射器
    /// 时建的，朝我们发包时用的是**另一个端口**，我们照着报上来的那个打必然打偏。
    /// 但运营商 NAT 的端口是顺序分配的（实测每秒消耗约一百个），真实端口就在报上来
    /// 那个附近。往上扫一段就能命中。
    ///
    /// 命中之后双向同时通：对方为了打洞已经朝我们发过包，它的 NAT 早就为我们这个
    /// 源地址开好了口子——缺的只是我们不知道该发到它的哪个端口。
    ///
    /// 只在准确地址打不通时才展开：锥形 NAT 常常一个包就成了，没必要为少数情况
    /// 让所有人都多发几千个包，也避免把正常打洞弄得像端口扫描。
    /// </summary>
    /// <summary>
    /// 扫描是**单边**的：谁的地址能被对方提前知道，谁就负责去找对方。
    ///
    /// 我方端点无关时，我的地址稳定且已经通过信令报给了对方，对方照着打就行——
    /// 找不到的那一侧是对方，所以由我来扫。反过来我方地址相关时，我的地址对方
    /// 根本预测不了，我再去扫对方那个**已知且稳定**的地址毫无意义：那里没有东西
    /// 需要找。更糟的是，如果我这侧是「地址+端口相关」型，朝对方几百个不同端口
    /// 发包会各自产生一个新映射，一口气烧掉几百个运营商端口，把我自己的真实映射
    /// 推得更远，反而让对方更难找到我。
    /// </summary>
    private static readonly TimeSpan SweepAfter = TimeSpan.FromSeconds(1.5);

    /// <summary>
    /// 窗口宽度按实测的 NAT 开门时间定。
    ///
    /// 实测：朝某个地址发一个包之后，静默 20 秒对方仍然打得进来，45 秒就不行了
    /// ——两层 NAT 串联，取更短的那个，约 30 秒（Linux nf_conntrack_udp_timeout
    /// 的默认值）。也就是说扫描期间开出的每一扇门，在整个打洞预算内都一直开着，
    /// 不需要反复去敲。
    ///
    /// 所以正确的做法不是把包挤在几秒内猛打，而是在预算内低速铺开、把网撒宽：
    /// 2496 个端口 / 24 个每轮 / 50ms 一轮 ≈ 1.6 遍覆盖，峰值约 480 包每秒、
    /// 25 KB/s，conntrack 峰值 2496 条——对企业级路由器可以忽略，也远低于运营商
    /// 每用户会话上限（常见 1000~8000 的量级要看，所以没有再往上加）。
    ///
    /// 为什么不干脆扫满 65535：那是"端口随机分配且毫无窗口信息"时才该用的手段。
    /// 实测运营商 NAT 是顺序分配、报上来的号就是窗口起点，撒满全域只多花 26 倍
    /// 代价换同样的命中率，还会撞上运营商的会话上限（波及整个家庭网络）、并且
    /// 在对端看来就是标准的端口扫描——真被拉黑，我们的服务器 IP 会被那个玩家的
    /// 网络长期拒绝，比打洞失败严重得多。
    /// </summary>
    private const int SweepBelow = 64;
    private const int SweepAbove = 2432;
    private const int SweepPerRound = 24;

    public sealed record Outcome(
        IPEndPoint? Peer,
        TimeSpan Elapsed,
        int Sent,
        int PunchesHeard,
        IReadOnlyList<IPEndPoint> HeardFrom)
    {
        public bool Success => Peer is not null;
    }

    public static async Task<Outcome> PunchAsync(
        Socket socket,
        IReadOnlyList<IPEndPoint> peerCandidates,
        ulong session,
        byte[] secret,
        DateTimeOffset punchAt,
        TimeSpan budget,
        bool sweepPorts,
        Action<string>? log,
        CancellationToken ct)
    {
        if (peerCandidates.Count == 0)
            return new Outcome(null, TimeSpan.Zero, 0, 0, []);

        // Windows 上 UDP socket 收到 ICMP「端口不可达」之后，下一次 Receive 会抛
        // WSAECONNRESET。端口扫描必然打中大量关闭的端口，几千个 ICMP 回来会把
        // 接收循环反复打断，严重时把队列撑爆（实测 SocketException 10055）。
        // SIO_UDP_CONNRESET 关掉这个行为，让 socket 老老实实只管收包。
        if (OperatingSystem.IsWindows())
        {
            try { socket.IOControl(unchecked((int)0x9800000C), [0, 0, 0, 0], null); }
            catch (SocketException) { /* 不支持就算了，只是少一层保护 */ }
        }

        // 自己发出去的 nonce。收到 Ack 时用它确认"这是对我那一发的回应"，
        // 而不是别人重放的旧包。
        var outstanding = new ConcurrentDictionary<ulong, byte>();
        var heardFrom = new ConcurrentDictionary<string, IPEndPoint>();
        var punchesHeard = 0;
        var sent = 0;

        var success = new TaskCompletionSource<IPEndPoint>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        using var stop = CancellationTokenSource.CreateLinkedTokenSource(ct);

        var receiver = Task.Run(async () =>
        {
            var buffer = new byte[2048];
            while (!stop.IsCancellationRequested)
            {
                SocketReceiveFromResult received;
                try
                {
                    received = await socket.ReceiveFromAsync(
                        buffer, SocketFlags.None, new IPEndPoint(IPAddress.Any, 0), stop.Token)
                        .ConfigureAwait(false);
                }
                catch (OperationCanceledException) { return; }
                catch (SocketException) { continue; }

                var packet = PunchProtocol.Parse(buffer.AsSpan(0, received.ReceivedBytes), secret);
                if (packet is null || packet.Value.Session != session) continue;
                var from = (IPEndPoint)received.RemoteEndPoint;

                switch (packet.Value.Type)
                {
                    case PunchProtocol.Kind.Punch:
                        // 对方的探路包到了，说明这个方向通了。回一个 Ack，让对方也确认。
                        Interlocked.Increment(ref punchesHeard);
                        heardFrom.TryAdd(from.ToString(), from);
                        try
                        {
                            var ack = PunchProtocol.Build(
                                PunchProtocol.Kind.Ack, session, packet.Value.Nonce, secret);
                            await socket.SendToAsync(ack, SocketFlags.None, from, stop.Token)
                                .ConfigureAwait(false);
                        }
                        catch (Exception e) when (e is SocketException or OperationCanceledException) { }
                        break;

                    case PunchProtocol.Kind.Ack:
                        if (outstanding.ContainsKey(packet.Value.Nonce))
                            success.TrySetResult(from);
                        break;
                }
            }
        }, stop.Token);

        var delay = punchAt - DateTimeOffset.UtcNow;
        if (delay > TimeSpan.Zero)
        {
            log?.Invoke($"等待约定打洞时刻，还有 {delay.TotalMilliseconds:0} ms");
            try { await Task.Delay(delay, ct).ConfigureAwait(false); }
            catch (OperationCanceledException) { }
        }

        log?.Invoke($"开始打洞：{peerCandidates.Count} 个对端候选，预算 {budget.TotalSeconds:0.#}s"
                    + (sweepPorts ? "，准确地址打不通会展开端口扫描" : "，本机地址对方预测不了，不扫描"));
        var started = DateTimeOffset.UtcNow;
        var deadline = started + budget;
        var sweepCursor = 0;

        while (DateTimeOffset.UtcNow < deadline && !success.Task.IsCompleted && !ct.IsCancellationRequested)
        {
            foreach (var candidate in peerCandidates)
                sent += await SendPunchAsync(socket, candidate, session, secret, outstanding, ct)
                    .ConfigureAwait(false);

            // 准确地址打不通，才展开端口扫描。IPv6 没有 NAT，扫了也没意义。
            if (sweepPorts && DateTimeOffset.UtcNow - started > SweepAfter)
            {
                foreach (var candidate in peerCandidates)
                {
                    if (candidate.AddressFamily != AddressFamily.InterNetwork) continue;
                    for (var i = 0; i < SweepPerRound; i++)
                    {
                        var offset = sweepCursor++ % (SweepBelow + SweepAbove) - SweepBelow;
                        if (offset == 0) continue;
                        var port = candidate.Port + offset;
                        if (port is < 1 or > 65535) continue;
                        sent += await SendPunchAsync(socket,
                            new IPEndPoint(candidate.Address, port), session, secret, outstanding, ct)
                            .ConfigureAwait(false);
                    }
                }
            }

            var finished = await Task.WhenAny(success.Task, Task.Delay(SendInterval, ct))
                .ConfigureAwait(false);
            if (finished == success.Task) break;
        }

        // 成功后多留一会儿接收循环，好让还在打的对方能收到我们的 Ack。
        if (success.Task.IsCompletedSuccessfully)
        {
            try { await Task.Delay(AckGrace, ct).ConfigureAwait(false); }
            catch (OperationCanceledException) { }
        }

        await stop.CancelAsync().ConfigureAwait(false);
        try { await receiver.ConfigureAwait(false); } catch { }

        var peer = success.Task.IsCompletedSuccessfully ? success.Task.Result : null;
        var elapsed = DateTimeOffset.UtcNow - started;
        if (peer is not null)
            log?.Invoke($"打洞成功 {peer}，用时 {elapsed.TotalMilliseconds:0} ms，发出 {sent} 个包");
        else
            log?.Invoke($"打洞失败：发出 {sent} 个包，收到对方探路包 {punchesHeard} 个");

        return new Outcome(peer, elapsed, sent, punchesHeard, heardFrom.Values.ToList());
    }

    /// <summary>发一个探路包，登记 nonce 好认回来的 Ack。发失败当没发过。</summary>
    private static async Task<int> SendPunchAsync(
        Socket socket, IPEndPoint target, ulong session, byte[] secret,
        ConcurrentDictionary<ulong, byte> outstanding, CancellationToken ct)
    {
        var nonce = (ulong)Random.Shared.NextInt64();
        outstanding[nonce] = 0;
        try
        {
            var punch = PunchProtocol.Build(PunchProtocol.Kind.Punch, session, nonce, secret);
            await socket.SendToAsync(punch, SocketFlags.None, target, ct).ConfigureAwait(false);
            return 1;
        }
        catch (Exception e) when (e is SocketException or OperationCanceledException) { return 0; }
    }

    /// <summary>
    /// 通路建立后定期发保活，维持 NAT 映射。运营商 NAT 的 UDP 映射老化时间通常
    /// 只有几十秒，停发就会被回收，之后对方再发过来就进不来了。
    /// </summary>
    public static async Task KeepAliveAsync(
        Socket socket, IPEndPoint peer, ulong session, byte[] secret,
        TimeSpan interval, CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try
            {
                var packet = PunchProtocol.Build(
                    PunchProtocol.Kind.Keepalive, session, (ulong)Random.Shared.NextInt64(), secret);
                await socket.SendToAsync(packet, SocketFlags.None, peer, ct).ConfigureAwait(false);
            }
            catch (Exception e) when (e is SocketException) { }
            try { await Task.Delay(interval, ct).ConfigureAwait(false); }
            catch (OperationCanceledException) { return; }
        }
    }
}
