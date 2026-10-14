package net.muxigame.minigames.horse;

import net.minecraft.world.entity.ai.control.MoveControl;
import net.minecraft.world.entity.animal.horse.Horse;

/** Non-player riders do not take vanilla tickRidden's full forward input path. */
public final class HorseMoveControl extends MoveControl {
    public HorseMoveControl(Horse horse){super(horse);}
    public void tick(){
        boolean moving=operation==Operation.MOVE_TO;super.tick();
        // Keep vanilla attribute speed, turning, friction, gravity and collision. Match full rider input.
        if(moving&&!mob.isNoAi()){mob.setZza(1);mob.setXxa(0);}
    }
}
