# 玩家积分与平台游戏管理（集成默认关闭）

普通每日任务成功领取 +1；`TaskCatalog.Definition.hard()` 为 true 时 +3。没有兑换、扣分或到期逻辑。旧 points/balance_cents 不清零，不改 UID/UUID/昵称。

网站复用 player_profiles.points，新增 task_point_ledger；UID+实际任务日+taskId 是永久唯一键。事务内插入流水并增加余额，重复同难度返回成功但不再加分，冲突返回 400。官网首次登录创建账户资料后才可入账；此前的 409 会保留游戏待发送事件。

服务端设置 `MUXI_TASK_POINTS_ENABLED=1`、`MUXI_TASK_POINTS_URL=https://网站/api/internal/game/task-claims`、`MUXI_TASK_POINTS_KEY`；网站设置 `BMC_GAME_PLATFORM_ENABLED=1`、`BMC_GAME_SERVICE_KEY`。密钥至少 32 字符，仅服务端保管，浏览器和任务请求不携带密钥或分值。默认关闭，不补发启用前领取的任务。

core 将可信服务端 UID、state.day、任务 ID、hard 写入 PlayerPersisted.muxi_task_points_pending，和领取位/背包同一次玩家保存。每 30 秒尝试每位在线玩家队首，不阻塞 tick；失败、未知提交结果、丢失响应继续重试。确认成功后保存出队；重启/退出后队列保留，离线玩家下次登录继续，跨天刷新不删除队列，死亡 Clone 显式复制队列。队列上限 10000，启用时不可靠身份或队列不可解析会阻止该次领奖。只接受稳定 UID 登录名与对应离线 UUID。Minecraft 原生 savePlayer 的静默磁盘失败、外部 NBT 回滚/损坏不提供跨系统全局事务保证；这与现有物品领奖保存边界一致，必须真实联调验证。

平台管理员与游戏 OP 等级是两个独立字段。permissions-proposal.json 提议 UID10000 平台管理员=true、gameOpLevel=4，**不会自动导入**。PlatformStore.initialize_permissions 仅作为人工审核后的离线初始化方法；没有对玩家暴露授权接口。既有账户中心 admin 保留管理边界。游戏 OP 记录不修改 ops.json、不证明正式服权限生效。

平台管理员可在玩家页设置/解除 UID 入服限制，服务端鉴权、同源 JSON 写入、事务审计。game_bans 独立于账户表，官网登录、昵称、积分读取不受影响。权限初始化不运行真实记录；全部测试使用临时数据库。

账户中心启用 `MUXI_GAME_ADMISSION_ENABLED=1`、`MUXI_GAME_PLATFORM_URL=https://网站`、`MUXI_GAME_PLATFORM_KEY` 后，在现有 `/api/internal/minecraft/join/{uid}` 消耗入服票据前查询网站；禁入返回 403，服务故障返回 503（仅入服 fail closed）。禁令只影响此链路的后续入服，不踢在线玩家；正式服必须确实接入现有入服许可机制才生效。没有任意命令、重启、热重载、支付功能。

验证：9 项 SQLite 隔离测试，2 项 FastAPI TestClient 集成测试、5 项入服检查隔离测试，Java 全量离线编译及既有 10025 项自测通过。使用一次性世界、合成 ServerPlayer 和真实 NeoForge 专用服务进程补验了原生 NBT 领奖保存、死亡 Clone 队列复制、PlayerDataStorage 从磁盘恢复、两次进程重启、HTTP 已提交但响应丢失、HTTP503 与幂等重试。真实回环 FastAPI 网站/账户中心 HTTP 路由验证禁入、解除、票据未被禁入消耗，以及封禁期间两侧官网登录正常。游戏监听由测试 mixin 禁用，Mojang API 指向回环；未接入真实服务器、账户或凭据。

浏览器使用独立临时资料和模拟 fetch 验证：管理员显示游戏入服管理，普通玩家隐藏；积分=4。桌面截图在 task/points-work/ui-admin.png。测试运行必须使用临时数据库，网站 API 测试显式跳过 .env；不导入真实认证配置。


## 隔离原生联调收尾（2026-10-01）

四个独立 NeoForge 测试进程共 35 项原生断言通过；网站积分依次为 1→4→4→4。代理记录严格为首次普通任务入账、503未提交、普通任务重放不入账、困难任务首次入账。所有临时进程已退出。没有下载依赖或修改系统权限。

补强三处边界：账本按 UTC 验证日期并容许任务时区领先一天；积分 URL 缺少主机时安全关闭集成而不让任务特性启动失败；损坏 NBT 待同步队列在领取前拒绝且不发物品/经验。时区回归测试、原生损坏队列测试、第四轮错误 URL 配置启动测试通过。更新后的 core 重新编译并通过既有 10025 项自测。

证据：验收工作目录中的 integration-20261001-033628\integration-result.json
复现脚本与测试模组源码：验收工作目录中的 run_points_integration.py、PointsSmoke.java、integration_service.py；只在该测试工作目录运行，读取已安装库，重新建立隔离数据/世界，不用于部署。

仍未验证：真实客户端登录握手、正式服配置及网络、所有整合包模组共存、硬断电或磁盘损坏的恢复、跨天刷新期间的实际进程场景。原生测试玩家没有真实游戏 socket；不能据此宣称正式服同步或 UID10000 游戏 OP4 生效。无需额外授权即可完成本次隔离补验，生产启用仍不在本次范围。
