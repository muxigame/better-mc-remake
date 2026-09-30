package net.muxigame.championcompanions.mixin;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.server.level.ServerLevel;
import net.muxigame.championcompanions.CompanionRules;
import net.muxigame.championcompanions.CompanionConfig;
import top.theillusivec4.champions.common.config.ChampionsConfig;
import org.objectweb.asm.Opcodes;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.Redirect;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(targets="top.theillusivec4.champions.common.champion.ChampionSpawnHandler", remap=false)
public abstract class OneRollMixin {
    // Replace this one probability read, never the global Champions setting.
    // The normal eligibility, beacon check and tier lottery still run unchanged.
    @Redirect(method="trySpawn", at=@At(value="FIELD",
        target="Ltop/theillusivec4/champions/common/config/ChampionsConfig;spawnChance:F",
        opcode=Opcodes.GETSTATIC))
    private static float muxi$ownedProbability(LivingEntity entity, ServerLevel level) {
        return CompanionConfig.chanceFor(entity, ChampionsConfig.spawnChance);
    }

    @Inject(method="trySpawn", at=@At("HEAD"), cancellable=true)
    private static void muxi$rollOnce(LivingEntity entity, ServerLevel level, CallbackInfo ci) {
        CompanionRules.beginTierContext(entity);
        if (!CompanionRules.isOwned(entity)) return;
        if (entity.getPersistentData().getBoolean(CompanionRules.ROLLED)) {
            CompanionRules.endTierContext();
            ci.cancel();
            return;
        }
        entity.getPersistentData().putBoolean(CompanionRules.ROLLED, true);
    }

    @Inject(method="trySpawn", at=@At("RETURN"))
    private static void muxi$clearTierContext(LivingEntity entity, ServerLevel level, CallbackInfo ci) {
        CompanionRules.endTierContext();
    }
}
