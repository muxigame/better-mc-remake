# Web / Core / YSM 接线合同 v1

网站外部浏览器是主入口；MCEF 文件选择器不属于本任务的必备激活条件。本批分工：官网/account owner 负责 `better-mc-remake/server/app/ysm_*`、官网页面及此协议；Core/Terminal owner `01a0fcd7-2c80-7299-ae3d-6621c89fa00b` 负责真实 `muxi-game-core/feature/ysmassets` 的当前连接身份、权限镜像、同步和撤权回退；YSM owner `01a11687-8264-7029-9fe0-3501270f9006` 负责实际格式隔离验证、用户授权目录、私有资源传输门禁及导入/回退 API。各方不改他人拥有的文件；同协议变更先更新并共同核对。

## 默认门禁

`ysm_game_router(store_or_provider, authorize_machine, resolve_connection)` 接受两个独立的受信任启动期 hook，主应用的 `app.state.ysm_machine_authorizer`、`app.state.ysm_connection_resolver` 未配置时统一返回 503。没有 HTTP 配置入口，没有以用户 Bearer/cookie 替代机器鉴权；没有生成新密钥、复用音乐/social 凭据或自动开通新 scope。实际部署鉴权、允许的 serverId 与资源用途仍需 owner 确认；若需新凭据/云权限，另按明确授权申请。

`authorize_machine(request, purpose)` 返回 `GameMachine(server_id)`。purpose 为 `sync`（受信任服务器内部快照/暂存）或 `player-read`（当前玩家目录/字节）；回调必须核实机器凭据及对应用途。`resolve_connection(machine, claim)` 返回真实 `GameConnection(server_id, connection_id, binding_generation, uid, live=True)`，不能照抄 request claim。绑定来自当前 listener 的稳定 MuxiUID，断开或旧 listener 替换必须失效。

claim 固定 `{serverId, connectionId, bindingGeneration, uid}`，前三项是非秘密 opaque 字符串、uid 为规范十进制字符串。它们只是身份一致性断言，绝不是另一种用户 token。回调实值必须匹配 claim；伪造 uid 返回 403，连接/epoch 改变返回 409。

## 机器协议

所有入口位于 `/api/internal/game/ysm`，JSON body 不超过 16 KiB，未知字段拒绝。任何响应不共享缓存；native/opaque 解析不在 Web HTTP 进程运行。

| POST 路径 | 请求 | 响应 |
| --- | --- | --- |
| `/snapshot` | `{afterSeq:0,limit:100,expectedRevision?:N}`，用途 sync | 同一 SQLite 读事务的 `{schemaVersion:1,revision,models,selections,events,nextSeq,hasMore,active:false,selectEnabled:false}` |
| `/catalog` | `{connection:claim,revision:N}` | 当前 UID 可见的 verified 目录；只保留本人的私有引用和公开引用，重新计算 ownerUids/modelIds，禁止透露他人的私有引用 |
| `/blob` | `{connection:claim,revision:N,modelId:opaqueId}` | 当前权限下 verified 的原始 bytes，`X-Muxi-SHA256` / `Content-Length`；读取前后核 live binding，IO 后再次核 revision |
| `/staged` | `{contentId:p_id,sha256,revision:N,generation:g_32hex,status:'staged'}`，用途 sync | exact content/revision 暂存确认；固定 `active:false`，拒绝 ready/active、未知字段及旧版本 |

snapshot 模型保留 `contentId/sha256/size/storageKey/validationStatus/validationProfile/validationReceiptId/references/ownerUids/modelIds/public`，与真实 Core `YsmAssetSnapshot.parseTrusted` 输入对齐。`storageKey` 只是固定 `verified/<sha>.bin` 逻辑 key，不是服务器绝对路径。远端必须用已核 hash/size 的 wire bytes 写自己的独立授权镜像，不能将源服务器路径拼进本机扫描目录。未验文件从不出现在此快照或字节接口。

outbox afterSeq 是 exclusive cursor，limit 1..500。未来 cursor 拒绝；分页始终携带 expectedRevision，变化就 409，客户端丢弃旧页、取新完整快照并重做授权投影，不能先应用部分页再猜测跳过撤权事件。删除、改私有、恢复的事件与权限同事务提交，快照与事件不会撕裂。

## 错误与重试

- 401/403：凭据、用途或 UID 不被认可；不得换用玩家 token、忽略权限或盲重试。
- 404：模型不在当前可访问集合；不得据此改查私有 SHA256。已缓存字节不授予目录/下载/选用权。
- 409 `stale_permission_revision` / `game_connection_changed`：立即丢弃旧版本和旧连接请求，重新取得真实身份及完整快照；曾经许可不自动延续。
- 409 文件完整性失败：保持隔离，重取 exact bytes 并重新验证，不标记 ready。
- 503 authority 未配置：保持禁用，等待批准的绑定；不是玩家重新登录就能解决的错误。

暂存请求可以重送同一 revision/generation，但不能将超时解释为已激活；未知成功结果必须核当前状态。真正解析、导入、每 UID 的目录/资源/选用过滤和实际穿戴回退全部证明后，另接运行时激活回执，本协议不提供这个绕过入口。

## 持久化与实测边界

验证提升现在保留 quarantine 原件，fsync/hash/size 验证后原子发布 verified 副本。验证事件或 SQLite commit 失败时，pending 重试仍能读原件；孤立 verified 副本没有验证状态，不能被下载或导出。重复副本仍随最后引用释放后的显式授权清理处理。

删除引用先撤当前权限和选用，再生成 outbox/default fallback 任务；恢复只还原本人私有引用。公开副本仍有他人公开引用时，不误删其合法权限。物理永久清理仍需显式授权，不能借凌晨 reload 执行。

接线验证使用真实 SQLite、FastAPI wire 和实际 Core 纯 Java 合同类，测试签收只用于 synthetic bytes。它不证明生产格式、MC live listener、真实穿戴回退或安全 reload。YSM 格式/私有分发未通过前保持 quarantine；凌晨只是计划，不绕过验证。
