package net.muxigame.localdebug;
import com.google.gson.*;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.animal.horse.Horse;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.phys.Vec3;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.tick.*;
import java.util.*;

/** QA only: observes native ticks and motion, never teleports or drives a race actor. */
public final class HorseRaceObserver {
 static final Map<UUID,Integer> posts=new HashMap<>(),idle=new HashMap<>(),maxIdle=new HashMap<>(),collisions=new HashMap<>();
 static final Map<UUID,Vec3> last=new HashMap<>();
 static final Map<UUID,Entity> references=new LinkedHashMap<>();
 static final Map<String,Integer> overlaps=new HashMap<>(),consecutive=new HashMap<>(),maxOverlap=new HashMap<>();
 static int frames;static boolean installed;
 public static void install(){if(installed)return;installed=true;NeoForge.EVENT_BUS.addListener(HorseRaceObserver::entity);NeoForge.EVENT_BUS.addListener(HorseRaceObserver::tick);}
 static boolean relevant(Entity e){return e.getTags().contains("muxi_horse_prototype")||e.getTags().contains("qa_horse_control");}
 static void entity(EntityTickEvent.Post event){Entity e=event.getEntity();if(e.level() instanceof ServerLevel&&relevant(e))posts.merge(e.getUUID(),1,Integer::sum);}
 static boolean leased(Entity e){try{return (boolean)Class.forName("net.muxigame.minigames.horse.HorseArenaPool").getMethod("requiresSimulation",Entity.class).invoke(null,e);}catch(Exception absent){return false;}}
 static Boolean optimizedTick(Entity e){try{return (Boolean)Class.forName("toni.doesittick.DoesItTick").getMethod("isTickable",Entity.class).invoke(null,e);}catch(Exception absent){return null;}}
 static void tick(ServerTickEvent.Post event){
  var server=event.getServer();frames++;var horses=new ArrayList<Horse>();
  for(var level:server.getAllLevels())for(var e:level.getAllEntities())if(relevant(e)){
   references.put(e.getUUID(),e);
   if(e instanceof Horse h&&!h.isNoAi()&&leased(h)){
    horses.add(h);Vec3 previous=last.put(e.getUUID(),e.position());int value=previous!=null&&e.position().subtract(previous).horizontalDistance()<.0001?idle.getOrDefault(e.getUUID(),0)+1:0;
    idle.put(e.getUUID(),value);maxIdle.merge(e.getUUID(),value,Math::max);
    if(h.horizontalCollision)collisions.merge(e.getUUID(),1,Integer::sum);
   }
  }
  for(int i=0;i<horses.size();i++)for(int j=i+1;j<horses.size();j++){
   var a=horses.get(i);var b=horses.get(j);String key=a.getUUID()+":"+b.getUUID();
   if(a.getBoundingBox().intersects(b.getBoundingBox())){overlaps.merge(key,1,Integer::sum);int n=consecutive.merge(key,1,Integer::sum);maxOverlap.merge(key,n,Math::max);}else consecutive.put(key,0);
  }
  if(server.getTickCount()%20==0)try{DebugFiles.write("horse-native-observer.json",capture(server));}catch(Exception failure){System.out.println("HORSE_QA_OBSERVER_IO "+failure);}
 }
 public static JsonObject capture(MinecraftServer server){
  var root=new JsonObject();root.addProperty("tick",server.getTickCount());root.addProperty("utcMillis",System.currentTimeMillis());root.addProperty("frames",frames);var rows=new JsonArray();
  for(var e:references.values()){
   var row=new JsonObject();row.addProperty("uuid",e.getUUID().toString());row.addProperty("name",e.getName().getString());row.addProperty("type",net.minecraft.core.registries.BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString());row.addProperty("dimension",e.level().dimension().location().toString());row.addProperty("x",e.getX());row.addProperty("y",e.getY());row.addProperty("z",e.getZ());row.addProperty("nativeTick",e.tickCount);row.addProperty("nativePostTicks",posts.getOrDefault(e.getUUID(),0));row.addProperty("maxIdleTicks",maxIdle.getOrDefault(e.getUUID(),0));row.addProperty("collisionFrames",collisions.getOrDefault(e.getUUID(),0));row.addProperty("removed",e.isRemoved());row.addProperty("leaseAllowed",leased(e));Boolean tickable=optimizedTick(e);if(tickable!=null)row.addProperty("doesItTickAllowed",tickable);row.addProperty("control",e.getTags().contains("qa_horse_control"));row.addProperty("entityTicking",e.level() instanceof ServerLevel l&&l.isPositionEntityTicking(e.blockPosition()));row.addProperty("vehicle",e.getVehicle()==null?"":e.getVehicle().getUUID().toString());row.addProperty("width",e.getBbWidth());double distance=Double.POSITIVE_INFINITY;
   for(var player:server.getPlayerList().getPlayers())if(player.level()==e.level())distance=Math.min(distance,e.position().subtract(player.position()).horizontalDistance());row.addProperty("minPlayerHorizontalDistance",Double.isFinite(distance)?distance:-1);rows.add(row);
  }
  root.add("actors",rows);root.add("pairOverlapFrames",new Gson().toJsonTree(overlaps));root.add("maxContinuousPairOverlap",new Gson().toJsonTree(maxOverlap));var tickets=new JsonArray();
  for(var level:server.getAllLevels())try{
   var manager=level.getChunkSource().chunkMap.getDistanceManager();var field=net.minecraft.server.level.DistanceManager.class.getDeclaredField("tickets");field.setAccessible(true);var map=(Map<?,?>)field.get(manager);
   for(var entry:map.entrySet())for(var t:(Iterable<?>)entry.getValue())if(t.toString().contains("muxi_horse_simulation")){var ticket=new JsonObject();ticket.addProperty("dimension",level.dimension().location().toString());ticket.addProperty("chunk",entry.getKey().toString());ticket.addProperty("ticket",t.toString());tickets.add(ticket);}
  }catch(Exception error){root.addProperty("ticketProbeError",error.toString());}
  root.add("simulationTickets",tickets);
  try{var f=Class.forName("net.muxigame.minigames.horse.HorseArenaPool").getDeclaredField("LIVE");f.setAccessible(true);Object pool=((Map<?,?>)f.get(null)).get(server);if(pool!=null)root.add("arenaPool",(JsonElement)pool.getClass().getMethod("snapshot").invoke(pool));}catch(Exception failure){root.addProperty("arenaPoolProbeError",failure.toString());}
  return root;
 }
 public static JsonObject controls(MinecraftServer server){
  var level=server.getLevel(net.minecraft.resources.ResourceKey.create(net.minecraft.core.registries.Registries.DIMENSION,net.minecraft.resources.ResourceLocation.parse("muxi_minigames:horse_stadium_lab")));if(level==null)throw new IllegalStateException("Stadium absent");
  Horse h=EntityType.HORSE.create(level);h.setNoAi(true);h.setPersistenceRequired();h.setInvulnerable(true);h.addTag("qa_horse_control");h.setCustomName(net.minecraft.network.chat.Component.literal("QA ordinary horse"));h.setPos(90.5,65,70.5);if(!level.addFreshEntity(h))throw new IllegalStateException("Control spawn failed");references.put(h.getUUID(),h);return capture(server);
 }
 public static void clearControls(){for(var e:references.values())if(e.getTags().contains("qa_horse_control")&&!e.isRemoved())e.discard();}
}
