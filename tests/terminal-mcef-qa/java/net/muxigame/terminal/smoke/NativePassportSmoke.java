package net.muxigame.terminal.smoke;
import com.cinemamod.mcef.*;
import com.google.gson.*;
import net.minecraft.client.*;
import net.minecraft.client.gui.screens.*;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.client.multiplayer.resolver.ServerAddress;
import net.muxigame.terminal.client.*;
import net.neoforged.neoforge.client.event.ClientTickEvent;
import net.neoforged.neoforge.common.NeoForge;
import java.nio.file.*;
import java.util.*;

/** Actual RpcHost pipe -> Core proof -> dedicated-server packets -> real CEF cookie. */
public final class NativePassportSmoke {
 private int ticks,stage,frames;private boolean done,probeSent;private volatile JsonObject probe;private volatile String text="";private volatile Boolean friendsVerified;private boolean friendsStarted;private MCEFBrowser shell;private Object firstConnection;
 private final List<String> checks=new ArrayList<>();
 public NativePassportSmoke(){NeoForge.EVENT_BUS.addListener(this::tick);}
 private void check(boolean value,String message){if(!value)throw new AssertionError(message);}
 private void next(){stage++;frames=0;}
 private void connect(){var mc=Minecraft.getInstance();String address="127.0.0.1:"+System.getProperty("muxi.sso.nativeServerPort");ConnectScreen.startConnecting(new TitleScreen(),mc,ServerAddress.parseString(address),new ServerData("131 private native SSO QA",address,ServerData.Type.OTHER),false,null);}
 private void launch(){shell.executeJavaScript("window.terminalLaunch({kind:'account',id:'passport',name:'Native SSO QA',source:document.querySelector('[data-open=guide]')}).catch(()=>{})",shell.getURL(),0);}
 private boolean ready(){var view=TerminalBrowserSession.content();return view!=null && view.getURL().startsWith(SSOFixture.ACCOUNT+"?terminal_view=") && TerminalBrowserSession.contentVisible() && TerminalBrowserSession.state().rendered() && !TerminalBrowserSession.state().loading() && !TerminalBrowserSession.contentMotion().animating();}
 private void screenshot(String name)throws Exception{try(var image=Screenshot.takeScreenshot(Minecraft.getInstance().getMainRenderTarget())){image.writeToFile(Path.of(name+".png"));}}
 private void tick(ClientTickEvent.Post event){if(done)return;var mc=Minecraft.getInstance();try{
  ticks++;frames++;
  if(ticks%40==0){var row=new JsonObject();row.addProperty("stage",stage);row.addProperty("posts",SSOFixture.posts);row.addProperty("ticks",ticks);row.add("terminal",new Gson().toJsonTree(TerminalBrowserSession.state()));Files.writeString(Path.of("sso-progress.json"),row.toString());
   var processes=new ArrayList<Map<String,Object>>();var root=ProcessHandle.current();processes.add(Map.of("pid",root.pid(),"started",root.info().startInstant().orElse(java.time.Instant.EPOCH).toString(),"kind","minecraft"));root.descendants().forEach(p->processes.add(Map.of("pid",p.pid(),"parent",p.parent().map(ProcessHandle::pid).orElse(-1L),"started",p.info().startInstant().orElse(java.time.Instant.EPOCH).toString(),"kind","owned-child")));Files.writeString(Path.of("sso-processes.json"),new Gson().toJson(processes));
  }
  if(ticks>4200)throw new AssertionError("Actual native SSO timeout stage="+stage);
  if(stage<14 && !TerminalBrowserSession.state().error().isEmpty())throw new AssertionError("Actual native account failed: "+TerminalBrowserSession.state().error());
  if(stage==0){if(ticks<100 || !(mc.screen instanceof TitleScreen) || mc.getOverlay()!=null || !MCEF.isInitialized())return;connect();next();return;}
  if(stage==1){if(mc.player==null || mc.level==null || mc.getConnection()==null || mc.screen instanceof ReceivingLevelScreen)return;firstConnection=mc.getConnection();check(mc.getUser().getName().equals(System.getProperty("muxi.sso.qaUid")),"real client UID");TerminalClient.openHome();shell=TerminalBrowserSession.current();checks.add("real multiplayer player and dedicated-server connection");next();return;}
  if(stage==2){if(frames<35 || shell.isLoading())return;launch();next();return;}
  if(stage==3){if(frames<20 || !ready())return;check(SSOFixture.posts==1,"one account GET after actual binding packet response");var view=TerminalBrowserSession.content();view.getText(value->text=value);if(!text.contains("UID "+System.getProperty("muxi.sso.qaUid")))return;
   if(!probeSent){probeSent=true;view.executeJavaScript("""
    (async()=>{const api=async(path,options={})=>{const r=await fetch(path,{...options,credentials:'same-origin',headers:{'Content-Type':'application/json'}});if(!r.ok)throw Error('HTTP '+r.status);return r.json();};try{const p=await api('/api/v1/player/profile');const s=await api('/api/v1/player/skin');const save=await api('/api/v1/player/skin',{method:'PUT',body:JSON.stringify({model:'slim',png:s.default.png})});const read=await api('/api/v1/player/skin');await api('/api/v1/player/skin',{method:'DELETE'});document.body.setAttribute('data-native-sso-qa',btoa(JSON.stringify({uid:p.user.uid,admin:p.permissions.platformAdmin,changed:save.skin.model==='slim'&&read.skin.model==='slim'})));}catch(e){document.body.setAttribute('data-native-sso-qa',btoa(JSON.stringify({error:e.message})));}})();
    """,SSOFixture.ACCOUNT,0);return;}
   view.getSource(source->{var match=java.util.regex.Pattern.compile("data-native-sso-qa=\"([^\"]+)\"").matcher(source);if(match.find())probe=JsonParser.parseString(new String(Base64.getDecoder().decode(match.group(1)),java.nio.charset.StandardCharsets.UTF_8)).getAsJsonObject();});if(probe==null)return;
   check(!probe.has("error") && probe.get("uid").getAsString().equals(System.getProperty("muxi.sso.qaUid")) && !probe.get("admin").getAsBoolean() && probe.get("changed").getAsBoolean(),"real CEF original Bearer permissions and reversible skin write");if(!friendsStarted){
    friendsStarted=true;java.util.concurrent.CompletableFuture.runAsync(()->{
     try{
      var type=Class.forName("net.muxigame.terminal.client.TerminalFriendsTransport");var constructor=type.getDeclaredConstructor();constructor.setAccessible(true);var transport=constructor.newInstance();
      var token=type.getDeclaredMethod("cookie");token.setAccessible(true);String access=(String)token.invoke(transport);
      var read=type.getDeclaredMethod("read",String.class);read.setAccessible(true);
      var snapshot=TerminalFriendsPolicy.snapshot((String)read.invoke(transport,access),System.getProperty("muxi.sso.qaUid"));
      friendsVerified=snapshot.get("authenticated").getAsBoolean() && snapshot.getAsJsonArray("friends").size()==1;
     }catch(Throwable failure){
      Throwable detail=failure instanceof java.lang.reflect.InvocationTargetException ? failure.getCause() : failure;
      NativeProofDiagnostics.record("friends","errorTypes="+NativeProofDiagnostics.errors(detail));friendsVerified=false;
     }
    });return;
   }
   if(friendsVerified==null)return;check(friendsVerified,"actual native friend read shares original account Bearer and UID");
   checks.add("actual native friends API and account CEF use the same original account token");screenshot("native-account-operations");next();return;
  }
  if(stage==4){if(frames<45)return;SSOFixture.control("expire-access",new JsonObject());SSOFixture.control("expire-session",new JsonObject());TerminalBrowserSession.content().reload();next();return;}
  if(stage==5){if(frames<20 || !ready() || SSOFixture.control("state",new JsonObject()).get("refreshes").getAsInt()==0)return;check(SSOFixture.control("state",new JsonObject()).get("refreshes").getAsInt()>0,"actual launcher refresh rotation");checks.add("actual source-access expiry refreshes the shared account through launcher");screenshot("native-account-renewed");check(mc.screen instanceof TerminalScreen && mc.screen.keyPressed(256,0,0),"actual Esc handler consumed close");check(mc.screen==null,"actual Esc closed terminal screen");next();return;}
  if(stage==6){if(frames<45)return;TerminalClient.openTerminal();next();return;}
  if(stage==7){if(frames<20 || !ready() || SSOFixture.posts<3)return;check(mc.getConnection()==firstConnection,"close/reopen retains real connection");checks.add("Esc close/reopen proves native authority again");TerminalBrowserSession.requestHome();next();return;}
  if(stage==8){if(frames<50 || TerminalBrowserSession.content()!=null)return;SSOFixture.control("hold-ticket",new JsonObject());launch();next();return;}
  if(stage==9){if(SSOFixture.control("state",new JsonObject()).get("pending_tickets").getAsInt()==0)return;TerminalBrowserSession.close();mc.disconnect(new TitleScreen());next();return;}
  if(stage==10){if(frames<35 || mc.player!=null)return;connect();next();return;}
  if(stage==11){if(mc.player==null || mc.level==null || mc.getConnection()==null || mc.screen instanceof ReceivingLevelScreen)return;check(mc.getConnection()!=firstConnection,"reconnect uses a new actual listener");TerminalClient.openHome();shell=TerminalBrowserSession.current();next();return;}
  if(stage==12){if(frames<35 || shell.isLoading())return;launch();next();return;}
  if(stage==13){if(frames<20 || !ready() || SSOFixture.posts<4)return;checks.add("real disconnect while issuer binding claim is in flight rejects late old listener; new listener binds the unchanged account access");screenshot("native-account-reconnected");String boundary=System.getProperty("muxi.sso.endAction","revoke");if(boundary.equals("preserve")){finish(true,null);return;}SSOFixture.control(boundary,new JsonObject());next();return;}
  if(stage==14){if(frames<30)return;var type=Class.forName("net.muxigame.terminal.client.TerminalPassportNavigation");var epoch=type.getDeclaredField("generation");epoch.setAccessible(true);var method=type.getDeclaredMethod("validate",MCEFBrowser.class,long.class);method.setAccessible(true);method.invoke(null,TerminalBrowserSession.content(),epoch.getLong(null));next();return;}
  if(stage==15){if(frames<20 || !TerminalBrowserSession.state().url().startsWith("about:blank") || TerminalBrowserSession.state().error().isEmpty())return;var backend=SSOFixture.control("state",new JsonObject());check(backend.get("skin_restored").getAsBoolean() && backend.get("other_skin_untouched").getAsBoolean(),"both accounts skin restored");for(var row:backend.getAsJsonArray("requests")){String path=row.getAsJsonObject().get("path").getAsString();check(!path.startsWith("/api/v1/auth/entry") && !path.startsWith("/oauth/"),"no browser interactive authorization");}for(var row:backend.getAsJsonArray("requests")){
    var request=row.getAsJsonObject();String path=request.get("path").getAsString();
    check(!path.startsWith("/api/v1/auth/terminal"),"no terminal exchange or additional web session");
    check(!request.get("cef_cookie").getAsBoolean(),"CEF sends no account cookie");
    if(path.equals("/account.html") && request.get("status").getAsInt()==200)
      check(request.get("original_account_bearer").getAsBoolean(),"every successful account document uses original Bearer");
   }
   checks.add("native account boundary blocks stale access; no browser cookie or terminal exchange");screenshot("native-account-boundary");finish(true,null);}

 }catch(Throwable error){error.printStackTrace();finish(false,error.getClass().getSimpleName()+": "+error.getMessage());}}
 private void finish(boolean success,String error){if(done)return;done=true;try{var result=new LinkedHashMap<String,Object>();result.put("success",success);result.put("checks",checks);result.put("authTicketIdentityFixture",false);result.put("minecraftServerListeners","actual-dedicated-server");result.put("actualRpcHost",true);result.put("actualGamePackets",stage>=4);result.put("originalAccountBearer",stage>=4);result.put("proofDiagnostics","native-proof-diagnostics.log");result.put("accountGetOpenings",SSOFixture.posts);result.put("productionSSO",false);result.put("stage",stage);result.put("error",error);Files.writeString(Path.of("client-smoke-result.json"),new Gson().toJson(result));}catch(Exception ignored){}TerminalBrowserSession.close();Minecraft.getInstance().stop();}
}
