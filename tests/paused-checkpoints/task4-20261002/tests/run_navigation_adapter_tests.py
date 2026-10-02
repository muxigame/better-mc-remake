"""Execute the real adapter against deterministic native/container API fixtures.

These are lifecycle/protocol tests, not actual MCEF rendering or production SSO.
No application credential is issued, no key is generated, and no game is started.
"""
from pathlib import Path
import json
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"repos/muxi-terminal-container/src/main/java/net/muxigame/terminal/client/TerminalPassportNavigation.java"
JDK=Path(r"C:\Program Files\Java\jdk-24\bin")
FILES={
"com/cinemamod/mcef/MCEF.java":"package com.cinemamod.mcef; public final class MCEF {}",
"com/cinemamod/mcef/MCEFBrowser.java":"package com.cinemamod.mcef; public class MCEFBrowser extends org.cef.browser.CefBrowser { public MCEFBrowser(int id,String url){super(id,url);} }",
"com/cinemamod/mcef/MCEFClient.java":"package com.cinemamod.mcef; public class MCEFClient { public org.cef.handler.CefLoadHandlerAdapter handler; public void addLoadHandler(org.cef.handler.CefLoadHandlerAdapter h){handler=h;} }",
"org/cef/browser/CefBrowser.java":r'''package org.cef.browser;
public class CefBrowser {
 public int id; public String url; public java.util.List<String> navigations=new java.util.ArrayList<>();
 public java.util.List<org.cef.network.CefRequest> requests=new java.util.ArrayList<>();
 public CefBrowser(int id,String url){this.id=id;this.url=url;}
 public int getIdentifier(){return id;} public String getURL(){return url;}
 public void loadURL(String value){url=value;navigations.add(value);}
 public void loadRequest(org.cef.network.CefRequest value){requests.add(value);url=value.getURL();}
}''',
"org/cef/browser/CefFrame.java":"package org.cef.browser; public record CefFrame(String url,boolean main) { public String getURL(){return url;} public boolean isMain(){return main;} }",
"org/cef/handler/CefLoadHandler.java":"package org.cef.handler; public interface CefLoadHandler { enum ErrorCode {ERR_ABORTED,ERR_FAILED} }",
"org/cef/handler/CefLoadHandlerAdapter.java":r'''package org.cef.handler;
public class CefLoadHandlerAdapter {
 public void onLoadEnd(org.cef.browser.CefBrowser b,org.cef.browser.CefFrame f,int s){}
 public void onLoadError(org.cef.browser.CefBrowser b,org.cef.browser.CefFrame f,CefLoadHandler.ErrorCode c,String m,String u){}
}''',
"org/cef/network/CefPostDataElement.java":"package org.cef.network; public class CefPostDataElement { public byte[] bytes; public static CefPostDataElement create(){return new CefPostDataElement();} public void setToBytes(int count,byte[] value){bytes=value.clone();} }",
"org/cef/network/CefPostData.java":"package org.cef.network; public class CefPostData { public CefPostDataElement element; public static CefPostData create(){return new CefPostData();} public void addElement(CefPostDataElement value){element=value;} }",
"org/cef/network/CefRequest.java":r'''package org.cef.network;
public class CefRequest { public String url,method;public CefPostData data;public java.util.Map<String,String> headers;
 public static CefRequest create(){return new CefRequest();} public void setURL(String v){url=v;}
 public String getURL(){return url;} public void setMethod(String v){method=v;}
 public void setPostData(CefPostData v){data=v;} public void setHeaderMap(java.util.Map<String,String> v){headers=v;}
}''',
"net/minecraft/client/Minecraft.java":r'''package net.minecraft.client;
public final class Minecraft { private static final Minecraft INSTANCE=new Minecraft();
 public Object player,screen,connection; public java.util.Queue<Runnable> queue=new java.util.ArrayDeque<>();
 public static Minecraft getInstance(){return INSTANCE;} public Object getConnection(){return connection;}
 public void execute(Runnable r){queue.add(r);} public void flush(){while(!queue.isEmpty())queue.remove().run();}
}''',
"net/muxigame/core/client/TerminalPassportApi.java":r'''package net.muxigame.core.client;
public final class TerminalPassportApi { public static java.util.function.Consumer<String> callback;
 public static void request(java.util.function.Consumer<String> value){callback=value;} }
''',
"net/muxigame/terminal/client/TerminalScreen.java":"package net.muxigame.terminal.client; public final class TerminalScreen {}",
"net/muxigame/terminal/client/TerminalBrowserSession.java":r'''package net.muxigame.terminal.client;
public final class TerminalBrowserSession {
 public enum Kind {HOME,BUILTIN,WEB,ACCOUNT}
 public record State(Kind kind,long viewId,String launchToken){}
 public static com.cinemamod.mcef.MCEFBrowser shell,app; public static State snapshot;
 public static long gen,serial; public static com.cinemamod.mcef.MCEFBrowser current(){return shell;}
 public static com.cinemamod.mcef.MCEFBrowser content(){return app;} public static State state(){return snapshot;}
 public static long generation(){return gen;}
 public static void home(){TerminalPassportNavigation.clear();app=null;gen++;snapshot=new State(Kind.HOME,0,"");}
 public static void beginLaunch(String token){home();snapshot=new State(Kind.ACCOUNT,0,token);}
 public static com.cinemamod.mcef.MCEFBrowser openAccountView(String url){String token=snapshot.launchToken();home();app=new com.cinemamod.mcef.MCEFBrowser((int)++serial+100,url);snapshot=new State(Kind.ACCOUNT,serial,token);return app;}
 public static boolean openExternal(String url){openAccountView(url);return true;}
}''',
"net/muxigame/terminal/client/NavigationAdapterTest.java":r'''package net.muxigame.terminal.client;
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
  TerminalBrowserSession.shell=new MCEFBrowser(1,HOME);TerminalBrowserSession.serial=0;TerminalBrowserSession.home();
  client=new MCEFClient();TerminalPassportNavigation.install(client);TerminalPassportApi.callback=null;}
 static void reply(String body){TerminalPassportApi.callback.accept(body);mc.flush();}
 static MCEFBrowser ready(){TerminalPassportNavigation.open();reply(BODY);return TerminalBrowserSession.content();}
 static void load(MCEFBrowser b,String url,int status){b.url=url;client.handler.onLoadEnd(b,new CefFrame(url,true),status);mc.flush();}
 static void shell(){check("shell remains local",TerminalBrowserSession.current().getURL().equals(HOME));
  check("no native POST on shell",TerminalBrowserSession.current().requests.isEmpty());
  check("no shell navigation",TerminalBrowserSession.current().navigations.isEmpty());}
 public static void main(String[] args)throws Exception{
  reset();TerminalBrowserSession.beginLaunch("22");MCEFBrowser a=ready();
  check("account browser is separate",a!=TerminalBrowserSession.current());check("animation token carried",TerminalBrowserSession.state().launchToken().equals("22"));
  client.handler.onLoadEnd(a,new CefFrame(ENTRY,true),200);check("native callbacks queued to game thread",a.requests.isEmpty());mc.flush();
  check("one native POST",a.requests.size()==1);var request=a.requests.getFirst();
  check("fixed exchange URL",request.getURL().equals(POST));check("POST method",request.method.equals("POST"));
  check("payload body only",new String(request.data.element.bytes,java.nio.charset.StandardCharsets.UTF_8).equals(BODY));
  check("exact origin",request.headers.get("Origin").equals("https://mc.muxigame.com"));
  check("action header",request.headers.get("X-Muxi-Terminal-Action").equals("1"));
  check("JSON header",request.headers.get("Content-Type").equals("application/json"));
  check("no payload in URL",!request.getURL().contains("synthetic"));
  TerminalBrowserSession.gen++;load(a,ACCOUNT,200);check("redirect remains same view",TerminalBrowserSession.content()==a);shell();
  reset();a=ready();load(a,ENTRY,200);load(a,ENTRY,200);check("duplicate completion cannot post twice",a.requests.size()==1);
  load(a,POST,401);check("duplicate entry does not disarm failure fallback",a.getURL().equals(ACCOUNT));shell();
  reset();TerminalPassportNavigation.open();var old=TerminalPassportApi.callback;TerminalBrowserSession.home();old.accept(BODY);mc.flush();
  check("cancelled async callback cannot open content",TerminalBrowserSession.content()==null);shell();
  reset();TerminalPassportNavigation.open();mc.connection=new Object();reply(BODY);check("reconnected callback rejected",TerminalBrowserSession.content()==null);shell();
  reset();a=ready();var impostor=new MCEFBrowser(a.id,ENTRY);client.handler.onLoadEnd(impostor,new CefFrame(ENTRY,true),200);mc.flush();
  check("reused CEF id cannot borrow payload",impostor.requests.isEmpty()&&a.requests.isEmpty());load(a,ENTRY,200);check("real browser still posts",a.requests.size()==1);shell();
  reset();a=ready();var replacement=TerminalBrowserSession.openAccountView(ACCOUNT);load(a,ENTRY,200);
  check("replaced view rejected",a.requests.isEmpty()&&replacement.requests.isEmpty());shell();
  reset();a=ready();TerminalBrowserSession.snapshot=new TerminalBrowserSession.State(TerminalBrowserSession.Kind.ACCOUNT,99,"");load(a,ENTRY,200);
  check("different view id rejected",a.requests.isEmpty());shell();
  reset();a=ready();client.handler.onLoadEnd(a,new CefFrame(ENTRY,false),200);mc.flush();check("iframe cannot post",a.requests.isEmpty());
  load(a,ENTRY,200);check("main frame retains one request",a.requests.size()==1);shell();
  reset();a=ready();a.url="https://attacker.example";client.handler.onLoadEnd(a,new CefFrame(ENTRY,true),200);mc.flush();check("changed browser URL rejected",a.requests.isEmpty());shell();
  reset();a=ready();var expiry=TerminalPassportNavigation.class.getDeclaredField("expires");expiry.setAccessible(true);expiry.setLong(null,System.nanoTime()-1);load(a,ENTRY,200);check("expired native payload rejected",a.requests.isEmpty());shell();
  reset();a=ready();mc.screen=new Object();load(a,ENTRY,200);check("closed terminal rejected",a.requests.isEmpty());shell();
  reset();a=ready();mc.connection=new Object();load(a,ENTRY,200);check("changed connection before POST rejected",a.requests.isEmpty());shell();
  reset();a=ready();client.handler.onLoadEnd(a,new CefFrame(ENTRY,true),200);TerminalBrowserSession.home();mc.flush();check("queued load after home rejected",a.requests.isEmpty());shell();
  reset();a=ready();load(a,ENTRY,200);load(a,POST,401);check("failed exchange falls back in child",a.getURL().equals(ACCOUNT));shell();
  reset();a=ready();client.handler.onLoadError(a,new CefFrame(ENTRY,true),ErrorCode.ERR_ABORTED,"fixture",ENTRY);mc.flush();check("aborted load does not cancel",a.navigations.isEmpty());
  client.handler.onLoadError(a,new CefFrame(ENTRY,true),ErrorCode.ERR_FAILED,"fixture",ENTRY);mc.flush();check("network failure stays in child",a.getURL().equals(ACCOUNT));shell();
  reset();TerminalPassportNavigation.open();reply("");check("missing support fallback is separate",TerminalBrowserSession.content()!=TerminalBrowserSession.current());
  check("normal login destination",TerminalBrowserSession.content().getURL().equals(ACCOUNT));shell();
  reset();TerminalBrowserSession.beginLaunch("23");TerminalPassportNavigation.open();old=TerminalPassportApi.callback;
  TerminalBrowserSession.beginLaunch("24");old.accept(BODY);mc.flush();check("superseded animation callback rejected",TerminalBrowserSession.content()==null);shell();
  System.out.println("SSO container adapter assertions: "+passed+" passed");
 }
}'''
}
with tempfile.TemporaryDirectory(prefix="navigation-adapter-",dir=ROOT/"evidence") as temp:
    temp=Path(temp)
    paths=[]
    for rel,text in FILES.items():
        path=temp/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding="utf-8");paths.append(path)
    out=temp/"classes";out.mkdir()
    args=temp/"javac.args"
    args.write_text("\n".join('"'+str(p).replace('\\','/')+'"' for p in [SOURCE,*paths]),encoding="utf-8")
    subprocess.run([str(JDK/"javac.exe"),"--release","21","-encoding","UTF-8","-d",str(out),"@"+str(args)],check=True)
    result=subprocess.run([str(JDK/"java.exe"),"-cp",str(out),"net.muxigame.terminal.client.NavigationAdapterTest"],capture_output=True,text=True)
    (ROOT/"evidence/navigation-adapter-tests.log").write_text(result.stdout+result.stderr,encoding="utf-8")
    print(result.stdout,end="")
    if result.returncode: print(result.stderr);raise SystemExit(result.returncode)
