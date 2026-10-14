package net.muxigame.horseunifiedqa;
import com.google.gson.*;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.Registries;
import net.minecraft.server.level.TicketType;
import net.minecraft.world.phys.Vec3;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.*;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.*;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.entity.animal.horse.Horse;
import net.minecraft.world.level.*;
import net.minecraft.world.level.block.Blocks;
import net.muxigame.minigames.*;
import net.muxigame.minigames.horse.*;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import java.nio.file.*;
import java.util.*;

/** Explicit generated asset fixture and observations on the actual dedicated server. */
@EventBusSubscriber(modid="muxi_horse_racing")
public final class HorseUnifiedServerQA {
    static final Gson JSON=new GsonBuilder().setPrettyPrinting().create();
    static final Path ROOT=Path.of(System.getProperty("qa.local.root","."));
    static final Map<String,UUID> SOURCES=new HashMap<>();
    static final Map<String,Horse> OBSERVED=new HashMap<>();
    static final Deque<JsonObject> ASSET_EVENTS=new ArrayDeque<>();
    static String fixturePhase="fresh-login";
    static final ResourceKey<Level> SOURCE=ResourceKey.create(Registries.DIMENSION,ResourceLocation.parse("muxi_game_core:adventure"));
    static final UUID PAD_ID=UUID.randomUUID();
    static final TicketType<UUID> PAD=TicketType.create("horse_qa_pad",UUID::compareTo,200);
    static boolean prepared,padHeld;
    static int last=-1;
    static void padTickets(net.minecraft.server.MinecraftServer server,boolean add){
        for(var key:List.of(Level.OVERWORLD,SOURCE)){var level=server.getLevel(key);if(level==null)throw new IllegalStateException("Fixture dimension absent");
            for(int x=-1;x<=0;x++)for(int z=-1;z<=0;z++){var chunk=new ChunkPos(x,z);if(add)level.getChunkSource().addRegionTicket(PAD,chunk,2,PAD_ID);else level.getChunkSource().removeRegionTicket(PAD,chunk,2,PAD_ID);}}
        padHeld=add;
    }
    static void floor(net.minecraft.server.level.ServerLevel level){
        for(int x=-5;x<=15;x++)for(int z=-5;z<=5;z++){if(!level.setBlockAndUpdate(new BlockPos(x,64,z),Blocks.STONE.defaultBlockState())&&!level.getBlockState(new BlockPos(x,64,z)).is(Blocks.STONE))throw new IllegalStateException("Fixture floor placement denied");for(int y=65;y<70;y++)level.setBlockAndUpdate(new BlockPos(x,y,z),Blocks.AIR.defaultBlockState());}
    }
    static void fresh(net.minecraft.server.MinecraftServer server){
        if(!SOURCES.isEmpty()||server.getPlayerList().getPlayerCount()!=2)throw new IllegalStateException("Exactly two fresh real clients required");
        for(var p:server.getPlayerList().getPlayers())if(!Set.of("10000","10001").contains(p.getGameProfile().getName())||p.connection.getConnection().getRemoteAddress()==null||!p.isAlive())throw new IllegalStateException("Live private numeric identities and real sockets only");
    }
    static JsonObject position(net.minecraft.world.entity.Entity e){var row=new JsonObject();row.addProperty("x",e.getX());row.addProperty("y",e.getY());row.addProperty("z",e.getZ());return row;}
    static void write(String name,Object value)throws Exception{Path p=ROOT.resolve("coordinator").resolve(name),tmp=p.resolveSibling(name+".tmp");Files.writeString(tmp,JSON.toJson(value));Files.move(tmp,p,StandardCopyOption.REPLACE_EXISTING,StandardCopyOption.ATOMIC_MOVE);}
    static JsonObject source(ServerPlayer p){
        var row=new JsonObject();UUID id=SOURCES.get(p.getGameProfile().getName());if(id==null)return row;
        row.addProperty("uuid",id.toString());var entity=p.server.getLevel(SOURCE).getEntity(id);row.addProperty("alive",entity!=null&&entity.isAlive());row.addProperty("mounted",p.getVehicle()==entity&&entity!=null);row.addProperty("pendingReturn",HorseReturn.pending(p));
        int count=0;for(var level:p.server.getAllLevels())for(var e:level.getAllEntities())if(!e.isRemoved()&&e.getUUID().equals(id))count++;row.addProperty("copies",count);
        var remembered=entity instanceof Horse h?h:OBSERVED.get(p.getGameProfile().getName());if(remembered!=null){OBSERVED.put(p.getGameProfile().getName(),remembered);row.add("lastKnownPosition",position(remembered));row.addProperty("lastKnownHealth",remembered.getHealth());row.addProperty("removed",remembered.isRemoved());row.addProperty("removalReason",remembered.getRemovalReason()==null?"NONE":remembered.getRemovalReason().name());}
        if(entity instanceof Horse h){row.addProperty("name",h.getCustomName().getString());row.addProperty("speed",h.getAttribute(Attributes.MOVEMENT_SPEED).getBaseValue());row.addProperty("jump",h.getAttribute(Attributes.JUMP_STRENGTH).getBaseValue());row.addProperty("health",h.getHealth());row.add("position",position(h));row.addProperty("entityTicking",h.level().isClientSide()?false:((net.minecraft.server.level.ServerLevel)h.level()).isPositionEntityTicking(h.blockPosition()));}
        return row;
    }
    @SubscribeEvent public static void login(net.neoforged.neoforge.event.entity.player.PlayerEvent.PlayerLoggedInEvent event){if(!Boolean.getBoolean("muxi.horse.unifiedQa")||SOURCES.isEmpty())return;if(event.getEntity() instanceof ServerPlayer p&&SOURCES.containsKey(p.getGameProfile().getName())){TrustedAccounts.admit(p);GameRuntime.get(p.server).platform.refresh(p);}}
    @SubscribeEvent public static void tick(ServerTickEvent.Post event){
        if(!Boolean.getBoolean("muxi.horse.unifiedQa")||!System.getProperty("qa.local.role","").equals("server"))return;
        try{if(padHeld&&event.getServer().getTickCount()%100==0)padTickets(event.getServer(),true);
            if(!SOURCES.isEmpty()&&event.getServer().getTickCount()%20==0){var evidence=new JsonObject();evidence.addProperty("serverTick",event.getServer().getTickCount());evidence.addProperty("fixturePhase",fixturePhase);var players=new JsonObject();for(var p:event.getServer().getPlayerList().getPlayers()){var row=source(p);row.addProperty("playerAlive",p.isAlive());row.add("playerPosition",position(p));players.add(p.getGameProfile().getName(),row);}evidence.add("players",players);write("fixture-assets-latest.json",evidence);ASSET_EVENTS.addLast(evidence);while(ASSET_EVENTS.size()>128)ASSET_EVENTS.removeFirst();write("fixture-asset-events.json",ASSET_EVENTS);}
        }catch(Exception observationFailure){observationFailure.printStackTrace();}
        Path input=ROOT.resolve("coordinator/horse-server-command.json");if(!Files.isRegularFile(input))return;
        try{
            var command=JsonParser.parseString(Files.readString(input)).getAsJsonObject();int id=command.get("id").getAsInt();if(id<=last)return;last=id;var result=new JsonObject();result.addProperty("id",id);
            try{
                var server=event.getServer();if(!server.getWorldData().getLevelName().equals("qa-world"))throw new IllegalStateException("Private unified world only");
                String type=command.get("type").getAsString();if(!type.equals("observe"))fixturePhase=type;
                if(type.equals("prepare-pad")){
                    fresh(server);if(prepared)throw new IllegalStateException("Fixture already prepared");padTickets(server,true);floor(server.overworld());floor(server.getLevel(SOURCE));prepared=true;
                }else if(type.equals("transit-home")||type.equals("transit-source")){
                    fresh(server);if(!prepared)throw new IllegalStateException("Prepare fixture chunks first");var target=type.equals("transit-home")?server.overworld():server.getLevel(SOURCE);
                    for(var p:server.getPlayerList().getPlayers()){double x=p.getGameProfile().getName().equals("10000")?.5:10.5;p.stopRiding();if(!p.teleportTo(target,x,65,.5,Set.of(),0,0))throw new IllegalStateException("Fixture transit denied");p.setDeltaMovement(Vec3.ZERO);p.fallDistance=0;}
                }else if(type.equals("mount-fixture")){
                    fresh(server);if(!prepared)throw new IllegalStateException("Fixture not prepared");var level=server.getLevel(SOURCE);
                    for(var p:server.getPlayerList().getPlayers())if(p.level()!=level||!level.isPositionEntityTicking(p.blockPosition())||!level.getBlockState(p.blockPosition().below()).is(Blocks.STONE))throw new IllegalStateException("Source player and entity-ticking floor must be ready before horse spawn");
                    for(var p:server.getPlayerList().getPlayers()){
                        String name=p.getGameProfile().getName();TrustedAccounts.admit(p);GameRuntime.get(server).platform.refresh(p);double x=name.equals("10000")?.5:10.5;var h=EntityType.HORSE.create(level);h.setPos(x,65,.5);h.setTamed(true);h.setAge(0);h.setNoAi(true);h.setPersistenceRequired();h.setCustomName(Component.literal("Private horse "+name));h.getAttribute(Attributes.MOVEMENT_SPEED).setBaseValue(name.equals("10000")?.38:.28);h.getAttribute(Attributes.JUMP_STRENGTH).setBaseValue(1.0);if(!level.addFreshEntity(h))throw new IllegalStateException("Generated horse rejected");SOURCES.put(name,h.getUUID());OBSERVED.put(name,h);
                        if(!p.startRiding(h,true))throw new IllegalStateException("Generated source mounting denied");
                    }
                    result.addProperty("assistedGeneratedHorseFixture",true);
                }else if(type.equals("release-pad")){if(!prepared)throw new IllegalStateException("Fixture not prepared");padTickets(server,false);
                }else if(!type.equals("observe"))throw new IllegalArgumentException("Unknown fixture callback");
                var players=new JsonObject();for(var p:server.getPlayerList().getPlayers()){var row=new JsonObject();row.add("source",source(p));row.add("app",GameRuntime.get(server).snapshot(p,""));row.addProperty("dimension",p.level().dimension().location().toString());row.addProperty("network",p.connection.getConnection().getRemoteAddress() instanceof java.net.InetSocketAddress);row.addProperty("uuid",p.getUUID().toString());try{row.addProperty("verifiedUid",TrustedAccounts.uid(p));}catch(IllegalArgumentException absent){row.addProperty("verifiedUid",0);}row.addProperty("connectionAcceptingMessages",p.connection.isAcceptingMessages());row.addProperty("alive",p.isAlive());row.addProperty("health",p.getHealth());row.add("position",position(p));row.addProperty("entityTicking",p.serverLevel().isPositionEntityTicking(p.blockPosition()));row.addProperty("feetBlock",p.level().getBlockState(p.blockPosition().below()).getBlock().builtInRegistryHolder().key().location().toString());players.add(p.getGameProfile().getName(),row);}result.add("players",players);result.addProperty("serverTick",server.getTickCount());result.addProperty("ok",true);
            }catch(Throwable failure){result.addProperty("ok",false);result.addProperty("error",failure.toString());failure.printStackTrace();}
            write("horse-server-result-"+id+".json",result);
        }catch(java.io.IOException temporaryLock){}catch(Throwable failure){failure.printStackTrace();}
    }
}
