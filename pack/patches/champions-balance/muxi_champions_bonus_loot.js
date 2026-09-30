// Only the Champions bonus table is changed, never entity/block/chest tables.
// CONFIG rewards are independently reduced in config/champions-server.toml.
// Keep entries, quantities, enchantments, tier requirements and old conditions.
LootJS.lootTables(event => {
  const tableId = 'champions:champion_loot'
  if (!event.hasLootTable(tableId)) {
    // Do not create/repair an absent bonus table: that could INCREASE rewards.
    console.info('[muxi-champions-balance] Bonus table absent; no table created. Config reward reduction remains active.')
    return
  }

  const pools = event.getLootTable(tableId).getPools()
  pools.forEach(pool => pool.when(conditions => conditions.randomChance(0.5)))
  console.info('[muxi-champions-balance] Champions bonus-table pools now have an additional 50% chance. Other tables unchanged.')
})
