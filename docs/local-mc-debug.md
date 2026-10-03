# 本机 Minecraft 隔离调试工具

入口：`scripts/local_mc_debug.py`。适用已安装的 **Windows、MC 1.21.1、NeoForge 21.1.250、Java 21 JDK**。复用已通过实际双客户端测试的启动元数据解析、原生客户端回调、WMI 超时和正常退出流程，保留现有启动器/SSO 专用 runner。无需 `a roc workspace`、MCP 连接、真实账号、密钥或下载新资源；workspace 空列表不代表本机工具不能运行。

## 依赖与职责

| 参数 | 输入 |
|---|---|
| `--project-root` | 包含此工具和 `tests/local-mc-debug/` 的 better-mc-remake 根目录；默认脚本所在仓 |
| `--instance-root` | 全新的 `<project-root>/build/local-mc-debug/<本轮唯一名字>`，存在即拒绝复用 |
| `--server-runtime` | 已安装服务端的只读根目录，含 NeoForge `libraries` |
| `--client-game` | 已安装客户端资源根目录，含 `versions`、`libraries`、`assets` |
| `--java-home` | 现有 Java 21 **JDK** 根目录，包含 java/javac；不改系统 JAVA_HOME |
| `--version`、`--natives-dir` | 默认 BatterMC5Remake；native 库默认对应版本的 `-natives` 目录 |
| `--port` | 本轮自有的空闲 loopback 端口；绑定预检拒绝冲突 |
| `--clients` | 实际客户端数量，默认 2；不按全部 Java 数量设置固定上限 |
| `--mod`、`--data-dir` | 可重复指定候选 mod 和它明确需要的数据目录，例如 tacz；不自动复制生产 mods/config |
| `--world` | 可选已知测试世界，**复制**到新服务端 `qa-world`，从不原地使用或修改源世界 |
| `--unix-temp` | 可选本机已有短临时目录，例如 `C:\Temp`，处理 WEPoll/AF_UNIX 路径问题 |

Python 3.10+ 标准库即可运行。可选已有 `psutil` 用于实际 MC 进程/RSS 清单，缺失时仍按可用物理内存预检，不据此宣称没有 MC。GPU 适配器/总显存由注册表读取，不调用 WMI；总显存不是当前剩余显存。已有 `nvidia-smi` 时以有界只读查询补充实际总/剩余/已用显存与利用率，并按测得剩余显存预检客户端预算；客户端回执另外记录实际 OpenGL renderer。可用 `--gpu-budget-mb` 显式覆盖剩余预算，并用 `--gpu-per-client-mb` 调整每个客户端的预算估计。没有显存测量时标明未知，操作者应评估实际 GPU 负载。结合其它 owner 的实际 MC 实例、GPU 利用率、RSS 和机器反馈调整客户端数及堆大小。内存预检按堆预算的 1.25 倍加预留内存估算；这些都是预检预算，不是实际峰值保证。

工具源码不绑定某个 task 目录或账号。新文件属于本调试工具 owner；不要改写现有 SSO runner、版本文件或其它 owner 的测试目录。共享手册只更新本工具段；提交前看最新 HEAD/status，并按文件范围提交，不能 `git add -A` 卷入别人的 dirty。

## 双实例冒烟命令

在已同步工具的 better-mc-remake 仓内打开 PowerShell。服务端/客户端沿用本机已安装资源，Java 21 JDK 通过当前会话的 `JAVA_HOME` 或显式路径传入：

```powershell
$Repo = (Get-Location).Path
$GameRoot = Join-Path $env:USERPROFILE 'workspace\dev\muxigame'
$JavaHome = $env:JAVA_HOME
if (-not $JavaHome) { throw '先将本会话 JAVA_HOME 指向已安装 Java 21 JDK，或把 $JavaHome 改成其路径' }
$Lab = Join-Path $Repo ('build\local-mc-debug\smoke-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
python -B .\scripts\local_mc_debug.py run `
  --project-root $Repo --instance-root $Lab `
  --server-runtime (Join-Path $GameRoot 'bmc5server') `
  --client-game (Join-Path $GameRoot '_client_test\game') `
  --java-home $JavaHome --version BatterMC5Remake `
  --port 25931 --clients 2 --accept-eula --unix-temp C:\Temp
```

默认不加载任何生产/game mod，只加载新实例内生成的 QA-only mod。冒烟检查：服务端 `Done` 且 tick 回调就绪、两个真实网络连接、各客户端看到同一玩家列表、隐藏且未聚焦的 GLFW 回执、QA 临时钻石 3 个经原生 Q 剩 2、原生 Ctrl+Q 剩 0且地上总数仍为 3，最后三进程正常退出。库存设置属于**服务端辅助测试**。这不是游戏通关、SSO、APP、视觉或物理键验证。

## 调试中发回调与停止

把上例加 `--mode hold --hold-seconds 600`，工具会保留连接供其它终端调用，期间每 15 秒输出状态。每轮独立 Lab、端口和日志；默认隐藏窗口，不抢 Session2 焦点。视觉/OS 按键测试需要先协调唯一焦点 owner，再明确使用 `--visible` 和实际 OS 输入工具；单纯改 visibility 不会自动提供视觉验收。

```powershell
python -B .\scripts\local_mc_debug.py status --instance-root $Lab
'{"type":"observe"}' | Set-Content -Encoding utf8 (Join-Path $Lab 'observe.json')
python -B .\scripts\local_mc_debug.py command --instance-root $Lab `
  --role host --json-file (Join-Path $Lab 'observe.json')
python -B .\scripts\local_mc_debug.py stop --instance-root $Lab --wait-seconds 120
```

`stop` 校验实例 owner marker，只向该 Lab 的命令文件写正常退出请求；主 runner 持有 Popen 句柄收集真实 exit code。服务端同时接收 stdin `stop`；QA-only Minecraft runTick Mixin 也检查本轮 stop 请求，帮助 ReceivingLevel 等等待界面正常退出。**没有 taskkill、按映像名停止所有 Java、终止 Windows Job 或自动强杀兜底。** `stop-request.json` 只证明请求发出，不证明 exit 0；主 runner 消失或加载阶段无法接受退出时，保留 `normalStopBlocked`/未验证结果，核对 owner marker 中的目录、PID、启动时间，再人工正常关闭该自有实例。不能把强杀或他人进程消失算通过。

命令 JSON 使用 UTF-8（支持 PowerShell UTF-8 BOM）。role 为 `server`、`host`、`guest`，多于两个客户端为 `client03` 等；客户端名字可用重复的 `--client-name` 自定义离线测试标识。回执 id/runId 由工具添加；一轮只允许一个控制者，外部 `command` 限定 `hold` 模式，避免与自动场景脚本抢命令。

| 回调 role/type | 内容 | 证明范围 |
|---|---|---|
| 任意/observe | 真实 MC 的 tick、连接、维度、库存或界面状态 | 游戏内状态 |
| 客户端/select/drop/aim/use/block-interact/chat-command | 原生 LocalPlayer/gameMode/网络动作；drop.all 对应单个/整堆 | 游戏内网络行为；不是 OS 键盘/鼠标 |
| 客户端/game-action | `game/action/value` 经可选公共框架 `GameNetwork.Action` 发送 | 包被发送；业务成功还需服务端 snapshot 断言 |
| 服务端/game-snapshot | `player/game` 经可选公共框架 `GameRuntime.snapshot` 返回 | 游戏真实快照；需要调用方写明确断言 |
| 服务端/execute | 在私有服务器执行 `command` | 辅助命令派发；不是无辅助玩家操作 |
| 服务端/inventory-set | `player/slot/item/count` | 辅助库存夹具，不代表正常取物流程 |
| 任意/stop | 自有客户端 Minecraft.stop / 自有 server.halt | 正常停止请求；exit code 仍由主 runner 收集 |

`game-action`/`game-snapshot` 只有显式添加匹配版本的公共框架/玩法/依赖 mod 时才可用；没有框架时回执明确失败，不伪造成功。加 `--mod <候选jar>`，依赖逐个加；需要的数据加 `--data-dir <tacz目录>`。QA 临时 core 配置关闭 identity/login，不复制生产凭据，也**不能证明真实登录/SSO链**。真实 SSO 应使用现有 `tests/run_terminal_mcef.py --native` 等对应 owner 的流程。

## 自动游戏内场景

`--mode scenario --scenario <JSON>` 顺序执行 `steps[]`，每一步可以有 `expect`（点分隔路径，数组用数字索引）、`waitSeconds`（0..60）、`timeoutSeconds`。没有写出的业务行为不能从 passed 推断。以下文件可直接用于纯 MC 回调场景：

```json
{"steps":[
  {"role":"host","type":"observe","expect":{"status.connected":true}},
  {"role":"server","type":"inventory-set","player":"DebugHost","slot":0,"item":"minecraft:diamond","count":3},
  {"role":"host","type":"select","slot":0},
  {"role":"host","type":"drop","all":false},
  {"role":"server","type":"observe","expect":{"status.byName.DebugHost.inventoryBySlot.0.count":2}}
]}
```

玩法 owner 可加入真实 `game-action` 后读取 `game-snapshot` 验证房间/目标/结算，并在自己的目录保存场景。Outbreak 的三章导演、终局、装备组件及胜负恢复完整验收仍可复用该仓原有 `tools/run_equipment_network_qa.py`；通用冒烟不替代完整玩法测试。

## 日志与通过判定

`local-mc-owner.json` 记录实例归属、来源 mod SHA、PID/启动时间、容量信息；`server/boot.log`、`host/boot.log`、`guest/boot.log` 记录启动输出，各实例还有 `logs/latest.log` 和 `crash-reports`。`compile.log`、`javac.args` 留存 QA 编译证据，`coordinator/status-*.json` 为可重试读取的进度；命令回执按 role/id 独立保存，`callback-receipts.json` 汇总已执行断言，`run-result.json` 汇总最终验收与 exit code。保存证据，不默认删除运行目录。

通过必须同时满足本轮声明的断言通过、实际客户端/服务端回执一致、所有本轮预期进程 **exit 0**、`normalExit=true`、无仍运行的 `normalStopBlocked`。只有 Done、端口监听、截图、退出码 0 或 stop 请求文件都不能单独作为通过。一般工具内部不判断画面观感；OS 物理 F/Tab/O、APP覆盖层、真实登录/SSO分别单独验收。

## 常见失败排查

1. **LoadingError / ModLoadingException**：先看相应 `boot.log` 的第一个错误和 `crash-reports`，核对 modId、重复 jar、NeoForge/MC/Java 版本、依赖上下界及 client-only mod 是否误入服务器。用 owner marker 中实际 SHA 核对候选；不要用重排 UI 或拉新账号代替诊断。
2. **ReceivingLevel / 卡在进服**：核对服务端 Done、两个进程是否仍活着、loopback端口、双方第一个异常、版本/registry是否匹配及服务端 status 的真实连接人数。工具先等 Done+tick 再起客户端，避免旧流程“客户端先连、服务端尚未监听”的竞态。若已连接但维度/区块未就绪，记录 status 的维度/tick，检查维度注册、出生点、chunk ticking 与玩家位置；不要把连接界面当作进图成功。
3. **131 OSHI/WMI 等待**：QA-only Mixin 在 `SystemReport.putHardware` 前设置 `GlobalConfig.OSHI_UTIL_WMI_TIMEOUT=2000`，日志应出现 `QA_HARDWARE_WMI_TIMEOUT_MS=2000`。不要重启 WMI/VM 服务。不要把此 QA mod 放到生产。
4. **WEPollSelectorImpl / AF_UNIX / EINVAL**：检查 `launch.args` 的 `jdk.net.unixdomain.tmpdir`，传本机可用的短 `--unix-temp`；原手册推荐 C:\Temp。长实例目录可能超出 Windows Unix-domain 路径限制，改路径再用新 Lab。
5. **回执/进度暂时读不到**：JSON 使用临时文件替换，读取对 Windows sharing lock/尚未完整替换容错；重试状态和回执，不强杀自有或其它 owner 的 MC。持续失败时保留错误和日志，修复后用新实例目录重跑。

不部署008，不改生产/user存档，不创建凭据，不建立新branch/worktree。所有会话先读本文并声明实例目录、端口、候选 SHA、焦点需求和实际资源预算；共享文件冲突由父级协调，广播摘要以本仓工具 commit 为准。


## Explicit shader client profile

`--mod` remains shared by both roles. Repeat `--client-mod` and `--server-mod`
for role-specific jars. The owner marker records each artifact hash and scope;
case-insensitive filename collisions within a role are rejected on Windows.
No process launch or normal-stop ownership rules change.

Copy a shaderpacks folder with `--data-dir`, then select its direct child with
`--shader-pack "Better MC - Low"`. This writes only the private client's Iris
selection (enabled, shadow distance 16); it does not import arbitrary config.
Missing packs, path separators, traversal and property-line injection are
rejected. The server does not receive this generated Iris configuration.

Explicit client settings: `--client-render-distance` and
`--client-simulation-distance` (2..32, defaults 3), `--client-max-fps` (1..260,
default 30), `--client-graphics-mode` (0..2, default 0). The marker records these
values. Server view/simulation remain 3; disclose this when comparing timings.

Run `python -B tests/test_local_mc_debug.py` and
`python -B tests/test_local_mc_shader_profile.py`. These verify ownership,
real CLI parsing and file preparation with mocked process launch. Their PASS
is not native shader or gameplay acceptance.


For a pack with an existing, audited loader dependency override, repeat
`--dependency-override modid=-dependency` (remove constraint) or
`modid=+dependency` (order after). Only these structured mod IDs are accepted,
at most 32 entries. Both private roles receive the generated FML setting and
the owner marker records the exact overrides. This does not import a config
directory. The shader fixture explicitly preserves its historical and current
server `sable=-scalablelux` setting; do not infer overrides for other packs.
The Python 3.12 regression run passes 6 ownership + 13 profile tests without
skips. Tests mock Java/resource/process operations; native acceptance is separate.

## Explicit PasterDream UI profile

`--client-pasterdream-ui true|false` writes only `[HUD] "enable mod ui"` in the
new private clients' PasterDream-Client.toml and records `clientSettings.pasterdreamUi`
in the owner marker. Omit it to retain mod defaults. No arbitrary config import,
server config change, resource-reload interception or production update is involved.

PasterDream 0.9.6 synchronizes its embedded UI pack at the first client login;
a changed selection causes a complete native resource reload. A fresh profile
using default true is not equivalent to an older profile configured false.
Keep this value identical for first-login, short-route and cross-dimension
comparisons. A false profile changes UI visuals and must not be reported as a
transparent product optimization. Native performance validation of this explicit
runner option is pending; preparation tests only validate config/role/receipt handling.
