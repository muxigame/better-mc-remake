package net.muxigame.minigames.horse.mixin;

import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.ai.control.MoveControl;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

/** Setter only; ordinary mobs keep their vanilla controller. */
@Mixin(Mob.class)
public interface HorseControlAccess {
    @Accessor("moveControl") void muxi$horseControl(MoveControl control);
}
