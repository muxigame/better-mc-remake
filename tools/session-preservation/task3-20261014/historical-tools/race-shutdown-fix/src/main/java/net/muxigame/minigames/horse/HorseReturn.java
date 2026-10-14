package net.muxigame.minigames.horse;

import net.minecraft.core.registries.Registries;
import net.minecraft.nbt.*;
import net.minecraft.resources.*;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.GameType;
import net.muxigame.minigames.PlayerReturns;
import java.util.Set;
import java.util.UUID;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.TicketType;
import net.minecraft.world.level.ChunkPos;

/** Durable spectator location/mode only. No inventory kit, horse serialization or asset cloning. */
public final class HorseReturn {
    public static final String KEY="muxi_horse_spectator_return_v1";
    private static final TicketType<UUID> SOURCE=TicketType.create("muxi_horse_return_source",UUID::compareTo,200);
    private HorseReturn(){}
    public static boolean pending(ServerPlayer p){return p.getPersistentData().contains(KEY,Tag.TAG_COMPOUND);}
    public static void capture(ServerPlayer p){
        if(pending(p))throw new IllegalStateException("Horse spectator return already pending");
        PlayerReturns.check(p);CompoundTag tag=new CompoundTag();
        tag.putString("dimension",p.level().dimension().location().toString());tag.putDouble("x",p.getX());tag.putDouble("y",p.getY());tag.putDouble("z",p.getZ());
        tag.putFloat("yaw",p.getYRot());tag.putFloat("pitch",p.getXRot());tag.putInt("mode",p.gameMode.getGameModeForPlayer().getId());
        if(p.getVehicle()!=null)tag.putUUID("vehicle",p.getVehicle().getUUID());
        p.getPersistentData().put(KEY,tag);PlayerReturns.save(p);keepSource(p);
    }
    /** Short renewable ticket; disconnected players never retain a permanent chunk ticket. */
    public static void keepSource(ServerPlayer p){
        if(!pending(p))return;var tag=p.getPersistentData().getCompound(KEY);if(!tag.hasUUID("vehicle"))return;
        var id=ResourceLocation.tryParse(tag.getString("dimension"));var level=id==null?null:p.server.getLevel(ResourceKey.create(Registries.DIMENSION,id));
        double x=tag.getDouble("x"),y=tag.getDouble("y"),z=tag.getDouble("z");
        if(level!=null&&Double.isFinite(x)&&Double.isFinite(y)&&Double.isFinite(z))level.getChunkSource().addRegionTicket(SOURCE,new ChunkPos(BlockPos.containing(x,y,z)),2,p.getUUID());
    }
    public static void restore(ServerPlayer p){
        if(!pending(p))return;var tag=p.getPersistentData().getCompound(KEY).copy();
        var id=ResourceLocation.tryParse(tag.getString("dimension"));
        var level=id==null?null:p.server.getLevel(ResourceKey.create(Registries.DIMENSION,id));
        if(level==null)throw new IllegalStateException("Return dimension unavailable; keep durable return marker");
        double x=tag.getDouble("x"),y=tag.getDouble("y"),z=tag.getDouble("z");
        if(!Double.isFinite(x)||!Double.isFinite(y)||!Double.isFinite(z))throw new IllegalStateException("Invalid return position");
        keepSource(p);p.stopRiding();
        if(!p.teleportTo(level,x,y,z,Set.of(),tag.getFloat("yaw"),tag.getFloat("pitch"))||p.level()!=level||p.position().distanceToSqr(x,y,z)>.01)
            throw new IllegalStateException("Return teleport was denied; keep durable return marker");
        p.setGameMode(GameType.byId(tag.getInt("mode")));
        if(tag.hasUUID("vehicle")){
            var vehicle=level.getEntity(tag.getUUID("vehicle"));
            if(vehicle==null||vehicle.isRemoved()||!vehicle.isAlive()||!p.startRiding(vehicle,false))
                throw new IllegalStateException("Original mount not ready; keep durable return marker for retry");
        }
        p.getPersistentData().remove(KEY);
        try{PlayerReturns.save(p);}catch(RuntimeException failure){p.getPersistentData().put(KEY,tag);throw failure;}
        level.getChunkSource().removeRegionTicket(SOURCE,new ChunkPos(BlockPos.containing(x,y,z)),2,p.getUUID());
    }
}
