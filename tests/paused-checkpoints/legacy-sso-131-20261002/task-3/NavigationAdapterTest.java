package net.muxigame.terminal.client;
import com.cinemamod.mcef.*;
import net.minecraft.client.Minecraft;
import net.muxigame.core.client.TerminalPassportApi;
import org.cef.browser.*;
import org.cef.handler.CefLoadHandler.ErrorCode;
public final class NavigationAdapterTest {
 static final String HOME="mod://muxi_terminal/terminal/index.html", ACCOUNT="https://mc.muxigame.com/account.html";
 static final String ENTRY="https://mc.muxigame.com/api/v1/auth/terminal", POST=ENTRY+"/exchange";
 static final String BODY="{\"ticket\":\"synthetic-one-use-fixture\",\"verifier\":\"synthetic-native-fixture\",\"requestId\":\"fixture\"}";
 static int passed;static MCEFClient client;static Minecraft mc=Minecraft.getInstance();
 static void check(String name,boolean value){if(!value)throw new AssertionError(name);passed++;}
 static void reset(){mc.queue.clear();mc.player=new Object();mc.connection=new Object();mc.screen=new TerminalScreen();
  TerminalBrowserSession.shell=new MCEFBrowser(1,HOME);TerminalBrowserSession.serial=0;TerminalBrowserSession.home();TerminalBrowserSession.error="";
  client=new MCEFClient();TerminalPassportNavigation.install(client);TerminalPassportApi.callback=null;TerminalPassportApi.validation=null;TerminalPassportApi.requests=0;}
 static void reply(String body){TerminalPassportApi.callback.accept(body);mc.flush();}
 static MCEFBrowser ready(){TerminalPassportNavigation.open();reply(BODY);return TerminalBrowserSession.content();}
 static void load(MCEFBrowser b,String url,int status){b.url=url;client.handler.onLoadEnd(b,new CefFrame(url,true),status);mc.flush();}
 static void shell(){check("shell persistent and never posts",TerminalBrowserSession.current().getURL().equals(HOME) && TerminalBrowserSession.current().requests.isEmpty() && TerminalBrowserSession.current().navigations.isEmpty());}
 static void unavailable(MCEFBrowser a){check("native error instead of interactive login",a.getURL().equals(TerminalPassportNavigation.ERROR) && !TerminalBrowserSession.error.isEmpty());shell();}
 static void validate(MCEFBrowser a)throws Exception{var generation=TerminalPassportNavigation.class.getDeclaredField("generation");generation.setAccessible(true);
  var method=TerminalPassportNavigation.class.getDeclaredMethod("validate",MCEFBrowser.class,long.class);method.setAccessible(true);method.invoke(null,a,generation.getLong(null));}
 public static void main(String[] args)throws Exception{
  reset();TerminalBrowserSession.beginLaunch("22");MCEFBrowser a=ready();
  check("separate account child and carried launch token",a!=TerminalBrowserSession.current() && TerminalBrowserSession.state().launchToken().equals("22"));
  client.handler.onLoadEnd(a,new CefFrame(ENTRY,true),200);check("CEF callback queues to game thread",a.requests.isEmpty());mc.flush();
  check("one native POST",a.requests.size()==1);var request=a.requests.getFirst();
  check("fixed method and destination",request.getURL().equals(POST) && request.method.equals("POST"));
  check("body only",new String(request.data.element.bytes,java.nio.charset.StandardCharsets.UTF_8).equals(BODY));
  check("fixed origin and action",request.headers.get("Origin").equals("https://mc.muxigame.com") && request.headers.get("X-Muxi-Terminal-Action").equals("1"));
  check("JSON and no URL secrets",request.headers.get("Content-Type").equals("application/json") && !request.getURL().contains("synthetic"));
  load(a,ACCOUNT,200);check("normal account uses original child",TerminalBrowserSession.content()==a);shell();
  reset();a=ready();load(a,ENTRY,200);load(a,ENTRY,200);check("duplicate load cannot post twice",a.requests.size()==1);
  load(a,POST,401);check("expired exchange asks for fresh ticket",TerminalPassportApi.requests==2);reply(BODY);load(a,ENTRY,200);
  check("retry remains same account child",a.requests.size()==2 && TerminalBrowserSession.content()==a);load(a,POST,401);unavailable(a);
  reset();a=ready();load(a,ENTRY,200);load(a,ACCOUNT,200);
  check("expired website redirect intercepted",TerminalPassportNavigation.beforeBrowse(a,new CefFrame(ACCOUNT,true),"https://mc.muxigame.com/api/v1/auth/entry?return_to=%2Faccount.html"));mc.flush();reply(BODY);load(a,ENTRY,200);load(a,ACCOUNT,200);
  check("silent renewal keeps child, shell and normal account",a.requests.size()==2 && a.getURL().equals(ACCOUNT));shell();
  check("OAuth redirect intercepted",TerminalPassportNavigation.beforeBrowse(a,new CefFrame(ACCOUNT,true),"https://account.muxigame.com/oauth/authorize"));mc.flush();reply("");unavailable(a);
  reset();a=ready();load(a,ENTRY,200);load(a,ACCOUNT,200);validate(a);TerminalPassportApi.validation.accept(false);mc.flush();unavailable(a);
  reset();a=ready();load(a,ENTRY,200);load(a,ACCOUNT,200);validate(a);TerminalPassportApi.validation.accept(true);mc.flush();check("valid identity check does not reload edits",a.requests.size()==1 && a.getURL().equals(ACCOUNT));
  validate(a);var validation=TerminalPassportApi.validation;TerminalBrowserSession.home();validation.accept(false);mc.flush();check("late heartbeat cannot replace newer view",TerminalBrowserSession.content()==null);shell();
  reset();TerminalPassportNavigation.open();var old=TerminalPassportApi.callback;TerminalBrowserSession.home();old.accept(BODY);mc.flush();check("cancelled callback rejected",TerminalBrowserSession.content()==null);shell();
  reset();TerminalPassportNavigation.open();mc.connection=new Object();reply(BODY);check("reconnect rejected",TerminalBrowserSession.content()==null);shell();
  reset();a=ready();var impostor=new MCEFBrowser(a.id,ENTRY);client.handler.onLoadEnd(impostor,new CefFrame(ENTRY,true),200);mc.flush();check("reused CEF id has no authority",impostor.requests.isEmpty() && a.requests.isEmpty());load(a,ENTRY,200);check("real child still posts",a.requests.size()==1);
  reset();a=ready();TerminalBrowserSession.openAccountView(ACCOUNT);load(a,ENTRY,200);check("replacement rejects late POST",a.requests.isEmpty());
  reset();a=ready();TerminalBrowserSession.snapshot=new TerminalBrowserSession.State(TerminalBrowserSession.Kind.ACCOUNT,99,"");load(a,ENTRY,200);check("view id enforced",a.requests.isEmpty());
  reset();a=ready();client.handler.onLoadEnd(a,new CefFrame(ENTRY,false),200);mc.flush();check("iframe cannot post",a.requests.isEmpty());
  check("iframe cannot request silent renewal",!TerminalPassportNavigation.beforeBrowse(a,new CefFrame(ACCOUNT,false),"https://account.muxigame.com/oauth/authorize"));
  load(a,ENTRY,200);check("main frame posts",a.requests.size()==1);
  reset();a=ready();a.url="https://attacker.example";client.handler.onLoadEnd(a,new CefFrame(ENTRY,true),200);mc.flush();check("changed origin rejects payload",a.requests.isEmpty());unavailable(a);
  reset();a=ready();var expiry=TerminalPassportNavigation.class.getDeclaredField("expires");expiry.setAccessible(true);expiry.setLong(null,System.nanoTime()-1);load(a,ENTRY,200);check("expired native payload refreshed without POST",a.requests.isEmpty() && TerminalPassportApi.requests==2);reply(BODY);load(a,ENTRY,200);check("fresh retry posts",a.requests.size()==1);
  reset();a=ready();mc.screen=new Object();load(a,ENTRY,200);check("closed terminal cannot POST",a.requests.isEmpty());unavailable(a);
  reset();a=ready();mc.connection=new Object();load(a,ENTRY,200);check("disconnected listener cannot POST",a.requests.isEmpty());unavailable(a);
  reset();a=ready();client.handler.onLoadEnd(a,new CefFrame(ENTRY,true),200);TerminalBrowserSession.home();mc.flush();check("queued completion after home ignored",a.requests.isEmpty());
  reset();a=ready();client.handler.onLoadError(a,new CefFrame(ENTRY,true),ErrorCode.ERR_ABORTED,"fixture",ENTRY);mc.flush();check("aborted navigation harmless",a.navigations.isEmpty());
  client.handler.onLoadError(a,new CefFrame(ENTRY,true),ErrorCode.ERR_FAILED,"fixture",ENTRY);mc.flush();unavailable(a);
  reset();TerminalPassportNavigation.open();reply("");unavailable(TerminalBrowserSession.content());
  reset();a=ready();load(a,ENTRY,200);load(a,ACCOUNT,200);TerminalBrowserSession.home();TerminalBrowserSession.shell=new MCEFBrowser(8,HOME);a=ready();load(a,ENTRY,200);check("CEF rebuild obtains a new native exchange",TerminalPassportApi.requests==2 && a.requests.size()==1);shell();
  reset();a=ready();load(a,ENTRY,200);load(a,ACCOUNT,200);TerminalBrowserSession.home();TerminalPassportNavigation.open();reply("");unavailable(TerminalBrowserSession.content());
  check("normal account page is not interactive auth",!TerminalPassportNavigation.interactiveAuth(ACCOUNT));
  check("external lookalike cannot trigger auth",!TerminalPassportNavigation.interactiveAuth("https://attacker.example/api/v1/auth/entry"));
  System.out.println("PASS: current-source native renewal/lifecycle assertions="+passed);
 }
}
