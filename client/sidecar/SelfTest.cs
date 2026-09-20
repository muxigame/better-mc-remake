using System.Text;
using System.Text.Json;
using System.Net;
using System.Net.Sockets;
using BatterMC.Core;
using BatterMC.Protocol;

namespace BatterMC.Launcher;

/// <summary>
/// 对几块「错了会很惨但不会当场报错」的纯逻辑做断言。
///
/// 这些东西的失败方式都很安静：离线 UUID 算错，玩家进服变成新号、背包全没；
/// TOML 改写出错，配置文件被写坏但游戏照常启动，等到用到那个功能才炸。
/// 所以宁可每次改动都跑一遍。
/// </summary>
internal static class SelfTest
{
    private static int _passed;
    private static int _failed;

    public static int Run()
    {
        _passed = _failed = 0;

        Console.WriteLine();
        Console.WriteLine("BatterMC5Remake 自检");
        Console.WriteLine();

        OfflineUuid();
        PropertiesOverlay();
        TomlOverlay();
        JsonOverlay();
        NbtRoundTrip();
        VersionRules();
        VersionCompare();
        ArgSplitting();
        NetworkRouteProxy().GetAwaiter().GetResult();

        Console.WriteLine();
        Console.WriteLine($"通过 {_passed}，失败 {_failed}");
        return _failed == 0 ? 0 : 1;
    }

    // ---------------------------------------------------------------- 断言

    private static void Check(string name, bool ok, string? detail = null)
    {
        if (ok) { _passed++; Console.WriteLine($"  [通过] {name}"); }
        else { _failed++; Console.WriteLine($"  [失败] {name}" + (detail is null ? "" : "\n         " + detail)); }
    }

    private static void Equal(string name, string expected, string actual)
        => Check(name, expected == actual, $"期望 <{expected}>\n         实得 <{actual}>");

    private static void Section(string title)
    {
        Console.WriteLine();
        Console.WriteLine("— " + title);
    }

    // ---------------------------------------------------------------- 用例

    /// <summary>
    /// 离线 UUID 必须和服务端算出来的完全一致。
    /// 下面三个值不是我推导的，是 BMC4 服务器 usercache.json 里真实记录过的，
    /// 等于拿 Minecraft 自己的实现当标准答案。
    /// </summary>
    private static void OfflineUuid()
    {
        Section("离线 UUID（对照服务器 usercache.json 的真实值）");

        Equal("_Roc_", "2e9c4a0c-4de5-3e5d-8521-865be124f623", OfflineAuth.OfflineUuid("_Roc_"));
        Equal("qs03", "d643908a-068a-34a4-b228-043e9e59f971", OfflineAuth.OfflineUuid("qs03"));
        Equal("XingYinChenMu", "3c53705f-b7fa-374e-b585-a0bb1a2c90d8", OfflineAuth.OfflineUuid("XingYinChenMu"));

        // 版本位必须是 3，变体位必须是 IETF
        var uuid = OfflineAuth.OfflineUuid("SomeoneElse");
        Check("版本位为 3", uuid[14] == '3', uuid);
        Check("变体位为 8/9/a/b", "89ab".Contains(uuid[19]), uuid);

        Check("用户名校验：拒绝 2 位", !OfflineAuth.IsValidUsername("ab"));
        Check("用户名校验：拒绝 17 位", !OfflineAuth.IsValidUsername(new string('a', 17)));
        Check("用户名校验：拒绝中文", !OfflineAuth.IsValidUsername("玩家"));
        Check("用户名校验：接受 _Roc_", OfflineAuth.IsValidUsername("_Roc_"));
    }

    private static Dictionary<string, JsonElement> Enforce(string json)
        => JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(json)!;

    private static void PropertiesOverlay()
    {
        Section("硬配置 · properties");

        // options.txt 用冒号
        var options = "version:3955\nrenderDistance:12\nlang:zh_cn\nmaxFps:260\n";
        var result = ConfigOverlay.ApplyProperties(
            options, Enforce("""{"renderDistance":"8","skipMultiplayerWarning":"true"}"""), out var changed);

        Check("冒号分隔：改掉点名的键", result.Contains("renderDistance:8"), result);
        Check("冒号分隔：保留没点名的键", result.Contains("lang:zh_cn") && result.Contains("maxFps:260"), result);
        Check("冒号分隔：追加缺失的键", result.Contains("skipMultiplayerWarning:true"), result);
        Check("冒号分隔：没有把等号混进来", !result.Contains("renderDistance="), result);
        Check("冒号分隔：改动计数", changed == 2, changed.ToString());

        // iris.properties 用等号，而且开头是注释
        var iris = "#This file stores configuration options\n#Sun Sep 20 02:26:21 CST 2026\n"
                 + "enableShaders=true\nmaxShadowRenderDistance=32\nshaderPack=Better MC - Ultra\n";
        var r2 = ConfigOverlay.ApplyProperties(
            iris, Enforce("""{"maxShadowRenderDistance":16}"""), out _);

        Check("等号分隔：改掉点名的键", r2.Contains("maxShadowRenderDistance=16"), r2);
        Check("等号分隔：保留玩家选的光影", r2.Contains("shaderPack=Better MC - Ultra"), r2);
        Check("等号分隔：保留注释", r2.Contains("#This file stores configuration options"), r2);

        // 值里带冒号的行不能被当成分隔符误判
        var tricky = "a=1\nurl=http://example.com:8099\n";
        var r3 = ConfigOverlay.ApplyProperties(tricky, Enforce("""{"a":2}"""), out _);
        Check("值里的冒号不影响解析", r3.Contains("url=http://example.com:8099") && r3.Contains("a=2"), r3);
    }

    private static void TomlOverlay()
    {
        Section("硬配置 · toml（NeoForge 模组配置）");

        var toml = """
            # 通用设置
            [general]
                # 是否启用彩蛋
                enableEasterEggs = true
                # 取值范围: 1 ~ 64
                maxStackSize = 64

            [client]
                # 取值范围: 0.0 ~ 1.0
                particleDensity = 1.0
                allowedDimensions = ["minecraft:overworld", "minecraft:the_nether"]
            """;

        var result = ConfigOverlay.ApplyToml(toml, Enforce("""
            {
              "general/maxStackSize": 16,
              "client/particleDensity": 0.5,
              "client/newKey": "hello",
              "audio/volume": 0.8
            }
            """), out var changed);

        Check("改掉 [general] 里的键", result.Contains("maxStackSize = 16"), result);
        Check("改掉 [client] 里的键", result.Contains("particleDensity = 0.5"), result);
        Check("同名不串表：只改指定表", result.Contains("enableEasterEggs = true"), result);
        Check("保留注释", result.Contains("# 取值范围: 1 ~ 64") && result.Contains("# 通用设置"), result);
        Check("保留数组", result.Contains("\"minecraft:the_nether\""), result);
        Check("往已有表里补键", result.Contains("newKey = \"hello\""), result);
        Check("为缺失的表新建表头", result.Contains("[audio]") && result.Contains("volume = 0.8"), result);
        Check("改动计数", changed == 4, changed.ToString());

        // newKey 必须落在 [client] 里，不能跑到 [audio] 后面去
        var clientAt = result.IndexOf("[client]", StringComparison.Ordinal);
        var newKeyAt = result.IndexOf("newKey", StringComparison.Ordinal);
        var audioAt = result.IndexOf("[audio]", StringComparison.Ordinal);
        Check("补的键落在正确的表里", clientAt < newKeyAt && newKeyAt < audioAt,
            $"[client]@{clientAt}  newKey@{newKeyAt}  [audio]@{audioAt}\n{result}");

        // 行尾注释不该被当成键值的一部分
        var withComment = "[a]\n  x = 1 # 这是注释\n";
        var r2 = ConfigOverlay.ApplyToml(withComment, Enforce("""{"a/x":9}"""), out _);
        Check("带行尾注释的键也能改", r2.Contains("x = 9"), r2);
    }

    private static void JsonOverlay()
    {
        Section("硬配置 · json");

        var json = """
            {
              "quality": { "fog_quality": "FANCY", "leaves": "FANCY" },
              "advanced": { "tracing": false },
              "custom": 42
            }
            """;

        var result = ConfigOverlay.ApplyJson(json, Enforce("""
            {"quality/fog_quality":"FAST","render/distance":12}
            """), out var changed);

        using var doc = JsonDocument.Parse(result);
        var root = doc.RootElement;

        Check("改掉嵌套键",
            root.GetProperty("quality").GetProperty("fog_quality").GetString() == "FAST", result);
        Check("保留同级其他键",
            root.GetProperty("quality").GetProperty("leaves").GetString() == "FANCY", result);
        Check("保留无关子树",
            root.GetProperty("custom").GetInt32() == 42, result);
        Check("自动建出缺失的中间层",
            root.GetProperty("render").GetProperty("distance").GetInt32() == 12, result);
        Check("改动计数", changed == 2, changed.ToString());
    }

    private static void NbtRoundTrip()
    {
        Section("servers.dat（NBT）");

        var tmp = Path.Combine(Path.GetTempPath(), "battermc-selftest-" + Guid.NewGuid().ToString("N")[..8]);
        try
        {
            var paths = LauncherPaths.At(tmp);
            paths.EnsureCreated();

            var servers = new List<ServerEntry>
            {
                new() { Name = "Batter MC 5", Host = "110.42.51.3", Port = 25565, Forced = true, Primary = true },
                new() { Name = "备用服 🎮", Host = "example.com", Port = 25577, Forced = true },
            };

            ServersDat.EnsureServers(paths, servers);
            var file = Path.Combine(paths.GameDir, "servers.dat");
            Check("生成了 servers.dat", File.Exists(file));

            var root = Nbt.ReadUncompressed(File.ReadAllBytes(file));
            var list = root["servers"] as NbtList;
            Check("读回 servers 列表", list is not null && list.Items.Count == 2,
                $"实得 {(list?.Items.Count ?? -1)} 项");

            var first = list!.Items[0] as NbtCompound;
            Equal("第一项名字", "Batter MC 5", first!.GetString("name") ?? "");
            Equal("默认端口不写端口号", "110.42.51.3", first.GetString("ip") ?? "");

            var second = list.Items[1] as NbtCompound;
            Equal("非默认端口带端口号", "example.com:25577", second!.GetString("ip") ?? "");
            Equal("非 ASCII 名字往返正确", "备用服 🎮", second.GetString("name") ?? "");

            // 再跑一次不应该产生重复条目
            ServersDat.EnsureServers(paths, servers);
            var again = Nbt.ReadUncompressed(File.ReadAllBytes(file))["servers"] as NbtList;
            Check("重复执行不产生重复条目", again!.Items.Count == 2, $"实得 {again.Items.Count} 项");

            // 玩家自己加的服务器必须留着
            var withPlayer = Nbt.ReadUncompressed(File.ReadAllBytes(file));
            var pl = (withPlayer["servers"] as NbtList)!;
            var mine = new NbtCompound();
            mine.SetString("name", "我自己的服");
            mine.SetString("ip", "192.168.1.5");
            pl.Items.Add(mine);
            File.WriteAllBytes(file, Nbt.WriteUncompressed(withPlayer));

            ServersDat.EnsureServers(paths, servers);
            var final = (Nbt.ReadUncompressed(File.ReadAllBytes(file))["servers"] as NbtList)!;
            Check("保留玩家自己加的服务器",
                final.Items.OfType<NbtCompound>().Any(c => c.GetString("ip") == "192.168.1.5"),
                $"共 {final.Items.Count} 项");

            ServersDat.EnsureServers(paths,
            [
                new ServerEntry { Name = "Batter MC 5", Host = "127.0.0.1", Port = 32123, Forced = true, Primary = true },
            ]);
            var proxied = (Nbt.ReadUncompressed(File.ReadAllBytes(file))["servers"] as NbtList)!;
            Check("同名主服可临时改成本地代理且不重复",
                proxied.Items.OfType<NbtCompound>().Count(c => c.GetString("name") == "Batter MC 5") == 1 &&
                proxied.Items.OfType<NbtCompound>().Any(c => c.GetString("ip") == "127.0.0.1:32123"));

            ServersDat.EnsureServers(paths, servers);
            var restored = (Nbt.ReadUncompressed(File.ReadAllBytes(file))["servers"] as NbtList)!;
            Check("代理退出后恢复公开地址",
                restored.Items.OfType<NbtCompound>().Any(c => c.GetString("name") == "Batter MC 5" && c.GetString("ip") == "110.42.51.3"));
        }
        finally
        {
            try { Directory.Delete(tmp, true); } catch { }
        }
    }

    private static void VersionRules()
    {
        Section("版本 JSON 规则求值");

        var f = VersionJson.Features.None;

        Check("没有规则 = 允许",
            VersionJson.RulesAllow(default, f));

        Check("osx 专属的库不能进 Windows 的 classpath",
            !VersionJson.RulesAllow(Json("""[{"action":"allow","os":{"name":"osx"}}]"""), f));

        Check("windows 专属的库要进",
            VersionJson.RulesAllow(Json("""[{"action":"allow","os":{"name":"windows"}}]"""), f));

        // 版本 JSON 里 -Xss1M 带 {"os":{"arch":"x86"}}，我们发的是 x64，绝不能加上
        Check("x86 专属参数不能用在 x64 上",
            !VersionJson.RulesAllow(Json("""[{"action":"allow","os":{"arch":"x86"}}]"""), f));

        Check("feature 关闭时不展开",
            !VersionJson.RulesAllow(
                Json("""[{"action":"allow","features":{"is_quick_play_multiplayer":true}}]"""), f));

        Check("feature 打开时展开",
            VersionJson.RulesAllow(
                Json("""[{"action":"allow","features":{"is_quick_play_multiplayer":true}}]"""),
                new VersionJson.Features(false, true, false)));

        var vars = new Dictionary<string, string>
        {
            ["version_name"] = "BatterMC5Remake",
            ["natives_directory"] = @"C:\g\natives",
        };
        Equal("占位符替换",
            @"-DignoreList=client-extra,BatterMC5Remake.jar",
            VersionJson.Substitute("-DignoreList=client-extra,${version_name}.jar", vars));
    }

    private static JsonElement Json(string s) => JsonDocument.Parse(s).RootElement.Clone();

    private static void VersionCompare()
    {
        Section("版本号比较");

        Check("1.0.1 比 1.0.0 新", SelfUpdater.IsNewer("1.0.1", "1.0.0"));
        Check("1.10.0 比 1.9.0 新", SelfUpdater.IsNewer("1.10.0", "1.9.0"));
        Check("相同版本不算新", !SelfUpdater.IsNewer("1.0.0", "1.0.0"));
        Check("旧版本不算新", !SelfUpdater.IsNewer("0.9.9", "1.0.0"));
        Check("段数不同也能比", SelfUpdater.IsNewer("1.1", "1.0.9"));

        Check("Java 主版本：21.0.4 -> 21", JavaManager.ParseMajor("21.0.4") == 21);
        Check("Java 主版本：1.8.0_402 -> 8", JavaManager.ParseMajor("1.8.0_402") == 8);
        Check("Java 主版本：17 -> 17", JavaManager.ParseMajor("17") == 17);
    }

    private static void ArgSplitting()
    {
        Section("JVM 参数拆分");

        var parts = GameLauncher.SplitArgs("-Xmx4G -Dfoo=\"a b\" -Dbar=baz").ToList();
        Check("拆出 3 个参数", parts.Count == 3, string.Join(" | ", parts));
        Check("引号内的空格不拆", parts.Count > 1 && parts[1] == "-Dfoo=a b",
            string.Join(" | ", parts));
        Check("空串返回空", !GameLauncher.SplitArgs("   ").Any());
    }

    private static async Task NetworkRouteProxy()
    {
        Section("MC 本地代理与并行选路");

        Check("私网 IPv4 识别为 LAN",
            MinecraftRouteProxy.Classify(IPAddress.Parse("192.168.3.8")) == RouteKind.LanDirect);
        Check("公网 IPv4 识别为直连",
            MinecraftRouteProxy.Classify(IPAddress.Parse("110.42.51.3")) == RouteKind.Ipv4Direct);
        Check("公网 IPv6 识别为直连",
            MinecraftRouteProxy.Classify(IPAddress.Parse("240e:1::1")) == RouteKind.Ipv6Direct);

        using var targetLifetime = new CancellationTokenSource();
        var target = new TcpListener(IPAddress.Loopback, 0);
        target.Start();
        var targetPort = ((IPEndPoint)target.LocalEndpoint).Port;
        var targetLoop = Task.Run(async () =>
        {
            try
            {
                while (!targetLifetime.IsCancellationRequested)
                {
                    var client = await target.AcceptTcpClientAsync(targetLifetime.Token);
                    _ = Task.Run(async () =>
                    {
                        using (client)
                        {
                            var stream = client.GetStream();
                            var buffer = new byte[32];
                            var read = await stream.ReadAsync(buffer, targetLifetime.Token);
                            if (read > 0) await stream.WriteAsync(buffer.AsMemory(0, read), targetLifetime.Token);
                        }
                    });
                }
            }
            catch (OperationCanceledException) { }
            catch (ObjectDisposedException) { }
        });

        try
        {
            var servers = new[]
            {
                new ServerEntry
                {
                    Name = "本地测试服", Primary = true, Host = "127.0.0.1", Port = targetPort,
                    Routes =
                    [
                        new RouteCandidate
                        {
                            Id = "unreachable-relay", Kind = RouteKind.Relay, Host = "127.0.0.1",
                            Port = ReserveClosedPort(), ProbeTimeoutMs = 300,
                        },
                        new RouteCandidate
                        {
                            Id = "lan", Kind = RouteKind.LanDirect, Host = "127.0.0.1", Port = targetPort,
                        },
                    ],
                },
            };

            await using var proxy = await MinecraftRouteProxy.StartAsync(servers, null, CancellationToken.None);
            Check("并行探测选中可达 LAN", proxy.Selection.Selected.Candidate.Id == "lan",
                proxy.Selection.Selected.Candidate.Id);
            Check("代理只监听回环地址",
                proxy.LocalAddress.StartsWith("127.0.0.1:", StringComparison.Ordinal), proxy.LocalAddress);

            using var minecraft = new TcpClient();
            await minecraft.ConnectAsync(IPAddress.Loopback, proxy.LocalPort);
            var payload = Encoding.UTF8.GetBytes("minecraft-proxy-ok");
            await minecraft.GetStream().WriteAsync(payload);
            var reply = new byte[payload.Length];
            var received = 0;
            while (received < reply.Length)
            {
                var read = await minecraft.GetStream().ReadAsync(reply.AsMemory(received));
                if (read == 0) break;
                received += read;
            }
            Equal("MC TCP 数据经本地代理往返", "minecraft-proxy-ok", Encoding.UTF8.GetString(reply, 0, received));
        }
        finally
        {
            targetLifetime.Cancel();
            target.Stop();
            try { await targetLoop; } catch { }
        }
    }

    private static int ReserveClosedPort()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        listener.Stop();
        return port;
    }
}
