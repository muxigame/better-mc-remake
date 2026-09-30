// Offline behavioral checks. This does not substitute for an in-game test.
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const assert = require('node:assert/strict')

const fields = [
  'MAX_HEALTH', 'ATTACK_DAMAGE', 'ARMOR', 'ARMOR_TOUGHNESS',
  'KNOCKBACK_RESISTANCE', 'ATTACK_KNOCKBACK'
]
const Attributes = Object.fromEntries(fields.map(name => [name, name]))
const extras = ['GOLEM_REGEN', 'GOLEM_SWEEP', 'DYNAMIC_REDUCTION']
const callbacks = new Map()
class Modifier {
  constructor(id, amount, operation) { Object.assign(this, { id, amount, operation }) }
}
const classes = {
  'net.muxigame.championcompanions.CompanionRules': {
    removeAttributeModifier: (attribute, id) => attribute.removeModifier(id)
  },
  'net.minecraft.world.entity.ai.attributes.Attributes': Attributes,
  'net.minecraft.world.entity.ai.attributes.AttributeModifier': Modifier,
  'net.minecraft.world.entity.ai.attributes.AttributeModifier$Operation': {
    ADD_MULTIPLIED_TOTAL: 'ADD_MULTIPLIED_TOTAL'
  },
  'net.minecraft.resources.ResourceLocation': {
    fromNamespaceAndPath: (namespace, name) => namespace + ':' + name
  },
  'dev.xkmc.modulargolems.init.registrate.GolemTypes': Object.fromEntries(
    extras.map(name => [name, { holder: () => name }])
  )
}
const context = vm.createContext({
  Java: { loadClass(name) { assert.ok(name in classes, name); return classes[name] } },
  EntityEvents: { spawned(type, callback) {
    assert.ok(!callbacks.has(type)); callbacks.set(type, callback)
  } }
})
const script = path.join(__dirname, 'muxi_golem_combat_balance.js')
vm.runInContext(fs.readFileSync(script, 'utf8'), context, { filename: script })
assert.deepEqual([...callbacks.keys()].sort(), [
  'modulargolems:dog_golem', 'modulargolems:humanoid_golem', 'modulargolems:metal_golem'
])
assert.ok(!callbacks.has('minecraft:iron_golem'))

class Attribute {
  constructor(base, bonus = 0) { this.base = base; this.bonus = bonus; this.modifiers = new Map() }
  removeModifier(id) { return this.modifiers.delete(id) }
  addTransientModifier(modifier) {
    assert.equal(modifier.operation, 'ADD_MULTIPLIED_TOTAL')
    assert.ok(!this.modifiers.has(modifier.id)); this.modifiers.set(modifier.id, modifier)
  }
  getValue() {
    let value = this.base + this.bonus
    for (const modifier of this.modifiers.values()) value *= 1 + modifier.amount
    return value
  }
}
function makeEntity() {
  const attrs = new Map([...fields, ...extras].map(name => [name, new Attribute(20, 4)]))
  attrs.set('MAX_HEALTH', new Attribute(100, 40))
  attrs.set('MOVEMENT_SPEED', new Attribute(0.3))
  attrs.set('ATTACK_SPEED', new Attribute(4))
  attrs.set('ENTITY_INTERACTION_RANGE', new Attribute(1.5))
  return {
    attrs, health: 140,
    getAttribute(name) { return this.attrs.get(name) || null },
    getMaxHealth() { return this.attrs.get('MAX_HEALTH').getValue() },
    getHealth() { return this.health },
    setHealth(value) { this.health = value }
  }
}
let checks = 0
for (const [type, callback] of callbacks) {
  const entity = makeEntity()
  callback({ entity })
  for (const name of [...fields, ...extras]) {
    const attribute = entity.getAttribute(name)
    assert.equal(attribute.getValue(), (attribute.base + attribute.bonus) / 2, type + ' ' + name)
    assert.equal(attribute.modifiers.size, 1)
    checks++
  }
  assert.equal(entity.health, 70)
  assert.equal(entity.getAttribute('MOVEMENT_SPEED').getValue(), 0.3)
  assert.equal(entity.getAttribute('ATTACK_SPEED').getValue(), 4)
  assert.equal(entity.getAttribute('ENTITY_INTERACTION_RANGE').getValue(), 1.5)
  callback({ entity })
  assert.equal(entity.getMaxHealth(), 70)
  assert.equal(entity.health, 70)
  assert.equal(entity.getAttribute('ATTACK_DAMAGE').modifiers.size, 1)
  entity.health = 25
  for (const attribute of entity.attrs.values()) attribute.modifiers.clear()
  callback({ entity })
  assert.equal(entity.health, 25, 'Chunk reload must not halve current health again')
  entity.getAttribute('ATTACK_DAMAGE').bonus += 8
  assert.equal(entity.getAttribute('ATTACK_DAMAGE').getValue(), 16, 'New gear must also be scaled')
  entity.getAttribute('ATTACK_DAMAGE').base = 40
  assert.equal(entity.getAttribute('ATTACK_DAMAGE').getValue(), 26, 'Base recalculation must keep modifier')
  entity.attrs.delete('GOLEM_SWEEP')
  assert.doesNotThrow(() => callback({ entity }))
  checks += 11
}
console.log('PASS: ' + checks + ' checks; 3 golem types; 9 attributes; no stacking; reload health stable; gear/base changes; missing attributes; mobility preserved.')
