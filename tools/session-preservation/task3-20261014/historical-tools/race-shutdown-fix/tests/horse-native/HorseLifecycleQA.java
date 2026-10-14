package net.muxigame.minigames.horseqa;

import com.google.gson.*;
import com.mojang.authlib.GameProfile;
import net.minecraft.network.Connection;
import net.minecraft.network.protocol.*;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.*;
import net.minecraft.server.network.*;
import net.minecraft.server.players.PlayerList;
import net.minecraft.network.chat.Component;
import net.minecraft.world.entity.*;
import net.minecraft.world.entity.animal.horse.Horse;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.item.*;
import net.minecraft.world.level.*;
import net.muxigame.minigames.*;
import net.muxigame.minigames.horse.*;
import net.neoforged.bus.api.*;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.entity.EntityTravelToDimensionEvent;
import net.neoforged.neoforge.event.entity.player.PlayerEvent;
import net.neoforged.neoforge.event.server.ServerStartedEvent;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import java.lang.reflect.*;
import java.net.InetSocketAddress;
import java.nio.file.*;
import java.util.*;

/** Actual NeoForge travel cancellation, native multi-player spectators, retry and logout recovery. */
@EventBusSubscriber(modid="muxi_horse_racing")
public final class HorseLifecycleQA {
    private static MinecraftServer server;private static HorseRooms rooms;private static ServerPlayer host,guest,stranger;private static Horse source;
    private static int stage,ticks;private static boolean cancelEntry,cancelReturn,done;private static final JsonArray checks=new JsonArray();
    private static void check(boolean value,String label){var row=new JsonObject();row.addProperty("passed",value);row.addProperty("label",label);checks.add(row);if(!value)throw new IllegalStateException(label);}
    @SuppressWarnings("unchecked") private static ServerPlayer player(String name)throws Exception{
        var p=new ServerPlayer(server,server.overworld(),new GameProfile(UUID.nameUUIDFromBytes(name.getBytes(java.nio.charset.StandardCharsets.UTF_8)),name),ClientInformation.createDefault());
        var connection=new Connection(PacketFlow.SERVERBOUND);var field=Connection.class.getDeclaredField("channel");field.setAccessible(true);field.set(connection,new io.netty.channel.embedded.EmbeddedChannel());
        p.connection=new ServerGamePacketListenerImpl(server,connection,p,CommonListenerCookie.createInitial(p.getGameProfile(),false)){
            public void send(Packet<?> packet){}public void send(Packet<?> packet,net.minecraft.network.PacketSendListener listener){}public java.net.SocketAddress getRemoteAddress(){return new InetSocketAddress("127.0.0.1",49113);}
        };
        var list=PlayerList.class.getDeclaredField("players");list.setAccessible(true);((List<ServerPlayer>)list.get(server.getPlayerList())).add(p);
        var map=PlayerList.class.getDeclaredField("playersByUUID");map.setAccessible(true);((Map<UUID,ServerPlayer>)map.get(server.getPlayerList())).put(p.getUUID(),p);
        p.setPos(.5,65,.5);p.setGameMode(GameType.SURVIVAL);server.overworld().addNewPlayer(p);return p;
    }
    @SubscribeEvent public static void travel(EntityTravelToDimensionEvent event){if(server==null)return;if(cancelEntry&&event.getEntity()==host&&event.getDimension().equals(HorseCourse.STADIUM_DIMENSION)||cancelReturn&&event.getEntity()==guest&&event.getDimension().equals(Level.OVERWORLD))event.setCanceled(true);}
    private static JsonObject room(){return rooms.snapshot(host).getAsJsonArray("rooms").get(0).getAsJsonObject();}
    private static void create(boolean invite){rooms.action(host,"create","");if(invite){var team=GameRuntime.get(server).memberships.owner(host.getUUID()).team();rooms.action(host,"invite",guest.getUUID().toString());rooms.action(guest,"join",team.shortId());}rooms.action(host,"enemy","1");}
    private static void asset(){check(source.isAlive()&&source.level()==server.overworld()&&host.getUUID().equals(source.getOwnerUUID()),"original horse alive at source with same ownership");check(source.getCustomName().getString().equals("Lifecycle source")&&source.getHealth()==17&&source.getMaxHealth()==17&&source.isSaddled()&&source.getBodyArmorItem().is(Items.DIAMOND_HORSE_ARMOR),"original name, full fixture health, saddle and armor preserved");long count=0;for(var level:server.getAllLevels())for(Entity entity:level.getAllEntities())if(entity.getUUID().equals(source.getUUID())&&!entity.isRemoved())count++;check(count==1,"original horse UUID exists exactly once across dimensions");}
    @SubscribeEvent(priority=EventPriority.LOWEST) @SuppressWarnings("unchecked") public static void start(ServerStartedEvent event){if(!Boolean.getBoolean("muxi.horse.lifecycleQa"))return;server=event.getServer();try{
        check(Files.exists(Path.of("HORSE_QA_ONLY"))&&server.getPlayerList().getPlayers().isEmpty(),"isolated generated assets and native players only");host=player("LifecycleHost");guest=player("LifecycleGuest");stranger=player("LifecycleStranger");
        source=EntityType.HORSE.create(server.overworld());source.setTamed(true);source.setOwnerUUID(host.getUUID());source.setAge(0);source.setNoAi(true);source.setPersistenceRequired();source.setPos(.5,65,.5);source.setCustomName(Component.literal("Lifecycle source"));source.getAttribute(Attributes.MOVEMENT_SPEED).setBaseValue(.3);source.getAttribute(Attributes.JUMP_STRENGTH).setBaseValue(1);source.getAttribute(Attributes.MAX_HEALTH).setBaseValue(17);source.setHealth(17);source.equipSaddle(new ItemStack(Items.SADDLE),net.minecraft.sounds.SoundSource.NEUTRAL);source.setBodyArmorItem(new ItemStack(Items.DIAMOND_HORSE_ARMOR));server.overworld().addFreshEntity(source);host.startRiding(source,true);
        var active=HorseRacing.class.getDeclaredField("ACTIVE");active.setAccessible(true);rooms=((Map<MinecraftServer,HorseRooms>)active.get(null)).get(server);
        create(true);boolean rejected=false;try{rooms.action(stranger,"join",GameRuntime.get(server).memberships.owner(host.getUUID()).team().shortId());}catch(IllegalArgumentException expected){rejected=true;}check(rejected,"uninvited native third player cannot join");
        check(host.getVehicle()==source&&host.level()==server.overworld()&&guest.level()==server.overworld()&&!HorseReturn.pending(host),"room waiting leaves both players and source assets in original world");cancelEntry=true;rooms.action(host,"start","");
    }catch(Throwable error){finish(false,error.toString());}}
    @SubscribeEvent(priority=EventPriority.LOWEST) public static void tick(ServerTickEvent.Post event){if(server==null||done)return;try{
        ticks++;if(ticks>6000)throw new IllegalStateException("bounded lifecycle deadline");String phase=room().get("phase").getAsString();
        if(stage==0&&phase.equals("BUILD_FAILED")){
            check(host.level()==server.overworld()&&host.gameMode.getGameModeForPlayer()==GameType.SURVIVAL&&!HorseReturn.pending(host)&&host.getVehicle()==source,"canceled entry rolls back location, mode and source mount");check(guest.level()==server.overworld()&&!HorseReturn.pending(guest),"entry failure does not strand second participant");asset();cancelEntry=false;rooms.action(host,"leave","");check(GameRuntime.get(server).memberships.owner(guest.getUUID())==null,"failed host room exit releases invited participant");create(true);rooms.action(host,"start","");stage=1;
        }else if(stage==1&&phase.equals("RUNNING")){
            check(host.level().dimension().equals(HorseCourse.STADIUM_DIMENSION)&&guest.level()==host.level()&&host.gameMode.getGameModeForPlayer()==GameType.SPECTATOR&&guest.gameMode.getGameModeForPlayer()==GameType.SPECTATOR,"native invited players share running scene as spectators");asset();cancelReturn=true;boolean rejected=false;try{rooms.action(guest,"leave","");}catch(IllegalStateException expected){rejected=true;}
            check(rejected&&HorseReturn.pending(guest)&&guest.level().dimension().equals(HorseCourse.STADIUM_DIMENSION)&&guest.gameMode.getGameModeForPlayer()==GameType.SPECTATOR,"canceled return retains durable marker, race dimension and mode");check(GameRuntime.get(server).memberships.owner(guest.getUUID())!=null,"failed return preserves room ownership for retry");cancelReturn=false;rooms.action(guest,"leave","");check(!HorseReturn.pending(guest)&&guest.level()==server.overworld()&&GameRuntime.get(server).memberships.owner(guest.getUUID())==null,"retry safely returns and removes only leaving guest");stage=2;
        }else if(stage==2&&phase.equals("FINISHED")){
            var results=room().getAsJsonArray("results");check(results.asList().stream().filter(r->r.getAsJsonObject().get("finished").getAsBoolean()).count()==2,"remaining native host and NPC finish and receive standings");check(host.getVehicle()==source&&!HorseReturn.pending(host)&&host.gameMode.getGameModeForPlayer()==GameType.SURVIVAL,"completed race restores source mount and original player mode");asset();rooms.action(host,"leave","");create(false);rooms.action(host,"start","");rooms.action(host,"leave","");check(!HorseReturn.pending(host)&&host.getVehicle()==source&&GameRuntime.get(server).memberships.owner(host.getUUID())==null,"exit during scene preparation safely cancels and permits reentry");create(false);rooms.action(host,"start","");stage=3;
        }else if(stage==3&&phase.equals("RUNNING")){
            NeoForge.EVENT_BUS.post(new PlayerEvent.PlayerLoggedOutEvent(host));check(!HorseReturn.pending(host)&&host.getVehicle()==source&&host.level()==server.overworld()&&GameRuntime.get(server).memberships.owner(host.getUUID())==null,"native logout event restores running source assets and clears room");asset();rooms.action(stranger,"create","");check(!rooms.snapshot(stranger).getAsJsonArray("rooms").get(0).getAsJsonObject().getAsJsonArray("horses").get(0).getAsJsonObject().get("sourceHorse").getAsBoolean(),"unmounted player receives a default competition horse identity");rooms.action(stranger,"leave","");finish(true,"");
        }
    }catch(Throwable error){error.printStackTrace();finish(false,error.toString());}}
    private static void finish(boolean success,String error){if(done)return;done=true;try{var report=new JsonObject();report.addProperty("success",success);report.addProperty("error",error);report.addProperty("nativePlayerCount",3);report.addProperty("realNetworkClientCount",0);report.add("checks",checks);Files.writeString(Path.of("result.json"),new GsonBuilder().setPrettyPrinting().create().toJson(report));}catch(Exception failure){failure.printStackTrace();}server.halt(false);}
}
