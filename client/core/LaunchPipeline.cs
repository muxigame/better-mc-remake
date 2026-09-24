using BatterMC.Protocol;

namespace BatterMC.Core;

public sealed class PipelineContext
{
    public required LauncherPaths Paths { get; init; }
    public required LauncherSettings Settings { get; init; }
    public required LocalState State { get; init; }
    public PackManifest? Manifest { get; set; }
    public VersionJson? Version { get; set; }
    public JavaInstall? Java { get; set; }
}

/// <summary>
/// 从「点开始」到「游戏窗口出现」之间的全部步骤，按顺序串起来。
///
/// 顺序是有讲究的：版本 JSON 本身就是整合包文件之一，所以必须先同步整合包，
/// 才能读它、才知道要补哪些 Minecraft 运行库。
/// </summary>
public sealed class LaunchPipeline
{
    private readonly PipelineContext _ctx;
    private readonly Downloader _downloader;

    public LaunchPipeline(PipelineContext ctx, Downloader downloader)
    {
        _ctx = ctx;
        _downloader = downloader;
    }

    public event Action<SyncStatus>? Status;
    public event Action<LauncherRelease>? LauncherUpdateAvailable;

    private void Report(string phase, string detail = "", double fraction = -1)
        => Status?.Invoke(new SyncStatus(phase, detail, fraction, 0, 0));

    /// <summary>做完一切准备工作，返回可以直接启动的上下文。</summary>
    public async Task PrepareAsync(bool forceFullVerify, CancellationToken ct)
    {
        var paths = _ctx.Paths;
        var settings = _ctx.Settings;
        var state = _ctx.State;

        paths.EnsureCreated();

        // 1. 清单
        Report("连接更新服务器", settings.UpdateBaseUrl);
        var sync = new SyncEngine(paths, state, settings, _downloader);
        var manifest = await sync.FetchManifestAsync(settings.UpdateBaseUrl, ct).ConfigureAwait(false);

        // Minecraft 本体走我们自己的镜像。上游在国内太慢，还有玩家直接连不上。
        _downloader.Mirror = string.IsNullOrWhiteSpace(sync.LastMirrorBaseUrl)
            ? null : new DownloadMirror(sync.LastMirrorBaseUrl!);
        if (_downloader.Mirror is not null) Log.Info($"Minecraft 本体镜像：{_downloader.Mirror.Base}");
        else Log.Info("未下发镜像地址，Minecraft 本体直连 Mojang / NeoForge");
        _ctx.Manifest = manifest;

        // 2. 在任何游戏下载/写入之前检查客户端支持策略，不能仅依赖 UI 禁用按钮。
        if (manifest.Launcher is not null &&
            SelfUpdater.IsNewer(manifest.Launcher.Version, GameLauncher.ThisVersion()))
        {
            Log.Info($"启动器有新版本：{manifest.Launcher.Version}");
            try { LauncherUpdateAvailable?.Invoke(manifest.Launcher); } catch { }
            if (SelfUpdater.IsRequired(manifest.Launcher, GameLauncher.ThisVersion()))
                throw new InvalidOperationException($"当前客户端已停止支持，请先更新到 {manifest.Launcher.Version}。");
        }

        // 2.5 光影：先看玩家现在实际在用什么。
        //     必须排在同步之前——iris.properties 是 Seed 文件，同步可能把它整份重投成
        //     出厂值（出厂是开着 Better MC - Low 的），重投之后就分不清是玩家自己关的
        //     还是刚被重投的，结果就是玩家在游戏里关掉光影、下次启动又被打开。
        if (!string.IsNullOrWhiteSpace(state.InstalledPackVersion)
            && File.Exists(paths.ResolveGameFile(ShaderPresets.ConfigPath)))
        {
            var adopted = ShaderPresets.AdoptPlayerChoice(settings.ShaderPack, ShaderPresets.Read(paths));
            if (adopted is not null)
            {
                Log.Info($"光影改用玩家自己的选择：{Describe(settings.ShaderPack)} -> {Describe(adopted)}");
                settings.ShaderPack = adopted;
                settings.Save(paths.SettingsFile);
            }
        }

        // 3. 整合包文件
        if (forceFullVerify)
        {
            Log.Info("强制全量校验：清空哈希缓存");
            state.Hashes.Clear();
        }

        if (settings.SkipVerify && !forceFullVerify)
        {
            Log.Warn("玩家开启了「跳过校验」，本次不检查整合包文件");
        }
        else
        {
            var plan = sync.Plan(manifest, new InlineProgress<SyncStatus>(s => Status?.Invoke(s)), ct);
            if (!plan.IsEmpty)
            {
                Report("同步整合包",
                    $"下载 {plan.Downloads.Count} 个（{SyncEngine.Human(plan.Bytes)}），删除 {plan.Deletions.Count} 个");
                await sync.ApplyAsync(plan, new InlineProgress<SyncStatus>(s => Status?.Invoke(s)), ct).ConfigureAwait(false);
            }
            else
            {
                Report("整合包已是最新", $"版本 {manifest.Pack.Version}");
            }
        }

        // 4. 版本 JSON
        var versionJsonPath = paths.ResolveGameFile(manifest.Minecraft.VersionJson);
        if (!File.Exists(versionJsonPath))
            throw new FileNotFoundException(
                $"版本文件没同步下来：{manifest.Minecraft.VersionJson}。检查更新服务器上的清单是否包含它。");

        Report("读取版本信息", manifest.Minecraft.VersionId);
        var version = VersionJson.Load(versionJsonPath);
        _ctx.Version = version;
        Log.Info($"版本 {version.Id}，主类 {version.MainClass}，{version.Libraries.Count} 个库");

        // 5. Minecraft 本体（官方 CDN）
        var vanillaPlan = await VanillaInstaller
            .PlanAsync(version, paths, state, _downloader, new InlineProgress<string>(m => Report("校验 Minecraft 本体", m)), ct)
            .ConfigureAwait(false);

        if (!vanillaPlan.IsEmpty)
        {
            // 这一批和整合包没关系，是 Minecraft 游戏本体。不写清楚玩家会以为在重下整合包。
            Report("下载 Minecraft 本体",
                $"游戏资源与运行库 {vanillaPlan.Items.Count} 个文件（{SyncEngine.Human(vanillaPlan.Bytes)}）");
            await _downloader.DownloadAllAsync(vanillaPlan.Items,
                new InlineProgress<DownloadProgress>(p => Status?.Invoke(new SyncStatus(
                    "下载 Minecraft 本体",
                    $"{p.FilesDone}/{p.FilesTotal}  {SyncEngine.Human(p.BytesDone)} / {SyncEngine.Human(p.BytesTotal)}",
                    p.BytesTotal > 0 ? Math.Clamp((double)p.BytesDone / p.BytesTotal, 0, 1) : -1,
                    p.BytesDone, p.BytesTotal))), ct).ConfigureAwait(false);
        }

        // 6. Java —— 必须排在 NeoForge 安装器之前，安装器本身就是个 Java 程序
        Report("查找 Java", $"需要 Java {manifest.Java.Major}");
        var java = JavaManager.Select(paths, settings, manifest.Java, out var reason);
        if (java is null)
        {
            Log.Warn(reason);
            Report("下载 Java", reason);
            java = await JavaManager.DownloadAsync(paths, manifest.Java, _downloader,
                new InlineProgress<DownloadProgress>(p => Status?.Invoke(new SyncStatus(
                    "下载 Java",
                    $"{SyncEngine.Human(p.BytesDone)} / {SyncEngine.Human(p.BytesTotal)}",
                    p.BytesTotal > 0 ? Math.Clamp((double)p.BytesDone / p.BytesTotal, 0, 1) : -1,
                    p.BytesDone, p.BytesTotal))), ct).ConfigureAwait(false);
            // 记下来，下次直接用
            settings.JavaPath = java.Path;
            settings.Save(paths.SettingsFile);
        }
        _ctx.Java = java;
        Log.Info($"使用 {java}");

        // 7. NeoForge 本体。这几个文件下不到，只能在本机由官方安装器生成，
        //    所以第一次安装会多花一两分钟。缺了的话每个模组都会报缺依赖。
        if (!NeoForgeInstaller.IsInstalled(version, paths, out var missingArtifacts))
        {
            Log.Info($"NeoForge 尚未安装（缺 {missingArtifacts}）");
            await NeoForgeInstaller.InstallAsync(
                version, paths, java, manifest.Minecraft.InstallerUrl, _downloader,
                new InlineProgress<DownloadProgress>(p => Status?.Invoke(new SyncStatus(
                    "安装 NeoForge",
                    $"{SyncEngine.Human(p.BytesDone)} / {SyncEngine.Human(p.BytesTotal)}",
                    p.BytesTotal > 0 ? Math.Clamp((double)p.BytesDone / p.BytesTotal, 0, 1) : -1,
                    p.BytesDone, p.BytesTotal))),
                new InlineProgress<string>(m => Report("安装 NeoForge", m, -1)),
                ct).ConfigureAwait(false);
        }

        // 8. 硬配置
        if (manifest.Overlays.Count > 0)
        {
            Report("应用服务器配置", $"{manifest.Overlays.Count} 个文件");
            var results = ConfigOverlay.ApplyAll(manifest.Overlays, paths, state);
            var failed = results.Where(r => r.Error is not null).ToList();
            foreach (var f in failed) Log.Warn($"硬配置失败 {f.Path}：{f.Error}");
        }


        // 8.5 光影预设。必须排在硬配置后面：iris.properties 是 Seed 文件，
        //     服务端发新版本会把它整份重投，玩家选的光影会被一起冲掉。
        //     启动器记着玩家点名要的那个预设，同步完再写回去。
        if (settings.ShaderPack is null)
        {
            // 第一次见到这个玩家：全新安装按默认值（不开光影），
            // 老玩家则原样收编他现在用的光影，不动他的选择。
            var firstInstall = string.IsNullOrWhiteSpace(state.InstalledPackVersion);
            settings.ShaderPack = firstInstall
                ? ShaderPresets.DefaultPack
                : ShaderPresets.Read(paths).AsSettingValue();
            settings.Save(paths.SettingsFile);
        }
        ShaderPresets.Apply(paths, settings.ShaderPack);

        // 全屏同理：命令行的 --fullscreen 只能开不能关，options.txt 里的值才是
        // 游戏真正认的那个，启动前按启动器里的开关写一次（Sodium Extras 那份一起写）。
        GameOptions.ApplyFullscreen(paths, settings.Fullscreen);

        // 9. 服务器列表
        if (manifest.Servers.Count > 0)
        {
            try { ServersDat.EnsureServers(paths, manifest.Servers); }
            catch (Exception ex) { Log.Warn($"servers.dat 写入失败：{ex.Message}"); }
        }

        // Download completion is not installation completion. Only mark the
        // version ready after the runtime, loader and final setup succeeded.
        if (!settings.SkipVerify || forceFullVerify)
        {
            state.InstalledPackVersion = manifest.Pack.Version;
            state.LastSync = DateTimeOffset.Now;
        }
        state.Save(paths.StateFile);
        Report("准备就绪", $"{manifest.Pack.Name} {manifest.Pack.Version}", 1);
    }
    private static string Describe(string? pack)
        => string.IsNullOrEmpty(pack) ? "无光影" : pack;

}
