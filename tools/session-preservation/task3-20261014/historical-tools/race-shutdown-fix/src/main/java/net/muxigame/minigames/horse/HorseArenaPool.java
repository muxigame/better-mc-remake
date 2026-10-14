package net.muxigame.minigames.horse;

import com.google.gson.*;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.*;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.*;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.*;
import java.io.IOException;
import java.nio.file.*;
import java.util.*;

/** Server-thread leases. A slot is reusable only after a durable return and loaded entity sweep. */
public final class HorseArenaPool {
    public static final int CAPACITY=32;
    private static final Map<MinecraftServer,HorseArenaPool> LIVE=new IdentityHashMap<>();
    /** Explicit lease membership, not a global type or tag whitelist. Server side only. */
    public static boolean requiresSimulation(Entity entity){
        if(!(entity.level() instanceof ServerLevel level)||entity.isRemoved()||!level.getServer().isSameThread())return false;
        var pool=LIVE.get(level.getServer());if(pool==null)return false;
        for(var lease:pool.leases.values())if(lease.dimension.equals(level.dimension().location().toString())
            &&lease.actors.containsKey(entity.getUUID())&&entity.getTags().contains(HorsePilot.MARKER)
            &&entity.getTags().contains(SESSION_TAG+lease.generation)&&entity.getTags().contains(SLOT_TAG+lease.slot))return true;
        return false;
    }
    public static final String SESSION_TAG="muxi_horse_session_",SLOT_TAG="muxi_horse_slot_";
    private static final TicketType<UUID> SIMULATION=TicketType.create("muxi_horse_simulation",UUID::compareTo);
    private static final TicketType<UUID> PRELOAD=TicketType.create("muxi_horse_preload",UUID::compareTo);
    public record Chunk(String dimension,int x,int z){}
    public record Actor(UUID id,Chunk location){}
    public static final class Lease {
        public final int slot;public final UUID generation;public final String dimension;
        private String state="PREPARING";
        private final Set<UUID> participants=new LinkedHashSet<>();
        private final Map<UUID,Actor> actors=new LinkedHashMap<>();
        private final Set<Chunk> ownedTickets=new LinkedHashSet<>();
        private final Set<Chunk> preloadTickets=new LinkedHashSet<>();
        private final Set<Chunk> simulationTickets=new LinkedHashSet<>();
        private int cursor,readyTicks,cleanTicks;private boolean vacant;
        private Lease(int slot,UUID generation,String dimension){this.slot=slot;this.generation=generation;this.dimension=dimension;}
        public String state(){return state;}
    }
    private final MinecraftServer server;private final Path journal;
    private final Map<Integer,Lease> leases=new LinkedHashMap<>();
    private int roundRobin;
    public HorseArenaPool(MinecraftServer server,Path journal){this.server=server;this.journal=journal;load();LIVE.put(server,this);}
    private void thread(){if(!server.isSameThread())throw new IllegalStateException("Server thread required");}
    private Lease current(Lease lease){var active=leases.get(lease.slot);return active!=null&&active.generation.equals(lease.generation)?active:null;}
    private ServerLevel level(String dimension){var id=ResourceLocation.tryParse(dimension);return id==null?null:server.getLevel(ResourceKey.create(Registries.DIMENSION,id));}
    public Lease reserve(UUID generation,ResourceKey<Level> dimension){
        thread();if(!dimension.equals(HorseCourse.DIMENSION)&&!dimension.equals(HorseCourse.STADIUM_DIMENSION))throw new IllegalArgumentException("Private race dimension required");
        for(int slot=0;slot<CAPACITY;slot++)if(!leases.containsKey(slot)){
            var lease=new Lease(slot,generation,dimension.location().toString());leases.put(slot,lease);
            try{save();}catch(RuntimeException failure){leases.remove(slot);throw failure;}
            return lease;
        }
        throw new IllegalStateException("All race sites occupied or awaiting safe recovery");
    }
    private List<Chunk> footprint(Lease lease){
        var result=new ArrayList<Chunk>();int x=lease.slot*256;
        for(int cx=(x-112)>>4;cx<=(x+112)>>4;cx++)for(int cz=-82>>4;cz<=82>>4;cz++)result.add(new Chunk(lease.dimension,cx,cz));
        return result;
    }
    private List<Chunk> required(Lease lease){var result=new LinkedHashSet<>(footprint(lease));for(var actor:lease.actors.values())result.add(actor.location);return List.copyOf(result);}
    private boolean loaded(Chunk chunk){var level=level(chunk.dimension);return level!=null&&level.getChunkSource().getChunkNow(chunk.x,chunk.z)!=null&&level.areEntitiesLoaded(ChunkPos.asLong(chunk.x,chunk.z))&&level.isPositionEntityTicking(new BlockPos(chunk.x*16+8,65,chunk.z*16+8));}
    private void ticket(Lease lease,Chunk chunk){
        var level=level(chunk.dimension);if(level==null)return;
        // FULL(33)-2 = ENTITY_TICKING(31). NeoForge's explicit forceTicks flag
        // also covers tick paths that gate work on distance from players.
        // UUID ownership makes release independent of other rooms/world tickets.
        if(lease.simulationTickets.add(chunk))
            level.getChunkSource().addRegionTicket(SIMULATION,new ChunkPos(chunk.x,chunk.z),2,lease.generation,true);

    }
    private void clearPreloads(Lease lease){for(var chunk:lease.simulationTickets){var level=level(chunk.dimension);if(level!=null)level.getChunkSource().removeRegionTicket(SIMULATION,new ChunkPos(chunk.x,chunk.z),2,lease.generation,true);}lease.simulationTickets.clear();for(var chunk:lease.preloadTickets){var level=level(chunk.dimension);if(level!=null)level.getChunkSource().removeRegionTicket(PRELOAD,new ChunkPos(chunk.x,chunk.z),0,lease.generation);}lease.preloadTickets.clear();}
    public void tick(){
        thread();var list=List.copyOf(leases.values());
        // Two new tickets globally per tick, including all recovery sites.
        int issued=0;for(int n=0;n<list.size()*2&&issued<2;n++){
            var lease=list.get(Math.floorMod(roundRobin++,list.size()));if(lease.state.equals("AWAITING_RETURN"))continue;var chunks=required(lease);
            if(lease.cursor<chunks.size()){ticket(lease,chunks.get(lease.cursor++));issued++;}
        }
        for(var lease:list){
            if(lease.state.equals("AWAITING_RETURN")){
                returnedParticipants(lease);
                if(lease.participants.isEmpty()){
                    leases.remove(lease.slot);
                    try{save();}catch(RuntimeException failure){leases.put(lease.slot,lease);throw failure;}
                }
                continue;
            }
            var chunks=required(lease);boolean ready=lease.cursor>=chunks.size()&&chunks.stream().allMatch(c->{var level=level(c.dimension);return level!=null&&lease.simulationTickets.contains(c)&&loaded(c);});
            lease.readyTicks=ready?lease.readyTicks+1:0;
            if(!ready&&lease.cursor>=chunks.size())lease.cursor=0;
            if(lease.state.equals("PREPARING"))lease.vacant=lease.readyTicks>=2&&empty(lease);
            if(lease.state.equals("RELEASING")||lease.state.equals("RECOVERING"))recover(lease);
        }
    }
    public boolean ready(Lease lease){thread();return current(lease)==lease&&lease.readyTicks>=2;}
    public boolean vacant(Lease lease){thread();return current(lease)==lease&&lease.state.equals("PREPARING")&&lease.vacant&&empty(lease);}
    public void activate(Lease lease){thread();if(!vacant(lease))throw new IllegalStateException("Race site not verified vacant");lease.state="ACTIVE";save();}
    public void participant(Lease lease,ServerPlayer player){thread();if(current(lease)!=lease)throw new IllegalStateException("Stale arena lease");if(lease.participants.add(player.getUUID()))save();}
    public void track(Lease lease,Entity entity){
        thread();if(current(lease)!=lease||!entity.getTags().contains(HorsePilot.MARKER))throw new IllegalStateException("Only generated race actors may be leased");
        entity.addTag(SESSION_TAG+lease.generation);entity.addTag(SLOT_TAG+lease.slot);
        lease.actors.put(entity.getUUID(),pose(entity));save();
    }
    private Actor pose(Entity entity){return new Actor(entity.getUUID(),new Chunk(entity.level().dimension().location().toString(),entity.blockPosition().getX()>>4,entity.blockPosition().getZ()>>4));}
    public void checkpoint(Lease lease,Collection<Entity> actors){
        thread();if(current(lease)!=lease)return;boolean changed=false;
        for(var entity:actors)if(!entity.isRemoved()&&lease.actors.containsKey(entity.getUUID())&&!inside(lease,entity)){var pose=pose(entity);if(!pose.equals(lease.actors.put(entity.getUUID(),pose)))changed=true;}
        if(changed){lease.cursor=0;lease.readyTicks=0;save();}
    }
    public void release(Lease lease){thread();if(lease==null||current(lease)!=lease)return;lease.state="RELEASING";lease.cleanTicks=0;save();}
    public boolean released(Lease lease){thread();return lease==null||current(lease)!=lease;}
    private boolean inside(Lease lease,Entity entity){return entity.level().dimension().location().toString().equals(lease.dimension)&&(entity.blockPosition().getX()>>4)>=(lease.slot*256-112>>4)&&(entity.blockPosition().getX()>>4)<=(lease.slot*256+112>>4)&&(entity.blockPosition().getZ()>>4)>=(-82>>4)&&(entity.blockPosition().getZ()>>4)<=(82>>4);}
    private boolean empty(Lease lease){
        var level=level(lease.dimension);if(level==null)return false;
        for(var player:server.getPlayerList().getPlayers())if(inside(lease,player))return false;
        for(var entity:level.getAllEntities())if(!entity.isRemoved()&&inside(lease,entity))return false;
        return true;
    }
    private void recover(Lease lease){
        if(current(lease)!=lease||lease.readyTicks<2)return;
        returnedParticipants(lease);
        for(var actor:lease.actors.values()){
            if(!loaded(actor.location))return;
            for(var level:server.getAllLevels()){
                var entity=level.getEntity(actor.id);if(entity==null||entity.isRemoved())continue;
                if(!entity.getTags().contains(HorsePilot.MARKER)||!entity.getTags().contains(SESSION_TAG+lease.generation)||!entity.getTags().contains(SLOT_TAG+lease.slot))return;
                entity.stopRiding();entity.discard();
            }
        }
        boolean safe=empty(lease);
        lease.cleanTicks=safe?lease.cleanTicks+1:0;if(lease.cleanTicks<2)return;
        // Keep the durable claim until ticket release finishes. A crash before the journal
        // removal must reload and recheck this generation rather than leak forced chunks.
        for(var chunk:lease.ownedTickets){var level=level(chunk.dimension);if(level!=null)level.setChunkForced(chunk.x,chunk.z,false);}
        clearPreloads(lease);
        // The durable slot claim protects offline returns without keeping the whole
        // empty course ticking. Login restoration itself uses its source ticket.
        if(lease.participants.isEmpty())leases.remove(lease.slot);
        else {lease.state="AWAITING_RETURN";lease.cursor=0;lease.readyTicks=0;}
        try{save();}catch(RuntimeException failure){leases.put(lease.slot,lease);lease.state="RECOVERING";lease.cursor=0;lease.readyTicks=0;lease.cleanTicks=0;throw failure;}
        // No later stale close can alter a newer generation's tickets.
        lease.ownedTickets.clear();
    }
    private void returnedParticipants(Lease lease){
        boolean changed=false;
        for(var id:List.copyOf(lease.participants)){
            var player=server.getPlayerList().getPlayer(id);
            if(player!=null&&!HorseReturn.pending(player)&&!inside(lease,player)){lease.participants.remove(id);changed=true;}
        }
        // Offline participants keep their durable claim until login restoration succeeds.
        if(changed)save();
    }
    public JsonObject snapshot(){
        var out=new JsonObject();out.addProperty("capacity",CAPACITY);out.addProperty("held",leases.size());var list=new JsonArray();
        for(var lease:leases.values()){var row=new JsonObject();row.addProperty("slot",lease.slot);row.addProperty("generation",lease.generation.toString());row.addProperty("state",lease.state);row.addProperty("participants",lease.participants.size());row.addProperty("actors",lease.actors.size());row.addProperty("ownedTickets",lease.ownedTickets.size());row.addProperty("preloadTickets",lease.preloadTickets.size());row.addProperty("entityTickTickets",lease.simulationTickets.size());row.addProperty("readyTicks",lease.readyTicks);list.add(row);}out.add("leases",list);return out;
    }
    private void save(){
        var out=new JsonObject();out.addProperty("schema",1);var records=new JsonArray();
        for(var lease:leases.values()){
            var row=new JsonObject();row.addProperty("slot",lease.slot);row.addProperty("generation",lease.generation.toString());row.addProperty("dimension",lease.dimension);row.addProperty("state",lease.state);
            row.add("participants",new Gson().toJsonTree(lease.participants));row.add("actors",new Gson().toJsonTree(lease.actors.values()));row.add("ownedTickets",new Gson().toJsonTree(lease.ownedTickets));records.add(row);
        }out.add("leases",records);var temp=journal.resolveSibling(journal.getFileName()+".tmp");
        try{Files.createDirectories(journal.getParent());Files.writeString(temp,out.toString());Files.move(temp,journal,StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);}catch(IOException failure){throw new IllegalStateException("Arena lease journal unavailable; reuse remains blocked",failure);}
    }
    private void load(){
        if(!Files.exists(journal))return;
        try{
            var root=JsonParser.parseString(Files.readString(journal)).getAsJsonObject();if(root.get("schema").getAsInt()!=1||root.getAsJsonArray("leases").size()>CAPACITY)throw new IllegalArgumentException("Invalid lease schema");
            var gson=new Gson();for(var item:root.getAsJsonArray("leases")){
                var row=item.getAsJsonObject();int slot=row.get("slot").getAsInt();String dim=row.get("dimension").getAsString();if(slot<0||slot>=CAPACITY||leases.containsKey(slot)||!Set.of(HorseCourse.DIMENSION.location().toString(),HorseCourse.STADIUM_DIMENSION.location().toString()).contains(dim))throw new IllegalArgumentException("Invalid arena slot");
                var lease=new Lease(slot,UUID.fromString(row.get("generation").getAsString()),dim);lease.state="RECOVERING";
                if(row.getAsJsonArray("participants").size()>4||row.getAsJsonArray("actors").size()>HorseRaceGrid.FIELD_SIZE*2||row.getAsJsonArray("ownedTickets").size()>200)throw new IllegalArgumentException("Invalid lease bounds");
                for(var player:row.getAsJsonArray("participants"))lease.participants.add(UUID.fromString(player.getAsString()));
                for(var actor:row.getAsJsonArray("actors")){var pose=gson.fromJson(actor,Actor.class);validate(pose.location);if(lease.actors.put(pose.id,pose)!=null)throw new IllegalArgumentException("Duplicate actor");}
                for(var chunk:row.getAsJsonArray("ownedTickets")){var value=gson.fromJson(chunk,Chunk.class);validate(value);lease.ownedTickets.add(value);}
                leases.put(slot,lease);
            }
        }catch(Exception failure){leases.clear();throw new IllegalStateException("Invalid arena journal; safe recovery required",failure);}
    }
    private void validate(Chunk chunk){if(chunk==null||ResourceLocation.tryParse(chunk.dimension)==null||Math.abs((long)chunk.x)>1_875_000||Math.abs((long)chunk.z)>1_875_000)throw new IllegalArgumentException("Invalid actor location");}
    public void shutdown(){thread();LIVE.remove(server);for(var lease:leases.values())lease.state="RECOVERING";save();for(var lease:leases.values()){clearPreloads(lease);for(var chunk:lease.ownedTickets){var level=level(chunk.dimension);if(level!=null)level.setChunkForced(chunk.x,chunk.z,false);}}}
}
