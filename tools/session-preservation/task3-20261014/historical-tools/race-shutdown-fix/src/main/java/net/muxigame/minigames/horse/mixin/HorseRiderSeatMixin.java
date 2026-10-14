package net.muxigame.minigames.horse.mixin;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.animal.horse.Horse;
import net.minecraft.world.phys.Vec3;
import net.muxigame.minigames.horse.MaidRider;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** Corrects the verified built-in Wine Fox seat on our generated rider only, on both logical sides. */
@Mixin(Entity.class)
public abstract class HorseRiderSeatMixin {
    @Inject(method="getVehicleAttachmentPoint",at=@At("RETURN"),cancellable=true)
    private void muxi$winefoxSeat(Entity vehicle,CallbackInfoReturnable<Vec3> result){
        Entity rider=(Entity)(Object)this;
        if(vehicle instanceof Horse&&rider.getClass().getName().equals("com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid")
            &&rider.getCustomName()!=null&&MaidRider.WINEFOX_SEAT.equals(rider.getCustomName().getStyle().getInsertion()))
            result.setReturnValue(result.getReturnValue().add(0,.6,0));
    }
}
