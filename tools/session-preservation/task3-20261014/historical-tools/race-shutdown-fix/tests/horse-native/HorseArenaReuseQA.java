package net.muxigame.minigames.horseqa;
import com.google.gson.*;
import net.minecraft.core.BlockPos;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.server.*;
import net.minecraft.server.level.*;
import net.minecraft.server.players.PlayerList;
import net.minecraft.world.entity.*;
import net.minecraft.world.entity.animal.horse.Horse;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.portal.DimensionTransition;
import net.minecraft.world.level.storage.LevelResource;
import net.minecraft.world.phys.Vec3;
import net.muxigame.minigames.*;
import net.muxigame.minigames.horse.*;
import net.neoforged.bus.api.*;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.event.server.ServerStartedEvent;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import java.lang.reflect.*;
import java.nio.file.*;
import java.util.*;

/** 36 naturally completed native oval races, then cancellation, blockers and real JVM recovery. */
@EventBusSubscriber(modid="muxi_horse_racing")
public final class HorseArenaReuseQA {
    private static MinecraftServer server;private static HorseRooms rooms;
    private static final List<ServerPlayer> players=new ArrayList<>();private static final List<Horse> sources=new ArrayList<>();
    private static final Set<Integer> usedSlots=new HashSet<>();private static final JsonArray checks=new JsonArray(),races=new JsonArray();
    private static int ticks,stage,stageTicks,batch,completed;private static boolean done,staleChecked,resume,releaseFaultSeen;
    private static Entity foreign;private static ServerPlayer intruder;private static CompoundTag returnTag;
    private static final Path ROOT=Path.of("").toAbsolutePath();
    private static void check(boolean value,String label){if(!value)throw new IllegalStateException(label);checks.add(label);}
    private static void stage(int next){stage=next;stageTicks=0;}
    @SuppressWarnings("unchecked") private static ServerPlayer fixture(String name)throws Exception{
        Field field=HorseNativeQA.class.getDeclaredField("server");field.setAccessible(true);field.set(null,server);
        Method method=HorseNativeQA.class.getDeclaredMethod("fixture",String.class);method.setAccessible(true);return (ServerPlayer)method.invoke(null,name);
    }
    private static Horse source(ServerPlayer p,int i){
        var source=EntityType.HORSE.create(server.overworld());source.setTamed(true);source.setAge(0);source.setNoAi(true);source.setPersistenceRequired();source.setPos(.5+i*5,65,.5);source.getAttribute(Attributes.MOVEMENT_SPEED).setBaseValue(.38);source.getAttribute(Attributes.JUMP_STRENGTH).setBaseValue(1);server.overworld().addFreshEntity(source);p.teleportTo(server.overworld(),source.getX(),65,.5,0,0);p.startRiding(source,true);return source;
    }
    private static JsonObject state(){return rooms.snapshot(players.getFirst());}
    private static JsonObject room(ServerPlayer p){return state().getAsJsonArray("rooms").asList().stream().map(JsonElement::getAsJsonObject).filter(r->r.get("host").getAsString().equals(p.getUUID().toString())).findFirst().orElse(null);}
    private static String phase(ServerPlayer p){var r=room(p);return r==null?"NONE":r.get("phase").getAsString();}
    private static int held(){return state().getAsJsonObject("arenaPool").get("held").getAsInt();}
    private static void start(ServerPlayer p){rooms.action(p,"create","");rooms.action(p,"enemy","5");rooms.action(p,"start","");check(phase(p).equals("PREPARING")&&!HorseReturn.pending(p),"start verifies vacancy before transfer");}
    private static void clean(){check(held()==0,"all arena leases released");check(server.getLevel(HorseCourse.STADIUM_DIMENSION).getForcedChunks().isEmpty(),"all owned chunk tickets released");for(var level:server.getAllLevels())for(var entity:level.getAllEntities())if(!entity.isRemoved())check(!entity.getTags().contains(HorsePilot.MARKER),"no generated horse or rider remains");}
    @SubscribeEvent(priority=EventPriority.LOWEST) @SuppressWarnings("unchecked") public static void started(ServerStartedEvent e){
        if(!Boolean.getBoolean("muxi.horse.reuseQa"))return;server=e.getServer();resume=Boolean.getBoolean("muxi.horse.reuseResumeQa");
        try{
            check(Files.exists(ROOT.resolve("HORSE_QA_ONLY"))&&server.getPlayerList().getPlayers().isEmpty(),"private empty native world only");
            Field active=HorseRacing.class.getDeclaredField("ACTIVE");active.setAccessible(true);rooms=((Map<MinecraftServer,HorseRooms>)active.get(null)).get(server);
            if(resume){var p=fixture("ArenaReuse0");players.add(p);server.getPlayerList().load(p);check(HorseReturn.pending(p)&&held()==1,"actual JVM restart retains participant snapshot and quarantined lease");stage(100);return;}
            for(int x=-5;x<=25;x++)for(int z=-5;z<=5;z++)server.overworld().setBlock(new BlockPos(x,64,z),Blocks.STONE.defaultBlockState(),2);
            for(int i=0;i<4;i++){var p=fixture("ArenaReuse"+i);players.add(p);sources.add(source(p,i));if(!Boolean.getBoolean("muxi.horse.reuseFaultQa"))start(p);}
            if(Boolean.getBoolean("muxi.horse.reuseFaultQa")){start(players.getFirst());rooms.action(players.getFirst(),"leave","");stage(10);}
        }catch(Throwable f){finish(false,f.toString());}
    }
    @SubscribeEvent(priority=EventPriority.LOWEST) public static void tick(ServerTickEvent.Post e){if(server==null||done)return;try{
        ticks++;stageTicks++;
        if(ticks%100==0){var report=new JsonObject();report.addProperty("stage",stage);report.addProperty("ticks",ticks);report.addProperty("completed",completed);report.add("pool",state().get("arenaPool"));Files.writeString(ROOT.resolve("reuse-progress.json"),report.toString());}
        if(ticks>24000)throw new IllegalStateException("Bounded reuse QA deadline");
        var host=players.getFirst();var level=server.getLevel(HorseCourse.STADIUM_DIMENSION);
        if(stage==0){
            if(batch==1&&!staleChecked&&stageTicks>=20){var before=level.getForcedChunks().size();var generation=room(players.get(1)).get("arenaGeneration").getAsString();rooms.action(host,"leave","");check(level.getForcedChunks().size()==before&&room(players.get(1)).get("arenaGeneration").getAsString().equals(generation),"closing previous finished generation preserves new lease and tickets");start(host);staleChecked=true;}
            if(players.stream().anyMatch(p->!phase(p).equals("FINISHED")))return;
            for(int i=0;i<4;i++){
                var p=players.get(i);var r=room(p);usedSlots.add(r.get("arenaSlot").getAsInt());check(p.getVehicle()==sources.get(i)&&!HorseReturn.pending(p)&&sources.get(i).isAlive(),"natural finish restores identical source horse");
                for(var item:r.getAsJsonArray("results")){var result=item.getAsJsonObject();check(result.get("finished").getAsBoolean()&&!result.get("dnf").getAsBoolean()&&result.get("recoveries").getAsInt()==0,"native race completes all gates and obstacles without teleport recovery");}
                races.add(r.deepCopy());completed++;
            }
            clean();batch++;
            if(batch>=9){for(var p:players)rooms.action(p,"leave","");check(completed==36&&usedSlots.size()==4,"36 complete races reuse four bounded slots rather than extending allocation");start(host);rooms.action(host,"leave","");stage(10);return;}
            if(batch==1){for(int i=1;i<4;i++){rooms.action(players.get(i),"leave","");start(players.get(i));}stageTicks=0;}
            else {for(var p:players){rooms.action(p,"leave","");start(p);}}
            return;
        }
        if(stage==10){if(held()!=0)return;clean();check(host.getVehicle()==sources.getFirst(),"preparation cancellation keeps original mount");start(host);stage(11);return;}
        if(stage==11){if(!phase(host).equals("RUNNING"))return;net.neoforged.neoforge.common.NeoForge.EVENT_BUS.post(new net.neoforged.neoforge.event.entity.player.PlayerEvent.PlayerLoggedOutEvent(host));check(host.getVehicle()==sources.getFirst()&&!HorseReturn.pending(host)&&phase(host).equals("NONE"),"actual logout event restores source and closes running race");stage(12);return;}
        if(stage==12){if(held()!=0)return;clean();foreign=EntityType.COW.create(level);((net.minecraft.world.entity.Mob)foreign).setNoAi(true);((net.minecraft.world.entity.Mob)foreign).setPersistenceRequired();foreign.setPos(.5,65,.5);check(level.addFreshEntity(foreign),"foreign fixture admitted before vacancy test");level.setBlock(new BlockPos(4,65,4),Blocks.DIAMOND_BLOCK.defaultBlockState(),2);start(host);stage(13);return;}
        if(stage==13){if(stageTicks<110||!level.areEntitiesLoaded(net.minecraft.world.level.ChunkPos.asLong(0,0))||!level.isPositionEntityTicking(new BlockPos(0,65,0)))return;var loadedForeign=level.getEntity(foreign.getUUID());check(loadedForeign!=null&&loadedForeign.isAlive(),"foreign UUID reloads alive before vacancy assertion; old removal="+foreign.getRemovalReason());foreign=loadedForeign;check(phase(host).equals("PREPARING")&&foreign.isAlive()&&level.getBlockState(new BlockPos(4,65,4)).is(Blocks.DIAMOND_BLOCK),"unmarked foreign entity prevents scene mutation and transfer: phase="+phase(host)+", alive="+foreign.isAlive()+", removed="+foreign.isRemoved()+", sentinel="+level.getBlockState(new BlockPos(4,65,4)));UUID id=foreign.getUUID();foreign=foreign.changeDimension(new DimensionTransition(server.overworld(),new Vec3(30,65,.5),Vec3.ZERO,0,0,DimensionTransition.DO_NOTHING));check(foreign!=null&&foreign.getUUID().equals(id),"foreign asset moves without deletion or cloning");stage(14);return;}
        if(stage==14){if(!phase(host).equals("RUNNING"))return;rooms.action(host,"leave","");stage(15);return;}
        if(stage==15){if(held()!=0)return;clean();check(foreign.isAlive(),"foreign entity survives arena cleanup");intruder=fixture("ArenaIntruder");intruder.teleportTo(level,.5,81,-61.5,0,15);start(host);stage(16);return;}
        if(stage==16){if(stageTicks<110)return;check(phase(host).equals("PREPARING")&&intruder.level()==level,"unrelated online player blocks reuse and is never teleported");intruder.teleportTo(server.overworld(),32,65,.5,0,0);stage(17);return;}
        if(stage==17){if(!phase(host).equals("RUNNING"))return;returnTag=host.getPersistentData().getCompound(HorseReturn.KEY).copy();host.getPersistentData().getCompound(HorseReturn.KEY).putString("dimension","muxi_minigames:missing");boolean rejected=false;try{rooms.action(host,"leave","");}catch(IllegalStateException expected){rejected=true;}check(rejected&&HorseReturn.pending(host)&&held()==1,"failed return retains lease and recovery marker");start(players.get(1));check(room(host).get("arenaSlot").getAsInt()!=room(players.get(1)).get("arenaSlot").getAsInt(),"failed return site is not reused by another host");rooms.action(players.get(1),"leave","");host.getPersistentData().put(HorseReturn.KEY,returnTag);rooms.action(host,"leave","");stage(18);return;}
        if(stage==18){if(held()!=0)return;clean();var temp=server.getWorldPath(LevelResource.ROOT).resolve("data/muxi-horse-arena-leases-v1.json.tmp");Files.createDirectory(temp);rooms.action(host,"create","");boolean rejected=false;try{rooms.action(host,"start","");}catch(IllegalStateException expected){rejected=true;}check(rejected&&held()==0&&phase(host).equals("WAITING")&&host.getVehicle()==sources.getFirst(),"failed durable claim changes neither lease availability nor player asset");Files.delete(temp);rooms.action(host,"start","");stage(19);return;}
        if(stage==19){if(!phase(host).equals("RUNNING"))return;rooms.action(host,"leave","");Files.createDirectory(server.getWorldPath(LevelResource.ROOT).resolve("data/muxi-horse-arena-leases-v1.json.tmp"));stage(20);return;}
        if(stage==20){if(!releaseFaultSeen){if(!level.getForcedChunks().isEmpty())return;var path=server.getWorldPath(LevelResource.ROOT).resolve("data/muxi-horse-arena-leases-v1.json");check(held()==1&&new HorseArenaPool(server,path).snapshot().get("held").getAsInt()==1,"failed final release keeps durable claim after tickets are removed");Files.delete(path.resolveSibling(path.getFileName()+".tmp"));releaseFaultSeen=true;return;}if(held()!=0)return;clean();check(true,"release retry rechecks same generation and frees all owned tickets");start(host);stage(21);return;}
        if(stage==21){if(!phase(host).equals("RUNNING")||stageTicks<160)return;PlayerReturns.save(host);var seed=new JsonObject();seed.addProperty("source",sources.getFirst().getUUID().toString());seed.add("room",room(host));Files.writeString(ROOT.resolve("reuse-restart-seed.json"),seed.toString());check(HorseReturn.pending(host)&&held()==1,"active lease and player .dat seeded for separate JVM recovery");Field active=HorseRacing.class.getDeclaredField("ACTIVE");active.setAccessible(true);((Map<?,?>)active.get(null)).remove(server);finish(true,"");return;}
        if(stage==100){if(stageTicks<220)return;check(held()==1&&HorseReturn.pending(host),"offline or pending participant prevents automatic restart reuse");var seed=JsonParser.parseString(Files.readString(ROOT.resolve("reuse-restart-seed.json"))).getAsJsonObject();var source=(Horse)server.overworld().getEntity(UUID.fromString(seed.get("source").getAsString()));check(source!=null&&source.isAlive(),"original source reloads alive after actual JVM restart");sources.add(source);for(var entity:level.getAllEntities())if(!entity.isRemoved())check(!entity.getTags().contains(HorsePilot.MARKER),"saved orphan race actors are rejected during chunk reload");net.neoforged.neoforge.common.NeoForge.EVENT_BUS.post(new net.neoforged.neoforge.event.entity.player.PlayerEvent.PlayerLoggedInEvent(host));check(!HorseReturn.pending(host)&&host.getVehicle()==source,"actual login restores the durable snapshot before slot release");stage(101);return;}
        if(stage==101){if(held()!=0)return;clean();start(host);check(room(host).get("arenaSlot").getAsInt()==0,"restart recovery returns original bounded slot to pool");stage(102);return;}
        if(stage==102){if(!phase(host).equals("RUNNING"))return;rooms.action(host,"leave","");stage(103);return;}
        if(stage==103){if(held()!=0)return;clean();check(host.getVehicle()==sources.getFirst(),"post-restart reuse preserves the same source mount");finish(true,"");}
    }catch(Throwable f){f.printStackTrace();finish(false,f.toString());}}
    private static void finish(boolean success,String error){if(done)return;done=true;try{var report=new JsonObject();report.addProperty("success",success);report.addProperty("error",error);report.addProperty("completeNativeRaces",completed);report.addProperty("usedSlots",usedSlots.size());report.addProperty("resume",resume);report.add("checks",checks);report.add("races",races);report.add("pool",state().get("arenaPool"));Files.writeString(ROOT.resolve("result.json"),new GsonBuilder().setPrettyPrinting().create().toJson(report));}catch(Throwable f){f.printStackTrace();}server.halt(false);}
}
