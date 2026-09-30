const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const assert = require('node:assert/strict')
const script = fs.readFileSync(path.join(__dirname, 'muxi_champions_health.js'), 'utf8')
const id = 'champions:minecraft_generic.max_health_modifier'
const operations = {
  ADD_MULTIPLIED_TOTAL: { name: 'total', equals(other) { return other === this } }
}
let checks = 0
function near(actual, expected, message) {
  assert.ok(Math.abs(actual - expected) <= Math.max(1e-5, Math.abs(expected) * 1e-7),
    (message || '') + ': ' + actual + ' != ' + expected)
  checks++
}
function check(condition, message) { assert.ok(condition, message); checks++ }
class Modifier {
  constructor(id, amount, operation) { this.key = id; this.value = amount; this.op = operation }
  amount() { return this.value }
  operation() { return this.op }
}
class Attribute {
  constructor(base) { this.base = base; this.modifiers = new Map(); this.cap = 1e9; this.otherMultiplier = 1 }
  getModifier(id) { return this.modifiers.get(id) || null }
  removeModifier(id) { return this.modifiers.delete(id) }
  addOrReplacePermanentModifier(mod) { this.modifiers.set(mod.key, mod) }
  getValue() {
    let result = this.base * this.otherMultiplier
    for (const mod of this.modifiers.values()) result *= 1 + mod.amount()
    return Math.min(this.cap, result)
  }
}
const creeperType = { equals(other) { return this === other } }
const mobType = { equals(other) { return this === other } }
let serial = 0
class LivingEntity {
  constructor(base = 20, tier = 4) {
    this.attr = new Attribute(base); this.tierLevel = tier; this.serial = ++serial
    this.type = mobType; this.removed = false; this.alive = true; this.client = false
    this.champion = true; this.server = null; this.healthWrites = 0
    this.attr.addOrReplacePermanentModifier(new Modifier(id, 0.35 * tier / 5, operations.ADD_MULTIPLIED_TOTAL))
    this.health = this.getMaxHealth()
  }
  getType() { return this.type }
  isRemoved() { return this.removed }
  isAlive() { return this.alive }
  level() { return { isClientSide: () => this.client } }
  getAttribute(key) { check(key === 'MAX_HEALTH', 'Must not touch any other attribute'); return this.attr }
  getMaxHealth() { return this.attr.getValue() }
  getHealth() { return this.health }
  setHealth(value) { this.healthWrites++; this.health = value }
  getServer() { return this.server }
  getUUID() { return 'uuid-' + this.serial }
  getId() { return this.serial }
}
class Player extends LivingEntity {}
class HashSet {
  constructor() { this.data = new Set() }
  add(key) { if (this.data.has(key)) return false; this.data.add(key); return true }
  remove(key) { return this.data.delete(key) }
}
function load(source = script) {
  const callbacks = {}; const logs = []
  const types = {
    'top.theillusivec4.champions.api.ChampionsApi': {
      get: () => ({ getChampion: entity => ({
        isPresent: () => entity.champion,
        get: () => ({ tier: () => ({ level: () => entity.tierLevel }) })
      }) })
    },
    'net.minecraft.world.entity.LivingEntity': LivingEntity,
    'net.minecraft.world.entity.player.Player': Player,
    'net.minecraft.world.entity.EntityType': { CREEPER: creeperType },
    'net.minecraft.world.entity.ai.attributes.Attributes': { MAX_HEALTH: 'MAX_HEALTH' },
    'net.minecraft.world.entity.ai.attributes.AttributeModifier': Modifier,
    'net.minecraft.world.entity.ai.attributes.AttributeModifier$Operation': operations,
    'net.minecraft.resources.ResourceLocation': { fromNamespaceAndPath: (ns, name) => ns + ':' + name },
    'java.util.HashSet': HashSet,
    'net.muxigame.championcompanions.CompanionRules': {
      effectFactor: e => e.ownedCompanion ? 0.5 : 1,
      entityKey: e => e.getUUID() + ':' + e.getId(),
      removeAttributeModifier: (attribute, id) => attribute.removeModifier(id)
    }
  }
  const ctx = vm.createContext({
    Java: { loadClass(name) { check(name in types, name); return types[name] } },
    ChampionsEvents: { spawn(fn) { callbacks.spawn = fn } },
    EntityEvents: { spawned(fn) { callbacks.join = fn } },
    ServerEvents: { loaded(fn) { callbacks.loaded = fn } },
    console: { info(text) { logs.push(text) }, error(text) { logs.push(text) } }
  })
  vm.runInContext(source, ctx)
  return { api: vm.runInContext('MuxiChampionHealth', ctx), callbacks, logs }
}
const { api, callbacks, logs } = load()
const bonuses = [0, 7.5, 18.75, 37.5, 60, 90]
function extra(h, tier) { return (bonuses[tier] / 60) * (0.625 * h + 80.15625 * h / (h + 13.75)) }
// Independent, explicit acceptance targets: do not only mirror the formula.
for (const [base, expectedByTier] of [
  [20, [27.5, 38.75, 57.5, 80, 110]],
  [10, [15, 22.5, 35, 50, 70]],
  [200, [225, 262.5, 325, 400, 500]]
]) {
  for (let tier = 1; tier <= 5; tier++) {
    const entity = new LivingEntity(base, tier)
    check(api.apply(entity), 'Apply V2 acceptance targets')
    near(entity.getMaxHealth(), expectedByTier[tier - 1], 'Explicit tier target H=' + base + ' tier=' + tier)
  }
}
for (const base of [1, 5, 10, 20, 40, 100, 500, 1000, 10000]) {
  let previousTarget = base
  for (let tier = 1; tier <= 5; tier++) {
    const entity = new LivingEntity(base, tier)
    const target = base + extra(base, tier)
    check(api.apply(entity), 'Must replace the old tier rule')
    near(entity.getMaxHealth(), target, 'Curve')
    near(entity.health, target, 'Fresh spawn stays full')
    near(entity.attr.base, base, 'Base value unchanged')
    check(target > previousTarget, 'Strict tier progression'); previousTarget = target
    check(entity.attr.modifiers.size === 1, 'No stacking with old health bonus')
    const writes = entity.healthWrites
    check(!api.apply(entity), 'Second run is idempotent')
    check(writes === entity.healthWrites, 'Idempotent run does not heal')
    entity.health = entity.getMaxHealth() * 0.25
    api.apply(entity)
    near(entity.health, target * 0.25, 'Damaged reloading preserves HP')
  }
}
// Already-saved V1 curve mobs must migrate once, preserving damage ratios.
const oldBonuses = [0, 10, 25, 50, 80, 120]
for (const base of [20, 100, 500]) {
  for (let tier = 1; tier <= 5; tier++) {
    for (const ratio of [1, 0.5, 0.1]) {
      const entity = new LivingEntity(base, tier)
      const oldAmount = oldBonuses[tier] * Math.sqrt(base / 20) / base
      entity.attr.addOrReplacePermanentModifier(new Modifier(id, oldAmount, operations.ADD_MULTIPLIED_TOTAL))
      entity.health = entity.getMaxHealth() * ratio
      const target = base + extra(base, tier)
      check(api.apply(entity), 'Migrate V1 health curve')
      near(entity.getMaxHealth(), target, 'V1 -> V2 maximum')
      near(entity.health, target * ratio, 'V1 -> V2 damage ratio')
      check(entity.attr.modifiers.size === 1, 'V1 modifier replaced, never stacked')
      const writes = entity.healthWrites
      check(!api.apply(entity), 'V2 migration does not repeat')
      check(entity.healthWrites === writes, 'V2 reload does not heal')
    }
  }
}
for (let tier = 1; tier <= 5; tier++) {
  const entity = new LivingEntity(20, tier)
  entity.health *= 0.4
  api.apply(entity)
  near(entity.health, (20 + bonuses[tier]) * 0.4, 'Old wounded mobs migrate at same percentage')
}
const equipment = new LivingEntity(20)
equipment.attr.otherMultiplier = 2
equipment.health = equipment.getMaxHealth()
api.apply(equipment)
near(equipment.getMaxHealth(), 40 + extra(40, 4), 'Other mod health is included and preserved')
near(equipment.attr.otherMultiplier, 2)
const capped = new LivingEntity(1000, 5)
capped.attr.cap = 1024; capped.health = 512
api.apply(capped)
near(capped.attr.getModifier(id).amount(), extra(1000, 5) / 1000, 'Capped baseline is not incorrectly divided')
near(capped.getMaxHealth(), 1024); near(capped.health, 512)
const changedTier = new LivingEntity()
api.apply(changedTier); changedTier.health = 40; changedTier.tierLevel = 5
api.apply(changedTier)
near(changedTier.getMaxHealth(), 110); near(changedTier.health, 55)
const exclusions = [new Player(), new LivingEntity(), new LivingEntity(), new LivingEntity(), new LivingEntity(), new LivingEntity(), new LivingEntity(20, 6), new LivingEntity(), new LivingEntity()]
exclusions[1].type = creeperType; exclusions[2].champion = false
exclusions[3].removed = true; exclusions[4].alive = false; exclusions[5].client = true
exclusions[7].attr.modifiers.clear(); exclusions[8].attr.base = NaN
for (const e of exclusions) {
  const old = e.health; check(!api.apply(e), 'Excluded entity unchanged'); near(e.health, old)
}
check(!api.apply({}), 'Non-living entities excluded')
const original = new LivingEntity(); api.apply(original); original.health = 37
const reloaded = new LivingEntity()
reloaded.attr.modifiers = new Map(original.attr.modifiers)
reloaded.health = Math.min(original.health, reloaded.getMaxHealth())
api.apply(reloaded)
near(reloaded.getMaxHealth(), 80, 'Permanent modifier survives simulated save/load')
near(reloaded.health, 37, 'NBT health is not clamped against old 25.6 maximum')
const failing = new LivingEntity()
const beforeFailure = failing.getMaxHealth()
let failOnce = true
failing.setHealth = function(value) {
  if (failOnce) { failOnce = false; throw new Error('simulated health setter failure') }
  this.health = value
}
assert.throws(() => api.apply(failing), /simulated health setter failure/)
near(failing.getMaxHealth(), beforeFailure, 'Failure restores previous modifier')
near(failing.health, beforeFailure, 'Failure restores previous health')
const taskQueue = []
const scheduled = new LivingEntity()
scheduled.server = { scheduleInTicks(ticks, fn) { near(ticks, 1); taskQueue.push(fn) } }
scheduled.attr.modifiers.clear(); scheduled.champion = false; scheduled.health = 20
callbacks.spawn({ getChampion: () => ({ entity: () => scheduled }) })
check(taskQueue.length === 1, 'Pre-builder event waits one tick')
scheduled.champion = true
scheduled.attr.addOrReplacePermanentModifier(new Modifier(id, 0.28, operations.ADD_MULTIPLIED_TOTAL))
scheduled.health = scheduled.getMaxHealth()
callbacks.join({ entity: scheduled })
check(taskQueue.length === 1, 'Duplicate spawn/join events deduplicated')
taskQueue.shift()(); near(scheduled.getMaxHealth(), 80); near(scheduled.health, 80)
callbacks.spawn({ getChampion: () => ({ entity: () => scheduled }) })
scheduled.champion = false
const oldMax = scheduled.getMaxHealth(); taskQueue.shift()()
near(scheduled.getMaxHealth(), oldMax, 'Cancelled/removed champion gets no extra health')
const regular = new LivingEntity(); regular.attr.modifiers.clear(); regular.server = scheduled.server
callbacks.join({ entity: regular }); check(taskQueue.length === 0, 'No delayed tasks for ordinary mobs')
callbacks.loaded({ server: { getAllLevels: () => [{ getAllEntities: () => [regular] }] } })
check(taskQueue.length === 0, 'Startup scan preserves ordinary mobs')
check(logs.every(text => !String(text).includes('Invalid')), 'No unexpected errors')
const rollback = load(script.replace('const dynamicEnabled = true', 'const dynamicEnabled = false'))
const saved = new LivingEntity(); api.apply(saved); saved.health = 40
rollback.api.apply(saved)
near(saved.getMaxHealth(), 25.6, 'Rollback original native rule'); near(saved.health, 12.8, 'Rollback preserves damage ratio')
console.log('PASS: ' + checks + ' assertions; exact Legendary 10->50, 20->80, 200->400 anchors; five tiers; V1 migration; no stacking; wounded/save stability; other modifiers; caps; lifecycle; exclusions; rollback.')

// Companion scaling is on added health only, not on the whole result.
for (const [h, wanted] of [[10,30], [20,50], [200,300]]) {
  const e = new LivingEntity(h, 4); e.ownedCompanion = true; e.health *= 0.5
  api.apply(e); near(e.getMaxHealth(), wanted); near(e.health, wanted / 2)
  const writes=e.healthWrites; api.apply(e); check(e.healthWrites===writes,'Companion reload idempotent')
}
// V2 saved data migrates without stacking; include unchanged 20 HP anchor.
for (const h of [10,20,100,200,500]) for(let t=1;t<=5;t++) {
  const e=new LivingEntity(h,t)
  const amount=bonuses[t]*Math.pow(h/20,Math.log(10/3)/Math.log(5))/h
  e.attr.addOrReplacePermanentModifier(new Modifier(id,amount,operations.ADD_MULTIPLIED_TOTAL)); e.health=e.getMaxHealth()*0.4
  api.apply(e); near(e.getMaxHealth(),h+extra(h,t)); near(e.health,(h+extra(h,t))*0.4)
}
console.log('PASS: V3 companion and V2 migration checks; total assertions='+checks)
