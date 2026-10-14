package net.muxigame.minigames.horseqa;

import com.google.gson.*;
import com.mojang.authlib.GameProfile;
import net.minecraft.core.BlockPos;
import net.minecraft.network.Connection;
import net.minecraft.network.protocol.*;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.*;
import net.minecraft.server.network.*;
import net.minecraft.server.players.PlayerList;
import net.minecraft.world.entity.*;
import net.minecraft.world.entity.animal.horse.Horse;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.item.*;
import net.minecraft.world.level.*;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.portal.DimensionTransition;
import net.minecraft.world.phys.Vec3;
import net.muxigame.minigames.*;
import net.muxigame.minigames.horse.*;
import net.neoforged.bus.api.*;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.event.server.ServerStartedEvent;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import java.lang.reflect.*;
import java.net.InetSocketAddress;
import java.nio.file.*;
import java.util.*;

/** Included in an existing minigames jar ONLY by this private runner. No added mod ID. */
@EventBusSubscriber(modid="muxi_horse_racing")
public final class HorseNativeQA {
    private static MinecraftServer server;
    private static HorsePilot pilot;
    private static HorseCourse course;
    private static HorseRooms rooms;
    private static ServerPlayer host,guest;
    private static Horse source;
    private static int phase,tick;
    private static boolean done;
    private static boolean restartSeed,secondStarting,initialEntered;
    private static Path cwd;
    private static final JsonArray checks=new JsonArray();
    private static final JsonArray runs=new JsonArray();
    private static final Gson GSON=new GsonBuilder().setPrettyPrinting().create();
    private static void check(boolean pass,String label){JsonObject row=new JsonObject();row.addProperty("label",label);row.addProperty("passed",pass);checks.add(row);if(!pass)throw new IllegalStateException(label);}
    @SubscribeEvent(priority=EventPriority.LOWEST)
    public static void started(ServerStartedEvent event){
        if(!Boolean.getBoolean("muxi.horse.nativeQa"))return;
        server=event.getServer();cwd=Path.of("").toAbsolutePath();
        try{
            if(!Files.exists(cwd.resolve("HORSE_QA_ONLY"))||!cwd.getFileName().toString().startsWith("horse-native-"))throw new IllegalStateException("Own private lab required");
            check(server.getPlayerList().getPlayers().isEmpty(),"isolated server starts with no real players");
            if(Boolean.getBoolean("muxi.horse.disabledQa")){
                check(server.getCommands().getDispatcher().getRoot().getChild("horseproto")==null,"prototype disabled: no race command registered");
                Field active=HorseRacing.class.getDeclaredField("ACTIVE");active.setAccessible(true);check(((Map<?,?>)active.get(null)).isEmpty(),"prototype disabled: no horse rooms registered");
                var ordinary=EntityType.HORSE.create(server.overworld());check(ordinary!=null&&!(ordinary.getMoveControl() instanceof HorseMoveControl),"ordinary vanilla Horse retains vanilla controller");
                boolean absent=false;try{Class.forName("com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid");}catch(ClassNotFoundException expected){absent=true;}check(absent,"default-off minigames boots with TLM/YSM absent");finish(true,"");return;
            }
            if(Boolean.getBoolean("muxi.horse.resumeQa")){
                host=fixture("HorseHostQA");server.getPlayerList().load(host);
                check(HorseReturn.pending(host),"actual player .dat retains spectator return marker after JVM restart");
                // Runtime chunk tickets need not persist. Explicitly reload the saved orphan chunk, then wait for entity IO.
                server.getLevel(HorseCourse.DIMENSION).setChunkForced(50,0,true);phase=3;return;
            }
            JsonObject suggestions=new JsonObject();for(String input:List.of("ysm ","ysm model ","ysm model select ","ysm model set ","ysm model set @s ")){
                var dispatcher=server.getCommands().getDispatcher();suggestions.add(input,new Gson().toJsonTree(dispatcher.getCompletionSuggestions(dispatcher.parse(input,server.createCommandSourceStack())).join().getList().stream().map(s->s.getText()).toList()));
            }Files.writeString(cwd.resolve("ysm-suggestions.json"),GSON.toJson(suggestions));
            course=new HorseCourse(0,0);course.build(server.getLevel(HorseCourse.DIMENSION));
            pilot=new HorsePilot(server.getLevel(HorseCourse.DIMENSION),course,new HorseIdentity(UUID.randomUUID(),"QA原版马",.25,.9,26,false),"qa-model-sync-only","default");
            check(pilot.horse.getType()==EntityType.HORSE&&pilot.rider.getVehicle()==pilot.horse,"actual vanilla Horse with installed TLM maid passenger");
            check((boolean)pilot.rider.getClass().getMethod("isYsmModel").invoke(pilot.rider),"installed TLM YSM branch enabled; rendering not asserted");
        }catch(Throwable failure){finish(false,failure.toString());}
    }
    @SubscribeEvent(priority=EventPriority.LOWEST)
    public static void tick(ServerTickEvent.Post event){
        if(!Boolean.getBoolean("muxi.horse.nativeQa")||server==null||done)return;
        try{
            tick++;
            if(phase<2){
                if(phase==1&&pilot.ticks>=5&&pilot.ticks<105){
                    // Cage the generated test mount; recovery must return only to its last validated gate.
                    BlockPos p=BlockPos.containing(pilot.horse.position());
                    for(int x=p.getX()-1;x<=p.getX()+1;x++)for(int z=p.getZ()-1;z<=p.getZ()+1;z++)for(int y=65;y<=68;y++)
                        if(x!=p.getX()||z!=p.getZ())pilot.horse.level().setBlock(new BlockPos(x,y,z),Blocks.STONE.defaultBlockState(),3);
                }
                if(phase==1&&pilot.ticks==110)course.build(server.getLevel(HorseCourse.DIMENSION));
                if(phase==0&&pilot.ticks==20)pilot.rider.stopRiding();
                pilot.tick();
                JsonObject trace=new JsonObject();trace.addProperty("case",phase);trace.addProperty("tick",pilot.ticks);trace.addProperty("gate",pilot.gate);trace.addProperty("x",pilot.horse.getX());trace.addProperty("y",pilot.horse.getY());trace.addProperty("z",pilot.horse.getZ());trace.addProperty("vx",pilot.horse.getDeltaMovement().x);trace.addProperty("vy",pilot.horse.getDeltaMovement().y);trace.addProperty("vz",pilot.horse.getDeltaMovement().z);trace.addProperty("jump",pilot.jumps);trace.addProperty("recover",pilot.recoveries);trace.addProperty("ground",pilot.horse.onGround());
                Files.writeString(cwd.resolve("motion.jsonl"),trace.toString()+"\n",StandardOpenOption.CREATE,StandardOpenOption.APPEND);
                if(tick%20==0)progress();
                if(pilot.finished||pilot.dnf){
                    JsonObject run=new JsonObject();run.addProperty("case",phase==0?"baseline":"injected-cage");run.addProperty("finished",pilot.finished);run.addProperty("dnf",pilot.dnf);run.addProperty("ticks",pilot.ticks);run.addProperty("gates",pilot.gate);run.addProperty("jumps",pilot.jumps);run.addProperty("recoveries",pilot.recoveries);run.addProperty("remounts",pilot.remounts);runs.add(run);
                    check(pilot.finished&&!pilot.dnf,"real course completes: "+run);
                    if(phase==0){check(pilot.recoveries==0,"baseline fence and water ditch crossed without teleport recovery");check(pilot.jumps>=2&&pilot.gate==course.gates.size(),"all turn/checkpoint gates visited and jumps executed");check(pilot.remounts==1,"detached maid remounts the same horse");}
                    else check(pilot.recoveries>0&&pilot.penaltyTicks==pilot.recoveries*80,"cage recovery is bounded and time penalty counted");
                    pilot.close();check(pilot.horse.isRemoved()&&pilot.rider.isRemoved(),"only generated prototype pair cleaned up");
                    if(phase++==0){course=new HorseCourse(80,0);course.build(server.getLevel(HorseCourse.DIMENSION));pilot=new HorsePilot(server.getLevel(HorseCourse.DIMENSION),course,new HorseIdentity(UUID.randomUUID(),"QA恢复马",.25,.9,26,false),"","");}
                    else setupRooms();
                }
            }else if(phase==2){
                if(tick%20==0)progress();
                var snapshot=rooms.snapshot(host);var room=snapshot.getAsJsonArray("rooms").get(0).getAsJsonObject();
                if(room.get("phase").getAsString().equals("RUNNING")&&!initialEntered){initialEntered=true;check(host.level().dimension().equals(HorseCourse.DIMENSION)&&HorseReturn.pending(host)&&source.level()==server.overworld(),"verified site starts with durable snapshot and original source at home");}
                if(secondStarting&&room.get("phase").getAsString().equals("RUNNING")){abnormalAndRestartSeed();return;}
                if(room.get("phase").getAsString().equals("FINISHED")){
                    check(room.getAsJsonArray("results").size()==3&&room.getAsJsonArray("results").asList().stream().allMatch(v->v.getAsJsonObject().get("finished").getAsBoolean()),"two player horses and NPC have real completed results");
                    check(!HorseReturn.pending(host)&&host.level()==server.overworld()&&host.getVehicle()==source,"normal finish restores host dimension, mode and original mounted horse");
                    check(source.getUUID().equals(HorseIdentity.read(source).horseId())&&source.getName().getString().equals("QA自养马")&&source.getAttributeValue(Attributes.MOVEMENT_SPEED)==.26,"source horse remains alive with name and attributes preserved");
                    check(host.getInventory().getItem(0).getCount()==7,"spectator race does not edit inventory");
                    check(snapshot.getAsJsonArray("top10").size()==3,"history contains player and NPC horse names");
                    rooms.action(host,"leave","");rooms.action(host,"create","");rooms.action(host,"start","");
                    secondStarting=true;
                }
            }else if(phase==3&&tick>=40){
                var lab=server.getLevel(HorseCourse.DIMENSION);lab.getChunk(50,0);var orphanIds=JsonParser.parseString(Files.readString(cwd.resolve("orphan-ids.json"))).getAsJsonArray();
                JsonObject orphanStatus=new JsonObject();orphanStatus.addProperty("forced",lab.getForcedChunks().contains(net.minecraft.world.level.ChunkPos.asLong(50,0)));orphanStatus.add("forcedChunks",new Gson().toJsonTree(lab.getForcedChunks().toLongArray()));JsonArray orphanRows=new JsonArray();for(var id:orphanIds){var entity=lab.getEntity(UUID.fromString(id.getAsString()));JsonObject row=new JsonObject();row.addProperty("id",id.getAsString());row.addProperty("present",entity!=null);if(entity!=null){row.addProperty("removed",entity.isRemoved());row.add("tags",new Gson().toJsonTree(entity.getTags()));}orphanRows.add(row);}orphanStatus.add("entities",orphanRows);Files.writeString(cwd.resolve("orphan-status.json"),GSON.toJson(orphanStatus));
                check(lab.getForcedChunks().contains(net.minecraft.world.level.ChunkPos.asLong(50,0))&&lab.getChunkSource().hasChunk(50,0),"saved orphan test chunk explicitly reloaded before entity checks: "+orphanStatus);
                check(orphanIds.asList().stream().allMatch(v->lab.getEntity(UUID.fromString(v.getAsString()))==null),"loaded private race chunks do not resurrect orphan generated horse or maid after restart: "+orphanStatus);
                var marker=host.getPersistentData().getCompound(HorseReturn.KEY);UUID id=marker.getUUID("vehicle");source=(Horse)server.overworld().getEntity(id);
                check(source!=null&&source.isAlive(),"original synthetic source horse reloads alive after actual JVM restart");
                net.neoforged.neoforge.common.NeoForge.EVENT_BUS.post(new net.neoforged.neoforge.event.entity.player.PlayerEvent.PlayerLoggedInEvent(host));
                check(!HorseReturn.pending(host)&&host.level()==server.overworld()&&host.getVehicle()==source&&host.gameMode.getGameModeForPlayer()==GameType.SURVIVAL,"real login event restores persisted location/mode and same source mount after restart");
                long count=0;for(var level:server.getAllLevels())for(var entity:level.getAllEntities())if(entity.getUUID().equals(id)&&!entity.isRemoved())count++;
                check(count==1&&host.getInventory().getItem(0).getCount()==7,"restart recovery creates no duplicate source horse and preserves inventory");
                boolean refused=false;HorseReturn.capture(host);var saved=host.getPersistentData().getCompound(HorseReturn.KEY).copy();host.getPersistentData().getCompound(HorseReturn.KEY).putString("dimension","muxi_minigames:unavailable");try{HorseReturn.restore(host);}catch(IllegalStateException expected){refused=true;}
                check(refused&&HorseReturn.pending(host)&&source.isAlive(),"unavailable return dimension keeps durable recovery marker and source asset");host.getPersistentData().put(HorseReturn.KEY,saved);HorseReturn.restore(host);finish(true,"");
            }
            if(tick>6000)throw new IllegalStateException("Native QA bounded deadline exceeded");
        }catch(Throwable failure){failure.printStackTrace();finish(false,failure.toString());}
    }
    @SuppressWarnings("unchecked") private static ServerPlayer fixture(String name)throws Exception{
        var p=new ServerPlayer(server,server.overworld(),new GameProfile(UUID.nameUUIDFromBytes(name.getBytes(java.nio.charset.StandardCharsets.UTF_8)),name),ClientInformation.createDefault());
        var connection=new Connection(PacketFlow.SERVERBOUND);Field ch=Connection.class.getDeclaredField("channel");ch.setAccessible(true);ch.set(connection,new io.netty.channel.embedded.EmbeddedChannel());
        p.connection=new ServerGamePacketListenerImpl(server,connection,p,CommonListenerCookie.createInitial(p.getGameProfile(),false)){
            public void send(Packet<?> packet){}
            public void send(Packet<?> packet,net.minecraft.network.PacketSendListener listener){}
            public java.net.SocketAddress getRemoteAddress(){return new InetSocketAddress("127.0.0.1",49111);}
        };
        Field players=PlayerList.class.getDeclaredField("players");players.setAccessible(true);((List<ServerPlayer>)players.get(server.getPlayerList())).add(p);
        Field map=PlayerList.class.getDeclaredField("playersByUUID");map.setAccessible(true);((Map<UUID,ServerPlayer>)map.get(server.getPlayerList())).put(p.getUUID(),p);
        p.setPos(.5,65,.5);p.setGameMode(GameType.SURVIVAL);server.overworld().addNewPlayer(p);return p;
    }
    @SuppressWarnings("unchecked") private static void setupRooms()throws Exception{
        host=fixture("HorseHostQA");guest=fixture("HorseGuestQA");host.getInventory().setItem(0,new ItemStack(Items.DIAMOND,7));
        source=EntityType.HORSE.create(server.overworld());source.setTamed(true);source.setAge(0);source.setNoAi(true);source.setPersistenceRequired();source.setCustomName(net.minecraft.network.chat.Component.literal("QA自养马"));source.getAttribute(Attributes.MOVEMENT_SPEED).setBaseValue(.26);source.getAttribute(Attributes.JUMP_STRENGTH).setBaseValue(.9);source.setPos(.5,65,.5);server.overworld().addFreshEntity(source);
        UUID original=source.getUUID();source=(Horse)source.changeDimension(new DimensionTransition(server.getLevel(HorseCourse.DIMENSION),new Vec3(240.5,65,.5),Vec3.ZERO,0,0,DimensionTransition.DO_NOTHING));
        check(source!=null&&source.getUUID().equals(original),"synthetic horse dimension transfer retains stable UUID");
        source=(Horse)source.changeDimension(new DimensionTransition(server.overworld(),new Vec3(.5,65,.5),Vec3.ZERO,0,0,DimensionTransition.DO_NOTHING));
        long count=0;for(var level:server.getAllLevels())for(var entity:level.getAllEntities())if(entity.getUUID().equals(original))count++;
        check(source!=null&&source.getUUID().equals(original)&&count==1,"synthetic horse roundtrip exists exactly once across dimensions");
        host.startRiding(source,true);Field active=HorseRacing.class.getDeclaredField("ACTIVE");active.setAccessible(true);rooms=((Map<MinecraftServer,HorseRooms>)active.get(null)).get(server);
        rooms.action(host,"create","");var team=GameRuntime.get(server).memberships.owner(host.getUUID()).team();
        check(rooms.roomWaiting(team)&&host.getVehicle()==source&&!HorseReturn.pending(host),"create is waiting-only and reads mounted horse without transferring source");
        boolean denied=false;try{rooms.action(guest,"join",team.shortId());}catch(IllegalArgumentException expected){denied=true;}check(denied,"room guessing cannot join without invitation");
        rooms.action(host,"invite",guest.getUUID().toString());rooms.action(guest,"join",team.shortId());check(team.members.size()==2&&host.getVehicle()==source,"invited join remains in lobby and source horse unchanged");
        rooms.action(host,"start","");check(rooms.snapshot(host).getAsJsonArray("rooms").get(0).getAsJsonObject().get("phase").getAsString().equals("PREPARING")&&!HorseReturn.pending(host)&&host.getVehicle()==source,"start preflight keeps original mount until site is verified");
    }
    private static void abnormalAndRestartSeed()throws Exception{
                    check(HorseReturn.pending(host),"spectator snapshot saved before second race transfer");
                    net.neoforged.neoforge.common.NeoForge.EVENT_BUS.post(new net.neoforged.neoforge.event.entity.player.PlayerEvent.PlayerLoggedOutEvent(host));
                    check(!HorseReturn.pending(host)&&host.getVehicle()==source&&GameRuntime.get(server).memberships.owner(host.getUUID())==null,"abnormal leave restores same source mount and releases room");
                    HorseReturn.capture(host);host.stopRiding();host.teleportTo(server.getLevel(HorseCourse.DIMENSION),.5,72,.5,0,0);host.setGameMode(GameType.SPECTATOR);
                    var saved=host.getPersistentData().getCompound(HorseReturn.KEY).copy();host.getPersistentData().remove(HorseReturn.KEY);host.getPersistentData().put(HorseReturn.KEY,saved);HorseReturn.restore(host);
                    check(host.getVehicle()==source&&!HorseReturn.pending(host)&&host.gameMode.getGameModeForPlayer()==GameType.SURVIVAL,"persisted spectator marker restores across reconstructed return state");
                    historyCases();
                    HorseReturn.capture(host);host.stopRiding();host.teleportTo(server.getLevel(HorseCourse.DIMENSION),.5,72,.5,0,0);host.setGameMode(GameType.SPECTATOR);PlayerReturns.save(host);restartSeed=true;
                    var orphanCourse=new HorseCourse(800,0);orphanCourse.build(server.getLevel(HorseCourse.DIMENSION));var orphan=new HorsePilot(server.getLevel(HorseCourse.DIMENSION),orphanCourse,new HorseIdentity(UUID.randomUUID(),"QA遗留马",.25,.9,26,false),"","");orphan.horse.setNoAi(true);
                    Files.writeString(cwd.resolve("orphan-ids.json"),new Gson().toJson(List.of(orphan.horse.getUUID().toString(),orphan.rider.getUUID().toString())));check(orphan.horse.isAlive()&&orphan.rider.isAlive(),"orphan generated horse and maid deliberately persisted for restart cleanup test");
                    check(HorseReturn.pending(host),"durable .dat spectator snapshot seeded for separate JVM recovery test");finish(true,"");
    }
    private static void historyCases(){
        Path path=cwd.resolve("history-qa.json");HorseHistory history=new HorseHistory(path);UUID horse=UUID.randomUUID(),session=UUID.randomUUID();
        var result=new HorseHistory.Result(session,horse,"旧马名","qa","1",100,0,false);history.record(result);history.record(result);
        history.record(new HorseHistory.Result(UUID.randomUUID(),horse,"新马名","qa","1",90,0,false));history.record(new HorseHistory.Result(UUID.randomUUID(),horse,"新马名","qa","2",80,0,false));
        history=new HorseHistory(path);check(history.top("qa","1").size()==1&&history.top("qa","1").get(0).horseName().equals("新马名")&&history.top("qa","2").size()==1,"durable results deduplicate UUID across rename and isolate course versions");
        for(int i=0;i<15;i++)history.record(new HorseHistory.Result(UUID.randomUUID(),UUID.randomUUID(),"QA-"+i,"qa","1",200+i,0,true));
        check(history.top("qa","1").size()==10&&history.top("qa","1").get(0).ticks()==90,"top ten sorted by best server time includes named NPC horses");
    }
    private static void progress()throws Exception{
        JsonObject p=new JsonObject();p.addProperty("phase",phase);p.addProperty("serverTicks",tick);
        if(pilot!=null){p.addProperty("gate",pilot.gate);p.addProperty("horseTicks",pilot.ticks);p.addProperty("x",pilot.horse.getX());p.addProperty("y",pilot.horse.getY());p.addProperty("z",pilot.horse.getZ());p.addProperty("jumps",pilot.jumps);p.addProperty("recoveries",pilot.recoveries);p.addProperty("onGround",pilot.horse.onGround());}
        if(rooms!=null)p.add("rooms",rooms.snapshot(host));Files.writeString(cwd.resolve("progress.json"),GSON.toJson(p));
    }
    @SuppressWarnings("unchecked") private static void finish(boolean pass,String error){
        if(done)return;done=true;try{
            if(pilot!=null)pilot.close();if(rooms!=null)rooms.close();
            for(var p:new ServerPlayer[]{host,guest})if(p!=null){if(!restartSeed||p!=host)HorseReturn.restore(p);Field list=PlayerList.class.getDeclaredField("players");list.setAccessible(true);((List<ServerPlayer>)list.get(server.getPlayerList())).remove(p);Field map=PlayerList.class.getDeclaredField("playersByUUID");map.setAccessible(true);((Map<UUID,ServerPlayer>)map.get(server.getPlayerList())).remove(p.getUUID());p.discard();}
            if(source!=null&&!restartSeed)source.discard();JsonObject out=new JsonObject();out.addProperty("success",pass);out.addProperty("error",error);out.addProperty("kind","131 actual Minecraft 1.21.1 / NeoForge / installed TLM + YSM, synthetic ServerPlayer connections; no real network-client or render acceptance");out.add("checks",checks);out.add("runs",runs);out.addProperty("normalStopRequested",true);Files.writeString(cwd.resolve("result.json"),GSON.toJson(out));
        }catch(Exception failure){failure.printStackTrace();}if(server!=null)server.halt(false);
    }
}
