package net.muxigame.minigames.horse;

import com.google.gson.*;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.Registries;
import net.minecraft.nbt.*;
import net.minecraft.resources.*;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.animal.horse.Horse;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.portal.DimensionTransition;
import net.minecraft.world.level.storage.LevelResource;
import net.minecraft.world.phys.Vec3;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

/** Sandbox-only physical asset trial. Not registered in gameplay and never creates a replacement entity. */
public final class HorseAssetLease {
    public static final String FIXTURE="muxi_horse_generated_lab_asset",LEASE="muxi_horse_asset_lease";
    public record Entry(UUID token,UUID horse,String source,double x,double y,double z,float yaw,float pitch,String snapshot,int targetX,int targetZ,String phase) {
        Entry phase(String value){return new Entry(token,horse,source,x,y,z,yaw,pitch,snapshot,targetX,targetZ,value);}
    }
    private final MinecraftServer server;
    private final Path file;
    private final Map<UUID,Entry> entries=new LinkedHashMap<>();
    private final Gson gson=new GsonBuilder().setPrettyPrinting().create();
    public HorseAssetLease(MinecraftServer server){
        this.server=server;Path root=server.getWorldPath(LevelResource.ROOT).toAbsolutePath();
        if(!Boolean.getBoolean("muxi.horse.assetLab")||!Files.exists(root.resolve("HORSE_ASSET_LAB")))throw new IllegalStateException("Physical horse leases require an explicitly marked isolated lab");
        file=root.resolve("data/muxi-horse-asset-lab.json");
        try{if(Files.exists(file)){var values=gson.fromJson(Files.readString(file),Entry[].class);if(values==null||values.length>4096)throw new IllegalStateException("Invalid lease journal");for(var value:values){validate(value);if(entries.putIfAbsent(value.token(),value)!=null)throw new IllegalStateException("Duplicate lease token");}}}catch(Exception failure){throw new IllegalStateException("Unreadable lease journal; fail closed",failure);}
    }
    private void thread(){if(!server.isSameThread())throw new IllegalStateException("Server thread required");}
    private static void validate(Entry e){if(e==null||e.token()==null||e.horse()==null||e.source()==null||!Double.isFinite(e.x())||!Double.isFinite(e.y())||!Double.isFinite(e.z())||e.snapshot()==null||e.snapshot().length()>131072||!Set.of("PREPARED","ACTIVE","RETURNING","CLOSED").contains(e.phase()))throw new IllegalStateException("Invalid lease entry");}
    private void store(Entry entry){validate(entry);var next=new LinkedHashMap<>(entries);next.put(entry.token(),entry);if(next.size()>4096)throw new IllegalStateException("Lab lease journal full");
        try{Files.createDirectories(file.getParent());byte[] data=gson.toJson(next.values()).getBytes(StandardCharsets.UTF_8);Path tmp=file.resolveSibling(file.getFileName()+".tmp");try(var channel=FileChannel.open(tmp,StandardOpenOption.CREATE,StandardOpenOption.TRUNCATE_EXISTING,StandardOpenOption.WRITE)){var buffer=java.nio.ByteBuffer.wrap(data);while(buffer.hasRemaining())channel.write(buffer);channel.force(true);}Files.move(tmp,file,StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);entries.clear();entries.putAll(next);}catch(Exception failure){throw new IllegalStateException("Lease journal write failed; state not advanced",failure);}}
    private ServerLevel source(Entry e){var id=ResourceLocation.tryParse(e.source());var level=id==null?null:server.getLevel(ResourceKey.create(Registries.DIMENSION,id));if(level==null)throw new IllegalStateException("Source dimension unavailable; keep lease pending");return level;}
    private Horse unique(UUID uuid){Horse found=null;for(var level:server.getAllLevels()){Entity entity=level.getEntity(uuid);if(entity!=null&&!entity.isRemoved()){if(!(entity instanceof Horse h)||found!=null)throw new IllegalStateException("Duplicate or incompatible UUID; stop recovery");found=h;}}return found;}
    public Entry checkout(UUID token,Horse horse,HorseCourse course){thread();if(entries.containsKey(token)){var previous=entries.get(token);if(!previous.horse().equals(horse.getUUID())||previous.targetX()!=(int)course.start.x||previous.targetZ()!=(int)course.start.z)throw new IllegalStateException("Lease token payload mismatch");return previous;}
        if(horse.getType()!=EntityType.HORSE||!horse.getTags().contains(FIXTURE)||!horse.isAlive()||horse.level().dimension().equals(HorseCourse.DIMENSION)||!horse.getPassengers().isEmpty())throw new IllegalStateException("Only generated, unmounted lab horses can be leased");
        if(entries.values().stream().anyMatch(e->e.horse().equals(horse.getUUID())&&!e.phase().equals("CLOSED")))throw new IllegalStateException("Horse already leased");
        if(unique(horse.getUUID())!=horse)throw new IllegalStateException("Source horse UUID not unique");
        CompoundTag snapshot=new CompoundTag();horse.saveWithoutId(snapshot);snapshot.remove("Passengers");var entry=new Entry(token,horse.getUUID(),horse.level().dimension().location().toString(),horse.getX(),horse.getY(),horse.getZ(),horse.getYRot(),horse.getXRot(),snapshot.toString(),(int)course.start.x,(int)course.start.z,"PREPARED");store(entry);
        // A durable PREPARED record can be recovered on either side of the native transfer.
        horse.addTag(LEASE);horse.setNoAi(true);horse.setInvulnerable(true);source(entry).setChunkForced(BlockPos.containing(entry.x(),entry.y(),entry.z()).getX()>>4,BlockPos.containing(entry.x(),entry.y(),entry.z()).getZ()>>4,true);
        var level=server.getLevel(HorseCourse.DIMENSION);if(level==null)throw new IllegalStateException("Lab dimension missing");course.build(level);
        Entity moved=horse.changeDimension(new DimensionTransition(level,course.start,Vec3.ZERO,0,0,DimensionTransition.DO_NOTHING));
        if(!(moved instanceof Horse h)||!h.getUUID().equals(entry.horse())||unique(entry.horse())!=h)throw new IllegalStateException("Native transfer failed; keep prepared lease");
        server.saveEverything(false,true,true);store(entry.phase("ACTIVE"));return entries.get(token);
    }
    public Horse horse(UUID token){thread();var entry=entries.get(token);if(entry==null||!entry.phase().equals("ACTIVE"))throw new IllegalStateException("No active horse lease");Horse horse=unique(entry.horse());if(horse==null||!horse.getTags().contains(FIXTURE)||!horse.getTags().contains(LEASE)||!horse.level().dimension().equals(HorseCourse.DIMENSION))throw new IllegalStateException("Active leased horse unavailable");return horse;}
    public Entry restore(UUID token){thread();var e=entries.get(token);if(e==null)throw new IllegalArgumentException("Unknown lease token");if(e.phase().equals("CLOSED"))return e;
        ServerLevel original=source(e);original.setChunkForced(((int)Math.floor(e.x()))>>4,((int)Math.floor(e.z()))>>4,true);
        var lab=server.getLevel(HorseCourse.DIMENSION);if(lab==null)throw new IllegalStateException("Lab dimension unavailable; keep lease pending");
        for(int cx=(e.targetX()-6)>>4;cx<=(e.targetX()+62)>>4;cx++)for(int cz=(e.targetZ()-6)>>4;cz<=(e.targetZ()+46)>>4;cz++)lab.setChunkForced(cx,cz,true);
        Horse horse=unique(e.horse());if(horse==null)throw new IllegalStateException("Horse not loaded; no snapshot clone is permitted");
        if(!horse.getTags().contains(FIXTURE)||!horse.isAlive())throw new IllegalStateException("Missing fixture provenance; keep lease pending");
        store(e.phase("RETURNING"));horse.ejectPassengers();
        if(horse.level()!=original){Entity moved=horse.changeDimension(new DimensionTransition(original,new Vec3(e.x(),e.y(),e.z()),Vec3.ZERO,e.yaw(),e.pitch(),DimensionTransition.DO_NOTHING));if(!(moved instanceof Horse h)||!h.getUUID().equals(e.horse()))throw new IllegalStateException("Native return failed");horse=h;}
        try{CompoundTag snapshot=TagParser.parseTag(e.snapshot());if(!snapshot.hasUUID("UUID")||!snapshot.getUUID("UUID").equals(e.horse()))throw new IllegalStateException("Snapshot identity mismatch");horse.load(snapshot);horse.setPos(e.x(),e.y(),e.z());horse.setYRot(e.yaw());horse.setXRot(e.pitch());horse.setDeltaMovement(Vec3.ZERO);horse.removeTag(LEASE);if(unique(e.horse())!=horse)throw new IllegalStateException("Return UUID not unique");server.saveEverything(false,true,true);store(e.phase("CLOSED"));original.setChunkForced(((int)Math.floor(e.x()))>>4,((int)Math.floor(e.z()))>>4,false);for(int cx=(e.targetX()-6)>>4;cx<=(e.targetX()+62)>>4;cx++)for(int cz=(e.targetZ()-6)>>4;cz<=(e.targetZ()+46)>>4;cz++)lab.setChunkForced(cx,cz,false);return entries.get(token);}catch(Exception failure){throw new IllegalStateException("Return remains pending",failure);}
    }
    public List<Entry> pending(){return entries.values().stream().filter(e->!e.phase().equals("CLOSED")).toList();}
    public Path journal(){return file;}
}
