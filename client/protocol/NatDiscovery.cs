using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;

namespace BatterMC.Protocol;

/// <summary>
/// 向反射器打听"我在公网上长什么样"，把**全部**出口候选收齐。
///
/// 为什么要收全部而不是一个：实测这条家宽线路在运营商级 NAT 后面，而且运营商
/// 有多个出口 IP、按流分配。同一个 socket 发往不同目标，大约一半概率会被换到
/// 另一个出口，IP 和端口全变。只上报一个地址的话，对端有一半概率打在空处。
///
/// 解法很简单：朝反射器的多个端口各问几次，把见到的所有 (IP, 端口) 都收下来。
/// 实测 3 个端口 × 6 轮，6/6 个 socket 都能把自己的两个出口身份探全，代价是
/// 每个 socket 十几个小包。
/// </summary>
public static class NatDiscovery
{
    private static readonly byte[] Magic = Encoding.ASCII.GetBytes("MUXI-REFLECT/1 ");

    public sealed record Result(
        IReadOnlyList<IPEndPoint> Candidates,
        int Replies,
        int Sent,
        bool AddressDependent);

    /// <summary>
    /// 补发间隔。只补给还没回话的目标，所以干净的线路上一轮就收工。
    /// </summary>
    private static readonly TimeSpan ResendInterval = TimeSpan.FromMilliseconds(100);

    /// <summary>
    /// 发完最后一轮之后，多久没有新目标回话就收工。有反射器根本不回（被墙、被
    /// 代理吞掉）时靠它止损，不必每次都等满 <see cref="MaxWait"/>。
    /// </summary>
    private static readonly TimeSpan QuietAfterLastRound = TimeSpan.FromMilliseconds(300);

    /// <summary>整段探测的硬上限。一个回包都没有时也就等这么久。</summary>
    private static readonly TimeSpan MaxWait = TimeSpan.FromMilliseconds(1500);

    /// <summary>
    /// 用 <paramref name="socket"/> 本身去探测——必须是之后真正用来打洞的那个
    /// socket，换一个的话 NAT 映射就不是同一个，探到的地址对不上。
    ///
    /// 所有目标一起发、一起收，而不是发一个等一个。
    ///
    /// 原先是 rounds × 端口 × 反射器逐个串行，每个都等回包、等不到就干等 600ms。
    /// 蜂窝上实测这一格要 2.2~5.5 秒（中位 3.3 秒）——比真正的打洞还慢好几倍，
    /// 而丢一个包就是整整 600ms 白等。现在一轮全发出去，谁没回话只补谁，所有目标
    /// 都回过话就立刻收工：干净线路上就是一个 RTT。
    ///
    /// 多轮并不能多探出出口身份：同一个 socket 发往同一个目标是同一个五元组，
    /// NAT 和双 WAN 路由器都按五元组记状态，第二轮拿到的必然是同一个映射。
    /// 能多探出东西的只有"换目标"（端口 × 反射器），所以轮数现在只用来抗丢包。
    /// </summary>
    public static async Task<Result> DiscoverAsync(
        Socket socket,
        string reflectorHosts,
        IReadOnlyList<int> reflectorPorts,
        int rounds,
        CancellationToken ct)
    {
        var addresses = new List<IPAddress>();
        foreach (var host in reflectorHosts.Split([',', ';', ' '],
                     StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
            foreach (var address in await ResolveAsync(host, ct).ConfigureAwait(false))
                if (!addresses.Contains(address)) addresses.Add(address);
        if (addresses.Count == 0) return new Result([], 0, 0, false);

        var targets = reflectorPorts
            .SelectMany(port => addresses.Select(address => new IPEndPoint(address, port)))
            .ToList();

        var gate = new object();
        // nonce → 它发往的目标。回包顺序不保证，靠 nonce 对回去。
        var outstanding = new Dictionary<string, IPEndPoint>(StringComparer.Ordinal);
        var answered = new HashSet<IPEndPoint>();
        // 每个目标看到的映射，最后按目标顺序摊平——不能按回包到达的顺序，否则同一组候选
        // 每次保鲜顺序都可能不同，agent 会误以为"候选变了"、每 15 秒多报一次。
        var byTarget = new Dictionary<IPEndPoint, List<IPEndPoint>>();
        // 每台反射器分别看到的映射。两台看到的不一样 = 换个目标就换映射 = 地址相关型。
        var perReflector = new Dictionary<string, HashSet<string>>(StringComparer.Ordinal);
        var sent = 0;
        var replies = 0;
        var clock = System.Diagnostics.Stopwatch.StartNew();
        var lastNews = TimeSpan.Zero;

        using var window = CancellationTokenSource.CreateLinkedTokenSource(ct);
        window.CancelAfter(MaxWait);

        async Task ReceiveAsync()
        {
            var buffer = new byte[1500];
            while (!window.IsCancellationRequested)
            {
                SocketReceiveFromResult received;
                try
                {
                    received = await socket.ReceiveFromAsync(
                            buffer, SocketFlags.None, new IPEndPoint(IPAddress.Any, 0), window.Token)
                        .ConfigureAwait(false);
                }
                catch (OperationCanceledException) { return; }
                // 反射器某个端口没开时 Windows 会把 ICMP 不可达变成下一次收包的
                // WSAECONNRESET。那只说明那一个目标不通，别的回包照收。
                catch (SocketException) { continue; }
                catch (ObjectDisposedException) { return; }

                if (ParseReply(buffer.AsSpan(0, received.ReceivedBytes)) is not { } reply) continue;
                lock (gate)
                {
                    if (!outstanding.TryGetValue(reply.Nonce, out var target)) continue;
                    replies++;
                    if (!byTarget.TryGetValue(target, out var views))
                        byTarget[target] = views = [];
                    if (!views.Contains(reply.Endpoint)) views.Add(reply.Endpoint);
                    var key = target.Address.ToString();
                    if (!perReflector.TryGetValue(key, out var mine))
                        perReflector[key] = mine = new HashSet<string>(StringComparer.Ordinal);
                    mine.Add(reply.Endpoint.ToString());
                    if (answered.Add(target)) lastNews = clock.Elapsed;
                    if (answered.Count == targets.Count) window.Cancel();
                }
            }
        }

        var receiver = ReceiveAsync();
        try
        {
            for (var round = 0; round < Math.Max(1, rounds) && !window.IsCancellationRequested; round++)
            {
                List<IPEndPoint> due;
                lock (gate) due = targets.Where(t => !answered.Contains(t)).ToList();
                foreach (var target in due)
                {
                    var nonce = $"r{round}p{target.Port}n{Random.Shared.Next():x}";
                    lock (gate) outstanding[nonce] = target;
                    var payload = Magic.Concat(Encoding.ASCII.GetBytes(nonce)).ToArray();
                    try
                    {
                        await socket.SendToAsync(payload, SocketFlags.None, target, window.Token)
                            .ConfigureAwait(false);
                        sent++;
                    }
                    catch (SocketException) { }
                    catch (OperationCanceledException) { break; }
                }
                try { await Task.Delay(ResendInterval, window.Token).ConfigureAwait(false); }
                catch (OperationCanceledException) { break; }
            }

            // 轮次发完了还有目标没回话：再给一小段安静期，等不到就不等了。
            var roundsDone = clock.Elapsed;
            while (!window.IsCancellationRequested)
            {
                TimeSpan quietSince;
                lock (gate) quietSince = lastNews > roundsDone ? lastNews : roundsDone;
                if (clock.Elapsed - quietSince >= QuietAfterLastRound) break;
                try { await Task.Delay(25, window.Token).ConfigureAwait(false); }
                catch (OperationCanceledException) { break; }
            }
        }
        finally
        {
            window.Cancel();
            await receiver.ConfigureAwait(false);
            // 补发过的目标，它更早那一发的回包可能在我们收工之后才到，会一直躺在这个
            // socket 里，等打洞的收包循环去读——那时它们会被当成"认不出的包"。能清的先清掉。
            Drain(socket);
        }
        ct.ThrowIfCancellationRequested();

        lock (gate)
        {
            var found = new List<IPEndPoint>();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var target in targets)
                if (byTarget.TryGetValue(target, out var seenAs))
                    foreach (var view in seenAs)
                        if (seen.Add(view.ToString())) found.Add(view);
            // 只有问过两台以上、且各自都拿到了答复，这个判定才有意义。
            var views = perReflector.Values.Where(x => x.Count > 0).ToList();
            var addressDependent = views.Count > 1 && !views.All(x => x.SetEquals(views[0]));
            return new Result(found, replies, sent, addressDependent);
        }
    }

    /// <summary>把 socket 里已经到了的包读掉扔掉，不等。</summary>
    private static void Drain(Socket socket)
    {
        var scratch = new byte[2048];
        EndPoint any = new IPEndPoint(IPAddress.Any, 0);
        try
        {
            for (var i = 0; i < 256 && socket.Available > 0; i++)
                socket.ReceiveFrom(scratch, ref any);
        }
        catch (Exception error) when (error is SocketException or ObjectDisposedException) { }
    }

    private static (string Nonce, IPEndPoint Endpoint)? ParseReply(ReadOnlySpan<byte> data)
    {
        try
        {
            using var document = JsonDocument.Parse(data.ToArray());
            var root = document.RootElement;
            if (!root.TryGetProperty("nonce", out var nonce) || nonce.GetString() is not { } text)
                return null;
            if (!root.TryGetProperty("ip", out var ip) || !root.TryGetProperty("port", out var port))
                return null;
            // 端口必须先验范围：这个 socket 的地址会报给别人，任何人都能往上面扔一个
            // port=70000 的 JSON，原先直接 new IPEndPoint 抛出来，整轮探测连同已经收到的
            // 候选一起作废。
            if (!port.TryGetInt32(out var number) || number is <= 0 or > 65535) return null;
            return IPAddress.TryParse(ip.GetString(), out var parsed)
                ? (text, new IPEndPoint(parsed, number))
                : null;
        }
        catch (Exception error) when (error is JsonException or InvalidOperationException
                                          or FormatException or ArgumentException)
        {
            return null;
        }
    }

    private static async Task<List<IPAddress>> ResolveAsync(string host, CancellationToken ct)
    {
        if (IPAddress.TryParse(host, out var literal)) return [literal];
        try
        {
            var resolved = await Dns.GetHostAddressesAsync(host, ct).ConfigureAwait(false);
            return resolved.Where(a => a.AddressFamily is AddressFamily.InterNetwork
                                               or AddressFamily.InterNetworkV6).ToList();
        }
        catch (SocketException)
        {
            return [];
        }
    }
}
