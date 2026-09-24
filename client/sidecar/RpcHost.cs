using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using BatterMC.Core;
using BatterMC.Protocol;

namespace BatterMC.Launcher;

/// <summary>
/// Tauri sidecar 的 NDJSON RPC 主机。
/// stdin 每行一条 {id,method,params}；stdout 每行一条回复或事件。
/// </summary>
internal sealed class RpcHost : IDisposable
{
    private readonly LauncherPaths _paths;
    private readonly LauncherSettings _settings;
    private readonly LocalState _state;
    private readonly Downloader _downloader = new();
    private readonly HttpClient _accountHttp = new() { Timeout = TimeSpan.FromSeconds(15) };
    private readonly object _outputLock = new();
    private readonly object _activityLock = new();
    private readonly SemaphoreSlim _refreshLock = new(1, 1);
    private DateTimeOffset _lastRefresh;
    private bool _clientUpdating;

    private CancellationTokenSource? _work;
    private PackManifest? _manifest;
    private LauncherRelease? _pendingLauncherUpdate;
    private bool _busy;
    private bool _shutdown;
    private string? _accountToken;
    private string? _accountRefreshToken;
    private JsonObject? _account;
    private JsonObject? _player;
    private string? _activeUpdateSource;
    private string? _updateError;

    public RpcHost(LauncherPaths paths, LauncherSettings settings, LocalState state)
    {
        _paths = paths;
        _settings = settings;
        _state = state;
        TryLoadCachedManifest();
        TryLoadAccountSession();
        Log.Line += OnLogLine;
    }

    public void Dispatch(string json) => _ = Task.Run(() => HandleAsync(json));

    public void Shutdown()
    {
        if (_shutdown) return;
        _shutdown = true;
        try { _work?.Cancel(); } catch { }
        try { _settings.Save(_paths.SettingsFile); } catch { }
        try { _state.Save(_paths.StateFile); } catch { }
    }

    public void Dispose()
    {
        Shutdown();
        Log.Line -= OnLogLine;
        _downloader.Dispose();
        _accountHttp.Dispose();
        _refreshLock.Dispose();
        _work?.Dispose();
    }

    private void OnLogLine(string level, string message) => Emit("log", new JsonObject
    {
        ["level"] = level,
        ["message"] = message,
    });

    private async Task HandleAsync(string json)
    {
        var id = 0;
        try
        {
            var msg = JsonNode.Parse(json)?.AsObject();
            if (msg is null) return;
            id = msg["id"]?.GetValue<int>() ?? 0;
            var method = msg["method"]?.GetValue<string>() ?? "";
            var p = msg["params"]?.AsObject() ?? new JsonObject();
            var result = await InvokeAsync(method, p).ConfigureAwait(false);
            Reply(id, true, result, null);
        }
        catch (OperationCanceledException)
        {
            Reply(id, false, null, "已取消");
        }
        catch (Exception ex)
        {
            Log.Error("处理界面请求失败", ex);
            Reply(id, false, null, Describe(ex));
        }
    }

    private async Task<JsonNode?> InvokeAsync(string method, JsonObject p) => method switch
    {
        "init" => await InitializeAsync().ConfigureAwait(false),
        "getState" => BuildState(),
        "saveSettings" => SaveSettings(p),
        "accountLogin" => await AccountLoginAsync(register: false).ConfigureAwait(false),
        "accountRegister" => await AccountLoginAsync(register: true).ConfigureAwait(false),
        "accountLogout" => await AccountLogoutAsync().ConfigureAwait(false),
        "skinGet" => await SkinRequestAsync(HttpMethod.Get, null).ConfigureAwait(false),
        "skinSave" => await SkinRequestAsync(HttpMethod.Put, SkinBody(p)).ConfigureAwait(false),
        "skinReset" => await SkinRequestAsync(HttpMethod.Delete, null).ConfigureAwait(false),
        "install" => await InstallAsync(p["forceVerify"]?.GetValue<bool>() ?? false).ConfigureAwait(false),
        "launch" => await LaunchAsync(p["forceVerify"]?.GetValue<bool>() ?? false).ConfigureAwait(false),
        "cancel" => Cancel(),
        "detectJava" => DetectJava(),
        "openPath" => OpenPath(p["which"]?.GetValue<string>() ?? "root"),
        "resetVerification" => ResetVerification(),
        "beginClientUpdate" => BeginClientUpdate(),
        "endClientUpdate" => EndClientUpdate(),
        _ => throw new InvalidOperationException($"未知方法 {method}"),
    };

    private void Reply(int id, bool ok, JsonNode? result, string? error)
    {
        if (id == 0) return;
        var node = new JsonObject { ["id"] = id, ["ok"] = ok };
        if (result is not null) node["result"] = result;
        if (error is not null) node["error"] = error;
        WriteLine(node.ToJsonString());
    }

    private void Emit(string name, JsonNode payload) => WriteLine(
        new JsonObject { ["event"] = name, ["payload"] = payload }.ToJsonString());

    private void WriteLine(string line)
    {
        lock (_outputLock)
        {
            Console.Out.WriteLine(line);
            Console.Out.Flush();
        }
    }

    private async Task<JsonNode> InitializeAsync()
    {
        try
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(12));
            var sync = new SyncEngine(_paths, _state, _settings, _downloader);
            _manifest = await sync.FetchManifestAsync(_settings.UpdateBaseUrl, timeout.Token).ConfigureAwait(false);
            _activeUpdateSource = sync.LastManifestUrl;
            _updateError = null;
            CheckLauncherUpdate();
        }
        catch (Exception ex)
        {
            _updateError = ex.Message;
            Log.Warn($"启动时获取更新信息失败：{ex.Message}");
        }
        await RestoreAccountAsync().ConfigureAwait(false);
        return BuildState();
    }

    private void TryLoadCachedManifest()
    {
        try
        {
            if (File.Exists(_paths.ManifestCacheFile))
                _manifest = PackManifest.FromJson(File.ReadAllText(_paths.ManifestCacheFile));
        }
        catch (Exception ex) { Log.Warn($"读取清单缓存失败：{ex.Message}"); }
    }

    private void CheckLauncherUpdate()
    {
        if (_manifest?.Launcher is not { } release ||
            !SelfUpdater.IsNewer(release.Version, GameLauncher.ThisVersion())) return;
        _pendingLauncherUpdate = release;
        Emit("launcherUpdate", LauncherUpdateNode(release));
    }

    private JsonNode BuildState()
    {
        var node = new JsonObject
        {
            ["launcherVersion"] = GameLauncher.ThisVersion(),
            ["busy"] = _busy,
            ["root"] = _paths.Root,
            ["gameDir"] = _paths.GameDir,
            ["installedVersion"] = _state.InstalledPackVersion,
            ["lastSync"] = _state.LastSync?.ToLocalTime().ToString("yyyy-MM-dd HH:mm"),
            ["settings"] = SettingsNode(),
            ["autoMemoryMb"] = _settings.EffectiveMaxMemoryMb(PackMemoryLimits.Read(_paths)),
            ["memoryCeilingMb"] = MemoryCeilingMb(),
            ["totalMemoryMb"] = TotalMemoryMb(),
            ["gpus"] = DetectedGpus(),
            ["account"] = _account?.DeepClone(),
            ["player"] = _player?.DeepClone(),
            ["updateServer"] = new JsonObject
            {
                ["configured"] = _settings.UpdateBaseUrl,
                ["active"] = _activeUpdateSource,
                ["connected"] = _manifest is not null,
                ["error"] = _updateError,
            },
        };

        if (_manifest is not null)
        {
            node["pack"] = new JsonObject
            {
                ["name"] = _manifest.Pack.Name,
                ["version"] = _manifest.Pack.Version,
                ["changelog"] = _manifest.Pack.Changelog,
                ["notice"] = _manifest.Notice,
                ["minecraft"] = _manifest.Minecraft.Version,
                ["loader"] = $"{_manifest.Minecraft.Loader} {_manifest.Minecraft.LoaderVersion}",
                ["javaMajor"] = _manifest.Java.Major,
                ["fileCount"] = _manifest.Files.Count,
                ["size"] = _manifest.Files.Sum(file => file.Size),
                ["overlayCount"] = _manifest.Overlays.Count,
            };

            node["servers"] = new JsonArray(_manifest.Servers.Select(s => (JsonNode)new JsonObject
            {
                ["name"] = s.Name,
                ["host"] = s.Host,
                ["port"] = s.Port,
                ["primary"] = s.Primary,
            }).ToArray());
            node["optional"] = BuildOptionalGroups(_manifest.Files);
        }

        node["components"] = ComponentsNode();
        node["shaders"] = ShadersNode();

        if (_pendingLauncherUpdate is not null)
            node["launcherUpdate"] = LauncherUpdateNode(_pendingLauncherUpdate);
        return node;
    }

    /// <summary>
    /// 状态栏那几个点：整合包 / Minecraft 本体 / 加载器 / 启动器各自装没装、是不是最新。
    ///
    /// 安装是分步的 —— 整合包文件、Minecraft 本体、NeoForge 是三件独立的事，
    /// 任何一件没做完游戏都起不来，用一个"已安装"糊在一起看不出卡在哪儿。
    /// 全是本地文件检查，不联网。
    /// </summary>
    private JsonObject ComponentsNode()
    {
        static JsonObject Item(string label, string state, string? version) => new()
        {
            ["label"] = label,
            ["state"] = state,
            ["version"] = version,
        };

        var packVersion = _manifest?.Pack.Version;
        var installed = _state.InstalledPackVersion;
        var packState = string.IsNullOrWhiteSpace(installed)
            ? "missing"
            : packVersion is not null && !installed.Equals(packVersion, StringComparison.OrdinalIgnoreCase)
                ? "update"
                : "ok";

        var mcState = "missing";
        var loaderState = "missing";
        try
        {
            var id = _manifest?.Minecraft.VersionId;
            if (!string.IsNullOrWhiteSpace(id))
            {
                // 本体：版本目录下的客户端 jar 在不在
                var jar = Path.Combine(_paths.VersionsDir, id, id + ".jar");
                if (File.Exists(jar)) mcState = "ok";

                // 加载器：NeoForge 那堆 artifact 齐不齐，缺一个都起不来
                var versionJson = _paths.ResolveGameFile(_manifest!.Minecraft.VersionJson);
                if (File.Exists(versionJson))
                {
                    var version = VersionJson.Load(versionJson);
                    if (NeoForgeInstaller.IsInstalled(version, _paths, out _)) loaderState = "ok";
                }
            }
        }
        catch (Exception ex)
        {
            Log.Warn($"读取安装状态失败：{ex.Message}");
        }

        return new JsonObject
        {
            ["pack"] = Item("BMC [Remake]", packState, packVersion),
            ["minecraft"] = Item("Minecraft", mcState, _manifest?.Minecraft.Version),
            ["loader"] = Item(LoaderLabel(_manifest?.Minecraft.Loader), loaderState, _manifest?.Minecraft.LoaderVersion),
            ["launcher"] = Item("启动器", _pendingLauncherUpdate is not null ? "update" : "ok", GameLauncher.ThisVersion()),
        };
    }

    private static string LoaderLabel(string? loader) => loader?.ToLowerInvariant() switch
    {
        "neoforge" => "NeoForge",
        "forge" => "Forge",
        "fabric" => "Fabric",
        null or "" => "加载器",
        _ => loader!,
    };

    /// <summary>
    /// 光影：可选的包、当前实际生效的那个。
    ///
    /// 当前值直接读 iris.properties —— 玩家在游戏里换了光影，启动器下次刷新就能看出来，
    /// 并顺手记进设置，这样下次启动写回去的也是他刚换的那个，不会被打回旧预设。
    /// 装完之前不读文件：那会儿文件还是整合包出厂值，读了会把默认预设顶掉。
    /// </summary>
    private JsonObject ShadersNode()
    {
        var installed = !string.IsNullOrWhiteSpace(_state.InstalledPackVersion);
        var file = _paths.ResolveGameFile(ShaderPresets.ConfigPath);
        var current = _settings.ShaderPack ?? ShaderPresets.DefaultPack;

        if (installed && File.Exists(file))
        {
            current = ShaderPresets.Read(_paths).AsSettingValue();
            if (!string.Equals(current, _settings.ShaderPack, StringComparison.Ordinal))
            {
                _settings.ShaderPack = current;
                _settings.Save(_paths.SettingsFile);
            }
        }

        var available = ShaderPresets.Available(_paths);
        // 玩家自己丢进去又删掉的包，别让设置卡在一个不存在的名字上
        if (current.Length > 0 && !available.Contains(current, StringComparer.OrdinalIgnoreCase))
            available.Insert(0, current);

        return new JsonObject
        {
            ["current"] = current,
            ["available"] = new JsonArray(available.Select(x => (JsonNode)x).ToArray()),
        };
    }

    private JsonArray BuildOptionalGroups(IEnumerable<ManagedFile> files)
    {
        var groups = files.Where(f => f.IsOptional)
            .GroupBy(OptionalKey, StringComparer.OrdinalIgnoreCase)
            .OrderBy(g => g.Key, StringComparer.OrdinalIgnoreCase);
        var result = new JsonArray();
        var enabled = new HashSet<string>(_settings.EnabledOptional, StringComparer.OrdinalIgnoreCase);
        var disabled = new HashSet<string>(_settings.DisabledOptional, StringComparer.OrdinalIgnoreCase);

        foreach (var group in groups)
        {
            var items = group.ToList();
            var paths = items.Select(f => f.Path).ToArray();
            result.Add(new JsonObject
            {
                ["path"] = group.Key,
                ["paths"] = new JsonArray(paths.Select(x => (JsonNode)x).ToArray()),
                ["label"] = items[0].Label is "光影包" ? Path.GetFileName(group.Key) : items[0].Label ?? Path.GetFileName(group.Key),
                ["group"] = items[0].Group ?? "其他",
                ["size"] = items.Sum(x => x.Size),
                ["enabled"] = items.All(f => OptionalContent.IsWanted(f, enabled, disabled)),
            });
        }
        return result;
    }

    private static string OptionalKey(ManagedFile file)
    {
        const string prefix = "shaderpacks/";
        if (!file.Path.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) return file.Path;
        var top = file.Path[prefix.Length..].Split('/')[0];
        if (top.EndsWith(".txt", StringComparison.OrdinalIgnoreCase)) top = top[..^4];
        return prefix + top;
    }

    private JsonObject SettingsNode() => new()
    {
        ["username"] = _settings.Username,
        ["javaPath"] = _settings.JavaPath,
        ["maxMemoryMb"] = _settings.MaxMemoryMb,
        ["extraJvmArgs"] = _settings.ExtraJvmArgs,
        ["windowWidth"] = _settings.WindowWidth,
        ["windowHeight"] = _settings.WindowHeight,
        // 游戏里按过 F11 的话以 options.txt 为准，顺手记回设置
        ["fullscreen"] = EffectiveFullscreen(),
        ["autoJoinServer"] = _settings.AutoJoinServer,
        ["updateBaseUrl"] = _settings.UpdateBaseUrl,
        ["authBaseUrl"] = _settings.AuthBaseUrl,
        ["keepLauncherOpen"] = _settings.KeepLauncherOpen,
        ["skipVerify"] = _settings.SkipVerify,
        ["gpu"] = _settings.Gpu,
        ["enabledOptional"] = new JsonArray(_settings.EnabledOptional.Select(x => (JsonNode)x).ToArray()),
        ["shaderPack"] = _settings.ShaderPack ?? ShaderPresets.DefaultPack,
    };

    private JsonNode SaveSettings(JsonObject p)
    {
        string? Str(string key) => p[key]?.GetValue<string>();
        bool? Bool(string key) => p[key] is { } n && n.GetValueKind() is JsonValueKind.True or JsonValueKind.False ? n.GetValue<bool>() : null;
        int? Int(string key) => p[key] is { } n && n.GetValueKind() == JsonValueKind.Number ? n.GetValue<int>() : null;

        // Game login identity is derived from the authenticated muxi UID only.
        if (p.ContainsKey("javaPath")) _settings.JavaPath = Str("javaPath");
        // 0 = 自动。非 0 就地夹进可用区间，免得存了个进游戏要挨警告屏的值
        if (Int("maxMemoryMb") is { } mm)
            _settings.MaxMemoryMb = mm <= 0 ? 0 : Math.Clamp(mm, 2048, MemoryCeilingMb());
        if (Str("extraJvmArgs") is { } ja) _settings.ExtraJvmArgs = ja;
        if (Int("windowWidth") is { } ww) _settings.WindowWidth = Math.Clamp(ww, 640, 7680);
        if (Int("windowHeight") is { } wh) _settings.WindowHeight = Math.Clamp(wh, 480, 4320);
        if (Bool("fullscreen") is { } fs) _settings.Fullscreen = fs;
        if (Bool("autoJoinServer") is { } aj) _settings.AutoJoinServer = aj;
        if (Str("updateBaseUrl") is { } url && !string.IsNullOrWhiteSpace(url)) _settings.UpdateBaseUrl = url.Trim().TrimEnd('/');
        if (Str("authBaseUrl") is { } auth && !string.IsNullOrWhiteSpace(auth)) _settings.AuthBaseUrl = auth.Trim().TrimEnd('/');
        if (Bool("keepLauncherOpen") is { } ko) _settings.KeepLauncherOpen = ko;
        if (Bool("skipVerify") is { } sv) _settings.SkipVerify = sv;
        if (Str("gpu") is { } gpu && gpu is "performance" or "power" or "auto") _settings.Gpu = gpu;
        // 空串是合法值：表示关掉光影。所以只认"键在不在"，不能用非空判断。
        if (p.ContainsKey("shaderPack"))
        {
            _settings.ShaderPack = (Str("shaderPack") ?? "").Trim();
            ShaderPresets.Apply(_paths, _settings.ShaderPack);
        }
        if (p["enabledOptional"] is JsonArray arr)
        {
            // 界面每次把"现在勾着的全部项"发过来。拆成两份记：默认关的记开了哪些，
            // 默认开的记关了哪些——玩家没碰过的项才能一直跟着清单的默认值走。
            var on = new HashSet<string>(
                arr.Select(x => x?.GetValue<string>()).Where(x => !string.IsNullOrEmpty(x)).Select(x => x!),
                StringComparer.OrdinalIgnoreCase);
            if (_manifest is not null)
            {
                var defaultOn = _manifest.Files.Where(f => f.OptionalDefaultOn).Select(f => f.Path)
                    .ToHashSet(StringComparer.OrdinalIgnoreCase);
                _settings.EnabledOptional = on.Where(x => !defaultOn.Contains(x)).ToList();
                _settings.DisabledOptional = defaultOn.Where(x => !on.Contains(x)).ToList();
                // 开关立刻生效：禁用只是给文件加个 .disabled 后缀，不用等下一次同步，也不用重下
                OptionalContent.Apply(_paths, _manifest.Files, _settings.EnabledOptional, _settings.DisabledOptional);
            }
            else
            {
                // 没有清单就分不清哪些是默认开的；只记勾选，别动已经关掉的默认项
                _settings.EnabledOptional = on.ToList();
            }
        }

        _settings.Save(_paths.SettingsFile);
        return BuildState();
    }

    private async Task<JsonNode> LaunchAsync(bool forceVerify)
    {
        BeginGameOperation();
        _work?.Dispose();
        _work = new CancellationTokenSource();
        var ct = _work.Token;

        try
        {
            await RequireAccountAsync().ConfigureAwait(false);
            Emit("busy", new JsonObject { ["busy"] = true });
            var ctx = new PipelineContext { Paths = _paths, Settings = _settings, State = _state };
            var pipeline = new LaunchPipeline(ctx, _downloader);
            pipeline.Status += s => Emit("status", new JsonObject
            {
                ["phase"] = s.Phase,
                ["detail"] = s.Detail,
                ["fraction"] = s.Fraction,
            });
            pipeline.LauncherUpdateAvailable += release =>
            {
                _pendingLauncherUpdate = release;
                Emit("launcherUpdate", LauncherUpdateNode(release));
            };

            await pipeline.PrepareAsync(forceVerify, ct).ConfigureAwait(false);
            _manifest = ctx.Manifest;
            Emit("state", BuildState());

            if (_manifest!.Launcher is { } mandatory &&
                SelfUpdater.IsRequired(mandatory, GameLauncher.ThisVersion()))
                throw new InvalidOperationException($"必须先把启动器更新到 {mandatory.Version} 才能进游戏。");

            var session = GameSession.OfflineUid(AuthenticatedUid());
            Log.Info($"玩家 {session.Username}，离线 UUID {session.UuidDashed}");

            MinecraftRouteProxy? proxy = null;
            // 本地代理不管勾没勾"自动进入服务器"都起：服务器列表里永远挂着它（127.0.0.1），
            // 玩家从主菜单点进服也要走我们的选路、隧道和打洞。勾没勾只决定启动时带不带快速加入，
            // 那由 GameLauncher 看设置决定。
            //
            // 而且游戏不再等连接。原先是"先连上服务器、再启动游戏"，连不上就抛异常，游戏压根
            // 不启动，玩家连单机都玩不了（实测撞上的是服务器重启那一分钟）。现在代理起来就启动
            // 游戏，问控制面、选路、建隧道、打洞都在后台和模组加载并行，进度照常报给进度条；
            // 连上了读完条照样进服，没连上由代理告诉玩家原因，后台一直接着连。
            if (_manifest.Servers.Count > 0)
            {
                proxy = MinecraftRouteProxy.Listen(_manifest.Servers);
                proxy.BeforeGameConnects = MintJoinGrantAsync;
                var liveProxy = proxy;
                proxy.RouteChanged += established => Emit("routeSelected", new JsonObject
                {
                    // 上报解析后的类型：候选声明为 Auto 时，原值对玩家毫无信息量
                    ["kind"] = established.Kind.ToString(),
                    ["label"] = established.Route.Candidate.Label ?? established.Route.Candidate.Id,
                    ["remote"] = established.Route.Endpoint.ToString(),
                    ["latencyMs"] = Math.Round(established.Route.Latency.TotalMilliseconds),
                    ["local"] = liveProxy.LocalAddress,
                    ["available"] = liveProxy.Selection.Reachable.Count,
                });
                proxy.RouteUnavailable += error =>
                {
                    Log.Warn($"{MinecraftRouteProxy.DescribeUnavailable(error)}，后台继续连：{error.Message}");
                    Emit("routePending", new JsonObject
                    {
                        ["reason"] = MinecraftRouteProxy.DescribeUnavailable(error),
                        ["detail"] = error.Message,
                    });
                };
                var manifestServers = _manifest.Servers;
                // 进度必须同步报：Progress<T> 会把回调丢进线程池，连上之后才到的旧进度会把
                // "已连上服务器"又盖回"正在连接"。连接现在全程在后台跑，这种错位随时会撞上。
                proxy.ConnectInBackground(
                    token => PlanConnectionAsync(manifestServers, token),
                    new SyncProgress(detail => Emit("status", new JsonObject
                    {
                        ["phase"] = "正在连接服务器",
                        ["detail"] = detail,
                        ["fraction"] = -1,
                    })));
                Emit("routeConnecting", new JsonObject());
            }

            await using var routeProxy = proxy;
            if (proxy is not null)
            {
                // Quick Play 和多人服务器列表都只指向回环代理，避免玩家从主菜单重连时绕过选路。
                ServersDat.EnsureServers(_paths, ProxiedServers(_manifest.Servers, proxy.LocalPort));
            }

            try
            {
                var launcher = new GameLauncher(_paths, _settings, _state);
                launcher.GameWindowReady += () => Emit("gameWindowReady", new JsonObject());
                Emit("status", new JsonObject
                {
                    ["phase"] = "启动游戏",
                    ["detail"] = "Minecraft 正在加载，首次启动需要几分钟",
                    ["fraction"] = -1,
                });
                Emit("gameStarted", new JsonObject());

                var result = await launcher.LaunchAsync(
                    ctx.Version!, ctx.Java!, _manifest, session, proxy?.LocalAddress, ct).ConfigureAwait(false);
                Emit("gameExited", new JsonObject
                {
                    ["code"] = result.ExitCode,
                    ["hint"] = result.CrashHint,
                    ["logFile"] = result.LogFile,
                });
                return new JsonObject { ["exitCode"] = result.ExitCode, ["hint"] = result.CrashHint };
            }
            finally
            {
                if (proxy is not null)
                {
                    try { ServersDat.EnsureServers(_paths, _manifest.Servers); }
                    catch (Exception ex) { Log.Warn($"恢复服务器列表失败：{ex.Message}"); }
                }
            }
        }
        finally
        {
            FinishGameOperation();
        }
    }

    /// <summary>在报告的线程上直接回调。Emit 自己加了锁，可以从任何线程调。</summary>
    private sealed class SyncProgress(Action<string> report) : IProgress<string>
    {
        public void Report(string value) => report(value);
    }

    /// <summary>
    /// 后台连接每一轮开头调用：问控制面拿最新的线路候选和隧道凭据。
    ///
    /// 控制面优先。清单里的线路是发布那一刻定死的，而服务器那台的 IPv6 是临时地址、会轮换，
    /// 运营商重拨还可能整个前缀都变。只有那台机器自己知道它此刻是什么地址，所以每次都来问。
    /// 问不到就退回清单——控制面挂了不该连累玩家进不去游戏。
    /// </summary>
    private async Task<ConnectPlan> PlanConnectionAsync(List<ServerEntry> manifestServers, CancellationToken ct)
    {
        // 凭据优先用本地配置（运维可以钉死一个），否则现取。和候选一起并行取：
        // 两次都是问同一个控制面，串着问只是多等一个往返。
        var tokenFetch = !string.IsNullOrWhiteSpace(_settings.TunnelToken)
            ? Task.FromResult<string?>(_settings.TunnelToken)
            : ControlPlaneClient.FetchClientTokenAsync(
                _settings.UpdateBaseUrl, "default", TimeSpan.FromSeconds(8), ct);
        var snapshot = await ControlPlaneClient
            .FetchAsync(_settings.UpdateBaseUrl, "default", TimeSpan.FromSeconds(8), ct)
            .ConfigureAwait(false);
        var servers = manifestServers;
        if (snapshot is { Online: true } && snapshot.Candidates.Count > 0)
        {
            servers = WithControlPlaneRoutes(manifestServers, snapshot.Candidates);
            Log.Info($"线路候选来自控制面：{snapshot.Candidates.Count} 条，" +
                     $"数据新鲜度 {snapshot.AgeSeconds:0.#}s");
        }
        else
        {
            Log.Warn("控制面没有可用候选，回退到清单里的线路");
        }
        var token = await tokenFetch.ConfigureAwait(false);
        return new ConnectPlan(servers, token, _settings.UpdateBaseUrl, "default",
            _settings.PunchReflector, _settings.TunnelPort);
    }

    /// <summary>
    /// 用控制面下发的候选替换主服务器的 routes，其余字段（名字、兜底地址）保持不变
    /// ——servers.dat 和"所有线路都失败"时的提示仍然要用清单里那个公开地址。
    /// </summary>
    private static List<ServerEntry> WithControlPlaneRoutes(
        List<ServerEntry> servers, List<RouteCandidate> candidates)
    {
        var result = new List<ServerEntry>(servers.Count);
        var replaced = false;
        var hasPrimary = servers.Any(server => server.Primary);
        foreach (var server in servers)
        {
            var isTarget = !replaced && (server.Primary || !hasPrimary);
            if (!isTarget) { result.Add(server); continue; }
            replaced = true;
            result.Add(new ServerEntry
            {
                Name = server.Name,
                Host = server.Host,
                Port = server.Port,
                Primary = server.Primary,
                Forced = server.Forced,
                Routes = candidates,
            });
        }
        return result;
    }

    private static IEnumerable<ServerEntry> ProxiedServers(IEnumerable<ServerEntry> servers, int localPort)
    {
        var items = servers.ToList();
        var hasPrimary = items.Any(server => server.Primary);
        var primaryReplaced = false;
        foreach (var server in items)
        {
            var replace = !primaryReplaced && (server.Primary || !hasPrimary);
            if (replace) primaryReplaced = true;
            yield return new ServerEntry
            {
                Name = server.Name,
                Host = replace ? "127.0.0.1" : server.Host,
                Port = replace ? localPort : server.Port,
                Primary = server.Primary,
                Forced = server.Forced,
            };
        }
    }

    // Both the final event and the RPC reply must be created AFTER busy is
    // cleared. Returning BuildState() from the try block captures busy=true,
    // even though the finally block runs before the reply is transmitted.
    internal async Task<JsonNode> RunInstallOperationAsync(Func<CancellationToken, Task> prepare)
    {
        BeginGameOperation();
        try
        {
            _work?.Dispose();
            _work = new CancellationTokenSource();
            await prepare(_work.Token).ConfigureAwait(false);
        }
        finally
        {
            FinishGameOperation();
        }
        return BuildState();
    }

    private Task<JsonNode> InstallAsync(bool forceVerify) => RunInstallOperationAsync(async ct =>
        {
            await RequireAccountAsync().ConfigureAwait(false);
            Emit("busy", new JsonObject { ["busy"] = true });
            var ctx = new PipelineContext { Paths = _paths, Settings = _settings, State = _state };
            var pipeline = new LaunchPipeline(ctx, _downloader);
            pipeline.Status += s => Emit("status", new JsonObject
            {
                ["phase"] = s.Phase,
                ["detail"] = s.Detail,
                ["fraction"] = s.Fraction,
            });
            pipeline.LauncherUpdateAvailable += release =>
            {
                _pendingLauncherUpdate = release;
                Emit("launcherUpdate", LauncherUpdateNode(release));
            };

            await pipeline.PrepareAsync(forceVerify, ct).ConfigureAwait(false);
            _manifest = ctx.Manifest;
        });

    private void FinishGameOperation()
    {
        try { _state.Save(_paths.StateFile); }
        finally
        {
            // Even a full disk or failed state write must unlock the UI.
            lock (_activityLock)
            {
                _busy = false;
                Emit("busy", new JsonObject { ["busy"] = false });
                Emit("state", BuildState());
            }
        }
    }

    private JsonNode Cancel()
    {
        _work?.Cancel();
        return new JsonObject { ["cancelled"] = true };
    }

    private void BeginGameOperation()
    {
        lock (_activityLock)
        {
            if (_clientUpdating) throw new InvalidOperationException("正在更新客户端，请稍候");
            if (_busy) throw new InvalidOperationException("正在忙，请先等当前操作完成");
            _busy = true;
        }
    }

    private JsonNode BeginClientUpdate()
    {
        lock (_activityLock)
        {
            if (_busy) throw new InvalidOperationException("请先退出游戏或等待当前任务完成，再更新客户端");
            if (_clientUpdating) throw new InvalidOperationException("客户端更新已在进行中");
            _clientUpdating = true;
        }
        return new JsonObject { ["reserved"] = true };
    }

    private JsonNode EndClientUpdate()
    {
        lock (_activityLock) _clientUpdating = false;
        return new JsonObject { ["released"] = true };
    }

    private string AccountApi(string path)
    {
        var baseUrl = _settings.AuthBaseUrl.TrimEnd('/');
        if (!Uri.TryCreate(baseUrl, UriKind.Absolute, out var uri))
            throw new InvalidOperationException("账号服务器地址无效");
        var local = uri.IsLoopback || uri.Host.Equals("localhost", StringComparison.OrdinalIgnoreCase);
        if (!local && !uri.Scheme.Equals(Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("账号登录只允许使用 HTTPS，请先为官网配置域名和证书");
        return $"{baseUrl}{path}";
    }

    private static string GameApi(string path)
        => LauncherSettings.OfficialUpdateBaseUrl.TrimEnd('/') + path;

    private static string Base64Url(byte[] bytes)
        => Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');

    private static Dictionary<string, string> ParseQuery(string query)
    {
        var result = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var item in query.TrimStart('?').Split('&', StringSplitOptions.RemoveEmptyEntries))
        {
            var pair = item.Split('=', 2);
            var key = Uri.UnescapeDataString(pair[0].Replace('+', ' '));
            var value = pair.Length == 2 ? Uri.UnescapeDataString(pair[1].Replace('+', ' ')) : "";
            result[key] = value;
        }
        return result;
    }

    private async Task<(string Flow, string Secret)> CreateLauncherAuthFlowAsync(CancellationToken ct)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, AccountApi("/api/launcher/auth-flow"));
        using var response = await _accountHttp.SendAsync(request, ct).ConfigureAwait(false);
        var text = await response.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
        var json = JsonNode.Parse(text)?.AsObject();
        if (!response.IsSuccessStatusCode)
            throw new InvalidOperationException(json?["detail"]?.GetValue<string>() ?? "无法创建启动器授权会话");
        var flow = json?["flow"]?.GetValue<string>() ?? "";
        var secret = json?["secret"]?.GetValue<string>() ?? "";
        if (string.IsNullOrWhiteSpace(flow) || string.IsNullOrWhiteSpace(secret))
            throw new InvalidOperationException("muxi 账户没有返回有效的启动器授权会话");
        return (flow, secret);
    }

    private async Task<string> WatchLauncherAuthFlowAsync(string flow, string secret, CancellationToken ct)
    {
        var url = AccountApi($"/api/launcher/auth-flow/{Uri.EscapeDataString(flow)}?secret={Uri.EscapeDataString(secret)}");
        while (true)
        {
            await Task.Delay(TimeSpan.FromMilliseconds(750), ct).ConfigureAwait(false);
            try
            {
                using var response = await _accountHttp.GetAsync(url, ct).ConfigureAwait(false);
                if (response.StatusCode == System.Net.HttpStatusCode.NotFound)
                    return "expired";
                if (!response.IsSuccessStatusCode)
                    continue;
                var json = JsonNode.Parse(await response.Content.ReadAsStringAsync(ct).ConfigureAwait(false))?.AsObject();
                var status = json?["status"]?.GetValue<string>() ?? "pending";
                if (!status.Equals("pending", StringComparison.OrdinalIgnoreCase))
                    return status;
            }
            catch (HttpRequestException)
            {
                // 短暂网络波动不应该打断已经打开的浏览器授权页。
            }
        }
    }

    private async Task CompleteLauncherAuthFlowAsync(string flow, string secret)
    {
        try
        {
            using var request = new HttpRequestMessage(
                HttpMethod.Delete,
                AccountApi($"/api/launcher/auth-flow/{Uri.EscapeDataString(flow)}?secret={Uri.EscapeDataString(secret)}"));
            using var _ = await _accountHttp.SendAsync(request).ConfigureAwait(false);
        }
        catch { }
    }

    private static async Task WriteBrowserResultAsync(NetworkStream stream, bool ok)
    {
        var title = ok ? "登录成功" : "登录失败";
        var body = ok ? "已完成 muxi 账户 授权，可以关闭这个页面并返回 Better MC。" : "授权没有完成，请返回 Better MC 重试。";
        var html = $"<!doctype html><meta charset=\"utf-8\"><title>{title}</title><style>body{{font:16px system-ui;background:#0b0d12;color:#f5f7fb;display:grid;place-items:center;min-height:100vh;margin:0}}main{{max-width:560px;padding:40px;border:1px solid #303746;background:#151922}}h1{{margin-top:0}}p{{color:#9aa4b5}}</style><main><h1>{title}</h1><p>{body}</p></main>";
        var bytes = Encoding.UTF8.GetBytes(html);
        var header = Encoding.ASCII.GetBytes($"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {bytes.Length}\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n");
        await stream.WriteAsync(header).ConfigureAwait(false);
        await stream.WriteAsync(bytes).ConfigureAwait(false);
    }

    private async Task<JsonNode> AccountLoginAsync(bool register)
    {
        const string clientId = "better-mc-launcher";
        var verifier = Base64Url(RandomNumberGenerator.GetBytes(48));
        var challenge = Base64Url(SHA256.HashData(Encoding.ASCII.GetBytes(verifier)));
        var state = Base64Url(RandomNumberGenerator.GetBytes(24));
        var nonce = Base64Url(RandomNumberGenerator.GetBytes(24));

        using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(register ? 15 : 5));
        var (launcherFlow, launcherFlowSecret) = await CreateLauncherAuthFlowAsync(timeout.Token).ConfigureAwait(false);

        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start(1);
        try
        {
            var endpoint = (IPEndPoint)listener.LocalEndpoint;
            var redirectUri = $"http://127.0.0.1:{endpoint.Port}/oauth/callback";
            var authorizePath = "/oauth/authorize" +
                $"?client_id={Uri.EscapeDataString(clientId)}" +
                $"&redirect_uri={Uri.EscapeDataString(redirectUri)}" +
                "&response_type=code" +
                $"&scope={Uri.EscapeDataString("openid profile email")}" +
                $"&state={Uri.EscapeDataString(state)}" +
                $"&nonce={Uri.EscapeDataString(nonce)}" +
                $"&code_challenge={Uri.EscapeDataString(challenge)}" +
                "&code_challenge_method=S256" +
                $"&launcher_flow={Uri.EscapeDataString(launcherFlow)}" +
                $"&launcher_flow_secret={Uri.EscapeDataString(launcherFlowSecret)}";

            var browserUrl = register
                ? AccountApi("/register") + $"?continue={Uri.EscapeDataString(authorizePath)}"
                : AccountApi(authorizePath);

            Process.Start(new ProcessStartInfo(browserUrl) { UseShellExecute = true });
            Emit("status", new JsonObject
            {
                ["phase"] = register ? "等待注册" : "等待登录",
                ["detail"] = register
                    ? "请在浏览器中创建并验证 muxi 账户，完成后会自动返回启动器"
                    : "请在浏览器中完成 muxi 账户登录",
                ["fraction"] = -1,
            });

            using var wait = CancellationTokenSource.CreateLinkedTokenSource(timeout.Token);
            var callbackTask = listener.AcceptTcpClientAsync(wait.Token).AsTask();
            var flowTask = WatchLauncherAuthFlowAsync(launcherFlow, launcherFlowSecret, wait.Token);
            var completed = await Task.WhenAny(callbackTask, flowTask).ConfigureAwait(false);
            if (completed == flowTask)
            {
                var flowStatus = await flowTask.ConfigureAwait(false);
                wait.Cancel();
                try { await callbackTask.ConfigureAwait(false); }
                catch (OperationCanceledException) { }
                if (flowStatus.Equals("cancelled", StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException(register ? "已取消 muxi 账户创建" : "已取消 muxi 账户登录");
                throw new InvalidOperationException(register ? "muxi 账户创建已超时，请重新尝试" : "muxi 账户登录已超时，请重新尝试");
            }

            using var client = await callbackTask.ConfigureAwait(false);
            wait.Cancel();
            try { await flowTask.ConfigureAwait(false); }
            catch (OperationCanceledException) { }
            await using var stream = client.GetStream();
            using var reader = new StreamReader(stream, Encoding.ASCII, false, 4096, leaveOpen: true);
            var requestLine = await reader.ReadLineAsync(timeout.Token).ConfigureAwait(false) ?? "";
            while (!string.IsNullOrEmpty(await reader.ReadLineAsync(timeout.Token).ConfigureAwait(false))) { }
            var parts = requestLine.Split(' ');
            if (parts.Length < 2 || !Uri.TryCreate("http://127.0.0.1" + parts[1], UriKind.Absolute, out var callback))
            {
                await WriteBrowserResultAsync(stream, false).ConfigureAwait(false);
                throw new InvalidOperationException("muxi 账户返回了无效的登录回调");
            }
            var query = ParseQuery(callback.Query);
            if (!query.TryGetValue("state", out var returnedState) || !CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(state), Encoding.UTF8.GetBytes(returnedState)))
            {
                await WriteBrowserResultAsync(stream, false).ConfigureAwait(false);
                throw new InvalidOperationException("muxi 账户登录状态校验失败");
            }
            if (!query.TryGetValue("code", out var code) || string.IsNullOrWhiteSpace(code))
            {
                await WriteBrowserResultAsync(stream, false).ConfigureAwait(false);
                throw new InvalidOperationException(query.GetValueOrDefault("error_description") ?? query.GetValueOrDefault("error") ?? "muxi 账户未返回授权码");
            }

            using var content = new FormUrlEncodedContent(new Dictionary<string, string>
            {
                ["grant_type"] = "authorization_code",
                ["client_id"] = clientId,
                ["code"] = code,
                ["redirect_uri"] = redirectUri,
                ["code_verifier"] = verifier,
            });
            using var response = await _accountHttp.PostAsync(AccountApi("/oauth/token"), content).ConfigureAwait(false);
            var text = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
            var json = JsonNode.Parse(text)?.AsObject();
            if (!response.IsSuccessStatusCode)
            {
                await WriteBrowserResultAsync(stream, false).ConfigureAwait(false);
                throw new InvalidOperationException(json?["error_description"]?.GetValue<string>() ?? $"登录失败（HTTP {(int)response.StatusCode}）");
            }

            _accountToken = json?["access_token"]?.GetValue<string>();
            _accountRefreshToken = json?["refresh_token"]?.GetValue<string>();
            if (string.IsNullOrEmpty(_accountToken))
            {
                await WriteBrowserResultAsync(stream, false).ConfigureAwait(false);
                throw new InvalidOperationException("muxi 账户没有返回访问令牌");
            }
            await LoadAccountAsync().ConfigureAwait(false);
            PersistAccountSession();
            await WriteBrowserResultAsync(stream, true).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (timeout.IsCancellationRequested)
        {
            throw new InvalidOperationException(register ? "muxi 账户创建已超时，请重新尝试" : "muxi 账户登录已超时，请重新尝试");
        }
        finally
        {
            listener.Stop();
            await CompleteLauncherAuthFlowAsync(launcherFlow, launcherFlowSecret).ConfigureAwait(false);
        }

        var gameName = OfflineAuth.UidLoginName(AuthenticatedUid());
        if (!OfflineAuth.IsValidUsername(gameName))
            throw new InvalidOperationException("Better MC 玩家游戏名无效");
        _settings.Username = gameName;
        _settings.Save(_paths.SettingsFile);
        Emit("state", BuildState());
        return BuildState();
    }

    private async Task LoadAccountAsync()
    {
        if (string.IsNullOrEmpty(_accountToken)) throw new InvalidOperationException("请先登录 muxi 账户");
        using var request = new HttpRequestMessage(HttpMethod.Get, AccountApi("/oauth/userinfo"));
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _accountToken);
        using var response = await _accountHttp.SendAsync(request).ConfigureAwait(false);
        var text = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
        var json = JsonNode.Parse(text)?.AsObject();
        if (!response.IsSuccessStatusCode)
            throw new InvalidOperationException(json?["error_description"]?.GetValue<string>() ?? "统一账户会话无效");
        _account = json;
        await LoadPlayerProfileAsync().ConfigureAwait(false);
        var gameName = OfflineAuth.UidLoginName(AuthenticatedUid());
        if (!OfflineAuth.IsValidUsername(gameName))
        {
            _account = null;
            _player = null;
            throw new InvalidOperationException("Better MC 返回了无效游戏名");
        }
    }

    private async Task LoadPlayerProfileAsync()
    {
        if (string.IsNullOrEmpty(_accountToken))
            throw new InvalidOperationException("请先登录 muxi 账户");
        using var request = new HttpRequestMessage(HttpMethod.Get, GameApi("/api/v1/player/profile"));
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _accountToken);
        using var response = await _accountHttp.SendAsync(request).ConfigureAwait(false);
        var text = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
        var json = JsonNode.Parse(text)?.AsObject();
        if (!response.IsSuccessStatusCode)
            throw new InvalidOperationException(json?["detail"]?.GetValue<string>() ?? "无法读取 Better MC 玩家资料");
        _player = json?["player"]?.AsObject();
        if (_player?["uid"]?.GetValue<long>() != AuthenticatedUid())
            throw new InvalidOperationException("玩家资料 UID 与登录账户不匹配");
    }

    /// <summary>
    /// 换一张一次性进服票。
    ///
    /// 游戏服务端是 offline-mode：用户名就是平台 UID，而 UID 是从 10000 开始的顺号。
    /// 没有这一步的话，谁把用户名填成别人的 UID 谁就是那个人——换个"不好猜"的登录名
    /// 也没用，游戏里 /msg 的 Tab 补全本来就会把在线玩家的登录名全列出来。
    /// 这张票是本人的 access token 换来的，冒名者拿不到。
    ///
    /// 在游戏发起连接的那一刻换，而不是启动游戏时换：400 多个模组要加载一两分钟，
    /// 玩家还可能在主菜单停留，启动时换的票到手就快过期了。
    /// </summary>
    private async Task MintJoinGrantAsync(CancellationToken ct)
    {
        // 没登录就什么都不做：服务端拒绝时给出的理由比这里编一个准确。
        if (string.IsNullOrEmpty(_accountToken)) return;
        var status = await PostJoinGrantAsync(ct).ConfigureAwait(false);
        // 只有 401 值得重试。access token 过期是最常见的情况，刷一次就好；
        // 其余状态码重试只是把玩家多晾一个往返。
        if (status == HttpStatusCode.Unauthorized && await RefreshAccountAsync().ConfigureAwait(false))
            status = await PostJoinGrantAsync(ct).ConfigureAwait(false);
        if (status == HttpStatusCode.OK) Log.Info("已换到进服票据");
        else Log.Warn($"没换到进服票据（HTTP {(int)status}），这次进服会被服务端拒绝");
    }

    private async Task<HttpStatusCode> PostJoinGrantAsync(CancellationToken ct)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, AccountApi("/api/launcher/minecraft/join"));
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _accountToken);
        using var response = await _accountHttp.SendAsync(request, ct).ConfigureAwait(false);
        return response.StatusCode;
    }

    private long AuthenticatedUid()
    {
        if (string.IsNullOrEmpty(_accountToken) || _account?["muxi_uid"] is not JsonValue value
            || !value.TryGetValue<long>(out var uid))
            throw new InvalidOperationException("登录身份缺少有效平台 UID，请重新登录");
        _ = OfflineAuth.UidLoginName(uid);
        return uid;
    }

    // ── 自定义皮肤 ──
    //
    // 原版 64×64 皮肤存在控制面，按平台 UID 一人一份；游戏里由整合包带的 CustomSkinLoader
    // 按登录名取回来（见 server/app/skins.py）。聊天头像、关掉 YSM 的玩家看到的身体都用它。
    // 页面只允许 data: 图片，所以贴图以 base64 在这里来回。

    private static JsonObject SkinBody(JsonObject p)
    {
        var body = new JsonObject { ["model"] = p["model"]?.GetValue<string>() is "slim" ? "slim" : "default" };
        // 不带图片 = 只改模型
        if (p["png"]?.GetValue<string>() is { Length: > 0 } png) body["png"] = png;
        return body;
    }

    /// <summary>返回 {"skin": {model, hash, png} | null}。</summary>
    private async Task<JsonNode> SkinRequestAsync(HttpMethod method, JsonObject? body)
    {
        if (string.IsNullOrEmpty(_accountToken)) throw new InvalidOperationException("请先登录 muxi 账户");
        var (status, json) = await SendSkinAsync(method, body).ConfigureAwait(false);
        // access token 过期是最常见的情况，刷一次再来
        if (status == HttpStatusCode.Unauthorized && await RefreshAccountAsync().ConfigureAwait(false))
            (status, json) = await SendSkinAsync(method, body).ConfigureAwait(false);
        if (status == HttpStatusCode.OK && json is not null) return json;
        var detail = json?["detail"] is JsonValue v && v.TryGetValue<string>(out var s) ? s : null;
        throw new InvalidOperationException(detail ?? $"皮肤服务暂时不可用（HTTP {(int)status}）");
    }

    private async Task<(HttpStatusCode Status, JsonObject? Json)> SendSkinAsync(HttpMethod method, JsonObject? body)
    {
        using var request = new HttpRequestMessage(method, GameApi("/api/v1/player/skin"));
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _accountToken);
        if (body is not null) request.Content = new StringContent(body.ToJsonString(), Encoding.UTF8, "application/json");
        using var response = await _accountHttp.SendAsync(request).ConfigureAwait(false);
        var text = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
        JsonObject? json = null;
        try { json = JsonNode.Parse(text) as JsonObject; } catch (JsonException) { }
        return (response.StatusCode, json);
    }

    private async Task<JsonNode> AccountLogoutAsync()
    {
        var revoke = _accountRefreshToken ?? _accountToken;
        if (!string.IsNullOrEmpty(revoke))
        {
            try
            {
                using var content = new FormUrlEncodedContent(new Dictionary<string, string>
                {
                    ["client_id"] = "better-mc-launcher",
                    ["token"] = revoke,
                });
                using var _ = await _accountHttp.PostAsync(AccountApi("/oauth/revoke"), content).ConfigureAwait(false);
            }
            catch { }
        }
        DropAccountSession();
        Emit("state", BuildState());
        return BuildState();
    }

    /// <summary>
    /// 用 refresh token 换一套新令牌。
    ///
    /// 返回 false 只代表上游明确拒绝了这个令牌（过期，或已经被上一次刷新轮换掉）；
    /// 网络不通会原样抛出，调用方据此区分"要重新登录"和"暂时连不上"——
    /// 把后者也当成过期，断网时就会白白把玩家踢下线。
    /// </summary>
    private async Task<bool> RefreshAccountAsync()
    {
        // 刷新必须串起来：上游每刷一次就把旧 refresh token 吊销，两处并发各刷一次的话，
        // 后一个拿着已经作废的令牌去换，换回来一个 invalid_grant，玩家就被莫名登出了。
        await _refreshLock.WaitAsync().ConfigureAwait(false);
        try
        {
            // 刚有人刷过就直接用他换来的那套，别再换一次
            if (!string.IsNullOrEmpty(_accountToken)
                && DateTimeOffset.UtcNow - _lastRefresh < TimeSpan.FromSeconds(30)) return true;

            if (string.IsNullOrEmpty(_accountRefreshToken)) return false;
            using var content = new FormUrlEncodedContent(new Dictionary<string, string>
            {
                ["grant_type"] = "refresh_token",
                ["client_id"] = "better-mc-launcher",
                ["refresh_token"] = _accountRefreshToken,
            });
            using var response = await _accountHttp.PostAsync(AccountApi("/oauth/token"), content).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode) return false;
            var json = JsonNode.Parse(await response.Content.ReadAsStringAsync().ConfigureAwait(false))?.AsObject();
            _accountToken = json?["access_token"]?.GetValue<string>();
            _accountRefreshToken = json?["refresh_token"]?.GetValue<string>();
            var ok = !string.IsNullOrEmpty(_accountToken) && !string.IsNullOrEmpty(_accountRefreshToken);
            if (ok)
            {
                _lastRefresh = DateTimeOffset.UtcNow;
                // 旧的已经被上游吊销了，这里不立刻落盘，进程一退玩家就登不回来
                PersistAccountSession();
            }
            return ok;
        }
        finally
        {
            _refreshLock.Release();
        }
    }

    /// <summary>
    /// 检测到的显卡，给设置界面显示用。
    ///
    /// 每次构造状态都重新枚举：外接显卡坞插拔、驱动更新都会改变这个列表，
    /// 而这只是读几个注册表键，不值得缓存。
    /// </summary>
    private JsonArray DetectedGpus()
    {
        var list = new JsonArray();
        try
        {
            var adapters = GpuPreference.Detect();
            foreach (var gpu in adapters)
            {
                list.Add(new JsonObject
                {
                    ["name"] = gpu.Name,
                    ["vram"] = gpu.VramBytes,
                    ["vramText"] = gpu.VramText,
                    // 这块卡对应设置里的哪个值。Windows 只有高性能/节能两档，
                    // 界面按名字选，映射在这里定死，别让前端自己猜。
                    ["choice"] = GpuPreference.ChoiceFor(adapters, gpu) == GpuChoice.HighPerformance
                        ? "performance" : "power",
                });
            }
        }
        catch (Exception ex)
        {
            Log.Warn($"枚举显卡失败：{ex.Message}");
        }
        return list;
    }

    private void TryLoadAccountSession()
    {
        var session = AccountStore.Load(_paths.AccountFile);
        if (session is null) return;
        _accountToken = session.AccessToken;
        _accountRefreshToken = session.RefreshToken;
    }

    private void PersistAccountSession()
    {
        if (string.IsNullOrEmpty(_accountToken) || string.IsNullOrEmpty(_accountRefreshToken)) return;
        try
        {
            AccountStore.Save(_paths.AccountFile, new AccountSession
            {
                AccessToken = _accountToken,
                RefreshToken = _accountRefreshToken,
                SavedAt = DateTimeOffset.UtcNow,
            });
        }
        catch (Exception ex)
        {
            Log.Warn($"保存登录态失败，下次可能需要重新登录：{ex.Message}");
        }
    }

    private void DropAccountSession()
    {
        _accountToken = null;
        _accountRefreshToken = null;
        _account = null;
        _player = null;
        AccountStore.Clear(_paths.AccountFile);
    }

    /// <summary>
    /// 用磁盘上的令牌把上次的登录接回来。
    ///
    /// access token 只有一小时，玩家几乎总是在它过期之后才再打开启动器，所以这里
    /// 大概率要走一次刷新。刷新失败要分两种情况：上游明确说令牌死了，那就清干净让
    /// 玩家重登；网络不通则原样留着，下次再试——连不上账号服务不是登出的理由。
    /// </summary>
    private async Task RestoreAccountAsync()
    {
        if (string.IsNullOrEmpty(_accountToken) && string.IsNullOrEmpty(_accountRefreshToken)) return;
        try
        {
            try
            {
                await LoadAccountAsync().ConfigureAwait(false);
            }
            catch
            {
                if (!await RefreshAccountAsync().ConfigureAwait(false))
                {
                    Log.Info("本地登录令牌已失效，需要重新登录");
                    DropAccountSession();
                    return;
                }
                await LoadAccountAsync().ConfigureAwait(false);
            }

            var gameName = OfflineAuth.UidLoginName(AuthenticatedUid());
            if (OfflineAuth.IsValidUsername(gameName))
            {
                _settings.Username = gameName;
                _settings.Save(_paths.SettingsFile);
            }
            Log.Info("已恢复上次的 muxi 账户登录");
        }
        catch (Exception ex)
        {
            // 多半是连不上账号服务。令牌保持原样，界面暂时显示未登录，点开始游戏时还会再试一次。
            _account = null;
            _player = null;
            Log.Warn($"暂时无法恢复登录态，已保留本地令牌：{ex.Message}");
        }
    }

    private async Task RequireAccountAsync()
    {
        // 冷启动时 init 那次恢复可能因为断网没成，这里再试一次，别让玩家白登一遍
        if (_account is null && !string.IsNullOrEmpty(_accountRefreshToken))
            await RestoreAccountAsync().ConfigureAwait(false);

        if (string.IsNullOrEmpty(_accountToken) || _account is null)
            throw new InvalidOperationException("请先登录 muxi 账户");
        try
        {
            await LoadAccountAsync().ConfigureAwait(false);
        }
        catch
        {
            if (!await RefreshAccountAsync().ConfigureAwait(false))
            {
                DropAccountSession();
                Emit("state", BuildState());
                throw new InvalidOperationException("muxi 账户 登录已过期，请重新登录");
            }
            await LoadAccountAsync().ConfigureAwait(false);
        }
        var gameName = OfflineAuth.UidLoginName(AuthenticatedUid());
        if (!OfflineAuth.IsValidUsername(gameName))
            throw new InvalidOperationException("账号玩家名无效，请联系管理员");
        _settings.Username = gameName;
    }

    private JsonNode DetectJava()
    {
        var list = new JsonArray();
        foreach (var j in JavaManager.Discover(_paths))
            list.Add(new JsonObject
            {
                ["path"] = j.Path,
                ["version"] = j.Version,
                ["major"] = j.Major,
                ["source"] = j.Source,
            });
        return new JsonObject { ["items"] = list };
    }

    private JsonNode OpenPath(string which)
    {
        var target = which switch
        {
            "game" => _paths.GameDir,
            "logs" => _paths.LogDir,
            "crash" => _paths.CrashDir,
            "mods" => Path.Combine(_paths.GameDir, "mods"),
            "config" => Path.Combine(_paths.GameDir, "config"),
            "saves" => Path.Combine(_paths.GameDir, "saves"),
            "runtime" => _paths.RuntimeDir,
            _ => _paths.Root,
        };
        Directory.CreateDirectory(target);
        Process.Start(new ProcessStartInfo(target) { UseShellExecute = true });
        return new JsonObject { ["path"] = target };
    }

    private JsonNode ResetVerification()
    {
        _state.Hashes.Clear();
        _state.SeededFiles.Clear();
        _state.SeedRevisions.Clear();
        _state.InstalledPackVersion = null;
        _state.Save(_paths.StateFile);
        return new JsonObject { ["cleared"] = true };
    }

    private static JsonObject LauncherUpdateNode(LauncherRelease release) => new()
    {
        ["version"] = release.Version,
        ["currentVersion"] = GameLauncher.ThisVersion(),
        ["notes"] = release.Notes,
        ["size"] = release.Size,
        ["mandatory"] = SelfUpdater.IsRequired(release, GameLauncher.ThisVersion()),
        ["minSupportedVersion"] = release.MinSupportedVersion,
        ["updateReason"] = release.UpdateReason,
    };

    /// <summary>
    /// 玩家最多能分配多少堆：给系统留 2G，再不超过整合包自己声明的阈值
    /// （config/memorysettings.json 的 maximumClient，超了游戏会弹警告屏）。
    /// </summary>
    /// <summary>
    /// 全屏的真相是 options.txt —— 玩家在游戏里按 F11 改了，启动器要跟着显示，
    /// 否则下次启动会被写回旧值，玩家会觉得这个开关是坏的。
    /// </summary>
    private bool EffectiveFullscreen()
    {
        var inGame = GameOptions.ReadFullscreen(_paths);
        if (inGame is not { } value || value == _settings.Fullscreen) return _settings.Fullscreen;
        _settings.Fullscreen = value;
        _settings.Save(_paths.SettingsFile);
        return value;
    }

    private int MemoryCeilingMb()
    {
        var total = TotalMemoryMb();
        var ceiling = total > 0 ? Math.Max(2048, total - 2048) : 8192;
        var limits = PackMemoryLimits.Read(_paths);
        if (limits.MaxMb > 0) ceiling = Math.Min(ceiling, limits.MaxMb);
        return ceiling;
    }

    private static int TotalMemoryMb()
    {
        try { return (int)(GC.GetGCMemoryInfo().TotalAvailableMemoryBytes / (1024 * 1024)); }
        catch { return 0; }
    }

    private static string Describe(Exception ex)
    {
        if (ex is AggregateException agg)
        {
            var inner = agg.InnerExceptions.FirstOrDefault();
            var extra = agg.InnerExceptions.Count > 1 ? $"（共 {agg.InnerExceptions.Count} 个错误）" : "";
            return (inner is null ? agg.Message : Describe(inner)) + extra;
        }
        return ex switch
        {
            // 不一定是更新服务器：下载 Minecraft 本体时连的是镜像或上游，主机名在 Message 里
            HttpRequestException => $"网络连接失败：{ex.Message}",
            TaskCanceledException => "网络超时，检查一下网络或者更新服务器地址",
            UnauthorizedAccessException => $"没有权限访问文件：{ex.Message}",
            IOException => $"读写文件失败：{ex.Message}",
            _ => ex.Message,
        };
    }
}
