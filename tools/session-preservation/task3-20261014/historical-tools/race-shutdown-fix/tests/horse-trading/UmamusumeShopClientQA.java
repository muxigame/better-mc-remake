package net.muxigame.minigames.horseqa;
import com.google.gson.*;import net.minecraft.client.*;import net.muxigame.minigames.horse.client.UmamusumePreviews;import net.muxigame.terminal.client.*;import com.cinemamod.mcef.MCEF;import org.cef.browser.*;import org.cef.handler.*;import org.cef.callback.*;
import net.neoforged.api.distmarker.Dist;import net.neoforged.fml.common.EventBusSubscriber;import net.neoforged.bus.api.*;import net.neoforged.neoforge.client.event.ClientTickEvent;
import java.nio.file.*;
/** Test-only CEF router observes the real APP; commands use its ordinary button handlers. */
@EventBusSubscriber(modid="muxi_horse_racing",value=Dist.CLIENT)
public final class UmamusumeShopClientQA {
 static String lastCapture="";static int stage;static long started,opened,last;static CefMessageRouter router;static volatile JsonObject dom;static boolean done;
 static Path root(){return Path.of(System.getProperty("qa.local.root"));}
 public static CefMessageRouter makeRouter(){return CefMessageRouter.create(new CefMessageRouter.CefMessageRouterConfig("horseQaQuery","horseQaCancel"),new CefMessageRouterHandlerAdapter(){public boolean onQuery(CefBrowser b,CefFrame f,long id,String request,boolean persistent,CefQueryCallback cb){try{dom=JsonParser.parseString(request).getAsJsonObject();Files.writeString(root().resolve("shop-dom-evidence.json"),new GsonBuilder().setPrettyPrinting().create().toJson(dom));cb.success("ok");}catch(Exception ex){cb.failure(1,ex.toString());}return true;}});}
 static void shot(String name)throws Exception{try(var png=Screenshot.takeScreenshot(Minecraft.getInstance().getMainRenderTarget())){png.writeToFile(root().resolve(name+".png"));}}
 @SubscribeEvent(priority=EventPriority.LOWEST) public static void render(ClientTickEvent.Post e){if(!Boolean.getBoolean("muxi.horse.shopClientQa")||done)return;var mc=Minecraft.getInstance();if(mc.level==null||mc.player==null)return;long now=System.currentTimeMillis();if(started==0)started=now;
  try{
   if(now-started>360000)throw new IllegalStateException("Bounded client QA timeout stage "+stage+"; previews="+UmamusumePreviews.evidence()+"; MCEF="+MCEF.isInitialized());
   if(stage==0){mc.player.getInventory().selected=8;if(now-last>3000){last=now;Files.writeString(root().resolve("shop-client-progress.json"),UmamusumePreviews.evidence().toString());}if(!UmamusumePreviews.evidence().get("completed").getAsBoolean()||!MCEF.isInitialized())return;shot("six-maids-world");Files.writeString(root().resolve("preview-evidence.json"),UmamusumePreviews.evidence().toString());
    TerminalClient.openApp("games");opened=now;stage=1;return;
   }
   if(stage==1&&now-opened>7000){var browser=TerminalBrowserSession.content();if(browser==null)return;browser.executeJavaScript(Files.readString(root().resolve("shop-client.js")),browser.getURL(),0);stage=2;return;}
   if(stage==2&&dom!=null){if(dom.has("error"))throw new IllegalStateException(dom.get("error").getAsString());if(dom.has("capture")&&!dom.get("capture").getAsString().equals(lastCapture)){lastCapture=dom.get("capture").getAsString();shot("shop-"+lastCapture);}if(dom.has("complete")&&dom.get("complete").getAsBoolean()){shot("shop-complete");var result=new JsonObject();result.addProperty("success",true);result.addProperty("actualMcefApp",true);result.addProperty("productionWrites",false);result.add("dom",dom);result.add("previews",UmamusumePreviews.evidence());Files.writeString(root().resolve("shop-client-result.json"),new GsonBuilder().setPrettyPrinting().create().toJson(result));done=true;}}
  }catch(Throwable failure){done=true;try{shot("shop-failure");var result=new JsonObject();result.addProperty("success",false);result.addProperty("error",failure.toString());result.add("previews",UmamusumePreviews.evidence());Files.writeString(root().resolve("shop-client-result.json"),result.toString());}catch(Exception ignored){} }
 }
}
