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
    private Task? _keepWarm;

    /// <summary>转发时的候选顺序；验证通过的线路会被提到最前面。</summary>
    private volatile IReadOnlyList<RouteProbeResult> _routes;

    private MinecraftRouteProxy(TcpListener listener, RouteSelection selection)
    {
        _listener = listener;
        Selection = selection;
        _routes = selection.Reachable;
        _acceptLoop = AcceptLoopAsync();
    }

    /// <summary>线路掉了又换到另一条时触发，供界面提示玩家。</summary>
    public event Action<RouteEstablishment>? RouteChanged;

    /// <summary>
    /// 已建立的端侧隧道。非空时游戏的每条连接都从隧道里开数据流，而不是自己
    /// 去连远端——Minecraft 全程只看得到 127.0.0.1，公网那一段由两端的工具负责。
    /// </summary>
    private IMuxiTunnel? _tunnel;

    /// <summary>已经验证通过的线路；还没调用 <see cref="EstablishAsync"/> 时为 null。</summary>
    public RouteEstablishment? Established { get; private set; }

    public RouteSelection Selection { get; }
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
            catch (Exception error) when (error is not OperationCanceledException
                                          || !ct.IsCancellationRequested)
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

                var built = await BuildP2PAsync(p2p, null, ct).ConfigureAwait(false);
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
                    while ((!_connections.IsEmpty || _recovering) && ready.Tunnel.IsAlive)
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
                try
                {
                    using var probe = await tunnel.OpenStreamAsync(ct).ConfigureAwait(false);
                    status = await MinecraftPing
                        .QueryAsync(probe, "127.0.0.1", 25565, StatusTimeout, ct).ConfigureAwait(false);
                }
                catch
                {
                    await tunnel.DisposeAsync().ConfigureAwait(false);
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
            catch (Exception error) when (error is not OperationCanceledException
                                          || !ct.IsCancellationRequested)
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

    /// <summary>保温正在重建线路。这期间后台升级先不接管，免得两边互相覆盖。</summary>
    private volatile bool _recovering;

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
                        status = await MinecraftPing
                            .QueryAsync(probe, "127.0.0.1", 25565, StatusTimeout, token2).ConfigureAwait(false);
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
            Log.Warn($"P2P 直连未建立：{error.Message}");
            progress?.Report("P2P 直连不可用，改用其他线路");
            return null;
        }
    }

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
                    _recovering = true;
                    try
                    {
                        var replacement = await RecoverAsync(ct).ConfigureAwait(false);
                        if (!ReferenceEquals(replacement.Route, current.Route))
                            RouteChanged?.Invoke(replacement);
                    }
                    catch (Exception retry) when (retry is not OperationCanceledException)
                    {
                        Log.Warn("保温重建线路失败：" + retry.Message);
                    }
                    finally
                    {
                        _recovering = false;
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
    private async Task<RouteEstablishment> RecoverAsync(CancellationToken ct)
    {
        if (_routes.Count == 0)
        {
            if (_p2p is null) throw new InvalidOperationException("没有任何可用线路");
            return await TryEstablishP2PAsync(
                       _p2p.ControlPlaneBaseUrl, _p2p.ServerId, _p2p.Token, _p2p.ReflectorHost, null, ct,
                       acceptSlow: true)
                       .ConfigureAwait(false)
                   ?? throw new InvalidOperationException("没有 TCP 线路，重新打洞也没成功");
        }

        RouteEstablishment? recovered = null;
        if (!string.IsNullOrWhiteSpace(_tunnelToken))
        {
            try
            {
                recovered = await EstablishTunnelAsync(_tunnelToken!, _tunnelPort, null, ct)
                    .ConfigureAwait(false);
            }
            catch (Exception error) when (error is not OperationCanceledException
                                          || !ct.IsCancellationRequested)
            {
                Log.Warn("重建隧道失败，退回直连/中转：" + error.Message);
            }
        }
        if (recovered is null)
        {
            try
            {
                recovered = await EstablishAsync(null, ct).ConfigureAwait(false);
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
                           null, ct, acceptSlow: true).ConfigureAwait(false)
                       ?? throw new InvalidOperationException("直连、中转和打洞都没能重建线路");
            }
        }
        StartUpgradeIfPaid(recovered);
        return recovered;
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
    public static async Task<MinecraftRouteProxy> StartAsync(
        IEnumerable<ServerEntry> servers,
        IProgress<string>? progress,
        CancellationToken ct,
        bool allowNoRoute = false)
    {
        RouteSelection selection;
        try
        {
            selection = await SelectRouteAsync(servers, progress, ct).ConfigureAwait(false);
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
        return new MinecraftRouteProxy(listener, selection);
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
