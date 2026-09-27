# 哥布林交易商生命周期与交互修复

- Better Client `enableTradingHud=false`：不再每 tick 获取准星下的商人并主动发送交互包；玩家手动右键交易不受影响。
- Goblin Traders `preventDespawnIfNamed=false`：Villager Names 自动生成的名字不再令自然商人永久保留。
- `muxi-game-core` 的运行时兼容 Mixin：仅对带非负 `DespawnDelay` 的自然商人生效；倒计时为零、无人交易且未拴绳时主动移除。刷怪蛋或命令生成的 `DespawnDelay=-1` 实体保留。

运行 `python apply_goblin_trader_config.py --apply` 应用整合包源配置，省略 `--apply` 只校验。
