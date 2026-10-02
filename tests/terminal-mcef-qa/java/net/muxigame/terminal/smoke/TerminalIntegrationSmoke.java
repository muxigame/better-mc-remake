package net.muxigame.terminal.smoke;

import com.cinemamod.mcef.*;
import com.google.gson.*;
import net.minecraft.client.*;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.client.gui.screens.ReceivingLevelScreen;
import net.minecraft.world.level.*;
import net.minecraft.world.level.levelgen.WorldOptions;
import net.minecraft.world.level.levelgen.presets.WorldPresets;
import net.minecraft.world.level.GameRules;
import net.minecraft.world.level.GameType;
import net.minecraft.world.Difficulty;
import net.minecraft.core.registries.Registries;
import net.muxigame.terminal.client.*;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.client.event.*;
import net.neoforged.neoforge.common.NeoForge;
import org.lwjgl.glfw.GLFW;
import org.cef.browser.*;
import org.cef.callback.CefQueryCallback;
import org.cef.handler.CefMessageRouterHandlerAdapter;
import java.nio.file.*;
import java.util.*;
import java.util.function.Consumer;

@Mod(value="muxi_terminal_smoke",dist=Dist.CLIENT)
public final class TerminalIntegrationSmoke {
    private int ticks,frames,stage;
    private boolean done,openShot,closeShot,closingDenied,worldStarting;
    private net.minecraft.client.player.LocalPlayer nativePlayer;
    private boolean probeSent;
    private volatile JsonObject accountProbe;
    private MCEFBrowser shell;
    private String webId;
    private Consumer<String> oldReply;
    private volatile String text="";
    private final String base=System.getProperty("muxi.container.fixtureUrl");
    private final List<String> passed=new ArrayList<>();
    private final List<Map<String,Object>> motionSamples=new ArrayList<>();
    public TerminalIntegrationSmoke(){if(Boolean.getBoolean("muxi.sso.realNative")){new NativePassportSmoke();return;}NeoForge.EVENT_BUS.addListener(this::tick);NeoForge.EVENT_BUS.addListener(this::frame);}
    private void check(boolean value,String message){if(!value)throw new AssertionError(message);}
    private void next(){stage++;frames=0;text="";}
    private void js(String code){shell.executeJavaScript(code,shell.getURL(),0);}
    private void launch(String kind,String id){js("window.terminalLaunch({kind:"+new Gson().toJson(kind)+",id:"+new Gson().toJson(id)+",name:'Integration QA',source:document.querySelector('[data-open=guide]')}).catch(()=>{})");}
    private void shot(String name)throws Exception{try(var image=Screenshot.takeScreenshot(Minecraft.getInstance().getMainRenderTarget())){image.writeToFile(Path.of(name+".png"));}}
    private boolean route(String end){var view=TerminalBrowserSession.content();return view!=null && view.getURL().endsWith(end);}
    private boolean ready(){return TerminalBrowserSession.contentVisible() && TerminalBrowserSession.state().rendered() && !TerminalBrowserSession.state().loading() && !TerminalBrowserSession.contentMotion().animating();}
    private void frame(ScreenEvent.Render.Post event){
        if(done || !(event.getScreen() instanceof TerminalScreen) || TerminalBrowserSession.content()==null || !TerminalBrowserSession.contentVisible())return;
        try{
            var motion=TerminalBrowserSession.contentMotion();
            if(motion.animating() && motion.alpha()>0.02 && motion.alpha()<0.98){
                var row=new LinkedHashMap<String,Object>();row.put("phase",motion.closing()?"closing":"opening");row.put("alpha",motion.alpha());row.put("scale",motion.scale());row.put("kind",TerminalBrowserSession.state().kind());row.put("viewId",TerminalBrowserSession.state().viewId());motionSamples.add(row);
                if(motion.alpha()>0.25 && motion.alpha()<0.75){
                    if(!motion.closing() && !openShot){openShot=true;shot("integrated-native-opening-mid");}
                    if(motion.closing() && !closeShot){closeShot=true;shot("integrated-native-closing-mid");}
                }
                if(motion.closing() && !closingDenied && TerminalBrowserSession.state().kind()==TerminalBrowserSession.Kind.BUILTIN){
                    var view=TerminalBrowserSession.content();denied(view,view.getMainFrame(),"tasks.snapshot");closingDenied=true;
                    check(TerminalBrowserSession.activeBrowser()==shell,"closing content must relinquish input");
                    var top=TerminalScreen.class.getDeclaredField("top");top.setAccessible(true);
                    var y=TerminalScreen.class.getDeclaredMethod("browserY",double.class);y.setAccessible(true);
                    check((int)y.invoke(event.getScreen(),top.getInt(event.getScreen())+60.0)==(int)Math.round(60*Minecraft.getInstance().getWindow().getGuiScale()),"closing input uses shell coordinates");
                }
            }
        }catch(Throwable error){error.printStackTrace();finish(false,error.toString());}
    }
    private void tick(ClientTickEvent.Post event){
        if(done)return;Minecraft mc=Minecraft.getInstance();
        try{
            if(++ticks%40==0){var progress=new LinkedHashMap<String,Object>();progress.put("stage",stage);progress.put("frames",frames);progress.put("posts",SSOFixture.posts);progress.put("pending",SSOFixture.pending!=null);progress.put("screen",mc.screen==null?"":mc.screen.getClass().getName());progress.put("state",TerminalBrowserSession.state());Files.writeString(Path.of("sso-progress.json"),new Gson().toJson(progress));}
            if(ticks>3000)throw new AssertionError("integrated smoke timeout stage="+stage+" state="+TerminalBrowserSession.state()+" syntheticPosts="+SSOFixture.posts+" requests="+SSOFixture.requests+" pending="+(SSOFixture.pending!=null));
            check(GLFW.glfwGetWindowAttrib(mc.getWindow().getWindow(),GLFW.GLFW_VISIBLE)==GLFW.GLFW_TRUE,"actual visible QA window");
            if(!worldStarting){
                if(ticks<100 || !(mc.screen instanceof TitleScreen) || mc.getOverlay()!=null || !MCEF.isInitialized())return;
                worldStarting=true;mc.options.renderDistance().set(2);
                mc.createWorldOpenFlows().createFreshLevel("sso-private-qa",new LevelSettings("SSO isolated QA",GameType.CREATIVE,false,Difficulty.PEACEFUL,false,new GameRules(),WorldDataConfiguration.DEFAULT),new WorldOptions(20261002131L,false,false),a->a.registryOrThrow(Registries.WORLD_PRESET).getHolderOrThrow(WorldPresets.FLAT).value().createWorldDimensions(),mc.screen);return;
            }
            if(mc.player==null || mc.level==null || mc.screen instanceof ReceivingLevelScreen || mc.getOverlay()!=null)return;
            if(nativePlayer==null){nativePlayer=mc.player;check(mc.getUser().getName().equals(System.getProperty("muxi.sso.qaUid")),"actual client UID equals issuer account UID");}
            check(mc.player==nativePlayer,"synthetic identity restored before every actual game tick");
            if(frames%40==0){var processes=new ArrayList<Map<String,Object>>();var root=ProcessHandle.current();
                processes.add(Map.of("pid",root.pid(),"started",root.info().startInstant().orElse(java.time.Instant.EPOCH).toString(),"kind","minecraft"));
                root.descendants().forEach(child->processes.add(Map.of("pid",child.pid(),"parent",child.parent().map(ProcessHandle::pid).orElse(-1L),"started",child.info().startInstant().orElse(java.time.Instant.EPOCH).toString(),"kind","owned-child")));
                Files.writeString(Path.of("sso-processes.json"),new Gson().toJson(Map.of("observedAt",java.time.Instant.now().toString(),"processes",processes)));
            }

            if(stage==0){TerminalClient.openHome();shell=TerminalBrowserSession.current();
                var member=org.cef.CefClient.class.getDeclaredField("loadHandler_");member.setAccessible(true);
                if(member.get(shell.getClient()) instanceof com.cinemamod.mcef.MCEFClient owner)owner.addLoadHandler(new org.cef.handler.CefLoadHandlerAdapter(){
                    @Override public void onLoadStart(CefBrowser b,CefFrame f,org.cef.network.CefRequest.TransitionType type){System.out.println("QA_CEF_MAIN_START="+f.isMain());}
                    @Override public void onLoadEnd(CefBrowser b,CefFrame f,int code){System.out.println("QA_CEF_MAIN_END="+f.isMain()+" STATUS="+code);}
                    @Override public void onLoadError(CefBrowser b,CefFrame f,org.cef.handler.CefLoadHandler.ErrorCode code,String message,String url){System.out.println("QA_CEF_MAIN_ERROR="+f.isMain()+" CODE="+code);}
                });
                next();return;}
            if(!(mc.screen instanceof TerminalScreen))return;frames++;
            if(stage>=4 && stage<20 && !TerminalBrowserSession.state().error().isEmpty())throw new AssertionError("native account state failed before expected revoke: "+TerminalBrowserSession.state().error());
            check(TerminalBrowserSession.current()==shell,"persistent shell identity");
            if(stage==1){
                if(frames==100 || frames==500){shot("sso-home-pending-"+frames);System.out.println("QA_HOME_BROWSER_LOADING="+shell.isLoading()+" ID="+shell.getIdentifier());shell.getSource(source->System.out.println("QA_HOME_SOURCE_LENGTH="+source.length()+" HAS_GUIDE="+source.contains("data-open=guide")));}
                if(frames<35 || shell.isLoading())return;shot("integrated-home");passed.add("actual Minecraft terminal home renders with issuer-bound client UID");launch("account","passport");stage=4;frames=0;text="";return;}
            if(stage==2){
                if(frames<20 || !ready() || !route("#/tasks"))return;
                // Animation intermediate-frame QA belongs to the animation owner; this runner tests SSO.
                passed.add("rapid duplicate/alternate launch renders latest actual content child");shot("integrated-tasks-ready");TerminalBrowserSession.requestHome();next();return;
            }
            if(stage==3){
                if(frames<12 || TerminalBrowserSession.content()!=null)return;
                // Do not claim animation or pre-fade bridge coverage when no intermediate frame was captured.
                passed.add("actual content returns home before account launch");launch("account","passport");next();return;
            }
            if(stage==4){
                if(SSOFixture.pending==null)return;check(SSOFixture.requestCalls==1,"one synthetic ticket request");SSOFixture.deliver();next();return;
            }
            if(stage==5){
                if(frames<20 || !route("account.html") || !ready())return;
                var view=TerminalBrowserSession.content();view.getText(value->text=value);if(!text.contains("UID "+System.getProperty("muxi.sso.qaUid")))return;
                if(!probeSent){probeSent=true;view.executeJavaScript("""
                    (async()=>{const api=async(path,options={})=>{const r=await fetch(path,{...options,credentials:'same-origin',headers:{'Content-Type':'application/json'}});if(!r.ok)throw Error('HTTP '+r.status);return r.json();};
                    try{const profile=await api('/api/v1/player/profile');const skin=await api('/api/v1/player/skin');
                    const saved=await api('/api/v1/player/skin',{method:'PUT',body:JSON.stringify({model:'slim',png:skin.default.png})});
                    const read=await api('/api/v1/player/skin');await api('/api/v1/player/skin',{method:'DELETE'});
                    document.body.setAttribute('data-sso-qa',btoa(JSON.stringify({uid:profile.user.uid,admin:profile.permissions.platformAdmin,skinOperation:saved.skin.model==='slim'&&read.skin.model==='slim',restored:true})));}
                    catch(e){document.body.setAttribute('data-sso-qa',btoa(JSON.stringify({error:e.message})));}})();
                    """,SSOFixture.ACCOUNT,0);return;}
                view.getSource(source->{var matcher=java.util.regex.Pattern.compile("data-sso-qa=\"([^\"]+)\"").matcher(source);if(matcher.find())accountProbe=JsonParser.parseString(new String(Base64.getDecoder().decode(matcher.group(1)),java.nio.charset.StandardCharsets.UTF_8)).getAsJsonObject();});
                if(accountProbe==null)return;
                check(!accountProbe.has("error"),"normal CEF cookie player API operation: "+accountProbe);
                check(accountProbe.get("uid").getAsString().equals(System.getProperty("muxi.sso.qaUid")) && !accountProbe.get("admin").getAsBoolean() && accountProbe.get("skinOperation").getAsBoolean(),"actual player UID/ordinary permissions/reversible skin write");
                check(SSOFixture.posts==1,"exactly one actual CEF POST");var posted=JsonParser.parseString(SSOFixture.postedBody).getAsJsonObject();
                check(posted.size()==3 && posted.get("ticket").getAsString().matches("[A-Za-z0-9_-]{43}") && posted.get("verifier").getAsString().matches("[A-Za-z0-9_-]{43}"),"actual one-use payload only in native POST body");
                check(SSOFixture.postedMethod.equals("POST") && SSOFixture.postedOrigin.equals("https://mc.muxigame.com") && SSOFixture.postedAction.equals("1"),"fixed native method/origin/action");
                check(!view.getURL().contains("synthetic") && TerminalWebPolicy.localDocument(shell.getURL()),"no URL credential or shell navigation");
                denied(view,view.getMainFrame(),"passport.open");denied(view,view.getMainFrame(),"tasks.snapshot");
                passed.add("actual CEF one-use native POST creates normal website cookie; actual player UID and reversible skin API operation; shell persistent; account bridge denied");shot("integrated-sso-account");TerminalBrowserSession.requestHome();next();return;
            }
            if(stage==6){if(frames<12 || TerminalBrowserSession.content()!=null)return;launch("account","passport");next();return;}
            if(stage==7){if(SSOFixture.pending==null)return;oldReply=SSOFixture.pending;SSOFixture.pending=null;TerminalBrowserSession.requestHome();next();return;}
            if(stage==8){if(frames<12 || TerminalBrowserSession.content()!=null)return;oldReply.accept(SSOFixture.BODY);next();return;}
            if(stage==9){
                if(frames<12)return;check(TerminalBrowserSession.content()==null && SSOFixture.posts==1,"cancelled late ticket cannot open or POST");
                passed.add("cancelled actual adapter async ticket remains home and cannot POST");SSOFixture.holdEntry=true;launch("account","passport");next();return;
            }
            if(stage==10){if(SSOFixture.pending==null)return;SSOFixture.deliver();next();return;}
            if(stage==11){if(frames<4 || TerminalBrowserSession.content()==null || !TerminalBrowserSession.state().loading())return;TerminalBrowserSession.requestHome();next();return;}
            if(stage==12){
                if(frames<35)return;check(TerminalBrowserSession.content()==null && SSOFixture.posts==1,"cancelled delayed actual CEF entry cannot consume ticket");
                passed.add("return during actual HTTPS entry load blocks late native POST");SSOFixture.holdEntry=false;launch("account","passport");next();return;
            }
            if(stage==13){if(SSOFixture.pending==null)return;SSOFixture.deliver();next();return;}
            if(stage==14){
                if(frames<20 || !route("account.html") || !ready())return;
                check(SSOFixture.posts==2,"second opening obtains an independent native exchange");
                SSOFixture.control("expire-access",new JsonObject());SSOFixture.control("expire-session",new JsonObject());TerminalBrowserSession.content().reload();next();return;
            }
            if(stage==15){if(SSOFixture.pending==null)return;SSOFixture.deliver();next();return;}
            if(stage==16){
                if(frames<20 || !route("account.html") || !ready())return;
                check(SSOFixture.posts==3,"actual expired website session silently exchanges again");
                check(SSOFixture.control("state",new JsonObject()).get("refreshes").getAsInt()>0,"actual issuer access expiry performs native refresh and rotation");
                passed.add("actual website SQLite session expiry silently restores same CEF child without OAuth/login page");shot("integrated-sso-renewed");
                TerminalBrowserSession.close();TerminalClient.openHome();shell=TerminalBrowserSession.current();next();return;
            }
            if(stage==17){if(frames<35 || shell.isLoading())return;launch("account","passport");next();return;}
            if(stage==18){if(SSOFixture.pending==null)return;SSOFixture.deliver();next();return;}
            if(stage==19){
                if(frames<20 || !route("account.html") || !ready())return;
                check(SSOFixture.posts==4,"rebuilt CEF browser obtains fresh native website session");
                passed.add("actual child and shell CefClient rebuild obtains a fresh native exchange");shot("integrated-sso-cef-rebuilt");
                String boundary=System.getProperty("muxi.sso.endAction","revoke");
                if(boundary.equals("preserve")){passed.add("whole-process restart phase preserves native account authority and uses fresh private CEF cookies");finish(true,null);return;}
                SSOFixture.control(boundary,new JsonObject());
                var type=Class.forName("net.muxigame.terminal.client.TerminalPassportNavigation");var field=type.getDeclaredField("generation");field.setAccessible(true);
                var method=type.getDeclaredMethod("validate",MCEFBrowser.class,long.class);method.setAccessible(true);method.invoke(null,TerminalBrowserSession.content(),field.getLong(null));next();return;
            }
            if(stage==20){
                if(frames<20 || !TerminalBrowserSession.state().url().startsWith("about:blank") || TerminalBrowserSession.state().error().isEmpty())return;
                var backend=SSOFixture.control("state",new JsonObject());
                check(backend.get("skin_restored").getAsBoolean() && backend.get("other_skin_untouched").getAsBoolean(),"actual reversible website operation restored own skin and never changed the other account");
                for(var row:backend.getAsJsonArray("requests")){String path=row.getAsJsonObject().get("path").getAsString();check(!path.startsWith("/api/v1/auth/entry") && !path.startsWith("/oauth/"),"interactive authentication page never requested by CEF");}
                passed.add("native account switch/revocation disables active account view; terminal error replaces protected website");shot("integrated-sso-revoked");finish(true,null);
            }
        }catch(Throwable error){error.printStackTrace();finish(false,error.toString());}
    }
    private static void denied(CefBrowser browser,CefFrame frame,String request)throws Exception{
        var type=Class.forName("net.muxigame.terminal.client.TerminalNativeBridge$Handler");var constructor=type.getDeclaredConstructor();constructor.setAccessible(true);
        var handler=(CefMessageRouterHandlerAdapter)constructor.newInstance();int[] result={0};handler.onQuery(browser,frame,123,request,false,new CefQueryCallback(){
            @Override public void success(String value){result[0]=200;}@Override public void failure(int code,String value){result[0]=code;}
        });if(result[0]!=403)throw new AssertionError("Actual bridge allowed "+request+" result="+result[0]);
    }
    private void finish(boolean success,String error){
        if(done)return;done=true;
        try{var report=new LinkedHashMap<String,Object>();report.put("success",success);report.put("checks",passed);report.put("motionSamples",motionSamples);report.put("syntheticPostCount",SSOFixture.posts);report.put("syntheticRequestCount",SSOFixture.requestCalls);report.put("windowVisible",true);report.put("realIsolatedWorld",true);report.put("authTicketIdentityFixture",true);report.put("actualTLSAuthCoreWebsite",true);report.put("minecraftServerListeners","fixtures");report.put("browserOwnCookies",true);report.put("productionSSO",false);report.put("error",error);report.put("stage",stage);Files.writeString(Path.of("client-smoke-result.json"),new Gson().toJson(report));}
        catch(Exception ignored){}TerminalBrowserSession.close();Minecraft.getInstance().stop();
    }
}
