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
        ShaderAdoption();
        SeedKeysOverlay();
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
        P2PHardening().GetAwaiter().GetResult();
        PunchRoleAssignment();
        ShutdownVsTimeout();
        HoleLoadPackets();
        MtuDiagnosis();
        TcpPunchConverge();
        MuxStreams();
        NetworkRouteProxy().GetAwaiter().GetResult();
        LaunchWithoutRoute().GetAwaiter().GetResult();
        QuicPacketSize().GetAwaiter().GetResult();
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

        // Xaero 这类 "key = value" 的配置，写回去必须保持等号两边的空格
        var xaero = "map_writing_distance = -1\nlighting = true\nterrain_slopes = 2\n";
        var r6 = ConfigOverlay.ApplyProperties(xaero, Enforce("""{"map_writing_distance":6}"""), out _);
        Check("保留等号后的空格", r6.Contains("map_writing_distance = 6"), r6);
        Check("其它行不动", r6.Contains("lighting = true") && r6.Contains("terrain_slopes = 2"), r6);

        var tight = "a=1\nb=2\n";
        var r7 = ConfigOverlay.ApplyProperties(tight, Enforce("""{"a":9}"""), out _);
        Check("没空格的格式也保持原样", r7.Contains("a=9") && !r7.Contains("a= 9"), r7);

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

    private static void ShaderAdoption()
    {
        Section("光影：以玩家在游戏里的选择为准");

        var root = Path.Combine(Path.GetTempPath(), "bmc-shader-" + Guid.NewGuid().ToString("N")[..8]);
        try
        {
            var paths = LauncherPaths.At(root);
            var iris = paths.ResolveGameFile(ShaderPresets.ConfigPath);
            Directory.CreateDirectory(Path.GetDirectoryName(iris)!);

            // 整合包出厂值：开着光影
            File.WriteAllText(iris,
                "enableShaders=true\nshaderPack=Better MC - Low\nmaxShadowRenderDistance=16\ncolorSpace=SRGB\n");

            Check("没动过就不改记录",
                ShaderPresets.AdoptPlayerChoice("Better MC - Low", ShaderPresets.Read(paths)) is null,
                "记录与文件一致时不该收编");

            // 玩家在游戏里关掉了光影
            File.WriteAllText(iris,
                "enableShaders=false\nshaderPack=Better MC - Low\nmaxShadowRenderDistance=16\ncolorSpace=SRGB\n");
            var adopted = ShaderPresets.AdoptPlayerChoice("Better MC - Low", ShaderPresets.Read(paths));
            Check("玩家关掉光影能被认出来", adopted == "", adopted ?? "(null)");

            // 收编之后再写回去，不能又把它打开
            ShaderPresets.Apply(paths, adopted);
            var after = File.ReadAllText(iris);
            Check("写回之后仍然是关闭的", after.Contains("enableShaders=false"), after);
            Check("保留玩家上次用的包名，方便他再打开", after.Contains("shaderPack=Better MC - Low"), after);
            Check("不碰其它设置", after.Contains("maxShadowRenderDistance=16") && after.Contains("colorSpace=SRGB"), after);

            // 玩家换成另一个包
            File.WriteAllText(iris, "enableShaders=true\nshaderPack=Better MC - High\n");
            Check("换包也能被认出来",
                ShaderPresets.AdoptPlayerChoice("", ShaderPresets.Read(paths)) == "Better MC - High",
                after);
        }
        finally
        {
            try { Directory.Delete(root, true); } catch { }
        }
    }

    private static void SeedKeysOverlay()
    {
        Section("硬配置 · 一次性下发（默认值改了，主权还给玩家）");

        var root = Path.Combine(Path.GetTempPath(), "bmc-seedkeys-" + Guid.NewGuid().ToString("N")[..8]);
        try
        {
            var paths = LauncherPaths.At(root);
            var options = paths.ResolveGameFile("options.txt");
            Directory.CreateDirectory(Path.GetDirectoryName(options)!);
            File.WriteAllText(options, "fov:-0.575\nguiScale:0\nlang:zh_cn\n");

            var state = new LocalState();
            var spec = new ConfigOverlaySpec
            {
                Path = "options.txt",
                Format = OverlayFormat.Properties,
                CreateIfMissing = false,
                SeedKeys = Enforce("""{"fov":0.0}"""),
            };

            ConfigOverlay.ApplyAll([spec], paths, state);
            Check("第一次启动：默认值推到位", File.ReadAllText(options).Contains("fov:0.0"),
                File.ReadAllText(options));
            Check("推过之后留下记账", !state.NeedsOverlaySeed("options.txt", "fov", "0.0"),
                string.Join(",", state.OverlaySeeds));

            // 玩家自己改了
            File.WriteAllText(options, "fov:0.35\nguiScale:0\nlang:zh_cn\n");
            ConfigOverlay.ApplyAll([spec], paths, state);
            Check("玩家改过之后不再被按回去", File.ReadAllText(options).Contains("fov:0.35"),
                File.ReadAllText(options));

            // 服务器换了新默认值 → 再推一次
            spec.SeedKeys = Enforce("""{"fov":0.25}""");
            ConfigOverlay.ApplyAll([spec], paths, state);
            Check("服务器换新值才再推一次", File.ReadAllText(options).Contains("fov:0.25"),
                File.ReadAllText(options));

            // 没有 state 就没法记账，这种情况宁可不发
            var noState = new ConfigOverlaySpec
            {
                Path = "options.txt",
                Format = OverlayFormat.Properties,
                CreateIfMissing = false,
                SeedKeys = Enforce("""{"guiScale":3}"""),
            };
            ConfigOverlay.ApplyAll([noState], paths, null);
            Check("没有记账就不下发", File.ReadAllText(options).Contains("guiScale:0"),
                File.ReadAllText(options));

            // 其它键一个都不许动
            Check("只碰点名的键", File.ReadAllText(options).Contains("lang:zh_cn"),
                File.ReadAllText(options));
        }
        finally
        {
            try { Directory.Delete(root, true); } catch { }
        }
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
            Check("并行探测选中可达 LAN", proxy.Selection.Selected?.Candidate.Id == "lan",
                proxy.Selection.Selected?.Candidate.Id ?? "(无)");
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
    /// 线路不通时照样放玩家进游戏，后台接着连。
    ///
    /// 实测的起因：服务器重启那一分钟里点开始游戏，隧道、中转、三次打洞全是"连上了、
    /// 一读就断"，启动器抛异常，游戏压根不启动，玩家连单机都进不去。而那一分钟里
    /// agent 一直在，只是接不上它本机的 Minecraft：再打洞只会让服务端白推压测流量。
    /// </summary>
    private static async Task LaunchWithoutRoute()
    {
        Section("线路不通照样进游戏");

        using var life = new CancellationTokenSource();
        var agent = new TcpListener(IPAddress.Loopback, 0);
        agent.Start();
        var agentPort = ((IPEndPoint)agent.LocalEndpoint).Port;
        var mode = AgentMcDown;
        var agentLoop = FakeAgentAsync(agent, () => Volatile.Read(ref mode), life.Token);
        try
        {
            // 线路本身是通的：TCP 探测连得上，隧道端口也是这个假 agent。
            var servers = new[]
            {
                new ServerEntry
                {
                    Name = "重启中", Primary = true, Host = "127.0.0.1", Port = agentPort,
                    Routes =
                    [
                        new RouteCandidate
                        {
                            Id = "lan", Kind = RouteKind.LanDirect, Host = "127.0.0.1", Port = agentPort,
                        },
                    ],
                },
            };
            var controlPlane = $"http://127.0.0.1:{ReserveClosedPort()}";

            // 1. agent 在、MC 不在：认出是服务器没开，不再去打洞
            await using (var probe = await MinecraftRouteProxy.StartAsync(servers, null,
                             CancellationToken.None, allowNoRoute: true))
            {
                var watch = System.Diagnostics.Stopwatch.StartNew();
                Exception? failure = null;
                try
                {
                    await probe.EstablishBestAsync("token", controlPlane, "default",
                        "127.0.0.1", agentPort, null, CancellationToken.None, upgradeInBackground: true);
                }
                catch (Exception error) { failure = error; }
                Check("agent 通、MC 不通时认出是服务器没开", failure is ServerNotRespondingException,
                    failure is null ? "(没抛)" : $"{failure.GetType().Name}: {failure.Message}");
                Check("认出之后不再白打洞", watch.Elapsed < TimeSpan.FromSeconds(3), $"{watch.ElapsedMilliseconds} ms");
            }

            // 1b. 开流时 agent 还没回 StreamReady 就断了：那是路径（TUN 代理、frps 接不上 agent）
            //     的毛病，agent 总是先回 StreamReady 再去接 MC。不能当成服务器没开，否则会把
            //     本来还能走的中转和打洞一起跳过。
            Volatile.Write(ref mode, AgentPathDrop);
            await using (var probe = await MinecraftRouteProxy.StartAsync(servers, null,
                             CancellationToken.None, allowNoRoute: true))
            {
                Exception? failure = null;
                try
                {
                    await probe.EstablishBestAsync("token", controlPlane, "default",
                        "127.0.0.1", agentPort, null, CancellationToken.None, skipP2P: true);
                }
                catch (Exception error) { failure = error; }
                Check("路径在开流时断掉不算服务器没开",
                    failure is not null and not ServerNotRespondingException,
                    failure is null ? "(没抛)" : $"{failure.GetType().Name}: {failure.Message}");
            }
            Volatile.Write(ref mode, AgentMcDown);

            // 2. 启动器真正的用法：代理先起、游戏马上启动，连接全在后台
            await using var proxy = MinecraftRouteProxy.Listen(servers);
            var changed = 0;
            var unavailable = new TaskCompletionSource<Exception>(TaskCreationOptions.RunContinuationsAsynchronously);
            proxy.RouteChanged += _ => Interlocked.Increment(ref changed);
            proxy.RouteUnavailable += error => unavailable.TrySetResult(error);
            var steps = new System.Collections.Concurrent.ConcurrentQueue<string>();
            proxy.ConnectInBackground(
                _ => Task.FromResult(new ConnectPlan(servers, "token", controlPlane, "default", "127.0.0.1", agentPort)),
                new SyncProgress(steps.Enqueue));
            Check("起代理不等网络", proxy.Established is null && proxy.LocalPort > 0);
            var first = await unavailable.Task.WaitAsync(TimeSpan.FromSeconds(10));
            Check("后台第一轮没连上时通知界面", first is ServerNotRespondingException, first.Message);
            Check("连接进度报给进度条", !steps.IsEmpty, string.Join(" | ", steps.Take(3)));

            // 3. 多人列表来问：替服务器回一句为什么
            var local = new IPEndPoint(IPAddress.Loopback, proxy.LocalPort);
            var listed = await MotdOrErrorAsync(local);
            Check("多人列表显示服务器在重启", listed.Contains("重启"), listed);

            // 4. 这时点加入：给出进不去的原因，而不是一句"连接中断"
            string kick;
            try { kick = await LoginKickAsync(proxy.LocalPort); }
            catch (Exception error) { kick = $"{error.GetType().Name}: {error.Message}"; }
            Check("点加入时告诉玩家为什么进不去", kick.Contains("重启"), kick);
            Check("还没连上时不装作有线路", proxy.Established is null);

            // 5. 服务器起来了：下一次来连就接上
            Volatile.Write(ref mode, AgentMcUp);
            Equal("服务器恢复后经本地代理连上", "fake-mc-online", await MotdOrErrorAsync(local));
            Check("重连成功后线路已建立", proxy.Established is not null);
            Check("重连成功时通知界面", Volatile.Read(ref changed) >= 1, changed.ToString());
        }
        finally
        {
            life.Cancel();
            agent.Stop();
            try { await agentLoop; } catch { }
        }
    }

    /// <summary>
    /// QUIC 的包长上限，见 <see cref="MsQuicMtu"/>。
    ///
    /// 实测事故：家宽到服务端的一条 P2P，连接用了几十分钟后，1300 字节的数据秒到、1400 字节
    /// 以上一个字节都过不来——QUIC 自己把包长探到了这条路过不去的大小，而且不往回退。
    /// 小包照常来回，连接一直"活着"，只有游戏数据停住，Minecraft 30 秒后两头各报超时。
    ///
    /// 本机回环的 MTU 很大，不锁的话 QUIC 很快就会把包长探到 1500（UDP 载荷 1472）。
    /// 所以这里在中间夹一个 UDP 转发器，数一数过去的包最大有多大。
    /// </summary>
    private static async Task QuicPacketSize()
    {
        Section("QUIC 包长上限");
        if (!QuicTunnel.IsSupported)
        {
            Check("本机不支持 QUIC，跳过", true);
            return;
        }
        Check("包长锁定可用", MsQuicMtu.Status.StartsWith("包长锁定"), MsQuicMtu.Status);

        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(40));
        await using var listener = await QuicTunnel.ListenAsync(
            0, QuicTunnel.SharedEphemeralCertificate, AddressFamily.InterNetwork, cts.Token);
        var server = new IPEndPoint(IPAddress.Loopback, listener.LocalEndPoint.Port);
        using var relay = new UdpClient(new IPEndPoint(IPAddress.Loopback, 0));
        var relayEnd = (IPEndPoint)relay.Client.LocalEndPoint!;
        int toListener = 0, toConnector = 0;
        var pump = Task.Run(async () =>
        {
            IPEndPoint? client = null;
            try
            {
                while (!cts.IsCancellationRequested)
                {
                    var got = await relay.ReceiveAsync(cts.Token);
                    if (got.RemoteEndPoint.Equals(server))
                    {
                        if (got.Buffer.Length > toConnector) toConnector = got.Buffer.Length;
                        if (client is not null) await relay.SendAsync(got.Buffer, client, cts.Token);
                    }
                    else
                    {
                        if (got.Buffer.Length > toListener) toListener = got.Buffer.Length;
                        client = got.RemoteEndPoint;
                        await relay.SendAsync(got.Buffer, server, cts.Token);
                    }
                }
            }
            catch (Exception error) when (error is OperationCanceledException or SocketException
                                              or ObjectDisposedException) { }
        });

        const int Total = 4 * 1024 * 1024, Upload = 2 * 1024 * 1024;
        var accept = listener.AcceptConnectionAsync(cts.Token).AsTask();
        await using var connection = await QuicTunnel.ConnectAsync(0, relayEnd, cts.Token);
        await using var serverConnection = await accept;
        // 给 QUIC 留出试探包长的时间：不锁的话这 2 秒里它已经探到 1500 了
        await Task.Delay(TimeSpan.FromSeconds(2), cts.Token);
        var serverSide = Task.Run(async () =>
        {
            await using var s = await serverConnection.AcceptInboundStreamAsync(cts.Token);
            var upload = new byte[1 + Upload];
            await s.ReadExactlyAsync(upload, cts.Token);
            var chunk = new byte[64 * 1024];
            for (var sent = 0; sent < Total; sent += chunk.Length) await s.WriteAsync(chunk, cts.Token);
            s.CompleteWrites();
        });
        await using var stream = await connection.OpenOutboundStreamAsync(
            System.Net.Quic.QuicStreamType.Bidirectional, cts.Token);
        await stream.WriteAsync(new byte[1 + Upload], cts.Token);
        var buffer = new byte[64 * 1024];
        long received = 0;
        int n;
        while ((n = await stream.ReadAsync(buffer, cts.Token)) > 0) received += n;
        await serverSide;
        cts.Cancel();
        try { await pump; } catch { }

        var clientMtu = MsQuicMtu.Read(connection);
        var serverMtu = MsQuicMtu.Read(serverConnection);
        Check("发起侧连接的包长上限已锁定", clientMtu?.Max == MsQuicMtu.Mtu,
            clientMtu is { } c ? $"{c.Min}~{c.Max}" : "(读不到)");
        Check("监听侧连接的包长上限已锁定", serverMtu?.Max == MsQuicMtu.Mtu,
            serverMtu is { } s2 ? $"{s2.Min}~{s2.Max}" : "(读不到)");
        Check("4 MB 经 QUIC 传完", received == Total, received.ToString());
        // 两个方向分开看：服务器往玩家靠监听侧锁，玩家往服务器靠发起侧锁，互相替代不了
        Check($"监听侧发出的 UDP 包不超过 {MsQuicMtu.Mtu - 28} 字节",
            toConnector > 0 && toConnector <= MsQuicMtu.Mtu - 28, $"{toConnector} 字节");
        Check($"发起侧发出的 UDP 包不超过 {MsQuicMtu.Mtu - 28} 字节",
            toListener > 0 && toListener <= MsQuicMtu.Mtu - 28, $"{toListener} 字节");
    }

    private const int AgentMcDown = 0, AgentMcUp = 1, AgentPathDrop = 2;

    /// <summary>
    /// 回环上的假 agent。握手照真 agent 的流程走（不验签）；数据连接按 <paramref name="mode"/>：
    /// <see cref="AgentMcDown"/> 回 StreamReady、读完开场就关，真 agent 接本机 Minecraft 被拒时
    /// 就是这样；<see cref="AgentMcUp"/> 当 Minecraft 回状态；<see cref="AgentPathDrop"/> 连
    /// StreamReady 都不回就断，模拟中间路径断掉。
    /// </summary>
    private static async Task FakeAgentAsync(TcpListener listener, Func<int> mode, CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            TcpClient client;
            try { client = await listener.AcceptTcpClientAsync(ct); }
            catch (Exception error) when (error is OperationCanceledException or ObjectDisposedException
                                              or SocketException) { return; }
            _ = Task.Run(async () =>
            {
                using (client)
                {
                    try
                    {
                        var stream = client.GetStream();
                        var preamble = new byte[5];
                        await stream.ReadExactlyAsync(preamble, ct);
                        if (!TunnelProtocol.PreambleMatches(preamble)) return;
                        var (frame, _) = await TunnelProtocol.ReadFrameAsync(stream, ct);
                        if (frame == TunnelProtocol.Frame.Hello)
                        {
                            await TunnelProtocol.WriteFrameAsync(stream, TunnelProtocol.Frame.Welcome, new byte[32], ct);
                            await TunnelProtocol.ReadFrameAsync(stream, ct);
                            await TunnelProtocol.WriteFrameAsync(stream, TunnelProtocol.Frame.Welcome, new byte[8], ct);
                            while (true)
                            {
                                var (beat, _) = await TunnelProtocol.ReadFrameAsync(stream, ct);
                                if (beat == TunnelProtocol.Frame.Ping)
                                    await TunnelProtocol.WriteFrameAsync(stream, TunnelProtocol.Frame.Pong,
                                        ReadOnlyMemory<byte>.Empty, ct);
                            }
                        }
                        if (frame != TunnelProtocol.Frame.OpenStream) return;
                        if (mode() == AgentPathDrop) return;
                        await TunnelProtocol.WriteFrameAsync(stream, TunnelProtocol.Frame.StreamReady,
                            ReadOnlyMemory<byte>.Empty, ct);
                        if (mode() != AgentMcUp)
                        {
                            // 真 agent 也只读一次开场就去接 MC，读多少算多少
                            var head = new byte[256];
                            if (await stream.ReadAsync(head, ct) == 0) return;
                            // 正常收尾（FIN），等对面先关，别留着没读的字节把它变成 RST
                            client.Client.Shutdown(SocketShutdown.Send);
                            while (await stream.ReadAsync(head, ct) > 0) { }
                            return;
                        }
                        await MinecraftPing.ServeStandInAsync(stream, "fake-mc-online", "fake",
                            TimeSpan.FromSeconds(5), ct);
                    }
                    catch (Exception error) when (error is IOException or SocketException or InvalidDataException
                                                      or OperationCanceledException or ObjectDisposedException) { }
                }
            });
        }
    }

    /// <summary>经本地代理查一次状态；查不到就把异常当结果返回，让断言记失败而不是把自检整个带崩。</summary>
    private static async Task<string> MotdOrErrorAsync(IPEndPoint local)
    {
        try
        {
            var status = await MinecraftPing.QueryAsync(local, "127.0.0.1", TimeSpan.FromSeconds(20),
                CancellationToken.None);
            return status.Motd;
        }
        catch (Exception error) { return $"{error.GetType().Name}: {error.Message}"; }
    }

    /// <summary>在报告的那个线程上直接收下。Progress&lt;T&gt; 会丢到线程池里异步回调，断言时可能还没到。</summary>
    private sealed class SyncProgress(Action<string> report) : IProgress<string>
    {
        public void Report(string value) => report(value);
    }

    /// <summary>以游戏的身份发起登录，返回服务端（这里是本地代理）给的断开原因。</summary>
    private static async Task<string> LoginKickAsync(int port)
    {
        using var client = new TcpClient();
        await client.ConnectAsync(IPAddress.Loopback, port);
        var stream = client.GetStream();
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(10));

        static void VarInt(List<byte> sink, int value)
        {
            var v = (uint)value;
            while ((v & ~0x7Fu) != 0) { sink.Add((byte)((v & 0x7F) | 0x80)); v >>= 7; }
            sink.Add((byte)v);
        }
        static byte[] Packet(int id, List<byte> body)
        {
            var inner = new List<byte>();
            VarInt(inner, id);
            inner.AddRange(body);
            var framed = new List<byte>();
            VarInt(framed, inner.Count);
            framed.AddRange(inner);
            return framed.ToArray();
        }
        static async Task<int> ReadVarInt(Stream s, CancellationToken ct)
        {
            var result = 0;
            var one = new byte[1];
            for (var shift = 0; shift < 35; shift += 7)
            {
                await s.ReadExactlyAsync(one, ct);
                result |= (one[0] & 0x7F) << shift;
                if ((one[0] & 0x80) == 0) return result;
            }
            throw new InvalidDataException("VarInt 过长");
        }

        var handshake = new List<byte>();
        VarInt(handshake, MinecraftPing.ProtocolVersion);
        var host = Encoding.UTF8.GetBytes("127.0.0.1");
        VarInt(handshake, host.Length);
        handshake.AddRange(host);
        handshake.Add((byte)(port >> 8));
        handshake.Add((byte)port);
        VarInt(handshake, 2); // next state: login
        var loginStart = new List<byte>();
        var name = Encoding.UTF8.GetBytes("10000");
        VarInt(loginStart, name.Length);
        loginStart.AddRange(name);
        loginStart.AddRange(new byte[16]);
        await stream.WriteAsync(Packet(0x00, handshake), deadline.Token);
        await stream.WriteAsync(Packet(0x00, loginStart), deadline.Token);

        var length = await ReadVarInt(stream, deadline.Token);
        var body = new byte[length];
        await stream.ReadExactlyAsync(body, deadline.Token);
        using var reader = new MemoryStream(body);
        var packetId = await ReadVarInt(reader, deadline.Token);
        if (packetId != 0x00) return $"(包 ID 0x{packetId:x2})";
        var textLength = await ReadVarInt(reader, deadline.Token);
        var json = Encoding.UTF8.GetString(body, (int)reader.Position, textLength);
        using var document = JsonDocument.Parse(json);
        return document.RootElement.GetProperty("text").GetString() ?? "";
    }

    /// <summary>
    /// 单连接多路复用。
    ///
    /// 打洞出来的 TCP 只有一条，玩家的游戏连接、带宽探测、保温探活都得挤在上面，
    /// 所以分片交错、流号串台、大包切分这几处必须是对的。写错的表现是"偶尔卡一下"
    /// 或者"数据莫名其妙串了"，在游戏里几乎不可能复现，只能在这里拦住。
    /// </summary>
    private static void MuxStreams()
    {
        Console.WriteLine("单连接多路复用");

        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        using var clientTcp = new TcpClient();
        var connecting = clientTcp.ConnectAsync(IPAddress.Loopback, port);
        using var serverTcp = listener.AcceptTcpClient();
        connecting.GetAwaiter().GetResult();
        listener.Stop();

        var client = new MuxConnection(clientTcp.GetStream(), initiator: true);
        var server = new MuxConnection(serverTcp.GetStream(), initiator: false);

        // 服务端侧：接到流就原样回显，模拟 agent 把流接到 Minecraft 上
        _ = Task.Run(async () =>
        {
            while (true)
            {
                Stream accepted;
                try { accepted = await server.AcceptAsync(CancellationToken.None); }
                catch { return; }
                _ = Task.Run(async () =>
                {
                    var buffer = new byte[16 * 1024];
                    try
                    {
                        int read;
                        while ((read = await accepted.ReadAsync(buffer)) > 0)
                            await accepted.WriteAsync(buffer.AsMemory(0, read));
                    }
                    catch { }
                });
            }
        });

        static byte[] Pattern(int size, byte seed)
        {
            var data = new byte[size];
            for (var i = 0; i < size; i++) data[i] = (byte)(seed + i);
            return data;
        }

        static byte[] RoundTrip(Stream s, byte[] payload)
        {
            s.WriteAsync(payload).AsTask().Wait(TimeSpan.FromSeconds(20));
            var got = new byte[payload.Length];
            var filled = 0;
            while (filled < got.Length)
            {
                var task = s.ReadAsync(got.AsMemory(filled)).AsTask();
                if (!task.Wait(TimeSpan.FromSeconds(20))) break;
                if (task.Result <= 0) break;
                filled += task.Result;
            }
            return filled == got.Length ? got : [];
        }

        // 1) 小包往返
        var a = client.OpenAsync(CancellationToken.None).GetAwaiter().GetResult();
        var small = Pattern(64, 7);
        Check("小包能原样往返", RoundTrip(a, small).AsSpan().SequenceEqual(small));

        // 2) 超过单帧上限的大包：必须被切分再拼回，且顺序不能乱
        var big = Pattern(200 * 1024, 31);
        Check("大包切分后顺序不乱", RoundTrip(a, big).AsSpan().SequenceEqual(big),
            "单帧上限 32 KB，200 KB 会被切成多帧");

        // 3) 两条流并发：分片交错时不能串台
        var b = client.OpenAsync(CancellationToken.None).GetAwaiter().GetResult();
        var da = Pattern(40 * 1024, 100);
        var db = Pattern(40 * 1024, 200);
        var ta = Task.Run(() => RoundTrip(a, da));
        var tb = Task.Run(() => RoundTrip(b, db));
        Task.WaitAll([ta, tb], TimeSpan.FromSeconds(30));
        Check("并发两条流互不串台",
            ta.Result.AsSpan().SequenceEqual(da) && tb.Result.AsSpan().SequenceEqual(db));

        // 4) 关掉一条不影响另一条
        a.Dispose();
        var after = Pattern(1024, 55);
        Check("关掉一条流之后另一条还能用", RoundTrip(b, after).AsSpan().SequenceEqual(after));

        // 5) 底层连接断了，读要立刻结束而不是挂死
        b.Dispose();
        client.DisposeAsync().AsTask().Wait(TimeSpan.FromSeconds(5));
        // 期望是"立刻失败"而不是"挂住"，所以抛异常算通过，超时才算不通过。
        var ended = false;
        try
        {
            ended = server.AcceptAsync(CancellationToken.None).Wait(TimeSpan.FromSeconds(5));
        }
        catch (AggregateException) { ended = true; }
        Check("连接断开后 Accept 立刻结束而不是挂死", ended);
        server.DisposeAsync().AsTask().Wait(TimeSpan.FromSeconds(5));
    }

    /// <summary>
    /// 超时不等于关停。
    ///
    /// 这条用例对应一个真实故障：HttpClient 超时抛的 TaskCanceledException 是
    /// OperationCanceledException 的子类，被 <c>when (error is not OperationCanceledException)</c>
    /// 放过去之后直接冲出整个 while 循环，agent 的上报永久停摆到进程重启，而现场
    /// 看起来一切正常（进程活着、别的循环还在转）。
    /// </summary>
    private static void ShutdownVsTimeout()
    {
        Section("超时与关停的区分");

        using var live = new CancellationTokenSource();
        using var stopped = new CancellationTokenSource();
        stopped.Cancel();

        // HttpClient 的超时就是这个形状：TaskCanceledException，而令牌没被取消。
        var timeout = new TaskCanceledException("The request was canceled due to timeout.");
        Check("HTTP 超时不算关停（必须继续循环）",
            !Cancellation.IsShutdown(timeout, live.Token));

        Check("令牌取消时的取消异常算关停",
            Cancellation.IsShutdown(new OperationCanceledException(), stopped.Token));
        Check("令牌取消时的 TaskCanceled 也算关停",
            Cancellation.IsShutdown(timeout, stopped.Token));

        // 普通故障永远不算关停，哪怕正在关停途中也该被记下来而不是当成正常退出。
        Check("普通异常不算关停",
            !Cancellation.IsShutdown(new IOException("connection reset"), live.Token));
        Check("关停途中的普通异常仍不算关停",
            !Cancellation.IsShutdown(new IOException("connection reset"), stopped.Token));
    }

    /// <summary>
    /// 从各包长的压测结果里认出「路径 MTU 黑洞」。
    ///
    /// 这条对应一次真实故障：玩家那侧 1200 字节包过了（还丢 23%），1400 / 1452 / 1472
    /// 全部 0/300，而 QUIC 握手完会把报文往上探、探进黑洞，最终表现成一句含糊的
    /// 「带不动」。分界线 1228 &lt; 1300 &lt; 1428 正好卡在他机器上一块 MTU 1300 的隧道
    /// 网卡上。有这条判断，日志里就直接写出病因和该关什么，而不是让人自己去读直方图。
    /// </summary>
    private static void MtuDiagnosis()
    {
        Section("路径 MTU 黑洞的判定");

        static HoleLoadTest.RoundResult R(int size, int received) =>
            new(size, 300, received, TimeSpan.FromMilliseconds(250), received > 0 ? 0 : -1,
                received > 0 ? received - 1 : -1, new int[12]);

        // 真实故障的形状：小包过，1400 及以上全灭。
        var real = HoleLoadTest.DiagnoseMtu([R(1200, 231), R(1400, 0), R(1452, 0), R(1472, 0)]);
        Check("认出 MTU 黑洞", real is not null);
        Check("报出能过的最大包长 1200", real?.Contains("1200") == true);
        Check("报出链路上的字节数 1228（含 UDP+IP 头 28）", real?.Contains("1228") == true);
        Check("报出第一个过不去的包长 1400", real?.Contains("1400") == true);
        Check("给出该怀疑什么", real?.Contains("VPN") == true);

        // 四种都过 = 没问题，不能报警。
        Check("全通时不报 MTU 问题",
            HoleLoadTest.DiagnoseMtu([R(1200, 300), R(1400, 300), R(1452, 300), R(1472, 300)]) is null);

        // 一个都没收到 = 对端旧版本或洞本身不通，跟尺寸门槛无关，不能误报成 MTU。
        Check("全灭时不误报成 MTU 问题",
            HoleLoadTest.DiagnoseMtu([R(1200, 0), R(1400, 0), R(1452, 0), R(1472, 0)]) is null);

        // 交错（小包死、大包活）说明是丢包不是尺寸门槛，同样不能报。
        Check("结果交错时不报 MTU 问题",
            HoleLoadTest.DiagnoseMtu([R(1200, 0), R(1400, 280), R(1452, 0), R(1472, 290)]) is null);

        // 只有一轮时信息不足，宁可不说。
        Check("样本不足时不下结论",
            HoleLoadTest.DiagnoseMtu([R(1200, 300)]) is null);
    }

    /// <summary>
    /// 打洞分工：谁预测端口、用什么步长。
    ///
    /// 这一组用例存在的原因是一个查了很久的 bug：两侧都硬写 predictPorts: true 且步长
    /// 都是 1，于是命中方程无解，TCP 打洞 0/255 而两边日志都显示自己正常拨了几百次。
    /// 所以这里不只钉常量，连**方程本身**一起钉——见 SimultaneousOpenHasSolution。
    /// </summary>
    private static void PunchRoleAssignment()
    {
        Section("打洞分工（谁预测端口、步长多少）");

        // 对端地址相关：它公布的端口是它探反射器时的映射，不是它朝我们发包时的，
        // 所以必须预测。两侧都预测没问题——救命的是步长不同，不是"只一侧猜"。
        Check("对端对称：发起方预测",
            P2PTags.ShouldPredictPeerPorts(
                myMappingAddressDependent: true, peerEndpointIndependent: false, initiator: true));
        Check("对端对称：接受方也预测",
            P2PTags.ShouldPredictPeerPorts(
                myMappingAddressDependent: true, peerEndpointIndependent: false, initiator: false));

        // 对端端点无关：公布的端口就是真端口，照着连，预测纯属浪费拨号预算。
        Check("对端锥形：发起方不预测",
            !P2PTags.ShouldPredictPeerPorts(
                myMappingAddressDependent: true, peerEndpointIndependent: true, initiator: true));
        Check("对端锥形：接受方不预测",
            !P2PTags.ShouldPredictPeerPorts(
                myMappingAddressDependent: false, peerEndpointIndependent: true, initiator: false));

        // 步长必须不相等，否则命中方程的分母是 0。
        Equal("发起方步长 1", "1", P2PTags.PredictStep(initiator: true).ToString());
        Equal("接受方步长 2", "2", P2PTags.PredictStep(initiator: false).ToString());
        Check("两侧步长不相等",
            P2PTags.PredictStep(true) != P2PTags.PredictStep(false));

        SimultaneousOpenHasSolution();

        // 前缀是两侧交换类型的唯一载体，标错等于分工错。
        Equal("对称型用 t: 前缀", "t:", P2PTags.TcpTagFor(addressDependent: true));
        Equal("锥形用 te: 前缀", "te:", P2PTags.TcpTagFor(addressDependent: false));

        Check("te: 解出端点无关",
            P2PTags.TryStripTcp("te:1.2.3.4:5678", out var text1, out var eim1)
            && text1 == "1.2.3.4:5678" && eim1);
        Check("t: 解出地址相关",
            P2PTags.TryStripTcp("t:1.2.3.4:5678", out var text2, out var eim2)
            && text2 == "1.2.3.4:5678" && !eim2);
        // 老版本只发 "t:"，没有类型信息。按最坏情况算是安全方向：宁可多预测，
        // 也不要照着一个不可靠的端口去打还以为自己打过了。
        Check("无前缀不算 TCP 候选",
            !P2PTags.TryStripTcp("1.2.3.4:5678", out var text3, out var eim3)
            && text3 == "1.2.3.4:5678" && !eim3);
    }

    /// <summary>
    /// 把 TCP 同时开的命中条件当成方程解一遍，钉住"步长不等才有解"。
    ///
    /// 两侧对表时各自看到自己的外部端口 p_A/p_B，开打时第 0 次 connect 实际拿到
    /// a=p_A+δ_A、b=p_B+δ_B，各以步长 s 往上拨。在地址+端口相关过滤下，命中要求
    /// 存在 i,j 同时满足 <c>1+s_A·i = δ_B+j</c> 和 <c>1+s_B·j = δ_A+i</c>。
    ///
    /// 这里直接暴力枚举窗口，不用解析解——用例要验的是"这个条件在我们的参数下能不能
    /// 被满足"，枚举比化简更不容易写错，也更能看出窗口够不够。
    /// </summary>
    private static void SimultaneousOpenHasSolution()
    {
        // δ = 背景端口消耗 20.4 个/秒 × 对表提前 900ms ≈ 18。
        const int drift = 18;
        const int window = 96;

        static bool Solvable(int stepA, int stepB, int driftA, int driftB, int window)
        {
            for (var i = 0; i < window; i++)
                for (var j = 0; j < window; j++)
                    if (1 + stepA * i == driftB + j && 1 + stepB * j == driftA + i)
                        return true;
            return false;
        }

        Check("两侧步长都是 1 时无解（这就是 0/255 的根因）",
            !Solvable(1, 1, drift, drift, window));
        Check("步长 1/2 时有解",
            Solvable(1, 2, drift, drift, window));
        // 窗口要撑得住线路更忙的时候。δ=30 相当于 1.5 秒漂移。
        Check("δ=30 时窗口 96 仍有解",
            Solvable(1, 2, 30, 30, window));
        // 两侧 δ 不一样也要有解——各自线路的繁忙程度本来就不同。
        Check("两侧 δ 不等（12 与 25）仍有解",
            Solvable(1, 2, 12, 25, window));
        // 反证窗口不是白给的：窗口太小就会打空。
        Check("窗口 16 在 δ=18 下无解（原来的 NarrowAhead 就是 16）",
            !Solvable(1, 2, drift, drift, 16));
    }

    /// <summary>
    /// 洞压测的包：参数编码要能原样回来，数据包要能校验，改一个字节就必须认不出来。
    ///
    /// 压测包是唯一长度可变的打洞包，所以它走单独的解析路径。这里盯住的是那条路径
    /// 别把定长包的校验放松掉——那是挡住端口扫描者的第一道门。
    /// </summary>
    private static void HoleLoadPackets()
    {
        Section("洞压测包");

        var plan = PunchProtocol.EncodeLoadPlan(1400, 300, 1536);
        var (size, count, rate) = PunchProtocol.DecodeLoadPlan(plan);
        Equal("参数往返 · 包长", "1400", size.ToString());
        Equal("参数往返 · 包数", "300", count.ToString());
        Equal("参数往返 · 速率", "1536", rate.ToString());

        var secret = PunchProtocol.DeriveSecret("selftest-token");
        const ulong session = 0x0102_0304_0506_0708UL;
        var packet = PunchProtocol.BuildLoad(session, 12345, secret, 1400);
        Equal("压测包长度就是要求的长度", "1400", packet.Length.ToString());

        var parsed = PunchProtocol.ParseLoad(packet, secret);
        Check("能解出会话和序号",
            parsed is { } ok && ok.Session == session && ok.Sequence == 12345);

        // 填充不进 MAC（几百个 1.4KB 全算 HMAC 会把压测本身变成 CPU 瓶颈），
        // 但**头部**必须一个字节都动不了。
        var tampered = (byte[])packet.Clone();
        tampered[13] ^= 0x01;
        Check("改了序号就认不出", PunchProtocol.ParseLoad(tampered, secret) is null);
        Check("换密钥就认不出",
            PunchProtocol.ParseLoad(packet, PunchProtocol.DeriveSecret("another")) is null);
        Check("短于定长包直接丢",
            PunchProtocol.ParseLoad(packet.AsSpan(0, PunchProtocol.PacketSize - 1), secret) is null);

        // 普通定长包不能被当成压测包，反之亦然——两条解析路径不能互相串。
        var punch = PunchProtocol.Build(PunchProtocol.Kind.Punch, session, 1, secret);
        Check("普通打洞包不会被当成压测包", PunchProtocol.ParseLoad(punch, secret) is null);
        Check("压测包不会被定长解析接受",
            PunchProtocol.Parse(packet, secret) is null);

        // 请求/回报走的是定长包，必须能被 Parse 接住，否则 agent 侧收包循环会丢掉它们。
        var request = PunchProtocol.Build(PunchProtocol.Kind.LoadRequest, session, plan, secret);
        Check("压测请求能被定长解析接受",
            PunchProtocol.Parse(request, secret) is { Type: PunchProtocol.Kind.LoadRequest } p
            && p.Nonce == plan);
        var report = PunchProtocol.Build(PunchProtocol.Kind.LoadReport, session, plan, secret);
        Check("压测回报能被定长解析接受",
            PunchProtocol.Parse(report, secret) is { Type: PunchProtocol.Kind.LoadReport });
        // LoadDone 是"对端可以立刻放开端口"的信号。它解不出来的后果不是少一行日志，
        // 而是应答方要等满超时，对端的第一个 QUIC Initial 被丢、再等约一秒重传。
        var done = PunchProtocol.Build(PunchProtocol.Kind.LoadDone, session, 0, secret);
        Check("压测收工信号能被解析",
            PunchProtocol.Parse(done, secret) is { Type: PunchProtocol.Kind.LoadDone });
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

        // Winner 必须指向真正收到 Ack 的那个 socket。
        //
        // 本机映射不可预测（sweepPorts=false）那一侧会多开一批诱饵端口一起打，赢的
        // 可能不是主 socket。NAT 映射按本地端口记账，上层如果照主端口去建 QUIC，
        // 就是把刚打好的洞扔掉换一个没打过的端口——现象是"打洞成功但连不上"，
        // 极难查，所以这里钉死。
        Check("A 报出了赢的那个 socket", ta.Result.Winner is not null);
        Check("B 报出了赢的那个 socket", tb.Result.Winner is not null);
        var bLocal = (tb.Result.Winner?.LocalEndPoint as IPEndPoint)?.Port ?? -1;
        Check("B（多开诱饵的那一侧）赢的端口确实在本机上",
            bLocal > 0, $"拿到的本地端口是 {bLocal}");
        Check("A（不开诱饵的那一侧）赢的就是它自己那个 socket",
            ReferenceEquals(ta.Result.Winner, a),
            "A 没开诱饵，赢的只能是主 socket");
    }

    /// <summary>
    /// 2026-09-24 端到端实测（蜂窝 × 家宽 × CGNAT 服务端）里修掉的几处，各钉一条。
    /// 每一条都对应一次真实失败，注释里写的是当时的现象。
    /// </summary>
    private static async Task P2PHardening()
    {
        Section("P2P 实测修复");
        var secret = PunchProtocol.DeriveSecret("selftest-token");
        const ulong session = 0x5EED_0924_0000_0001UL;

        // 1. 对端的压测请求 = 对端已经判定打通，而且指明了它真正在用的那一对端口。
        //    实测：两边各自认定的端口对不是同一对，预热 6 次里 5 次 0/300。
        {
            using var mine = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
            using var peer = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
            mine.Bind(new IPEndPoint(IPAddress.Loopback, 0));
            peer.Bind(new IPEndPoint(IPAddress.Loopback, 0));
            // 候选故意给一个没人听的端口：唯一能让它成功的就是那个压测请求。
            var dead = new IPEndPoint(IPAddress.Loopback, ReserveClosedPort());
            var plan = PunchProtocol.EncodeLoadPlan(1200, 50, 1024);
            var punch = UdpPuncher.PunchAsync(mine, [dead], session, secret,
                DateTimeOffset.UtcNow, TimeSpan.FromSeconds(3), sweepPorts: false, null,
                CancellationToken.None);
            await Task.Delay(150);
            await peer.SendToAsync(PunchProtocol.Build(PunchProtocol.Kind.LoadRequest, session, plan, secret),
                SocketFlags.None, mine.LocalEndPoint!);
            var outcome = await punch.WaitAsync(TimeSpan.FromSeconds(10));
            Check("对端的压测请求算打通", outcome.Success, $"Peer={outcome.Peer}");
            Check("以压测请求的来源为准", Equals(outcome.Peer, peer.LocalEndPoint),
                $"{outcome.Peer} vs {peer.LocalEndPoint}");
            Check("压测请求被原样交出来，应答方不必等重发", outcome.PeerLoadRequest == plan);
            // 对端已经收工，不该再等 Ack 宽限（上限 2 秒）。
            Check("对端已收工时不等 Ack 宽限", outcome.Elapsed < TimeSpan.FromSeconds(1.5),
                $"{outcome.Elapsed.TotalMilliseconds:0} ms");
            foreach (var decoy in new[] { outcome.Winner })
                if (decoy is not null && !ReferenceEquals(decoy, mine)) decoy.Dispose();
        }

        // 2. 应答方按请求的实际来源回，而不是按打洞时记下的那个地址。
        {
            using var responder = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
            using var requester = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
            responder.Bind(new IPEndPoint(IPAddress.Loopback, 0));
            requester.Bind(new IPEndPoint(IPAddress.Loopback, 0));
            var stale = new IPEndPoint(IPAddress.Loopback, ReserveClosedPort());
            var respond = HoleLoadTest.RespondAsync(responder, stale, session, secret, null,
                CancellationToken.None);
            var rounds = await HoleLoadTest.RequestAsync(requester, (IPEndPoint)responder.LocalEndPoint!,
                session, secret, [(1200, 50, 1024)], null, CancellationToken.None);
            await respond.WaitAsync(TimeSpan.FromSeconds(10));
            Check("应答方按实际来源回（打洞时记的地址已过时）",
                rounds.Count == 1 && rounds[0].Received == 50,
                rounds.Count == 0 ? "没有结果" : rounds[0].Describe());
        }

        // 3. 一轮整轮丢光时，请求方要在"按计划早该推完"之后很快收手，而不是干等 1.2 秒；
        //    而应答方要等得住它问下一轮。实测：前者干等、后者 250ms 就收摊，后面几轮全
        //    对着空气问，还得出一条"路径 MTU 1200~1250"的假结论。
        {
            using var requester = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
            requester.Bind(new IPEndPoint(IPAddress.Loopback, 0));
            var nobody = new IPEndPoint(IPAddress.Loopback, ReserveClosedPort());
            var watch = System.Diagnostics.Stopwatch.StartNew();
            var rounds = await HoleLoadTest.RequestAsync(requester, nobody, session, secret,
                [(1200, 50, 1024)], null, CancellationToken.None);
            Check("对端不应答时一轮很快收手", watch.Elapsed < TimeSpan.FromSeconds(1),
                $"{watch.ElapsedMilliseconds} ms，轮数 {rounds.Count}");
        }

        // 4. 出口探测：所有目标一起发一起收；有个端口不回话时靠安静期收手，而不是
        //    每个都干等 600ms。实测蜂窝上原来这一格 2.2~5.5 秒。
        {
            using var reflectorA = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
            using var reflectorB = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
            reflectorA.Bind(new IPEndPoint(IPAddress.Loopback, 0));
            reflectorB.Bind(new IPEndPoint(IPAddress.Loopback, 0));
            using var stop = new CancellationTokenSource();
            var echoA = FakeReflectorAsync(reflectorA, stop.Token);
            var echoB = FakeReflectorAsync(reflectorB, stop.Token);
            var portA = ((IPEndPoint)reflectorA.LocalEndPoint!).Port;
            var portB = ((IPEndPoint)reflectorB.LocalEndPoint!).Port;

            using var probe = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
            probe.Bind(new IPEndPoint(IPAddress.Loopback, 0));
            var watch = System.Diagnostics.Stopwatch.StartNew();
            var all = await NatDiscovery.DiscoverAsync(probe, "127.0.0.1", [portA, portB], 6,
                CancellationToken.None);
            var allAnswered = watch.Elapsed;
            Check("所有反射器都回话时一个往返就收工", allAnswered < TimeSpan.FromMilliseconds(400),
                $"{allAnswered.TotalMilliseconds:0} ms");
            Check("探到了出口", all.Candidates.Count == 1, string.Join(",", all.Candidates));

            using var probe2 = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
            probe2.Bind(new IPEndPoint(IPAddress.Loopback, 0));
            watch.Restart();
            var partial = await NatDiscovery.DiscoverAsync(probe2, "127.0.0.1",
                [portA, ReserveClosedPort(), portB], 6, CancellationToken.None);
            Check("有反射器端口不回话时照样收得回来", partial.Candidates.Count == 1);
            Check("不回话的端口不会拖满上限", watch.Elapsed < TimeSpan.FromMilliseconds(1300),
                $"{watch.ElapsedMilliseconds} ms");
            stop.Cancel();
            try { await Task.WhenAll(echoA, echoB); } catch { }
        }

        // 5. 双向对拷：一个方向结束要把半关闭传过去，不能两边都干等到对面超时。
        //    实测：玩家断开后服务端那头要等 Minecraft 30 秒超时才放人，客户端这条转发也
        //    一直占着连接表，后台打好的 P2P 永远切不过去。
        {
            var (gameSide, proxyLocal) = await LoopbackPairAsync();
            var (proxyRemote, serverSide) = await LoopbackPairAsync();
            using (gameSide) using (proxyLocal) using (proxyRemote) using (serverSide)
            {
                var bridge = StreamBridge.RunAsync(proxyLocal.GetStream(), proxyRemote.GetStream(),
                    TimeSpan.FromSeconds(5), CancellationToken.None);
                gameSide.Client.Shutdown(SocketShutdown.Send);   // 游戏那头断开
                var buffer = new byte[16];
                var read = await serverSide.GetStream().ReadAsync(buffer).AsTask()
                    .WaitAsync(TimeSpan.FromSeconds(2));
                Check("游戏断开后服务端那头立刻读到 EOF", read == 0);
                serverSide.Client.Shutdown(SocketShutdown.Send);  // 服务端随之收尾
                var watch = System.Diagnostics.Stopwatch.StartNew();
                await bridge.WaitAsync(TimeSpan.FromSeconds(10));
                Check("两头都收尾后转发立即结束，不等宽限", watch.Elapsed < TimeSpan.FromSeconds(1),
                    $"{watch.ElapsedMilliseconds} ms");
            }

            var (game2, local2) = await LoopbackPairAsync();
            var (remote2, server2) = await LoopbackPairAsync();
            using (game2) using (local2) using (remote2) using (server2)
            {
                var watch = System.Diagnostics.Stopwatch.StartNew();
                var bridge = StreamBridge.RunAsync(local2.GetStream(), remote2.GetStream(),
                    TimeSpan.FromMilliseconds(300), CancellationToken.None);
                game2.Client.Shutdown(SocketShutdown.Send);
                // 服务端这次装死、不收尾：宽限到了也必须结束。
                await bridge.WaitAsync(TimeSpan.FromSeconds(10));
                Check("对端不收尾时宽限一到就拆", watch.Elapsed < TimeSpan.FromSeconds(2),
                    $"{watch.ElapsedMilliseconds} ms");
            }
        }

        // 6. 一条 TCP 线路都不通时代理照样起得来——打洞不依赖任何 TCP 线路。
        //    实测：中转挂了、玩家又没有 IPv6，选路一抛，打洞连试的机会都没有。
        {
            var servers = new[]
            {
                new ServerEntry
                {
                    Name = "全不通", Primary = true, Host = "127.0.0.1", Port = ReserveClosedPort(),
                    Routes =
                    [
                        new RouteCandidate
                        {
                            Id = "dead-relay", Kind = RouteKind.Relay, Host = "127.0.0.1",
                            Port = ReserveClosedPort(), ProbeTimeoutMs = 300,
                        },
                    ],
                },
            };
            var threw = false;
            try { await using var _ = await MinecraftRouteProxy.StartAsync(servers, null, CancellationToken.None); }
            catch (InvalidOperationException) { threw = true; }
            Check("不允许无线路时照旧报错", threw);
            await using var proxy = await MinecraftRouteProxy.StartAsync(servers, null,
                CancellationToken.None, allowNoRoute: true);
            Check("允许无线路时代理照样起来", proxy.Selection.Selected is null
                                                && proxy.Selection.Reachable.Count == 0);
        }
    }

    /// <summary>回环上的假反射器：把来源地址按真反射器的格式回过去。</summary>
    private static async Task FakeReflectorAsync(Socket socket, CancellationToken ct)
    {
        var buffer = new byte[512];
        const string magic = "MUXI-REFLECT/1 ";
        while (!ct.IsCancellationRequested)
        {
            SocketReceiveFromResult got;
            try
            {
                got = await socket.ReceiveFromAsync(buffer, SocketFlags.None,
                    new IPEndPoint(IPAddress.Any, 0), ct);
            }
            catch (OperationCanceledException) { return; }
            catch (SocketException) { continue; }
            var text = Encoding.ASCII.GetString(buffer, 0, got.ReceivedBytes);
            if (!text.StartsWith(magic, StringComparison.Ordinal)) continue;
            var from = (IPEndPoint)got.RemoteEndPoint;
            var reply = $"{{\"nonce\":\"{text[magic.Length..]}\",\"ip\":\"{from.Address}\",\"port\":{from.Port}}}";
            try { await socket.SendToAsync(Encoding.ASCII.GetBytes(reply), SocketFlags.None, from, ct); }
            catch (Exception error) when (error is SocketException or OperationCanceledException) { }
        }
    }

    private static async Task<(TcpClient Client, TcpClient Server)> LoopbackPairAsync()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        try
        {
            var client = new TcpClient();
            var accept = listener.AcceptTcpClientAsync();
            await client.ConnectAsync(IPAddress.Loopback, ((IPEndPoint)listener.LocalEndpoint).Port);
            return (client, await accept);
        }
        finally { listener.Stop(); }
    }

    /// <summary>
    /// TCP 打洞的收敛。
    ///
    /// 两边同时拨号会打通**两条**连接（我拨通你一条、你拨通我一条），真实链路上
    /// 因为端口预测还会更多。各留各的就谁也读不到谁——实测表现是隧道"建立成功"、
    /// 一开流立刻 EOF，极难查：两边日志都写着打洞成功。
    ///
    /// 这里把这个局面直接构造出来：A、B 互相知道对方端口，两条必然都通。再塞一个
    /// 外人抢在正主前面连进来，验证它既不会被选中、也不会把正主挤掉。
    /// </summary>
    private static void TcpPunchConverge()
    {
        Console.WriteLine("TCP 打洞收敛");
        var secret = PunchProtocol.DeriveSecret("selftest-token");
        const ulong session = 0x5EC0_1234_5678_9ABCUL;

        var pa = ReserveClosedPort();
        var pb = ReserveClosedPort();
        while (pb == pa) pb = ReserveClosedPort();

        var punchAt = DateTimeOffset.UtcNow.AddMilliseconds(500);
        var budget = TimeSpan.FromSeconds(5);

        // 端口预测在回环上没有意义（邻近端口是别的进程），这里只验收敛，关掉。
        var ta = TcpPuncher.PunchAsync(
            pa, [new IPEndPoint(IPAddress.Loopback, pb)], session, secret, punchAt, budget,
            predictPorts: false, initiator: true, narrowWindow: false, null, CancellationToken.None);
        var tb = TcpPuncher.PunchAsync(
            pb, [new IPEndPoint(IPAddress.Loopback, pa)], session, secret, punchAt, budget,
            predictPorts: false, initiator: false, narrowWindow: false, null, CancellationToken.None);

        // 监听在等待约定时刻之前就起来了，所以外人现在就能连上。发满一帧垃圾：
        // MAC 对不上就该被丢掉，不能占住名额——否则扫到端口的人就能让打洞永远不成。
        TcpClient? stranger = null;
        try
        {
            Thread.Sleep(100);
            stranger = new TcpClient();
            stranger.Connect(IPAddress.Loopback, pb);
            stranger.GetStream().Write(new byte[PunchProtocol.PacketSize]);
        }
        catch { }

        Task.WaitAll([ta, tb], TimeSpan.FromSeconds(30));
        try { stranger?.Dispose(); } catch { }

        Check("两边都打通", ta.Result.Success && tb.Result.Success,
            $"A={ta.Result.Success} B={tb.Result.Success}");
        if (!ta.Result.Success || !tb.Result.Success) return;

        var sa = ta.Result.Socket!;
        var sb = tb.Result.Socket!;
        var la = (IPEndPoint)sa.LocalEndPoint!;
        var ra = (IPEndPoint)sa.RemoteEndPoint!;
        var lb = (IPEndPoint)sb.LocalEndPoint!;
        var rb = (IPEndPoint)sb.RemoteEndPoint!;
        Check("两边守的是同一条连接", la.Port == rb.Port && ra.Port == lb.Port,
            $"A {la}<->{ra}，B {lb}<->{rb}");

        // 真正的判据：数据能从 A 的逻辑流走到 B。各守一条时这一步必然 EOF。
        var passed = false;
        var detail = "";
        try
        {
            var muxA = new MuxConnection(new NetworkStream(sa, ownsSocket: true), initiator: true);
            var muxB = new MuxConnection(new NetworkStream(sb, ownsSocket: true), initiator: false);
            try
            {
                var open = muxA.OpenAsync(CancellationToken.None);
                var accept = muxB.AcceptAsync(CancellationToken.None);
                if (!Task.WaitAll([open, accept], TimeSpan.FromSeconds(10)))
                    throw new TimeoutException("开流/接流超时");

                var payload = new byte[4096];
                for (var i = 0; i < payload.Length; i++) payload[i] = (byte)(i * 77 + 13);
                open.Result.Write(payload);
                open.Result.Flush();
                var got = new byte[payload.Length];
                accept.Result.ReadExactly(got);
                passed = got.AsSpan().SequenceEqual(payload);
            }
            finally
            {
                muxA.DisposeAsync().AsTask().Wait(TimeSpan.FromSeconds(5));
                muxB.DisposeAsync().AsTask().Wait(TimeSpan.FromSeconds(5));
            }
        }
        catch (Exception error)
        {
            detail = (error.InnerException ?? error).Message;
        }
        Check("打洞出来的连接上能跑通复用流", passed, detail);
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
