# Champions 强敌额外奖励减半与词缀核查

核查版本：Champions Unofficial 21.1.1.7，Minecraft 1.21.1，LootJS 3.7.0。

## 词缀结论（本次不修改战斗词缀）

本机 16 个内置词缀没有独立的“100% 破甲”开关。直接核对安装 JAR：

- `ChampionEventsHandler.onWoundedDamage`：创伤令已计算的受伤乘 1.5。
- `ChampionEventsHandler.onWoundedHeal`：创伤令治疗乘 0.5；`WoundEffect` 本身没有护甲属性修正。
- `MoltenAffix`：附加火焰伤害和持续光环调用 `damageSources().onFire()`；此原版伤害类型在 Minecraft 的 `bypasses_armor` 标签中。但代码没有把整次普通攻击改为绕甲伤害。
- `affixes.molten.auraDamage` 当前为 2.0；将它调成 1.0 是“附加火焰伤害减半”，不是“忽略 50% 护甲”。本次不混淆两者，也不因旧版说明就删除整个词缀。

旧版原作者说明中 Molten 的确提到 armor penetration；当前非官方重写版本必须以安装 JAR 为准。

## 额外掉落修改范围

只改 Champions 自己的额外物品奖励。普通怪物本身的掉落、精英的原生掉落、其他模组掉落、经验、生成概率、阶级、词缀强度、枪械/傀儡此前改动均不改变。

1. `config/champions-server.toml`：原 7 条真实物品奖励不变，在每个最低阶级加入等权重 `minecraft:air` 空奖励。权重按阶级增量为 10、8、9、4、5，对应累积为 10、18、27、31、36。每次原奖励抽取有 50% 留空，奖励种类、物品数量、附魔规则与阶级成长保留。
2. `kubejs/server_scripts/muxi_champions_bonus_loot.js`：用 LootJS 仅对已加载的 `champions:champion_loot` 的各池追加 50% 条件，不遍历或更改其他战利品表。表不存在时只记录日志，不创建奖励表。

这是长期平均额外物品产出减半，不保证每只精英恰好掉原来的一半。

## 为什么空奖励有效

安装 JAR 的 `ChampionLootHandler.parse` 接受注册表中的 `minecraft:air`，不排除它；`buildEligibleList` 使用最低阶级（条目阶级 <= 精英阶级）；`weightedPick` 按全体权重抽取；`spawnDrop` 直接跳过 `ItemStack.isEmpty()`。因此空奖励不会生成空气实体，也不会删除怪物的其他掉落。

## 兼容性注意

安装包把默认表放在旧路径 `data/champions/loot_tables/champion_loot.json`，而本机 Minecraft 默认表使用单数 `loot_table`。未新增替代表，以免意外激活原先不存在的奖励。表实际是否注册以启动时脚本日志为准；本次未启动或重载正式服务器验证。

## 应用、验证与回滚

默认只检查：`python apply_champions_balance.py`

实际应用：`python apply_champions_balance.py --apply`

脚本同时更新本机 `bmc5server` 与 `pack/source/Better MC Remake [FORGE]`。写入前备份、检查并发变化、只改变 TOML 的 `loot.lootDrops`，重复执行不会再减半。不改 JAR、不重启服务器、不发布 OSS、不碰 packspec 或 muxi-game-core 在制改动。

离线检查包括 JavaScript 语法、模拟表作用范围/保留条件/缺失表行为以及各阶级每条奖励概率的有理数精确比较。这不代替游戏内实测。

服务器关闭了配置文件自动监视，统一在下次正常重启后生效。回滚时恢复归档 `before` 中两份原 TOML，删除服务器和发布源中的新增 `muxi_champions_bonus_loot.js`，然后正常重启。
