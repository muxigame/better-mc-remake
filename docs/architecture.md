# BatterMC5Remake 启动器、官网与 OSS 分发

自建的 Minecraft 整合包启动器，配一个 FastAPI 官网；大文件全部由 OSS 分发。
替代 PCL——顺便解决 PCL 那个「启动前内存优化把 JVM 打死」的随机崩溃。

```
玩家电脑                                  官网后端                 文件分发
┌────────────────────────┐      ┌──────────────────────┐      ┌───────────┐
│ Tauri 2 + .NET sidecar │ JSON │ FastAPI 官网 / 账号 API │ 元数据│ 阿里云 OSS │
│ · 下载页 / 整合包同步    │◄────►│ /api/v1/manifest     │─────►│ 客户端/包体 │
│ · 账号登录 / Java / 启动 │      │ /api/v1/auth/*       │      └───────────┘
│ · 并行选路 / MC TCP 代理 │      └──────────────────────┘
└───────────┬────────────┘
            └──── Minecraft 本体 / 运行库 ────► Mojang / NeoForge CDN
```

整合包自己的 1.5 G（模组、配置、资源包）和客户端安装包走 OSS；
Minecraft 本体、144 个运行库、几千个资源文件走官方 CDN——
它们自带 SHA-1，而且不花你的带宽。

---

## 目录

| 项目 | 说明 |
|---|---|
| `client/tauri` | Tauri 2 / Rust 宿主：窗口、安全 IPC、原生文件选择和 sidecar 生命周期。 |
| `client/sidecar` | 无界面 .NET sidecar，通过 NDJSON RPC 向 Tauri 提供完整游戏能力。 |
| `client/sidecar/web` | 纯 HTML/CSS/JS 界面，由 Tauri 直接打进应用。 |
| `client/core` | 客户端引擎：同步、哈希、硬配置、Java 管理、启动、NBT。`net9.0-windows` |
| `client/protocol` | 客户端拥有的 C# 清单数据结构；服务端按 JSON schema 实现，不共享源代码。 |
| `server/app` | FastAPI 官网后端 + Python 清单构建工具。 |
| `server/web` | 无构建步骤的官网前端与实机图库。 |
| `scripts` | 工作区级构建和 OSS 发布脚本。 |

---

## 构建

开发机需要 .NET 9 SDK、Node.js（仅 Tauri CLI）、Python 3、Rust MSVC 工具链、Visual C++ Build Tools 和 WebView2。
玩家只需要安装生成的 NSIS 安装包，不需要自己安装 .NET、Node 或 Rust。

```powershell
.\scripts\build.ps1 -SelfTest
```

输出在 `artifacts\`：

- `artifacts\client\BatterMC5Remake-setup.exe` —— 推荐分发的 NSIS 安装包
- `artifacts\client\BatterMC5Remake.exe` + `battermc-backend.exe` —— 便携运行必须同时带着的两个文件
- `artifacts\client\launcher-release.json` —— 安装包版本号 / SHA-256 / 体积
- `artifacts\server\` —— FastAPI 官网与 API 部署包

服务器要放到 Linux VPS：

```powershell
.\scripts\build.ps1 -Target server
# 上传 artifacts\server 后：pip install -r requirements.txt
# python -m app.cli serve --port 8099
```

---

## 发布一次整合包更新

### 1. 配置发布源（只做一次）

编辑 `server\packspec.json` 的 `root`、版本号和 include / exclude 规则。

### 2. 改版本号，然后构建

```powershell
Push-Location .\server
.\.venv\Scripts\python.exe -m app.cli build --spec .\packspec.json --publish .\publish --link
Pop-Location
```

- 扫描整合包、算 SHA-1（带缓存，第二次几秒完成）
- 生成 `publish\manifest.json`
- 把要分发的文件同步到 `publish\`
- `--link` 用硬链接代替复制：同一块盘上秒完成、不占额外空间。
  **代价是改了源文件发布目录会立刻跟着变，所以改完必须重新 build。**
  不确定就别加这个参数，老老实实复制。

### 3. 起官网与 API

```powershell
.\server\run-server.ps1 -Port 8099
```

| 接口 | 用途 |
|---|---|
| `GET /api/v1/manifest` | 整合包清单 |
| `GET /api/v1/launcher/latest` | 启动器最新版本 |
| 清单内的 `url` | 直接指向 OSS 的整合包对象 |
| `GET /healthz` | 健康检查 |
| `GET /api/v1/site` | 官网版本、体积和客户端下载信息 |

Windows 上有现成的脚本：

```powershell
.\server\run-server.ps1                  # 生产式启动
.\server\run-server.ps1 -Reload          # 本地开发热更新
```

### 4. 发布新版启动器（待接 Tauri 签名更新器）

当前应直接分发 `artifacts\client\BatterMC5Remake-setup.exe`。旧的 `set-launcher --exe`
和“批处理覆盖运行中 exe”的更新方式属于 WinForms 单文件版，不能直接用于 Tauri + sidecar。
启用自动更新前要生成 Tauri updater 签名密钥，并同时签名主程序和 sidecar 安装包。

sidecar 目前会明确拒绝旧式 `applyLauncherUpdate`，避免只替换其中一个二进制造成版本错配。

### 5. 发布到阿里云 OSS

发布目标固定为 `oss://muxigame-prod-static-cn/bmc/release/latest/`，地域为 `cn-hangzhou`。
凭据保存在项目根目录的 `.env`，发布脚本会自动加载，再执行：

```powershell
.\scripts\publish-oss.ps1
```

脚本会上传 `artifacts\client\BatterMC5Remake-setup.exe`、`manifest.json` 和
`server\publish\` 中的按需下载文件。官网不再提供 1.5 GB 整包 ZIP。

---

## 配置怎么分权

这是整套东西的核心。分两层。

### 第一层：文件级策略

在 `packspec.json` 的 `include` 里按 glob 指定，先命中的规则说了算。

| 策略 | 行为 | 用在哪 |
|---|---|---|
| `Managed` | 每次启动校验哈希，不一致就覆盖；玩家删了补回来 | `mods/**`、`config/**`、`kubejs/**` |
| `Seed` | 只在本地不存在时投放一次，之后是玩家的，永不覆盖 | `options.txt`、键位 |
| `Optional` | 玩家在设置里勾了才装，取消勾选就删掉 | `shaderpacks/**` |

再加一个 `prune`：列出的目录里，凡是不在清单上的文件一律删除。

```jsonc
"prune": ["mods"]
```

`mods` 必须 prune，否则玩家私自塞进去的模组会让他连不上服务器。
**`config` 默认不 prune**——模组会在运行时自己生成配置文件，全删了只会每次启动
重建一遍，还会冲掉玩家合理的本地调整。

### 第二层：键级硬配置

文件级策略只能「整个文件归谁管」。真正要的是：
**同一个文件里，服务器点名的键强制同步，没点名的键玩家随便改。**

这就是 `overlays`：

```jsonc
"overlays": [
  {
    "path": "config/iris.properties",
    "format": "Properties",
    "enforce": {
      "maxShadowRenderDistance": 16,   // 服务器说了算
      "disableUpdateMessage": true
    }
  },
  {
    "path": "config/某模组-client.toml",
    "format": "Toml",
    "enforce": {
      "client/particleDensity": 0.5,   // '/' 分隔表名和键名 → [client] 下的 particleDensity
      "audio/volume": 0.8              // 表不存在会自动建 [audio]
    }
  },
  {
    "path": "config/某模组.json",
    "format": "Json",
    "enforce": {
      "quality/fog_quality": "FAST"    // 嵌套路径，中间层不存在会自动建
    }
  }
]
```

三种格式的实现要点：

- **Properties** —— 分隔符从文件内容里嗅探。`options.txt` 用冒号，
  `iris.properties` 用等号，不用手工指定。注释和排版原样保留。
- **Toml** —— 按行做外科手术，**不做完整解析也不重新序列化**。
  NeoForge 的配置注释里全是取值范围说明，冲掉玩家就没法调了。
  多行数组、行尾注释都处理了。
- **Json** —— 用 `JsonNode` 定位赋值。注意 JSON 不保留注释，别拿它改 json5。

每次启动、进游戏之前应用一次。玩家在游戏里改了被锁的键，下次启动会被改回来；
没被锁的键一直是他自己的。

---

## 客户端

### 目录布局

便携优先，所有数据放在 exe 旁边；exe 所在目录不可写（比如被装进 Program Files）
时自动退回 `%LOCALAPPDATA%\BatterMC5Remake`。

```
BatterMC5Remake.exe          Tauri 主程序
battermc-backend.exe         .NET 游戏引擎 sidecar（便携版必须同目录）
Better MC Remake [FORGE]\    干净游戏目录，相当于 .minecraft
runtime\              自动下载的 Java
launcher\
  settings.json       玩家设置，服务器不碰
  state.json          安装状态 + 文件哈希缓存
  manifest.cache.json 上次的清单，断网时用它离线启动
  logs\               启动器日志 + 游戏 stdout/stderr
  crash\              hs_err / 堆转储
```

项目根目录的 `Better MC Remake [FORGE]\` 是只读的干净游戏源，不存放客户端 exe 或构建产物。
构建只产出客户端安装包；整合包由客户端的“下载”页面从 OSS 按需安装。

### Java

不随包分发 JRE——那会让启动器从几十 M 变成上百 M。
先把机器上已有的 Java 全扫出来：`JAVA_HOME`、`~/.jdks`、各厂商安装目录、
注册表、`PATH`、以及 PCL / HMCL / 官方启动器下过的那些。
版本号优先读 JDK 的 `release` 文件（不用起进程，快得多），失败才退回 `java -version`。

实在没有合适的，才去下一份，解压到 `runtime\`，**不动系统里原有的 Java**。

国内直连 Adoptium 经常超时，所以可以在 `packspec.json` 里自己托管：

```jsonc
"java": {
  "major": 21,
  "url": "http://你的服务器:8099/files/runtime/jre21-win-x64.zip",
  "sha256": "...",
  "size": 47185920
}
```

### NeoForge 本体（第一次启动会多花一两分钟）

版本 JSON 的 `libraries` 列表里**没有**这几个东西：

```
net/neoforged/neoforge/<v>/neoforge-<v>-universal.jar    neoforge 这个「模组」本身
net/neoforged/neoforge/<v>/neoforge-<v>-client.jar       打过补丁的 Minecraft
net/minecraft/client/<mc>-<neoform>/client-*-extra.jar   从本体 jar 拆出来的资源
net/minecraft/client/<mc>-<neoform>/client-*-slim.jar
```

它们是 NeoForge 官方安装器用 binarypatcher 在**本地**从原版 `client.jar` 生成的。
原版 jar 不允许再分发，所以任何启动器都必须在玩家机器上跑一次安装器
（PCL、HMCL、官方启动器都是这么干的），没有捷径。

FML 不从 classpath 找它们，而是靠 `-DlibraryDirectory` 加上
`--fml.neoForgeVersion` / `--fml.mcVersion` / `--fml.neoFormVersion` 自己拼路径。
所以启动器要判断「装没装」也得先从版本 JSON 的 game 参数里读出这三个版本号。

**漏掉这一步的症状极具迷惑性**：FML 正常启动，然后每一个模组都报
`Missing or unsupported mandatory dependencies`，而缺的那个依赖是
`minecraft` 和 `neoforge` 本身。看起来像整合包坏了，其实是本体没装。

启动器会自动处理：检测缺失 → 下载官方安装器 → `--install-client` → 校验产物。
顺带把已经下好的本体 jar 复制到 `versions/<mc>/<mc>.jar`，省掉安装器再下一次 26 M。

想让玩家不依赖 `maven.neoforged.net`，把安装器托管到自己服务器上：

```jsonc
"minecraft": {
  "installerUrl": "http://你的服务器:8099/files/neoforge-21.1.250-installer.jar"
}
```

### 账号登录与游戏 UUID

账号系统已经从 Better MC 拆到独立 `muxi-auth`。`account.muxigame.com` 是统一的
OAuth 2.0 / OpenID Connect Provider，负责注册、邮箱验证、密码、角色和统一身份。

Better MC 官网是 confidential OIDC client，使用 Authorization Code + PKCE；回调完成后
只保存本站 HttpOnly Session。Better MC Launcher 是 public native client，不内置
`client_secret`，使用系统浏览器、Authorization Code + PKCE，并监听 `127.0.0.1` 随机端口
接收回调。启动器只在内存中保存 Access/Refresh Token，不接触用户密码。

游戏本体暂时不接 OAuth。启动器从 UserInfo 取得统一账户用户名后仍调用
`GameSession.Offline(username)`，因此 Minecraft 和服务器侧认证逻辑保持简单。

游戏服目前仍是离线模式，因此验证通过的账号玩家名会固定映射到离线 UUID。

UUID 用 `UUID.nameUUIDFromBytes(("OfflinePlayer:" + 用户名).getBytes(UTF_8))`，
也就是 RFC 4122 的 version 3（MD5）。必须和服务端算的一模一样，
否则玩家进服变成新号、背包和进度全没。

> ⚠️ **这里不能用 `new Guid(byte[])`。** .NET 的 Guid 二进制布局前三段是小端，
> 直接塞进去会把字节序搞反，得到一个和服务端对不上的 UUID。代码里是按大端手工拼的。

自检里拿 BMC4 服务器 `usercache.json` 真实记录过的三个 UUID 做对照，
等于用 Minecraft 自己的实现当标准答案。

### 服务器列表

`servers.dat`（未压缩 NBT，和 gzip 的 `level.dat` 不一样）。

### 游戏线路选择与本地代理

主服务器可以在清单的 `routes` 里提供多个入口：

```jsonc
{
  "name": "Batter MC 5",
  "host": "play.example.cn",
  "port": 25565,
  "primary": true,
  "routes": [
    { "id": "lan", "kind": "LanDirect", "transport": "tcp", "host": "192.168.1.8", "port": 25565 },
    { "id": "v6", "kind": "Ipv6Direct", "transport": "tcp", "host": "240e:...", "port": 25565 },
    { "id": "relay-hz", "kind": "Relay", "transport": "tcp", "host": "relay-hz.example.cn", "port": 443 }
  ]
}
```

点击开始游戏后的顺序是：

1. 正常完成整合包、Java、NeoForge 和硬配置准备。
2. 并行解析所有 TCP 候选，再并行执行有超时的 TCP 握手探测。
3. 按 LAN → IPv6 → IPv4 → 端口映射 → UDP 隧道 → TCP 隧道 → Relay 分组，组内按人工优先级和握手 RTT 排序。
4. 只在 `127.0.0.1` 上启动一个随机端口的 TCP 代理。
5. Minecraft 的 Quick Play 参数和主服务器列表项都临时写成本地代理地址，不再暴露实际入口给游戏。
6. 每次 MC 建立连接时先用首选线路；线路已经失效则自动尝试其他已探测可用线路。
7. 游戏退出或用户取消时停止监听和现有转发，并把服务器列表恢复成公开兜底地址。

清单没有 `routes` 时，旧的 `host`/`port` 会自动成为兼容候选。`UdpTunnel` 是协议预留类型；
当前数据面只接受 `transport: tcp`，不会把 QUIC/UDP 端点错误地当作裸 Minecraft TCP 端口。
后续接入房间控制面和加密 QUIC sidecar 时仍复用同一个候选模型，本地 Minecraft 地址不需要再改。
清单里 `forced: true` 的条目每次启动写回列表顶部，玩家在游戏里删掉也会回来；
玩家自己加的服务器原样保留。

字符串用 Java 的 modified UTF-8 编码——和标准 UTF-8 的差别只有 `U+0000` 和
BMP 外的字符（emoji），服务器名字里带 emoji 时这个差别就是乱码和正常的区别。

### 这个启动器不做内存优化

**故意的。**

游戏加载期间去清空 Java 进程的工作集，是随机崩溃最常见的来源：
死亡点不固定、没有 Java 异常、没有 `hs_err`、没有 Windows 事件日志、退出码 -1。
本项目就是因为这个问题才写的。

崩溃诊断会把这种情况直接指出来，而不是让人对着一个空白的崩溃报告发呆。

---

## 诊断

三个命令行模式，出问题让玩家跑一条，比让他描述「打不开」有用得多。
报告同时会存到 `launcher\verify-report.txt`。

```powershell
battermc-backend.exe --selftest   # 内部逻辑自检（UUID、配置、NBT、规则、选路与 TCP 代理）
battermc-backend.exe --verify     # 只检查不动手：算出要下载/删除什么、找 Java
battermc-backend.exe --install    # 完整跑一遍准备流程，但不启动游戏
battermc-backend.exe --launch     # 准备并启动游戏，全程走控制台，不开界面
```

> PowerShell 里测这些要注意：**它不会等 GUI 子系统的 exe**。
> 用 `$p = Start-Process ... -PassThru; $p.WaitForExit()`，
> 直接 `& exe --install` 会立刻返回，既拿不到退出码也收不到输出。

其他开关：

| 开关 | 用途 |
|---|---|
| `--game-dir <路径>` | 指定游戏目录 |
Tauri 开发模式下可以用系统 WebView2 开发者工具；发布版默认关闭。

---

## 一些实现上踩过的坑

- **NeoForge 的 `universal` / `client` jar 不在版本 JSON 的 libraries 里。**
  必须跑一次官方安装器在本地生成，见上面那节。漏掉的表现是每个模组都报缺
  `minecraft` / `neoforge` 依赖，很容易误判成整合包坏了。
- **静态文件服务必须允许点开头的文件。** 整合包里确实有
  （`config/euphoria_patcher/.data.json`、各光影包的 `.gitignore`），
  发布工具会把它们作为普通 OSS 对象上传，客户端按清单中的完整 URL 下载。
- **版本 JSON 换名字时内部的 `id` 也得改。** 不改的话 `${version_name}` 展开成旧名字，
  `-DignoreList=client-extra,旧名.jar` 失效，securejarhandler 会把本体 jar 当模块加载，
  启动直接失败。`packspec.json` 里 `map[].rewriteVersionId` 负责这件事。
- **`-Xss1M` 带 `{"os":{"arch":"x86"}}` 规则。** 我们发的是 x64，规则求值必须正确，
  否则会给 64 位 JVM 加上 32 位才需要的栈大小。
- **CSS 里类选择器的 `display:flex` 比 `[hidden]` 的默认 `display:none` 优先级高。**
  不显式写 `[hidden]{display:none!important}`，设置面板一进来就是展开的。
- **PowerShell 不会等 GUI 子系统的 exe。** 脚本里测 `--install` 要用
  `Start-Process -PassThru` 再 `WaitForExit()`，否则拿不到退出码也收不到输出。

---

## 协议版本

`manifest.json` 顶层有 `schema`。客户端目前只认 `1`。
以后要做不兼容改动，加字段并递增它。
