// Offline checks of script behavior; not a Minecraft integration test.
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const assert = require('node:assert/strict')
const filename = path.join(__dirname, 'muxi_champions_bonus_loot.js')
let callback
let checks = 0
vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
  LootJS: { lootTables(fn) { assert.equal(callback, undefined); callback = fn } },
  Java: { loadClass(name) {
    assert.equal(name, 'net.minecraft.world.level.storage.loot.providers.number.ConstantValue')
    return { exactly(value) { return { type: 'constant', value } } }
  } },
  console: { info() {} }
}, { filename })
assert.equal(typeof callback, 'function'); checks++

function pool(tier) {
  return {
    conditions: ['existing_tier_' + tier],
    entries: [{ item: 'test_item_' + tier, count: tier, weight: 10 }],
    rolls: 1,
    when(fn) { fn({ randomChance: value => this.conditions.push(value) }) }
  }
}

// Three fresh registry loads model startup followed by two data reloads.
for (let reload = 0; reload < 3; reload++) {
  const pools = [1, 2, 3, 4, 5].map(pool)
  const ordinary = { id: 'minecraft:entities/skeleton', pools: [pool(1)] }
  const thirdParty = { id: 'cataclysm:entities/ignis', pools: [pool(5)] }
  const untouched = JSON.stringify([ordinary, thirdParty])
  let requested = 0
  callback({
    hasLootTable(id) { assert.equal(id, 'champions:champion_loot'); return true },
    getLootTable(id) {
      assert.equal(id, 'champions:champion_loot'); requested++
      return { getPools() { return pools } }
    }
  })
  assert.equal(requested, 1); checks++
  for (let i = 0; i < pools.length; i++) {
    const p = pools[i]
    assert.equal(p.conditions.length, 2)
    assert.equal(p.conditions[0], 'existing_tier_' + (i + 1))
    assert.equal(p.conditions[1].value, 0.5)
    assert.equal(p.entries[0].count, i + 1)
    assert.equal(p.entries[0].weight, 10)
    assert.equal(p.rolls, 1)
    checks += 6
  }
  assert.equal(JSON.stringify([ordinary, thirdParty]), untouched); checks++
}

// Missing table stays missing, with no exception and no fabricated rewards.
assert.doesNotThrow(() => callback({
  hasLootTable(id) { assert.equal(id, 'champions:champion_loot'); return false },
  getLootTable() { assert.fail('Missing table must not be accessed or created') }
})); checks++
console.log('PASS: ' + checks + ' script assertions; exact bonus-table scope, five pools, fresh reloads, preserved entries/conditions, unrelated tables and missing-table behavior.')
