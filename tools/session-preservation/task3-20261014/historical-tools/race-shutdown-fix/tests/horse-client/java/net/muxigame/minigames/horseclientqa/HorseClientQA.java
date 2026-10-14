package net.muxigame.minigames.horseclientqa;

import com.google.gson.*;
import net.minecraft.client.*;
import net.minecraft.client.gui.screens.*;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.*;
import net.minecraft.world.Difficulty;
import net.minecraft.world.entity.animal.horse.Horse;
import net.minecraft.world.level.*;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.levelgen.*;
import net.minecraft.world.level.levelgen.presets.WorldPresets;
import net.muxigame.minigames.*;
import net.muxigame.minigames.horse.*;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.client.event.ClientTickEvent;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import java.nio.file.*;
import java.util.*;

/** QA attached to an existing jar copy only. Genuine native client, private integrated world. */
@EventBusSubscriber(modid="muxi_horse_racing",value=Dist.CLIENT)
public final class HorseClientQA {
    public static int maidDraws,mountedMaidDraws;
    private static int ticks,stage,frames;
    private static boolean starting,closing,done,enemyRequested;
    private static volatile boolean ready;
    private static volatile String error="";
    private static volatile int previewHorseId,previewMaidId,raceHorseId;
    private static volatile JsonObject roomState;
    private static Horse source;
    private static UUID sourceId;
    private static HorsePilot preview;
    private static String model,texture;
    private static final JsonArray checks=new JsonArray();
    private static final JsonObject report=new JsonObject();
    private static void check(boolean pass,String label){if(!pass)throw new IllegalStateException(label);checks.add(label);}
    private static void save(String file,Object value)throws Exception{Files.writeString(Path.of(file),new GsonBuilder().setPrettyPrinting().create().toJson(value));}
    private static void shot(String name)throws Exception{try(var image=Screenshot.takeScreenshot(Minecraft.getInstance().getMainRenderTarget())){image.writeToFile(Path.of(name+".png"));}}
    private static void serverTask(java.util.function.Consumer<ServerPlayer> task){var mc=Minecraft.getInstance();var server=mc.getSingleplayerServer();UUID id=mc.player.getUUID();ready=false;server.execute(()->{try{task.accept(server.getPlayerList().getPlayer(id));ready=true;}catch(Throwable failure){error=failure.toString();}});}
    private static JsonObject state(ServerPlayer p){for(var row:GameRuntime.get(p.server).snapshot(p,"").getAsJsonArray("games")){var game=row.getAsJsonObject();if(game.get("id").getAsString().equals(HorseRooms.ID))return game.getAsJsonObject("state");}throw new IllegalStateException("Horse module missing");}
    private static void next(){stage++;frames=0;}
    private static void camera(Entity horse){if(horse==null)return;var p=Minecraft.getInstance().player;double x=horse.getX()+4,y=horse.getY()+3.2,z=horse.getZ()+6;double dx=horse.getX()-x,dz=horse.getZ()-z,dy=horse.getY()+1.2-y;p.setPos(x,y,z);p.setYRot((float)Math.toDegrees(Math.atan2(-dx,dz)));p.setXRot((float)-Math.toDegrees(Math.atan2(dy,Math.hypot(dx,dz))));p.setOldPosAndRot();}
    private static void close(boolean success,String reason)throws Exception{report.addProperty("success",success);report.addProperty("error",reason);report.add("checks",checks);report.addProperty("maidDraws",maidDraws);report.addProperty("mountedMaidDraws",mountedMaidDraws);save("client-result.json",report);closing=true;stage=99;frames=0;var mc=Minecraft.getInstance();if(mc.level!=null)mc.level.disconnect();mc.disconnect(new TitleScreen());}
    @SubscribeEvent public static void serverTick(ServerTickEvent.Post e){if(!Boolean.getBoolean("muxi.horse.clientQa")||closing)return;try{if(preview!=null&&stage==3)preview.tick();if(stage>=6&&stage<9){var p=e.getServer().getPlayerList().getPlayers().stream().findFirst().orElse(null);if(p!=null){roomState=state(p);var level=e.getServer().getLevel(HorseCourse.DIMENSION);for(Entity entity:level.getAllEntities())if(entity instanceof Horse h&&h.getTags().contains(HorsePilot.MARKER)){raceHorseId=h.getId();break;}}}}catch(Throwable failure){error=failure.toString();}}
    @SubscribeEvent public static void tick(ClientTickEvent.Post e){if(!Boolean.getBoolean("muxi.horse.clientQa")||done)return;var mc=Minecraft.getInstance();try{
        ticks++;
        if(ticks%40==0){JsonObject progress=new JsonObject();progress.addProperty("stage",stage);progress.addProperty("ticks",ticks);progress.addProperty("frames",frames);progress.addProperty("maidDraws",maidDraws);progress.addProperty("screen",mc.screen==null?"":mc.screen.getClass().getName());save("client-progress.json",progress);}
        if(stage==99){if(mc.level!=null||mc.getSingleplayerServer()!=null||++frames<40)return;report.addProperty("normalLogout",true);save("client-result.json",report);done=true;mc.stop();return;}
        if(!error.isEmpty())throw new IllegalStateException(error);
        if(Files.exists(Path.of("request-normal-close.json"))||ticks>10000)throw new IllegalStateException("Private QA deadline stage "+stage);
        if(mc.getOverlay()!=null)return;
        if(!starting){if(!(mc.screen instanceof TitleScreen))return;check(Files.exists(Path.of("HORSE_QA_ONLY")),"own private lab");starting=true;mc.options.renderDistance().set(2);mc.createWorldOpenFlows().createFreshLevel("horse-private-qa",new LevelSettings("Horse private QA",GameType.CREATIVE,false,Difficulty.PEACEFUL,false,new GameRules(),WorldDataConfiguration.DEFAULT),new WorldOptions(92345678L,false,false),a->a.registryOrThrow(Registries.WORLD_PRESET).getHolderOrThrow(WorldPresets.FLAT).value().createWorldDimensions(),mc.screen);return;}
        if(mc.player==null||mc.level==null||mc.screen instanceof ReceivingLevelScreen)return;
        frames++;if(frames>1800)throw new IllegalStateException("Stage timeout "+stage);
        if(stage==0){
            Class<?> registry=Class.forName("com.elfmcys.yesstevemodel.oo0o0Oooo0OOO00OOo0OO000");Map<?,?> models=(Map<?,?>)registry.getMethod("oOoo00O0o0oO0o0oO00OO0O0").invoke(null);if(models.isEmpty())return;
            model=models.keySet().stream().map(Object::toString).filter(k->k.contains("01_taisho_maid")).findFirst().orElse(null);if(model==null&&frames<500)return;check(model!=null,"installed Wine Fox taisho maid model registered");Object definition=models.get(model).getClass().getMethod("oOo0OO0O0o000OO0O000oo0o").invoke(models.get(model));Map<?,?> textures=(Map<?,?>)definition.getClass().getMethod("oOOO00ooO0oOOoOOo0OoOOOo").invoke(definition);texture=textures.keySet().stream().map(Object::toString).findFirst().orElseThrow();report.addProperty("model",model);report.addProperty("texture",texture);System.setProperty("muxi.horse.model",model);System.setProperty("muxi.horse.texture",texture);next();
            serverTask(p->{var world=p.server.overworld();for(int x=-5;x<=5;x++)for(int z=-5;z<=5;z++)world.setBlockAndUpdate(new BlockPos(x,64,z),Blocks.STONE.defaultBlockState());source=EntityType.HORSE.create(world);source.setPos(.5,65,.5);source.setTamed(true);source.setAge(0);source.setPersistenceRequired();source.setCustomName(Component.literal("Client QA source"));world.addFreshEntity(source);sourceId=source.getUUID();p.teleportTo(world,.5,65,.5,0,0);p.startRiding(source,true);var course=new HorseCourse(480,0);var level=p.server.getLevel(HorseCourse.DIMENSION);course.build(level);preview=new HorsePilot(level,course,new HorseIdentity(UUID.randomUUID(),"Wine Fox visual QA",.225,.8,24,false),model,texture);previewHorseId=preview.horse.getId();previewMaidId=preview.rider.getId();p.stopRiding();p.teleportTo(level,484,68,6,150,15);p.setGameMode(GameType.SPECTATOR);});return;
        }
        if(stage==1){if(!ready||!mc.level.dimension().equals(HorseCourse.DIMENSION))return;var horse=mc.level.getEntity(previewHorseId);var maid=mc.level.getEntity(previewMaidId);if(horse==null||maid==null)return;camera(horse);if(frames<100||mountedMaidDraws<5)return;
            check(maid.getVehicle()==horse,"real client receives mounted maid relationship");check(MaidRider.WINEFOX_SEAT.equals(maid.getCustomName().getStyle().getInsertion()),"generated rider seat marker synchronizes to real client");report.addProperty("riderAttachmentY",maid.getVehicleAttachmentPoint(horse).y);check(maid.getVehicleAttachmentPoint(horse).y>=.6,"Wine Fox seat correction applied on actual client");Object renderer=mc.getEntityRenderDispatcher().getRenderer(maid);var field=renderer.getClass().getDeclaredField("ysmMaidRenderer");field.setAccessible(true);Object ysm=field.get(renderer);Object geo=ysm.getClass().getMethod("getGeoEntity",Entity.class).invoke(ysm,maid);String resolved=(String)geo.getClass().getMethod("O000OOo000oOOo0OO0oo0o0O").invoke(geo);report.addProperty("resolvedModel",resolved);check(model.equals(resolved),"native YSM renderer resolves actual selected model");check(geo.getClass().getMethod("getGeoModel").invoke(geo)!=null,"native YSM geometry loaded");report.addProperty("horseRenderer",mc.getEntityRenderDispatcher().getRenderer(horse).getClass().getName());check((Object)mc.getEntityRenderDispatcher().getRenderer(horse) instanceof net.minecraft.client.renderer.entity.HorseRenderer,"vanilla Horse renderer retained");shot("winefox-mounted-still");next();return;
        }
        if(stage==2){next();return;}
        if(stage==3){camera(mc.level.getEntity(previewHorseId));if(frames==60)shot("winefox-mounted-moving");if(frames<160)return;check(mountedMaidDraws>50,"native mounted YSM branch rendered across moving race frames");next();serverTask(p->{preview.close();preview.course.unload(p.server.getLevel(HorseCourse.DIMENSION));preview=null;p.teleportTo(p.server.overworld(),.5,65,.5,0,0);p.setGameMode(GameType.CREATIVE);p.startRiding(source,true);});return;}
        if(stage==4){if(!ready||!mc.level.dimension().equals(Level.OVERWORLD)||mc.player.getVehicle()==null)return;check(mc.player.getVehicle().getUUID().equals(sourceId),"genuine client mounted source horse before creating room");mc.player.connection.sendCommand("horseproto createMap hills-plains");next();return;}
        if(stage==5){if(frames<30)return;next();serverTask(p->{roomState=state(p);check(roomState.getAsJsonArray("rooms").size()==1,"real command packet creates waiting room");check(p.getVehicle()==source&&p.level().dimension().equals(Level.OVERWORLD),"waiting room preserves original horse and dimension");});return;}
        if(stage==6){if(!ready)return;if(!enemyRequested){mc.player.connection.sendCommand("horseproto enemy 5");enemyRequested=true;frames=0;return;}if(frames<20)return;check(roomState.getAsJsonArray("rooms").get(0).getAsJsonObject().get("enemyTier").getAsInt()==5,"real command packet selects highest enemy tier while waiting");check(!roomState.getAsJsonObject("market").get("tradingEnabled").getAsBoolean()&&roomState.getAsJsonObject("market").getAsJsonArray("offers").size()==5,"product exposes fixed catalog quotes with real trading disabled");mc.player.connection.sendCommand("horseproto start");next();return;}
        if(stage==7){if(mc.level.dimension().equals(HorseCourse.DIMENSION)){camera(mc.level.getEntity(raceHorseId));if(frames==80)shot("real-client-full-race");}if(roomState==null||roomState.getAsJsonArray("rooms").isEmpty())return;var room=roomState.getAsJsonArray("rooms").get(0).getAsJsonObject();if(!room.get("phase").getAsString().equals("FINISHED"))return;report.add("race",roomState.deepCopy());next();serverTask(p->{check(p.getVehicle()==source&&source.getUUID().equals(sourceId),"race restores genuine player to same original mount");check(!HorseReturn.pending(p),"durable return marker cleared after success");var results=roomState.getAsJsonArray("rooms").get(0).getAsJsonObject().getAsJsonArray("results");check(results.size()==2,"player horse and NPC both produce results");for(var r:results)check(r.getAsJsonObject().get("finished").getAsBoolean()&&!r.getAsJsonObject().get("dnf").getAsBoolean(),"complete native race without DNF");check(roomState.getAsJsonArray("top10").size()==2,"player and NPC native results persisted in history");});return;}
        if(stage==8){if(!ready||!mc.level.dimension().equals(Level.OVERWORLD)||mc.player.getVehicle()==null)return;shot("real-client-returned");mc.player.connection.sendCommand("horseproto leave");next();return;}
        if(stage==9&&frames>40){close(true,"");}
    }catch(Throwable failure){try{close(false,failure.toString());}catch(Throwable nested){nested.printStackTrace();mc.stop();}}}
}
