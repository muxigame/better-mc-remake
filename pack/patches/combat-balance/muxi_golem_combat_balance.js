// Modular Golems combat balance: 50% of the unmodified attribute value.
// Applies to material stats, upgrades and equipment, not just bare materials.
// Transient, stable-ID modifiers do not alter the stored base stats or stack
// when an entity is loaded again. No per-tick polling and no extra damage hook.
// Mobility, attack speed, reach, size, cooldowns, recipes and slots are unchanged.
// Restart the server for consistent application to all already-loaded golems.

const MuxiGolemCombatBalance = (() => {
  const CompanionRules = Java.loadClass('net.muxigame.championcompanions.CompanionRules')
  const Attributes = Java.loadClass('net.minecraft.world.entity.ai.attributes.Attributes')
  const AttributeModifier = Java.loadClass('net.minecraft.world.entity.ai.attributes.AttributeModifier')
  const Operation = Java.loadClass('net.minecraft.world.entity.ai.attributes.AttributeModifier$Operation')
  const ResourceLocation = Java.loadClass('net.minecraft.resources.ResourceLocation')
  const GolemTypes = Java.loadClass('dev.xkmc.modulargolems.init.registrate.GolemTypes')

  const id = ResourceLocation.fromNamespaceAndPath('muxigame', 'golem_combat_half')
  const modifier = new AttributeModifier(id, -0.5, Operation.ADD_MULTIPLIED_TOTAL)
  const attributes = [
    Attributes.MAX_HEALTH,
    Attributes.ATTACK_DAMAGE,
    Attributes.ARMOR,
    Attributes.ARMOR_TOUGHNESS,
    Attributes.KNOCKBACK_RESISTANCE,
    Attributes.ATTACK_KNOCKBACK,
    GolemTypes.GOLEM_REGEN.holder(),
    GolemTypes.GOLEM_SWEEP.holder(),
    GolemTypes.DYNAMIC_REDUCTION.holder()
  ]

  return {
    apply(entity) {
      attributes.forEach(attribute => {
        var instance = entity.getAttribute(attribute)
        if (instance == null) return
        CompanionRules.removeAttributeModifier(instance, id)
        instance.addTransientModifier(modifier)
      })

      // Clamp only. Multiplying current health here would halve it again after
      // every chunk reload, since transient modifiers are not stored in NBT.
      var maxHealth = entity.getMaxHealth()
      if (entity.getHealth() > maxHealth) entity.setHealth(maxHealth)
    }
  }
})()

;[
  'modulargolems:metal_golem',
  'modulargolems:humanoid_golem',
  'modulargolems:dog_golem'
].forEach(type => {
  EntityEvents.spawned(type, event => MuxiGolemCombatBalance.apply(event.entity))
})
