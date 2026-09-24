using System.Collections.Concurrent;
using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using BatterMC.Protocol;

namespace BatterMC.Core;

public sealed record RouteProbeResult(
    RouteCandidate Candidate,
    IPEndPoint Endpoint,
    bool Reachable,
    TimeSpan Latency,
    string? Error)
{
    public string DisplayName => Candidate.Label ?? Candidate.Id ?? $"{Endpoint.Address}:{Endpoint.Port}";
}

/// <param name="Selected">
/// 最优的那条。一条 TCP 线路都不通时为 null——这时只剩打洞一条路，见
/// <see cref="MinecraftRouteProxy.StartAsync"/> 的 allowNoRoute。
/// </param>
public sealed record RouteSelection(RouteProbeResult? Selected, IReadOnlyList<RouteProbeResult> Reachable)
{
    public static RouteSelection None { get; } = new(null, []);
}

/// <summary>一条已经用真实 Minecraft 握手验证过的线路。</summary>
public sealed record RouteEstablishment(
    RouteProbeResult Route, RouteKind Kind, MinecraftStatus Status);

/// <summary>
/// 连服务器要用的东西：线路候选、隧道凭据、打洞参数。凭据和候选都要问控制面现取，
/// 所以由调用方在后台准备，见 <see cref="MinecraftRouteProxy.ConnectInBackground"/>。
/// </summary>
/// <param name="TunnelToken">取不到时为 null：只能走直连/中转，不能建隧道、不能打洞。</param>
public sealed record ConnectPlan(
    IReadOnlyList<ServerEntry> Servers, string? TunnelToken, string ControlPlaneBaseUrl,
    string ServerId, string ReflectorHost, int TunnelPort);

/// <summary>
/// 线路和服务端的 agent 都是通的，但 agent 接不上它本机的 Minecraft：服务器正在启动、
/// 重启，或者游戏服挂了。
///
/// 必须和"网络不通"分开。网络不通时换线路、打洞都有意义；这种情况下所有线路都通到
/// 同一台 agent、同一个接不上的游戏服，换谁都一样。实测服务器重启那一分钟里，客户端
/// 照样把隧道、中转、三轮打洞挨个试完才放弃，每轮打洞服务端还要推 1.6 MB 压测包。
/// </summary>
public sealed class ServerNotRespondingException(Exception inner)
    : InvalidOperationException(
        "服务器的 Minecraft 没有响应：线路是通的，但服务端接不上它本机的游戏服（多半正在启动或重启）",
        inner);

/// <summary>
/// 把 Minecraft 的固定 TCP 连接收进 127.0.0.1，再转发到并行探测得到的最优线路。
/// 游戏永远只看到本机端口，网络线路选择和故障切换都留在启动器侧。
/// </summary>
public sealed class MinecraftRouteProxy : IAsyncDisposable
{
    /// <summary>
    /// 保温间隔。服务端对没握手的空闲连接 60 秒就断（实测），所以隧道探活必须
    /// 明显短于这个值，否则玩家读条几分钟之后拿到的是一条早就死掉的线路。
    /// </summary>
    private static readonly TimeSpan KeepWarmInterval = TimeSpan.FromSeconds(25);

    private readonly TcpListener _listener;
    private readonly CancellationTokenSource _lifetime = new();
    private readonly ConcurrentDictionary<long, Task> _connections = new();
    private readonly Task _acceptLoop;
    private long _nextConnectionId;

    /// <summary>
    /// 游戏每发起一条连接，转发之前先跑一遍。用来换进服票据——服务端的 LoginGate 会
    /// 拿这张票核实"连过来的真是这个 UID 本人"。
    ///
    /// 必须在转发之前**完成**，不能并行发出去了事：票是服务端收到登录包之后才去平台
    /// 核销的，换票只要比登录包晚一步，服务端就查无此票，玩家被拒之门外。
    ///
    /// 里面抛异常不挡连接：换不到票时让玩家照常连过去，由服务端给出拒绝理由，
    /// 比在这里静默断开好查得多。
    /// </summary>
    public Func<CancellationToken, Task>? BeforeGameConnects { get; set; }
    private Task? _keepWarm;

    /// <summary>转发时的候选顺序；验证通过的线路会被提到最前面。</summary>
    private volatile IReadOnlyList<RouteProbeResult> _routes;

    /// <summary>服务器表（线路候选）。后台连接每轮会从控制面重新取，重连时拿它重新探。</summary>
    private volatile IReadOnlyList<ServerEntry> _servers;

    private MinecraftRouteProxy(TcpListener listener, RouteSelection selection, IReadOnlyList<ServerEntry> servers)
    {
        _listener = listener;
        Selection = selection;
        _routes = selection.Reachable;
        _servers = servers;
        _acceptLoop = AcceptLoopAsync();
    }

    /// <summary>线路掉了又换到另一条、或者后台重连连上时触发，供界面提示玩家。</summary>
    public event Action<RouteEstablishment>? RouteChanged;

    /// <summary>
    /// 后台第一轮没连上、或者掉线后重建也没成时触发（之后每轮失败不再重复），参数是失败原因。
    /// 后台会接着连，连上时触发 <see cref="RouteChanged"/>。
    /// </summary>
    public event Action<Exception>? RouteUnavailable;

    /// <summary>
    /// 还没连上时，游戏来连本地代理最多等多久。等的是被这次连接叫醒的那一轮重连，
    /// 一般几百毫秒就有结果；这个上限只防重连本身卡住。游戏登录阶段自己等 30 秒。
    /// </summary>
    public TimeSpan PendingRouteWait { get; set; } = TimeSpan.FromSeconds(15);

    /// <summary>
    /// 给玩家看的一句话：为什么还没连上。界面和本地代理的替身应答用同一套说法。
    /// </summary>
    /// <param name="error">最近一次失败；还没失败过（第一轮还在连）时为 null。</param>
    public static string DescribeUnavailable(Exception? error) => error switch
    {
        null => "还在连接服务器",
        ServerNotRespondingException => "服务器正在启动或重启",
        _ => "暂时连不上服务器",
    };

    /// <summary>
    /// 已建立的端侧隧道。非空时游戏的每条连接都从隧道里开数据流，而不是自己
    /// 去连远端——Minecraft 全程只看得到 127.0.0.1，公网那一段由两端的工具负责。
    /// </summary>
    private IMuxiTunnel? _tunnel;

    /// <summary>
    /// 已经验证通过的线路；还没连上、或者掉线后重建失败正在后台重连时为 null。
    /// 好几条后台任务都会读写它，所以是 volatile。
    /// </summary>
    public RouteEstablishment? Established
    {
        get => _established;
        private set => _established = value;
    }
    private volatile RouteEstablishment? _established;

    /// <summary>最近一次选路的结果。后台连接会重新探，所以会变。</summary>
    public RouteSelection Selection { get; private set; }
    public int LocalPort => ((IPEndPoint)_listener.LocalEndpoint).Port;
    public string LocalAddress => $"127.0.0.1:{LocalPort}";

    /// <summary>
    /// 候选声明为 Auto 时才按地址反推类型；显式声明的类型（尤其是 Relay/TcpTunnel）
    /// 不能按 IP 猜，否则中转会被显示成「直连」。
    /// </summary>
    public static RouteKind EffectiveKind(RouteProbeResult route) =>
        route.Candidate.Kind == RouteKind.Auto
            ? Classify(route.Endpoint.Address)
            : route.Candidate.Kind;

    /// <summary>
    /// 在启动游戏之前真正把线路打通：逐条候选做一次完整的 Minecraft 状态查询，
    /// 第一条问得出版本号的就是它。
    ///
    /// 光看 TCP 握手不够——中转节点、运营商劫持、错配的端口转发都能让 TCP 连上
    /// 而后面没有 Minecraft。验证通过后该线路被提到转发顺序最前面，并启动保温，
    /// 玩家读条期间线路掉了能及时换掉而不是等进服才发现。
    /// </summary>
    public async Task<RouteEstablishment> EstablishAsync(
        IProgress<string>? progress, CancellationToken ct)
    {
        var routes = _routes;
        var failures = new List<string>();
        foreach (var route in routes)
        {
            var kind = EffectiveKind(route);
            progress?.Report($"正在连接 {Describe(kind)} {route.Endpoint}");
            try
            {
                // 握手里写 127.0.0.1，和游戏经本地代理进服时写的完全一致。
                var status = await MinecraftPing
                    .QueryAsync(route.Endpoint, "127.0.0.1", TimeSpan.FromSeconds(8), ct)
                    .ConfigureAwait(false);

                Promote(route);
                var established = new RouteEstablishment(route, kind, status);
                Established = established;
                _keepWarm ??= KeepWarmAsync(_lifetime.Token);
                Log.Info($"线路已建立 {kind} {route.Endpoint} " +
                         $"{status.Version} {status.Online}/{status.Max} " +
                         $"{status.Elapsed.TotalMilliseconds:0}ms");
                progress?.Report($"已连上 {Describe(kind)}，{status.Elapsed.TotalMilliseconds:0} ms");
                return established;
            }
            catch (Exception error) when (error is not OperationCanceledException
                                          || !ct.IsCancellationRequested)
            {
                var reason = error is OperationCanceledException ? "超时" : error.Message;
                failures.Add($"{Describe(kind)} {route.Endpoint}：{reason}");
                Log.Warn($"线路 {route.Endpoint} 未能建立：{reason}");
                progress?.Report($"{Describe(kind)} 不可用，换下一条");
            }
        }
        throw new InvalidOperationException(
            "所有线路都没能建立连接：" + Environment.NewLine + string.Join(Environment.NewLine, failures));
    }

    /// <summary>
    /// 按优先级把连接建起来：打洞直连 → 端侧隧道 → 纯直连/中转。
    ///
    /// 三级都在这里收口，是因为上一版把顺序散在调用方，结果隧道那一级抛异常时
    /// 直接把整个启动流程带崩了——玩家明明有一条能用的中转线路却进不去游戏。
    /// 前两级失败一律只降级，最后一级才允许抛。
    /// </summary>
    /// <param name="skipP2P">
    /// 诊断用：跳过打洞，直接验隧道那一段。打洞通常会成功，成功了就轮不到隧道，
    /// 隧道自己的毛病就永远露不出来——实测就是这样漏掉过一次隧道握手超时。
    /// </param>
    /// <summary>隧道内做一次 MC 状态查询的上限。建立/验收阶段用它。</summary>
    private static readonly TimeSpan StatusTimeout = TimeSpan.FromSeconds(8);

    /// <summary>保温探活的上限。和同一处直连探活的 6 秒保持一致。</summary>
    private static readonly TimeSpan KeepWarmStatusTimeout = TimeSpan.FromSeconds(6);

    /// <param name="upgradeInBackground">
    /// 启动游戏用的模式：先用最快能通的线路把连接建起来、马上放行游戏，打洞放到后台，
    /// 通了再换过去。诊断默认不开，好在前台完整量一遍打洞。
    ///
    /// 为什么值得这样做：打洞 + 验收实测 7~13 秒，原先整段串在"点开始游戏"和"游戏
    /// 进程启动"之间。而 Minecraft 要加载几分钟模组才会发起第一条连接——这几分钟里
    /// 线路是闲着的，足够在后台打完洞、验完带宽、失败了还能重试几次。于是：
    /// <list type="bullet">
    /// <item>有 IPv6/局域网/公网直连的玩家：直接用，根本不打洞（省下整段 10 秒）。</item>
    /// <item>只能走中转的玩家：先挂在中转上启动，后台打通后在没有游戏连接的时刻
    ///   切到 P2P。中转实际只承担了验收那一次状态查询。</item>
    /// <item>什么 TCP 线路都不通的玩家：只能等打洞，前台打，失败了重试。</item>
    /// </list>
    /// </param>
    public async Task<RouteEstablishment> EstablishBestAsync(
        string? tunnelToken, string controlPlaneBaseUrl, string serverId,
        string reflectorHost, int tunnelPort, IProgress<string>? progress, CancellationToken ct,
        bool skipP2P = false, bool upgradeInBackground = false)
    {
        if (!string.IsNullOrWhiteSpace(tunnelToken))
        {
            _p2p = skipP2P
                ? null
                : new P2PParams(controlPlaneBaseUrl, serverId, tunnelToken!, reflectorHost);
            _tunnelToken = tunnelToken;
            _tunnelPort = tunnelPort;

            // 一条 TCP 线路都没有时，打洞是唯一的指望——不管什么模式都在前台打，并且
            // 多给几次机会。原先这种情况根本走不到打洞：选路阶段就抛了"所有游戏线路
            // 均不可达"。实测正是这样：中转那台的 frpc 没起、玩家又没有 IPv6，打洞
            // 本来能通，玩家却连尝试都没有就被拒之门外。
            if (_routes.Count == 0 && _p2p is not null)
                return await ForegroundP2PAsync(_p2p, progress, ct,
                    "没有可达的 TCP 线路，打洞也没有成功").ConfigureAwait(false);

            if (!upgradeInBackground && _p2p is not null)
            {
                var p2p = await TryEstablishP2PAsync(
                        controlPlaneBaseUrl, serverId, tunnelToken!, reflectorHost, progress, ct)
                    .ConfigureAwait(false);
                if (p2p is not null) return p2p;
            }

            RouteEstablishment? viaTunnel = null;
            try
            {
                viaTunnel = await EstablishTunnelAsync(tunnelToken!, tunnelPort, progress, ct)
                    .ConfigureAwait(false);
            }
            // 服务器没开（ServerNotRespondingException）不在这里接：直连、中转、打洞通到的
            // 都是同一个游戏服，接着试只是拖时间。
            catch (Exception error) when (error is not ServerNotRespondingException
                                          && !Cancellation.IsShutdown(error, ct))
            {
                // 常见情形：玩家只剩中转可用，而中转节点上没有开隧道端口。
                Log.Warn($"端侧隧道全部失败，退回直连/中转：{error.Message}");
                progress?.Report("隧道不可用，改用直连/中转");
            }

            RouteEstablishment established;
            if (viaTunnel is not null)
            {
                established = viaTunnel;
            }
            else
            {
                try
                {
                    established = await EstablishAsync(progress, ct).ConfigureAwait(false);
                }
                // TCP 探测是通的，却没有一条真能进服：玩家开着 TUN 模式的代理（本地替所有
                // 地址完成握手）、中转节点在但它后面的 frpc 挂了……这和"一条线路都没有"
                // 是一回事，打洞照样可能是唯一的活路。诊断模式在前面已经打过了，不重复。
                catch (Exception error) when (upgradeInBackground && _p2p is not null
                                              && (error is not OperationCanceledException
                                                  || !ct.IsCancellationRequested))
                {
                    Log.Warn($"TCP 线路探测通过但都进不了服，改为打洞：{error.Message}");
                    return await ForegroundP2PAsync(_p2p, progress, ct,
                        "TCP 线路都进不了服，打洞也没有成功").ConfigureAwait(false);
                }
            }
            if (upgradeInBackground) StartUpgradeIfPaid(established);
            return established;
        }

        return await EstablishAsync(progress, ct).ConfigureAwait(false);
    }

    /// <summary>
    /// 只剩打洞一条路时在前台打，最多 <see cref="ForegroundP2PAttempts"/> 次；最后一次放宽
    /// 带宽验收（见 <see cref="BuildP2PAsync"/> 的 acceptSlow）。
    /// </summary>
    private async Task<RouteEstablishment> ForegroundP2PAsync(
        P2PParams p2p, IProgress<string>? progress, CancellationToken ct, string failure)
    {
        for (var attempt = 1; attempt <= ForegroundP2PAttempts; attempt++)
        {
            var only = await TryEstablishP2PAsync(
                    p2p.ControlPlaneBaseUrl, p2p.ServerId, p2p.Token, p2p.ReflectorHost, progress, ct,
                    acceptSlow: attempt == ForegroundP2PAttempts)
                .ConfigureAwait(false);
            if (only is not null) return only;
            if (attempt < ForegroundP2PAttempts)
            {
                progress?.Report($"打洞没成，{ForegroundRetryDelay.TotalSeconds:0} 秒后再试一次");
                await Task.Delay(ForegroundRetryDelay, ct).ConfigureAwait(false);
            }
        }
        throw new InvalidOperationException(failure);
    }

    /// <summary>没有任何 TCP 线路时前台打洞的次数。控制面按出口 IP 限 30 秒 4 次。</summary>
    private const int ForegroundP2PAttempts = 3;
    private static readonly TimeSpan ForegroundRetryDelay = TimeSpan.FromSeconds(1);

    /// <summary>
    /// 后台升级的重试节奏。第一次立刻打；失败后隔几秒再来，越往后越稀——
    /// 打不通的线路多半是 NAT 组合本身不行，没必要一直敲。
    /// </summary>
    private static readonly TimeSpan[] UpgradeBackoff =
        [TimeSpan.Zero, TimeSpan.FromSeconds(3), TimeSpan.FromSeconds(12), TimeSpan.FromSeconds(40)];

    /// <summary>打洞要用的那几样东西。后台升级和掉线后重建都要用，所以记下来。</summary>
    private sealed record P2PParams(
        string ControlPlaneBaseUrl, string ServerId, string Token, string ReflectorHost);

    private P2PParams? _p2p;

    /// <summary>后台升级因为服务器没开而暂停了；保温下次确认服务器正常时接着升级。</summary>
    private volatile bool _upgradeParked;
    private string? _tunnelToken;
    private int _tunnelPort;
    private Task? _upgrade;

    /// <summary>这条线路的流量算不算在我们自己的服务器上。</summary>
    private static bool IsPaid(RouteKind kind) => kind is RouteKind.Relay or RouteKind.TcpTunnel;

    /// <summary>当前线路要花我们的带宽、且还没在升级时，起一个后台打洞。</summary>
    private void StartUpgradeIfPaid(RouteEstablishment established)
    {
        if (_p2p is null || !IsPaid(established.Kind)) return;
        if (_upgrade is { IsCompleted: false }) return;
        Log.Info($"当前走的是 {Describe(established.Kind)}，后台打洞，通了就换过去");
        _upgrade = UpgradeToP2PAsync(_p2p, _lifetime.Token);
    }

    /// <summary>
    /// 后台把线路升级成 P2P：打洞 + 验收，失败了按 <see cref="UpgradeBackoff"/> 重试。
    ///
    /// 打通之后不立刻抢：游戏正开着的连接不能迁移，所以等到一条游戏连接都没有的时刻
    /// 再换。启动场景下这几乎总是成立的——游戏还在加载模组。
    /// </summary>
    private async Task UpgradeToP2PAsync(P2PParams p2p, CancellationToken ct)
    {
        try
        {
            for (var attempt = 0; attempt < UpgradeBackoff.Length; attempt++)
            {
                if (UpgradeBackoff[attempt] > TimeSpan.Zero)
                    await Task.Delay(UpgradeBackoff[attempt], ct).ConfigureAwait(false);
                if (_tunnel is P2PTunnel { IsAlive: true }) return;

                (IMuxiTunnel Tunnel, MinecraftStatus Status)? built;
                try { built = await BuildP2PAsync(p2p, null, ct).ConfigureAwait(false); }
                // 打通了但服务器那头的游戏服没开（在重启）：接着按退避打只是让服务端一遍遍推
                // 压测流量。先停下，等保温确认服务器回来了再接着升级（见 KeepWarmAsync）。
                catch (ServerNotRespondingException refused)
                {
                    Log.Info("后台打洞打通了，但" + refused.Message + "——先不升级，等服务器回来");
                    _upgradeParked = true;
                    return;
                }
                if (built is not { } ready)
                {
                    Log.Info($"后台打洞第 {attempt + 1} 次没成"
                             + (attempt + 1 < UpgradeBackoff.Length ? "，稍后再试" : "，不再尝试，继续用当前线路"));
                    continue;
                }

                var adoptedIt = false;
                try
                {
                    // 等到没有游戏连接再换。等的这段时间里 QUIC 自己的保活维持着映射。
                    var waited = false;
                    while ((!_connections.IsEmpty || Rebuilding) && ready.Tunnel.IsAlive)
                    {
                        waited = true;
                        await Task.Delay(TimeSpan.FromSeconds(2), ct).ConfigureAwait(false);
                    }

                    // 等过一阵的话，接手前再确认一次它还活着：IsAlive 只在开流失败时才翻假，
                    // 洞在等待期间塌了（QUIC 30 秒空闲判死）它照样报 true。
                    var alive = ready.Tunnel.IsAlive;
                    if (alive && waited)
                    {
                        try
                        {
                            await using var probe = await ready.Tunnel.OpenStreamAsync(ct).ConfigureAwait(false);
                            await MinecraftPing.QueryAsync(probe, "127.0.0.1", 25565, KeepWarmStatusTimeout, ct)
                                .ConfigureAwait(false);
                        }
                        catch (Exception error) when (!Cancellation.IsShutdown(error, ct))
                        {
                            alive = false;
                        }
                    }
                    if (!alive)
                    {
                        Log.Warn("后台打通的 P2P 在等待切换期间失效，重新打");
                        continue;
                    }

                    var adopted = AdoptP2P(ready.Tunnel, ready.Status);
                    adoptedIt = true;
                    Log.Info("已从付费线路切到 P2P 直连（后台升级）");
                    RouteChanged?.Invoke(adopted);
                    return;
                }
                finally
                {
                    // 没接手的（失效了、或者代理在等待期间被关了）一律收掉，别漏 QUIC 连接。
                    if (!adoptedIt) P2PTunnel.DisposeInBackground(ready.Tunnel);
                }
            }
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested) { }
        catch (Exception error)
        {
            Log.Warn("后台打洞升级异常，继续用当前线路：" + error.Message);
        }
    }

    /// <summary>
    /// 在启动游戏之前把端侧隧道建起来，并一直挂着。
    ///
    /// 这是"先连上再进游戏"的关键：隧道终结在两端的工具之间，Minecraft 不在这条
    /// 链路上，所以它不受服务端那个 60 秒空闲超时的约束，玩家读条几分钟也不会断。
    /// 逐条候选去连，第一条握手成功的就用它。
    /// </summary>
    public async Task<RouteEstablishment> EstablishTunnelAsync(
        string token, int tunnelPort, IProgress<string>? progress, CancellationToken ct)
    {
        var failures = new List<string>();
        foreach (var route in _routes)
        {
            var kind = EffectiveKind(route);
            var endpoint = new IPEndPoint(route.Endpoint.Address, tunnelPort);
            progress?.Report($"正在连接 {Describe(kind)} {endpoint}");
            try
            {
                var tunnel = await TunnelClient
                    .ConnectAsync(endpoint, token, TimeSpan.FromSeconds(8), ct)
                    .ConfigureAwait(false);

                // 隧道这一级也量一次，但**只记录不拒绝**：它是最后的兜底，慢也得用。
                // 量它的意义是做对照——同一台机器同一时刻，UDP 跑 1 KB/s 而 TCP 正常，
                // 才能把锅定在"运营商掐 UDP"上，而不是笼统说"这条线路不行"。
                //
                // 但走中转时不量：那 2 MB 是从我们自己的中转推出去的，每个玩家每次
                // 启动都推一遍，正是这套东西想省下的带宽；而且它只能告诉我们中转有
                // 多快，说明不了玩家那条路径的问题。直连隧道（局域网 / IPv6）不花
                // 我们的钱，照量。
                if (kind is not (RouteKind.Relay or RouteKind.TcpTunnel))
                {
                    try
                    {
                        var rate = await TunnelProbe
                            .MeasureAsync(tunnel, TunnelProbe.DefaultBytes, TunnelProbe.DefaultTimeout, ct)
                            .ConfigureAwait(false);
                        Log.Info($"隧道带宽实测：{rate.MbytesPerSecond:0.00} MB/s");
                    }
                    // 这里连取消也要吞掉（外层 ct 真被取消时除外）。对照测量失败不该
                    // 把整条隧道判死——实测出现过探测超时穿透出去、把一条本来可用的
                    // 隧道报成"未能建立：超时"，玩家因此被推到更差的线路上。
                    catch (Exception error) when (error is not OperationCanceledException
                                                  || !ct.IsCancellationRequested)
                    {
                        Log.Warn("隧道带宽实测未完成（不影响使用）：" + error.Message);
                    }
                }

                // 隧道通了不代表对面那台的 Minecraft 也起来了。开一条数据流做一次
                // 真实状态查询，问得出版本号才算真的能进服——否则玩家会在读条走完
                // 之后才发现连不上，那时候已经晚了。
                MinecraftStatus status;
                // 流已经开好（agent 回了 StreamReady）之后，状态查询才被关掉，才能算"agent 在、
                // 游戏服不在"。开流这一步的 EOF/重置不算：agent 总是先回 StreamReady 再去接
                // Minecraft，所以那一步断掉的只可能是路径——玩家本机的 TUN 代理、frps 接不上 agent。
                var streamOpened = false;
                try
                {
                    using var probe = await tunnel.OpenStreamAsync(ct).ConfigureAwait(false);
                    streamOpened = true;
                    status = await MinecraftPing
                        .QueryAsync(probe, "127.0.0.1", 25565, StatusTimeout, ct).ConfigureAwait(false);
                }
                catch (Exception error)
                {
                    await tunnel.DisposeAsync().ConfigureAwait(false);
                    // 所有线路都通到这同一台 agent，换下一条也是一样，直接报上去。
                    if (streamOpened && IsRefusedBehindAgent(error)) throw new ServerNotRespondingException(error);
                    throw;
                }

                _tunnel = tunnel;
                Promote(route);
                var established = new RouteEstablishment(route, kind, status);
                Established = established;
                _keepWarm ??= KeepWarmAsync(_lifetime.Token);
                Log.Info($"隧道已建立 {kind} {endpoint}，" +
                         $"服务端 {status.Version} {status.Online}/{status.Max}");
                progress?.Report($"已连上 {Describe(kind)}，服务端 {status.Version}");
                return established;
            }
            catch (Exception error) when (error is not ServerNotRespondingException
                                          && !Cancellation.IsShutdown(error, ct))
            {
                var reason = error is OperationCanceledException ? "超时" : error.Message;
                failures.Add($"{Describe(kind)} {endpoint}：{reason}");
                Log.Warn($"隧道 {endpoint} 未能建立：{reason}");
                progress?.Report($"{Describe(kind)} 不可用，换下一条");
            }
        }
        throw new InvalidOperationException(
            "所有线路都没能建立隧道：" + Environment.NewLine + string.Join(Environment.NewLine, failures));
    }

    /// <summary>
    /// 先试打洞直连。成功的话游戏流量完全不经过我们的服务器，只有几十字节的信令
    /// 走控制面——这是唯一能真正省下中转带宽的路径。
    ///
    /// 打不通是常态（对称 NAT、UDP 被封、对端没就绪都会失败），所以这里失败不抛给
    /// 玩家，由调用方接着试隧道和中转。
    /// </summary>
    public Task<RouteEstablishment?> TryEstablishP2PAsync(
        string controlPlaneBaseUrl, string serverId, string token, string reflectorHost,
        IProgress<string>? progress, CancellationToken ct)
        => TryEstablishP2PAsync(controlPlaneBaseUrl, serverId, token, reflectorHost, progress, ct,
            acceptSlow: false);

    /// <param name="acceptSlow">见 <see cref="BuildP2PAsync"/>。</param>
    private async Task<RouteEstablishment?> TryEstablishP2PAsync(
        string controlPlaneBaseUrl, string serverId, string token, string reflectorHost,
        IProgress<string>? progress, CancellationToken ct, bool acceptSlow)
    {
        var built = await BuildP2PAsync(
                new P2PParams(controlPlaneBaseUrl, serverId, token, reflectorHost), progress, ct,
                acceptSlow)
            .ConfigureAwait(false);
        if (built is not { } ready) return null;
        var established = AdoptP2P(ready.Tunnel, ready.Status);
        progress?.Report($"P2P 直连成功，服务端 {ready.Status.Version}");
        return established;
    }

    /// <summary>
    /// 把一条已经通过验收的 P2P 隧道换成当前隧道。换下来的旧隧道等到没有连接用它时
    /// 再收——正在跑的游戏连接不能被拆。
    /// </summary>
    /// <summary>换 <see cref="_tunnel"/> 的几处（接管 P2P、保温摘除、释放）共用的锁。</summary>
    private readonly object _swapGate = new();

    /// <summary>
    /// 有人正在重建线路（保温、后台重连）。这期间后台升级先不接管，免得两边互相覆盖 _tunnel。
    ///
    /// 是计数不是开关：保温和后台重连会交叠——保温重建失败时在自己的 finally 之前就把重连
    /// 拉起来了，开关的话保温一收尾就把重连那一轮的"正在重建"也清掉，升级趁机接管 P2P，
    /// 随后被重连建好的隧道覆盖，那条 QUIC 连接就漏了。
    /// </summary>
    private int _rebuilding;
    private bool Rebuilding => Volatile.Read(ref _rebuilding) > 0;

    private bool _disposed;

    private RouteEstablishment AdoptP2P(IMuxiTunnel tunnel, MinecraftStatus status)
    {
        IMuxiTunnel? old;
        lock (_swapGate)
        {
            // 代理已经在关（游戏退出了）：迟到的接管不能装上去——没人会再收它。
            if (_disposed)
            {
                P2PTunnel.DisposeInBackground(tunnel);
                throw new OperationCanceledException("本地代理已关闭，放弃接管 P2P");
            }
            old = _tunnel;
            _tunnel = tunnel;
        }
        _keepWarm ??= KeepWarmAsync(_lifetime.Token);
        // P2P 不对应清单里的任何一条候选，造一条合成记录好让上层统一处理
        var synthetic = new RouteProbeResult(
            new RouteCandidate { Id = "p2p", Label = "P2P 直连", Kind = RouteKind.UdpTunnel },
            new IPEndPoint(IPAddress.None, 0), true, status.Elapsed, null);
        var established = new RouteEstablishment(synthetic, RouteKind.UdpTunnel, status);
        Established = established;
        Log.Info($"P2P 直连已建立，服务端 {status.Version} {status.Online}/{status.Max}");
        if (old is not null && !ReferenceEquals(old, tunnel)) _ = RetireWhenIdleAsync(old);
        return established;
    }

    /// <summary>旧隧道等到没有游戏连接在用时再收。进程退出时由 DisposeAsync 兜底。</summary>
    private async Task RetireWhenIdleAsync(IMuxiTunnel old)
    {
        try
        {
            var stop = _lifetime.Token;
            while (!_connections.IsEmpty)
                await Task.Delay(TimeSpan.FromSeconds(5), stop).ConfigureAwait(false);
        }
        catch (Exception error) when (error is OperationCanceledException or ObjectDisposedException) { }
        P2PTunnel.DisposeInBackground(old);
    }

    /// <summary>打洞 + 验收。成功返回隧道和验收时拿到的服务端状态，不改动当前线路。</summary>
    /// <param name="acceptSlow">
    /// 带宽验收不达标也收下（状态查询仍然必须通过）。只在"一条 TCP 线路都没有、而且
    /// 已经是最后一次尝试"时用：那时 P2P 是唯一能通的路，慢一点也比进不去强；
    /// 有别的线路可退时绝不能这样放水。
    /// </param>
    private async Task<(IMuxiTunnel Tunnel, MinecraftStatus Status)?> BuildP2PAsync(
        P2PParams p2p, IProgress<string>? progress, CancellationToken ct, bool acceptSlow = false)
    {
        // 洞打通、QUIC 也握上了，状态查询却被 agent 关掉：游戏服没开，不是这条路不行。
        var refused = false;
        try
        {
            // 验收回调里填上。EstablishAsync 只在某条通过验收时才返回，
            // 所以走到下面时它一定有值。
            MinecraftStatus? status = null;
            var tunnel = await P2PTunnel.EstablishAsync(
                p2p.ControlPlaneBaseUrl, p2p.ServerId, p2p.Token, p2p.ReflectorHost, progress, ct,
                async (candidate, token2) =>
                {
                    using (var probe = await candidate.OpenStreamAsync(token2).ConfigureAwait(false))
                    {
                        try
                        {
                            status = await MinecraftPing
                                .QueryAsync(probe, "127.0.0.1", 25565, StatusTimeout, token2).ConfigureAwait(false);
                        }
                        // 只认 QUIC：TCP 打洞那条腿（MuxTunnel）是一条 socket 上的多路复用，
                        // NAT 把那条 socket 掐了，所有流都读到 EOF，看着和 agent 关流一模一样。
                        catch (Exception error) when (candidate is not MuxTunnel && IsRefusedBehindAgent(error))
                        {
                            refused = true;
                            throw;
                        }
                    }

                    // 状态查询只有几百字节，对"进世界时能不能扛住区块"没有预测力。
                    // 实测撞过小包全通、一传大流量就全丢的链路：握手和登录正常，进世界
                    // 时 ACK 一个不回，两分钟后 QUIC 判对端失联——而玩家已经读了三分钟条。
                    // 所以这里用真实大流量再压一遍。压不过的那条会被就地丢掉，换并行
                    // 打通的另一条重试，两条都不合格才退回中转。
                    progress?.Report("正在测试直连带宽");
                    try
                    {
                        var bandwidth = await TunnelProbe
                            .MeasureAsync(candidate, TunnelProbe.DefaultBytes, TunnelProbe.DefaultTimeout, token2)
                            .ConfigureAwait(false);
                        Log.Info($"{candidate.Describe} 带宽测试通过：{bandwidth.Bytes / 1024} KB / "
                                 + $"{bandwidth.Elapsed.TotalSeconds:0.0}s = {bandwidth.MbytesPerSecond:0.0} MB/s");
                    }
                    catch (IOException slow) when (acceptSlow)
                    {
                        Log.Warn($"{candidate.Describe} 带宽不达标（{slow.Message}），"
                                 + "但没有别的线路可走，先用着");
                    }
                })
                .ConfigureAwait(false);

            if (status is null)
            {
                P2PTunnel.DisposeInBackground(tunnel);
                throw new InvalidOperationException("隧道通过了验收却没拿到服务端状态");
            }
            return (tunnel, status);
        }
        catch (Exception error) when (!Cancellation.IsShutdown(error, ct))
        {
            // 前台打洞原本要连打三轮：服务器没开时每一轮都会同样落空，第一轮就该收手。
            if (refused) throw new ServerNotRespondingException(error);
            Log.Warn($"P2P 直连未建立：{error.Message}");
            progress?.Report("P2P 直连不可用，改用其他线路");
            return null;
        }
    }

    /// <summary>
    /// 在经过鉴权的通道（端侧隧道、P2P）上，状态查询被对面直接关掉：只可能是 agent
    /// 接不上本机的 Minecraft，于是收了这条流。
    ///
    /// 裸 TCP 线路上同样的现象不算数：玩家本机开着 TUN 模式的代理时，任何地址都能
    /// "连上"再被关掉，那说明不了服务器的状态。超时也不算：P2P 路径 MTU 有问题时，
    /// 状态响应（带模组列表，好几 KB）同样会超时，那是线路的毛病。
    /// </summary>
    private static bool IsRefusedBehindAgent(Exception error) =>
        error is EndOfStreamException
        || error is IOException { InnerException: SocketException { SocketErrorCode: SocketError.ConnectionReset } };

    /// <summary>把验证通过的线路提到最前，其余保持原有顺序作为降级备选。</summary>
    private void Promote(RouteProbeResult route)
    {
        var reordered = new List<RouteProbeResult> { route };
        reordered.AddRange(_routes.Where(x => !ReferenceEquals(x, route)));
        _routes = reordered;
    }

    private async Task KeepWarmAsync(CancellationToken ct)
    {
        try
        {
            var strikes = 0;
            while (!ct.IsCancellationRequested)
            {
                await Task.Delay(KeepWarmInterval, ct).ConfigureAwait(false);
                var current = Established;
                if (current is null) continue;
                var tunnel = _tunnel;

                // 游戏正在用这条隧道时不要探活。
                //
                // 理由有两条。一是没必要：真实流量本身就在续命，探活是给"连接已建好、
                // 玩家还在读条"那段空窗期用的。二是危险：探活要另开一条流，而开流本身
                // 可能失败（实测撞过 QUIC 的流额度上限），一旦失败就要判断隧道死活——
                // 判错的代价是把玩家正在用的连接一起拆掉。实测就是这么断的。
                if (!_connections.IsEmpty) continue;

                try
                {
                    // 有隧道时必须**走隧道内部**探活，不能去 ping 路由地址。
                    //
                    // 两个原因：一是 P2P 的合成路由 endpoint 是占位符（0.0.0.0:0），
                    // ping 它必然失败、反而把好好的 P2P 判死切走；二是即便地址真实，
                    // 直连 ping 通了也只证明那条路由活着，证明不了隧道还在——实测
                    // 出现过隧道在玩家读条期间悄悄超时、游戏连上来的瞬间被 reset 踢
                    // 掉线。走隧道内部既能真正续命，又能在游戏连上来之前发现它死了。
                    if (tunnel is not null && tunnel.IsAlive)
                    {
                        await using var probe = await tunnel.OpenStreamAsync(ct).ConfigureAwait(false);
                        await MinecraftPing.QueryAsync(probe, "127.0.0.1", 25565, KeepWarmStatusTimeout, ct)
                            .ConfigureAwait(false);
                    }
                    else if (tunnel is not null)
                    {
                        throw new IOException("隧道已不可用");
                    }
                    else
                    {
                        await MinecraftPing
                            .QueryAsync(current.Route.Endpoint, "127.0.0.1", TimeSpan.FromSeconds(6), ct)
                            .ConfigureAwait(false);
                    }
                    strikes = 0;
                    // 升级在服务器重启时暂停过，现在服务器回来了，接着升级
                    if (_upgradeParked)
                    {
                        _upgradeParked = false;
                        StartUpgradeIfPaid(current);
                    }
                }
                catch (Exception error) when (error is not OperationCanceledException
                                              || !ct.IsCancellationRequested)
                {
                    var what = tunnel is null ? $"线路 {current.Route.Endpoint}" : tunnel.Describe;

                    // 一次探活失败不能判死。开流失败、对端瞬时忙、探活包被丢，都可能
                    // 让单次探测出错，而隧道本身好好的。连续两次才动手，并且隧道自己
                    // 报 IsAlive=false 时立刻动手——那是确凿的。
                    strikes++;
                    if (tunnel is not null && tunnel.IsAlive && strikes < 2)
                    {
                        Log.Warn($"保温探活失败（第 {strikes} 次，隧道仍报活着，先不动）：{error.Message}");
                        continue;
                    }

                    // 只摘掉"我探的那一条"。探活要好几秒，这期间后台升级可能已经换上了
                    // P2P——直接置空会把刚换上的 P2P 一起扔掉（还漏掉它的 QUIC 连接）。
                    lock (_swapGate)
                    {
                        if (!ReferenceEquals(_tunnel, tunnel))
                        {
                            Log.Info("保温探活期间线路已被换掉，不再重建");
                            strikes = 0;
                            continue;
                        }
                        _tunnel = null;
                    }
                    Log.Warn($"保温发现{what}已失效：{error.Message}");
                    if (tunnel is not null)
                    {
                        try { await tunnel.DisposeAsync().ConfigureAwait(false); } catch { }
                    }
                    // 重建期间别让后台升级插进来接管，两边会互相覆盖 _tunnel。
                    Interlocked.Increment(ref _rebuilding);
                    try
                    {
                        var replacement = await RecoverAsync(_progress, ct).ConfigureAwait(false);
                        if (!ReferenceEquals(replacement.Route, current.Route))
                            RouteChanged?.Invoke(replacement);
                    }
                    // 判据是"我们自己要退出"，不是异常类型：重建里的超时抛的也是取消异常，
                    // 用类型过滤会让它穿出去，把保温循环整个打死。
                    catch (Exception retry) when (!Cancellation.IsShutdown(retry, ct))
                    {
                        Log.Warn("保温重建线路失败：" + retry.Message);
                        // 重建不成就别再装作有线路。原先 Established 留着旧值，游戏来连时照着
                        // 一条死线路转发，玩家只看到"连接中断"；现在退回"未连上"，交给后台重连：
                        // 它按退避接着试，游戏来连时会被叫醒，连不上也会替服务器说明原因。
                        //
                        // 只在确实是我们把它置空时才通知、才转入重连：重建的这几秒里后台升级可能
                        // 已经把 P2P 接上了，那时线路是好的，再报"连不上"会让界面一直挂着"重连中"。
                        bool lost;
                        lock (_swapGate)
                        {
                            lost = ReferenceEquals(Established, current);
                            if (lost) Established = null;
                        }
                        if (lost)
                        {
                            RouteUnavailable?.Invoke(retry);
                            ReconnectInBackground(retry);
                        }
                    }
                    finally
                    {
                        Interlocked.Decrement(ref _rebuilding);
                    }
                }
            }
        }
        catch (OperationCanceledException) { }
    }

    /// <summary>
    /// 当前线路死了之后重建。
    ///
    /// 原先这里只会退到裸直连/中转（<see cref="EstablishAsync"/>），P2P 一掉就永久放弃，
    /// 这局剩下的时间全走中转；而没有任何 TCP 线路的玩家（只能靠打洞的那批）连中转
    /// 都没有，直接就断了。现在：能走隧道先走隧道，落到付费线路就在后台重新打洞；
    /// 一条 TCP 线路都没有就直接重新打洞。
    /// </summary>
    private async Task<RouteEstablishment> RecoverAsync(IProgress<string>? progress, CancellationToken ct)
    {
        if (_routes.Count == 0)
        {
            if (_p2p is null) throw new InvalidOperationException("没有任何可用线路");
            return await TryEstablishP2PAsync(
                       _p2p.ControlPlaneBaseUrl, _p2p.ServerId, _p2p.Token, _p2p.ReflectorHost, progress, ct,
                       acceptSlow: true)
                       .ConfigureAwait(false)
                   ?? throw new InvalidOperationException("没有 TCP 线路，重新打洞也没成功");
        }

        RouteEstablishment? recovered = null;
        if (!string.IsNullOrWhiteSpace(_tunnelToken))
        {
            try
            {
                recovered = await EstablishTunnelAsync(_tunnelToken!, _tunnelPort, progress, ct)
                    .ConfigureAwait(false);
            }
            catch (Exception error) when (error is not ServerNotRespondingException
                                          && !Cancellation.IsShutdown(error, ct))
            {
                Log.Warn("重建隧道失败，退回直连/中转：" + error.Message);
            }
        }
        if (recovered is null)
        {
            try
            {
                recovered = await EstablishAsync(progress, ct).ConfigureAwait(false);
            }
            // 直连/中转也都进不了服（比如中转后面的 frpc 挂了），打洞是剩下的唯一办法。
            // 不这么做的话，P2P 一掉、中转又不行，这局剩下的时间每 25 秒重建一次、每次都失败。
            catch (Exception error) when (_p2p is not null
                                          && (error is not OperationCanceledException
                                              || !ct.IsCancellationRequested))
            {
                Log.Warn("直连/中转都没能重建，改为重新打洞：" + error.Message);
                return await TryEstablishP2PAsync(
                           _p2p.ControlPlaneBaseUrl, _p2p.ServerId, _p2p.Token, _p2p.ReflectorHost,
                           progress, ct, acceptSlow: true).ConfigureAwait(false)
                       ?? throw new InvalidOperationException("直连、中转和打洞都没能重建线路");
            }
        }
        StartUpgradeIfPaid(recovered);
        return recovered;
    }

    /// <summary>
    /// 重连的退避。游戏加载模组要好几分钟，前几次密一点，服务器重启这种一两分钟就好的
    /// 情况能赶在读条走完之前连上；之后稀下来，玩家在玩单机时也不会一直敲服务器。
    /// </summary>
    private static readonly TimeSpan[] ReconnectBackoff =
    [
        TimeSpan.FromSeconds(5), TimeSpan.FromSeconds(10), TimeSpan.FromSeconds(20),
        TimeSpan.FromSeconds(30), TimeSpan.FromSeconds(60),
    ];

    /// <summary>两轮重连之间至少隔这么久，游戏连着来叫也一样：每轮都要问控制面，可能还要打洞。</summary>
    private static readonly TimeSpan ReconnectMinGap = TimeSpan.FromSeconds(2);

    private Task? _reconnect;
    private volatile Exception? _lastFailure;

    /// <summary>游戏来连本地代理时叫醒重连，不等退避。</summary>
    private readonly SemaphoreSlim _reconnectKick = new(0, 1);

    /// <summary>每一轮重连结束（不管成没成）都换一个新的，等结果的游戏连接各自拿当时那个。</summary>
    private TaskCompletionSource _reconnectRound = NewRound();

    /// <summary>开始过、结束过几轮重连。游戏连接要等的是"它叫醒之后开始的那一轮"。</summary>
    private long _roundsStarted;
    private long _roundsFinished;

    private static TaskCompletionSource NewRound() => new(TaskCreationOptions.RunContinuationsAsynchronously);

    /// <summary>
    /// 在后台把线路连起来，并且一直连下去，直到代理关掉：第一轮问控制面、选路、完整建立；
    /// 没连上就按退避一轮轮重试，连上之后掉了线（保温重建失败）也回到这里接着连。
    ///
    /// 启动游戏不再等它。原先是"先连上服务器、再启动游戏"，连不上就抛异常，游戏压根不启动，
    /// 玩家连单机都玩不了——实测撞上的是服务器重启那一分钟。现在游戏要的只是本机代理的
    /// 端口，模组加载那几分钟里后台把线路连好：连上了，读完条照样直接进服；没连上，游戏来连时
    /// 由代理告诉玩家原因，点返回就是主菜单。
    /// </summary>
    /// <param name="plan">
    /// 取凭据和线路候选。要问控制面，所以也放在后台；之后每轮重连都会重新取一次——服务器的
    /// IPv6 是临时地址会轮换，启动那一刻控制面没问到凭据的话，后面几轮也还能补上。
    /// </param>
    /// <param name="progress">连接进度，启动器的进度条接着它。</param>
    public void ConnectInBackground(Func<CancellationToken, Task<ConnectPlan>> plan, IProgress<string>? progress)
    {
        lock (_swapGate)
        {
            if (_disposed || _reconnect is { IsCompleted: false }) return;
            _plan = plan;
            _progress = progress;
            _standIn = true;
            _reconnect = ConnectLoopAsync(firstRound: true, _lifetime.Token);
        }
    }

    /// <summary>线路掉了、保温也没重建成功时回到后台重连。已经在连就什么都不做。</summary>
    /// <param name="why">刚才那次失败。游戏来连时，本地代理拿它告诉玩家为什么进不去。</param>
    public void ReconnectInBackground(Exception? why = null)
    {
        if (why is not null) _lastFailure = why;
        lock (_swapGate)
        {
            if (_disposed || _reconnect is { IsCompleted: false }) return;
            _reconnect = ConnectLoopAsync(firstRound: false, _lifetime.Token);
        }
    }

    private Func<CancellationToken, Task<ConnectPlan>>? _plan;
    private IProgress<string>? _progress;

    /// <summary>
    /// 没连上时由代理替服务器回话。只有游戏用的代理（走 <see cref="ConnectInBackground"/>）开：
    /// 诊断工具要的是真实结果，替身回一句"1.21.1"会被它当成"经代理查询成功"。
    /// </summary>
    private volatile bool _standIn;

    private void KickReconnect()
    {
        try { _reconnectKick.Release(); }
        catch (SemaphoreFullException) { } // 已经叫过了，还没被取走
    }

    private async Task ConnectLoopAsync(bool firstRound, CancellationToken ct)
    {
        // 一律到线程池上跑，不在调用方里同步执行到第一个 await：调用方拿着 _swapGate 的锁，
        // 而保温调用时它自己的"正在重建"计数也还没退。
        await Task.Yield();
        try
        {
            for (var round = 1; ; round++)
            {
                var initial = firstRound && round == 1;
                if (!initial)
                {
                    // 首轮立刻开始；之后按退避等，游戏来连时（玩家点加入、多人列表刷新）提前叫醒
                    var retry = firstRound ? round - 1 : round;
                    var backoff = ReconnectBackoff[Math.Min(retry - 1, ReconnectBackoff.Length - 1)];
                    if (_lastFailure is { } last)
                        _progress?.Report($"{DescribeUnavailable(last)}，{backoff.TotalSeconds:0} 秒后再试");
                    await _reconnectKick.WaitAsync(backoff, ct).ConfigureAwait(false);
                }
                // 后台升级可能抢先把 P2P 接上了
                if (Established is not null) return;

                Interlocked.Increment(ref _rebuilding);
                Interlocked.Increment(ref _roundsStarted);
                try
                {
                    RouteEstablishment established;
                    if (initial)
                    {
                        established = await FirstConnectAsync(ct).ConfigureAwait(false);
                    }
                    else
                    {
                        _progress?.Report("正在重新连接服务器");
                        await RefreshAsync(ct).ConfigureAwait(false);
                        established = await RecoverAsync(_progress, ct).ConfigureAwait(false);
                    }
                    _lastFailure = null;
                    Log.Info(initial ? "后台连接已建立" : $"后台重连成功（第 {round} 轮）");
                    RouteChanged?.Invoke(established);
                    return;
                }
                catch (Exception error) when (!Cancellation.IsShutdown(error, ct))
                {
                    _lastFailure = error;
                    Log.Warn($"{DescribeUnavailable(error)}（第 {round} 轮）：{error.Message}");
                    if (initial) RouteUnavailable?.Invoke(error);
                }
                finally
                {
                    Interlocked.Decrement(ref _rebuilding);
                    Interlocked.Increment(ref _roundsFinished);
                    NextRound();
                }
                await Task.Delay(ReconnectMinGap, ct).ConfigureAwait(false);
            }
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested) { }
        finally
        {
            // 代理关了也要放行还在等的游戏连接
            NextRound();
        }
    }

    private void NextRound() =>
        Interlocked.Exchange(ref _reconnectRound, NewRound()).TrySetResult();

    /// <summary>首轮：取凭据和候选、选路，然后按优先级完整建立一次（见 <see cref="EstablishBestAsync"/>）。</summary>
    private async Task<RouteEstablishment> FirstConnectAsync(CancellationToken ct)
    {
        var plan = await _plan!(ct).ConfigureAwait(false);
        ApplyPlan(plan);
        try
        {
            var selection = await SelectRouteAsync(plan.Servers, _progress, ct).ConfigureAwait(false);
            Selection = selection;
            _routes = selection.Reachable;
        }
        // 有凭据就还能打洞：打洞不依赖任何 TCP 线路。没有凭据时这就是这一轮失败的原因。
        catch (InvalidOperationException error) when (!string.IsNullOrWhiteSpace(plan.TunnelToken))
        {
            Log.Warn($"{error.Message}——还有打洞可以试");
        }
        return await EstablishBestAsync(plan.TunnelToken, plan.ControlPlaneBaseUrl, plan.ServerId,
                plan.ReflectorHost, plan.TunnelPort, _progress, ct, upgradeInBackground: true)
            .ConfigureAwait(false);
    }

    /// <summary>
    /// 重连前把凭据、候选和可达线路都重新取一遍。线路全挂时不能一直抱着起代理那一刻的表：
    /// 中转闪断回来了、服务器换了 IPv6，旧表里都没有。
    /// </summary>
    private async Task RefreshAsync(CancellationToken ct)
    {
        if (_plan is { } plan)
        {
            try { ApplyPlan(await plan(ct).ConfigureAwait(false)); }
            catch (Exception error) when (!Cancellation.IsShutdown(error, ct))
            {
                Log.Warn("重新取线路候选失败，沿用上次的：" + error.Message);
            }
        }
        try
        {
            var selection = await SelectRouteAsync(_servers, null, ct).ConfigureAwait(false);
            Selection = selection;
            _routes = selection.Reachable;
        }
        catch (InvalidOperationException)
        {
            // 现在一条都不通：清掉旧表，RecoverAsync 会直接去打洞
            Selection = RouteSelection.None;
            _routes = [];
        }
    }

    private void ApplyPlan(ConnectPlan plan)
    {
        _servers = plan.Servers;
        if (string.IsNullOrWhiteSpace(plan.TunnelToken)) return;
        _tunnelToken = plan.TunnelToken;
        _tunnelPort = plan.TunnelPort;
        _p2p = new P2PParams(plan.ControlPlaneBaseUrl, plan.ServerId, plan.TunnelToken!, plan.ReflectorHost);
    }

    /// <summary>
    /// 游戏来连时还没连上：叫醒重连，等这一轮的结果。连上了返回 true，照常转发；
    /// 没连上就由调用方替服务器回话。
    /// </summary>
    private async Task<bool> AwaitPendingRouteAsync(CancellationToken ct)
    {
        // 叫醒时可能正赶上一轮已经在跑，它是在玩家点加入之前开始的，服务器也许正好在这之间
        // 起来了——它的失败说明不了现在。所以要等的是叫醒之后才开始的那一轮。
        var wanted = Interlocked.Read(ref _roundsStarted) + 1;
        KickReconnect();
        var deadline = Task.Delay(PendingRouteWait, ct);
        while (true)
        {
            // 先拿信号再看条件，否则条件看完、开始等之前那一轮恰好结束，这次通知就丢了。
            var pulse = Volatile.Read(ref _reconnectRound).Task;
            if (Established is not null) return true;
            if (Interlocked.Read(ref _roundsFinished) >= wanted) return false;
            if (await Task.WhenAny(pulse, deadline).ConfigureAwait(false) == deadline)
                return Established is not null;
        }
    }

    private static string Describe(RouteKind kind) => kind switch
    {
        RouteKind.LanDirect => "局域网直连",
        RouteKind.Ipv6Direct => "IPv6 直连",
        RouteKind.Ipv4Direct => "IPv4 直连",
        RouteKind.PortMapped => "路由器映射",
        RouteKind.UdpTunnel => "UDP/QUIC 隧道",
        RouteKind.TcpTunnel => "TCP 隧道",
        RouteKind.Relay => "中继线路",
        _ => "自动线路",
    };

    /// <param name="allowNoRoute">
    /// 一条 TCP 线路都不通时也把代理起起来，而不是抛"所有游戏线路均不可达"。
    ///
    /// 调用方手里有隧道凭据时应当传 true：打洞不依赖任何 TCP 线路，中转挂了、玩家又
    /// 没有 IPv6 时它恰恰是唯一能通的路。原先选路一抛，打洞连尝试的机会都没有。
    /// </param>
    /// <summary>
    /// 只把本地代理起起来，不碰任何网络：游戏要的只是一个本机端口。选路、问控制面、建隧道、
    /// 打洞都交给 <see cref="ConnectInBackground"/>，和游戏加载并行。
    /// </summary>
    public static MinecraftRouteProxy Listen(IEnumerable<ServerEntry> servers)
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start(32);
        Log.Info($"MC 本地代理监听 127.0.0.1:{((IPEndPoint)listener.LocalEndpoint).Port}，线路在后台连");
        return new MinecraftRouteProxy(listener, RouteSelection.None, servers.ToList());
    }

    public static async Task<MinecraftRouteProxy> StartAsync(
        IEnumerable<ServerEntry> servers,
        IProgress<string>? progress,
        CancellationToken ct,
        bool allowNoRoute = false)
    {
        var serverList = servers.ToList();
        RouteSelection selection;
        try
        {
            selection = await SelectRouteAsync(serverList, progress, ct).ConfigureAwait(false);
        }
        catch (InvalidOperationException error) when (allowNoRoute)
        {
            Log.Warn($"{error.Message}——还有打洞可以试，代理照常起来");
            selection = RouteSelection.None;
        }
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start(32);
        Log.Info($"MC 本地代理监听 127.0.0.1:{((IPEndPoint)listener.LocalEndpoint).Port}，" +
                 (selection.Selected is { } best
                     ? $"首选 {best.Candidate.Kind} {best.Endpoint} {best.Latency.TotalMilliseconds:0}ms"
                     : "没有可达的 TCP 线路，只能靠打洞"));
        return new MinecraftRouteProxy(listener, selection, serverList);
    }

    public static async Task<RouteSelection> SelectRouteAsync(
        IEnumerable<ServerEntry> servers,
        IProgress<string>? progress,
        CancellationToken ct)
    {
        var candidates = servers
            .Where(server => server.Primary)
            .DefaultIfEmpty(servers.FirstOrDefault()!)
            .Where(server => server is not null)
            .SelectMany(ExpandCandidates)
            .Where(candidate => candidate.Transport.Equals("tcp", StringComparison.OrdinalIgnoreCase))
            .ToList();

        if (candidates.Count == 0)
            throw new InvalidOperationException("服务器没有可用的 TCP 线路候选");

        progress?.Report($"并行解析 {candidates.Count} 条候选线路");
        var expanded = await Task.WhenAll(candidates.Select(candidate => ResolveAsync(candidate, ct)))
            .ConfigureAwait(false);
        var endpoints = expanded.SelectMany(x => x).DistinctBy(x =>
            $"{x.Candidate.Id}|{x.Candidate.Kind}|{x.Endpoint.Address}|{x.Endpoint.Port}").ToList();
        if (endpoints.Count == 0)
            throw new InvalidOperationException("候选线路的域名均无法解析");

        progress?.Report($"并行测试 {endpoints.Count} 个网络入口");
        var results = await Task.WhenAll(endpoints.Select(x => ProbeAsync(x.Candidate, x.Endpoint, ct)))
            .ConfigureAwait(false);
        var reachable = results.Where(x => x.Reachable)
            .OrderBy(x => EffectivePriority(x.Candidate, x.Endpoint.Address))
            .ThenBy(x => x.Latency)
            .ToList();

        foreach (var result in results)
        {
            var status = result.Reachable ? $"{result.Latency.TotalMilliseconds:0}ms" : result.Error ?? "不可达";
            Log.Info($"线路探测 {result.Candidate.Kind} {result.Endpoint}：{status}");
        }

        if (reachable.Count == 0)
            throw new InvalidOperationException("所有游戏线路均不可达，请检查网络后重试");
        return new RouteSelection(reachable[0], reachable);
    }

    private static IEnumerable<RouteCandidate> ExpandCandidates(ServerEntry server)
    {
        if (server.Routes.Count > 0)
            return server.Routes.Where(x => !string.IsNullOrWhiteSpace(x.Host) && x.Port is > 0 and <= 65535);
        if (string.IsNullOrWhiteSpace(server.Host) || server.Port is <= 0 or > 65535)
            return [];
        return
        [
            new RouteCandidate
            {
                Id = "legacy-primary",
                Label = server.Name,
                Kind = RouteKind.Auto,
                Transport = "tcp",
                Host = server.Host,
                Port = server.Port,
            },
        ];
    }

    private static async Task<List<(RouteCandidate Candidate, IPEndPoint Endpoint)>> ResolveAsync(
        RouteCandidate candidate, CancellationToken ct)
    {
        try
        {
            IPAddress[] addresses;
            if (IPAddress.TryParse(candidate.Host, out var literal))
                addresses = [literal];
            else
                addresses = await Dns.GetHostAddressesAsync(candidate.Host, ct).ConfigureAwait(false);

            return addresses
                .Where(address => address.AddressFamily is AddressFamily.InterNetwork or AddressFamily.InterNetworkV6)
                .Select(address => (candidate, new IPEndPoint(address, candidate.Port)))
                .ToList();
        }
        catch (Exception ex) when (ex is SocketException or ArgumentException)
        {
            Log.Warn($"线路域名解析失败 {candidate.Host}：{ex.Message}");
            return [];
        }
    }

    private static async Task<RouteProbeResult> ProbeAsync(
        RouteCandidate candidate, IPEndPoint endpoint, CancellationToken ct)
    {
        var timeoutMs = candidate.ProbeTimeoutMs > 0
            ? Math.Clamp(candidate.ProbeTimeoutMs, 250, 10_000)
            : 1_800;
        // 私网地址的探测不必等那么久。
        //
        // 控制面给局域网候选的是 3 秒，而服务端公布的是它自己的内网地址——对不在同一个
        // 局域网的玩家（也就是几乎所有人）这几条必然超时，而选路要等所有探测结束，于是
        // 每个人点开始游戏都白等整整 3 秒（实测两台远程测试机每次都是这 3 秒）。同一个
        // 局域网里 TCP 握手是毫秒级的，800ms 还没回就不是一条能用的局域网线路。
        if (Classify(endpoint.Address) == RouteKind.LanDirect && !IPAddress.IsLoopback(endpoint.Address))
            timeoutMs = Math.Min(timeoutMs, LanProbeTimeoutMs);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(timeoutMs);
        using var client = new TcpClient(endpoint.AddressFamily);
        var watch = Stopwatch.StartNew();
        try
        {
            await client.ConnectAsync(endpoint, timeout.Token).ConfigureAwait(false);
            watch.Stop();
            return new RouteProbeResult(candidate, endpoint, true, watch.Elapsed, null);
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            return new RouteProbeResult(candidate, endpoint, false, watch.Elapsed, $"超时 {timeoutMs}ms");
        }
        catch (Exception ex) when (ex is SocketException or IOException)
        {
            return new RouteProbeResult(candidate, endpoint, false, watch.Elapsed, ex.Message);
        }
    }

    /// <summary>私网地址探测的上限，理由见 <see cref="ProbeAsync"/>。</summary>
    private const int LanProbeTimeoutMs = 800;

    private static int EffectivePriority(RouteCandidate candidate, IPAddress address)
    {
        var kind = candidate.Kind == RouteKind.Auto ? Classify(address) : candidate.Kind;
        return (int)kind * 10_000 + Math.Clamp(candidate.Priority, -9_999, 9_999);
    }

    public static RouteKind Classify(IPAddress address)
    {
        if (IPAddress.IsLoopback(address)) return RouteKind.LanDirect;
        if (address.AddressFamily == AddressFamily.InterNetworkV6)
        {
            var bytes = address.GetAddressBytes();
            if (address.IsIPv6LinkLocal || (bytes[0] & 0xfe) == 0xfc) return RouteKind.LanDirect;
            return RouteKind.Ipv6Direct;
        }

        var b = address.GetAddressBytes();
        var local = b[0] == 10 || b[0] == 127 ||
                    b[0] == 169 && b[1] == 254 ||
                    b[0] == 172 && b[1] is >= 16 and <= 31 ||
                    b[0] == 192 && b[1] == 168;
        return local ? RouteKind.LanDirect : RouteKind.Ipv4Direct;
    }

    private async Task AcceptLoopAsync()
    {
        try
        {
            while (!_lifetime.IsCancellationRequested)
            {
                var local = await _listener.AcceptTcpClientAsync(_lifetime.Token).ConfigureAwait(false);
                local.NoDelay = true;
                var id = Interlocked.Increment(ref _nextConnectionId);
                var task = ForwardAsync(local, _lifetime.Token);
                _connections[id] = task;
                _ = task.ContinueWith(completed => _connections.TryRemove(id, out var _),
                    CancellationToken.None, TaskContinuationOptions.ExecuteSynchronously, TaskScheduler.Default);
            }
        }
        catch (OperationCanceledException) when (_lifetime.IsCancellationRequested) { }
        catch (ObjectDisposedException) when (_lifetime.IsCancellationRequested) { }
        catch (Exception ex) { Log.Error("MC 本地代理监听失败", ex); }
    }

    private async Task ForwardAsync(TcpClient local, CancellationToken ct)
    {
        using (local)
        {
            if (BeforeGameConnects is { } prepare)
            {
                try { await prepare(ct).ConfigureAwait(false); }
                catch (Exception ex) { Log.Warn($"进服票据没换到，先照常连过去（服务端会说明原因）：{ex.Message}"); }
            }
            // 还没连上、后台在重连（启动时线路全不通，或者掉线后重建失败）：叫醒重连，等这一轮
            // 的结果。连上了就照常往下走；还是没连上，就替服务器回话——多人列表显示原因，
            // 点加入的直接看到进不去的理由，点返回就能去玩单机。
            //
            // 不能照旧去碰 _routes：线路 TCP 是通的（中转在），后面的游戏服不在，转过去只会
            // 让游戏报一句"连接中断"。诊断工具的代理不走这里（见 _standIn），照旧转发。
            if (_standIn && Established is null && _reconnect is { IsCompleted: false })
            {
                if (!await AwaitPendingRouteAsync(ct).ConfigureAwait(false))
                {
                    var why = DescribeUnavailable(_lastFailure);
                    Log.Info($"游戏来连时还没连上服务器（{why}），由本地代理回话");
                    try
                    {
                        await MinecraftPing.ServeStandInAsync(local.GetStream(),
                            $"{why}，启动器正在后台重连",
                            $"{why}。\n启动器正在后台自动重连，过一会儿再点加入就行；\n现在可以先返回去玩单机。",
                            TimeSpan.FromSeconds(10), ct).ConfigureAwait(false);
                    }
                    catch (Exception error) when (!Cancellation.IsShutdown(error, ct))
                    {
                        Log.Info("本地代理回话未完成：" + error.Message);
                    }
                    return;
                }
            }

            // 隧道已建立时，游戏的每条连接都从隧道里开一条数据流。这样公网那一段
            // 的选路、保活、换线全在两端工具之间完成，Minecraft 只看得到本机端口。
            var tunnel = _tunnel;
            if (tunnel is not null && tunnel.IsAlive)
            {
                Stream? stream = null;
                try
                {
                    stream = await tunnel.OpenStreamAsync(ct).ConfigureAwait(false);
                }
                catch (Exception ex) when (ex is not OperationCanceledException)
                {
                    Log.Warn($"隧道开流失败，回退到直连：{ex.Message}");
                }
                if (stream is not null)
                {
                    using (stream)
                    {
                        Log.Info($"MC 连接经{tunnel.Describe}");
                        // 必须带收尾：游戏断开后要把半关闭传过隧道，否则服务端那头要等
                        // Minecraft 自己 30 秒超时，这条转发也一直占着连接表，挡住后台
                        // 升级到 P2P。见 StreamBridge。
                        await StreamBridge.RunAsync(local.GetStream(), stream, StreamBridge.DefaultLinger, ct)
                            .ConfigureAwait(false);
                    }
                    return;
                }
            }

            TcpClient? remote = null;
            RouteProbeResult? connected = null;
            foreach (var route in _routes)
            {
                var attempt = new TcpClient(route.Endpoint.AddressFamily);
                try
                {
                    using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
                    timeout.CancelAfter(TimeSpan.FromSeconds(5));
                    await attempt.ConnectAsync(route.Endpoint, timeout.Token).ConfigureAwait(false);
                    attempt.NoDelay = true;
                    remote = attempt;
                    connected = route;
                    break;
                }
                catch (Exception ex) when (ex is SocketException or IOException or OperationCanceledException)
                {
                    attempt.Dispose();
                    Log.Warn($"代理连接 {route.Endpoint} 失败，切下一条：{ex.Message}");
                }
            }

            if (remote is null || connected is null)
            {
                Log.Warn("MC 发起连接时所有已探测线路均失效");
                return;
            }

            using (remote)
            {
                Log.Info($"MC 连接使用 {connected.Candidate.Kind} {connected.Endpoint}");
                var upstream = PumpAsync(local, remote, ct);
                var downstream = PumpAsync(remote, local, ct);
                await Task.WhenAll(upstream, downstream).ConfigureAwait(false);
            }
        }
    }

    private static async Task PumpAsync(TcpClient source, TcpClient destination, CancellationToken ct)
    {
        try
        {
            await source.GetStream().CopyToAsync(destination.GetStream(), 64 * 1024, ct).ConfigureAwait(false);
            try { destination.Client.Shutdown(SocketShutdown.Send); } catch { }
        }
        catch (Exception ex) when (ex is IOException or SocketException or OperationCanceledException) { }
    }

    public async ValueTask DisposeAsync()
    {
        lock (_swapGate) _disposed = true;
        _lifetime.Cancel();
        _listener.Stop();
        try { await _acceptLoop.ConfigureAwait(false); } catch { }
        // 后台升级收到取消后会自己把手上建好的隧道收掉（见 UpgradeToP2PAsync 的 finally），
        // 给它一点时间走完，别在它还拿着 QUIC 连接时就把 CTS 释放了。
        if (_upgrade is { } upgrade)
        {
            try { await upgrade.WaitAsync(TimeSpan.FromSeconds(2)).ConfigureAwait(false); } catch { }
        }
        // 后台重连同理：它可能正拿着一条刚建好、还没装上的隧道。
        if (_reconnect is { } reconnect)
        {
            try { await reconnect.WaitAsync(TimeSpan.FromSeconds(2)).ConfigureAwait(false); } catch { }
        }
        var connections = _connections.Values.ToArray();
        if (connections.Length > 0)
        {
            try { await Task.WhenAll(connections).WaitAsync(TimeSpan.FromSeconds(2)).ConfigureAwait(false); } catch { }
        }
        IMuxiTunnel? last;
        lock (_swapGate) { last = _tunnel; _tunnel = null; }
        if (last is not null)
        {
            try { await last.DisposeAsync().ConfigureAwait(false); } catch { }
        }
        _lifetime.Dispose();
        Log.Info("MC 本地代理已停止");
    }
}
