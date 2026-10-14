package net.muxigame.terminal.client;

import com.cinemamod.mcef.MCEFBrowser;
import net.minecraft.client.Minecraft;
import org.cef.browser.CefBrowser;
import org.cef.browser.CefFrame;
import org.cef.handler.CefLoadHandlerAdapter;
import org.cef.handler.CefLoadHandler.ErrorCode;
import java.net.URI;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

/** The original account token stays in native HTTPS headers; no website session is issued. */
final class TerminalPassportNavigation {
    static final String ERROR="about:blank#muxi-terminal-account-unavailable";
    private static final String ACCOUNT="https://mc.muxigame.com/account.html";
    private static MCEFBrowser expected;
    private static volatile CefBrowser protectedView;
    private static String document="";
    private static long expires,generation,viewId;
    private static boolean opening,awaitingSession;
    private static Object gameConnection;
    private static int retries;
    private TerminalPassportNavigation() {}
    static void install(com.cinemamod.mcef.MCEFClient client){
        client.addLoadHandler(new CefLoadHandlerAdapter(){
            @Override public void onLoadEnd(CefBrowser browser,CefFrame frame,int status){
                if(frame==null || !frame.isMain() || status==-3)return;
                String url=frame.getURL();Minecraft.getInstance().execute(()->loaded(browser,url,status));
            }
            @Override public void onLoadError(CefBrowser browser,CefFrame frame,ErrorCode code,String message,String url){
                if(code==ErrorCode.ERR_ABORTED || frame==null || !frame.isMain())return;
                Minecraft.getInstance().execute(()->{
                    synchronized(TerminalPassportNavigation.class){if(currentView(browser) && !staleAccountLoad(browser,url))fail("账户服务暂时无法连接，请返回后重试");}
                });
            }
        });
    }
    private static synchronized void loaded(CefBrowser browser,String url,int status){
        if(!currentView(browser) || url.startsWith("about:blank") || staleAccountLoad(browser,url))return;
        if(!live()){fail("客户端账户连接已结束，请在客户端恢复账户");return;}
        if(accountDocument(url) && status==200){retries=0;awaitingSession=false;return;}
        if(status==401 || status==403){renew(browser);return;}
        if(status>=400)fail("账户页面暂时不可用，请返回后重试");
    }
    static boolean credentialOrigin(String url){
        try{URI uri=URI.create(url);return "https".equalsIgnoreCase(uri.getScheme()) && "mc.muxigame.com".equalsIgnoreCase(uri.getHost())
            && uri.getRawUserInfo()==null && (uri.getPort()==-1 || uri.getPort()==443);}
        catch(IllegalArgumentException ignored){return false;}
    }
    static String freshAccess(CefBrowser browser){
        if(protectedView!=browser)return "";
        try{
            String access=(String)Class.forName("net.muxigame.core.client.TerminalPassportApi").getMethod("freshAccessToken").invoke(null);
            if(protectedView==browser && access!=null && access.matches("[A-Za-z0-9_-]{43}"))return access;
        }catch(ReflectiveOperationException | LinkageError ignored){}
        Minecraft.getInstance().execute(()->{
            synchronized(TerminalPassportNavigation.class){if(currentView(browser))fail("客户端账户已失效，请在客户端恢复登录");}
        });return "";
    }
    static boolean beforeBrowse(CefBrowser browser,CefFrame frame,String target){
        if(!interactiveAuth(target)){
            if(!TerminalWebPolicy.account(target) || permitsResource(browser,target))return false;
            if(frame!=null && frame.isMain())Minecraft.getInstance().execute(()->{if(TerminalBrowserSession.content()==browser)resume();});
            return true;
        }
        if(frame==null || !frame.isMain())return true;
        Minecraft.getInstance().execute(()->{
            synchronized(TerminalPassportNavigation.class){
                if(currentView(browser))renew(browser);
                else if(TerminalBrowserSession.content()==browser)TerminalBrowserSession.accountError(browser,"请从终端重新打开账户");
            }
        });return true;
    }
    static synchronized boolean staleAccountLoad(CefBrowser browser,String url){return currentView(browser) && awaitingSession && accountDocument(url) && !document.equals(url);}
    static boolean permitsResource(CefBrowser browser,String url){return !TerminalWebPolicy.account(url) || protectedView==browser;}
    static boolean interactiveAuth(String url){
        try{
            URI uri=URI.create(url);String host=uri.getHost(),path=uri.getPath();
            if("account.muxigame.com".equalsIgnoreCase(host))return true;
            return "mc.muxigame.com".equalsIgnoreCase(host) && path!=null
                && (path.startsWith("/api/v1/auth/") && !path.equals("/api/v1/auth/me") || path.equals("/login") || path.equals("/login.html"));
        }catch(IllegalArgumentException ignored){return false;}
    }
    private static boolean accountDocument(String url){try{return credentialOrigin(url) && "/account.html".equals(URI.create(url).getPath());}catch(IllegalArgumentException ignored){return false;}}
    private static boolean currentView(CefBrowser browser){return expected!=null && browser==expected && TerminalBrowserSession.content()==expected
        && TerminalBrowserSession.state().kind()==TerminalBrowserSession.Kind.ACCOUNT && TerminalBrowserSession.state().viewId()==viewId;}
    private static boolean live(){return connected() && Minecraft.getInstance().screen instanceof TerminalScreen;}
    private static boolean connected(){Minecraft mc=Minecraft.getInstance();return mc.player!=null && mc.getConnection()==gameConnection;}
    static synchronized void clear(){
        protectedView=null;expected=null;document="";expires=0;opening=false;gameConnection=null;viewId=0;retries=0;awaitingSession=false;generation++;
        try{Class.forName("net.muxigame.core.client.TerminalPassportApi").getMethod("cancel").invoke(null);}catch(ReflectiveOperationException | LinkageError ignored){}
    }
    private static void fail(String message){MCEFBrowser browser=expected;clear();if(browser!=null)TerminalBrowserSession.accountError(browser,message);}
    private static void request(Consumer<String> callback){
        try{Class.forName("net.muxigame.core.client.TerminalPassportApi").getMethod("request",Consumer.class).invoke(null,callback);}
        catch(ReflectiveOperationException | LinkageError ignored){callback.accept("");}
    }
    private static boolean valid(String value){try{TerminalFriendsTransport.originalAccess(value);return true;}catch(RuntimeException ignored){return false;}}
    private static void loadAccount(MCEFBrowser browser,long epoch){
        document=ACCOUNT+"?terminal_view="+epoch;awaitingSession=true;expires=System.nanoTime()+20_000_000_000L;
        protectedView=browser;browser.loadURL(document);scheduleValidation(browser,epoch);scheduleLoadTimeout(browser,epoch);
    }
    static void open(){
        Minecraft mc=Minecraft.getInstance();MCEFBrowser shell=TerminalBrowserSession.current();
        String sourceUrl=shell==null?"":shell.getURL();long shellGeneration=TerminalBrowserSession.generation();Object connection=mc.getConnection();final long epoch;
        synchronized(TerminalPassportNavigation.class){if(opening)return;clear();opening=true;epoch=generation;}
        request(value->mc.execute(()->{
            synchronized(TerminalPassportNavigation.class){
                if(generation!=epoch)return;opening=false;
                if(mc.player==null || mc.getConnection()!=connection || !(mc.screen instanceof TerminalScreen) || shell==null
                    || TerminalBrowserSession.current()!=shell || !sourceUrl.equals(shell.getURL()) || TerminalBrowserSession.generation()!=shellGeneration)return;
                if(!valid(value)){TerminalBrowserSession.openAccountError("客户端账户暂不可用，请在客户端恢复登录");return;}
                MCEFBrowser account=TerminalBrowserSession.openAccountView("about:blank");
                expected=account;gameConnection=connection;viewId=TerminalBrowserSession.state().viewId();loadAccount(account,generation);
            }
        }));
    }
    static synchronized void resume(){
        MCEFBrowser browser=TerminalBrowserSession.content();
        if(expected!=null || opening || browser==null || TerminalBrowserSession.state().kind()!=TerminalBrowserSession.Kind.ACCOUNT)return;
        expected=browser;gameConnection=Minecraft.getInstance().getConnection();viewId=TerminalBrowserSession.state().viewId();retries=0;renew(browser);
    }
    static synchronized boolean authorized(CefBrowser browser){return currentView(browser) && connected() && !opening && !awaitingSession && accountDocument(browser.getURL());}
    private static void renew(CefBrowser browser){
        if(!currentView(browser) || opening)return;
        if(!live()){fail("客户端账户连接已结束，请在客户端恢复登录");return;}
        if(++retries>1){fail("账户会话暂时无法恢复，请返回后重试");return;}
        protectedView=null;browser.stopLoad();browser.loadURL(ERROR);opening=true;long epoch=++generation;
        TerminalBrowserSession.loaded(browser,ACCOUNT,true,"");
        request(value->Minecraft.getInstance().execute(()->{
            synchronized(TerminalPassportNavigation.class){
                if(generation!=epoch || !currentView(browser))return;opening=false;
                if(!live() || !valid(value)){fail("客户端账户已失效，请在客户端恢复登录");return;}loadAccount(expected,epoch);
            }
        }));
    }
    private static void scheduleLoadTimeout(MCEFBrowser browser,long epoch){
        CompletableFuture.runAsync(()->Minecraft.getInstance().execute(()->{
            synchronized(TerminalPassportNavigation.class){if(epoch==generation && currentView(browser) && awaitingSession && System.nanoTime()>=expires)renew(browser);}
        }),CompletableFuture.delayedExecutor(20,TimeUnit.SECONDS));
    }
    private static void scheduleValidation(MCEFBrowser browser,long epoch){CompletableFuture.runAsync(()->Minecraft.getInstance().execute(()->validate(browser,epoch)),CompletableFuture.delayedExecutor(30,TimeUnit.SECONDS));}
    private static synchronized void validate(MCEFBrowser browser,long epoch){
        if(epoch!=generation || !currentView(browser))return;
        if(!connected()){fail("客户端账户连接已结束，请在客户端恢复登录");return;}
        if(opening || awaitingSession){scheduleValidation(browser,epoch);return;}
        try{
            Consumer<Boolean> callback=ok->Minecraft.getInstance().execute(()->{
                synchronized(TerminalPassportNavigation.class){
                    if(epoch!=generation || !currentView(browser))return;
                    if(!connected() || !Boolean.TRUE.equals(ok)){fail("客户端账户已失效，请在客户端恢复登录");return;}scheduleValidation(browser,epoch);
                }
            });
            Class.forName("net.muxigame.core.client.TerminalPassportApi").getMethod("validate",Consumer.class).invoke(null,callback);
        }catch(ReflectiveOperationException | LinkageError ignored){fail("客户端账户连接需要更新，请返回后重试");}
    }
}
