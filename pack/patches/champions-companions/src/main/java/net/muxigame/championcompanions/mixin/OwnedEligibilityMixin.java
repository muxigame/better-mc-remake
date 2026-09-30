package net.muxigame.championcompanions.mixin;
import net.minecraft.world.entity.LivingEntity;
import net.muxigame.championcompanions.CompanionRules;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

@Mixin(targets="net.muxigame.core.feature.champions.ChampionRules", remap=false)
public abstract class OwnedEligibilityMixin {
    @Inject(method="mayBeChampion", at=@At("HEAD"), cancellable=true)
    private static void muxi$allowOnlyOwnedCompanions(LivingEntity entity, CallbackInfoReturnable<Boolean> cir) {
        if (CompanionRules.isOwned(entity)) cir.setReturnValue(true);
    }
}
