# muxi UID 身份与中文昵称

## 职责

- 启动器从已登录 muxi 身份的 `muxi_uid` 生成十进制 Minecraft 登录名，例如 `10000`。
- 离线 UUID 始终等于 Java `UUID.nameUUIDFromBytes("OfflinePlayer:10000".getBytes(UTF_8))`。
- 用户名和昵称均不参与 UUID 计算。
- 账户服务提供受服务端专用密钥保护的最小资料接口，只返回 UID、登录名、UUID 和显示昵称。
- `muxi-identity-1.0.0.jar` 仅安装在游戏服务端，进服后异步读取资料，每 60 秒刷新在线玩家昵称。
- Simple Nicknames 安装在客户端与服务端，负责名牌、聊天、Tab 和计分板的显示。服务端关闭 MiniMessage 格式解析，允许重复中文昵称。

## 本机目录

客户端源：`pack/source/Better MC Remake [FORGE]/`

用户提供的服务端源：`BMC5Server/BMC5Server/`（原始目录保留，不启动、不重建旧世界）。

服务端专用配置：`BMC5Server/BMC5Server/config/muxi-identity-bridge.json`。其中 `serverKey` 与账户服务环境变量 `MUXI_MC_PROFILE_KEY` 一致，**不可放进客户端、公开整合包、Git 或截图**。

## 构建与安装

在仓库根目录，用 JDK 21 或更新版本编译（输出目标 Java 21），不打包任何 Minecraft/第三方类：

```powershell
..\.ops-venv\Scripts\python.exe game-server\identity\build.py `
  --java-home 'C:\Program Files\Java\jdk-24' `
  --nickname-jar artifacts\uid-nickname-qa\simplenicknames-1.21.1-neoforge-0.8.0.jar
..\.ops-venv\Scripts\python.exe game-server\identity\install.py
```

安装器校验 Simple Nicknames 官方 SHA-512；只写明确的 mod/config 文件，以原子替换避开既有 staging 硬链接。原配置备份位于忽略的 `artifacts/uid-nickname-qa/install-backups/`。

Simple Nicknames 使用作者原始 Modrinth 下载地址；其 ARR 二进制不转载到 OSS。清单构建校验固定 SHA-1，发布脚本跳过外部二进制上传。自身桥接模组源码为 MIT（见 LICENSE）。

## 上线顺序

1. 账户服务部署新的只读昵称接口，并持久化 `MUXI_MC_PROFILE_KEY`。
2. 停服备份旧世界，将本目录指定的两个 mod 与配置同步至实际运行服；使用 Java 21。
3. 与 UID 版启动器、整合包一起切换。不要只推客户端而让运行服仍缺少昵称模组。
4. 老用户名到数字 UID 的首次切换会改变离线 UUID；旧世界有玩家数据时，应先单独规划 playerdata、advancements、stats 与各 Mod UUID 绑定数据迁移。安装器不迁移或删除任何存档。

## 安全边界

这不是 Minecraft 进服认证模组。UID 是公开标识，不是密码。`online-mode=false` 下，其他离线客户端仍可能使用同一 UID 冒名；平台登录仅约束官方启动器。公开运营或对接积分、余额前，必须另外接入服务端验证的一次性进服凭证，不能把 UID 本身视作身份凭证。

## 测试隔离

测试根目录：`artifacts/uid-nickname-qa/`。测试服使用独立普通副本、回环地址及全新 QA 世界，不与干净包共享可写硬链接。

`client-before.json` 和 `clean-client-audit.json` 对比全部客户端文件 SHA-256；允许变化仅为新增 Simple Nicknames、其配置、以及 Entity Culling 的 `minecraft:player` 兼容项。

`native-harness-result.json` 是真实 NeoForge 进程中的 FakePlayer 集成测试记录，覆盖异步 HTTP、原生昵称 API、中文重名、改昵称保持 UUID 和禁止玩家绕过平台改昵称。它不等于真人客户端的头顶名牌/Tab 视觉验收。

2026-09-22 全整合包隔离副本启动到 `Done`，327 个 Mod 中已加载昵称桥接。停服时触发
原包已有的 `PackAnalytics 1.0.5 / BCC` 兼容错误：`NoClassDefFoundError: dev/wuffs/bcc/data/BetterStatusServerHolder`。
同一错误也出现在原包 `2026-09-20` 的崩溃记录中。本次没有为此删除/升级原包中的其他 Mod；
不能把这次启动检查描述为“全服无错误验收”。
