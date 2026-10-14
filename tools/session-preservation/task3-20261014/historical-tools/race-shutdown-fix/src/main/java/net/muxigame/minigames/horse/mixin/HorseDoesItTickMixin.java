package net.muxigame.minigames.horse.mixin;
import net.minecraft.world.entity.Entity;
import net.muxigame.minigames.horse.HorseArenaPool;
import org.spongepowered.asm.mixin.*;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** Optional compatibility for installed Does It Tick 1.1.4 distance cancellation. */
@Pseudo
@Mixin(targets="toni.doesittick.DoesItTick",remap=false)
public abstract class HorseDoesItTickMixin {
    @Inject(method="isTickable",at=@At("HEAD"),cancellable=true,remap=false)
    private static void muxi$leasedRaceActor(Entity entity,CallbackInfoReturnable<Boolean> result){
        if(HorseArenaPool.requiresSimulation(entity))result.setReturnValue(true);
    }
}
