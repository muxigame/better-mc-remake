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
        ShaderPreset();
        OptionalToggle();
        MemoryClamp();
        FullscreenOption();
        DownloadResume();
        PartSurvivesPrune();
        SeedRevisionSync();
        Section("平台 UID 游戏身份");
        Check("UID 数字作为真实登录名", GameSession.OfflineUid(10000).Username == "10000");
        Check("UID UUID 按 Java 离线算法", GameSession.OfflineUid(10000).UuidDashed == OfflineAuth.OfflineUuid("10000"));
        Check("不同 UID 不共享游戏 UUID", GameSession.OfflineUid(10000).UuidDashed != GameSession.OfflineUid(10001).UuidDashed);
        var rejectedUid = false;
        try { GameSession.OfflineUid(9999); } catch (ArgumentOutOfRangeException) { rejectedUid = true; }
        Check("无效 UID 不得回退到用户名", rejectedUid);
        AccountSessionRoundTrip();
        MirrorRewrite();
        GpuSelection();
        ClientUpdatePolicy();
        NbtRoundTrip();
        VersionRules();
        VersionCompare();
        ArgSplitting();
        PunchPortSweep();
        NetworkRouteProxy().GetAwaiter().GetResult();
        InstallLifecycle().GetAwaiter().GetResult();
        PipelineCompletionMarker().GetAwaiter().GetResult();
        InstallerProcessLifetime().GetAwaiter().GetResult();

        Console.WriteLine();
        Console.WriteLine($"通过 {_passed}，失败 {_failed}");
        return _failed == 0 ? 0 : 1;
    }

    // ---------------------------------------------------------------- 断言

    /// <summary>
    /// 显卡选择。写错了玩家不会看到任何报错，只会觉得"这游戏就是卡"，
    /// 所以真的写一次注册表再读回来。
    ///
    /// 用的是一个不存在的 exe 路径，跑完就删——不碰任何真实程序的显卡设置。
    /// </summary>
    private static void GpuSelection()
    {
        Section("显卡选择");

        var adapters = GpuPreference.Detect();
        Check("能枚举到显示适配器", adapters.Count > 0,
            "一块都没枚举到，注册表路径或过滤条件可能不对");
        // 串流/远程桌面的虚拟显示器不该混进来，否则单显卡机器会被当成双显卡
        Check("虚拟显示器已被滤掉",
            !adapters.Any(a => a.Name.Contains("Virtual", StringComparison.OrdinalIgnoreCase)),
            "混进了：" + string.Join("，", adapters.Select(a => a.Name)));
        foreach (var a in adapters)
            Console.WriteLine($"         · {a.Name}（{a.VramText}，{(a.LooksDiscrete ? "独显" : "核显")}）");

        // 排序决定"高性能"档落在哪块卡上，排反了玩家点独显会被写成节能档
        const long gb = 1024L * 1024 * 1024;
        void RankCase(string title, GpuAdapter first, GpuAdapter second, string expectTop)
        {
            // 注册表返回顺序不保证，两种输入顺序都要排出同样的结果
            foreach (var (label, order) in new (string, List<GpuAdapter>)[]
            {
                ("正序输入", new List<GpuAdapter> { first, second }),
                ("逆序输入", new List<GpuAdapter> { second, first }),
            })
            {
                GpuPreference.Rank(order);
                Check($"{title}（{label}）",
                    order[0].Name == expectTop
                    && GpuPreference.ChoiceFor(order, order[0]) == GpuChoice.HighPerformance
                    && GpuPreference.ChoiceFor(order, order[1]) == GpuChoice.PowerSaving,
                    $"排在前面的是 {order[0].Name}，期望 {expectTop}");
            }
        }

        RankCase("Intel 核显 + NVIDIA 独显",
            new GpuAdapter("Intel(R) UHD Graphics 770", 128L * 1024 * 1024, "Intel"),
            new GpuAdapter("NVIDIA GeForce RTX 4060 Laptop GPU", 8 * gb, "NVIDIA"),
            "NVIDIA GeForce RTX 4060 Laptop GPU");

        RankCase("AMD 核显 + AMD 独显",
            new GpuAdapter("AMD Radeon(TM) Graphics", 512L * 1024 * 1024, "AMD"),
            new GpuAdapter("AMD Radeon RX 7600M XT", 8 * gb, "AMD"),
            "AMD Radeon RX 7600M XT");

        // Arc 是 Intel 唯一的独显系列，不能因为厂商是 Intel 就判成核显
        RankCase("Intel 核显 + Intel Arc 独显",
            new GpuAdapter("Intel(R) Iris(R) Xe Graphics", 128L * 1024 * 1024, "Intel"),
            new GpuAdapter("Intel(R) Arc(TM) A770 Graphics", 16 * gb, "Intel"),
            "Intel(R) Arc(TM) A770 Graphics");

        var fake = @"C:\muxi-selftest-\不存在的\java.exe";
        try
        {
            Check("写入高性能偏好",
                GpuPreference.Apply(fake, GpuChoice.HighPerformance)
                && GpuPreference.Read(fake) == GpuChoice.HighPerformance);
            Check("改成节能偏好",
                GpuPreference.Apply(fake, GpuChoice.PowerSaving)
                && GpuPreference.Read(fake) == GpuChoice.PowerSaving);
            // Auto 的语义是"删掉这条"，留着反而会把选择钉死
            Check("选自动时删除该条目",
                GpuPreference.Apply(fake, GpuChoice.Auto) && GpuPreference.Read(fake) is null);

            GpuPreference.Apply(fake, GpuChoice.HighPerformance);
            GpuPreference.Forget(fake);
            Check("清理旧路径不留残留", GpuPreference.Read(fake) is null);
        }
        finally
        {
            GpuPreference.Forget(fake);
        }

        // 设置里的字符串和枚举必须对得上，错了会默默按默认值走
        var settings = new LauncherSettings();
        Check("默认使用独立显卡", settings.EffectiveGpuChoice() == GpuChoice.HighPerformance);
        settings.Gpu = "power";
        Check("power 映射到节能", settings.EffectiveGpuChoice() == GpuChoice.PowerSaving);
        settings.Gpu = "auto";
        Check("auto 映射到自动", settings.EffectiveGpuChoice() == GpuChoice.Auto);
        settings.Gpu = "乱填的值";
        Check("无法识别的值回落到独立显卡", settings.EffectiveGpuChoice() == GpuChoice.HighPerformance);
    }

    /// <summary>
    /// 镜像改写规则错了不会报错，只会让所有人悄悄回落到上游——也就是这套东西
    /// 等于没做。所以每条规则都钉死在这里。
    /// </summary>
    private static void MirrorRewrite()
    {
        Section("Minecraft 本体镜像");
        const string root = "https://cdn.example.com/bmc/mirror";
        var mirror = new DownloadMirror(root + "/");

        Equal("资源对象按主机名加路径改写",
            root + "/resources.download.minecraft.net/ab/abc123",
            mirror.Rewrite("https://resources.download.minecraft.net/ab/abc123") ?? "<null>");
        Equal("原版库改写",
            root + "/libraries.minecraft.net/com/mojang/logging/1.0/logging-1.0.jar",
            mirror.Rewrite("https://libraries.minecraft.net/com/mojang/logging/1.0/logging-1.0.jar") ?? "<null>");
        Equal("NeoForge 库改写",
            root + "/maven.neoforged.net/releases/net/neoforged/neoforge/21.1.250/x.jar",
            mirror.Rewrite("https://maven.neoforged.net/releases/net/neoforged/neoforge/21.1.250/x.jar") ?? "<null>");
        Equal("客户端 jar 改写",
            root + "/piston-data.mojang.com/v1/objects/deadbeef/client.jar",
            mirror.Rewrite("https://piston-data.mojang.com/v1/objects/deadbeef/client.jar") ?? "<null>");

        // 整合包文件本来就在我们的 OSS 上，再套一层会拼出一个不存在的地址
        Check("不改写我们自己的分发地址",
            mirror.Rewrite("https://muxigame-prod-static-cn.oss-cn-hangzhou.aliyuncs.com/bmc/release/latest/files/mods/a.jar") is null);
        Check("不改写未知主机", mirror.Rewrite("https://example.org/a.jar") is null);
        // 按路径镜像会让不同查询串撞到同一个对象上
        Check("带查询串的地址不改写",
            mirror.Rewrite("https://api.adoptium.net/v3/assets/latest/21/hotspot?os=windows") is null);
        Check("非 http(s) 不改写", mirror.Rewrite("ftp://libraries.minecraft.net/a.jar") is null);
        Check("空地址不改写", mirror.Rewrite("") is null);
    }

    /// <summary>
    /// 登录态必须能跨进程活下来，否则玩家每开一次启动器就得重登一次。
    ///
    /// 这段走的是 DPAPI（crypt32 的 P/Invoke），编译通过不代表调得通，
    /// 所以这里真的写一次盘再读回来。顺便确认密文里看不见原始令牌——
    /// 那个文件躺在 exe 旁边，便携模式下可能被同步到网盘。
    /// </summary>
    private static void AccountSessionRoundTrip()
    {
        Section("登录态持久化");
        var file = Path.Combine(Path.GetTempPath(), "muxi-selftest-" + Guid.NewGuid().ToString("N")[..8] + ".bin");
        const string refresh = "selftest-refresh-1234567890abcdef";
        try
        {
            AccountStore.Save(file, new AccountSession
            {
                AccessToken = "selftest-access",
                RefreshToken = refresh,
                SavedAt = DateTimeOffset.UtcNow,
            });
            Check("登录态写得进磁盘", File.Exists(file));

            var loaded = AccountStore.Load(file);
            Check("刷新令牌原样读得回来", loaded?.RefreshToken == refresh);
            Check("访问令牌原样读得回来", loaded?.AccessToken == "selftest-access");

            var raw = File.ReadAllBytes(file);
            Check("令牌没有明文落盘",
                !Encoding.UTF8.GetString(raw).Contains(refresh, StringComparison.Ordinal));

            // 密文被改过就该当成没登录，而不是抛异常把启动器带崩
            raw[^1] ^= 0xFF;
            File.WriteAllBytes(file, raw);
            Check("密文损坏按未登录处理", AccountStore.Load(file) is null);

            AccountStore.Clear(file);
            Check("退出账号后文件不留痕", !File.Exists(file));
        }
        finally
        {
            try { if (File.Exists(file)) File.Delete(file); } catch { }
        }
    }

    private static async Task InstallLifecycle()
    {
        Section("安装结束状态回归");
        var root = Path.Combine(Path.GetTempPath(), "bmc-install-state-" + Guid.NewGuid().ToString("N"));
        var output = Console.Out;
        var captured = new StringWriter();
        var checks = new List<(string Name, bool Passed)>();
        try
        {
            Console.SetOut(captured);
            var paths = LauncherPaths.At(root);
            using var host = new RpcHost(paths, new LauncherSettings(), new LocalState());
            var result = await host.RunInstallOperationAsync(_ => Task.CompletedTask);
            checks.Add(("成功 RPC 返回空闲，不反锁按钮", result["busy"]?.GetValue<bool>() == false));
            var finalEvent = captured.ToString().Split('\n', StringSplitOptions.RemoveEmptyEntries)
                .Select(line => System.Text.Json.Nodes.JsonNode.Parse(line))
                .Last(node => node?["event"]?.GetValue<string>() == "state");
            checks.Add(("最后状态事件与 RPC 一致", finalEvent?["payload"]?["busy"]?.GetValue<bool>() == false));
            var failed = false;
            try { await host.RunInstallOperationAsync(_ => Task.FromException(new IOException("test"))); }
            catch (IOException) { failed = true; }
            result = await host.RunInstallOperationAsync(_ => Task.CompletedTask);
            checks.Add(("失败后可再次检查并修复", failed && result["busy"]?.GetValue<bool>() == false));
            var cancelled = false;
            try { await host.RunInstallOperationAsync(_ => Task.FromCanceled(new CancellationToken(true))); }
            catch (OperationCanceledException) { cancelled = true; }
            result = await host.RunInstallOperationAsync(_ => Task.CompletedTask);
            checks.Add(("取消后不残留忙碌锁", cancelled && result["busy"]?.GetValue<bool>() == false));

            // Reproduce a state write failure without touching real player data.
            File.Delete(paths.StateFile);
            Directory.CreateDirectory(paths.StateFile);
            failed = false;
            try { await host.RunInstallOperationAsync(_ => Task.CompletedTask); }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { failed = true; }
            Directory.Delete(paths.StateFile);
            result = await host.RunInstallOperationAsync(_ => Task.CompletedTask);
            checks.Add(("保存状态失败也释放忙碌锁", failed && result["busy"]?.GetValue<bool>() == false));
        }
        finally
        {
            Console.SetOut(output);
            if (Directory.Exists(root)) Directory.Delete(root, true);
        }
        foreach (var (name, passed) in checks) Check(name, passed);
    }

    private static async Task InstallerProcessLifetime()
    {
        Section("安装进程取消与超时");
        static System.Diagnostics.Process StartOwnedChild()
        {
            var psi = new System.Diagnostics.ProcessStartInfo
            {
                FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "cmd.exe"),
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
            };
            psi.ArgumentList.Add("/c");
            psi.ArgumentList.Add("ping -n 60 127.0.0.1 > nul");
            return System.Diagnostics.Process.Start(psi)!;
        }
        using (var process = StartOwnedChild())
        {
            using var cancel = new CancellationTokenSource(200);
            var cancelled = false;
            try { await ChildProcessLifetime.WaitAsync(process, TimeSpan.FromSeconds(10), cancel.Token); }
            catch (OperationCanceledException) { cancelled = true; }
            Check("取消结束本次安装进程", cancelled && process.HasExited);
        }
        using (var process = StartOwnedChild())
        {
            var timedOut = false;
            try { await ChildProcessLifetime.WaitAsync(process, TimeSpan.FromMilliseconds(200), CancellationToken.None); }
            catch (TimeoutException) { timedOut = true; }
            Check("超时不会无限等待安装器", timedOut && process.HasExited);
        }
    }

    private sealed class InstallFixtureHttp : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            ct.ThrowIfCancellationRequested();
            var body = request.RequestUri!.AbsolutePath.EndsWith("/api/v1/manifest", StringComparison.Ordinal)
                ? """{"manifestUrl":"https://fixture.invalid/pack.json","filesBaseUrl":"https://fixture.invalid/files"}"""
                : """{"pack":{"name":"Fixture","version":"9.9.9"},"minecraft":{"versionJson":"versions/missing.json"},"files":[]}""";
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(body) });
        }
    }

    private static async Task PipelineCompletionMarker()
    {
        Section("下载与安装完成边界");
        var root = Path.Combine(Path.GetTempPath(), "bmc-readiness-" + Guid.NewGuid().ToString("N"));
        try
        {
            var state = new LocalState();
            using var downloader = new Downloader(new HttpClient(new InstallFixtureHttp()));
            var context = new PipelineContext { Paths = LauncherPaths.At(root), Settings = new LauncherSettings(), State = state };
            var pipeline = new LaunchPipeline(context, downloader);
            var phases = new List<string>();
            pipeline.Status += progress => phases.Add(progress.Phase);
            var failed = false;
            try { await pipeline.PrepareAsync(false, CancellationToken.None); }
            catch (FileNotFoundException) { failed = true; }
            Check("文件同步完成但运行环境失败不标记已安装", failed && state.InstalledPackVersion is null && state.LastSync is null);
            Check("运行环境未成功不发准备就绪", phases.Contains("整合包已是最新") && !phases.Contains("准备就绪"));
        }
        finally { if (Directory.Exists(root)) Directory.Delete(root, true); }
    }

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

    /// <summary>
    /// 取消下载后再来一次，必须从断点续传，而不是从头重下。
    /// 起一个最小的 HTTP 服务（认 Range，慢速发），把取消落在传输中途。
    /// </summary>
    private static void DownloadResume()
    {
        Section("下载中断续传");
        var tmp = Path.Combine(Path.GetTempPath(), "battermc-resume-" + Guid.NewGuid().ToString("N")[..8]);
        var listener = new TcpListener(IPAddress.Loopback, 0);
        using var serverStop = new CancellationTokenSource();
        try
        {
            Directory.CreateDirectory(tmp);
            var payload = new byte[512 * 1024];
            new Random(20260923).NextBytes(payload);
            var target = Path.Combine(tmp, "big.jar");
            File.WriteAllBytes(Path.Combine(tmp, "expected.bin"), payload);
            var sha = Hashing.Sha1File(Path.Combine(tmp, "expected.bin"));

            var rangeRequests = 0;
            listener.Start();
            var port = ((IPEndPoint)listener.LocalEndpoint).Port;
            _ = Task.Run(() => ServeRangeAsync(listener, payload, () => Interlocked.Increment(ref rangeRequests), serverStop.Token));

            var item = new DownloadItem
            {
                Url = $"http://127.0.0.1:{port}/big.jar",
                TargetPath = target,
                ExpectedSize = payload.Length,
                ExpectedSha1 = sha,
                Display = "big.jar",
            };

            using var downloader = new Downloader();

            // 第一次：收到一部分就取消
            using var cts = new CancellationTokenSource();
            var progress = new SyncProgress<DownloadProgress>(p =>
            {
                if (p.BytesDone >= 64 * 1024) cts.Cancel();
            });
            var cancelled = false;
            try { downloader.DownloadAllAsync([item], progress, cts.Token).GetAwaiter().GetResult(); }
            catch (OperationCanceledException) { cancelled = true; }
            Check("取消会中断下载", cancelled);

            var part = target + ".part";
            var partLen = File.Exists(part) ? new FileInfo(part).Length : 0;
            Check("取消后残片留在盘上", partLen > 0 && partLen < payload.Length, $"part={partLen} 全长={payload.Length}");
            Check("取消后不会留下半个成品", !File.Exists(target));

            // 第二次：必须带 Range 续传
            Interlocked.Exchange(ref rangeRequests, 0);
            downloader.DownloadAllAsync([item], new SyncProgress<DownloadProgress>(_ => { }), CancellationToken.None)
                .GetAwaiter().GetResult();

            Check("续传后文件完整", File.Exists(target) && Hashing.Sha1File(target).Equals(sha, StringComparison.OrdinalIgnoreCase));
            Check("第二次是断点续传而不是从头下", Volatile.Read(ref rangeRequests) == 1, $"带 Range 的请求 {rangeRequests} 次");
            Check("完成后残片已清理", !File.Exists(part));

            // NeoForge 安装器没有清单条目，大小和校验值只能现问 maven
            var probedSize = downloader.TryGetLengthAsync(item.Url, CancellationToken.None).GetAwaiter().GetResult();
            var probedSha = downloader.TryGetSha1Async(item.Url, CancellationToken.None).GetAwaiter().GetResult();
            Check("HEAD 能问出文件大小", probedSize == payload.Length, $"{probedSize}");
            Check("能读到 maven 的 .sha1 伴生文件", string.Equals(probedSha, sha, StringComparison.OrdinalIgnoreCase), probedSha ?? "null");
            Check("问不到元数据时返回空值而不是抛异常",
                downloader.TryGetLengthAsync("http://127.0.0.1:1/nothing", CancellationToken.None).GetAwaiter().GetResult() == 0
                && downloader.TryGetSha1Async("http://127.0.0.1:1/nothing", CancellationToken.None).GetAwaiter().GetResult() is null);
        }
        finally
        {
            serverStop.Cancel();
            try { listener.Stop(); } catch { }
            try { if (Directory.Exists(tmp)) Directory.Delete(tmp, true); } catch { }
        }
    }

    /// <summary>同步上报的进度实现。Progress&lt;T&gt; 会把回调丢到同步上下文里异步执行，取消时机会飘。</summary>
    private sealed class SyncProgress<T>(Action<T> report) : IProgress<T>
    {
        public void Report(T value) => report(value);
    }

    private static async Task ServeRangeAsync(TcpListener listener, byte[] payload, Action onRange, CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try
            {
                using var client = await listener.AcceptTcpClientAsync(ct).ConfigureAwait(false);
                using var stream = client.GetStream();

                var buf = new byte[8192];
                var n = await stream.ReadAsync(buf, ct).ConfigureAwait(false);
                var head = Encoding.ASCII.GetString(buf, 0, n);

                // maven 伴生的校验文件
                if (head.Contains(".sha1", StringComparison.Ordinal))
                {
                    var digest = Convert.ToHexString(System.Security.Cryptography.SHA1.HashData(payload)).ToLowerInvariant();
                    var digestBody = Encoding.ASCII.GetBytes(digest + "\n");
                    await stream.WriteAsync(Encoding.ASCII.GetBytes(
                        $"HTTP/1.1 200 OK\r\nContent-Length: {digestBody.Length}\r\nConnection: close\r\n\r\n"), ct).ConfigureAwait(false);
                    await stream.WriteAsync(digestBody, ct).ConfigureAwait(false);
                    continue;
                }

                // HEAD 只回头不回体
                if (head.StartsWith("HEAD ", StringComparison.Ordinal))
                {
                    await stream.WriteAsync(Encoding.ASCII.GetBytes(
                        $"HTTP/1.1 200 OK\r\nContent-Length: {payload.Length}\r\nAccept-Ranges: bytes\r\nConnection: close\r\n\r\n"), ct).ConfigureAwait(false);
                    continue;
                }

                long from = 0;
                var marker = head.IndexOf("bytes=", StringComparison.OrdinalIgnoreCase);
                if (marker >= 0)
                {
                    var digits = new string(head[(marker + 6)..].TakeWhile(char.IsAsciiDigit).ToArray());
                    if (long.TryParse(digits, out var parsed) && parsed > 0 && parsed < payload.Length)
                    {
                        from = parsed;
                        onRange();
                    }
                }

                var body = payload.AsMemory((int)from);
                var header = from > 0
                    ? $"HTTP/1.1 206 Partial Content\r\nContent-Length: {body.Length}\r\n"
                      + $"Content-Range: bytes {from}-{payload.Length - 1}/{payload.Length}\r\nConnection: close\r\n\r\n"
                    : $"HTTP/1.1 200 OK\r\nContent-Length: {body.Length}\r\nConnection: close\r\n\r\n";
                await stream.WriteAsync(Encoding.ASCII.GetBytes(header), ct).ConfigureAwait(false);

                // 慢速发，好让取消稳定落在传输中途
                for (var off = 0; off < body.Length; off += 32 * 1024)
                {
                    var len = Math.Min(32 * 1024, body.Length - off);
                    await stream.WriteAsync(body.Slice(off, len), ct).ConfigureAwait(false);
                    await stream.FlushAsync(ct).ConfigureAwait(false);
                    await Task.Delay(20, ct).ConfigureAwait(false);
                }
            }
            catch (OperationCanceledException) { return; }
            catch { /* 客户端取消会把连接掐断，继续等下一个 */ }
        }
    }

    /// <summary>
    /// 断点续传的前提是残片还在。prune 会扫 mods 目录清理多余文件，
    /// 要是把 .part 也当垃圾清掉，玩家取消一次就得从头再下 1.5 GB。
    /// </summary>
    private static void PartSurvivesPrune()
    {
        Section("残片与 prune");
        var tmp = Path.Combine(Path.GetTempPath(), "battermc-parttest-" + Guid.NewGuid().ToString("N")[..8]);
        try
        {
            var paths = LauncherPaths.At(tmp);
            paths.EnsureCreated();
            using var downloader = new Downloader();
            var settings = new LauncherSettings();
            var manifest = new PackManifest
            {
                Prune = ["mods"],
                Files =
                [
                    new ManagedFile { Path = "mods/big.jar", Size = 4096, Sha1 = new string('a', 40) },
                ],
            };

            var part = paths.ResolveGameFile("mods/big.jar.part");
            Directory.CreateDirectory(Path.GetDirectoryName(part)!);
            File.WriteAllText(part, "下到一半被取消了");

            var state = new LocalState { LastFilesBaseUrl = "https://example.invalid/files" };
            var plan = new SyncEngine(paths, state, settings, downloader).Plan(manifest, null, CancellationToken.None);
            Check("清单里还要的文件，其残片必须留着续传",
                !plan.Deletions.Contains("mods/big.jar.part"), string.Join(",", plan.Deletions));

            // 清单里已经没有的目标，残片就是纯垃圾
            var orphan = paths.ResolveGameFile("mods/gone.jar.part");
            File.WriteAllText(orphan, "垃圾");
            var plan2 = new SyncEngine(paths, state, settings, downloader).Plan(manifest, null, CancellationToken.None);
            Check("清单里已经没有的残片照清",
                plan2.Deletions.Contains("mods/gone.jar.part"), string.Join(",", plan2.Deletions));
        }
        finally
        {
            try { if (Directory.Exists(tmp)) Directory.Delete(tmp, true); } catch { }
        }
    }

    private static void FullscreenOption()
    {
        Section("全屏开关");
        var tmp = Path.Combine(Path.GetTempPath(), "battermc-fstest-" + Guid.NewGuid().ToString("N")[..8]);
        try
        {
            var paths = LauncherPaths.At(tmp);
            paths.EnsureCreated();
            var file = paths.ResolveGameFile(GameOptions.OptionsPath);
            File.WriteAllText(file, "version:3955\nfullscreen:false\nrenderDistance:12\nlang:zh_cn\n");

            Check("读出游戏里的全屏设置", GameOptions.ReadFullscreen(paths) == false);

            GameOptions.ApplyFullscreen(paths, true);
            var after = File.ReadAllText(file);
            Check("写入后游戏侧也是开的", GameOptions.ReadFullscreen(paths) == true, after);
            Check("只动 fullscreen 这一个键",
                after.Contains("renderDistance:12") && after.Contains("lang:zh_cn") && after.Contains("version:3955"), after);

            // 玩家在游戏里按 F11 关掉 —— 启动器要能读出来
            File.WriteAllText(file, after.Replace("fullscreen:true", "fullscreen:false"));
            Check("玩家在游戏里改了能读回来", GameOptions.ReadFullscreen(paths) == false);

            Check("没有 options.txt 时返回未知",
                GameOptions.ReadFullscreen(LauncherPaths.At(Path.Combine(tmp, "empty"))) is null);
        }
        finally
        {
            try { if (Directory.Exists(tmp)) Directory.Delete(tmp, true); } catch { }
        }
    }

    private static void MemoryClamp()
    {
        Section("内存上限");
        var tmp = Path.Combine(Path.GetTempPath(), "battermc-memtest-" + Guid.NewGuid().ToString("N")[..8]);
        try
        {
            var paths = LauncherPaths.At(tmp);
            paths.EnsureCreated();
            var config = paths.ResolveGameFile("config/memorysettings.json");
            Directory.CreateDirectory(Path.GetDirectoryName(config)!);
            File.WriteAllText(config, """
                {
                  "minimumClient": { "desc:": "...", "minimumClient": 3000 },
                  "maximumClient": { "desc:": "...", "maximumClient": 10000 },
                  "disableWarnings": { "desc:": "...", "disableWarnings": false }
                }
                """);

            var limits = PackMemoryLimits.Read(paths);
            Check("读出整合包声明的内存区间", limits.MinMb == 3000 && limits.MaxMb == 10000, limits.ToString());
            Check("没有配置文件时按未知处理",
                PackMemoryLimits.Read(LauncherPaths.At(Path.Combine(tmp, "empty"))).MaxMb == 0);

            // 玩家手填 16G：真机 12G 也好 64G 也好，都不该超过整合包阈值
            var greedy = new LauncherSettings { MaxMemoryMb = 16384 }.EffectiveMaxMemoryMb(limits);
            Check("手填超过整合包阈值会被夹回来", greedy <= 10000, greedy + " MB");
            Check("夹回来之后仍然是个能玩的值", greedy >= 3000, greedy + " MB");

            // 填得太小同样不行，低于阈值游戏也会弹警告屏
            var stingy = new LauncherSettings { MaxMemoryMb = 512 }.EffectiveMaxMemoryMb(limits);
            Check("手填低于整合包下限会被抬上来", stingy >= 3000, stingy + " MB");

            // 没有整合包信息时也不能顶到物理内存
            var noLimits = new LauncherSettings { MaxMemoryMb = 999999 }.EffectiveMaxMemoryMb();
            var total = (long)(GC.GetGCMemoryInfo().TotalAvailableMemoryBytes / (1024 * 1024));
            Check("没有整合包信息也要给系统留余量",
                noLimits <= Math.Max(2048, total - 2048), $"{noLimits} MB / 物理 {total} MB");

            // 自动挡不受影响
            var auto = new LauncherSettings().EffectiveMaxMemoryMb(limits);
            Check("自动挡仍落在区间内", auto >= 3000 && auto <= 10000, auto + " MB");
        }
        finally
        {
            try { if (Directory.Exists(tmp)) Directory.Delete(tmp, true); } catch { }
        }
    }

    private static void OptionalToggle()
    {
        Section("可选内容开关");
        var tmp = Path.Combine(Path.GetTempPath(), "battermc-opttest-" + Guid.NewGuid().ToString("N")[..8]);
        try
        {
            var paths = LauncherPaths.At(tmp);
            paths.EnsureCreated();
            using var downloader = new Downloader();
            const string path = "mods/optional-mod.jar";
            var disabled = path + OptionalContent.DisabledSuffix;
            var body = "jar bytes"u8.ToArray();

            PackManifest Manifest(string sha) => new()
            {
                Prune = ["mods"],
                Files =
                [
                    new ManagedFile { Path = path, Size = body.Length, Sha1 = sha, Policy = FilePolicy.Optional },
                ],
            };
            var manifest = Manifest(new string('a', 40));

            var on = new LauncherSettings { EnabledOptional = [path] };
            var off = new LauncherSettings();
            LocalState NewState() => new() { LastFilesBaseUrl = "https://example.invalid/files" };

            // 玩家没勾：也不会去下，但更不该删已经装好的
            var fresh = new SyncEngine(paths, NewState(), off, downloader).Plan(manifest, null, CancellationToken.None);
            Check("没装过又没开：不删任何东西", fresh.Deletions.Count == 0, string.Join(",", fresh.Deletions));
            Check("可选内容照样跟着同步下来，只是以禁用态落地",
                fresh.Downloads.Count == 1 && fresh.Downloads[0].TargetPath.EndsWith(OptionalContent.DisabledSuffix, StringComparison.Ordinal),
                fresh.Downloads.Count == 1 ? fresh.Downloads[0].TargetPath : "没有下载项");

            // 装好的状态：文件在，勾着
            var enabledAbs = paths.ResolveGameFile(path);
            Directory.CreateDirectory(Path.GetDirectoryName(enabledAbs)!);
            File.WriteAllBytes(enabledAbs, body);
            manifest = Manifest(Hashing.Sha1File(enabledAbs));

            var keep = new SyncEngine(paths, NewState(), on, downloader).Plan(manifest, null, CancellationToken.None);
            Check("开着且内容正确：什么都不做", keep.IsEmpty, $"下载{keep.Downloads.Count} 删{keep.Deletions.Count} 改名{keep.Renames.Count}");

            // 取消勾选：改名，不删、不重下
            var turnOff = new SyncEngine(paths, NewState(), off, downloader).Plan(manifest, null, CancellationToken.None);
            Check("关掉只改名不删文件",
                turnOff.Deletions.Count == 0 && turnOff.Downloads.Count == 0
                && turnOff.Renames.Count == 1 && turnOff.Renames[0] == (path, disabled),
                $"删{turnOff.Deletions.Count} 下{turnOff.Downloads.Count} 改名{turnOff.Renames.Count}");

            // 落到磁盘上：禁用态
            OptionalContent.Apply(paths, manifest.Files, off.EnabledOptional);
            Check("禁用后文件还在，只是改了名",
                !File.Exists(enabledAbs) && File.Exists(paths.ResolveGameFile(disabled)));

            // 禁用态再同步：不能被 prune 当垃圾清掉，也不该重下
            var whileOff = new SyncEngine(paths, NewState(), off, downloader).Plan(manifest, null, CancellationToken.None);
            Check("禁用态不会被 prune 清掉", whileOff.Deletions.Count == 0, string.Join(",", whileOff.Deletions));
            Check("禁用态不会重复下载", whileOff.Downloads.Count == 0);

            // 重新勾上：改回来，仍然不用下载
            var turnOn = new SyncEngine(paths, NewState(), on, downloader).Plan(manifest, null, CancellationToken.None);
            Check("重新启用只改名回来",
                turnOn.Downloads.Count == 0 && turnOn.Renames.Count == 1 && turnOn.Renames[0] == (disabled, path),
                $"下{turnOn.Downloads.Count} 改名{turnOn.Renames.Count}");
            OptionalContent.Apply(paths, manifest.Files, on.EnabledOptional);
            Check("启用后文件名回到 .jar", File.Exists(enabledAbs));

            // 玩家自己丢进 mods 的本地模组：启动器没装过，一根手指都不许动
            var mine = paths.ResolveGameFile("mods/my-minimap.jar");
            File.WriteAllText(mine, "player's own mod");
            var myDisabled = paths.ResolveGameFile("mods/my-other-mod.jar.disabled");
            File.WriteAllText(myDisabled, "player disabled it himself");

            var untouched = new SyncEngine(paths, NewState(), on, downloader).Plan(manifest, null, CancellationToken.None);
            Check("玩家自己的模组不删", untouched.Deletions.Count == 0, string.Join(",", untouched.Deletions));
            Check("玩家自己禁用的模组也不删", File.Exists(myDisabled));

            // 启动器装过、但整合包后来移除了的文件：必须收回来
            var retired = "mods/retired-by-server.jar";
            File.WriteAllText(paths.ResolveGameFile(retired), "shipped before, dropped now");
            var stateWithRecord = NewState();
            stateWithRecord.InstalledFiles.Add(retired);
            var cleanup = new SyncEngine(paths, stateWithRecord, on, downloader).Plan(manifest, null, CancellationToken.None);
            Check("清单里移除的模组会被收回",
                cleanup.Deletions.Contains(retired), string.Join(",", cleanup.Deletions));
            Check("收回时不误伤玩家的文件",
                !cleanup.Deletions.Contains("mods/my-minimap.jar"), string.Join(",", cleanup.Deletions));

            // 老客户端升级上来没有这份记账，靠哈希缓存兜底也能认出自己装过的东西
            var legacy = NewState();
            legacy.Hashes[retired] = new HashCacheEntry { Size = 1, MTimeTicks = 1, Sha1 = new string('c', 40) };
            legacy.InstalledFiles.Clear();
            foreach (var key in legacy.Hashes.Keys) legacy.InstalledFiles.Add(key);
            var legacyPlan = new SyncEngine(paths, legacy, on, downloader).Plan(manifest, null, CancellationToken.None);
            Check("老安装靠哈希缓存也能认出自己装过的文件",
                legacyPlan.Deletions.Contains(retired), string.Join(",", legacyPlan.Deletions));
        }
        finally
        {
            try { if (Directory.Exists(tmp)) Directory.Delete(tmp, true); } catch { }
        }
    }

    private static void ShaderPreset()
    {
        Section("光影预设");

        var iris = """
            #This file stores configuration options for Iris
            allowUnknownShaders=false
            colorSpace=SRGB
            enableShaders=true
            maxShadowRenderDistance=16
            shaderPack=Better MC - Low
            """;

        var on = ShaderPresets.Parse(iris);
        Check("读出游戏里选的光影", on.Enabled && on.Pack == "Better MC - Low", on.ToString());
        Check("对外表示就是包名", on.AsSettingValue() == "Better MC - Low", on.AsSettingValue());

        var offText = iris.Replace("enableShaders=true", "enableShaders=false");
        var off = ShaderPresets.Parse(offText);
        Check("关掉光影时读出无光影", !off.Enabled && off.AsSettingValue() == "", off.ToString());

        Check("没有配置文件按无光影算", ShaderPresets.Parse("").AsSettingValue() == "");

        // 写入走的是硬配置那套键级替换，验证只动两个键、别的原样
        var toUltra = ConfigOverlay.ApplyProperties(
            iris, Enforce("""{"enableShaders":"true","shaderPack":"Better MC - Ultra"}"""), out _);
        Check("切换光影只改 shaderPack", toUltra.Contains("shaderPack=Better MC - Ultra"), toUltra);
        Check("切换光影保留其它设置",
            toUltra.Contains("colorSpace=SRGB") && toUltra.Contains("maxShadowRenderDistance=16"), toUltra);
        Check("切换光影保留注释", toUltra.Contains("#This file stores"), toUltra);
        Check("切回去能再读出来", ShaderPresets.Parse(toUltra).Pack == "Better MC - Ultra");

        // 关光影只写 enableShaders，shaderPack 留着，玩家再打开还是上次那个
        var turnedOff = ConfigOverlay.ApplyProperties(iris, Enforce("""{"enableShaders":"false"}"""), out _);
        Check("关光影不抹掉上次选的包", turnedOff.Contains("shaderPack=Better MC - Low"), turnedOff);
        Check("关光影后读出来是无光影", ShaderPresets.Parse(turnedOff).AsSettingValue() == "", turnedOff);
    }

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

        // 列表型值：只摘掉点名的条目，玩家自己选的资源包一个都不能少
        var packs = """
            resourcePacks:["vanilla","mod/pasterdream:packs/paster_vanilla_ui","file/Mandala Utopia.zip"]
            lang:zh_cn
            """;
        var removals = new Dictionary<string, List<string>>
        {
            ["resourcePacks"] = ["mod/pasterdream:packs/paster_vanilla_ui", "builtin/paster_vanilla_ui"],
            ["lang"] = ["zh_cn"],
        };
        var r4 = ConfigOverlay.RemoveListEntries(packs, removals, out var dropped);

        Check("摘掉点名的资源包", !r4.Contains("paster_vanilla_ui"), r4);
        Check("其余资源包原样保留",
            r4.Contains("""resourcePacks:["vanilla","file/Mandala Utopia.zip"]"""), r4);
        Check("不在列表里的条目不计数", dropped == 1, dropped.ToString());
        Check("非数组的值不碰", r4.Contains("lang:zh_cn"), r4);

        var r5 = ConfigOverlay.RemoveListEntries(r4, removals, out var dropped2);
        Check("已经摘干净就不再改", dropped2 == 0 && r5 == r4, r5);
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

        // NeoForge 的键常带空格，既要能就地改，补键时也必须带引号，否则整份配置解析不了
        var quoted = "[HUD]\n\t\"enable mod ui\" = true\n";
        var r3 = ConfigOverlay.ApplyToml(quoted, Enforce("""
            {
              "HUD/enable mod ui": false,
              "HUD/paster health hud": false
            }
            """), out _);
        Check("带空格的引号键能就地改", r3.Contains("\"enable mod ui\" = false"), r3);
        Check("补的带空格键自带引号", r3.Contains("\"paster health hud\" = false"), r3);
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

    private static void SeedRevisionSync()
    {
        Section("Seed 修订同步");
        var tmp = Path.Combine(Path.GetTempPath(), "battermc-seedtest-" + Guid.NewGuid().ToString("N")[..8]);
        try
        {
            var paths = LauncherPaths.At(tmp);
            paths.EnsureCreated();
            var settings = new LauncherSettings();
            using var downloader = new Downloader();
            const string path = "config/example.toml";
            var shaV1 = new string('a', 40);
            var shaV2 = new string('b', 40);
            PackManifest Manifest(string sha) => new()
            {
                Files =
                [
                    new ManagedFile { Path = path, Size = 0, Sha1 = sha, Policy = FilePolicy.Seed },
                ],
            };

            // 真正首次安装：文件不存在、也没有修订记账，必须下载。
            var firstState = new LocalState { LastFilesBaseUrl = "https://example.invalid/files" };
            var first = new SyncEngine(paths, firstState, settings, downloader).Plan(Manifest(shaV1), null, CancellationToken.None);
            Check("首次安装 Seed 会下载", first.Downloads.Count == 1);

            // 模拟 v1 已成功投放，玩家随后改了本地内容。同一修订不能覆盖。
            var absolute = paths.ResolveGameFile(path);
            Directory.CreateDirectory(Path.GetDirectoryName(absolute)!);
            File.WriteAllText(absolute, "player custom value");
            firstState.MarkSeedRevision(path, shaV1);
            var sameRevision = new SyncEngine(paths, firstState, settings, downloader).Plan(Manifest(shaV1), null, CancellationToken.None);
            Check("同一 Seed 修订保留玩家修改", sameRevision.Downloads.Count == 0);

            // 服务端发布 v2 SHA 后，必须强制同步一次。
            var nextRevision = new SyncEngine(paths, firstState, settings, downloader).Plan(Manifest(shaV2), null, CancellationToken.None);
            Check("Seed SHA 变化会强制同步一次",
                nextRevision.Downloads.Count == 1 && nextRevision.SeedRevisionTargets.TryGetValue(path, out var revision) && revision == shaV2);

            // 老客户端升级到新记账模型：已有文件但没有 SeedRevisions 时，只建基线，不覆盖。
            var legacyState = new LocalState { LastFilesBaseUrl = "https://example.invalid/files" };
            var bootstrap = new SyncEngine(paths, legacyState, settings, downloader).Plan(Manifest(shaV1), null, CancellationToken.None);
            Check("老安装首次升级只建立 Seed 基线", bootstrap.Downloads.Count == 0 &&
                legacyState.TryGetSeedRevision(path, out var baseline) && baseline == shaV1);
        }
        finally
        {
            try { if (Directory.Exists(tmp)) Directory.Delete(tmp, true); } catch { }
        }
    }

    private static void ClientUpdatePolicy()
    {
        Section("客户端支持策略");
        var release = new LauncherRelease { Version = "1.2.0", MinSupportedVersion = "1.1.5" };
        Check("低于最低支持版本必须更新", SelfUpdater.IsRequired(release, "1.1.4"));
        Check("等于最低支持版本允许稍后", !SelfUpdater.IsRequired(release, "1.1.5"));
        Check("较新但非最新版本允许稍后", !SelfUpdater.IsRequired(release, "1.1.9"));
        Check("最新客户端不会强制循环更新", !SelfUpdater.IsRequired(release, "1.2.0"));
        Check("不强制降级", !SelfUpdater.IsRequired(release, "1.3.0"));
        release.MinSupportedVersion = null;
        release.BlockedVersions.Add("1.1.8");
        Check("单独停用问题版本", SelfUpdater.IsRequired(release, "1.1.8+build1"));
        Check("停用问题版不牵连其它版本", !SelfUpdater.IsRequired(release, "1.1.7"));
        Check("正式版高于预发布版", SelfUpdater.IsNewer("1.1.5", "1.1.5-rc.1"));
        Check("预发布数字正确排序", SelfUpdater.IsNewer("1.1.5-rc.10", "1.1.5-rc.2"));
        Check("忽略 build metadata", SelfUpdater.Compare("v1.1.5+build1", "1.1.5+build2") == 0);
        var control = ManifestControl.FromJson("{\"launcher\":{\"version\":\"1.2.0\",\"minSupportedVersion\":\"1.1.5\",\"blockedVersions\":[\"1.1.8\"]}}");
        Check("控制面策略 JSON 正确反序列化", control?.Launcher is {} parsed && SelfUpdater.IsRequired(parsed, "1.1.4") && SelfUpdater.IsRequired(parsed, "1.1.8"));
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

    /// <summary>
    /// 打洞的端口扫描。
    ///
    /// 对端如果是地址相关型 NAT，它报上来的映射端口和它实际朝我们发包时用的不是
    /// 同一个，照着报的那个打必然打偏。这里把"报错的端口"直接构造出来：告诉 A
    /// 一个偏了 40 的端口，只有扫描真的展开了，A 才可能找到 B。
    /// </summary>
    private static void PunchPortSweep()
    {
        Console.WriteLine("打洞端口扫描");
        var secret = PunchProtocol.DeriveSecret("selftest-token");
        using var a = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
        using var b = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
        a.Bind(new IPEndPoint(IPAddress.Loopback, 0));
        b.Bind(new IPEndPoint(IPAddress.Loopback, 0));
        var aPort = ((IPEndPoint)a.LocalEndPoint!).Port;
        var bPort = ((IPEndPoint)b.LocalEndPoint!).Port;

        const ulong session = 0xB0B0_1234_5678_9ABCUL;
        var punchAt = DateTimeOffset.UtcNow.AddMilliseconds(200);
        var budget = TimeSpan.FromSeconds(3);

        // A 拿到的端口偏低 40：对端先探反射器拿到映射，几秒后才朝我们发包，那时
        // 顺序分配的 NAT 已经往上走了——真实端口一定高于报上来的那个，所以扫描
        // 窗口也是向上偏的。这里照着真实方向构造。
        var wrong = new IPEndPoint(IPAddress.Loopback, bPort - 40);
        var right = new IPEndPoint(IPAddress.Loopback, aPort);

        // 单边分工：A 的地址对 B 是已知的，所以由 A 去找 B；B 只管朝已知地址发，不扫。
        var ta = UdpPuncher.PunchAsync(a, [wrong], session, secret, punchAt, budget,
            sweepPorts: true, null, CancellationToken.None);
        var tb = UdpPuncher.PunchAsync(b, [right], session, secret, punchAt, budget,
            sweepPorts: false, null, CancellationToken.None);
        Task.WaitAll([ta, tb], TimeSpan.FromSeconds(20));

        Check("错端口时扫描能打通", ta.Result.Success && tb.Result.Success,
            $"A={ta.Result.Success}（发出 {ta.Result.Sent}） B={tb.Result.Success}");
        Check("扫描确实展开了（发包数远超准确地址那一条）", ta.Result.Sent > 100,
            $"A 只发了 {ta.Result.Sent} 个包");
        Check("不该扫的那一侧没有多发包", tb.Result.Sent < 60,
            $"B 发了 {tb.Result.Sent} 个包，本不该扫描");
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
