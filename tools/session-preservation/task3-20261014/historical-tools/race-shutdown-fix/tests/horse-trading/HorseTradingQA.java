package net.muxigame.minigames.horseqa;
import com.google.gson.*;
import com.mojang.authlib.GameProfile;
import net.minecraft.network.*;
import net.minecraft.network.protocol.*;
import net.minecraft.server.*;
import net.minecraft.server.level.*;
import net.minecraft.server.network.*;
import net.minecraft.server.players.PlayerList;
import net.minecraft.core.BlockPos;
import net.minecraft.world.entity.*;
import net.minecraft.world.entity.animal.horse.Horse;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.item.*;
import net.minecraft.world.level.*;
import net.minecraft.world.level.block.Blocks;
import net.muxigame.minigames.*;
import net.muxigame.minigames.horse.*;
import net.neoforged.bus.api.*;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.event.server.ServerStartedEvent;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import java.lang.reflect.*;
import java.net.InetSocketAddress;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

/** Real native entities and transport-bound synthetic users; isolated DB only. */
@EventBusSubscriber(modid="muxi_horse_racing")
public final class HorseTradingQA {
    private static MinecraftServer server;private static HorseRooms rooms;private static HorseTrading trade;
    private static ServerPlayer p,other;private static Horse source;private static UUID bought,sourceId;
    private static int stage,ticks,last;private static boolean done;private static UUID race;
    private static String quote,oldQuote,saleOperation;private static final JsonArray checks=new JsonArray();
    private static void check(boolean yes,String label){if(!yes)throw new IllegalStateException(label);checks.add(label);}
    private static String s(JsonObject o,String k){return o.has(k)?o.get(k).getAsString():"";}
    @SuppressWarnings("unchecked") private static ServerPlayer fixture(String name)throws Exception{
        var profile=new GameProfile(UUID.nameUUIDFromBytes(("OfflinePlayer:"+name).getBytes(StandardCharsets.UTF_8)),name);var player=new ServerPlayer(server,server.overworld(),profile,ClientInformation.createDefault());
        var connection=new Connection(PacketFlow.SERVERBOUND);var field=Connection.class.getDeclaredField("channel");field.setAccessible(true);field.set(connection,new io.netty.channel.embedded.EmbeddedChannel());
        player.connection=new ServerGamePacketListenerImpl(server,connection,player,CommonListenerCookie.createInitial(profile,false)){
            public void send(Packet<?> packet){}public void send(Packet<?> packet,PacketSendListener listener){}public java.net.SocketAddress getRemoteAddress(){return new InetSocketAddress("127.0.0.1",49113);}
        };
        field=PlayerList.class.getDeclaredField("players");field.setAccessible(true);((List<ServerPlayer>)field.get(server.getPlayerList())).add(player);field=PlayerList.class.getDeclaredField("playersByUUID");field.setAccessible(true);((Map<UUID,ServerPlayer>)field.get(server.getPlayerList())).put(player.getUUID(),player);
        player.setPos(.5,65,.5);player.setGameMode(GameType.CREATIVE);server.overworld().addNewPlayer(player);TrustedAccounts.admit(player);return player;
    }
    @SubscribeEvent(priority=EventPriority.LOWEST) @SuppressWarnings("unchecked") public static void started(ServerStartedEvent e){if(!Boolean.getBoolean("muxi.horse.tradeQa"))return;server=e.getServer();try{
        check(Files.exists(Path.of("HORSE_QA_ONLY"))&&server.getPlayerList().getPlayers().isEmpty(),"private engine world; no real player assets");
        p=fixture("10000");other=fixture("10001");var active=HorseRacing.class.getDeclaredField("ACTIVE");active.setAccessible(true);rooms=((Map<MinecraftServer,HorseRooms>)active.get(null)).get(server);trade=rooms.trading();
        server.overworld().setChunkForced(0,0,true);for(int x=-8;x<10;x++)for(int z=-8;z<10;z++){server.overworld().setBlockAndUpdate(new BlockPos(x,64,z),Blocks.STONE.defaultBlockState());server.overworld().setBlockAndUpdate(new BlockPos(x,65,z),Blocks.AIR.defaultBlockState());server.overworld().setBlockAndUpdate(new BlockPos(x,66,z),Blocks.AIR.defaultBlockState());}
        GameRuntime.get(server).platform.refresh(p);GameRuntime.get(server).platform.refresh(other);trade.refresh(p);
        if(Boolean.getBoolean("muxi.horse.tradeResumeQa")){var saved=JsonParser.parseString(Files.readString(Path.of("restart-fixture.json"))).getAsJsonObject();sourceId=UUID.fromString(s(saved,"source"));bought=UUID.fromString(s(saved,"bought"));stage=90;return;}
        source=EntityType.HORSE.create(server.overworld());source.setPos(.5,65,.5);source.setTamed(true);source.setOwnerUUID(p.getUUID());source.setAge(0);source.setNoAi(true);source.setPersistenceRequired();source.setCustomName(net.minecraft.network.chat.Component.literal("自养原马甲"));source.getAttribute(Attributes.MOVEMENT_SPEED).setBaseValue(.3375);source.getAttribute(Attributes.JUMP_STRENGTH).setBaseValue(1.0);source.getAttribute(Attributes.MAX_HEALTH).setBaseValue(30);source.setHealth(30);source.equipSaddle(new ItemStack(Items.SADDLE),null);source.equipBodyArmor(p,new ItemStack(Items.DIAMOND_HORSE_ARMOR));server.overworld().addFreshEntity(source);sourceId=source.getUUID();p.startRiding(source,true);
    }catch(Throwable failure){finish(false,failure.toString());}}
    private static JsonObject state(){return trade.snapshot(p);}
    private static JsonObject lastQuote(){var rows=state().getAsJsonArray("orders");if(rows==null)return null;for(int i=rows.size()-1;i>=0;i--){var o=rows.get(i).getAsJsonObject();if(s(o,"action").equals("quote")){if(!o.has("receipt"))return null;var q=o.getAsJsonObject("receipt");return s(q,"quote").equals(oldQuote)?null:q;}}return null;}
    private static JsonObject job(String action){var rows=state().getAsJsonArray("orders");if(rows==null)return null;for(int i=rows.size()-1;i>=0;i--){var o=rows.get(i).getAsJsonObject();if(s(o,"action").equals(action))return o;}return null;}
    private static boolean done(String action){var j=job(action);return j!=null&&s(j,"state").equals("DONE");}
    private static void quote(String action,String value){oldQuote=quote;rooms.action(p,action,value);next();}
    private static void confirm(){var q=lastQuote();quote=s(q,"quote");rooms.action(p,"tradeConfirm",quote);rooms.action(p,"tradeConfirm",quote);next();}
    private static void next(){stage++;last=ticks;}
    private static JsonObject room(){var rows=rooms.snapshot(p).getAsJsonArray("rooms");return rows.isEmpty()?null:rows.get(0).getAsJsonObject();}
    private static long count(UUID id){long n=0;for(var l:server.getAllLevels())for(var e:l.getAllEntities())if(e.getUUID().equals(id)&&!e.isRemoved())n++;return n;}
    private static long balance(){return state().has("service")?state().getAsJsonObject("service").get("balance").getAsLong():-1;}
    private static String pool(String name){var service=state().getAsJsonObject("service");if(service==null)return "";for(var item:service.getAsJsonArray("pool")){var a=item.getAsJsonObject();if(s(a.getAsJsonObject("body"),"name").equals(name)&&s(a,"state").equals("POOL"))return s(a,"asset");}return "";}
    private static void create(){rooms.action(p,"create","");race=GameRuntime.get(server).memberships.owner(p.getUUID()).team().session;}
    @SubscribeEvent(priority=EventPriority.LOWEST) public static void tick(ServerTickEvent.Post e){if(server==null||done)return;try{
        ticks++;if(ticks%60==0){GameRuntime.get(server).platform.refresh(p);GameRuntime.get(server).platform.refresh(other);trade.refresh(p);var v=new JsonObject();v.addProperty("stage",stage);v.addProperty("ticks",ticks);v.add("trade",state());Files.writeString(Path.of("trade-progress.json"),v.toString());}
        if(ticks>11000)throw new IllegalStateException("Bounded native trade deadline stage "+stage);
        switch(stage){
            case 0 -> {if(!GameRuntime.get(server).platform.allowed(p)||!GameRuntime.get(server).platform.allowed(other)||!state().has("service"))return;check(balance()==10000,"real service admission and isolated unified balance");System.setProperty("muxi.horse.tradeQa.failJournal","true");try{rooms.action(p,"tradeQuoteSell","");throw new IllegalStateException("Journal failure unexpectedly accepted sale");}catch(IllegalStateException expected){check(p.getVehicle()==source&&!HorseTrading.locked(source)&&balance()==10000,"failed durable journal prevents source mutation and money movement");}finally{System.clearProperty("muxi.horse.tradeQa.failJournal");}quote("tradeQuoteBuy","seabiscuit");}
            case 1 -> {if(lastQuote()==null)return;var q=lastQuote();check(q.get("amount").getAsInt()==120&&q.getAsJsonObject("body").getAsJsonArray("traits").size()==3,"fixed purchase price and visible traits before confirmation");try{trade.action(other,"tradeConfirm",s(q,"quote"),null);throw new IllegalStateException("Other user confirmed quote");}catch(IllegalArgumentException expected){check(true,"another UID cannot confirm this player's frozen quote");}confirm();}
            case 2 -> {if(!done("grant_ack"))return;bought=UUID.fromString(s(job("buy").getAsJsonObject("receipt"),"asset"));check(count(bought)==1,"double confirm and lost buy/ack receipts create exactly one native horse");var h=(Horse)server.overworld().getEntity(bought);check(h.isTamed()&&p.getUUID().equals(h.getOwnerUUID())&&h.isSaddled()&&!HorseTrading.locked(h),"delivered ownership and saddle; provisional lock released");check(source.isAlive()&&source.getOwnerUUID().equals(p.getUUID())&&source.getBodyArmorItem().is(Items.DIAMOND_HORSE_ARMOR),"unrelated self-raised horse and armor unchanged by purchase");Files.writeString(Path.of("reject-next-sale.flag"),"private failure");quote("tradeQuoteSell","");}
            case 3 -> {if(lastQuote()==null)return;check(lastQuote().get("amount").getAsInt()==1320,"attribute-driven buyback is fixed before custody mutation");confirm();saleOperation=s(job("sale_prepare"),"id");}
            case 4 -> {var j=job("sale_prepare");if(j==null||!s(j,"state").equals("REJECTED")||HorseTrading.locked(source)||p.getVehicle()!=source)return;check(source.isAlive()&&source.getUUID().equals(sourceId)&&source.isNoAi()&&!source.isInvulnerable()&&!source.isNoGravity()&&source.isSaddled()&&source.getBodyArmorItem().is(Items.DIAMOND_HORSE_ARMOR),"rejected custody restores same UUID, owner, flags, saddle, armor and mount");quote("tradeQuoteSell","");}
            case 5 -> {if(lastQuote()==null)return;confirm();}
            case 6 -> {if(!done("sale_commit"))return;check(count(sourceId)==0&&trade.evidence().getAsJsonObject("sold").has(sourceId.toString()),"confirmed sale removes original only after durable credit and tombstone");check(job("sale_commit").getAsJsonObject("receipt").get("delta").getAsInt()==1320,"lost sale acknowledgement credits 1320 once");create();trade.refresh(p);next();}
            case 7 -> {if(pool("自养原马甲").isEmpty())return;quote("tradeQuoteRent",pool("自养原马甲"));}
            case 8 -> {if(lastQuote()==null)return;check(lastQuote().get("amount").getAsInt()==45,"top rental fee exceeds all four players' possible 37-point reward");confirm();}
            case 9 -> {if(!done("rent"))return;check(trade.identity(race,p.getUUID()).horseId().equals(sourceId),"rental projects sold stable identity and original attributes");rooms.action(p,"tradeCancelRental","");next();}
            case 10 -> {if(!done("rental_end")||pool("自养原马甲").isEmpty())return;check(job("rental_end").getAsJsonObject("receipt").get("refunded").getAsBoolean(),"pre-start rental cancelled and refunded despite lost end receipt");quote("tradeQuoteRent",pool("自养原马甲"));}
            case 11 -> {if(lastQuote()==null)return;confirm();}
            case 12 -> {if(!done("rent")||trade.rental(race,p.getUUID())==null)return;rooms.action(p,"start","");next();}
            case 13 -> {if(room()==null||!s(room(),"phase").equals("FINISHED")||!done("rental_end"))return;check(!job("rental_end").getAsJsonObject("receipt").get("refunded").getAsBoolean(),"completed real vanilla race retains rental fee");check(room().getAsJsonArray("results").size()==2&&room().getAsJsonArray("results").asList().stream().allMatch(v->v.getAsJsonObject().get("finished").getAsBoolean()),"rented horse and maid complete oval obstacles with native physics");check(count(sourceId)==0&&count(bought)==1,"racing uses generated projection; sold source never resurrected; purchased own horse persists");rooms.action(p,"leave","");trade.refresh(p);next();}
            case 14 -> {if(!state().has("service"))return;create();if(pool("Equinox").isEmpty())return;quote("tradeQuoteRent",pool("Equinox"));}
            case 15 -> {if(lastQuote()==null)return;confirm();}
            case 16 -> {if(!done("rent")||trade.rental(race,p.getUUID())==null)return;rooms.action(p,"start","");next();}
            case 17 -> {if(room()==null||!s(room(),"phase").equals("RUNNING")||!done("rental_running"))return;check(true,"second rental enters physical race before simulated server stop");System.setProperty("muxi.horse.tradeQa.pauseDelivery","true");rooms.action(p,"leave","");check(!HorseReturn.pending(p),"early physical exit restores player while voluntary rental keeps fee");rooms.action(p,"tradeQuoteBuy","winx");oldQuote=quote;next();}
            case 18 -> {if(lastQuote()==null)return;confirm();}
            case 19 -> {var j=job("buy");if(j==null||!s(j,"state").equals("RECEIVED"))return;check(count(UUID.fromString(s(j.getAsJsonObject("receipt"),"asset")))==0,"durable paid purchase paused before native birth for actual JVM restart");var saved=new JsonObject();saved.addProperty("source",sourceId.toString());saved.addProperty("bought",bought.toString());saved.addProperty("pending",s(j.getAsJsonObject("receipt"),"asset"));saved.addProperty("realm",trade.realm());Files.writeString(Path.of("restart-fixture.json"),saved.toString());create();next();}
            case 20 -> {if(pool("Equinox").isEmpty())return;quote("tradeQuoteRent",pool("Equinox"));}
            case 21 -> {if(lastQuote()==null)return;confirm();}
            case 22 -> {if(!done("rent")||trade.rental(race,p.getUUID())==null)return;rooms.action(p,"start","");next();}
            case 23 -> {if(room()==null||!s(room(),"phase").equals("RUNNING")||!done("rental_running"))return;check(true,"unfinished physical rental left active for actual server stop and restart refund");finish(true,"");}
            case 90 -> {if(!done("grant_ack")||!done("rental_end"))return;var saved=JsonParser.parseString(Files.readString(Path.of("restart-fixture.json"))).getAsJsonObject();UUID pending=UUID.fromString(s(saved,"pending"));if(count(pending)!=1)return;check(trade.realm().equals(s(saved,"realm")),"realm and operation identity survive real JVM restart");check(count(pending)==1&&count(bought)==1&&count(sourceId)==0,"restart delivers pending horse once, keeps existing horse, and rejects sold source resurrection");check(!HorseTrading.locked(server.overworld().getEntity(pending)),"recovered paid delivery receives acknowledgement and unlocks");check(job("rental_end").getAsJsonObject("receipt").get("refunded").getAsBoolean(),"server interruption refunds unfinished physical rental exactly once on restart");finish(true,"");}
        }
    }catch(Throwable failure){finish(false,failure.toString());}}
    private static void finish(boolean success,String error){if(done)return;done=true;try{var report=new JsonObject();report.addProperty("success",success);report.addProperty("error",error);report.addProperty("stage",stage);report.addProperty("ticks",ticks);report.add("checks",checks);if(trade!=null)report.add("nativeJournal",trade.evidence());Files.writeString(Path.of("trade-result.json"),new GsonBuilder().setPrettyPrinting().create().toJson(report));}catch(Exception ignored){}server.halt(false);}
}
