package net.muxigame.minigames.horseqa;
import com.google.gson.*;
import com.mojang.authlib.GameProfile;
import net.minecraft.network.Connection;
import net.minecraft.network.protocol.*;
import net.minecraft.network.protocol.game.ServerboundAcceptTeleportationPacket;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.*;
import net.minecraft.server.network.*;
import net.minecraft.server.players.PlayerList;
import net.minecraft.world.entity.*;
import net.minecraft.world.entity.animal.horse.Horse;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.level.*;
import net.minecraft.world.phys.Vec3;
import net.muxigame.minigames.*;
import net.muxigame.minigames.horse.*;
import net.neoforged.bus.api.*;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.event.entity.EntityMountEvent;
import net.neoforged.neoforge.event.server.ServerStartedEvent;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import java.lang.reflect.*;
import java.net.InetSocketAddress;
import java.nio.file.*;
import java.util.*;

/** Native engine regression with explicitly synthetic ServerPlayers/teleport acknowledgements. */
@EventBusSubscriber(modid="muxi_horse_racing")
public final class HorseStartQA {
 static MinecraftServer server;static HorseRooms rooms;static final List<ServerPlayer> players=new ArrayList<>();
 static Horse source;static Mob detached;static int ticks,stage,caseNumber,entryTick,countdownTick,runningTick,blockedUntil,allFinishedTick;static boolean done,detachmentTested;
 static final JsonArray checks=new JsonArray(),events=new JsonArray(),races=new JsonArray();
 static void check(boolean pass,String label){var row=new JsonObject();row.addProperty("passed",pass);row.addProperty("label",label);checks.add(row);if(!pass)throw new IllegalStateException(label);}
 @SuppressWarnings("unchecked") static ServerPlayer fixture(String name)throws Exception {
  var p=new ServerPlayer(server,server.overworld(),new GameProfile(UUID.nameUUIDFromBytes(name.getBytes(java.nio.charset.StandardCharsets.UTF_8)),name),ClientInformation.createDefault());
  var connection=new Connection(PacketFlow.SERVERBOUND);var field=Connection.class.getDeclaredField("channel");field.setAccessible(true);field.set(connection,new io.netty.channel.embedded.EmbeddedChannel());
  p.connection=new ServerGamePacketListenerImpl(server,connection,p,CommonListenerCookie.createInitial(p.getGameProfile(),false)){
   public void send(Packet<?> p){}public void send(Packet<?> p,net.minecraft.network.PacketSendListener l){}public java.net.SocketAddress getRemoteAddress(){return new InetSocketAddress("127.0.0.1",49115);}
  };
  var list=PlayerList.class.getDeclaredField("players");list.setAccessible(true);((List<ServerPlayer>)list.get(server.getPlayerList())).add(p);
  var map=PlayerList.class.getDeclaredField("playersByUUID");map.setAccessible(true);((Map<UUID,ServerPlayer>)map.get(server.getPlayerList())).put(p.getUUID(),p);
  p.setPos(.5+players.size()*5,65,.5);p.setGameMode(GameType.SURVIVAL);server.overworld().addNewPlayer(p);players.add(p);return p;
 }
 static JsonObject room(){return rooms.snapshot(players.getFirst()).getAsJsonArray("rooms").get(0).getAsJsonObject();}
 static void startCase(int count){rooms.action(players.getFirst(),"create","");var team=GameRuntime.get(server).memberships.owner(players.getFirst().getUUID()).team();for(int i=1;i<count;i++){rooms.action(players.getFirst(),"invite",players.get(i).getUUID().toString());rooms.action(players.get(i),"join",team.shortId());}rooms.action(players.getFirst(),"enemy","3");rooms.action(players.getFirst(),"start","");try{rooms.action(players.getFirst(),"start","");throw new IllegalStateException("duplicate start accepted");}catch(IllegalStateException expected){check(!expected.getMessage().equals("duplicate start accepted"),"duplicate start rejected before entry");}stage=0;entryTick=countdownTick=runningTick=0;detachmentTested=false;detached=null;allFinishedTick=0;}
 static List<Horse> horses(){var out=new ArrayList<Horse>();for(var e:server.getLevel(HorseCourse.STADIUM_DIMENSION).getAllEntities())if(e instanceof Horse h&&h.getTags().contains(HorsePilot.MARKER)&&h.isAlive())out.add(h);return out;}
 static void ack(ServerPlayer p)throws Exception {var f=ServerGamePacketListenerImpl.class.getDeclaredField("awaitingTeleport");f.setAccessible(true);p.connection.handleAcceptTeleportPacket(new ServerboundAcceptTeleportationPacket(f.getInt(p.connection)));}
 @SubscribeEvent(priority=EventPriority.HIGHEST) public static void mount(EntityMountEvent event){if(server!=null&&event.isMounting()&&event.getEntity()==detached&&server.getTickCount()<blockedUntil)event.setCanceled(true);}
 @SubscribeEvent(priority=EventPriority.LOWEST) @SuppressWarnings("unchecked") public static void started(ServerStartedEvent e){if(!Boolean.getBoolean("muxi.horse.startQa"))return;server=e.getServer();try {
  check(Files.exists(Path.of("HORSE_QA_ONLY"))&&server.getPlayerList().getPlayers().isEmpty(),"private empty generated fixture world");
  for(int i=0;i<4;i++)fixture("HorseStart"+i);
  source=EntityType.HORSE.create(server.overworld());source.setTamed(true);source.setAge(0);source.setNoAi(true);source.setPersistenceRequired();source.setPos(.5,65,.5);source.getAttribute(Attributes.MOVEMENT_SPEED).setBaseValue(.36);source.getAttribute(Attributes.JUMP_STRENGTH).setBaseValue(1);source.setCustomName(net.minecraft.network.chat.Component.literal("Start source"));server.overworld().addFreshEntity(source);players.getFirst().startRiding(source,true);
  var f=HorseRacing.class.getDeclaredField("ACTIVE");f.setAccessible(true);rooms=((Map<MinecraftServer,HorseRooms>)f.get(null)).get(server);startCase(1);
 }catch(Throwable error){finish(false,error.toString());}}
 static void stableGrid(JsonObject row)throws Exception {
  check(row.getAsJsonArray("results").size()==8&&horses().size()==8,"eight actual vanilla horses fill formal field");check(row.get("npcCount").getAsInt()==(caseNumber==1?4:7),"NPC count matches one/four participant lineup");
  var field=horses();for(int i=0;i<field.size();i++)for(int j=i+1;j<field.size();j++)check(field.get(i).position().distanceTo(field.get(j).position())>1.7,"separate start footprints "+i+":"+j);
  check(field.stream().allMatch(h->!server.getLevel(HorseCourse.STADIUM_DIMENSION).noCollision(h,h.getBoundingBox().move(0,-.15,0))&&h.isNoAi()&&h.getPassengers().size()==1),"all eight settled with rider before countdown");
  check(row.getAsJsonArray("results").asList().stream().allMatch(v->v.getAsJsonObject().get("rawTicks").getAsInt()==0&&!v.getAsJsonObject().get("dnf").getAsBoolean()),"no race clock, stuck budget or DNF during entry");
  Path journal=server.getWorldPath(net.minecraft.world.level.storage.LevelResource.ROOT).resolve("data/muxi-horse-arena-leases-v1.json");check(new HorseArenaPool(server,journal).snapshot().get("held").getAsInt()==1,"sixteen-actor journal can reload safely");
  var level=server.getLevel(HorseCourse.STADIUM_DIMENSION);int supported=0;for(int i=0;i<192;i++)for(double lane:new double[]{-4.5,-2.7,-.9,.9,2.7,4.5}){var at=HorseStadium.point(-Math.PI/2+Math.PI*2*i/192,lane);if(!level.getBlockState(net.minecraft.core.BlockPos.containing(at.x,64,at.z)).isAir())supported++;}check(supported==1152,"generated oval supports grid lanes and overtaking shoulders around entire lap");
 }
 @SubscribeEvent(priority=EventPriority.LOWEST) public static void tick(ServerTickEvent.Post e){if(server==null||done)return;try {
  ticks++;if(stage>=2&&stage<=3){var owner=GameRuntime.get(server).memberships.owner(players.getFirst().getUUID());if(owner!=null)for(var p:players)EntryReadyNetwork.acknowledge(p,new EntryReadyNetwork.Reply(owner.team().session,true));}if(stage==5){if(rooms.snapshot(players.getFirst()).getAsJsonObject("arenaPool").get("held").getAsInt()==0){check(players.stream().allMatch(p->GameRuntime.get(server).memberships.owner(p.getUUID())==null&&!HorseReturn.pending(p)),"previous room and all participant returns released");caseNumber++;startCase(caseNumber==1?4:1);}return;}var row=room();String phase=row.get("phase").getAsString();if(ticks%20==0){var event=new JsonObject();event.addProperty("serverTick",server.getTickCount());event.addProperty("case",caseNumber);event.add("room",row.deepCopy());events.add(event);Files.writeString(Path.of("start-progress.json"),event.toString());}
  if(Set.of("BUILD_FAILED","START_FAILED").contains(phase))throw new IllegalStateException("unexpected start failure "+row);
  if(stage==0&&phase.equals("ASSEMBLING")){stage=1;entryTick=ticks;check(players.getFirst().isChangingDimension(),"native player awaits dimension acknowledgement before racing");}
  if(stage==1){check(phase.equals("ASSEMBLING"),"unacknowledged entry never races or auto-returns");check(players.getFirst().level().dimension().equals(HorseCourse.STADIUM_DIMENSION)&&HorseReturn.pending(players.getFirst()),"entry retains spectator and source return marker");if(ticks-entryTick>=80){stableGrid(row);for(var p:players)if(p.isChangingDimension())ack(p);stage=2;}}
  if(stage==2&&phase.equals("COUNTDOWN")){countdownTick=ticks;stage=3;stableGrid(row);}
  if(stage==3){check(phase.equals("COUNTDOWN")||phase.equals("RUNNING"),"countdown keeps complete field and room membership");if(phase.equals("RUNNING")){check(ticks-countdownTick>=100,"five full seconds precede formal run");runningTick=ticks;stage=4;}}
  if(stage==4){
   if(caseNumber==2){if(ticks-runningTick==10){for(var h:horses())for(var rider:List.copyOf(h.getPassengers()))rider.discard();}if(phase.equals("RACE_FAILED")){check(horses().stream().allMatch(Horse::isNoAi),"technical failure freezes generated race copies");check(players.getFirst().level().dimension().equals(HorseCourse.STADIUM_DIMENSION)&&HorseReturn.pending(players.getFirst()),"all-actor technical failure stays visible rather than silent automatic exit");rooms.action(players.getFirst(),"leave","");check(!HorseReturn.pending(players.getFirst())&&players.getFirst().getVehicle()==source,"explicit failure exit restores exact source mount");finish(true,"");return;}}
   else {
    if(!detachmentTested&&caseNumber==0&&ticks-runningTick>=20){var h=horses().stream().filter(v->v.getCustomName().getString().contains("赛方")).findFirst().orElseThrow();detached=(Mob)h.getPassengers().getFirst();detached.stopRiding();blockedUntil=server.getTickCount()+25;detachmentTested=true;}
    if(detachmentTested&&ticks-runningTick==65){check(detached.isPassenger()&&row.getAsJsonArray("results").asList().stream().noneMatch(v->v.getAsJsonObject().get("dnf").getAsBoolean()),"three transient mount vetoes recover without premature DNF");}
    if(ticks-runningTick==160){check(phase.equals("RUNNING")&&row.getAsJsonArray("results").asList().stream().allMatch(v->v.getAsJsonObject().get("gate").getAsInt()>0),"eight runners advance beyond start and remain in race for eight seconds");if(caseNumber==1){rooms.action(players.getFirst(),"leave","");stage=5;}}
    if(phase.equals("RUNNING")&&row.getAsJsonArray("results").asList().stream().allMatch(v->v.getAsJsonObject().get("finished").getAsBoolean())){if(allFinishedTick==0)allFinishedTick=ticks;if(ticks-allFinishedTick>40)throw new IllegalStateException("All finished but standings/return did not advance");}
    if(phase.equals("RACE_FAILED"))throw new IllegalStateException("healthy field all DNF "+row);
    if(caseNumber==0&&phase.equals("FINISHED")){check(row.getAsJsonArray("results").asList().stream().allMatch(v->v.getAsJsonObject().get("finished").getAsBoolean()&&!v.getAsJsonObject().get("dnf").getAsBoolean()),"all eight finish formal obstacle lap through native physics");races.add(row.deepCopy());check(players.getFirst().getVehicle()==source&&source.isAlive()&&!HorseReturn.pending(players.getFirst()),"natural finish restores original UUID mount");rooms.action(players.getFirst(),"leave","");stage=5;}
   }
  }
  if(ticks>9000)throw new IllegalStateException("bounded native start regression deadline");
 }catch(Throwable error){error.printStackTrace();finish(false,error.toString());}}
 static void finish(boolean pass,String error){if(done)return;done=true;try {var report=new JsonObject();report.addProperty("success",pass);report.addProperty("error",error);report.addProperty("syntheticServerPlayers",true);report.addProperty("syntheticTeleportAcknowledgements",true);report.addProperty("realNetworkClientAcceptance",false);report.addProperty("nativeEnginePhysics",true);report.add("checks",checks);report.add("races",races);report.add("events",events);Files.writeString(Path.of("result.json"),new GsonBuilder().setPrettyPrinting().create().toJson(report));}catch(Exception write){write.printStackTrace();}server.halt(false);}
}
