# 傀儡与永恒枪械数值调整

## 倍率与范围

- Modular Golems 3.1.43：大型、人形、犬型的最大生命、攻击伤害、护甲、护甲韧性、抗击退、攻击击退、自然回血、横扫属性、动态减伤属性使用 `ADD_MULTIPLIED_TOTAL = -0.5`。
- 该修正放在属性乘算阶段，覆盖材料、升级与装备的属性加成，而不是只减半材料表。Minecraft 的属性上下限及伤害/护甲计算规则仍然有效；不是承诺最终战斗力严格等于一半。
- 不修改移动速度、攻击速度、攻击距离、体型、跳跃、冷却、配方、材料消耗、升级槽、特性等级与免疫开关。不另设最终伤害钩子，以免基于攻击属性的伤害重复减半；不使用属性的固定技能效果也不由本脚本改写。
- 永恒枪械（TaCZ）：`config/tacz-server.toml` 的 `DamageBaseMultiplier` 从 `0.5` 改为 `0.6666666666666666`。穿甲、爆头、重量、弹匣、射速及其他设置不变。

## 部署位置

将本目录的 `muxi_golem_combat_balance.js` 同步到以下两处：

1. `muxigame/bmc5server/kubejs/server_scripts/`
2. `muxigame/better-mc-remake/pack/source/Better MC Remake [FORGE]/kubejs/server_scripts/`

两端的 `config/tacz-server.toml` 保持相同倍率。干净源的 config 与 kubejs 文件已被现有 packspec 纳入发布，但修改源文件不等于已发布至 OSS。

## 生效与回滚

服务端关闭了配置文件自动监视；统一在下次正常重启后生效。KubeJS 的 `EntityEvents.spawned` 在服务端 `EntityJoinLevelEvent` 触发，包含新生成及从存档/区块重新加载的实体，不做每 tick 扫描。

修正使用固定 ID `muxigame:golem_combat_half` 和临时属性修正，不写入实体的基础数值，重复加载不会叠成 1/4。降低生命上限时仅裁剪超出上限的当前生命，避免每次读档再次扣掉一半当前血量。

回滚：停止服务端后删除两处新增脚本，恢复备份的 TaCZ 配置（干净源配置原先不存在则删除新增文件），然后正常启动。临时属性修正不会存入实体 NBT；移除脚本后加载实体即恢复原属性，但不会额外补满当前生命。

## 校验

`node test_balance.cjs`：验证三类目标、九项属性、重复触发、加载后血量、装备变化、基础属性变化、缺失属性和不修改移动/攻速/攻击距离。该测试是离线行为模拟，不代替 Minecraft 内实测。

已用本机安装的 KubeJS 2101.7.2-build.377、Minecraft 1.21.1、NeoForge 21.1.250 和 Modular Golems 3.1.43 的类签名核对所使用的接口。未替换原模组 JAR，不修改 muxi-game-core 的在制任务功能。
