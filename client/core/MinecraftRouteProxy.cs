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

public sealed record RouteSelection(RouteProbeResult Selected, IReadOnlyList<RouteProbeResult> Reachable);

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
    public async Task<RouteEstablishment> EstablishBestAsync(
        string? tunnelToken, string controlPlaneBaseUrl, string serverId,
        string reflectorHost, int tunnelPort, IProgress<string>? progress, CancellationToken ct)
    {
        if (!string.IsNullOrWhiteSpace(tunnelToken))
        {
            var p2p = await TryEstablishP2PAsync(
                controlPlaneBaseUrl, serverId, tunnelToken!, reflectorHost, progress, ct)
                .ConfigureAwait(false);
            if (p2p is not null) return p2p;

            try
            {
                return await EstablishTunnelAsync(tunnelToken!, tunnelPort, progress, ct)
                    .ConfigureAwait(false);
            }
            catch (Exception error) when (error is not OperationCanceledException
                                          || !ct.IsCancellationRequested)
            {
                // 常见情形：玩家只剩中转可用，而中转节点上没有开隧道端口。
                Log.Warn($"端侧隧道全部失败，退回直连/中转：{error.Message}");
                progress?.Report("隧道不可用，改用直连/中转");
            }
        }

        return await EstablishAsync(progress, ct).ConfigureAwait(false);
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

                // 隧道通了不代表对面那台的 Minecraft 也起来了。开一条数据流做一次
                // 真实状态查询，问得出版本号才算真的能进服——否则玩家会在读条走完
                // 之后才发现连不上，那时候已经晚了。
                MinecraftStatus status;
                try
                {
                    using var probe = await tunnel.OpenStreamAsync(ct).ConfigureAwait(false);
                    status = await MinecraftPing
                        .QueryAsync(probe, "127.0.0.1", 25565, ct).ConfigureAwait(false);
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
    public async Task<RouteEstablishment?> TryEstablishP2PAsync(
        string controlPlaneBaseUrl, string serverId, string token, string reflectorHost,
        IProgress<string>? progress, CancellationToken ct)
    {
        try
        {
            var tunnel = await P2PTunnel.EstablishAsync(
                controlPlaneBaseUrl, serverId, token, reflectorHost, progress, ct)
                .ConfigureAwait(false);

            MinecraftStatus status;
            try
            {
                using var probe = await tunnel.OpenStreamAsync(ct).ConfigureAwait(false);
                status = await MinecraftPing
                    .QueryAsync(probe, "127.0.0.1", 25565, ct).ConfigureAwait(false);
            }
            catch
            {
                await tunnel.DisposeAsync().ConfigureAwait(false);
                throw;
            }

            _tunnel = tunnel;
            _keepWarm ??= KeepWarmAsync(_lifetime.Token);
            // P2P 不对应清单里的任何一条候选，造一条合成记录好让上层统一处理
            var synthetic = new RouteProbeResult(
                new RouteCandidate { Id = "p2p", Label = "P2P 直连", Kind = RouteKind.UdpTunnel },
                new IPEndPoint(IPAddress.None, 0), true, status.Elapsed, null);
            var established = new RouteEstablishment(synthetic, RouteKind.UdpTunnel, status);
            Established = established;
            Log.Info($"P2P 直连已建立，服务端 {status.Version} {status.Online}/{status.Max}");
            progress?.Report($"P2P 直连成功，服务端 {status.Version}");
            return established;
        }
        catch (Exception error) when (error is not OperationCanceledException
                                      || !ct.IsCancellationRequested)
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
                        await MinecraftPing.QueryAsync(probe, "127.0.0.1", 25565, ct)
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

                    Log.Warn($"保温发现{what}已失效：{error.Message}");
                    if (tunnel is not null)
                    {
                        _tunnel = null;
                        try { await tunnel.DisposeAsync().ConfigureAwait(false); } catch { }
                    }
                    try
                    {
                        var replacement = await EstablishAsync(null, ct).ConfigureAwait(false);
                        if (!ReferenceEquals(replacement.Route, current.Route))
                            RouteChanged?.Invoke(replacement);
                    }
                    catch (Exception retry) when (retry is not OperationCanceledException)
                    {
                        Log.Warn("保温重建线路失败：" + retry.Message);
                    }
                }
            }
        }
        catch (OperationCanceledException) { }
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

    public static async Task<MinecraftRouteProxy> StartAsync(
        IEnumerable<ServerEntry> servers,
        IProgress<string>? progress,
        CancellationToken ct)
    {
        var selection = await SelectRouteAsync(servers, progress, ct).ConfigureAwait(false);
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start(32);
        Log.Info($"MC 本地代理监听 127.0.0.1:{((IPEndPoint)listener.LocalEndpoint).Port}，" +
                 $"首选 {selection.Selected.Candidate.Kind} {selection.Selected.Endpoint} " +
                 $"{selection.Selected.Latency.TotalMilliseconds:0}ms");
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
                        var up = PumpStreamAsync(local.GetStream(), stream, ct);
                        var down = PumpStreamAsync(stream, local.GetStream(), ct);
                        await Task.WhenAll(up, down).ConfigureAwait(false);
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

    /// <summary>流到流的对拷。隧道那一侧不一定是 TcpClient（QUIC 就不是）。</summary>
    private static async Task PumpStreamAsync(Stream source, Stream destination, CancellationToken ct)
    {
        try
        {
            await source.CopyToAsync(destination, 64 * 1024, ct).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is IOException or SocketException or OperationCanceledException
                                       or ObjectDisposedException) { }
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
        _lifetime.Cancel();
        _listener.Stop();
        try { await _acceptLoop.ConfigureAwait(false); } catch { }
        var connections = _connections.Values.ToArray();
        if (connections.Length > 0)
        {
            try { await Task.WhenAll(connections).WaitAsync(TimeSpan.FromSeconds(2)).ConfigureAwait(false); } catch { }
        }
        if (_tunnel is not null)
        {
            try { await _tunnel.DisposeAsync().ConfigureAwait(false); } catch { }
            _tunnel = null;
        }
        _lifetime.Dispose();
        Log.Info("MC 本地代理已停止");
    }
}
