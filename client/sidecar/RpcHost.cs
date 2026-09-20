using System.Diagnostics;
using System.Net.Http.Headers;
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

    private CancellationTokenSource? _work;
    private PackManifest? _manifest;
    private LauncherRelease? _pendingLauncherUpdate;
    private bool _busy;
    private bool _shutdown;
    private string? _accountToken;
    private JsonObject? _account;

    public RpcHost(LauncherPaths paths, LauncherSettings settings, LocalState state)
    {
        _paths = paths;
        _settings = settings;
        _state = state;
        TryLoadCachedManifest();
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
        "accountLogin" => await AccountLoginAsync(p).ConfigureAwait(false),
        "accountLogout" => AccountLogout(),
        "install" => await InstallAsync(p["forceVerify"]?.GetValue<bool>() ?? false).ConfigureAwait(false),
        "launch" => await LaunchAsync(p["forceVerify"]?.GetValue<bool>() ?? false).ConfigureAwait(false),
        "cancel" => Cancel(),
        "detectJava" => DetectJava(),
        "openPath" => OpenPath(p["which"]?.GetValue<string>() ?? "root"),
        "applyLauncherUpdate" => throw new InvalidOperationException("Tauri 更新器需要签名密钥，尚未启用"),
        "resetVerification" => ResetVerification(),
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
            CheckLauncherUpdate();
        }
        catch (Exception ex)
        {
            Log.Warn($"启动时获取更新信息失败：{ex.Message}");
        }
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
            ["autoMemoryMb"] = _settings.EffectiveMaxMemoryMb(),
            ["totalMemoryMb"] = TotalMemoryMb(),
            ["account"] = _account?.DeepClone(),
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

        if (_pendingLauncherUpdate is not null)
            node["launcherUpdate"] = LauncherUpdateNode(_pendingLauncherUpdate);
        return node;
    }

    private JsonArray BuildOptionalGroups(IEnumerable<ManagedFile> files)
    {
        var groups = files.Where(f => f.Policy == FilePolicy.Optional)
            .GroupBy(OptionalKey, StringComparer.OrdinalIgnoreCase)
            .OrderBy(g => g.Key, StringComparer.OrdinalIgnoreCase);
        var result = new JsonArray();

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
                ["enabled"] = paths.All(p => _settings.EnabledOptional.Contains(p, StringComparer.OrdinalIgnoreCase)),
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
        ["fullscreen"] = _settings.Fullscreen,
        ["autoJoinServer"] = _settings.AutoJoinServer,
        ["updateBaseUrl"] = _settings.UpdateBaseUrl,
        ["keepLauncherOpen"] = _settings.KeepLauncherOpen,
        ["skipVerify"] = _settings.SkipVerify,
        ["enabledOptional"] = new JsonArray(_settings.EnabledOptional.Select(x => (JsonNode)x).ToArray()),
    };

    private JsonNode SaveSettings(JsonObject p)
    {
        string? Str(string key) => p[key]?.GetValue<string>();
        bool? Bool(string key) => p[key] is { } n && n.GetValueKind() is JsonValueKind.True or JsonValueKind.False ? n.GetValue<bool>() : null;
        int? Int(string key) => p[key] is { } n && n.GetValueKind() == JsonValueKind.Number ? n.GetValue<int>() : null;

        if (Str("username") is { } u) _settings.Username = u.Trim();
        if (p.ContainsKey("javaPath")) _settings.JavaPath = Str("javaPath");
        if (Int("maxMemoryMb") is { } mm) _settings.MaxMemoryMb = Math.Max(0, mm);
        if (Str("extraJvmArgs") is { } ja) _settings.ExtraJvmArgs = ja;
        if (Int("windowWidth") is { } ww) _settings.WindowWidth = Math.Clamp(ww, 640, 7680);
        if (Int("windowHeight") is { } wh) _settings.WindowHeight = Math.Clamp(wh, 480, 4320);
        if (Bool("fullscreen") is { } fs) _settings.Fullscreen = fs;
        if (Bool("autoJoinServer") is { } aj) _settings.AutoJoinServer = aj;
        if (Str("updateBaseUrl") is { } url && !string.IsNullOrWhiteSpace(url)) _settings.UpdateBaseUrl = url.Trim().TrimEnd('/');
        if (Bool("keepLauncherOpen") is { } ko) _settings.KeepLauncherOpen = ko;
        if (Bool("skipVerify") is { } sv) _settings.SkipVerify = sv;
        if (p["enabledOptional"] is JsonArray arr)
            _settings.EnabledOptional = arr.Select(x => x?.GetValue<string>()).Where(x => !string.IsNullOrEmpty(x)).Select(x => x!).ToList();

        _settings.Save(_paths.SettingsFile);
        return BuildState();
    }

    private async Task<JsonNode> LaunchAsync(bool forceVerify)
    {
        if (_busy) throw new InvalidOperationException("正在忙，请先等当前操作完成");
        await RequireAccountAsync().ConfigureAwait(false);

        _busy = true;
        _work?.Dispose();
        _work = new CancellationTokenSource();
        var ct = _work.Token;

        try
        {
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

            if (_manifest!.Launcher is { Mandatory: true } mandatory &&
                SelfUpdater.IsNewer(mandatory.Version, GameLauncher.ThisVersion()))
                throw new InvalidOperationException($"必须先把启动器更新到 {mandatory.Version} 才能进游戏。");

            var session = GameSession.Offline(_settings.Username);
            Log.Info($"玩家 {session.Username}，离线 UUID {session.UuidDashed}");

            MinecraftRouteProxy? proxy = null;
            if (_settings.AutoJoinServer && _manifest.Servers.Count > 0)
            {
                Emit("status", new JsonObject
                {
                    ["phase"] = "选择游戏线路",
                    ["detail"] = "正在并行探测直连与兜底入口",
                    ["fraction"] = -1,
                });
                var routeProgress = new Progress<string>(detail => Emit("status", new JsonObject
                {
                    ["phase"] = "选择游戏线路",
                    ["detail"] = detail,
                    ["fraction"] = -1,
                }));
                proxy = await MinecraftRouteProxy.StartAsync(_manifest.Servers, routeProgress, ct)
                    .ConfigureAwait(false);
                var selected = proxy.Selection.Selected;
                Emit("routeSelected", new JsonObject
                {
                    ["kind"] = selected.Candidate.Kind.ToString(),
                    ["label"] = selected.Candidate.Label ?? selected.Candidate.Id,
                    ["remote"] = selected.Endpoint.ToString(),
                    ["latencyMs"] = Math.Round(selected.Latency.TotalMilliseconds),
                    ["local"] = proxy.LocalAddress,
                    ["available"] = proxy.Selection.Reachable.Count,
                });
            }

            await using var routeProxy = proxy;
            if (proxy is not null)
            {
                // Quick Play 和多人服务器列表都只指向回环代理，避免玩家从主菜单重连时绕过选路。
                ServersDat.EnsureServers(_paths, ProxiedServers(_manifest.Servers, proxy.LocalPort));
            }

            try
            {
                var launcher = new GameLauncher(_paths, _settings);
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
            _busy = false;
            _state.Save(_paths.StateFile);
            Emit("busy", new JsonObject { ["busy"] = false });
        }
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

    private async Task<JsonNode> InstallAsync(bool forceVerify)
    {
        if (_busy) throw new InvalidOperationException("正在忙，请先等当前操作完成");
        await RequireAccountAsync().ConfigureAwait(false);

        _busy = true;
        _work?.Dispose();
        _work = new CancellationTokenSource();
        var ct = _work.Token;

        try
        {
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
            var current = BuildState();
            Emit("state", current.DeepClone());
            return current;
        }
        finally
        {
            _busy = false;
            _state.Save(_paths.StateFile);
            Emit("busy", new JsonObject { ["busy"] = false });
        }
    }

    private JsonNode Cancel()
    {
        _work?.Cancel();
        return new JsonObject { ["cancelled"] = true };
    }

    private string AccountApi(string path)
    {
        var baseUrl = _settings.UpdateBaseUrl.TrimEnd('/');
        if (!Uri.TryCreate(baseUrl, UriKind.Absolute, out var uri))
            throw new InvalidOperationException("账号服务器地址无效");
        var local = uri.IsLoopback || uri.Host.Equals("localhost", StringComparison.OrdinalIgnoreCase);
        if (!local && !uri.Scheme.Equals(Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("账号登录只允许使用 HTTPS，请先为官网配置域名和证书");
        return $"{baseUrl}{path}";
    }

    private async Task<JsonNode> AccountLoginAsync(JsonObject p)
    {
        var identity = p["identity"]?.GetValue<string>()?.Trim() ?? "";
        var password = p["password"]?.GetValue<string>() ?? "";
        if (string.IsNullOrWhiteSpace(identity) || string.IsNullOrEmpty(password))
            throw new InvalidOperationException("请输入邮箱（或玩家名）和密码");

        var payload = new JsonObject { ["identity"] = identity, ["password"] = password };
        using var content = new StringContent(payload.ToJsonString(), Encoding.UTF8, "application/json");
        using var response = await _accountHttp.PostAsync(AccountApi("/api/v1/auth/launcher-login"), content).ConfigureAwait(false);
        var text = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
        var json = JsonNode.Parse(text)?.AsObject();
        if (!response.IsSuccessStatusCode)
            throw new InvalidOperationException(json?["detail"]?.GetValue<string>() ?? $"登录失败（HTTP {(int)response.StatusCode}）");

        _accountToken = json?["token"]?.GetValue<string>();
        _account = json?["user"]?.AsObject();
        var username = _account?["username"]?.GetValue<string>() ?? "";
        if (string.IsNullOrEmpty(_accountToken) || !OfflineAuth.IsValidUsername(username))
        {
            _accountToken = null;
            _account = null;
            throw new InvalidOperationException("登录服务器返回了无效账号信息");
        }
        _settings.Username = username;
        _settings.Save(_paths.SettingsFile);
        Emit("state", BuildState());
        return BuildState();
    }

    private JsonNode AccountLogout()
    {
        _accountToken = null;
        _account = null;
        Emit("state", BuildState());
        return BuildState();
    }

    private async Task RequireAccountAsync()
    {
        if (string.IsNullOrEmpty(_accountToken) || _account is null)
            throw new InvalidOperationException("请先登录 Batter MC 账号");

        using var request = new HttpRequestMessage(HttpMethod.Get, AccountApi("/api/v1/auth/me"));
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _accountToken);
        using var response = await _accountHttp.SendAsync(request).ConfigureAwait(false);
        var text = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
        var json = JsonNode.Parse(text)?.AsObject();
        if (!response.IsSuccessStatusCode)
        {
            _accountToken = null;
            _account = null;
            Emit("state", BuildState());
            throw new InvalidOperationException(json?["detail"]?.GetValue<string>() ?? "登录已过期，请重新登录");
        }
        _account = json?["user"]?.AsObject();
        var username = _account?["username"]?.GetValue<string>() ?? "";
        if (!OfflineAuth.IsValidUsername(username))
            throw new InvalidOperationException("账号玩家名无效，请联系管理员");
        _settings.Username = username;
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
        _state.InstalledPackVersion = null;
        _state.Save(_paths.StateFile);
        return new JsonObject { ["cleared"] = true };
    }

    private static JsonObject LauncherUpdateNode(LauncherRelease release) => new()
    {
        ["version"] = release.Version,
        ["notes"] = release.Notes,
        ["mandatory"] = release.Mandatory,
    };

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
            HttpRequestException => $"连不上更新服务器：{ex.Message}",
            TaskCanceledException => "网络超时，检查一下网络或者更新服务器地址",
            UnauthorizedAccessException => $"没有权限访问文件：{ex.Message}",
            IOException => $"读写文件失败：{ex.Message}",
            _ => ex.Message,
        };
    }
}
