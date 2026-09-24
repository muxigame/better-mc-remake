using System.Net;
using System.Net.Quic;
using System.Net.Sockets;
using System.Runtime.Versioning;
using System.Text;
using System.Text.Json;
using BatterMC.Protocol;

namespace BatterMC.Core;

/// <summary>
/// 打洞得来的隧道。
///
/// 存在的理由只有一个：省服务器带宽。中转线路本身又快又稳，但玩家的游戏流量全
/// 从我们自己的服务器过，那笔带宽是实打实的成本。这条路让数据在两端之间直连，
/// 服务器只承担几十个字节的信令。
///
/// 流程：
///   1. 用一个 UDP socket 向反射器的多个端口各问几次，把**全部**出口候选收齐
///      （这条家宽在运营商 NAT 后面且有多个出口，只报一个的话对端约一半概率打空）
///   2. 把候选交给控制面，换回对端候选和一个约定打洞时刻
///   3. 到点两边同时朝对方所有候选猛发，任意一对先通就用哪一对
///   4. 关掉裸 socket，让 QUIC 绑同一个本地端口——NAT 映射按本地端口记账，
///      所以握手直接穿过刚打好的洞
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class P2PTunnel : IMuxiTunnel
{
    /// <summary>反射器端口。多问几个才能发现自己的多个出口身份。</summary>
    private static readonly int[] ReflectorPorts = [45701, 45702, 45703];
    private const int DiscoveryRounds = 6;

    private readonly QuicConnection _connection;
    private readonly IPEndPoint _peer;

    /// <summary>
    /// 连接已经出过什么事。非空即已死。
    ///
    /// 为什么不能直接看 <c>_connection.RemoteEndPoint</c>：那是建连时记下来的一个
    /// 属性，**对端显式关闭并 Dispose 之后它依然非空**（实测验证过）。于是
    /// <see cref="IsAlive"/> 对一条已经死掉的 QUIC 连接恒返回 true，上层"隧道自己
    /// 报死就立刻换路"那条快路径对 P2P 形同虚设，判死只剩保温那条慢路——保温间隔
    /// 25 秒、要连续两次，最快 50 秒才换得掉，而这 50 秒正落在玩家读条期间。
    ///
    /// 改成和 <see cref="MuxConnection"/> 同构：记录第一次故障，之后一律报死。
    /// </summary>
    private volatile Exception? _fault;

    private P2PTunnel(QuicConnection connection, IPEndPoint peer)
    {
        _connection = connection;
        _peer = peer;
    }

    public bool IsAlive => _fault is null && _connection.RemoteEndPoint is not null;
    public string Describe => $"P2P 直连 {_peer}";

    public async Task<Stream> OpenStreamAsync(CancellationToken ct)
    {
        try
        {
            return await _connection.OpenOutboundStreamAsync(QuicStreamType.Bidirectional, ct)
                .ConfigureAwait(false);
        }
        catch (Exception error) when (error is QuicException or ObjectDisposedException)
        {
            // 开流失败是这条连接最早、也最可靠的死讯。记下来，让 IsAlive 立刻翻假，
            // 后面每条新的 Minecraft 连接就不用再各自白撞一次。
            _fault = error;
            throw;
        }
    }

    /// <summary>
    /// 走完整套流程。任何一步失败都抛，调用方据此回退到隧道或中转——打洞本来就
    /// 是尽力而为，打不通不该让玩家进不去游戏。
    /// </summary>
    /// <summary>TCP 反射器端口。和 UDP 那组分开——NAT 给两种协议的是两套映射。</summary>
    private static readonly int[] TcpReflectorPorts = [45711, 45712];

    /// <summary>
    /// 同一次信令、同一个约定时刻，UDP 和 TCP 一起打，谁先通谁上。
    ///
    /// 为什么并行而不是"UDP 不行再试 TCP"：信令（候选交换 + 约定时刻）是共用的，
    /// 并行不多花一次往返；顺序尝试的话，UDP 被运营商限速的玩家要白等一整轮才
    /// 轮到 TCP。两条路各有各的死法——UDP 可能被限速，TCP 可能被 NAT 的顺序
    /// 分配打偏——事先谁也说不准，同时开打最省时间。
    /// </summary>
    /// <param name="validate">
    /// 对打通的隧道做验收。抛异常表示这条不合格，会被收掉并换下一条。
    ///
    /// 必须在这里做、不能等 EstablishAsync 返回之后：UDP 和 TCP 是并行打的，
    /// 先通的那条未必好用——实测有线路 UDP 打得通、QUIC 握得上手，但持续大流量
    /// 被运营商掐到 3 KB/s。验收放在外面的话，这种情况会连另一条已经打通的
    /// TCP 一起丢掉，白白退回中转。
    /// </param>
    public static async Task<IMuxiTunnel> EstablishAsync(
        string controlPlaneBaseUrl, string serverId, string token, string reflectorHost,
        IProgress<string>? progress, CancellationToken ct,
        Func<IMuxiTunnel, CancellationToken, Task>? validate = null)
    {
        Log.Info(QuicTunnel.DescribeSupport());
        var quicOk = QuicTunnel.IsSupported;

        // 整条建立流程的时间线。玩家唯一感知得到的就是总时长，而它是十几步叠出来的，
        // 其中好几步是我们自己写死的等待——不分段量，就不知道该优化哪一步。
        var timing = new PhaseTimer();
        var udp = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
        var tcpEnabled = P2PTags.TcpPunchEnabled;
        var tcpPort = tcpEnabled ? ReserveTcpPort() : 0;
        try
        {
            udp.Bind(new IPEndPoint(IPAddress.Any, 0));
            var udpPort = ((IPEndPoint)udp.LocalEndPoint!).Port;

            progress?.Report("正在探测本机公网出口");
            // UDP 和 TCP 的出口探测互不相干（两套映射、两个 socket），一起探。
            // 原先是先 UDP 后 TCP 串着来，TCP 那 0.4~1.4 秒白白叠在建立时延上。
            var tcpProbe = tcpEnabled
                ? DiscoverTcpAsync(reflectorHost, tcpPort, ct)
                : Task.FromResult(new List<IPEndPoint>());
            var discovery = quicOk
                ? await NatDiscovery
                    .DiscoverAsync(udp, reflectorHost, ReflectorPorts, DiscoveryRounds, ct)
                    .ConfigureAwait(false)
                : new NatDiscovery.Result([], 0, 0, false);

            if (discovery.Candidates.Count > 0)
            {
                Log.Info("本机 NAT 类型（UDP）：" + (discovery.AddressDependent
                    ? "地址相关型——对端需要在邻近端口扫描才能命中"
                    : "端点无关型——对端照着这个地址直接打就行"));
                Log.Info($"本机 UDP 出口候选 {discovery.Candidates.Count} 个：" +
                         string.Join("，", discovery.Candidates));
            }

            timing.Mark("探UDP出口");
            var tcpMine = TcpPuncher.WithoutProxied(
                await tcpProbe.ConfigureAwait(false), discovery.Candidates);
            if (tcpEnabled) timing.Mark("探TCP出口");
            // 同一个本地端口问不同反射器拿到不同端口 = 地址相关型。
            //
            // 这个判断以前只用来打一行日志，现在要**告诉对端**：谁去连、谁来收、
            // 到底要不要预测端口，全由两侧类型的组合决定，见 P2PTags。
            var tcpDependent = tcpMine.Select(x => x.ToString()).Distinct().Count() > 1;
            if (tcpMine.Count > 0)
                Log.Info($"本机 TCP 出口候选 {tcpMine.Count} 个：{string.Join("，", tcpMine)}"
                         + (tcpDependent ? "（地址相关，对端需预测端口）" : "（端点无关）"));

            if (discovery.Candidates.Count == 0 && tcpMine.Count == 0)
                throw new InvalidOperationException("UDP 和 TCP 都探测不到自己的公网出口");

            progress?.Report($"与服务器交换连接信息（UDP {discovery.Candidates.Count} / TCP {tcpMine.Count}）");
            var rendezvous = await ExchangeAsync(
                controlPlaneBaseUrl, serverId, discovery.Candidates, tcpMine,
                tcpDependent, ct).ConfigureAwait(false);
            timing.Mark("交换信令");
            if (rendezvous.PeerCandidates.Count == 0
                && (!tcpEnabled || rendezvous.PeerTcpCandidates.Count == 0))
                throw new InvalidOperationException("服务器侧没有可用的打洞候选");

            var secret = PunchProtocol.DeriveSecret(token);
            progress?.Report($"正在打洞（对端 UDP {rendezvous.PeerCandidates.Count} / "
                             + $"TCP {rendezvous.PeerTcpCandidates.Count}）");

            var races = new List<Task<IMuxiTunnel?>>();
            if (quicOk && rendezvous.PeerCandidates.Count > 0 && !UdpPunchDisabled)
                races.Add(ViaUdpAsync(
                    udp, udpPort, rendezvous, secret, discovery.AddressDependent, timing, ct));
            if (tcpEnabled && rendezvous.PeerTcpCandidates.Count > 0)
                races.Add(ViaTcpAsync(
                    tcpDependent,
                    tcpPort, rendezvous, secret, controlPlaneBaseUrl, reflectorHost, ct));

            IMuxiTunnel? won = null;
            var failures = new List<string>();
            var pending = new List<Task<IMuxiTunnel?>>(races);
            while (pending.Count > 0 && won is null)
            {
                var done = await Task.WhenAny(pending).ConfigureAwait(false);
                pending.Remove(done);

                IMuxiTunnel? candidate = null;
                // 判据必须是"是我们自己要退出"，不能是"异常类型是取消"。
                //
                // 这两者不一样，而混起来会出真事故：验收里的 MC 状态查询有 8 秒超时，
                // 超时抛的是 OperationCanceledException——用 `is not
                // OperationCanceledException` 做过滤器就接不住它，于是**一条候选的
                // 超时会把整轮打洞连同另一条腿一起掐掉**，日志上只留一句
                // "The operation was canceled."。实测撞到过：QUIC 明明已经建好，
                // P2P 却整体作废、直接退到隧道。
                try { candidate = await done.ConfigureAwait(false); }
                catch (Exception error) when (!Cancellation.IsShutdown(error, ct))
                {
                    failures.Add(error.Message);
                }
                if (candidate is null) continue;
                if (validate is null) { won = candidate; break; }

                try
                {
                    await validate(candidate, ct).ConfigureAwait(false);
                    timing.Mark("验收");
                    won = candidate;
                }
                // 验收途中我们自己要退出（代理关了、游戏退了）：这条隧道没人要了，收掉再走，
                // 否则它的 QUIC 连接带着 5 秒一次的保活一直挂到启动器退出。
                catch (Exception error) when (Cancellation.IsShutdown(error, ct))
                {
                    DisposeInBackground(candidate);
                    foreach (var loser in pending)
                        _ = loser.ContinueWith(
                            t => { if (t.IsCompletedSuccessfully && t.Result is { } late) DisposeInBackground(late); },
                            TaskScheduler.Default);
                    throw;
                }
                catch (Exception error) when (!Cancellation.IsShutdown(error, ct))
                {
                    timing.Mark("验收失败");
                    Log.Warn($"{candidate.Describe} 打通了但没通过验收：{error.Message}");
                    failures.Add($"{candidate.Describe} 未通过验收：{error.Message}");
                    // 收一条被否决的隧道**不能让玩家等着**。
                    //
                    // 这里原来是 await DisposeAsync()，而那个方法要做 QUIC 的
                    // CloseAsync + DisposeAsync；链路已经是黑洞时这一步能耗掉几十秒。
                    // 实测过一次：验收 3 秒就放弃了，之后到"放弃"这一格却还有 33 秒，
                    // 玩家从点开始游戏到退回中转白等 45 秒。
                    //
                    // 这条连接已经判死，它怎么收摊跟玩家无关，扔后台去。
                    DisposeInBackground(candidate);
                }
            }

            // 已经有赢家，剩下那条就算随后也通了也用不上，收掉别漏 socket
            foreach (var loser in pending)
                _ = loser.ContinueWith(
                    t => { if (t.IsCompletedSuccessfully) _ = t.Result?.DisposeAsync(); },
                    TaskScheduler.Default);

            timing.Mark("等另一条腿");
            if (won is null)
            {
                Log.Info(timing.Describe("打洞时间线 timeline(fail)："));
                throw new InvalidOperationException(
                    "打洞未成功" + (failures.Count > 0 ? "：" + string.Join("；", failures) : ""));
            }
            Log.Info(timing.Describe("打洞时间线 timeline(ok)："));
            return won;
        }
        catch
        {
            try { udp.Close(); } catch { }
            throw;
        }
    }

    private static async Task<IMuxiTunnel?> ViaUdpAsync(
        Socket udp, int localPort, Rendezvous rendezvous, byte[] secret,
        bool addressDependent, PhaseTimer timing, CancellationToken ct)
    {
        // 不再干等约定时刻：拿到会合信息就开打，理由见 UdpPuncher.PunchAsync 开头。
        // 于是"UDP打洞"这一格现在包含了等 agent 取件的时间——它原先藏在固定 4 秒的
        // "等约定时刻"里，看不出来到底要多久。
        var outcome = await UdpPuncher.PunchAsync(
            udp, rendezvous.PeerCandidates, rendezvous.Session, secret,
            rendezvous.PunchAt, TimeSpan.FromSeconds(10),
            sweepPorts: !addressDependent,
            message => Log.Info("UDP 打洞：" + message), ct,
            alternates: rendezvous.PeerAlternates).ConfigureAwait(false);
        timing.Mark("UDP打洞");
        if (!outcome.Success)
            throw new InvalidOperationException(
                $"UDP 打洞未成功（发出 {outcome.Sent} 个包，收到对方 {outcome.PunchesHeard} 个）");

        var peer = outcome.Peer!;

        // 打通的那条路属于**具体某一个本地端口**。本机映射不可预测时打洞会多开几个
        // 诱饵端口一起打（生日攻击，见 UdpPuncher.DecoySockets），赢的可能不是主
        // socket——NAT 映射按本地端口记账，后面的预热和 QUIC 都必须用赢的那一个，
        // 拿错了等于把刚打好的洞扔掉。
        var live = outcome.Winner ?? udp;
        var livePort = ((IPEndPoint)live.LocalEndPoint!).Port;
        if (livePort != localPort)
            Log.Info($"P2P 打通的是额外端口 {livePort}（主端口 {localPort} 没中），后续都用它");

        // 交给 QUIC 之前先用裸 UDP 量一下这个洞本身带不带得动，见 HoleLoadTest。
        //
        // 放在这里是唯一可行的位置：socket 一关映射就是 QUIC 的了，那之后测出来的
        // 任何数字都分不清是洞的问题还是 QUIC 的问题——而这恰好是我们卡了最久的
        // 那个岔路口。整段不到一秒，而且一个包都收不到时会提前收手。
        try
        {
            var load = await HoleLoadTest.RequestAsync(
                live, peer, rendezvous.Session, secret, HoleLoadTest.Plans(),
                message => Log.Info("P2P " + message), ct).ConfigureAwait(false);
            Log.Info("P2P " + HoleLoadTest.Summarize(load));
            // 压测能直接看出"路径 MTU 黑洞"这种病，而它在上层只会表现成一句
            // "带不动"。结论在这里就写出来，排查的人不用自己去读那几行直方图。
            if (HoleLoadTest.DiagnoseMtu(load) is { } mtu) Log.Warn("P2P " + mtu);
            timing.Mark("洞预热");

            // 纯等待的对照臂：MUXI_HOLE_IDLE=<毫秒> 时不灌流量、只干等这么久。
            //
            // 要分清"预热靠的是流量"还是"只要让映射多活一会儿就行"。4 轮压测比 0 轮
            // 晚约 1 秒交给 QUIC，而那 1 秒里既有流量也有时间——不把两者拆开，就只是
            // 换了个说法在猜。
            var idle = Environment.GetEnvironmentVariable("MUXI_HOLE_IDLE");
            if (int.TryParse(idle, out var idleMs) && idleMs > 0)
            {
                Log.Info($"P2P 洞预热对照：不灌流量，只等 {idleMs} ms");
                await Task.Delay(idleMs, ct).ConfigureAwait(false);
            }
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            // 压测只是诊断。它出任何问题都不该把一个已经打通的洞连带废掉。
            Log.Warn("洞压测异常，跳过：" + error.Message);
        }

        // 交接必须快：运营商 NAT 的 UDP 映射老化只有几十秒，socket 一关就得立刻
        // 让 QUIC 顶上，中间拖久了洞就塌了。
        try { live.Close(); } catch { }
        if (!ReferenceEquals(live, udp)) { try { udp.Close(); } catch { } }
        // 别在这里加"短超时 + 重试"。试过，是负优化。
        //
        // 动机是真的：我们做完洞预热就马上连，而对端要先收到收工信号、关掉裸 socket、
        // 再在同一个端口起监听，必然比我们晚一点；我们的第一个 Initial 被丢，就得等
        // msquic 自己约一秒的 PTO。但实测「350ms 超时后重连一次」把这一格从 1080ms
        // 拖到了 3477ms——取消一次 QUIC 连接、再用同一个本地端口重绑的代价，比等
        // msquic 重传更高，而且重试自己还要从头走一遍 PTO 时间表。
        //
        // 而这件事本来也不值得优化：对端热着的时候这一格只有 66~85ms，只有它刚重启
        // 那一次会到 1080ms（首次 QuicListener 的进程级初始化，启动时预热也只能消掉
        // 一部分）。长驻的 agent 上就是第一个玩家偶尔多等一秒。
        // 交接那一瞬间要让一小步。
        //
        // 对端和我们做同一件事：关掉裸 socket、再在同一个端口上起 QUIC。它的监听在
        // 收到我们的收工信号后约 2ms 就绪（实测），但那 2ms 是个真实的缝——我们的第一个
        // Initial 正好落进去就被丢，然后要等 msquic 自己约一秒的 PTO。
        //
        // 实测这一格是双峰的：38ms 或者 1052ms，五次里两次落在慢的那一峰。期望代价
        // 约 0.4 × 1050 ≈ 420ms，而让一步只要这么几十毫秒。
        //
        // 注意别把这里改成"短超时 + 重试"，那条路试过、是负优化：取消一次 QUIC 连接
        // 再用同一个本地端口重绑的代价比等 msquic 重传还高，实测把这一格从 1080ms
        // 拖到 3477ms。
        try { await Task.Delay(QuicHandoffGap, ct).ConfigureAwait(false); }
        catch (OperationCanceledException) { throw; }
        var connection = await QuicTunnel.ConnectAsync(livePort, peer, ct).ConfigureAwait(false);
        timing.Mark("QUIC握手");
        Log.Info($"P2P(UDP/QUIC) 隧道已建立 {peer}，本地端口 {livePort}");
        return new P2PTunnel(connection, peer);
    }

    /// <summary>
    /// 端口交接的让步时间。取值理由见 <see cref="ViaUdpAsync"/> 里那段注释。
    ///
    /// 60ms 是"明显盖住对端那 2ms 的就绪窗口 + 一点网络抖动"，同时远小于赌输之后
    /// 要付的一个 PTO（约 1 秒）。往大调没有额外收益，往小调会重新开始丢 Initial。
    /// </summary>
    private static readonly TimeSpan QuicHandoffGap = TimeSpan.FromMilliseconds(60);

    /// <summary>
    /// 临时开关：设了环境变量 MUXI_NO_TCP_PUNCH=1 就不做 TCP 打洞。
    ///
    /// 为什么需要：TCP 打洞和 UDP 打洞挤在同一个窗口里并行跑，两边各几百个并发
    /// connect，抢的是同一张 NAT 表。实测过一轮 770 个并发之后，同时刻的 UDP 打洞
    /// 和到中转的隧道**一起**失败——表被打爆，整条线路瘫一分钟。现在跨 NAT 的 UDP
    /// 打洞双向都收不到包，而本机 hairpin 是通的，TCP 那边是头号嫌疑。
    /// 靠这个开关做对照实验，比改代码重发一版快得多。
    /// </summary>
    internal static bool TcpPunchDisabled =>
        Environment.GetEnvironmentVariable("MUXI_NO_TCP_PUNCH") == "1";

    /// <summary>
    /// 对称的开关：MUXI_NO_UDP_PUNCH=1 就不做 UDP 打洞。
    ///
    /// 为什么需要它：UDP 这条腿通常两秒就通，于是整个流程立刻收尾，TCP 那条腿连
    /// 开打前的对表都还没跑到就被取消了。换句话说，**只要 UDP 能通，TCP 路径上的
    /// 任何改动都无法被验证**。这正是那个"agent 侧从不对表"的 bug 能活到今天的原因
    /// 之一：本机 hairpin 上 UDP 永远先赢，没人见过 TCP 那条腿完整跑一遍。
    ///
    /// 只用于对照实验，正常启动两条腿都跑。
    /// </summary>
    internal static bool UdpPunchDisabled =>
        Environment.GetEnvironmentVariable("MUXI_NO_UDP_PUNCH") == "1";

    /// <summary>开打前多久做最后一次 TCP 对表。要够短，又够完成"探反射器 + 一个 HTTPS 往返"。</summary>
    private static readonly TimeSpan TcpRefreshBefore = TimeSpan.FromMilliseconds(900);

    private static async Task<IMuxiTunnel?> ViaTcpAsync(
        bool myTcpAddressDependent,
        int localPort, Rendezvous rendezvous, byte[] secret,
        string controlPlaneBaseUrl, string reflectorHost, CancellationToken ct)
    {
        if (TcpPunchDisabled)
            throw new InvalidOperationException("TCP 打洞已被 MUXI_NO_TCP_PUNCH 关闭");

        var punchAt = rendezvous.PunchAt + TcpPuncher.LeadTime;
        var peers = rendezvous.PeerTcpCandidates;
        var fresh = false;

        // 开打前最后一次对表。
        //
        // 这是 TCP 打洞能不能成的关键。这条线路上 TCP 外部端口顺序分配，实测每秒
        // 消耗约 20 个（线路上所有设备的连接都算），而控制面那次交换发生在开打前
        // 8 秒——漂移上百个端口，两边还得**同时**猜中对方，等于不可能。实测连着
        // 失败：255 个目标全打空。
        //
        // 所以在这里重新探一次自己的映射、和对端换一次最新值，把陈旧度从 8 秒压到
        // 几百毫秒，漂移降到个位数。窗口因此可以开得很窄，拨号量反而大幅下降。
        var wait = punchAt - TcpRefreshBefore - DateTimeOffset.UtcNow;
        if (wait > TimeSpan.Zero)
        {
            try { await Task.Delay(wait, ct).ConfigureAwait(false); }
            catch (OperationCanceledException) { throw; }
        }
        try
        {
            var mine = TcpPuncher.WithoutProxied(
                await DiscoverTcpAsync(reflectorHost, localPort, ct).ConfigureAwait(false), []);
            var swapped = await RefreshTcpAsync(
                controlPlaneBaseUrl, rendezvous.Session, "client", mine, ct).ConfigureAwait(false);
            if (swapped.Count > 0)
            {
                peers = swapped;
                fresh = true;
                Log.Info($"TCP 打洞：开打前对表成功，对端最新映射 {string.Join("，", swapped)}");
            }
            else
            {
                Log.Info("TCP 打洞：对端没赶上这次对表，只能用 8 秒前的旧候选（大概率打空）");
            }
        }
        catch (Exception error) when (error is not OperationCanceledException)
        {
            Log.Warn("TCP 打洞：对表失败，退回旧候选：" + error.Message);
        }

        // 要不要预测对端端口，由两侧映射类型的组合决定，不再两边都硬写 true。
        //
        // 原先两处都是 predictPorts: true，于是"打通"要求两边同时猜中对方的端口——
        // 搜索空间是端口**对** 2³²，而不是 2¹⁶。把拨号量从 64 加到 256 对这个量级
        // 毫无意义，这也是 TCP 打洞从来没成过、却一直查不出原因的地方。
        var predict = P2PTags.ShouldPredictPeerPorts(
            myTcpAddressDependent, rendezvous.PeerTcpEndpointIndependent, initiator: true);
        Log.Info($"TCP 打洞：本机{(myTcpAddressDependent ? "地址相关" : "端点无关")}、"
                 + $"对端{(rendezvous.PeerTcpEndpointIndependent ? "端点无关" : "地址相关")}"
                 + $" → {(predict ? "由我预测对端端口" : "照对端公布的端口直接连，不预测")}");

        var outcome = await TcpPuncher.PunchAsync(
            localPort, peers, rendezvous.Session, secret,
            punchAt,
            TimeSpan.FromSeconds(10) - TcpPuncher.LeadTime,
            predictPorts: predict, initiator: true, narrowWindow: fresh,
            message => Log.Info("TCP 打洞：" + message), ct).ConfigureAwait(false);
        if (!outcome.Success)
            throw new InvalidOperationException($"TCP 打洞未成功（尝试 {outcome.Attempts} 次）");

        var socket = outcome.Socket!;
        var mux = new MuxConnection(new NetworkStream(socket, ownsSocket: true), initiator: true);
        Log.Info($"P2P(TCP) 隧道已建立 {outcome.Peer}，本地端口 {localPort}");
        return new MuxTunnel(mux, outcome.Peer!, "TCP");
    }

    /// <summary>
    /// 把自己此刻的 TCP 映射报给控制面，同时取回对端此刻的。
    ///
    /// 两边都在开打前几百毫秒调它，谁先到谁先存，后到的那个立刻拿到对方的值；
    /// 先到的那个再调一次（不带候选）就能读回来。服务端只保留几秒，过期即弃——
    /// 陈旧的映射比没有更坏，会把拨号预算浪费在必然打空的端口上。
    /// </summary>
    private static async Task<List<IPEndPoint>> RefreshTcpAsync(
        string baseUrl, ulong session, string side,
        IReadOnlyList<IPEndPoint> mine, CancellationToken ct)
    {
        var url = $"{baseUrl.TrimEnd('/')}/api/v1/tunnel/punch/tcp-refresh"
                  + $"?session={session:x16}&side={side}";
        var body = "{\"candidates\":["
                   + string.Join(",", mine.Select(x => $"\"{x}\""))
                   + "]}";
        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(3) };
        using var content = new StringContent(body, Encoding.UTF8, "application/json");

        for (var attempt = 0; attempt < 3; attempt++)
        {
            using var reply = await http.PostAsync(url,
                attempt == 0 ? content : new StringContent("{}", Encoding.UTF8, "application/json"),
                ct).ConfigureAwait(false);
            if (!reply.IsSuccessStatusCode) return [];
            using var document = JsonDocument.Parse(
                await reply.Content.ReadAsStringAsync(ct).ConfigureAwait(false));
            var list = new List<IPEndPoint>();
            if (document.RootElement.TryGetProperty("peerCandidates", out var peers))
                foreach (var item in peers.EnumerateArray())
                    if (IPEndPoint.TryParse(item.GetString() ?? "", out var endpoint))
                        list.Add(endpoint);
            if (list.Count > 0) return list;
            // 对端可能比我们晚几十毫秒到，等一下再读一次
            try { await Task.Delay(TimeSpan.FromMilliseconds(120), ct).ConfigureAwait(false); }
            catch (OperationCanceledException) { return []; }
        }
        return [];
    }

    /// <summary>从多个反射器各问一次，拿到这个本地端口在公网上的样子。</summary>
    private static async Task<List<IPEndPoint>> DiscoverTcpAsync(
        string reflectorHosts, int localPort, CancellationToken ct)
    {
        // 各反射器端口一起问。原先逐个串行，其中一台被本机代理接管时每个都要等满
        // 8 秒超时。结果仍按 主机 × 端口 的顺序收，和原来的顺序一致。
        var probes = reflectorHosts.Split([',', ';', ' '],
                StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .SelectMany(host => TcpReflectorPorts.Select(port =>
                TcpPuncher.DiscoverAsync(host, port, localPort, ct)))
            .ToList();
        var results = await Task.WhenAll(probes).ConfigureAwait(false);
        var found = new List<IPEndPoint>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var seenAs in results)
        {
            if (seenAs is not null && seen.Add(seenAs.ToString())) found.Add(seenAs);
            if (found.Count >= 4) break;
        }
        return found;
    }

    /// <summary>先占一个端口号拿到值再放开，之后所有 TCP 操作都绑这个号（靠 SO_REUSEADDR）。</summary>
    private static int ReserveTcpPort()
    {
        using var probe = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp);
        probe.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.ReuseAddress, true);
        probe.Bind(new IPEndPoint(IPAddress.Any, 0));
        return ((IPEndPoint)probe.LocalEndPoint!).Port;
    }

    /// <param name="PeerAlternates">agent 备用 socket 的出口，见 <see cref="P2PTags.UdpAlternate"/>。</param>
    internal readonly record struct Rendezvous(
        ulong Session, IReadOnlyList<IPEndPoint> PeerCandidates, DateTimeOffset PunchAt,
        IReadOnlyList<IPEndPoint> PeerTcpCandidates, bool PeerTcpEndpointIndependent,
        IReadOnlyList<IPEndPoint> PeerAlternates);

    /// <summary>
    /// TCP 候选在同一个候选列表里传，加 "t:" 前缀区分。
    ///
    /// 这样控制面一个字都不用改——它只是把字符串原样转给对端。NAT 对 TCP 和 UDP
    /// 是两套独立映射，必须分开带，但没必要为此再加一组接口字段。
    /// </summary>
    public const string TcpTag = P2PTags.Tcp;

    internal static async Task<Rendezvous> ExchangeAsync(
        string baseUrl, string serverId, IReadOnlyList<IPEndPoint> mine, CancellationToken ct)
        => await ExchangeAsync(baseUrl, serverId, mine, [], false, ct).ConfigureAwait(false);

    internal static async Task<Rendezvous> ExchangeAsync(
        string baseUrl, string serverId, IReadOnlyList<IPEndPoint> mine,
        IReadOnlyList<IPEndPoint> mineTcp, bool myTcpAddressDependent, CancellationToken ct)
    {
        var session = (ulong)Random.Shared.NextInt64();
        // 复用控制面那条热连接，省掉一次 TLS 握手，见 ControlPlaneClient.Http。
        var http = ControlPlaneClient.Http;
        using var timer = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timer.CancelAfter(TimeSpan.FromSeconds(10));
        var url = $"{baseUrl.TrimEnd('/')}/api/v1/tunnel/punch?server_id={Uri.EscapeDataString(serverId)}";
        // 手工拼这个 JSON 而不是用 PostAsJsonAsync：后者走反射序列化，在裁剪后的
        // 单文件发布里会在运行时找不到类型。仓库其他地方用源生成上下文是同一个原因。
        var body = new System.Text.StringBuilder()
            .Append("{\"sessionId\":\"").Append(session.ToString("x16")).Append("\",\"candidates\":[");
        var tcpTag = P2PTags.TcpTagFor(myTcpAddressDependent);
        var all = mine.Select(x => x.ToString())
            .Concat(mineTcp.Select(x => tcpTag + x)).ToList();
        for (var i = 0; i < all.Count; i++)
        {
            if (i > 0) body.Append(',');
            body.Append('"').Append(JsonEncodedText.Encode(all[i])).Append('"');
        }
        body.Append("]}");
        using var content = new StringContent(body.ToString(), System.Text.Encoding.UTF8, "application/json");
        using var response = await http.PostAsync(url, content, timer.Token).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();

        using var document = JsonDocument.Parse(
            await response.Content.ReadAsStringAsync(timer.Token).ConfigureAwait(false));
        var root = document.RootElement;

        var peers = new List<IPEndPoint>();
        var peersTcp = new List<IPEndPoint>();
        var alternates = new List<IPEndPoint>();
        // 对端只要有一条 TCP 候选带的是端点无关的前缀，就按端点无关算：同一侧的
        // 候选类型必然一致，而混进来的老前缀条目只说明对端版本旧，不说明它对称。
        var peerTcpEim = false;
        if (root.TryGetProperty("peerCandidates", out var list) && list.ValueKind == JsonValueKind.Array)
            foreach (var item in list.EnumerateArray())
            {
                if (P2PTags.TryStripAlternate(item.GetString() ?? "", out var alternate))
                {
                    if (IPEndPoint.TryParse(alternate, out var spare)) alternates.Add(spare);
                    continue;
                }
                var tcp = P2PTags.TryStripTcp(item.GetString() ?? "", out var text, out var eim);
                if (!IPEndPoint.TryParse(text, out var endpoint)) continue;
                if (tcp)
                {
                    peersTcp.Add(endpoint);
                    peerTcpEim |= eim;
                }
                else peers.Add(endpoint);
            }

        // 优先相对值：见服务端 _punch_payload。两台机器的钟差多少都不影响约定时刻，
        // 而绝对值那条路会把对时误差直接变成打洞偏差。
        var punchAt = root.TryGetProperty("punchInMs", out var inMs) && inMs.TryGetDouble(out var delayMs)
            ? DateTimeOffset.UtcNow + TimeSpan.FromMilliseconds(delayMs)
            : root.TryGetProperty("punchAt", out var at) && at.TryGetDouble(out var seconds)
                ? DateTimeOffset.FromUnixTimeMilliseconds((long)(seconds * 1000))
                : DateTimeOffset.UtcNow.AddSeconds(1);
        return new Rendezvous(session, peers, punchAt, peersTcp, peerTcpEim, alternates);
    }

    /// <summary>
    /// 关一条不再需要的隧道，但绝不阻塞调用方。
    ///
    /// QUIC 的 CloseAsync/DisposeAsync 在一条已经黑洞的链路上会等很久（实测量级是
    /// 几十秒）。被否决的隧道怎么收摊和玩家没关系，所以扔到后台并且封一个上限——
    /// 超时就不管了，进程退出时操作系统会收掉那个 socket。
    /// </summary>
    internal static void DisposeInBackground(IMuxiTunnel tunnel)
    {
        _ = Task.Run(async () =>
        {
            try
            {
                await tunnel.DisposeAsync().AsTask()
                    .WaitAsync(TimeSpan.FromSeconds(3)).ConfigureAwait(false);
            }
            catch (Exception error)
            {
                Log.Info($"后台收隧道没能在 3 秒内完成，不再等：{error.GetType().Name}");
            }
        });
    }

    public async ValueTask DisposeAsync()
    {
        // 这两步都可能在黑洞链路上挂很久，各自封住。调用方要么走
        // DisposeInBackground（推荐），要么自己承担最多这么久。
        try
        {
            await _connection.CloseAsync(0).AsTask()
                .WaitAsync(TimeSpan.FromSeconds(2)).ConfigureAwait(false);
        }
        catch { }
        try
        {
            await _connection.DisposeAsync().AsTask()
                .WaitAsync(TimeSpan.FromSeconds(2)).ConfigureAwait(false);
        }
        catch { }
        Log.Info($"P2P 隧道 {_peer} 已关闭");
    }
}
