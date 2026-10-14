# 官网 YSM 用户模型管理候选

本次实现位于真实 `better-mc-remake` dev：官网玩家中心链接 `/ysm-models.html`，API 和存储在 `server/app/ysm_assets*.py`，同步暂存合同在 `server/app/ysm_asset_bridge.py`。生产 YSM 2.6.5 没有通过本轮私有目录、下载、选用与撤权回退验证，故所有新上传保持隔离；本候选没有部署、没有执行生产 reload。

## 身份与目录

所有用户请求复用 `current_player_account` 的已验证稳定 MuxiUID。显式原账户 Bearer 优先于旧网站 cookie，过期 Bearer 不回退；没有新的用户 token。`X-Muxi-Expected-UID` 只是旧页面防账户切换的乐观校验，不能指定实际上传者。游戏桥以后必须从当前存活 connection/listener 的 `TrustedAccounts.socialUid` 取身份，不能相信客户端 UID、登录名或离线 UUID。

Web 默认目录是数据库数据卷下独立 `ysm-user-assets/`，可用 `BMC_YSM_ASSET_ROOT` 明确配置。内部包含专用 SQLite、incoming、quarantine、verified；不复用音乐存储或凭据，不指向 YSM 公共/custom/auth 扫描目录。生产目标目录、两机传输主体及其最小访问权限尚待 owner 确认，未创建新凭据或云授权。

## 用户操作

2026-10-14 用户选择私有模型 A：仅本人可选用，其他玩家可看到本人的穿戴。显示所需资源会传给观察者客户端，不能保证不可提取、不可复制或文件保密。`visibility=private` 限制公共选用目录、本页直接下载和选用，不表示穿戴对其他玩家隐藏。公开则主动分享目录、下载和选用资格。

现有用户 API 和 `player-read` 机器 API 保持按引用验权：他人的私有模型不会因旁观穿戴而进入自己的可选目录，也不能用 owner 的 `m_` 引用、私有 `p_` ID 或 hash 直接导出文件。渲染所需资源应由真实游戏运行时按当前穿戴和观察者连接传输；它不是 `canSelect`、`canDownload` 或用户 HTTP 的额外授权。具体运行时合同见 [接线合同](ysm-user-assets-wire.md#私有模型-a-用户已选择)。本轮只更新页面与合同，未实现或启用原生观察者资源传输。

- 多文件队列逐文件上传、进度、取消、错误与幂等重试；单文件 64 MiB，账户逻辑配额 256 MiB / 100 项，保留待恢复引用也占配额。
- 默认私有，仅本人选用，穿戴效果对其他玩家可见；上传时或上传后主动选择公开。未验证文件即使请求公开，也不进入公共目录、不允许下载或选用。
- SHA256 共享内容与每用户逻辑资产分开存储。公开目录同内容只有一个 opaque 条目；公开 hash 查重成功时只创建本人引用，不再上传文件。私有、未知和未验证内容的 hash 查询返回相同不可见结果。
- 列表、下载、选用及修改均由服务端检查当前身份和当前引用权限。文件名只用于逻辑显示，不拼成磁盘路径；原始请求流有大小、并发与超时限制，不在 Web 进程解析 opaque/native 容器。
- 删除先撤这条引用授予的目录、下载与选用资格，并写事务 outbox；恢复默认私有，恢复后仍按 A 的穿戴可见规则处理。最后引用释放后保留 7 天，物理清理必须显式授权，未提供自动永久删除任务。恢复不能重新授权已经物理清理的内容。
- 公开改私有或删除会产生同步失效与失去权限的穿戴回退任务；同内容其他有效引用的权限保留。公开改私有不隐藏 owner 的合法穿戴。当前仅证明 Web 权限撤销和暂存任务生成，没有证明游戏客户端实际回退，也无法召回以前已下载或为显示而接收的资源。

## API

| 方法 / 路径 | 含义 |
| --- | --- |
| GET /api/v1/player/ysm/models | 本人资产与逻辑配额，`include_deleted=true` 包含可恢复引用 |
| POST /api/v1/player/ysm/models?filename=...&visibility=private | 原始 `.ysm` 请求流，`application/octet-stream`，`Idempotency-Key` |
| PATCH /api/v1/player/ysm/models/{id}/visibility | 切换公开/私有，JSON visibility 与幂等键 |
| DELETE /api/v1/player/ysm/models/{id} | 可恢复删除，与幂等键 |
| POST /api/v1/player/ysm/models/{id}/restore | 恢复本人引用，默认私有，与幂等键 |
| GET /api/v1/player/ysm/models/{id}/download | 当前权限且格式验证通过后下载 |
| POST /api/v1/player/ysm/models/{id}/select | 只有真实运行时激活回执 ready 才可选用，与幂等键 |
| GET /api/v1/ysm/models/public | 已登录玩家可见的去重公开目录 |
| GET /api/v1/ysm/models/public/by-sha256/{digest} | 仅查询当前公开且验证通过的内容 |
| POST /api/v1/player/ysm/models/references | 从当前公开条目创建本人逻辑引用，与幂等键 |

页面由既有 OIDC/Bearer 页面入口保护，Nginx internal location 不允许静态绕过；容器和外层 HTTP/HTTPS 上传专用代理都允许 64 MiB 流，API/page 返回 no-store。所有浏览器写请求保留同源/CSRF 边界。

本候选面向普通官网浏览器，这是本任务主入口。额外 MCEF 新页面文件选择器不属于本任务必备激活条件。现有 Terminal ACCOUNT 同窗策略静态上允许新页面/API 的原 Bearer 注入，但 `TerminalPassportNavigation.accountDocument()` 只认 `/account.html`，`TerminalViewClient.onFileDialog()` 会取消独立新页面的文件选择，原生下载保存也被禁止。未改 Terminal owner 文件，不能据官网测试声称 MCEF 上传/下载可用；若后续扩展该入口，再由 Terminal owner 做页面适配及真实 MCEF 验收。普通 WEB 书签也没有账户 Bearer，不作为绕过途径。

## 验证与激活边界

只识别后缀不是格式验证。`promote_verified` 默认关闭，必须由已登记的隔离验证器对 exact SHA256/size/profile 签收后在受信任本地 adapter 中调用，用户 HTTP 不能提升文件。实际 2.6.5 容器验证器、CPU/内存/时间限制和生产部署权限未交付；本轮测试只使用清楚标注的 synthetic opaque bytes 与测试签收。

Web `mark_synced` 要求当前 exact revision/generation；暂存桥永远 `active=false`，落盘不是导入或授权成功。只有 YSM owner 完成选用目录隐藏、直接下载/选用 ACL、A 所需观察者渲染分发、连接断开撤销、授权缓存失效与实际穿戴回退矩阵后，才能启用真实激活适配器。资源曾被客户端接收不授予选用资格；清授权缓存也不构成文件不可提取的保证。

Web 与真实 Core 的版本化机器协议、当前连接核验、撤权分页及错误处理见 [接线合同](ysm-user-assets-wire.md)。默认启动 hook 未配置即 503，stage 确认固定 `active=false`，用户 Bearer/cookie 不能替代机器鉴权，没有新用户 token。

安全热加载尚未证明，具体事实及 Asia/Shanghai 可配置凌晨方案见 [桥接合同](ysm-user-assets-bridge.md)。凌晨也不能绕过私有权限条件。当前 CLI 是 dry-run / 树内隔离 stage 候选，没有装任务、没有 reload、没有把文件送入原公共模型目录。

## 协调与候选

官网/account 主线负责 Web 本批文件；YSM 实际维护 owner 为 `01a11687-8264-7029-9fe0-3501270f9006`，其 compat 和 upstream 减负文件均保持原状。Core 的传送 owner dirty 和 Web 通用 MC 调试 owner dirty 未包含本批提交。父仓 gitlink 仍由杂项协调。

正式落地前需要 YSM owner 提供生产版本用户授权目录、容器校验入口、热加载/统一 reload 的线程与同步权限事实，以及两个真实 UID 客户端的授权和撤权验收回执。然后再决定是否需要新的专用机器凭据/云权限，并按明确授权申请；当前不借用音乐或社交 credential。
