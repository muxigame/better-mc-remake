package net.muxigame.minigames.horse;

import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.animal.horse.Horse;
import net.minecraft.world.entity.ai.attributes.Attributes;
import java.util.UUID;

/** Reads player assets. The prototype never removes, edits, saves or transfers the source horse. */
public record HorseIdentity(UUID horseId,String name,double speed,double jump,double health,boolean sourceHorse) {
    public static HorseIdentity read(ServerPlayer player){
        if(player.getVehicle() instanceof Horse h)return read(h);
        return new HorseIdentity(UUID.randomUUID(),"未知马",.225,.8,24,false);
    }
    public static HorseIdentity read(Horse horse){return new HorseIdentity(horse.getUUID(),horse.hasCustomName()?horse.getName().getString():"未知马",horse.getAttributeValue(Attributes.MOVEMENT_SPEED),horse.getAttributeValue(Attributes.JUMP_STRENGTH),horse.getMaxHealth(),true);}
}
