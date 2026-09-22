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
    /// 用 <paramref name="socket"/> 本身去探测——必须是之后真正用来打洞的那个
    /// socket，换一个的话 NAT 映射就不是同一个，探到的地址对不上。
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

        var found = new List<IPEndPoint>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        var sent = 0;
        var replies = 0;
        var buffer = new byte[1500];
        // 每台反射器分别看到的映射。两台看到的不一样 = 换个目标就换映射 = 地址相关型。
        var perReflector = new Dictionary<string, HashSet<string>>(StringComparer.Ordinal);

        for (var round = 0; round < rounds; round++)
        {
            foreach (var port in reflectorPorts)
            {
                foreach (var address in addresses)
                {
                    var nonce = $"r{round}p{port}n{Random.Shared.Next():x}";
                    var payload = Magic.Concat(Encoding.ASCII.GetBytes(nonce)).ToArray();
                    try
                    {
                        await socket.SendToAsync(payload, SocketFlags.None,
                            new IPEndPoint(address, port), ct).ConfigureAwait(false);
                        sent++;
                    }
                    catch (SocketException) { continue; }

                    // 每发一个就等一小会儿，等不到就继续——丢包是常态，不值得重试
                    using var wait = CancellationTokenSource.CreateLinkedTokenSource(ct);
                    wait.CancelAfter(TimeSpan.FromMilliseconds(600));
                    try
                    {
                        var received = await socket.ReceiveFromAsync(
                            buffer, SocketFlags.None, new IPEndPoint(IPAddress.Any, 0), wait.Token)
                            .ConfigureAwait(false);
                        var endpoint = ParseReply(buffer.AsSpan(0, received.ReceivedBytes), nonce);
                        if (endpoint is null) continue;
                        replies++;
                        if (seen.Add(endpoint.ToString())) found.Add(endpoint);
                        if (!perReflector.TryGetValue(address.ToString(), out var mine))
                            perReflector[address.ToString()] = mine = new HashSet<string>(StringComparer.Ordinal);
                        mine.Add(endpoint.ToString());
                    }
                    catch (OperationCanceledException) when (!ct.IsCancellationRequested) { }
                    catch (SocketException) { }
                }
            }
        }
        // 只有问过两台以上、且各自都拿到了答复，这个判定才有意义。
        var views = perReflector.Values.Where(x => x.Count > 0).ToList();
        var addressDependent = views.Count > 1 && !views.All(x => x.SetEquals(views[0]));
        return new Result(found, replies, sent, addressDependent);
    }

    private static IPEndPoint? ParseReply(ReadOnlySpan<byte> data, string expectedNonce)
    {
        try
        {
            using var document = JsonDocument.Parse(data.ToArray());
            var root = document.RootElement;
            // nonce 必须对上：多个探测包在飞，回包顺序不保证
            if (!root.TryGetProperty("nonce", out var nonce) || nonce.GetString() != expectedNonce)
                return null;
            if (!root.TryGetProperty("ip", out var ip) || !root.TryGetProperty("port", out var port))
                return null;
            return IPAddress.TryParse(ip.GetString(), out var parsed)
                ? new IPEndPoint(parsed, port.GetInt32())
                : null;
        }
        catch (JsonException)
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
