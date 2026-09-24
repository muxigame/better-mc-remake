using System.Net;
using System.Net.Sockets;
using System.Text.Json;

namespace BatterMC.Protocol;

/// <summary>
/// TCP 打洞。
///
/// 为什么曾经要有 TCP 版，以及那个理由为什么是错的——这段务必读完再动手改：
///
/// 当初引入它，是因为"有些线路对持续的 UDP 大流量限速极狠（实测 3 KB/s）"。
/// 后来把这条前提逐项量了一遍，它不成立：
///
/// <list type="bullet">
/// <item>两条真实线路（本机 CGNAT、玩家家宽）各自朝公网服务器灌定速 UDP，
///   2 MB/s 持续 20 秒，**零丢包**；4 MB/s 时撞到的上限和 TCP 完全一样。</item>
/// <item>突发也不致命：15 毫秒内甩出 3200 个包只是丢一部分，之后的涓流照样通，
///   不存在"被限速器记恨"这回事。</item>
/// <item>本机 hairpin 打洞 + QUIC 跑到 10.3 MB/s，同一份代码、同一台路由器。</item>
/// </list>
///
/// 那 3 KB/s 测的不是线路，是**我们自己的隧道**在某条特定跨 NAT 路径上的表现。
/// 所以别再把"绕开 UDP 限速"当成 TCP 打洞的理由去做设计决策。
///
/// 它现在留下来的理由是另一个，而且成立：多一条**独立的**路。UDP 完全被封的网络、
/// UDP 侧是对称型而 TCP 侧是锥形的 NAT，都真实存在。它是并行的第二条腿，不是绕路。
///
/// **不要在本机 hairpin 上验证 TCP 打洞。** 实测两侧的候选是交错的
/// （1783/1788 对 1781/1784）——同一台 NAT 上两侧共用一个端口游标，于是我们自己
/// 这一侧的一百多次拨号会直接推动对侧的映射。命中模型假设两侧的漂移量 δ 互相独立，
/// 这个前提在 hairpin 上不成立，所以 hairpin 既证不明也证不伪，只会浪费时间。
/// 要验证必须有真正跨两台 NAT 的对端。
///
/// 和 UDP 版的差别在于 NAT 对 TCP 的映射行为往往更严：实测本机 UDP 是端点无关
/// （对所有目标同一个映射），而 TCP 是**地址+端口相关 + 顺序分配**——同一台反射器
/// 的两个端口都会拿到两个不同的映射。所以照着反射器看到的端口打必然打偏，只能往上预测。
///
/// 要预测多远由这条线路的端口消耗速度决定，而这个速度包含**整条线路上所有设备**的
/// 连接，不只是我们自己的。实测这条家宽 20.4 个/秒（22 秒爬 456 个，零回退），
/// 候选从探到到开打隔 5~15 秒，也就是要追 100~300 个。这也意味着 TCP 打洞在这种
/// NAT 上是概率事件：窗口开得再大也只是提高命中率，不保证成功——所以它和 UDP 并行
/// 跑、谁通用谁，两条都不成还有隧道和中转兜底。
/// </summary>
public static class TcpPuncher
{
    /// <summary>
    /// 单次 connect 的超时。
    ///
    /// 打洞是拼覆盖面，单次不该久等：目标都在国内，SYN-ACK 一秒回不来就是没打通。
    /// 这个值还直接决定限流下的吞吐——并发封顶 64、单次 1 秒，就是 64 次/秒。
    /// </summary>
    private static readonly TimeSpan AttemptTimeout = TimeSpan.FromSeconds(1);

    /// <summary>握手要在打洞窗口里完成，不能拖成又一个超时来源。</summary>
    private static readonly TimeSpan HandshakeTimeout = TimeSpan.FromSeconds(3);

    /// <summary>
    /// 端口预测窗口。
    ///
    /// 顺序分配的 NAT 上，真实端口在反射器看到的那个之上——中间隔的是这条线路上
    /// 同期建立的**所有**连接，不只是我们自己的。原先按"隔几个到几十个"给了 32，
    /// 实测下来差了一个数量级：这条家宽 22 秒里外部端口从 27499 爬到 27955，
    /// 平均 20.4 个/秒（2 秒步长 17~69，11 步零回退）。而候选从探到到真正开打
    /// 要隔 5~15 秒，漂移是 100~300 个，32 的窗口等于必然打空。
    ///
    /// 窗口大小由"安全的拨号量"倒推，不是由"想覆盖多少漂移"决定。
    ///
    /// 想覆盖 20 个/秒 × 十几秒的陈旧度，需要几百个目标；但那个量级的并发 SYN
    /// 会打爆对端的 NAT 会话表，连能用的 UDP 打洞一起拖下水（见 LeadTime）。
    /// 在"不能伤到对端"这条约束下，只能反过来：安全量是 256 次，四个候选各 64。
    ///
    /// 老实说这个窗口只够覆盖 3 秒左右的漂移，在对称型 + 顺序分配的 NAT 上命中
    /// 是小概率。保留它是因为在候选足够新、或者对端 NAT 温和的线路上确实能中，
    /// 而代价已经压到不会妨碍别的路径。
    /// </summary>
    private const int PredictAhead = 64;

    /// <summary>
    /// 对表过的候选用这个深度。
    ///
    /// 不是拍的：按 <see cref="P2PTags.PredictStep"/> 里那个方程，步长 1/2 时命中所需的
    /// 下标是 <c>i = δ_A + 2δ_B − 3</c>。对表提前 900ms、背景端口消耗 20.4 个/秒，
    /// δ≈18，代入得 i≈51、j≈34。给到 96 是留出到 1.6 秒漂移的余量——线路忙的时候
    /// δ 会更大，而多拨几十个 SYN 的代价远小于打空一整轮。
    ///
    /// 原来是 16。那个值只够覆盖 δ≈6，也就是 300ms 的漂移，而对表本身就要 900ms。
    /// </summary>
    private const int NarrowAhead = 96;

    /// <summary>
    /// 一次打洞最多拨多少次，以及最多几个同时在飞。
    ///
    /// 这两个数是有教训的：窗口刚开大时一轮甩出 770 个并发 connect，三轮 2310 个，
    /// 结果同一时刻的 UDP 打洞（发出 162 包、收到 0）和到中转的 TCP 隧道**一起**
    /// 失败了——路由器的 NAT 表被我们自己打爆，为了打一个洞把整条线路弄瘫。
    ///
    /// 但 24 这个并发又压得过头了，而且和上面那段注释自相矛盾：单次超时 1 秒、
    /// 并发 24，就是 24 次/秒，而预算只有 6 秒（10 秒减去让给 UDP 的 4 秒）——
    /// 256 个目标里有 110 多个**从来没被拨过**，扫描顺序又是按偏移从小到大，
    /// 等于窗口的后半截根本不存在，日志里却照样写着"256 个目标"。
    ///
    /// 64 是实测过安全的量级：今天量过一次 130 个并发 SYN 打出去，同一时刻一条
    /// 1 MB/s 的 UDP 流逐秒速率完全没抖（994→994→987→1001）。64 × 6 秒 = 384，
    /// 够把 256 个目标拨完，离 2310 那个翻车的量级还差一个数量级。
    /// </summary>
    private const int MaxDials = 256;

    /// <summary>
    /// 同时在飞的上限。
    ///
    /// 这个值不只是"别打爆 NAT 表"的安全阀，它还直接决定端口算术成不成立：
    /// 命中模型假设第 i 次拨号拿到的外部端口是 a+i，而端口是在 connect() 那一刻
    /// 分配的。并发压得比目标数小，拨号就被摊成好几批，背景流量（实测 20.4 个/秒）
    /// 会插进我们自己的端口序列，a+i 直接失真。所以它要 ≥ 窄窗口下的实际目标数。
    ///
    /// 安全性这边有实测垫底：今天量过 130 个并发 SYN 打出去，同时刻一条 1 MB/s 的
    /// UDP 流逐秒速率完全没抖（994→994→987→1001）。翻车那次是 2310 个，差一个数量级。
    /// 另外本机实测拨错的目标会在 2~12ms 内收到 RST 而不是等满 1 秒超时，所以这么多
    /// 并发实际只占用几十毫秒。
    /// </summary>
    private const int MaxInFlight = 200;

    /// <summary>
    /// TCP 比 UDP 晚开打多久。
    ///
    /// 两条路同时全速打会互相拆台：TCP 那几百个 SYN 落到对端家里的 CGNAT 上，
    /// 会话表一满，连带把同一时刻的 UDP 打洞包也丢掉。实测过两次同样的签名——
    /// 本机路由器上 2310 个并发 connect 之后 "UDP 发出 162 包收到 0"，玩家那边
    /// CGNAT 上 700 个 SYN 之后双方各发三千多包、互相收到 0。
    ///
    /// UDP 打通只要 2 秒出头（实测 2.0~2.2s），所以先把这 4 秒干干净净留给它，
    /// UDP 不行再让 TCP 上。两边都从共同的约定时刻算这个偏移，不用额外对表。
    /// </summary>
    public static readonly TimeSpan LeadTime = TimeSpan.FromSeconds(4);

    private const byte RoleInitiator = 1;
    private const byte RoleAcceptor = 2;

    public sealed record Outcome(Socket? Socket, IPEndPoint? Peer, TimeSpan Elapsed, int Attempts)
    {
        public bool Success => Socket is not null;
    }

    /// <summary>
    /// 问 TCP 反射器"我在公网上长什么样"。
    ///
    /// 必须用独立的 TCP 反射器：NAT 给 TCP 和 UDP 分配的是两套互不相干的映射，
    /// 拿 UDP 那个地址来打 TCP 等于打空。
    /// </summary>
    public static async Task<IPEndPoint?> DiscoverAsync(
        string reflectorHost, int reflectorPort, int localPort, CancellationToken ct)
    {
        using var socket = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
        socket.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.ReuseAddress, true);
        socket.Bind(new IPEndPoint(IPAddress.Any, localPort));
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
        deadline.CancelAfter(TimeSpan.FromSeconds(8));
        try
        {
            await socket.ConnectAsync(reflectorHost, reflectorPort, deadline.Token).ConfigureAwait(false);
            var buffer = new byte[256];
            var got = await socket.ReceiveAsync(buffer, SocketFlags.None, deadline.Token)
                .ConfigureAwait(false);
            if (got <= 0) return null;
            using var document = JsonDocument.Parse(buffer.AsMemory(0, got));
            var root = document.RootElement;
            return IPAddress.TryParse(root.GetProperty("ip").GetString(), out var ip)
                ? new IPEndPoint(ip, root.GetProperty("port").GetInt32())
                : null;
        }
        catch (Exception error) when (error is SocketException or OperationCanceledException
                                          or JsonException or KeyNotFoundException)
        {
            return null;
        }
    }

    /// <summary>
    /// 在约定时刻同时 listen + connect，然后在打通的连接上握手点名。
    ///
    /// 两边都绑同一个本地端口是关键：NAT 的映射按本地端口记，换个端口就是另一个洞。
    ///
    /// 难点不在"打通"而在"打通了好几条，怎么保证两边守的是同一条"。实测一轮里
    /// 就能同时拨通两三条——对端报了多个候选，每个候选还带一串预测端口，凡是能
    /// hairpin 回去的都会通。早先的做法是"发起方留拨通的、接受方留接受的"，但同
    /// 一轮里拨通多条时这条规则选不出**哪一条**，两边各留一条不同的，一开流就 EOF。
    ///
    /// 现在改成显式点名：每条连接先互报身份（带 MAC，顺便挡掉扫端口的外人和
    /// hairpin 到自己身上的情况），再由发起方在选中的那条上发一个 TcpSelect，
    /// 接受方只认收到点名的那条。没有推断，两边一定落在同一条上。
    /// </summary>
    /// <param name="initiator">
    /// 发起方负责点名。客户端是发起方，服务端侧是接受方——和
    /// <see cref="MuxConnection"/> 的开流方向一致。
    /// </param>
    public static async Task<Outcome> PunchAsync(
        int localPort,
        IReadOnlyList<IPEndPoint> peerCandidates,
        ulong session,
        byte[] secret,
        DateTimeOffset punchAt,
        TimeSpan budget,
        bool predictPorts,
        bool initiator,
        bool narrowWindow,
        Action<string>? log,
        CancellationToken ct)
    {
        if (peerCandidates.Count == 0)
            return new Outcome(null, null, TimeSpan.Zero, 0);

        // 步长按角色分，两侧必须不同，否则命中方程无解——见 P2PTags.PredictStep。
        var step = P2PTags.PredictStep(initiator);
        var targets = Expand(peerCandidates, predictPorts, narrowWindow, step);
        using var stop = CancellationTokenSource.CreateLinkedTokenSource(ct);
        var winner = new TaskCompletionSource<Socket?>(TaskCreationOptions.RunContinuationsAsynchronously);
        var myRole = initiator ? RoleInitiator : RoleAcceptor;
        var claimed = 0;
        var attempts = 0;
        var wasInbound = false;

        // 监听和拨号绑同一个端口，靠 SO_REUSEADDR。对方的 SYN 如果先到，就走这条。
        using var listener = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
        listener.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.ReuseAddress, true);
        listener.Bind(new IPEndPoint(IPAddress.Any, localPort));
        listener.Listen(64);

        // 一条连接的全部判定都在这里：互报身份 → 发起方点名 / 接受方等点名。
        // 无论哪一步不过，socket 都在这里收掉，外面不用管。
        async Task VetAsync(Socket sock, bool inbound)
        {
            try
            {
                sock.NoDelay = true;
                using var limit = CancellationTokenSource.CreateLinkedTokenSource(stop.Token);
                limit.CancelAfter(HandshakeTimeout);

                await sock.SendAsync(
                        PunchProtocol.Build(PunchProtocol.Kind.TcpHello, session, myRole, secret),
                        SocketFlags.None, limit.Token).ConfigureAwait(false);

                var frame = new byte[PunchProtocol.PacketSize];
                if (!await ReadFrameAsync(sock, frame, limit.Token).ConfigureAwait(false))
                { sock.Dispose(); return; }

                // 角色和自己一样 = hairpin 连到了自己的监听口，不是对端。
                if (PunchProtocol.Parse(frame, secret) is not
                        { Type: PunchProtocol.Kind.TcpHello } hello
                    || hello.Session != session || hello.Nonce == myRole)
                { sock.Dispose(); return; }

                if (initiator)
                {
                    // 名额只有一个。抢不到的直接收掉；抢到之后发点名发失败了就把
                    // 名额让回去，别让一条刚断的连接占着位子。
                    if (Interlocked.CompareExchange(ref claimed, 1, 0) != 0)
                    { sock.Dispose(); return; }
                    try
                    {
                        await sock.SendAsync(
                                PunchProtocol.Build(PunchProtocol.Kind.TcpSelect, session, 0, secret),
                                SocketFlags.None, limit.Token).ConfigureAwait(false);
                    }
                    catch
                    {
                        Interlocked.Exchange(ref claimed, 0);
                        sock.Dispose();
                        return;
                    }
                }
                else
                {
                    // 等对端点名。这里用 stop 而不是握手超时——点名可能比握手晚不少，
                    // 对端要等自己那一轮拨号都回来才挑得出来。
                    var pick = new byte[PunchProtocol.PacketSize];
                    if (!await ReadFrameAsync(sock, pick, stop.Token).ConfigureAwait(false))
                    { sock.Dispose(); return; }
                    if (PunchProtocol.Parse(pick, secret) is not
                            { Type: PunchProtocol.Kind.TcpSelect } sel || sel.Session != session)
                    { sock.Dispose(); return; }
                }

                if (winner.TrySetResult(sock)) wasInbound = inbound;
                else sock.Dispose();
            }
            catch
            {
                try { sock.Dispose(); } catch { }
            }
        }

        var accepting = Task.Run(async () =>
        {
            while (!stop.IsCancellationRequested)
            {
                Socket inbound;
                try { inbound = await listener.AcceptAsync(stop.Token).ConfigureAwait(false); }
                catch { return; }
                _ = VetAsync(inbound, inbound: true);
            }
        }, CancellationToken.None);

        var delay = punchAt - DateTimeOffset.UtcNow;
        if (delay > TimeSpan.Zero)
        {
            log?.Invoke($"等待约定打洞时刻，还有 {delay.TotalMilliseconds:0} ms");
            try { await Task.Delay(delay, ct).ConfigureAwait(false); }
            catch (OperationCanceledException) { }
        }

        log?.Invoke($"开始 TCP 打洞：{targets.Count} 个目标"
                    + (predictPorts ? $"（端口预测步长 {step}）" : "") + $"，预算 {budget.TotalSeconds:0.#}s");

        var started = DateTimeOffset.UtcNow;
        var deadline = started + budget;

        // 限流的单趟扫描，不再一轮一轮重来。
        //
        // 之所以单趟够用：对端的映射是它在约定时刻拨号那一下建的，之后就固定了，
        // 我们要找的是一个**不动的**目标。重复拨同一批端口不会提高命中率，只会
        // 多占对方路由器的 NAT 表。按偏移从小到大扫一遍才是对的搜索顺序——偏移
        // 小对应对端候选新鲜，本来就是更可能的那一侧。
        var pacing = new SemaphoreSlim(MaxInFlight);
        var dials = new List<Task>();

        async Task DialAndVetAsync(IPEndPoint target)
        {
            try { await pacing.WaitAsync(stop.Token).ConfigureAwait(false); }
            catch { return; }
            try
            {
                var sock = await DialAsync(localPort, target, stop.Token).ConfigureAwait(false);
                if (sock is not null) await VetAsync(sock, inbound: false).ConfigureAwait(false);
            }
            finally { try { pacing.Release(); } catch { } }
        }

        foreach (var target in targets)
        {
            if (attempts >= MaxDials || winner.Task.IsCompleted
                || stop.IsCancellationRequested) break;
            attempts++;
            dials.Add(DialAndVetAsync(target));
        }

        // 必须带上 deadline。少了它，642 个目标按 64 并发、单次 1 秒算要跑满 10 秒，
        // 排队和收尾一叠加实测跑到 19 秒——远超预算，对端早放弃了。
        var scan = deadline - DateTimeOffset.UtcNow;
        if (scan < TimeSpan.Zero) scan = TimeSpan.Zero;
        await Task.WhenAny(Task.WhenAll(dials), winner.Task, Task.Delay(scan, stop.Token))
            .ConfigureAwait(false);

        // 收尾再等一段：接受方可能一条都拨不通，全靠对端点名才知道用哪条。
        var left = deadline - DateTimeOffset.UtcNow;
        if (!winner.Task.IsCompleted && left > TimeSpan.Zero)
        {
            try { await winner.Task.WaitAsync(left, stop.Token).ConfigureAwait(false); }
            catch (Exception error) when (error is TimeoutException or OperationCanceledException) { }
        }

        // 先封口再取消：封口之后晚到的 TrySetResult 会失败，那条连接自己会收掉
        winner.TrySetResult(null);
        var chosen = winner.Task.IsCompletedSuccessfully ? winner.Task.Result : null;
        await stop.CancelAsync().ConfigureAwait(false);
        try { await accepting.ConfigureAwait(false); } catch { }

        var elapsed = DateTimeOffset.UtcNow - started;
        if (chosen is null)
        {
            log?.Invoke($"TCP 打洞失败：尝试 {attempts} 次");
            return new Outcome(null, null, elapsed, attempts);
        }

        var chosenPeer = (IPEndPoint)chosen.RemoteEndPoint!;
        log?.Invoke($"TCP 打洞成功（{(wasInbound ? "对方连进来" : "拨通")}）{chosenPeer}，"
                    + $"用时 {elapsed.TotalMilliseconds:0} ms，尝试 {attempts} 次");
        return new Outcome(chosen, chosenPeer, elapsed, attempts);
    }

    /// <summary>读满一整帧。对端半路断掉就返回 false，由调用方收摊。</summary>
    private static async Task<bool> ReadFrameAsync(Socket sock, byte[] buffer, CancellationToken ct)
    {
        var read = 0;
        while (read < buffer.Length)
        {
            int got;
            try
            {
                got = await sock.ReceiveAsync(
                    buffer.AsMemory(read), SocketFlags.None, ct).ConfigureAwait(false);
            }
            catch { return false; }
            if (got <= 0) return false;
            read += got;
        }
        return true;
    }

    /// <summary>
    /// 丢掉从代理出去的 TCP 候选。
    ///
    /// 本机挂着 mihomo 这类代理时，去某些反射器的 TCP 连接会被它接管，反射器看到的
    /// 是**代理出口**的地址。这种候选拿去打洞必然落空——对端连过去是代理机房，那后面
    /// 没有我们。更要命的是它摊薄预算：每个候选都要展开几百个目标，四个候选里两个是
    /// 假的，真候选的窗口就只剩一半。
    ///
    /// 判据是和 UDP 探到的出口 IP 对不对得上：代理接管的是 TCP，UDP 那条看到的才是
    /// 真实公网出口。一个都对不上时不过滤——那说明 UDP 自己也没探到，没有可信参照，
    /// 宁可多打几个也别把唯一的路堵死。
    /// </summary>
    public static List<IPEndPoint> WithoutProxied(
        IReadOnlyList<IPEndPoint> tcp, IReadOnlyList<IPEndPoint> udpReference)
    {
        if (udpReference.Count == 0) return [.. tcp];
        var real = udpReference.Select(x => x.Address).ToHashSet();
        var kept = tcp.Where(x => real.Contains(x.Address)).ToList();
        return kept.Count > 0 ? kept : [.. tcp];
    }

    /// <summary>
    /// 把候选展开成实际要拨的目标，需要的话加上预测端口。
    ///
    /// <paramref name="step"/> 是端口步长，两侧必须取不同的值，见
    /// <see cref="P2PTags.PredictStep"/>。
    /// </summary>
    private static List<IPEndPoint> Expand(
        IReadOnlyList<IPEndPoint> candidates, bool predict, bool narrow, int step)
    {
        var list = new List<IPEndPoint>();
        foreach (var c in candidates) list.Add(c);
        if (!predict) return list;

        // 把拨号预算全部花在**深度**上，而不是平摊到每个候选。
        //
        // 实测对端报上来的候选是连号的（122.245.210.17:11310/11311/11312/11313）
        // ——它们本来就是同一个映射附近，互相高度冗余。原先"每个候选各往上预测
        // PredictAhead 个"把 256 次拨号摊成 4×64，覆盖深度只有 64（约 3 秒漂移），
        // 而实测间隔是 8 秒、漂移约 160 个，必然打空。
        //
        // 同样 256 次拨号，从最小的那个候选往上连续铺开，覆盖深度就是 256
        // （约 12 秒），一次都没多拨。只往上：对方先问反射器再朝我们发起，
        // 中间只会消耗掉更大的端口号。
        // 从**最大**的那个候选往上，不是最小的。
        //
        // 候选是同一次探测里从几台反射器拿到的，顺序分配的 NAT 上它们本身就是递增的
        // （实测 11310/11311/11312/11313）。从最小的那个起步，头几个预测端口正好落在
        // 已经在列表里的那几个上，白占名额；而真实端口一定在**所有**观测值之上——
        // 对端是先问完反射器才朝我们发起的，中间只会再消耗掉更大的端口号。
        var byAddress = list
            .GroupBy(x => x.Address, x => x.Port)
            .Select(g => (Address: g.Key, Highest: g.Max()))
            .ToList();
        // 候选是开打前几百毫秒刚对表拿到的话，漂移只有个位数，十几个端口就够——
        // 拨号量从几百降到几十，既更快命中，也不会去挤对端的 NAT 表。
        var budget = narrow
            ? NarrowAhead * Math.Max(1, list.Count)
            : Math.Max(0, MaxDials - list.Count);
        var depth = byAddress.Count > 0 ? budget / byAddress.Count : 0;
        foreach (var (address, highest) in byAddress)
        {
            for (var i = 1; i <= depth; i++)
            {
                var port = highest + i * step;
                if (port > 65535) break;
                var candidate = new IPEndPoint(address, port);
                if (!list.Contains(candidate)) list.Add(candidate);
            }
        }
        return list;
    }

    private static async Task<Socket?> DialAsync(int localPort, IPEndPoint target, CancellationToken ct)
    {
        var socket = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
        try
        {
            socket.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.ReuseAddress, true);
            socket.Bind(new IPEndPoint(IPAddress.Any, localPort));
            using var attempt = CancellationTokenSource.CreateLinkedTokenSource(ct);
            attempt.CancelAfter(AttemptTimeout);
            await socket.ConnectAsync(target, attempt.Token).ConfigureAwait(false);
            socket.NoDelay = true;
            return socket;
        }
        catch
        {
            socket.Dispose();
            return null;
        }
    }
}
