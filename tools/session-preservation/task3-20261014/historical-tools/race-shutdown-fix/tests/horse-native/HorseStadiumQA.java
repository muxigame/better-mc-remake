package net.muxigame.minigames.horseqa;
import com.google.gson.*;
import com.mojang.authlib.GameProfile;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.network.Connection;
import net.minecraft.network.protocol.*;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.*;
import net.minecraft.server.network.*;
import net.minecraft.server.players.PlayerList;
import net.minecraft.world.entity.*;
import net.minecraft.world.entity.animal.horse.Horse;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.level.*;
import net.minecraft.world.phys.*;
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

/** Full-size scene and eight genuine vanilla physics runners in an owned, empty engine instance. */
@EventBusSubscriber(modid="muxi_horse_racing")
public final class HorseStadiumQA {
    private static MinecraftServer server;private static HorseRooms rooms;
    private static ServerPlayer host;private static Horse source;
    private static final List<ServerPlayer> players=new ArrayList<>();
    private static int ticks,runningTick;private static boolean done,inspected;
    private static long tickBegin;private static final List<Double> tickMillis=new ArrayList<>();
    private static final JsonArray checks=new JsonArray(),sight=new JsonArray();
    private static final JsonObject report=new JsonObject();
    private static void check(boolean value,String label){var row=new JsonObject();row.addProperty("passed",value);row.addProperty("label",label);checks.add(row);if(!value)throw new IllegalStateException(label);}
    @SuppressWarnings("unchecked") private static ServerPlayer fixture(String name)throws Exception{
        var p=new ServerPlayer(server,server.overworld(),new GameProfile(UUID.nameUUIDFromBytes(name.getBytes(java.nio.charset.StandardCharsets.UTF_8)),name),ClientInformation.createDefault());
        var connection=new Connection(PacketFlow.SERVERBOUND);Field ch=Connection.class.getDeclaredField("channel");ch.setAccessible(true);ch.set(connection,new io.netty.channel.embedded.EmbeddedChannel());
        p.connection=new ServerGamePacketListenerImpl(server,connection,p,CommonListenerCookie.createInitial(p.getGameProfile(),false)){
            public void send(Packet<?> packet){}public void send(Packet<?> packet,net.minecraft.network.PacketSendListener listener){}public java.net.SocketAddress getRemoteAddress(){return new InetSocketAddress("127.0.0.1",49112);}
        };
        Field list=PlayerList.class.getDeclaredField("players");list.setAccessible(true);((List<ServerPlayer>)list.get(server.getPlayerList())).add(p);
        Field map=PlayerList.class.getDeclaredField("playersByUUID");map.setAccessible(true);((Map<UUID,ServerPlayer>)map.get(server.getPlayerList())).put(p.getUUID(),p);
        p.setPos(.5,65,.5);p.setGameMode(GameType.SURVIVAL);server.overworld().addNewPlayer(p);players.add(p);return p;
    }
    @SubscribeEvent(priority=EventPriority.LOWEST) @SuppressWarnings("unchecked") public static void started(ServerStartedEvent e){
        if(!Boolean.getBoolean("muxi.horse.stadiumQa"))return;server=e.getServer();
        try{
            check(Files.exists(Path.of("HORSE_QA_ONLY"))&&server.getPlayerList().getPlayers().isEmpty(),"owned empty world; no real player horses");
            check(System.getProperty("muxi.horse.prototype")==null,"automatic formal module without opt-in flag");
            host=fixture("StadiumHost");source=EntityType.HORSE.create(server.overworld());source.setTamed(true);source.setAge(0);source.setNoAi(true);source.setPersistenceRequired();source.setPos(.5,65,.5);source.getAttribute(Attributes.MOVEMENT_SPEED).setBaseValue(.26);source.getAttribute(Attributes.JUMP_STRENGTH).setBaseValue(.9);server.overworld().addFreshEntity(source);host.startRiding(source,true);
            Field active=HorseRacing.class.getDeclaredField("ACTIVE");active.setAccessible(true);rooms=((Map<MinecraftServer,HorseRooms>)active.get(null)).get(server);
            rooms.action(host,"create","");var team=GameRuntime.get(server).memberships.owner(host.getUUID()).team();
            check(rooms.roomWaiting(team)&&host.getVehicle()==source&&!HorseReturn.pending(host),"default oval creation waits without touching source mount");
            for(int i=0;i<3;i++){ServerPlayer guest=fixture("StadiumGuest"+i);rooms.action(host,"invite",guest.getUUID().toString());rooms.action(guest,"join",team.shortId());}
            rooms.action(host,"enemy","5");rooms.action(host,"start","");
            check(room().get("phase").getAsString().equals("PREPARING")&&host.getVehicle()==source&&!HorseReturn.pending(host),"host start builds asynchronously before any player transfer");
        }catch(Throwable failure){finish(false,failure.toString());}
    }
    private static JsonObject room(){return rooms.snapshot(host).getAsJsonArray("rooms").get(0).getAsJsonObject();}
    @SubscribeEvent(priority=EventPriority.HIGHEST) public static void pre(ServerTickEvent.Pre e){if(server!=null&&!done)tickBegin=System.nanoTime();}
    @SubscribeEvent(priority=EventPriority.LOWEST) public static void tick(ServerTickEvent.Post e){if(server==null||done)return;try{
        ticks++;tickMillis.add((System.nanoTime()-tickBegin)/1e6);var row=room();String phase=row.get("phase").getAsString();
        if(ticks%20==0)Files.writeString(Path.of("stadium-progress.json"),row.toString());
        if(phase.equals("BUILD_FAILED"))throw new IllegalStateException("Scene failed to build");
        if(phase.equals("RUNNING")){
            if(!inspected){inspected=true;runningTick=ticks;report.add("build",row.deepCopy());
                var level=server.getLevel(HorseCourse.STADIUM_DIMENSION);Vec3 eye=HorseStadium.podium(0,0).add(0,host.getEyeHeight(),0);
                check(host.level()==level&&host.gameMode.getGameModeForPlayer()==GameType.SPECTATOR&&host.position().distanceTo(HorseStadium.podium(0,0))<.01,"default spectator occupies presidential podium exactly");
                check(source.level()==server.overworld()&&source.isAlive(),"actual source horse remains in original world");
                int clear=0,plants=0;
                for(int i=0;i<192;i++){double a=-Math.PI/2+Math.PI*2*i/192;Vec3 target=HorseStadium.point(a,0).add(0,1.2,0);var hit=level.clip(new ClipContext(eye,target,ClipContext.Block.VISUAL,ClipContext.Fluid.NONE,host));
                    var test=new JsonObject();test.addProperty("sample",i);test.addProperty("clear",hit.getType()==HitResult.Type.MISS);
                    if(hit.getType()!=HitResult.Type.MISS){var block=BuiltInRegistries.BLOCK.getKey(level.getBlockState(hit.getBlockPos()).getBlock()).toString();test.addProperty("block",block);test.addProperty("at",hit.getBlockPos().toShortString());if(block.contains("leaves")||block.equals("minecraft:oak_log")||block.contains("azalea"))plants++;}else clear++;sight.add(test);
                }
                report.addProperty("clearSightSamples",clear);report.addProperty("plantOcclusions",plants);check(plants==0,"grouped gardens and groves do not occlude race from podium");check(clear>=182,"at least 95 percent of track body samples visible from podium");
                check(row.getAsJsonArray("results").size()==8,"four invited racers and four NPCs generated in an eight-horse grid");
            }
            if(ticks%5==0){var level=server.getLevel(HorseCourse.STADIUM_DIMENSION);JsonArray poses=new JsonArray();for(Entity entity:level.getAllEntities())if(entity instanceof Horse h&&h.getTags().contains(HorsePilot.MARKER)){var pose=new JsonObject();pose.addProperty("uuid",h.getUUID().toString());pose.addProperty("x",h.getX());pose.addProperty("y",h.getY());pose.addProperty("z",h.getZ());poses.add(pose);}var trace=new JsonObject();trace.addProperty("tick",ticks-runningTick);trace.add("horses",poses);Files.writeString(Path.of("stadium-motion.jsonl"),trace+"\n",StandardOpenOption.CREATE,StandardOpenOption.APPEND);}
        }
        if(phase.equals("FINISHED")){
            for(var result:row.getAsJsonArray("results")){var r=result.getAsJsonObject();check(r.get("finished").getAsBoolean()&&!r.get("dnf").getAsBoolean(),r.get("name").getAsString()+": every oval gate reached by native physics");check(r.get("recoveries").getAsInt()==0,r.get("name").getAsString()+": no teleport recovery through real obstacles");}
            check(row.getAsJsonArray("results").asList().stream().filter(v->v.getAsJsonObject().get("score").getAsInt()==10).count()==1,"exactly one champion gets ten, every entrant scored");check(row.getAsJsonArray("results").asList().stream().allMatch(v->v.getAsJsonObject().get("score").getAsInt()>=1&&v.getAsJsonObject().get("score").getAsInt()<=10),"all eight ranks have bounded participation score");
            check(players.stream().allMatch(p->p.level()==server.overworld()&&!HorseReturn.pending(p)&&p.gameMode.getGameModeForPlayer()==GameType.SURVIVAL),"all spectators return to original dimension and mode");check(host.getVehicle()==source&&source.isAlive(),"same original mount restored without cloning");
            check(rooms.snapshot(host).getAsJsonArray("top10").size()==8,"history records four player horse names and four NPCs by oval version");report.add("race",row.deepCopy());rooms.action(host,"leave","");check(players.stream().allMatch(p->GameRuntime.get(server).memberships.owner(p.getUUID())==null),"host exit releases all room memberships");finish(true,"");
        }
        if(ticks>6000)throw new IllegalStateException("Bounded stadium deadline exceeded");
    }catch(Throwable failure){failure.printStackTrace();finish(false,failure.toString());}}
    private static void finish(boolean pass,String error){if(done)return;done=true;try{report.addProperty("success",pass);report.addProperty("error",error);report.add("checks",checks);report.add("sight",sight);Collections.sort(tickMillis);if(!tickMillis.isEmpty()){report.addProperty("serverTickP95Millis",tickMillis.get((int)(tickMillis.size()*.95)));report.addProperty("serverTickP99Millis",tickMillis.get((int)(tickMillis.size()*.99)));report.addProperty("serverTickMaxMillis",tickMillis.getLast());}Files.writeString(Path.of("result.json"),new GsonBuilder().setPrettyPrinting().create().toJson(report));}catch(Throwable f){f.printStackTrace();}server.halt(false);}
}
