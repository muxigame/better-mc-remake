package net.muxigame.minigames.horseqa;
import com.google.gson.*;
import com.mojang.authlib.GameProfile;
import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import com.github.tartaricacid.touhoulittlemaid.item.ItemFilm;
import net.minecraft.network.*;
import net.minecraft.network.protocol.*;
import net.minecraft.server.*;
import net.minecraft.server.level.*;
import net.minecraft.server.network.*;
import net.minecraft.server.players.PlayerList;
import net.minecraft.core.BlockPos;
import net.minecraft.world.entity.*;
import net.minecraft.world.entity.animal.horse.Horse;
import net.minecraft.world.level.*;
import net.minecraft.world.level.block.Blocks;
import net.muxigame.minigames.*;
import net.muxigame.minigames.horse.*;
import net.neoforged.bus.api.*;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.event.server.ServerStartedEvent;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import java.net.InetSocketAddress;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

/** Real commerce transport and native maid/Champions/film, private generated assets only. */
@EventBusSubscriber(modid="muxi_horse_racing")
public final class UmamusumeTradingQA {
    private static MinecraftServer server;private static HorseRooms rooms;private static HorseTrading trade;
    private static ServerPlayer p;private static Horse original;
    private static int stage,ticks,index;private static boolean done;private static String quote="",previous="",pending="";
    private static final JsonArray checks=new JsonArray(),delivered=new JsonArray();private static JsonObject resumed;
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

    @SubscribeEvent(priority=EventPriority.LOWEST) @SuppressWarnings("unchecked") public static void started(ServerStartedEvent e){if(!Boolean.getBoolean("muxi.horse.umamusumeQa"))return;server=e.getServer();try{
        check(Files.exists(Path.of("HORSE_QA_ONLY"))&&server.getPlayerList().getPlayers().isEmpty(),"private engine world and synthetic uid; no production assets");
        p=fixture("10000");var active=HorseRacing.class.getDeclaredField("ACTIVE");active.setAccessible(true);rooms=((Map<MinecraftServer,HorseRooms>)active.get(null)).get(server);trade=rooms.trading();
        for(int x=-4;x<42;x++)for(int z=-4;z<12;z++){server.overworld().setBlockAndUpdate(new BlockPos(x,64,z),Blocks.STONE.defaultBlockState());for(int y=65;y<69;y++)server.overworld().setBlockAndUpdate(new BlockPos(x,y,z),Blocks.AIR.defaultBlockState());}
        for(int x=0;x<3;x++)server.overworld().setChunkForced(x,0,true);
        GameRuntime.get(server).platform.refresh(p);trade.refresh(p);
        if(Boolean.getBoolean("muxi.horse.umamusumeResumeQa")){resumed=JsonParser.parseString(Files.readString(Path.of("restart-fixture.json"))).getAsJsonObject();stage=90;return;}
        original=EntityType.HORSE.create(server.overworld());original.setPos(39.5,65,5.5);original.setTamed(true);original.setOwnerUUID(p.getUUID());original.setNoAi(true);original.setPersistenceRequired();server.overworld().addFreshEntity(original);
    }catch(Throwable failure){finish(false,failure.toString());}}
    private static JsonObject state(){return trade.snapshot(p);}
    private static JsonObject last(String action){var rows=state().getAsJsonArray("orders");if(rows==null)return null;for(int i=rows.size()-1;i>=0;i--){var o=rows.get(i).getAsJsonObject();if(s(o,"action").equals(action))return o;}return null;}
    private static JsonObject lastQuote(){var q=last("quote");if(q==null||!q.has("receipt"))return null;var r=q.getAsJsonObject("receipt");return s(r,"quote").equals(previous)?null:r;}
    private static boolean acked(String asset){var rows=state().getAsJsonArray("orders");if(rows==null)return false;for(var row:rows){var o=row.getAsJsonObject();if(s(o,"action").equals("grant_ack")&&s(o,"state").equals("DONE")&&o.has("receipt")&&s(o.getAsJsonObject("receipt"),"asset").equals(asset))return true;}return false;}
    private static long count(UUID id){long n=0;for(var l:server.getAllLevels())for(var ent:l.getAllEntities())if(ent.getUUID().equals(id)&&!ent.isRemoved())n++;return n;}
    private static JsonObject product(){return UmamusumeCatalog.offers().get(index).getAsJsonObject();}
    private static void request(){previous=quote;p.setPos(2.5+index*4,65,5.5);rooms.action(p,"tradeQuoteBuy",s(product(),"sku"));stage=1;}
    @SubscribeEvent(priority=EventPriority.LOWEST) public static void tick(ServerTickEvent.Post e){if(server==null||done)return;try{
        ticks++;if(ticks%40==0){GameRuntime.get(server).platform.refresh(p);trade.refresh(p);var progress=new JsonObject();progress.addProperty("stage",stage);progress.addProperty("index",index);progress.add("trade",state());Files.writeString(Path.of("trade-progress.json"),progress.toString());}
        if(ticks>4000)throw new IllegalStateException("Bounded native deadline at stage "+stage+" product "+index);
        switch(stage){
            case 0->{if(!GameRuntime.get(server).platform.allowed(p)||!state().has("service"))return;check(state().getAsJsonObject("service").get("balance").getAsInt()==10000,"existing platform guard and isolated unified points admitted");request();}
            case 1->{var q=lastQuote();if(q==null)return;quote=s(q,"quote");check(q.get("amount").getAsInt()==product().get("purchase").getAsInt()&&q.getAsJsonObject("body").get("affixes").equals(product().get("affixes")),s(product(),"name")+": fixed price and affixes visible before debit");rooms.action(p,"tradeConfirm",quote);rooms.action(p,"tradeConfirm",quote);stage=2;}
            case 2->{var buy=last("buy");if(buy==null||!buy.has("receipt"))return;var r=buy.getAsJsonObject("receipt");if(!s(r.getAsJsonObject("body"),"sku").equals(s(product(),"sku")))return;var id=UUID.fromString(s(r,"asset"));if(!acked(id.toString()))return;
                check(count(id)==1,s(product(),"name")+": duplicate confirm and lost acknowledgements yield exactly one native entity");var maid=(EntityMaid)server.overworld().getEntity(id);UmamusumeCompanion.verify(maid,p.getUUID(),product());check(!HorseTrading.locked(maid)&&!maid.isNoAi()&&!maid.isInvulnerable()&&!maid.isNoGravity(),s(product(),"name")+": actual TLM owner, YSM ID, Champion tier and affixes; delivery unlocked");maid.setNoAi(true);maid.setInvulnerable(true);
                var saved=product().deepCopy();saved.addProperty("actualEntity",id.toString());delivered.add(saved);index++;if(index<6){request();return;}stage=3;
            }
            case 3->{var first=delivered.get(0).getAsJsonObject();var maid=(EntityMaid)server.overworld().getEntity(UUID.fromString(s(first,"actualEntity")));var film=ItemFilm.maidToFilm(maid);check(!film.isEmpty(),"actual TLM maidToFilm generated a native film");maid.discard();p.setPos(35.5,65,5.5);ItemFilm.filmToMaid(film,server.overworld(),new BlockPos(34,65,5),p);stage=4;}
            case 4->{EntityMaid revived=null;for(var entity:server.overworld().getAllEntities())if(entity instanceof EntityMaid m&&!m.isRemoved()&&m.getX()>33&&m.getX()<36){revived=m;break;}if(revived==null)return;
                if(ticks%20!=0)return;var first=delivered.get(0).getAsJsonObject();UmamusumeCompanion.verify(revived,p.getUUID(),first);check(true,"actual native film revival preserves owner, approved YSM ID, fixed Champion tier and all affixes");revived.setNoAi(true);revived.setInvulnerable(true);first.addProperty("actualEntity",revived.getUUID().toString());
                check(original.isAlive()&&original.getOwnerUUID().equals(p.getUUID()),"unrelated original horse unchanged");System.setProperty("muxi.horse.tradeQa.pauseDelivery","true");index=0;previous=quote;p.setPos(30.5,65,5.5);rooms.action(p,"tradeQuoteBuy",s(product(),"sku"));stage=5;
            }
            case 5->{var q=lastQuote();if(q==null)return;quote=s(q,"quote");rooms.action(p,"tradeConfirm",quote);stage=6;}
            case 6->{var buy=last("buy");if(buy==null||!buy.has("receipt")||!s(buy,"state").equals("RECEIVED"))return;pending=s(buy.getAsJsonObject("receipt"),"asset");check(count(UUID.fromString(pending))==0,"paid seventh order durably paused before entity birth for actual JVM restart");var saved=new JsonObject();saved.add("delivered",delivered);saved.addProperty("pending",pending);saved.addProperty("realm",trade.realm());saved.addProperty("originalHorse",original.getUUID().toString());Files.writeString(Path.of("restart-fixture.json"),saved.toString());finish(true,"");}
            case 90->{var id=UUID.fromString(s(resumed,"pending"));if(!acked(id.toString()))return;check(trade.realm().equals(s(resumed,"realm")),"realm and order identity survive actual JVM restart");check(count(id)==1,"actual restart delivers pending paid maid exactly once");var pendingMaid=(EntityMaid)server.overworld().getEntity(id);UmamusumeCompanion.verify(pendingMaid,p.getUUID(),UmamusumeCatalog.offers().get(0).getAsJsonObject());check(!HorseTrading.locked(pendingMaid),"recovered pending delivery acknowledges and unlocks");
                for(var item:resumed.getAsJsonArray("delivered")){var b=item.getAsJsonObject();var actual=UUID.fromString(s(b,"actualEntity"));check(count(actual)==1,s(b,"name")+": saved native entity loaded once");UmamusumeCompanion.verify((EntityMaid)server.overworld().getEntity(actual),p.getUUID(),b);check(true,s(b,"name")+": native JVM reload retains owner, YSM and fixed Champions");}
                check(count(UUID.fromString(s(resumed,"originalHorse")))==1,"original unrelated horse survives actual restart");finish(true,"");}
        }
    }catch(Throwable failure){finish(false,failure.toString());}}
    private static void finish(boolean success,String error){if(done)return;done=true;try{var report=new JsonObject();report.addProperty("success",success);report.addProperty("error",error);report.addProperty("stage",stage);report.addProperty("ticks",ticks);report.add("checks",checks);report.add("delivered",delivered);if(trade!=null)report.add("nativeJournal",trade.evidence());Files.writeString(Path.of("trade-result.json"),new GsonBuilder().setPrettyPrinting().create().toJson(report));}catch(Exception ignored){}server.halt(false);}
}
