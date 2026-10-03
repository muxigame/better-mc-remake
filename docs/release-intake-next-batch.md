# 下一批完整更新发布接收清单

当前工作根目录：`C:\Users\ranzh\workspace\dev\muxigame`。本文件属于 `better-mc-remake` 仓库，旧 task 目录中的原始日志与 QA 证据继续留在原地，作为历史证据读取；不要据其旧绝对路径寻找当前源码副本。

**当前结论：未达到整批发布门槛。** 新共享结算需求仍在开发中；不向 008 同步、不修改生产、不停服、不发布新的客户端或整合包。正式 pack 1.4.28 保持原状。所有 owner 仍对各自功能和产物负责；发布方只接收、核对和执行获批发布步骤，不代替开发修复或补测。

## 接收时源码基线

以下为本次只读读取的子仓 dev HEAD。父仓 956632963f0f51184c54f77ed2c9c7ec4ec482f8 当前 gitlink 指向 better-mc-remake d6ef182 与 Core e0dcc3d；better-mc-remake 子仓分支已前进到 9b8d564accd750b4c5b1e94a7556f6a20444da85。父仓指针由杂项协调者统一归口。发布前须重新读取各仓远端和 dirty 状态，不能把此快照当作未来同步目标。

| 仓库 | 当前 HEAD | 接收备注 |
|---|---|---|
| better-mc-remake | 9b8d564accd750b4c5b1e94a7556f6a20444da85 | 最新提交更新本机调试工具文档/实现；client/tauri/package.json、package-lock.json、pack/packspec.json 仍有其他 owner 的 dirty 改动，不触碰、不混入。 |
| `muxi-game-core` | `e0dcc3d622a9f49e88385f17dcd9ef1cf79d4b1a` | 已含冒险维度环境保护；共享 shader/logging 等 dirty 内容仍由各自 owner 归口，不混入。 |
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
| 冒险维度环境保护 | Core `e0dcc3d622a9f49e88385f17dcd9ef1cf79d4b1a`；模块级 QA 记录 6,995 项既有 Java 自检与 120 项专项断言通过，server/client 正常 exit 0；QA-only 产物 SHA-256 `250254b35b56883b50b217cd99182b2539fb6e032af7f41bf205058543a7383b`。 | 仅对 `muxi_game_core:adventure` 屏蔽爆炸方块破坏、环境点火和原版火蔓延，保留爆炸伤害/击退/声音、火焰视觉与其他维度规则。该 QA jar 来自 `a51e523` 基线叠加本模块，不能当最终 Core 发布 jar；需由 owner 提供与 e0dcc3d 匹配的最终 Core 版本/哈希，并列入后续 Core 替换和正常重启。 |
| 全局 seed 键位 / Game Core | 当前 Game Core 源码包含输入相关提交；迁移后仍有 owner dirty 内容。 | 对应 owner 完成归口、提交和自测；按最终共同源码重建并验证全套产物。 |

现存全批验收 composite 仍记录 `overallAllFeatureAcceptanceClaim=false`；虽已有冒险维度保护专项回执，新的共享结算仍在开发中，尚无整批通过汇总。任何未来生产操作前均须重核全部验收、008 当前状态和主机信任，不得据旧记录假定门槛已满足。

## 只读补充：伙伴补丁与当前打包选择

父仓 `docs/migration-business-review-20261003.md` 指向 `better-mc-remake/pack/patches/champions-companions` 的版本不一致：构建脚本和 `neoforge.mods.toml` 为 1.0.5，`run_smoke.py` / `deploy.py` 固定读取 1.0.4，README 顶部描述仍为 1.0.3。本次只读核对未改这些文件。

统一根当前没有该补丁的 1.0.4/1.0.5 构建 JAR；配置的完整 pack root (`pack/source/Better MC Remake [FORGE]`) 和 `pack/staging/manifest.json` 也不存在，`pack/source` 当前只有 `flight-development`。`bmc5server/mods` 目录能看到本地 1.0.3 JAR；这只是当前工作区文件，不代表 008 生产状态。结论：本批不选择/不添加 Champion Companions 新版，也不覆盖或重复发布 1.0.3；若 owner 要纳入，先统一构建、smoke、deploy 和 README 版本，使用同一 JAR 完成验收并提供哈希，再由 pack owner 放入 canonical pack root 与新 manifest。

Core `e0dcc3d` 的冒险维度保护专项验收是有界的模块 QA，不是最终 pack/Core 发布产物。当前 `build/libs` 中的 `muxi-game-core-1.12.3-task14-qa.1.jar` 不以该功能专项 SHA 为证据；正式打包需要 owner 给出基于已接受提交的最终 Core JAR 和 SHA，并确认它进入客户端/服务端使用的匹配整包。当前无 staging manifest，所以不能凭本机现有 JAR 名称推断发布内容。
## 同步与发布门槛

- [ ] 每个功能 owner 确认最终 commit、分支、测试回执、产物版本与 SHA-256；测试范围覆盖当前源码 HEAD。
- [ ] 完成需要的双真实客户端及完整整合包验收；物理键盘、视觉、SSO 与原生回调分别记录，不能互相替代。
- [ ] 共享 framework、Game Core、资源和各玩法 JAR 组成匹配依赖矩阵；QA-only 文件已排除，dirty 文件归属明确且未夹带。
- [ ] 全量源码提交已从 owner dev 仓可达；同步前读取 008 当前状态并保全冲突。父仓 gitlink 由迁移协调 owner 统一更新。
- [ ] 整批验收汇总通过，发布清单中的产物哈希与最终来源一致；只有此时才能进入 008 同步和获批发布阶段。

## 获批后的发布顺序

当前源仓路径为 `C:\Users\ranzh\workspace\dev\muxigame\better-mc-remake`。客户端与整合包的 OSS 发布只使用项目自带脚本 `client/publish.ps1`、`pack/publish.ps1`；服务器只用 `bmc5server/start.bat` 启动。最终接受的客户端/整包产物和哈希须在停机窗口前准备、复核完成，停机窗口内不构建、不做全量源码传输，也不写临时发布或启动脚本。

全批门槛通过并获批后：先发布客户端和游戏整合包并核验公网版本/哈希，再发维护公告并至少等待 120 秒；随后按项目流程正常保存、停服、备份和应用已核准内容，尽快用 `bmc5server/start.bat` 启服并做健康检查。任一版本、哈希、脚本退出码或健康检查不符，停止后续步骤并按现有回滚流程处理。当前批次尚未过门槛，因此这些步骤均未执行。
