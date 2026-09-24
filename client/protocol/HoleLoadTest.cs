using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Text;

namespace BatterMC.Protocol;

/// <summary>
/// 打通之后、交给 QUIC 之前，先用裸 UDP 在这个洞里推一段定速数据。
///
/// 为什么非要有这一步：排查"P2P 打通了但带不动"的时候，能测的东西一个个都测干净了，
/// 唯一测不到的恰好是最关键的那个——
///
/// <list type="bullet">
/// <item>两条线路各自朝公网服务器灌定速 UDP：2 MB/s、20 秒、零丢包。</item>
/// <item>我们的 QUIC 在回环上：254 MB/s。</item>
/// <item>我们的 Mux/TCP 隧道过公网：10 MB/s。</item>
/// <item>本机 hairpin 打洞 + QUIC（同一台 CGNAT 路由器）：10.3 MB/s。</item>
/// <item>而跨到玩家那台的 P2P + QUIC：5 秒只过来 38 KB。</item>
/// </list>
///
/// （上面这条是加这段压测之前的状态。加完之后同一条路径实测 2.7~9.3 MB/s，见文末。）
///
/// 也就是说问题既不在代码、也不在 QUIC、也不在任何一侧的出方向，而在"对端入方向、
/// 在有量的时候"。这一段没有任何现成工具能单独量——它只存在于两个 NAT 之间那个
/// 刚打通的洞里。所以把量它的能力做进协议本身。
///
/// 一次压测跑四种包长。日志里"小包通、大包不通"的形状是 MTU 黑洞的签名，而"都过、
/// 只是少"是限速的签名；不分开测，这两件事在结果上长得一模一样。
///
/// 结论只进日志，不做门禁：它的用途是告诉我们该修哪里，而不是再添一条否决通路的理由。
///
/// ## 它查出来的结论，以及它顺带变成了功能
///
/// 拿到真实跨 NAT 路径上一跑，答案是**四种包长全部 300/300 零丢包、1.5 MB/s**，
/// 包括 1472（正好 1500 字节 IP 包）。也就是说限速和 MTU 都不成立，同一个洞上
/// 裸 UDP 满速而 QUIC 只有 8 KB/s。
///
/// 而加上这段压测之后，那条路径就通了——跨 NAT 实测 2.7~9.3 MB/s。做过三臂对照
/// （每臂交错跑，共 26 次）：
///
/// <list type="bullet">
/// <item>打完洞立刻交给 QUIC：6/8 成功。</item>
/// <item>只干等 1 秒、不灌流量：4/4 成功。</item>
/// <item>灌这四轮：14/14 成功。</item>
/// </list>
///
/// 两次失败只出现在"立刻交给 QUIC"那一臂。样本太小，撑不起"预热就是修复"这个因果
/// ——速率在每一臂里都在 1.7~9.3 之间跳，那主要是测量噪声（2 MB 在 8 MB/s 下只采样
/// 0.25 秒）。**保留这段的理由是它便宜（0.6 秒）而且预热过的 18 次里一次没失败**，
/// 不是因为机制已经证实。谁要去掉它，先把失败率重新量一遍。
/// </summary>
public static class HoleLoadTest
{
    /// <summary>
    /// 默认压测计划。
    ///
    /// 1200 字节对应 1228 的 UDP 报文、1256 的 IP 包——这是实测过能过两条线路的尺寸。
    /// 1400 往上就进了 PPPoE（1492）和各种隧道封装的雷区，正是要看它过不过。
    /// 每轮 300 包、1.5 MB/s 约 0.25 秒，两轮加收尾不到一秒，NAT 映射几十秒的寿命
    /// 完全等得起。
    /// </summary>
    public static readonly (int Size, int Count, int RateKb)[] DefaultPlans =
    [
        (1200, 300, 1536),
        (1400, 300, 1536),
        // 1452 是 QUIC 实现握手之后常用的报文长度，1472 是 1500 MTU 下不分片的上限。
        //
        // 加这两档是因为实测出现了一个只有这样才能定性的现象：同一个洞上 1200 和 1400
        // 都是 300/300 零丢包、1.5 MB/s，而紧接着的 QUIC 批量传输只有 8 KB/s。线路、
        // 限速、1400 以下的 MTU 全都被排除了，剩下的差别就是 msquic 握手后会把报文
        // 往上探。这两档要么证实"更大的包过不去"，要么把 MTU 这条也排除掉。
        (1452, 300, 1536),
        (1472, 300, 1536),
    ];

    /// <summary>
    /// 实际要跑的轮数，默认全跑。MUXI_HOLE_ROUNDS=N 取前 N 轮，0 就整段跳过。
    ///
    /// 加这个开关是为了做一次 A/B：把压测从 2 轮加到 4 轮的那一次改动之后，同一条
    /// 跨 NAT 路径上的 QUIC 批量传输从 8 KB/s 变成了 6.5~9.3 MB/s，而那次改动**只**
    /// 动了轮数。要么是巧合，要么是"先给新洞灌半秒满速双向流量，NAT 才把它提到
    /// 快速路径"——后者意味着这半秒是功能而不是诊断，得当成必需步骤留着。
    /// 不用开关对照，就只能在这两个解释之间猜。
    /// </summary>
    public static IReadOnlyList<(int Size, int Count, int RateKb)> Plans()
    {
        var raw = Environment.GetEnvironmentVariable("MUXI_HOLE_ROUNDS");
        if (!int.TryParse(raw, out var rounds) || rounds < 0) return DefaultPlans;
        return DefaultPlans.Take(Math.Min(rounds, DefaultPlans.Length)).ToArray();
    }

    /// <summary>一轮的观察窗口。发端推 0.25 秒，留到 1.2 秒是给往返和排队的余量。</summary>
    private static readonly TimeSpan RoundWindow = TimeSpan.FromMilliseconds(1200);

    /// <summary>请求没人应时的重发间隔。对端可能还在收摊上一轮。</summary>
    private static readonly TimeSpan RequestRetry = TimeSpan.FromMilliseconds(250);

    /// <summary>应答方这一整段的总上限。正常流程远用不到，纯粹兜底。</summary>
    public static readonly TimeSpan RespondWindow = TimeSpan.FromSeconds(5);

    /// <summary>
    /// 一轮里迟迟收不到数据时，在"按计划早该发完"之后再等多久就判这一轮颗粒无收。
    ///
    /// 原先没有这一条，收不到就干等满 <see cref="RoundWindow"/>（1.2 秒）才问下一轮；
    /// 而应答方推完一轮只等 250ms 就收摊去起 QUIC 了。实测蜂窝上 1400 字节那一轮整轮
    /// 丢光时，后面 1452、1472 和两轮二分全是在对着空气问——日志里那句"路径 MTU 落在
    /// 1200~1250 之间"就是这么来的假结论；而请求方在这几轮里白耗的 5 秒，全压在
    /// QUIC 握手前面。600ms 盖得住一次请求重发（250ms）加一个蜂窝往返。
    /// </summary>
    private static readonly TimeSpan SilentRoundGrace = TimeSpan.FromMilliseconds(600);

    /// <summary>收到过数据之后，这么久没有新包就算这一轮到头了（剩下的是真丢了）。</summary>
    private static readonly TimeSpan TailGrace = TimeSpan.FromMilliseconds(300);

    /// <summary>
    /// 等**第一个**请求的耐心。
    ///
    /// 只给 600ms，不能用 RespondWindow 那 4 秒：对端打洞一成功就立刻发第一个请求，
    /// 所以 600ms 已经极其宽裕；而等不到的情况（对端是旧版本、或者压测被关了）下，
    /// 我们每多等一毫秒都是 QUIC 监听晚起一毫秒——这个 socket 占着监听要用的端口。
    /// 用 4 秒的后果是对端那边 QUIC 连接直接连不上，把一个本来能用的洞白白浪费掉。
    /// </summary>
    private static readonly TimeSpan FirstRequestWait = TimeSpan.FromMilliseconds(600);

    public sealed record RoundResult(
        int Size, int Asked, int Received, TimeSpan Span, int FirstSeq, int LastSeq, int[] Bins)
    {
        public double KbPerSecond => Span > TimeSpan.Zero
            ? Received * (double)Size / 1024.0 / Span.TotalSeconds
            : 0;

        public double LossRatio => Asked > 0 ? 1.0 - (double)Received / Asked : 0;

        /// <summary>
        /// 一行摘要。刻意把逐 100ms 的到达直方图印出来：只看总数分不清"前两格来完就断"
        /// 和"一路稀稀拉拉"，而这两种对应完全不同的病。
        /// </summary>
        public string Describe()
        {
            var shape = string.Concat(Bins.Select(b => b switch
            {
                0 => "_",
                < 5 => ".",
                < 20 => "-",
                < 60 => "=",
                _ => "#",
            }));
            return $"{Size} 字节包 收到 {Received}/{Asked}（丢 {LossRatio * 100:0.#}%）"
                   + $"，{KbPerSecond:0.#} KB/s，序号 {FirstSeq}~{LastSeq}，到达形状 [{shape}]";
        }
    }

    /// <summary>
    /// 请求方：逐轮向对端要数据并计数。
    ///
    /// 对端不应答不算失败——可能是旧版本。那种情况这一轮的 Received 是 0，调用方照常
    /// 继续去建 QUIC。
    /// </summary>
    /// <summary>
    /// 跑一轮：向对端要 <paramref name="count"/> 个 <paramref name="size"/> 字节的包并计数。
    ///
    /// 抽成独立方法是为了让二分探测能复用同一段逻辑——两份各自演进的收包循环迟早
    /// 会在超时、计数或速率口径上走岔，而这几个数字正是判断病因的依据。
    /// </summary>
    private static async Task<RoundResult?> RunRoundAsync(
        Socket socket, IPEndPoint peer, ulong session, byte[] secret,
        int size, int count, int rateKb, Action<string>? log, CancellationToken ct)
    {
        var buffer = new byte[65535];
        var request = PunchProtocol.Build(
            PunchProtocol.Kind.LoadRequest, session,
            PunchProtocol.EncodeLoadPlan(size, count, rateKb), secret);

        var bins = new int[(int)(RoundWindow.TotalMilliseconds / 100)];
        var received = 0;
        var first = -1;
        var last = -1;
        var firstArrival = TimeSpan.Zero;
        var lastArrival = TimeSpan.Zero;
        var nextRequest = TimeSpan.Zero;
        var watch = Stopwatch.StartNew();
        // 按计划这一轮该推多久。定速推，所以能算出来。
        var planned = TimeSpan.FromSeconds(count * (double)size / (Math.Max(1, rateKb) * 1024.0));
        var giveUp = planned + SilentRoundGrace;

        while (watch.Elapsed < RoundWindow && !ct.IsCancellationRequested)
        {
            if (received == 0 && watch.Elapsed >= giveUp) break;
            if (received > 0 && watch.Elapsed - lastArrival >= TailGrace) break;
            // 还没见到数据就按间隔重发请求；见到了就闭嘴，别和数据抢路。
            if (received == 0 && watch.Elapsed >= nextRequest)
            {
                try
                {
                    await socket.SendToAsync(request, SocketFlags.None, peer, ct)
                        .ConfigureAwait(false);
                }
                catch (SocketException) { }
                nextRequest = watch.Elapsed + RequestRetry;
            }

            var left = RoundWindow - watch.Elapsed;
            if (left <= TimeSpan.Zero) break;
            var slice = received == 0 && left > RequestRetry ? RequestRetry : left;
            // 别睡过了上面那两条收手线。
            var toGiveUp = received == 0 ? giveUp - watch.Elapsed : lastArrival + TailGrace - watch.Elapsed;
            if (toGiveUp > TimeSpan.Zero && toGiveUp < slice) slice = toGiveUp;
            if (slice <= TimeSpan.Zero) continue;

            SocketReceiveFromResult got;
            try
            {
                using var timer = CancellationTokenSource.CreateLinkedTokenSource(ct);
                timer.CancelAfter(slice);
                got = await socket.ReceiveFromAsync(
                        buffer, SocketFlags.None, new IPEndPoint(IPAddress.Any, 0), timer.Token)
                    .ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (!ct.IsCancellationRequested) { continue; }
            catch (SocketException) { continue; }

            if (got.RemoteEndPoint is not IPEndPoint from || !from.Equals(peer)) continue;
            // 只认这一轮的包长。上一轮迟到的包（对端重复应答了一次重发的请求时尤其多）
            // 不能算进这一轮——否则 1200 的尾巴会把 1400 那一轮"凑满"，路径 MTU 的判断
            // 就全错了。压测包本来就按请求的长度填充，长度对不上的一定不是这一轮的。
            if (got.ReceivedBytes != size) continue;
            if (PunchProtocol.ParseLoad(buffer.AsSpan(0, got.ReceivedBytes), secret)
                is not { } load || load.Session != session) continue;

            received++;
            var seq = (int)load.Sequence;
            if (first < 0) { first = seq; firstArrival = watch.Elapsed; }
            last = seq;
            lastArrival = watch.Elapsed;
            var bin = (int)(watch.Elapsed.TotalMilliseconds / 100);
            if (bin >= 0 && bin < bins.Length) bins[bin]++;
            if (received >= count) break;
        }
        watch.Stop();

        // 速率按"数据实际在路上的那段"算，不能按整个窗口——窗口里含着等请求被应答
        // 的往返时间，拿它做分母会把速率算低一半还多。
        var span = lastArrival > firstArrival ? lastArrival - firstArrival : watch.Elapsed;
        var result = new RoundResult(size, count, received, span, first, last, bins);

        // 把结果告诉对端，好让服务端日志里也留一行——线上出问题时我们能拿到的
        // 往往只有服务端日志。
        try
        {
            await socket.SendToAsync(
                    PunchProtocol.Build(PunchProtocol.Kind.LoadReport, session,
                        PunchProtocol.EncodeLoadPlan(size, received, (int)result.KbPerSecond),
                        secret),
                    SocketFlags.None, peer, ct)
                .ConfigureAwait(false);
        }
        catch (SocketException) { }

        // 下面这句在循环里，放最后一轮之后会漏掉提前 break 的情形，所以统一在
        // 循环外发（见函数末尾的 LoadDone）。
        // 只有**第一轮**颗粒无收才提前收手——那说明对端不认识这个请求（旧版本），
        // 继续下去只是每轮白等一秒二，而这一秒二加在玩家点「开始游戏」到进服之间。
        //
        // 后面某一轮收到 0 绝不能当成理由退出：那恰恰是我们要的结论（"这个包长过不去"）。
        // 早先写成无条件 break，结果是 1452 一旦全丢就永远测不到 1472，而这两档的
        // 差别正是判断路径 MTU 落在哪里的依据。
        return result;
    }

    public static async Task<List<RoundResult>> RequestAsync(
        Socket socket, IPEndPoint peer, ulong session, byte[] secret,
        IReadOnlyList<(int Size, int Count, int RateKb)> plans,
        Action<string>? log, CancellationToken ct)
    {
        var results = new List<RoundResult>();
        var buffer = new byte[65535];
        foreach (var (size, count, rateKb) in plans)
        {
            var round = await RunRoundAsync(
                    socket, peer, session, secret, size, count, rateKb, log, ct)
                .ConfigureAwait(false);
            if (round is null) break;
            results.Add(round);
            log?.Invoke("洞压测 " + round.Describe());

            // 只有**第一轮**颗粒无收才提前收手——那说明对端不认识这个请求（旧版本），
            // 继续下去只是每轮白等一秒二，而这一秒二加在玩家点「开始游戏」到进服之间。
            //
            // 后面某一轮收到 0 绝不能当成理由退出：那恰恰是我们要的结论（"这个包长
            // 过不去"）。早先写成无条件 break，结果是 1452 一旦全丢就永远测不到 1472，
            // 而这两档的差别正是判断路径 MTU 落在哪里的依据。
            if (round.Received == 0 && results.Count == 1) break;
        }

        // 发现尺寸门槛就二分一下，把它夹准。
        //
        // 默认计划里 1200 和 1400 之间隔了 200 字节，只够说出"门槛在这两者之间"。
        // 而这一段只在**真的出现门槛**时才跑：健康路径上所有包长全通，一轮都不加。
        // 病态路径本来就要退回中转，多花半秒换一个准确的数字很值——排查的人拿到
        // "过不去的是 1340 而不是 1400"才能对上具体是哪一跳压的 MTU。
        for (var probe = 0; probe < MaxBisect; probe++)
        {
            var ok = results.Where(r => r.Received > 0).Select(r => r.Size).ToList();
            var dead = results.Where(r => r.Received == 0).Select(r => r.Size).ToList();
            if (ok.Count == 0 || dead.Count == 0) break;
            var low = ok.Max();
            var high = dead.Min();
            if (high <= low || high - low <= BisectFloor) break;

            var mid = low + (high - low) / 2;
            var round = await RunRoundAsync(
                    socket, peer, session, secret, mid, BisectCount, BisectRateKb, log, ct)
                .ConfigureAwait(false);
            if (round is null) break;
            results.Add(round);
            log?.Invoke($"洞压测（二分 {low}~{high}）" + round.Describe());
        }

        // 明确告诉对端"问完了"，别让它靠超时去猜。
        //
        // 这一个包省掉对端几百毫秒的等待，而那几百毫秒正好夹在它放开端口和我们发起
        // QUIC 之间——省下来的不是这一个包的时间，是我们第一个 Initial 不会被丢、
        // 不用等 msquic 约一秒的重传。丢了也无所谓：对端还有超时兜底。
        //
        // 连发三次：丢一个 LoadDone 的代价不小——对端要等满宽限（1.3 秒）才放开端口，
        // 我们的 QUIC Initial 和它的第一次重传都会落在那之前被吞掉。
        var done = PunchProtocol.Build(PunchProtocol.Kind.LoadDone, session, 0, secret);
        for (var i = 0; i < 3; i++)
        {
            try { await socket.SendToAsync(done, SocketFlags.None, peer, ct).ConfigureAwait(false); }
            catch (SocketException) { }
        }
        return results;
    }

    /// <summary>二分最多追加几轮。两轮就能把 200 字节的区间夹到 50 以内。</summary>
    private const int MaxBisect = 2;

    /// <summary>区间小到这个程度就不用再夹了，再夹也指不到具体某一跳。</summary>
    private const int BisectFloor = 64;

    /// <summary>二分那几轮用更少的包：只要判"过不过"，不需要测速率。</summary>
    private const int BisectCount = 80;

    private const int BisectRateKb = 1024;

    /// <summary>
    /// 应答方：对端每要一轮就推一轮，对端不要了就立刻放开端口。
    ///
    /// 必须定速推。猛发的话第一个瓶颈是本机上行，测出来的是"我们自己发太快"而不是
    /// "这个洞带不动"——两者的结论正好相反。
    ///
    /// 退出条件是"对端不再问"，**不是**"我这边配了几轮"。这个区别是实打实的：按自己的
    /// 轮数计数时，客户端版本比我们旧、只要两轮，我们就会傻等到整个窗口耗尽，而这个
    /// socket 占着 QUIC 要用的端口——监听晚起几秒，P2P 直接失败。
    /// </summary>
    /// <param name="peer">打洞时认定的对端。只是初值：见下面关于来源地址的说明。</param>
    /// <param name="firstRequest">
    /// 打洞收包循环已经替我们收下的第一个请求（见 <see cref="UdpPuncher.Outcome.PeerLoadRequest"/>）。
    /// 有它就不必等对端 250ms 后的重发。
    /// </param>
    /// <param name="peerFinished">打洞时就已经收到对端的 LoadDone：它不会再问了，直接返回。</param>
    public static async Task RespondAsync(
        Socket socket, IPEndPoint peer, ulong session, byte[] secret,
        Action<string>? log, CancellationToken ct, ulong? firstRequest = null,
        bool peerFinished = false)
    {
        if (peerFinished && firstRequest is null) return;
        var overall = Stopwatch.StartNew();
        var served = 0;
        // 第一个请求等 600ms，之后每轮只等一小会儿——理由见两个常量各自的注释。
        var patience = FirstRequestWait;
        var pending = firstRequest is { } nonce ? PunchProtocol.DecodeLoadPlan(nonce) : ((int, int, int)?)null;
        (int Size, int Count, int RateKb)? lastServed = null;

        while (served < MaxRoundsServed && !ct.IsCancellationRequested)
        {
            (int Size, int Count, int RateKb)? plan;
            if (pending is { } first && IsSanePlan(first))
            {
                plan = first;
                pending = null;
            }
            else
            {
                var next = await NextRequestAsync(
                        socket, peer, session, secret, patience, overall, log, ct)
                    .ConfigureAwait(false);
                if (next is null) return;
                plan = next.Value.Plan;
                // 同一个计划紧接着又来一次，是对端在我们推数据期间按 250ms 重发的请求，
                // 不是新的一轮。照推的话一轮会被推好几遍：白白拖后后面几轮（请求方会把
                // 迟到的包当成下一轮的），还会把 MaxRoundsServed 用光，让最后几轮和二分
                // 没人应答。请求方从不连着问同一个计划（二分的包数和默认轮不同）。
                if (lastServed is { } same && same == plan.Value) continue;
                if (!next.Value.From.Equals(peer))
                {
                    log?.Invoke($"洞压测：请求来自 {next.Value.From}（打洞时记的是 {peer}），按实际来源回");
                    peer = next.Value.From;
                }
            }

            var (packetSize, wanted, rateKb) = plan.Value;
            lastServed = plan.Value;
            served++;
            await SendPlanAsync(socket, peer, session, secret, packetSize, wanted, rateKb, ct)
                .ConfigureAwait(false);
            log?.Invoke($"洞压测：已按 {rateKb} KB/s 推出 {wanted} 个 {packetSize} 字节包");
            patience = NextRoundGrace;
        }
    }

    private static bool IsSanePlan((int Size, int Count, int RateKb) plan) =>
        plan.Size >= PunchProtocol.PacketSize && plan.Count is > 0 and <= 4000;

    /// <summary>
    /// 等下一个压测请求。等到就返回它的参数和实际来源，等不到就返回 null（该收摊了）。
    ///
    /// 顺路收到的回报就记一行——线上出问题时我们能拿到的往往只有服务端日志。
    ///
    /// **不按来源地址过滤**，只看会话号和 MAC。原先要求来源必须等于打洞时认定的对端，
    /// 而对端开了诱饵端口时两边各自认定的端口对经常不是同一对：实测蜂窝 6 次里 5 次，
    /// 服务端在 :6535 上等，客户端从 :6376 来问，请求全被当成外人丢掉，预热 0/300。
    /// MAC 已经挡住了外人，来源地址这一层过滤只剩下误杀。
    /// </summary>
    private static async Task<((int Size, int Count, int RateKb) Plan, IPEndPoint From)?> NextRequestAsync(
        Socket socket, IPEndPoint peer, ulong session, byte[] secret,
        TimeSpan patience, Stopwatch overall, Action<string>? log, CancellationToken ct)
    {
        var buffer = new byte[65535];
        var waited = Stopwatch.StartNew();
        while (waited.Elapsed < patience && overall.Elapsed < RespondWindow
               && !ct.IsCancellationRequested)
        {
            var slice = patience - waited.Elapsed;
            var left = RespondWindow - overall.Elapsed;
            if (left < slice) slice = left;
            if (slice <= TimeSpan.Zero) return null;

            SocketReceiveFromResult got;
            try
            {
                using var timer = CancellationTokenSource.CreateLinkedTokenSource(ct);
                timer.CancelAfter(slice);
                got = await socket.ReceiveFromAsync(
                        buffer, SocketFlags.None, new IPEndPoint(IPAddress.Any, 0), timer.Token)
                    .ConfigureAwait(false);
            }
            catch (OperationCanceledException) { return null; }
            catch (SocketException) { continue; }

            if (got.RemoteEndPoint is not IPEndPoint from) continue;
            // 对端已经开始发 QUIC 了（长首部、至少 1200 字节的 Initial）：说明它问完了、
            // 只是 LoadDone 丢了。立刻放开端口，这个 Initial 被我们吃掉，但它的重传能赶上。
            if (got.ReceivedBytes >= 1200 && (buffer[0] & 0xC0) == 0xC0) return null;
            if (PunchProtocol.Parse(buffer.AsSpan(0, got.ReceivedBytes), secret)
                is not { } packet || packet.Session != session) continue;

            if (packet.Type == PunchProtocol.Kind.LoadReport)
            {
                var (size, count, rate) = PunchProtocol.DecodeLoadPlan(packet.Nonce);
                log?.Invoke($"洞压测回报：{size} 字节包对端收到 {count} 个，约 {rate} KB/s");
                continue;
            }
            // 对端说问完了，立刻收摊——这个端口马上要给 QUIC 监听用，多占一毫秒
            // 都是对端第一个 Initial 被丢掉的风险。
            if (packet.Type == PunchProtocol.Kind.LoadDone) return null;
            if (packet.Type != PunchProtocol.Kind.LoadRequest) continue;

            var plan = PunchProtocol.DecodeLoadPlan(packet.Nonce);
            if (!IsSanePlan(plan)) continue;
            return (plan, from);
        }
        return null;
    }

    /// <summary>一次打洞最多应几轮压测。纯粹是防滥用的上限，不是"预期轮数"。</summary>
    private const int MaxRoundsServed = 8;

    /// <summary>
    /// 推完一轮后等下一个请求的宽限。
    ///
    /// 原先是 250ms，理由是"对端连着问的间隔只有几毫秒"——那只在这一轮对端收到了数据时
    /// 成立。整轮丢光时对端要等到自己的收手线才会问下一轮（新版约 0.9 秒，1.1.31 及以前
    /// 是满 1.2 秒），250ms 早收摊了，后面几轮全没人应。正常结束靠对端显式发的
    /// LoadDone，这个宽限只在 LoadDone 丢了时才会被等满，所以放宽它不拖慢正常流程。
    /// </summary>
    private static readonly TimeSpan NextRoundGrace = TimeSpan.FromMilliseconds(1300);

    private static async Task SendPlanAsync(
        Socket socket, IPEndPoint peer, ulong session, byte[] secret,
        int packetSize, int count, int rateKb, CancellationToken ct)
    {
        // 20ms 一片：粗到不被 Windows 那 15ms 的定时器精度主导，细到不会甩出一个会被
        // 当成洪泛的突发。这个切法在本机实测能把速率控到目标值 1% 以内。
        var perSecond = Math.Max(1, rateKb * 1024 / packetSize);
        var perSlice = Math.Max(1, perSecond / 50);
        var watch = Stopwatch.StartNew();
        var slice = 0;
        for (uint seq = 0; seq < count && !ct.IsCancellationRequested;)
        {
            for (var i = 0; i < perSlice && seq < count; i++, seq++)
            {
                var packet = PunchProtocol.BuildLoad(session, seq, secret, packetSize);
                try
                {
                    await socket.SendToAsync(packet, SocketFlags.None, peer, ct)
                        .ConfigureAwait(false);
                }
                catch (SocketException) { return; }
            }
            slice++;
            var due = TimeSpan.FromMilliseconds(slice * 20.0);
            var lag = due - watch.Elapsed;
            if (lag > TimeSpan.FromMilliseconds(1))
                await Task.Delay(lag, ct).ConfigureAwait(false);
        }
    }

    /// <summary>
    /// 从各包长的结果里读出路径能过的最大报文，以及该怀疑什么。
    ///
    /// 为什么值得单独做：压测的原始数据人能看懂，但玩家日志里只剩一句"带不动"，
    /// 排查的人拿不到方向。而这几个数字能直接指出病因——
    ///
    /// 小包过、大包 100% 不过 = 路径 MTU 黑洞。这不是限速：限速会让两种包长都变慢，
    /// 而黑洞是按尺寸一刀切。实测撞到过一次：1200 字节（IP 1228）过、1400（IP 1428）
    /// 及以上全灭，而同一台机器几小时前四种包长全部零丢包——也就是路径变了。
    /// 那台机器上挂着一块 MTU 1300 的隧道网卡，1228 &lt; 1300 &lt; 1428，分界线正好对上。
    ///
    /// **只出结论，不做否决。** QUIC 在这种路径上能不能凑合活着，由带宽验收去判；
    /// 这里多加一道门禁只会多一条误杀好路径的可能。
    /// </summary>
    public static string? DiagnoseMtu(IReadOnlyList<RoundResult> rounds)
    {
        if (rounds.Count < 2) return null;
        var ok = rounds.Where(r => r.Received > 0).ToList();
        var dead = rounds.Where(r => r.Received == 0).ToList();
        // 一个都没收到 = 对端没应答（旧版本）或者洞本身不通，不是 MTU 的事。
        if (ok.Count == 0 || dead.Count == 0) return null;

        var largestOk = ok.Max(r => r.Size);
        var smallestDead = dead.Min(r => r.Size);
        if (smallestDead <= largestOk) return null;   // 交错，说明是丢包不是尺寸门槛

        // 加上 UDP 头 8 和 IPv4 头 20 才是链路上的包长。
        return $"路径 MTU 受限：{largestOk} 字节的包能过（链路上 {largestOk + 28} 字节），"
               + $"{smallestDead} 字节（{smallestDead + 28}）一个都过不去。"
               + "所以路径 MTU 落在这两者之间。QUIC 握手之后会把报文往上探，"
               + "探到黑洞里就会表现成「打通了却带不动」。"
               + "常见原因是路上有 VPN / 加速器 / 花生壳之类的隧道网卡把 MTU 压低了，"
               + "关掉它再试一次就能确认。";
    }

    /// <summary>把多轮结果压成一行，给上层直接记日志。</summary>
    public static string Summarize(IReadOnlyList<RoundResult> rounds)
    {
        if (rounds.Count == 0) return "洞压测：对端没有应答（旧版本或已关闭）";
        var text = new StringBuilder("洞压测：");
        text.AppendJoin("；", rounds.Select(r => r.Describe()));
        return text.ToString();
    }
}
