# 下一批完整更新发布接收清单

当前工作根目录：`C:\Users\ranzh\workspace\dev\muxigame`。本文件属于 `better-mc-remake` 仓库，旧 task 目录中的原始日志与 QA 证据继续留在原地，作为历史证据读取；不要据其旧绝对路径寻找当前源码副本。

**当前结论：未达到整批发布门槛。** 不向 008 同步、不修改生产、不停服、不发布新的客户端或整合包。正式 pack 1.4.28 保持原状。所有 owner 仍对各自功能和产物负责；发布方只接收、核对和执行获批发布步骤，不代替开发修复或补测。

## 当前源码快照

以下是统一根下读取到的当前 dev HEAD；发布前仍须重新读取各仓远端和 dirty 状态，不能把此快照当作未来同步目标。

| 仓库 | 当前 HEAD | 接收备注 |
|---|---|---|
| `better-mc-remake` | `1ca85e6aa59e229a5b2669697c56aa316981f9f4` | `client/tauri/package.json`、`package-lock.json`、`pack/packspec.json` 有其他 owner 的 dirty 改动；不触碰、不混入本次文档提交。 |
| `muxi-game-core` | `1b6467bd3426088a06c8214915fcd0e2f62ae46d` | 迁移记录标注有多 owner dirty 内容；只由对应 owner 归口。 |
| `muxi-minigames` | `aa9b47d532bb84e4ac14f644f78a3fe68eacbc04` | 包含共享装备提交 `235c4c1` 的后续提交；当前仓干净。 |
| `muxi-flight` | `4ef7ab5d6421fbba74957fb292cf3ebd8361adc7` | 当前仓干净；已有 QA 证据需逐项核对其源码基线。 |
| `muxi-horse-racing` | `c5ea2e2f6e3e5fa9c4e53d5039c30f260acea9f0` | 当前仓干净；已有单客户端证据不能代替双客户端整包验收。 |
| `muxi-outbreak` | `81b57bab7e0db1b83caaaee641a340c268597bb3` | 当前仓干净；见下方接受范围。 |
| `muxi-zombie-challenge` | `88829cc1925111445b83c8a108bc6f99e5fa3d94` | `0af4765` 已包含在当前历史中；当前 HEAD 另有统一工作区 QA 路径修正。 |
| `muxi-terminal` | `8954f89d5590ab59472e8f92662a6d87b6981b6a` | 当前仓干净；物理全局 F 键位归 terminal owner 集成。 |

## 功能验收与缺口

| 功能 | 已收到的 owner 证据 | 发布前仍须完成 |
|---|---|---|
| 空战 | 当前仓有 lifecycle、JVM、HTTP、MCEF 和 full-pack QA 记录。 | owner 确认当前 HEAD 与证据基线；补齐本批要求的全包验收和尚未覆盖的 PvE tiers 2–5 样例。不要扩大为未证实的平衡或 AI 改善声明。 |
| 赛马 | task-11 隔离回执覆盖 1 个真实网络客户端，10 分 exactly-once、现金不变、原马 UUID 恰好归还一次；原生生命周期和 JVM 重启检查通过。 | 当前源码匹配的双真实客户端与完整整合包验收；核实原始用户复现路径，并给出与最终提交和产物一致的哈希。 |
| Outbreak | `muxi-outbreak/docs/equipment-acceptance-20261003.json` 记录 2 个真实客户端、54 个网络断言、正常退出及配套资源检查；候选 Outbreak SHA-256 `bafabf983dcc11f3e18218938f4616c9a7764e270018c2dc38e3a33c0a7536e8`。物理全局 F 键位由 terminal owner 负责。 | 重新生成并验证与当前 `muxi-minigames` HEAD `aa9b47d` 匹配的最终候选；补齐整批所需的最终验收记录。旧网络验收使用 framework `235c4c1` 构建的候选，不能直接作为新 HEAD 的产物证明。 |
| Zombie Challenge | owner 报告 `0af4765` 的原生回调 49 项、服务端夹具 53 项通过；当前 HEAD 是其后续提交 `88829cc`。 | 实际物理按键、双真实客户端和完整整合包验收；提供最终产物版本及 SHA-256，并确认测试对应当前源码与当前 framework。 |
| 全局 seed 键位 / Game Core | 当前 Game Core 源码包含输入相关提交；迁移后仍有 owner dirty 内容。 | 对应 owner 完成归口、提交和自测；按最终共同源码重建并验证全套产物。 |

现存全批验收 composite 仍记录 `overallAllFeatureAcceptanceClaim=false`，且没有更新后的全批通过 composite 汇总本轮较新的 owner 证据。该文件也曾记录 008 主机信任门槛未满足；任何未来生产操作前必须重新核验，不得据旧记录假定已经满足。

## 同步与发布门槛

- [ ] 每个功能 owner 确认最终 commit、分支、测试回执、产物版本与 SHA-256；测试范围覆盖当前源码 HEAD。
- [ ] 完成需要的双真实客户端及完整整合包验收；物理键盘、视觉、SSO 与原生回调分别记录，不能互相替代。
- [ ] 共享 framework、Game Core、资源和各玩法 JAR 组成匹配依赖矩阵；QA-only 文件已排除，dirty 文件归属明确且未夹带。
- [ ] 全量源码提交已从 owner dev 仓可达；同步前读取 008 当前状态并保全冲突。父仓 gitlink 由迁移协调 owner 统一更新。
- [ ] 整批验收汇总通过，发布清单中的产物哈希与最终来源一致；只有此时才能进入 008 同步和获批发布阶段。

## 获批后的发布顺序

当前源仓路径为 `C:\Users\ranzh\workspace\dev\muxigame\better-mc-remake`。客户端与整合包的 OSS 发布只使用项目自带脚本 `client/publish.ps1`、`pack/publish.ps1`；服务器只用 `bmc5server/start.bat` 启动。最终接受的客户端/整包产物和哈希须在停机窗口前准备、复核完成，停机窗口内不构建、不做全量源码传输，也不写临时发布或启动脚本。

全批门槛通过并获批后：先发布客户端和游戏整合包并核验公网版本/哈希，再发维护公告并至少等待 120 秒；随后按项目流程正常保存、停服、备份和应用已核准内容，尽快用 `bmc5server/start.bat` 启服并做健康检查。任一版本、哈希、脚本退出码或健康检查不符，停止后续步骤并按现有回滚流程处理。当前批次尚未过门槛，因此这些步骤均未执行。
