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
    /// 自己这侧成功之后，还要继续替对方回 Ack 的**上限**。
    ///
    /// 两侧极少同时成功：先收到 Ack 的那一方如果立刻收摊，对方的探路包就没人
    /// 应答，会一直打到预算耗尽然后判失败——一边以为通了、一边以为没通。
    ///
    /// 但这曾经是**无条件**等满 2 秒，而且 elapsed 在它之后才算，于是日志里
    /// "打洞成功用时"永远是 2.0 秒出头——那个高度一致的数字来自这个常量，不是网络。
    /// 减掉它，实测的真实打洞时延是 20~197ms。更糟的是这 2 秒是真实阻塞：它挡在
    /// udp.Close() 和 QUIC 接手之间，两侧并行叠加，白白占掉总建立时延的约四分之一。
    ///
    /// 现在它只是上限。正常情况下 <c>punchesHeard &gt; 0</c> 就说明我们已经替对端
    /// 回过 Ack，那之后对端能否确认已经不取决于我们再多留多久——再留
    /// <see cref="AckRedundancy"/> 就够冗余了。
    /// </summary>
    private static readonly TimeSpan AckGrace = TimeSpan.FromSeconds(2);

    /// <summary>
    /// 已经回过 Ack 之后再多留一会儿，防那一个 Ack 正好丢了。
    ///
    /// 三个 <see cref="SendInterval"/>：对端每 50ms 还会再打一次探路包，我们会再回
    /// 一次。固定等 2 秒相比之下并没有多给任何保障——只是等了 40 个间隔而不是 3 个。
    /// </summary>
    private static readonly TimeSpan AckRedundancy = TimeSpan.FromMilliseconds(150);

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

    /// <summary>
    /// 每轮扫几个。这个数乘以 1/SendInterval 就是发包速率，而速率才是真正的约束。
    ///
    /// 原来是 24，配上 50ms 一轮就是 **480 包/秒**——按每包 60 字节约 29 KB/s。
    /// 问题有两个，实测都撞上了：
    ///   · 玩家那条线路对 UDP 限速到 3 KB/s（正是当初要做 TCP 隧道的原因），
    ///     480 pps 远在预算之上，连同直连地址那几个包一起被丢掉，双方各发三千
    ///     多个包、互相收到 0。
    ///   · 在对端的运营商 NAT 看来，这就是标准端口扫描。
    ///
    /// 降到 4（80 包/秒，约 5 KB/s）。覆盖面确实小了，但覆盖面只在"对端映射不可
    /// 预测"时才值钱；两边都是端点无关映射时，准确地址那一个包就够了，扫描纯属
    /// 陪跑。真正的解法是把 NAT 类型也传给对端、可预测时根本不扫——那要改线上
    /// 的候选格式，得等客户端都更新完再做。
    /// </summary>
    private const int SweepPerRound = 4;

    /// <summary>
    /// 本机映射不可预测时，额外开几个 socket 一起朝对端已知端点发（生日攻击）。
    ///
    /// 这是对称型 NAT 能不能稳定打通的关键。对称型那一侧的外部端口连它自己都不知道
    /// （朝反射器和朝对端拿到的是两个不同的端口），所以只能由对端扫描去撞。撞的概率
    /// 是「对端扫了 M 个端口 / 端口空间里在用的 K 个」——单个 socket 就一发。
    ///
    /// 多开 N 个 socket 各发一遍，就变成 1−(1−M/K)^N：只要**任意**一对撞上就通。
    /// 实测蜂窝（中国移动，UDP 地址相关型）单 socket 6 次 3 成，也就是 M/K≈50%；
    /// N=16 时理论上 1−0.5^16，远超"够用"。
    ///
    /// 为什么这不是"端口扫描"：这些 socket 都只朝对端**那一个**已知端点发，源端口
    /// 多、目标端口唯一。真正会被运营商认成扫描的是反过来——一个源打几千个目标端口，
    /// 那是 SweepPerRound 那一侧的事，代价在那里已经压过了。
    ///
    /// 16 个 socket × 1 个目标 / 50ms ≈ 320 包/秒、约 19 KB/s。今天实测过这条链路
    /// 扛 2 MB/s 定速 UDP 零丢包，这个量级不构成风险。
    /// </summary>
    private const int DecoySockets = 16;

    /// <param name="PeerLoadRequest">
    /// 打洞期间对端已经发来的洞压测请求（nonce 里编着计划）。对端只有在它自己判定
    /// 打通之后才会发这个包，所以收到它本身就是成功的证据；而且它已经被打洞的收包
    /// 循环吃掉了，必须原样交给应答方，否则对端要等一次 250ms 的重发。
    /// </param>
    public sealed record Outcome(
        IPEndPoint? Peer,
        TimeSpan Elapsed,
        int Sent,
        int PunchesHeard,
        IReadOnlyList<IPEndPoint> HeardFrom,
        Socket? Winner = null,
        ulong? PeerLoadRequest = null,
        bool PeerFinished = false)
    {
        public bool Success => Peer is not null;
    }

    /// <summary>
    /// 应答方（agent）在自己开了诱饵时，打通后再等对端表态的上限。
    ///
    /// 两边各自认定的那一对端口可能不同（谁的 Ack 先到谁就是赢家）。对端判定成功后发来的
    /// 第一个压测请求会指明它真正在用的那一对，但前提是我们那一侧对应的诱饵还开着——原先
    /// 成功 150ms 后就把没赢的诱饵全关了，对端选中的恰好是别的那个时，它的压测请求和 QUIC
    /// 都会打到一个已经关掉的端口上。只在应答方、且确实开了诱饵时才等。
    /// </summary>
    private static readonly TimeSpan PeerChoiceWait = TimeSpan.FromMilliseconds(600);

    public static async Task<Outcome> PunchAsync(
        Socket socket,
        IReadOnlyList<IPEndPoint> peerCandidates,
        ulong session,
        byte[] secret,
        DateTimeOffset punchAt,
        TimeSpan budget,
        bool sweepPorts,
        Action<string>? log,
        CancellationToken ct,
        IReadOnlyList<IPEndPoint>? alternates = null,
        bool awaitPeerChoice = false)
    {
        if (peerCandidates.Count == 0)
            return new Outcome(null, TimeSpan.Zero, 0, 0, [], null);
        // 对端备用 socket 的出口（见 P2PTags.UdpAlternate）：照准确地址一样打，诱饵也朝它们
        // 发，但不参与对称型判断、也不扫——它们不是"同一个 socket 的多个映射"。
        var exact = alternates is { Count: > 0 }
            ? peerCandidates.Concat(alternates.Where(a => !peerCandidates.Contains(a))).ToList()
            : peerCandidates;

        // Windows 上 UDP socket 收到 ICMP「端口不可达」之后，下一次 Receive 会抛
        // WSAECONNRESET。端口扫描必然打中大量关闭的端口，几千个 ICMP 回来会把
        // 接收循环反复打断，严重时把队列撑爆（实测 SocketException 10055）。
        // SIO_UDP_CONNRESET 关掉这个行为，让 socket 老老实实只管收包。
        static void SilenceIcmpResets(Socket target)
        {
            if (!OperatingSystem.IsWindows()) return;
            try { target.IOControl(unchecked((int)0x9800000C), [0, 0, 0, 0], null); }
            catch (SocketException) { /* 不支持就算了，只是少一层保护 */ }
        }
        SilenceIcmpResets(socket);

        // 本机映射不可预测时，多开几个 socket 一起朝对端已知端点发，好让对端的扫描
        // 有 N 倍的机会撞上其中一个（生日攻击，见 DecoySockets）。
        //
        // 只在 !sweepPorts 时开——那个标志的含义正是"我方地址对端预测不了"，也就是
        // 需要被找到的那一侧。反过来两侧都端点无关时，准确地址一发就中，多开纯浪费。
        var decoys = new List<Socket>();
        if (!sweepPorts)
        {
            for (var i = 0; i < DecoySockets; i++)
            {
                try
                {
                    var extra = new Socket(
                        AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
                    extra.Bind(new IPEndPoint(IPAddress.Any, 0));
                    SilenceIcmpResets(extra);
                    decoys.Add(extra);
                }
                catch (SocketException) { break; }
            }
            if (decoys.Count > 0)
                log?.Invoke($"本机映射对端预测不了，额外开 {decoys.Count} 个端口一起打"
                            + "（靠数量换命中率，不是端口扫描）");
        }

        // 自己发出去的 nonce。收到 Ack 时用它确认"这是对我那一发的回应"，
        // 而不是别人重放的旧包。
        var outstanding = new ConcurrentDictionary<ulong, byte>();
        // 连"是哪个 socket 听到的"一起记：兜底路径（没收到 Ack 但收到过对端探路包）
        // 也要能说出该把哪个本地端口交给 QUIC。
        var heardFrom = new ConcurrentDictionary<string, (IPEndPoint Peer, Socket Via)>();
        var punchesHeard = 0;
        var strangers = 0;
        var sent = 0;

        // 成功时必须连"是哪个 socket 收到的"一起记下来：多开了诱饵之后，打通的那条
        // 路属于某一个具体的本地端口，NAT 映射按本地端口记账——交给 QUIC 的必须是
        // 那一个，拿错了等于把刚打好的洞扔了。
        var success = new TaskCompletionSource<(IPEndPoint Peer, Socket Via)>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        // 对端已经收工、开始问压测了：它不再需要我们的 Ack，收尾宽限一律免掉。
        var peerDone = 0;
        var peerFinished = 0;
        long peerLoadRequest = 0;
        // 已经靠 Ack 记下一对之后，对端的压测请求又指明了另一对：以后者为准。
        Tuple<IPEndPoint, Socket>? overrideWinner = null;
        using var stop = CancellationTokenSource.CreateLinkedTokenSource(ct);

        async Task ReceiveLoopAsync(Socket mine)
        {
            var buffer = new byte[2048];
            while (!stop.IsCancellationRequested)
            {
                SocketReceiveFromResult received;
                try
                {
                    received = await mine.ReceiveFromAsync(
                        buffer, SocketFlags.None, new IPEndPoint(IPAddress.Any, 0), stop.Token)
                        .ConfigureAwait(false);
                }
                catch (OperationCanceledException) { return; }
                catch (ObjectDisposedException) { return; }
                catch (SocketException) { continue; }

                var packet = PunchProtocol.Parse(buffer.AsSpan(0, received.ReceivedBytes), secret);
                if (packet is null || packet.Value.Session != session)
                {
                    // 到了但认不出来：HMAC 不对（两边密钥派生不一致）或会话号不匹配。
                    // 必须单独计数——否则它和"什么都没到"在日志里长得一模一样，
                    // 而这两者一个是代码 bug、一个是网络不通，排查方向完全相反。
                    //
                    // 只数**长得像我们的包**（开头是魔数）的那些。出口探测迟到的反射器
                    // 回包（JSON）也会落在这个 socket 上，把它们算进来会让日志错误地
                    // 断言"密钥对不上、是代码问题"，把排查引到完全错误的方向。
                    if (received.ReceivedBytes >= 4
                        && System.Buffers.Binary.BinaryPrimitives.ReadUInt32BigEndian(buffer) == PunchProtocol.Magic)
                        Interlocked.Increment(ref strangers);
                    continue;
                }
                var from = (IPEndPoint)received.RemoteEndPoint;

                switch (packet.Value.Type)
                {
                    case PunchProtocol.Kind.Punch:
                        // 对方的探路包到了，说明这个方向通了。回一个 Ack，让对方也确认。
                        Interlocked.Increment(ref punchesHeard);
                        heardFrom.TryAdd(from.ToString(), (from, mine));
                        try
                        {
                            var ack = PunchProtocol.Build(
                                PunchProtocol.Kind.Ack, session, packet.Value.Nonce, secret);
                            // 必须从收到它的那个 socket 回：源端口一变，对端 NAT 的
                            // 过滤就对不上，这个 Ack 等于没发。
                            await mine.SendToAsync(ack, SocketFlags.None, from, stop.Token)
                                .ConfigureAwait(false);
                        }
                        catch (Exception e) when (e is SocketException or OperationCanceledException) { }
                        break;

                    case PunchProtocol.Kind.Ack:
                        if (outstanding.ContainsKey(packet.Value.Nonce))
                            success.TrySetResult((from, mine));
                        break;

                    // 对端判定打通之后会立刻朝**它认定的那一对端口**发压测请求。
                    //
                    // 这比 Ack 更有信息量：它告诉我们对端实际在用哪个外部端口。实测蜂窝
                    // 那侧开了诱饵端口时，两边各自认定的端口对经常不是同一对——服务端
                    // 记的是 :6535，而客户端真正在用的是 :6376。服务端照 :6535 等压测
                    // 请求，6 次里 5 次一个都没等到，白耗 1.2 秒，预热形同虚设。
                    // 以对端发来的这个包为准，两边就一定落在同一对上。
                    // 对端连压测都做完了（或者压测被关了、直接收工）：同样是"它已经判定打通、
                    // 而且就在用这一对"的证据。不接住它的话，应答方要白等满第一个请求的
                    // 耐心（600ms）才去起 QUIC 监听，对端的第一个 Initial 就被吞了。
                    case PunchProtocol.Kind.LoadDone:
                        Interlocked.Exchange(ref peerFinished, 1);
                        Interlocked.Exchange(ref peerDone, 1);
                        if (!success.TrySetResult((from, mine)))
                            Volatile.Write(ref overrideWinner, Tuple.Create(from, mine));
                        break;

                    case PunchProtocol.Kind.LoadRequest:
                        Interlocked.Exchange(ref peerLoadRequest, (long)packet.Value.Nonce);
                        Interlocked.Exchange(ref peerDone, 1);
                        // 覆盖掉可能已经记下的另一对：对端认的那一对才是之后真正走流量的。
                        if (!success.TrySetResult((from, mine)))
                            Volatile.Write(ref overrideWinner, Tuple.Create(from, mine));
                        break;
                }
            }
        }

        var receivers = new List<Task> { Task.Run(() => ReceiveLoopAsync(socket), stop.Token) };
        foreach (var decoy in decoys)
        {
            var mine = decoy;
            receivers.Add(Task.Run(() => ReceiveLoopAsync(mine), stop.Token));
        }

        // 拿到会合信息就开打，不再干等约定时刻。
        //
        // 约定时刻原本是为了"两边同时发"，但 UDP 打洞并不需要同时：先发的一方只是
        // 在自己的 NAT 上先开了口子，它发到对面的包被对面 NAT 丢掉，无害；等对面也
        // 开始发，下一个 50ms 周期就通了。真正决定何时能通的是**较晚那一方何时开始
        // 发**，而约定时刻只是给较晚那一方（agent 轮询取件）留的余量。
        //
        // 实测这一格固定 4.0 秒，占建立总时长的三分之一，而 agent 取件实际只要
        // 0.1~1.2 秒。现在两边都是拿到就发，预算的终点仍按约定时刻 + 预算算，
        // 所以对端是旧版本、仍在等约定时刻时，我们照样陪它打满原来那一段。
        var delay = punchAt - DateTimeOffset.UtcNow;
        var started = DateTimeOffset.UtcNow;
        var deadline = (delay > TimeSpan.Zero ? punchAt : started) + budget;

        // 把对端地址写出来，不只写个数。打洞失败时最要紧的问题是"我打的这个地址
        // 到底对不对"——两边日志各有一半，只有都印出地址才能对出是谁的候选陈旧了。
        // 实测有过双方各发三千多个包、互相收到 0 的情况，没有地址就只能靠猜。
        log?.Invoke($"开始打洞：对端 [{string.Join("，", peerCandidates)}]"
                    + (exact.Count > peerCandidates.Count
                        ? $"，备用 [{string.Join("，", exact.Skip(peerCandidates.Count))}]" : "")
                    + $"，预算 {budget.TotalSeconds:0.#}s"
                    + (delay > TimeSpan.Zero ? $"（比约定时刻早 {delay.TotalMilliseconds:0} ms 开打）" : "")
                    + (sweepPorts ? "，准确地址打不通会展开端口扫描" : "，本机地址对方预测不了，不扫描"));
        var sweepCursor = 0;

        // 对端是不是对称型：同一个地址报了多个不同端口，就说明它每个目标拿到的映射
        // 都不一样——那正是地址相关型的定义。
        //
        // 这个判据不需要改候选格式，也不需要对端升级：候选列表里本来就带着答案。
        // （实测：蜂窝端报 6 个端口 36.28.73.47:6362~6427，家宽端报 1 个。）
        // 判成对称型就立刻开扫，不再白等那 1.5 秒。判错的代价很小：多扫一会儿而已，
        // 准确地址那一发每轮照发。
        var peerLikelySymmetric = peerCandidates
            .GroupBy(x => x.Address)
            .Any(g => g.Select(x => x.Port).Distinct().Count() > 1);
        var sweepAfter = peerLikelySymmetric ? TimeSpan.Zero : SweepAfter;
        if (sweepPorts && peerLikelySymmetric)
            log?.Invoke("对端同一地址报了多个端口，判定为对称型——立刻开扫，不等准确地址");

        while (DateTimeOffset.UtcNow < deadline && !success.Task.IsCompleted && !ct.IsCancellationRequested)
        {
            foreach (var candidate in exact)
            {
                sent += await SendPunchAsync(socket, candidate, session, secret, outstanding, ct)
                    .ConfigureAwait(false);
                // 诱饵只朝**准确地址**发，不参与扫描：它们存在的意义是多开几个不同的
                // 外部端口让对端的扫描有机会撞上，而不是自己去搜。
                foreach (var decoy in decoys)
                    sent += await SendPunchAsync(decoy, candidate, session, secret, outstanding, ct)
                        .ConfigureAwait(false);
            }

            // 准确地址打不通，才展开端口扫描。IPv6 没有 NAT，扫了也没意义。
            //
            // 但对端明显是对称型时，"准确地址"根本不存在——它报上来的端口是它探反射器
            // 时的映射，朝我们发包用的是另一个。那种情况下先等 1.5 秒纯属白等，实测
            // 蜂窝端的打洞耗时里有 1.5 秒就是这么来的（总耗时 1.9~2.1 秒）。
            if (sweepPorts && DateTimeOffset.UtcNow - started > sweepAfter)
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
        //
        // 判据是"我们是否已经替对端回过 Ack"，不是固定时长：收到过对端的探路包
        // （punchesHeard > 0）就意味着接收循环已经朝它回过 Ack。等到这一刻为止，
        // 再补一小段冗余就收工。
        //
        // 还没收到过对端探路包的情况也真实存在——对端靠端口扫描找到我们时，它的
        // 探路包打的是陈旧候选、从没到过我们这儿，我们只收到它的 Ack。那种情况下
        // 才需要一直等到 AckGrace 上限，让它的扫描包有机会到达。
        //
        // 对端已经发来压测请求时整段免掉：那说明它自己早就判定成功了，不再需要我们的 Ack。
        if (success.Task.IsCompletedSuccessfully && Volatile.Read(ref peerDone) == 0)
        {
            var cap = DateTimeOffset.UtcNow + AckGrace;
            while (Volatile.Read(ref punchesHeard) == 0 && Volatile.Read(ref peerDone) == 0
                   && DateTimeOffset.UtcNow < cap && !ct.IsCancellationRequested)
            {
                try { await Task.Delay(TimeSpan.FromMilliseconds(25), ct).ConfigureAwait(false); }
                catch (OperationCanceledException) { break; }
            }
            // 冗余只在"确实回过 Ack"这一路上补。等满上限那一路已经等够久了，
            // 再叠 150ms 只会让最慢的情形比改之前还慢。
            if (Volatile.Read(ref punchesHeard) > 0 && Volatile.Read(ref peerDone) == 0)
            {
                using var early = CancellationTokenSource.CreateLinkedTokenSource(ct);
                var redundancy = Task.Delay(AckRedundancy, early.Token);
                while (!redundancy.IsCompleted && Volatile.Read(ref peerDone) == 0)
                {
                    try { await Task.WhenAny(redundancy, Task.Delay(25, ct)).ConfigureAwait(false); }
                    catch (OperationCanceledException) { break; }
                }
                early.Cancel();
            }
        }

        // 应答方开了诱饵：等对端表态它用的是哪一对，理由见 PeerChoiceWait。
        if (awaitPeerChoice && decoys.Count > 0 && success.Task.IsCompletedSuccessfully)
        {
            var until = DateTimeOffset.UtcNow + PeerChoiceWait;
            while (Volatile.Read(ref peerDone) == 0 && DateTimeOffset.UtcNow < until
                   && !ct.IsCancellationRequested)
            {
                try { await Task.Delay(TimeSpan.FromMilliseconds(25), ct).ConfigureAwait(false); }
                catch (OperationCanceledException) { break; }
            }
        }

        await stop.CancelAsync().ConfigureAwait(false);
        foreach (var task in receivers) { try { await task.ConfigureAwait(false); } catch { } }

        var won = success.Task.IsCompletedSuccessfully ? success.Task.Result : default;
        if (Volatile.Read(ref overrideWinner) is { } corrected
            && (!corrected.Item1.Equals(won.Peer) || !ReferenceEquals(corrected.Item2, won.Via)))
        {
            log?.Invoke($"对端的压测请求来自 {corrected.Item1}，以它为准（Ack 记下的是 {won.Peer}）");
            won = (corrected.Item1, corrected.Item2);
        }
        var peer = success.Task.IsCompletedSuccessfully ? won.Peer : null;
        var winner = success.Task.IsCompletedSuccessfully ? won.Via : null;

        var elapsed = DateTimeOffset.UtcNow - started;

        // 收到 Ack 是最强证据，但不是唯一证据。
        //
        // 实测撞到过两侧判定不一致：对方的探路包到了我们这里（HMAC 验过），我们按协议
        // 回了 Ack，对方收到 Ack 判定成功；而我们自己发出去的探路包因为对方报上来的
        // 地址对我们不成立、始终没拿到 Ack，于是判定失败、没有启动监听。结果对方以为
        // 通了，连过来却没人在听，白等一整个握手超时。
        //
        // 收到一个验证通过的探路包，本身就证明了「对方能到达我们」；而我们回 Ack 时
        // 用的是观测到的真实源地址，那条路径上的 NAT 映射已经因此打开。这就足够继续
        // 往下走了。宁可多起一次监听（对方不来，20 秒后自己收摊），也不能让明明打通
        // 的链路因为判据写窄而作废。
        if (peer is null && heardFrom.Count > 0)
        {
            var observed = heardFrom.Values.First();
            peer = observed.Peer;
            winner = observed.Via;
            log?.Invoke($"未收到 Ack，但收到对方 {punchesHeard} 个探路包，按观测到的地址 {peer} 继续");
        }

        if (peer is not null)
            log?.Invoke($"打洞成功 {peer}，用时 {elapsed.TotalMilliseconds:0} ms，发出 {sent} 个包");
        else
            log?.Invoke($"打洞失败：发出 {sent} 个包，收到对方有效探路包 {punchesHeard} 个"
                    + (strangers > 0
                        ? $"；另有 {strangers} 个包到达但校验不通过——两边密钥或会话号对不上，这是代码问题不是网络问题"
                        : "；期间一个包都没到达，链路层就被挡住了"));

        // 兜底：认出了对端却没记住是哪个 socket（理论上不会走到），就按主 socket 算。
        // 宁可用主 socket 的端口试一次，也不要返回 null 让上层拿不到端口。
        if (peer is not null) winner ??= socket;

        // 没赢的诱饵全部收掉。必须在 winner 定下来之后再收——兜底路径可能把 winner
        // 指到某个诱饵上，先收就把刚打通的那个洞关了。
        foreach (var decoy in decoys)
            if (!ReferenceEquals(decoy, winner))
                try { decoy.Close(); } catch { }

        var pendingLoad = Volatile.Read(ref peerDone) != 0
            ? (ulong?)(ulong)Interlocked.Read(ref peerLoadRequest)
            : null;
        return new Outcome(
            peer, elapsed, sent, punchesHeard,
            heardFrom.Values.Select(x => x.Peer).ToList(), winner, pendingLoad,
            Volatile.Read(ref peerFinished) != 0);
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
