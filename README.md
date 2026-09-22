# Batter MC 重做 — 1.21.1 / NeoForge

2026-09-19 从 BMC4 (1.20.1/Forge) 迁到 **Better MC [BMC5] v53**（MC 1.21.1 / NeoForge 21.1.250）。

## 目录

| 目录 | 内容 |
|---|---|
| `..\bmc5server\` | 同级的真实 Minecraft 服务端；世界、配置和模组不进入本仓库 |
| `..\muxi-game-core\` | 独立的通用服务端功能模组仓库；昵称身份同步是第一个功能 |
| `BMC5Pack\` | 客户端源。405 mod jar + 497 config + 20 资源包 + 20 光影 + options.txt；`BMC5-v53.zip` 是 CurseForge 原包，PCL 可直接导入 |
| `待处理_无1.21.1版\` | 没有 1.21.1 版本的 mod：内容型 3 待解决 / 工具型 27 / 已解决 22 / 冲突 1，见其 `清单.csv` |
| `client\` | 独立客户端工程：Tauri 2 + .NET 9 sidecar；协议模型归客户端所有 |
| `pack\` | MC 客户端整合包发布域：版本规则、干净客户端源、staging、增量 OSS 发布 |
| `server\` | FastAPI 官网 + API + 已发布版本快照；不负责上传客户端内容 |
| `scripts\` | 跨项目构建脚本 |
| `artifacts\` | 本机构建产物，不属于源码仓库 |
| `BMC5迁移评估.csv` | 402 个旧 mod 的逐条迁移结论 |

## 启动

```
powershell -File ..\bmc5server\start-muxi.ps1
```

**必须 Java 21**。`start-muxi.ps1` 自动检测本机 Java 21，也支持 `-JavaExe` 显式指定；不使用旧机器的硬编码路径，不启停 FRP。

首次启动实测 `Done (9.723s)`，world 正常生成，已确认加载的额外维度：aether、deeperdarker (otherside)、twilightforest。

## 自建启动器 BatterMC5Remake

`client\` 和 `server\` 是两个独立工程。它们替代 PCL 并提供配套更新服务。写它的直接原因是
**PCL 启动前的「内存优化」会把正在加载的 JVM 打死** —— 死亡点随机、没有 Java 异常、
没有 hs_err、没有 Windows 事件日志、退出码 -1；同一套包用脚本直接启动则退出码 0。
这个启动器不做任何内存优化。

```powershell
scripts\build.ps1 -SelfTest         # 构建客户端和官网后端到 artifacts\
server\run-server.ps1               # 启动官网和 API
client\publish.ps1                  # 只发布启动器安装包
client\promote.ps1 -Version 1.1.2   # 将已有历史版本晋升/回滚为 latest，不改二进制
pack\publish.ps1                    # 只增量发布 MC 整合包内容 + manifest
```

OSS 发布位置：`oss://muxigame-prod-static-cn/bmc/release/latest/`（杭州地域）。

能力：邮箱注册与验证、官网/客户端统一账号、整合包增量同步（带哈希缓存，8966 个文件校验约 1 秒）、**键级硬配置下发**
（服务器点名的键每次启动强制纠正，没点名的键保留玩家自己的值）、
Java 自动发现与按需下载、账号验证后的固定玩家名（游戏侧仍使用对应离线 UUID）、servers.dat 维护、
崩溃诊断。客户端 UI 已迁到 Tauri 2，并使用 Tauri updater 对完整 NSIS 客户端做签名自更新；
更新会同时替换 Tauri 主程序、.NET sidecar 和前端资源，不会只替换单个 exe。

开始游戏时客户端会先并行解析、探测主服务器的所有 TCP 路线候选，按
LAN → IPv6 → IPv4 → 端口映射 → 隧道 → 国内 Relay 的优先级和实测握手延迟选路。
Minecraft 只连接 `127.0.0.1` 上的临时端口，由 sidecar 代理到选中的线路；首选线路在
真正连接时失效会自动尝试其余已探测可用线路。当前清单先把原有公网入口登记为国内
Relay 兜底，后续控制面可以直接下发同一 `routes` 结构的房主 LAN/IPv6/映射/隧道候选。

实测：全新安装 → 主菜单 175 秒（PCL 是 202 秒），71 项自检全通过。

## 客户端专属 mod 的剔除

服务端 mods = 客户端 350 - 73。剔除依据记录在两个文件：
- `client_only_removed.json` — 72 个，靠 Modrinth `server_side=unsupported` 元数据 + BMC4 经验名单自动判定
- `removed_by_bootloop.txt` — 1 个（`distraction_free_recipes`），元数据没标但实际会让专用服崩溃，靠启动循环抓出来

被剔的 jar 都在 `..\bmc5server\removed_client_mods\`，没有删除。

## 在官方底包之外加装的

| mod | 版本 | 服务端 | 客户端 | 用途 |
|---|---|---|---|---|
| Rhino | 2101.2.8-build.91 | ✓ | ✓ | KubeJS 的 JS 引擎（前置） |
| KubeJS | 2101.7.2-build.377 | ✓ | ✓ | 配方/事件/注册表脚本，平衡层主力 |
| Better Advanced Tooltips | 2101.1.0-build.5 | ✓ | ✓ | KubeJS 的必需前置 |
| LootJS | 1.21.1-3.7.0 | ✓ | ✓ | 掉落表脚本 |
| ProbeJS | 8.0.3 | — | ✓ | 生成类型定义供 IDE 补全，纯开发用 |
| Easy NPC: Core | 7.12.1 | ✓ | ✓ | NPC 实体 + 对话 + 交易，替代 Custom NPCs |
| Easy NPC: Config UI | 7.12.1 | ✓ | ✓ | Easy NPC 的配置界面和网络层，必需 |
| IceAndFire CE | 2.0 | ✓ | ✓ | 冰火传说社区版，替代停更在 1.20.1 的原版 |
| Uranus | 2.4.1 | ✓ | ✓ | IaF-CE 前置 |
| Jupiter | 2.3.7 | ✓ | ✓ | IaF-CE 前置（配置框架） |
| Eternal Starlight | 0.9.0 | ✓ | ✓ | 星光维度，带 Boss 追踪进程链，替代 Blue Skies |
| [调色方块] RGB Blocks | 1.21.1-1.1.9.2 | ✓ | ✓ | 任意 24 位 RGB 取色器 + 油漆桶 |
| [马铠附魔] Shiny Horses Reborn | 1.2.0 | ✓ | ✓ | 马铠可附魔，原 ShinyHorses 的社区续作 |
| [任务击杀计数修正] Quest Kill Task | 0.3.1 | ✓ | ✓ | FTB Quests 击杀任务的团队计数修正 |

Architectury 13.0.11 是 BMC5 自带的，版本正好满足 KubeJS，无需额外处理。
ConnectorExtras 自带 `kubejs-bridge`，走 Sinytra Connector 加载的 Fabric mod 也能被脚本覆盖。

脚本目录：`..\bmc5server\kubejs\`（`startup_scripts` / `server_scripts` / `assets` / `data`）。
加装后实测启动 `Done (8.437s)`。

**Easy NPC 注意**：Modrinth 上的 `easy-npc` 是 bundle 包，本身不含代码，只给能自动解析依赖的启动器用。手动装必须放 Core + Config UI 两个 jar。用法：刷怪蛋或 `/easy_npc` 命令生成 NPC，用 NPC 配置法杖或命令开图形界面配对话、交易、皮肤（支持玩家名和 URL）、action。内置 Immersive Melodies / Armourer's Workshop / Epic Fight 集成，其中 Immersive Melodies 我们旧包里就有且已迁过来。

## 已知非致命报错

以下都是 BMC5 官方包自带的问题（已比对加装 KubeJS 之前的日志确认），条目被跳过，不影响启动：

- `Fabric Resource Conditions: Unknown resource condition key: sawmill:flag` —— Repurposed Structures 的村庄数据包引用了未装的 mod
- `Access transformer file provided by mod wdutils does not exist`
- `Moonlight: Fabric API detected!` —— BMC5 自带 Sinytra Connector 导致
- `RecipeManager: piglinproliferation:deepslate_fire_ring` 配方缺 `type` 字段
- 装了 KubeJS 后会多报 `Couldn't load tag aether_villages:collections/ancient_aether_biomes` 之类 —— aether_villages 引用了 Ancient Aether / Aether Redux 的群系标签而这两个 mod 没装，KubeJS 只是把原本被静默吞掉的数据问题显式打出来了

## 待办

1. 把 `待处理_无1.21.1版\内容型_要找替代\` 里 25 个逐个定方案（详见该目录 README）
2. 重新做平衡层（KubeJS / LootJS）和任务线（FTB Quests）——1.20.1 的脚本因为 Curios→Accessories、BOP→OTBWG 等体系替换，物品/群系 ID 全变，不能直接搬
3. 客户端分发链（PCL + 补丁程序 + wd_filehost）需要跟着换；WebDisplays 没有 1.21.1 版，`wd_filehost` 那套要重新选型

## 已解决的迁移缺口

| 原 mod | 解决方式 |
|---|---|
| Custom NPCs | Easy NPC 7.12.1。旧版 Custom NPCs 数据格式不通用，NPC 要重做。 |
| Ice and Fire | IceAndFire Community Edition 2.0（IAFEnvoy 的非官方 fork，原版停在 1.20.1）。**不含 myrmex 蚁族系列**，其余内容齐全，另加了下界合金相关内容。用自己的 Uranus/Jupiter 库，不再依赖 Citadel |
| It Takes A Pillage | BMC5 自带 Continuation 版，无需处理 |
| Dragon Mounts Patches | BMC5 改用 Dragon Mounts Remastered，补丁不再需要 |
| Blue Skies | 已装 **Eternal Starlight 0.9.0**。Blue Skies 停在 1.20.4（末次 2025-05-29）。评估并否决了 The Undergarden（15 群系但官方 feature 列表只写 1 个地牢）和 The Bumblezone（主题与整包调性不符）。用 1 个有内容的维度换掉原来 2 个 |
| The Aether: Redux | 放弃。BMC5 自带 **Deep Aether 1.1.5.1**（326 万下载，2026-03 更新）占同一生态位，已确认通过 TerraBlender 向 `THE_AETHER` 注册了 `deep_aether` / `rare` 两个群系区域。Redux 停在 1.20.1（末次更新 2025-05-29） |

Ice and Fire Delight 复查后仍无 1.21.1 版本，已按用户决定放弃。

**下界体系**：BMC5 v53 自带 `EternalNether v21.1.3`（Bygone Nether 的续作），另有 MyNethersDelight、NetherChested、YungsBetterNetherFortresses、formationsnether、advancednetherite、MoogsNetherStructures、netherite_tweaks_luna，下界内容已足够密，无需再补。

**末地注意**：包里的 `YungsBetterEndIsland` 是 YUNG 的 mod，**不是** paulevs 的 BetterEnd(BCLib 系)。后者不在 BMC5 里，所有针对它的补丁/附属一律无意义。

**关于天境2(Aether II)**：查过了，最低支持 1.21.11，且全部版本是 alpha（最新 `26.1.2-alpha.4.1`，2026-07-24）。本包是 1.21.1，用不了。若将来整包升到 1.21.11+ 可以再评估。

**包内当前 Aether 体系**：`aether 1.5.10` + `deep_aether 1.1.5.1` + `AetherVillages 1.0.8` + 三个官方附属（enhanced_extinguishing / protect_your_moa / treasure_reforging）。1.21.1 上还可加但未装的：Aether Addon: Emissivity、Explore Ruins: The Aether - Dungeons、Aether's Delight、Aetherial Islands。

## 模组命名规范

**底包自带的不动**；我们自己加的一律在文件名前缀 `[中文名]`，紧贴文件名不留空格。中文名来源优先级：

1. 旧 BMC4 包里已有的命名（迁移时直接沿用，不重复查）
2. MC 百科 mcmod.cn 的中文名
3. 都没有则意译

已加装的模组全部遵循此规范，见下方清单。

## 当前额外维度

`aether`（The Aether + Deep Aether）、`deeperdarker:otherside`、`twilightforest`、`eternal_starlight:starlight`、`extradelight:cornfield`、`pasterdream` —— 共 6 个。

BMC4 实测也是 6 个（aether / twilightforest / otherside / blue_skies×2 / extradelight），所以数量持平、成分不同。整合包宣传页写的「7 个维度」是把原版主世界/下界/末地算了进去。

重建取向是**质不取量**：Eternal Starlight 有完整的「找传送门遗迹 → 打 Gatekeeper → Seeking Eye 定位 Boss → Glimmering Tablet 追踪」进程链，而被否决的候选多是地形大但结构稀疏。

已知无害警告：`biolith: Ignoring world 'eternal_starlight:starlight'; unknown dimension type` —— Biolith 不认这个维度类型，不会往里注入群系，属正确行为。`StructureEssentials: Non-unique structure_set salt:0` 在装它之前就有 9 次（暮色森林 + 原版结构本身的问题），装后仍是 9 次。

## 帕斯特之梦结构生成调整

模组自带 Cristel Lib 生成的配置，运行时会转成 `cristellib:runtime_pack` 数据包覆盖 jar 内定义，**不需要改 mod 本体也不需要挂 Paxi 数据包**：

```
config/pasterdream/structure_placement_config.json5   spacing / separation / salt
config/pasterdream/structure_toggle_config.json5      每个结构的开关（349 行）
```

2026-09-19 按需求整体改稀，114 项全部调整，原文件备份在同目录 `.bak`。规则：

| 类别 | 处理 |
|---|---|
| 入口 `struct_dyedream_crack_1_set` | **保持原值 37/20**（592 格），与原版废弃传送门（640 格）同档 |
| 会刷爆的 4 个 | **×4** |
| 梦境维度内 29 个地貌装饰物（原 spacing ≤ 10） | **×1.2**，略微改稀 |
| 其余 81 项 | **×1.5** |

会刷爆的 4 个调整明细：

| 结构 | 群系 | 原 | 新 | 平均间距 |
|---|---|---|---|---|
| fisherman_hut_0 | beach | 11/7 | 44/28 | 176 → 704 格 |
| fisherman_hut_1 | beach | 12/8 | 48/32 | 192 → 768 格 |
| picnic_basket_structure_set | plains + 梦境 | 20/10 | 80/40 | 320 → 1280 格 |
| warped_relic_0 | warped_forest | 15/8 | 60/32 | 240 → 960 格 |

`shadow_world_doors` 用同心环放置（`distance 32, spread 3, count 64`，与原版要塞同机制），全世界固定 64 个，不受 spacing 影响，未改。

梦境维度内的地貌装饰物（气球 11 套、风蚀岩、暗影锁链、大气泡、石柱、风沼树等）是维度观感的组成部分而非可探索结构，因此只做 ×1.2 的轻微调整，例如气球 160→192 格、暗影锁链 160→192 格、石柱 80→96 格。其中 `wind_infested_stone_0/1` 原值 2/1，×1.2 取整后不变。

改完实测：删世界重新生成，`Done (19.623s)`，Cristel Lib 正常加载 `cristellib:runtime_pack`，6 个维度齐全。原始值始终保留在 `structure_placement_config.json5.bak`。
