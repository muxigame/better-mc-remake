using System.Text.Json;
using BatterMC.Protocol;

namespace BatterMC.Core;

/// <summary>控制面返回的一次快照。</summary>
public sealed record ControlPlaneSnapshot(
    bool Online,
    string YourIp,
    int TunnelPort,
    double AgeSeconds,
    List<RouteCandidate> Candidates);

/// <summary>
/// 从控制面取当前可用入口。
///
/// 清单里不能再写死地址：Minecraft 那台机器的 IPv6 是临时地址会轮换，RA 前缀
/// 还可能因为运营商重拨整体更换。写死的话今天能连，过几小时全员失联。
///
/// 控制面拿不到时不抛死：退回调用方给的兜底候选（中转），玩家至少还能进服。
/// </summary>
public static class ControlPlaneClient
{
    public static async Task<ControlPlaneSnapshot?> FetchAsync(
        string baseUrl, string serverId, TimeSpan timeout, CancellationToken ct)
    {
        var url = $"{baseUrl.TrimEnd('/')}/api/v1/tunnel/endpoints?server_id={Uri.EscapeDataString(serverId)}";
        using var http = new HttpClient { Timeout = timeout };
        try
        {
            var json = await http.GetStringAsync(url, ct).ConfigureAwait(false);
            using var document = JsonDocument.Parse(json);
            var root = document.RootElement;

            var candidates = new List<RouteCandidate>();
            if (root.TryGetProperty("candidates", out var list) && list.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in list.EnumerateArray())
                {
                    var host = Text(item, "host");
                    if (string.IsNullOrWhiteSpace(host)) continue;
                    var port = item.TryGetProperty("port", out var p) && p.TryGetInt32(out var pv) ? pv : 25565;
                    if (port is <= 0 or > 65535) continue;
                    candidates.Add(new RouteCandidate
                    {
                        Id = Text(item, "id"),
                        Label = Text(item, "label"),
                        Kind = Enum.TryParse<RouteKind>(Text(item, "kind"), out var kind) ? kind : RouteKind.Auto,
                        Transport = Text(item, "transport") is { Length: > 0 } t ? t : "tcp",
                        Host = host,
                        Port = port,
                        Priority = item.TryGetProperty("priority", out var pr) && pr.TryGetInt32(out var prv) ? prv : 0,
                        ProbeTimeoutMs = item.TryGetProperty("probeTimeoutMs", out var pt)
                                         && pt.TryGetInt32(out var ptv) ? ptv : 0,
                    });
                }
            }

            var snapshot = new ControlPlaneSnapshot(
                Online: root.TryGetProperty("online", out var on) && on.ValueKind == JsonValueKind.True,
                YourIp: Text(root, "yourIp"),
                TunnelPort: root.TryGetProperty("tunnelPort", out var tp) && tp.TryGetInt32(out var tpv) ? tpv : 0,
                AgeSeconds: root.TryGetProperty("ageSeconds", out var age) && age.TryGetDouble(out var agv) ? agv : -1,
                Candidates: candidates);

            Log.Info($"控制面返回 {candidates.Count} 条候选，服务端在线={snapshot.Online}，" +
                     $"数据新鲜度 {snapshot.AgeSeconds:0.#}s，我的公网地址 {snapshot.YourIp}");
            return snapshot;
        }
        catch (Exception error) when (error is HttpRequestException or TaskCanceledException
                                          or JsonException or OperationCanceledException)
        {
            Log.Warn($"控制面不可达（将退回兜底入口）：{error.Message}");
            return null;
        }
    }

    /// <summary>
    /// 取隧道用的客户端 token。
    ///
    /// 不写进整合包、也不要求玩家配置：控制面按周期从 master 派生，玩家每次启动
    /// 现取。写死在包里的话换代就得重发版，而且包是公开下载的，等于密钥裸奔。
    /// </summary>
    public static async Task<string?> FetchClientTokenAsync(
        string baseUrl, string serverId, TimeSpan timeout, CancellationToken ct)
    {
        var url = $"{baseUrl.TrimEnd('/')}/api/v1/tunnel/client-token?server_id={Uri.EscapeDataString(serverId)}";
        using var http = new HttpClient { Timeout = timeout };
        try
        {
            using var document = JsonDocument.Parse(
                await http.GetStringAsync(url, ct).ConfigureAwait(false));
            var token = Text(document.RootElement, "token");
            if (string.IsNullOrWhiteSpace(token)) return null;
            Log.Info($"已取得隧道凭据（{token.Length} 位，指纹 {token[..8]}）");
            return token;
        }
        catch (Exception error) when (error is HttpRequestException or TaskCanceledException
                                          or JsonException or OperationCanceledException)
        {
            Log.Warn($"取隧道凭据失败，本次只能走直连/中转：{error.Message}");
            return null;
        }
    }

    private static string Text(JsonElement element, string name) =>
        element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? ""
            : "";
}
