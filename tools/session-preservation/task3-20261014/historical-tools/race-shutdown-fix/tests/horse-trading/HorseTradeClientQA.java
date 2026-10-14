package net.muxigame.minigames.horseclientqa;
import com.google.gson.*;
import net.minecraft.client.*;
import net.minecraft.client.gui.screens.*;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.Registries;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.*;
import net.minecraft.world.entity.animal.horse.Horse;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.item.*;
import net.minecraft.world.level.*;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.levelgen.*;
import net.minecraft.world.level.levelgen.presets.WorldPresets;
import net.minecraft.world.Difficulty;
import net.muxigame.minigames.*;
import net.muxigame.minigames.client.TerminalGamesApi;
import net.muxigame.minigames.horse.*;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.client.event.ClientTickEvent;
import java.lang.reflect.*;
import java.nio.file.*;
import java.util.*;

/** Chromium cards -> privileged bridge -> native packets -> real trade HTTP + horse rendering. */
@EventBusSubscriber(modid="muxi_horse_racing",value=Dist.CLIENT)
public final class HorseTradeClientQA {
    private static int ticks,stage,frames;private static boolean starting,closing,done,ready,raceDismissed;private static volatile String error="";
    private static Horse source;private static UUID sourceId,bought;private static String quote,oldQuote;private static final JsonArray checks=new JsonArray();
    private static void check(boolean yes,String label){if(!yes)throw new IllegalStateException(label);if(!checks.asList().contains(new JsonPrimitive(label)))checks.add(label);}
    private static String s(JsonObject o,String key){return o!=null&&o.has(key)?o.get(key).getAsString():"";}
    private static JsonObject game(){for(var e:JsonParser.parseString(TerminalGamesApi.snapshotJson()).getAsJsonObject().getAsJsonArray("games"))if(s(e.getAsJsonObject(),"id").equals(HorseRooms.ID))return e.getAsJsonObject();return null;}
    private static JsonObject trade(){var g=game();return g==null?null:g.getAsJsonObject("state").getAsJsonObject("trading");}
    private static JsonObject job(String action){var t=trade();if(t==null||!t.has("orders"))return null;var rows=t.getAsJsonArray("orders");for(int i=rows.size()-1;i>=0;i--){var j=rows.get(i).getAsJsonObject();if(s(j,"action").equals(action))return j;}return null;}
    private static JsonObject lastQuote(){var j=job("quote");if(j==null||!j.has("receipt"))return null;var q=j.getAsJsonObject("receipt");return s(q,"quote").equals(oldQuote)?null:q;}
    private static boolean finished(String action){return s(job(action),"state").equals("DONE");}
    private static JsonObject room(){var g=game();if(g==null)return null;var rows=g.getAsJsonObject("state").getAsJsonArray("rooms");return rows.isEmpty()?null:rows.get(0).getAsJsonObject();}
    private static JsonObject dom()throws Exception{var f=HorseCefQA.class.getDeclaredField("dom");f.setAccessible(true);return (JsonObject)f.get(null);}
    private static void js(String source)throws Exception{var m=HorseCefQA.class.getDeclaredMethod("js",String.class);m.setAccessible(true);m.invoke(null,source);var f=HorseCefQA.class.getDeclaredField("dom");f.setAccessible(true);f.set(null,null);}
    private static boolean action(String label,String title)throws Exception{
        var d=dom();if(d==null||!d.getAsJsonArray("actions").asList().stream().map(JsonElement::getAsJsonObject).anyMatch(b->s(b,"label").equals(label)&&!b.get("disabled").getAsBoolean()))return false;
        if(title!=null&&!d.get("text").getAsString().contains(title))return false;
        String filter=title==null?"[...document.querySelectorAll('#mg-content [data-mg-action]')]":"[...document.querySelectorAll('#mg-content .guide-card')].filter(c=>c.querySelector('h4')?.textContent.includes("+new Gson().toJson(title)+")).flatMap(c=>[...c.querySelectorAll('[data-mg-action]')])";
        js(filter+".find(b=>!b.disabled&&b.textContent.trim()==="+new Gson().toJson(label)+")?.click()");return true;
    }
    private static void serverTask(java.util.function.Consumer<ServerPlayer> task){var mc=Minecraft.getInstance();ready=false;UUID id=mc.player.getUUID();mc.getSingleplayerServer().execute(()->{try{task.accept(mc.getSingleplayerServer().getPlayerList().getPlayer(id));ready=true;}catch(Throwable f){error=f.toString();}});}
    private static void next(){stage++;frames=0;}
    private static String pendingCapture="";private static int captureTick;private static final Set<String> captures=new HashSet<>();
    private static boolean screenshot(String name)throws Exception{
        if(captures.contains(name))return true;
        if(name.startsWith("app-")){
            if(!name.equals(pendingCapture)){
                String title=switch(name){case "app-trade-catalog"->"Seabiscuit";case "app-trade-buy-confirm"->"购买确认";case "app-trade-sale-confirm"->"整匹出售确认";case "app-trade-rent-confirm"->"单场租赁确认";default->"";};
                String selection="[...document.querySelectorAll('#mg-content .guide-card')]";
                selection+=title.isEmpty()?".at(-1)":".find(c=>c.querySelector('h4')?.textContent.includes("+new Gson().toJson(title)+"))";
                js(selection+"?.scrollIntoView({block:'center',behavior:'instant'})");pendingCapture=name;captureTick=ticks;return false;
            }
            if(ticks-captureTick<5||dom()==null)return false;
        }
        try(var img=Screenshot.takeScreenshot(Minecraft.getInstance().getMainRenderTarget())){img.writeToFile(Path.of(name+".png"));}
        HorseCefQA.save(name);Files.writeString(Path.of(name+"-snapshot.json"),TerminalGamesApi.snapshotJson());captures.add(name);return true;
    }
    private static void finish(boolean success,String why)throws Exception{var report=new JsonObject();report.addProperty("success",success);report.addProperty("error",why);report.add("checks",checks);report.addProperty("mountedMaidDraws",HorseClientQA.mountedMaidDraws);report.addProperty("MCEF",true);report.addProperty("visualAppAcceptance",success);report.addProperty("mcefShutdownGuard",System.getProperty("muxi_terminal.mcef_shutdown_guard"));Files.writeString(Path.of("client-result.json"),new GsonBuilder().setPrettyPrinting().create().toJson(report));closing=true;HorseCefQA.close();var mc=Minecraft.getInstance();if(mc.level!=null)mc.level.disconnect();mc.disconnect(new TitleScreen());stage=99;frames=0;}
    @SubscribeEvent public static void tick(ClientTickEvent.Post event){if(!Boolean.getBoolean("muxi.horse.tradeClientQa")||done)return;var mc=Minecraft.getInstance();try{
        ticks++;if(stage==99){if(mc.level!=null||mc.getSingleplayerServer()!=null||++frames<40)return;done=true;mc.stop();return;}if(!error.isEmpty())throw new IllegalStateException(error);if(ticks>10000||Files.exists(Path.of("request-normal-close.json")))throw new IllegalStateException("Bounded client deadline stage "+stage);
        if(ticks%40==0){var progress=new JsonObject();progress.addProperty("stage",stage);progress.addProperty("ticks",ticks);progress.addProperty("maidDraws",HorseClientQA.mountedMaidDraws);Files.writeString(Path.of("client-progress.json"),progress.toString());}
        if(mc.getOverlay()!=null)return;
        if(!starting){if(!(mc.screen instanceof TitleScreen))return;check(Files.exists(Path.of("HORSE_QA_ONLY")),"owned hidden client and private world");starting=true;mc.createWorldOpenFlows().createFreshLevel("horse-trade-client-qa",new LevelSettings("Trade private QA",GameType.CREATIVE,false,Difficulty.PEACEFUL,false,new GameRules(),WorldDataConfiguration.DEFAULT),new WorldOptions(92345678L,false,false),a->a.registryOrThrow(Registries.WORLD_PRESET).getHolderOrThrow(WorldPresets.FLAT).value().createWorldDimensions(),mc.screen);return;}
        if(mc.player==null||mc.level==null||mc.screen instanceof ReceivingLevelScreen)return;frames++;HorseCefQA.poll(ticks);if(ticks%20==0)TerminalGamesApi.request();
        switch(stage){
            case 0 -> {Class<?> registry=Class.forName("com.elfmcys.yesstevemodel.oo0o0Oooo0OOO00OOo0OO000");Map<?,?> models=(Map<?,?>)registry.getMethod("oOoo00O0o0oO0o0oO00OO0O0").invoke(null);String model=models.keySet().stream().map(Object::toString).filter(k->k.contains("01_taisho_maid")).findFirst().orElse(null);if(model==null)return;Object definition=models.get(model).getClass().getMethod("oOo0OO0O0o000OO0O000oo0o").invoke(models.get(model));Map<?,?> textures=(Map<?,?>)definition.getClass().getMethod("oOOO00ooO0oOOoOOo0OoOOOo").invoke(definition);String texture=textures.keySet().stream().map(Object::toString).findFirst().orElseThrow();System.setProperty("muxi.horse.model",model);System.setProperty("muxi.horse.texture",texture);check(true,"installed Wine Fox model and texture resolved locally; interim rider selection");serverTask(p->{check(p.getGameProfile().getName().equals("10000"),"designated isolated UID on genuine integrated client connection");TrustedAccounts.admit(p);var l=p.server.overworld();for(int x=-8;x<10;x++)for(int z=-8;z<10;z++)l.setBlockAndUpdate(new BlockPos(x,64,z),Blocks.STONE.defaultBlockState());p.teleportTo(l,.5,65,.5,0,0);source=EntityType.HORSE.create(l);source.setPos(.5,65,.5);source.setTamed(true);source.setOwnerUUID(p.getUUID());source.setNoAi(true);source.setPersistenceRequired();source.setAge(0);source.setCustomName(net.minecraft.network.chat.Component.literal("自养原马甲"));source.getAttribute(Attributes.MOVEMENT_SPEED).setBaseValue(.3375);source.getAttribute(Attributes.JUMP_STRENGTH).setBaseValue(1);source.getAttribute(Attributes.MAX_HEALTH).setBaseValue(30);source.setHealth(30);source.equipSaddle(new ItemStack(Items.SADDLE),null);source.equipBodyArmor(p,new ItemStack(Items.DIAMOND_HORSE_ARMOR));l.addFreshEntity(source);sourceId=source.getUUID();p.startRiding(source,true);GameRuntime.get(p.server).platform.refresh(p);});next();}
            case 1 -> {if(!ready||game()==null||trade()==null)return;var app=JsonParser.parseString(TerminalGamesApi.snapshotJson()).getAsJsonObject();if(!app.has("platform")||!app.getAsJsonObject("platform").get("available").getAsBoolean())return;TerminalGamesApi.submit(HorseRooms.ID,"tradeRefresh","");next();}
            case 2 -> {if(trade()==null||!trade().has("service"))return;if(!TerminalGamesApi.openApp(HorseRooms.ID,"shop"))return;check("disabled:1".equals(System.getProperty("muxi_terminal.mcef_shutdown_guard")),"guarded existing MCEF; no global process shutdown");next();}
            case 3 -> {if(dom()==null)return;if(!dom().get("text").getAsString().contains("购买 120 分")){HorseCefQA.click("[data-mg-page='shop']");return;}check(dom().get("text").getAsString().contains("速度 0.17")&&dom().get("text").getAsString().contains("单场租 45 分"),"actual APP renders fixed attributes, purchase and rent prices");if(!screenshot("app-trade-catalog"))return;if(action("查看购买确认","Seabiscuit"))next();}
            case 4 -> {if(lastQuote()==null||dom()==null||!dom().get("text").getAsString().contains("确认金额 120 分"))return;check(true,"actual rendered purchase confirmation exposes price and traits before debit");if(!screenshot("app-trade-buy-confirm"))return;quote=s(lastQuote(),"quote");if(action("确认这笔交易","Seabiscuit"))next();}
            case 5 -> {if(!finished("grant_ack"))return;bought=UUID.fromString(s(job("buy").getAsJsonObject("receipt"),"asset"));serverTask(p->{var h=(Horse)p.server.overworld().getEntity(bought);check(h!=null&&h.isAlive()&&p.getUUID().equals(h.getOwnerUUID())&&!HorseTrading.locked(h),"CEF purchase delivers owned actual vanilla horse after lost acknowledgements");});next();}
            case 6 -> {if(!ready)return;if(!screenshot("app-trade-buy-delivered"))return;oldQuote=quote;if(action("为正骑的自养马报价",null))next();}
            case 7 -> {if(lastQuote()==null||dom()==null||!dom().get("text").getAsString().contains("确认金额 1320 分"))return;check(dom().get("text").getAsString().contains("整匹含现有鞍/铠出售"),"actual sale confirmation explains whole-horse ownership and equipment");quote=s(lastQuote(),"quote");if(!screenshot("app-trade-sale-confirm"))return;if(action("确认这笔交易","自养原马甲"))next();}
            case 8 -> {if(!finished("sale_commit"))return;if(!HorseCefQA.create())return;check(true,"actual APP sale commits once, then room wizard uses the same mutation gate");next();}
            case 9 -> {if(room()==null||!s(room(),"phase").equals("WAITING"))return;if(room().get("enemyTier").getAsInt()!=3){action("强敌 3 档",null);return;}if(!TerminalGamesApi.openApp(HorseRooms.ID,"shop"))return;oldQuote=quote;next();}
            case 10 -> {if(dom()==null)return;if(!dom().get("text").getAsString().contains("赛方马池")){HorseCefQA.click("[data-mg-page='shop']");return;}if(action("查看租赁确认","自养原马甲"))next();}
            case 11 -> {if(lastQuote()==null){if(frames>60){action("刷新积分与库存",null);stage=10;frames=0;}return;}if(dom()==null||!dom().get("text").getAsString().contains("确认金额 45 分"))return;quote=s(lastQuote(),"quote");if(!screenshot("app-trade-rent-confirm"))return;if(action("确认这笔交易","单场租赁确认"))next();}
            case 12 -> {if(!finished("rent"))return;if(!TerminalGamesApi.openApp(HorseRooms.ID,"lobby"))return;next();}
            case 13 -> {if(dom()==null)return;if(!dom().get("text").getAsString().contains("开始比赛")){HorseCefQA.click("[data-mg-open-room='horse_racing']");return;}if(action("开始比赛",null))next();}
            case 14 -> {if(room()==null)return;if(!s(room(),"phase").equals("WAITING")&&!raceDismissed){raceDismissed=true;HorseCefQA.dismiss();}if(s(room(),"phase").equals("RUNNING")&&frames%30==0){mc.player.setYRot(0);mc.player.setXRot(15);if(HorseClientQA.mountedMaidDraws>10)screenshot("native-trade-race");}if(!s(room(),"phase").equals("FINISHED")||!finished("rental_end"))return;check(HorseClientQA.mountedMaidDraws>10,"actual native client renders mounted YSM maid on rented vanilla horse");check(!job("rental_end").getAsJsonObject("receipt").get("refunded").getAsBoolean(),"completed actual race keeps agreed single-race rental fee");if(!TerminalGamesApi.openApp(HorseRooms.ID,"shop"))return;next();}
            case 15 -> {if(dom()==null||frames<25)return;if(!dom().get("text").getAsString().contains("报价与订单")){HorseCefQA.click("[data-mg-page='shop']");return;}if(!screenshot("app-trade-final"))return;finish(true,"");}
        }
    }catch(Throwable failure){try{finish(false,failure.toString());}catch(Exception ignored){error=failure.toString();}}}
}
