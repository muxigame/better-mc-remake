### 6.5 本机 Minecraft 隔离调试（2026-10-03）

通用工具位于已同步 dev 的 `better-mc-remake/scripts/local_mc_debug.py`，完整指南为该仓 `docs/local-mc-debug.md`，游戏内辅助类在 `tests/local-mc-debug/`。使用父级确认的当前工作副本，不根据本手册位置猜相邻旧 clone。工具不依赖 a roc workspace/MCP；workspace 空列表不代表本机调试不可用。

先准备现有 Python 3.10+、Java 21 JDK、MC 1.21.1/NeoForge 21.1.250 服务端 libraries 和已安装客户端 versions/libraries/assets/natives。在工具仓根运行：

```powershell
$Repo = (Get-Location).Path
$GameRoot = Join-Path $env:USERPROFILE 'workspace\dev\muxigame'
$JavaHome = $env:JAVA_HOME  # 当前会话指向现有 Java 21 JDK；未设置则显式填入其路径
if (-not $JavaHome) { throw '请指定已安装 Java 21 JDK 的路径' }
$Lab = Join-Path $Repo ('build\local-mc-debug\smoke-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
python -B .\scripts\local_mc_debug.py run `
  --project-root $Repo --instance-root $Lab `
  --server-runtime (Join-Path $GameRoot 'bmc5server') `
  --client-game (Join-Path $GameRoot '_client_test\game') `
  --java-home $JavaHome --port 25931 --clients 2 --accept-eula --unix-temp C:\Temp
```

每个 owner 使用不同的新实例目录/空闲端口；实例必须在本仓 `build/local-mc-debug/` 下，原目录存在即拒绝覆盖。只读复用安装资源，`--world` 只复制已知测试世界，候选/依赖通过重复 `--mod`、所需数据通过 `--data-dir` 明确传入。不复制生产登录配置、凭据或用户存档，不部署008。默认隐藏、不抢 Session2 焦点；真实视觉或 OS 物理按键测试须先协调焦点 owner，再明确使用可见实例。

容量按可用物理内存、GPU 显存/利用率、实际 MC 的 RSS 和实例负载安排。已有 psutil/nvidia-smi 时记录实际数据，不调用 WMI，不把所有 Java 算作 MC，也不设置固定“Java <4”门槛；堆、内存预留与 GPU 预算均可参数化。显存不可测时明确标记未知并由操作者评估。更多实例前先读其它 owner 的现有负载，而非只数进程。

加 `--mode hold --hold-seconds 600` 保持实例，`status` 查看状态，`command --role host/guest/server --json-file <命令JSON>` 调用游戏内回调；自动场景用 `--mode scenario --scenario <JSON>`。正常清理自有实例：

```powershell
python -B .\scripts\local_mc_debug.py status --instance-root $Lab
python -B .\scripts\local_mc_debug.py stop --instance-root $Lab --wait-seconds 120
```

owner marker、各实例 boot.log/latest.log、compile.log、coordinator/status/独立命令回执、callback-receipts.json 和 run-result.json 均留在 Lab。通过需声明的断言通过、实际客户端/服务端回执一致、全部预期自有进程 exit 0、normalExit=true 且无 normalStopBlocked。stop 请求只是请求，不等于通过；退出使用 Minecraft.stop、server stop/halt，不按映像名 taskkill、不关闭 Job 强杀、不处理他人 PID。

冒烟验证真实网络连接及原生 Q/Ctrl+Q；库存准备、服务端命令等属于游戏内辅助测试。game-action 发包回执不证明业务已成功，须再读 game-snapshot 写明确断言。这些不能证明 OS 物理 F/Tab/O、画面观感、APP覆盖层或真实登录/SSO；对应 owner 继续各自完整验收。

LoadingError 先看第一个 ModLoadingException/依赖或重复 jar 错误及实际候选 SHA；ReceivingLevel 先核服务端 Done+tick、端口、双方首个异常、registry/版本、实际人数、维度与区块就绪。QA-only Mixin 在 SystemReport.putHardware 前将 OSHI WMI timeout 设为 2000，勿重启 WMI/VM 服务。WEPoll/AF_UNIX/EINVAL 用显式短 `--unix-temp`（C:\Temp 是示例，可改本机可用短目录）。JSON 文件锁/替换期间重试读取，不能把卡住/强杀当作通过。修复后使用新 Lab。
