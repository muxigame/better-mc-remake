package net.muxigame.championcompanions;

import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.OwnableEntity;
import net.minecraft.world.entity.ai.attributes.AttributeInstance;
import net.minecraft.resources.ResourceLocation;
import java.util.Set;
import java.util.UUID;

/** Deliberately NOT a blanket permission for tameables or neutral entities. */
public final class CompanionRules {
    public static final String ROLLED = "muxi_champion_companions.rolled_v1";
    private static final ThreadLocal<Boolean> OWNED_TIER_CONTEXT =
        ThreadLocal.withInitial(() -> false);
    public static final Set<String> TYPES = Set.of("touhou_little_maid:maid",
        "modulargolems:metal_golem", "modulargolems:humanoid_golem", "modulargolems:dog_golem");
    public static final Set<String> SAFE_AFFIXES = Set.of("hasty", "lively", "dampening", "adaptable",
        "reflective", "shielding", "knocking", "paralyzing", "wounding");
    private CompanionRules() {}
    public static boolean matches(String id, UUID owner) { return owner != null && TYPES.contains(id); }
    public static boolean isOwned(Entity entity) {
        if (!(entity instanceof OwnableEntity ownable)) return false;
        return matches(BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString(), ownable.getOwnerUUID());
    }
    public static double effectFactor(Entity entity) { return isOwned(entity) ? 0.5 : 1.0; }
    /** Selective tier-weight override active only while Champions rolls one entity. */
    public static void beginTierContext(Entity entity) { OWNED_TIER_CONTEXT.set(isOwned(entity)); }
    public static void endTierContext() { OWNED_TIER_CONTEXT.remove(); }
    public static int[] tierWeights(int[] naturalWeights) {
        return OWNED_TIER_CONTEXT.get() ? new int[]{50, 25, 15, 7, 3} : naturalWeights;
    }
    /** Avoid KubeJS's remapped/hidden Entity.getUUID/getId methods in scripts. */
    public static String entityKey(Entity entity) { return entity.getUUID().toString() + ":" + entity.getId(); }
    /** Rhino considers the overloaded vanilla removeModifier calls ambiguous. */
    public static void removeAttributeModifier(AttributeInstance attribute, ResourceLocation id) { attribute.removeModifier(id); }
    public static boolean friendly(Entity source, Entity target) {
        if (!isOwned(source) || target == null) return false;
        UUID owner = ((OwnableEntity)source).getOwnerUUID();
        return source == target || owner.equals(target.getUUID()) || source.isAlliedTo(target)
            || (target instanceof OwnableEntity other && owner.equals(other.getOwnerUUID()));
    }
}
