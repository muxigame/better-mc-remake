# 用户 YSM 资产同步候选（2026-10-13）

本候选只做经过信任验证的资产快照检查与独立隔离落盘，未实现或启用游戏模型导入。不会 SSH、执行 YSM、解析模型、生成凭据、安装任务、reload 或部署。

## 真实项目和当前边界

唯一项目根为 `C:/Users/ranzh/workspace/dev/muxigame`。Web 是 `better-mc-remake`，审查起点 dev `9a2032a7671a300e6400ceb291161f1c7708f7d2`。Core dev `2f7dceb54822d42e9dff7f3224250985d92b84d0`、compat dev `576e1c920083cdd127d15bb242109ca0a9d860c4` 有其他 owner 在制修改，本候选不修改它们。

当前生产 YSM 目标为 2.6.5 NeoForge / MC 1.21.1，已审计 JAR SHA256 `b285c73d4ec010d9a9be3c53c1bee890cf269645be5f1bcf1c27a2e8e82807cb`。已登记上游 `ysm-upstream-java` 是 3.0-dev / Forge 1.20.1，不能作为当前生产 API。

本机已有 `bmc5server/config/yes_steve_model` 根；原公共 custom 输入和 builtin 数据保持原样。生产用户授权目录的正式根、扫描入口和对接权限仍须 YSM owner 确认。本候选绝不把用户上传放入原 custom/public/auth 扫描目录，也不依据目录名自动推断它具有私有下载权限。审查没有读取私有模型正文或运行 native。

## UID 与下载、选择权限

`muxi-minigames/src/main/java/net/muxigame/minigames/TrustedAccounts.java` 的 `socialUid(ServerPlayer)` 只在当前在线 player 实例、当前可接收消息的 listener 及保存的同一 connection 下返回已核验 UID。Core `TerminalPassportServer` 在 Auth 响应验证和 server-thread live 检查后调用 `admitTerminal`。用户名、昵称和离线 UUID 只是公开标识/一致性检查，不能由客户端 body.uid、HTTP header 或任意 UUID 直接授予所有权。后续桥必须在服务端当前 listener 上解析稳定 MuxiUID，再按用户资产 API 的独立用途验权；不把 social 信任冒充游戏结算准入。

既有 2.6.5 类审计在 compat 的忽略目录 `build/production-upload-contract-20261013`：授权集合类 `OOo0o0000Ooo0o00OO0oOOoO` 持有 model ID Set 并序列化到玩家 attachment；auth add/remove 命令属于 `oOoOOo00OOoOOoO0ooo0O0oO`；服务器管理器 `O0Ooo000O0ooO00Oooo00oOO` 及模型命令 `ooo00o00000oO0OO00oO000o` 检查该集合。它们证明存在选用授权，不证明传输字节对未授权者保密。

上游 3.0 的 [model-authorization 契约](../../ysm-upstream-java/docs/product-decisions/decisions/model-authorization.md) 明确默认“无选择权仍可取得资产以呈现其他玩家”；RESTRICTED_AUTH 也保留展示信息。因此不能只调用原 auth grant/remove 来实现严格私有列表与下载。该事实限定于上游契约；当前 2.6.5 opaque native 传输的严格私有门禁仍未验证。

私有 runtime 放行前至少验证：

- 两个真实独立 UID/listener：B 无法列出 A 私有资产、查看缩略图/metadata、按 ID/hash 下载或直接请求 YSM 网络资源。
- 服务端每次 download/use 按当前 revision 检查；已有缓存、旧请求 key、旧 listener 均不能授予新权限。
- public→private/delete 先原子撤销新请求资格、清失效选择，再同步当前连接的默认模型回退；需实际客户端与远端玩家画面/网络回执。
- 撤权后客户端清自身授权投影与选择缓存；不能承诺召回先前合法下载的文件或秘密副本。

## 内部存储合同

仅受信任的 `YsmAssetStore` 导出 `bridge_snapshot()`：

`{revision, models:[{contentId:p_id, sha256, size, storageKey:"verified/<sha>.bin", public, ownerUids, modelIds, validationStatus:"verified", validationProfile, validationReceiptId}], selections:[{uid,modelId,contentId}]}`

未验证上传不导出。每项还包含 references:[{modelId,ownerUid,visibility}]，准确保留每个逻辑资产归属，禁止仅凭 ownerUids/modelIds 两个聚合集合交叉授予权限；aggregate 与 references 不符或 desired selection 已失效即拒绝。SHA256 仅机器内部；前端及跨 UID 查找使用 opaque ID，不能暴露私有 hash 是否存在。blob 根与隔离目标根独立、由部署注册；不可接受用户指定绝对路径。

`events_after(seq)` 按递增 seq 返回事务事件，包括 asset.created/visibility/delete/restore、content.verified/rejected/purged、selection.changed。快照与事件必须仍匹配同一 current revision；任何 copy 前后发生的变化均拒绝本轮。相同 SHA256 只复制一份内容，引用权限保留在清单，不混入音乐桶或凭据。

`AssetBridge.run()` 默认 dry-run。显式 stage 时复核每项 validator 签收、大小和 SHA256，再创建自己独立 generation、fsync 内容及原子 `permissions.json`/`staged.json`。权限变更恰好晚于指针发布时写无资产的 invalidated 清单。所有清单固定 active=false/selectEnabled=false。只完成 staging，事件 cursor 也不能被 runtime 当作撤权执行完毕回执。

`acknowledge_activation` 对过期 revision 报 StaleRevision；即使 revision 正确也拒绝 activation。候选不调用 `mark_synced(active=True)`，不因文件落盘开放 select。未来适配器只有实际导入、每 UID 列表/下载/选用 ACL 和穿戴回退回执齐全后，才能调用 store 的精确 revision/generation ACK；旧 ACK 必须拒绝。

## 凌晨更新策略

`next_run(aware_now, "03:00", "Asia/Shanghai")` 计算严格未来的每日时间，时间可配置。Asia/Shanghai 当前无 DST；缺 IANA 数据时只允许 1992 年后的 UTC+8。其他时区必须有 IANA 数据：不存在的 DST 墙钟时间跳过该日，重复时间只用 fold=0 每日一次。没有创建真实任务。

候选 CLI：

```powershell
python -B scripts/sync_ysm_user_assets.py --snapshot <trusted-internal.json> --events <outbox.json> --source-root <backend-root> --target <Web/build/owner-specific/quarantine> --at 03:00
```

默认只检查；`--stage` 仅允许目标位于本项目忽略的 build 内，不能用于生产目录。JSON 文件仅可来自受信任的内部导出，不是用户上传文件或远程客户端输入。

正式任务方案待目录/权限验收后才实施：Linux systemd calendar 指定 Asia/Shanghai，或 Windows Task Scheduler 按主机时区计算对应时间；进程执行前重新获取 current revision，采用单实例锁、失败回执和有限重试，错过任务不无条件追赶运行 reload。每夜先落盘独立授权根，再由经过证明的版本适配器统一导入。未证明私有门禁时，即使到凌晨也保持隔离，不自动 reload。无密钥和任务安装动作包含在本候选内。

## 热加载未通过的具体原因

2.6.5 `ysm model reload` 经上述管理器的 native callback machinery 运行，是全局操作；已有 owner 实测主要覆盖未变输入。compat [实际同步/目录移除证据](../../muxi-ysm-compat/docs/demand-proof-runtime-review-20261013.md) 记录原 resync/global reload 对已移除 Java catalog 条目未恢复、实际模型缺失。该实验不是新文件导入实验，不能直接证明新上传不能导入，也不能作为安全热加载成功证据。当前未获得 typed per-user 导入、完整代际切换与撤权回退证明。因此不调用它、不过滤 opaque 包、不用 export、析构或清全局 map 代替导入。

## 验证

测试只使用自造 opaque bytes 与测试 validator 签收，不涉及真实模型解析。临时数据在 `build/owner-131-ysm-bridge-tests`。覆盖 dry-run、私有权限清单、准确文件哈希、未验/超大/篡改/穿越拒绝、实际 Windows junction、并发 revision 撤权、旧 ACK、北京时间及 DST gap/fold。符号链接创建是否支持取决于本机权限，跳过必须单列，不当作通过。

```powershell
$env:PYTHONPATH="$PWD/server"
python -B -m unittest tests.test_ysm_asset_bridge -v
```
