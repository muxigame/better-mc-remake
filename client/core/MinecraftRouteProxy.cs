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

/// <summary>
/// 把 Minecraft 的固定 TCP 连接收进 127.0.0.1，再转发到并行探测得到的最优线路。
/// 游戏永远只看到本机端口，网络线路选择和故障切换都留在启动器侧。
/// </summary>
public sealed class MinecraftRouteProxy : IAsyncDisposable
{
    private readonly TcpListener _listener;
    private readonly CancellationTokenSource _lifetime = new();
    private readonly ConcurrentDictionary<long, Task> _connections = new();
    private readonly IReadOnlyList<RouteProbeResult> _routes;
    private readonly Task _acceptLoop;
    private long _nextConnectionId;

    private MinecraftRouteProxy(TcpListener listener, RouteSelection selection)
    {
        _listener = listener;
        Selection = selection;
        _routes = selection.Reachable;
        _acceptLoop = AcceptLoopAsync();
    }

    public RouteSelection Selection { get; }
    public int LocalPort => ((IPEndPoint)_listener.LocalEndpoint).Port;
    public string LocalAddress => $"127.0.0.1:{LocalPort}";

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
        _lifetime.Cancel();
        _listener.Stop();
        try { await _acceptLoop.ConfigureAwait(false); } catch { }
        var connections = _connections.Values.ToArray();
        if (connections.Length > 0)
        {
            try { await Task.WhenAll(connections).WaitAsync(TimeSpan.FromSeconds(2)).ConfigureAwait(false); } catch { }
        }
        _lifetime.Dispose();
        Log.Info("MC 本地代理已停止");
    }
}
