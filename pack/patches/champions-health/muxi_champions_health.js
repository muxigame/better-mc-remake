// Champions 21.1.1.7 / KubeJS 2101.7.2: health-based tier scaling.
// H = max health with ONLY the Champions tier health modifier removed.
// V3: H + tierWeight * (0.625*H + 80.15625*H/(H+13.75)).
// Legendary anchors: 10 -> 50, 20 -> 80, 200 -> 400. Owned companions get HALF the bonus.
// Own the original modifier ID, so the old +7/14/21/28/35% is NOT stacked.
// Permanent modifiers preserve damaged HP correctly across NBT saves/loads.
// Set dynamicEnabled=false for a migration back to the original tier rule.
const MuxiChampionHealth = (() => {
  const dynamicEnabled = true
  const tierBonus = [0, 0.125, 0.3125, 0.625, 1, 1.5]
  const CompanionRules = Java.loadClass('net.muxigame.championcompanions.CompanionRules')
  const ChampionsApi = Java.loadClass('top.theillusivec4.champions.api.ChampionsApi')
  const LivingEntity = Java.loadClass('net.minecraft.world.entity.LivingEntity')
  const Player = Java.loadClass('net.minecraft.world.entity.player.Player')
  const EntityType = Java.loadClass('net.minecraft.world.entity.EntityType')
  const Attributes = Java.loadClass('net.minecraft.world.entity.ai.attributes.Attributes')
  const AttributeModifier = Java.loadClass('net.minecraft.world.entity.ai.attributes.AttributeModifier')
  const Operation = Java.loadClass('net.minecraft.world.entity.ai.attributes.AttributeModifier$Operation')
  const ResourceLocation = Java.loadClass('net.minecraft.resources.ResourceLocation')
  const HashSet = Java.loadClass('java.util.HashSet')
  const modifierId = ResourceLocation.fromNamespaceAndPath('champions', 'minecraft_generic.max_health_modifier')
  const operation = Operation.ADD_MULTIPLIED_TOTAL
  const pending = new HashSet()

  // Rhino/KubeJS exposes some Java no-arg methods and record components as
  // properties (not functions), notably Entity.level and AttributeModifier.
  function read(object, name) {
    return typeof object[name] === 'function' ? object[name]() : object[name]
  }

  function eligible(entity) {
    return entity instanceof LivingEntity && !(entity instanceof Player) &&
      !entity.getType().equals(EntityType.CREEPER) && !entity.isRemoved() &&
      entity.isAlive() && !read(read(entity, 'level'), 'isClientSide')
  }

  function apply(entity) {
    if (!eligible(entity)) return false
    var champion = ChampionsApi.get().getChampion(entity)
    if (!champion.isPresent()) return false
    var tier = Number(read(read(champion.get(), 'tier'), 'level'))
    if (tier < 1 || tier >= tierBonus.length || Math.floor(tier) !== tier) return false
    var attribute = entity.getAttribute(Attributes.MAX_HEALTH)
    if (attribute == null) return false
    var previous = attribute.getModifier(modifierId)
    // Preserve the native eligibility/conditions. Do not grant health where
    // Champions never applied its health modifier (including excluded mobs).
    if (previous == null) return false
    var previousMax = Number(entity.getMaxHealth())
    var previousHealth = Number(entity.getHealth())
    if (!isFinite(previousMax) || previousMax <= 0 || !isFinite(previousHealth)) return false

    // Do not divide the already-clamped result by a multiplier: that would be
    // incorrect at an attribute limit. Read the actual unmodified attribute.
    var baseline
    CompanionRules.removeAttributeModifier(attribute, modifierId)
    try {
      baseline = Number(attribute.getValue())
    } finally {
      attribute.addOrReplacePermanentModifier(previous)
    }
    if (!isFinite(baseline) || baseline <= 0) return false
    var amount = dynamicEnabled
      ? tierBonus[tier] * (0.625 + 80.15625 / (baseline + 13.75)) * CompanionRules.effectFactor(entity)
      : 0.35 * (tier / 5) * CompanionRules.effectFactor(entity)
    if (!isFinite(amount) || amount < 0) return false
    if (read(previous, 'operation').equals(operation) && Math.abs(Number(read(previous, 'amount')) - amount) < 1e-9) return false

    try {
      attribute.addOrReplacePermanentModifier(new AttributeModifier(modifierId, amount, operation))
      var nextMax = Number(entity.getMaxHealth())
      if (!isFinite(nextMax) || nextMax <= 0) throw new Error('Invalid resulting max health')
      // Fresh, full-health spawns stay full; an already-wounded creature stays
      // at the same HP percentage. Subsequent loads are idempotent.
      var healthRatio = Math.max(0, Math.min(1, previousHealth / previousMax))
      entity.setHealth(Math.min(nextMax, nextMax * healthRatio))
      return true
    } catch (error) {
      attribute.addOrReplacePermanentModifier(previous)
      entity.setHealth(Math.min(previousMax, previousHealth))
      throw error
    }
  }

  function enqueue(entity) {
    if (!eligible(entity)) return
    var server = entity.getServer()
    if (server == null) return
    var key = CompanionRules.entityKey(entity)
    if (!pending.add(key)) return
    // ChampionsEvents.spawn runs BEFORE its builder applies native modifiers.
    // A one-tick deferral also lets existing muxi friendly-mob cleanup finish.
    try {
      server.scheduleInTicks(1, () => {
        pending.remove(key)
        try { apply(entity) } catch (error) {
          console.error('[muxi-champions-health] ' + key + ': ' + error)
        }
      })
    } catch (error) {
      pending.remove(key)
      throw error
    }
  }

  function considerLoaded(entity) {
    if (!eligible(entity)) return
    var attribute = entity.getAttribute(Attributes.MAX_HEALTH)
    if (attribute != null && attribute.getModifier(modifierId) != null) enqueue(entity)
  }

  return { apply: apply, enqueue: enqueue, considerLoaded: considerLoaded }
})()

// Natural spawn, summon and applying/changing a tier on an existing entity.
ChampionsEvents.spawn(event => MuxiChampionHealth.enqueue(event.getChampion().entity()))
// Chunk/save loading; ordinary mobs do not get delayed tasks.
EntityEvents.spawned(event => MuxiChampionHealth.considerLoaded(event.entity))
// Catch startup chunks too. One startup scan, NOT per-tick world scanning.
ServerEvents.loaded(event => {
  event.server.getAllLevels().forEach(level => {
    level.getAllEntities().forEach(entity => MuxiChampionHealth.considerLoaded(entity))
  })
  console.info('[muxi-champions-health] Loaded tier health handler. No affix, loot or XP changes.')
})
