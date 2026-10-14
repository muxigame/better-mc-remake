package net.muxigame.terminal.client;

import com.cinemamod.mcef.MCEFBrowser;
import net.minecraft.client.Minecraft;
import org.cef.browser.CefBrowser;
import org.cef.browser.CefFrame;
import org.cef.handler.CefLoadHandlerAdapter;
import org.cef.network.CefPostData;
import org.cef.network.CefPostDataElement;
import org.cef.network.CefRequest;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

/** Native one-use exchange. The account child never renders an interactive login page. */
final class TerminalPassportNavigation {
    static final String ERROR="about:blank#muxi-terminal-account-unavailable";
    private static final String ACCOUNT="https://mc.muxigame.com/account.html";
    private static final String ENTRY="https://mc.muxigame.com/api/v1/auth/terminal";
    private static final String POST=ENTRY+"/exchange";
    private static MCEFBrowser expected;
    private static String payload;
    private static long expires,generation,viewId;
    private static boolean opening;
    private static Object gameConnection;
    private static int retries;
    private TerminalPassportNavigation() {}

    static void install(com.cinemamod.mcef.MCEFClient client){
        client.addLoadHandler(new CefLoadHandlerAdapter(){
            @Override public void onLoadEnd(CefBrowser browser,CefFrame frame,int status){
                if(frame==null || !frame.isMain())return;
                String url=frame.getURL();
                Minecraft.getInstance().execute(()->loaded(browser,url,status));
            }
            @Override public void onLoadError(CefBrowser browser,CefFrame frame,ErrorCode code,String message,String url){
                if(code==ErrorCode.ERR_ABORTED || frame==null || !frame.isMain())return;
                Minecraft.getInstance().execute(()->{
                    if(currentView(browser))fail("账户服务暂时无法连接，请返回后重试");
                });
            }
        });
    }
    private static synchronized void loaded(CefBrowser browser,String url,int status){
        if(!currentView(browser))return;
        if(!live()){fail("客户端账户连接已结束，请在客户端恢复账户");return;}
        if(payload==null){
            if(POST.equals(url) && status>=400){renew(browser);return;}
            if(ACCOUNT.equals(url) && status==200){retries=0;return;}
            if(status>=400)fail("账户页面暂时不可用，请返回后重试");
            return;
        }
        if(!ENTRY.equals(url) || !ENTRY.equals(browser.getURL()) || status!=200){
            fail("账户会话未能建立，请返回后重试");return;
        }
        if(System.nanoTime()>expires){renew(browser);return;}
        byte[] bytes=payload.getBytes(StandardCharsets.UTF_8);payload=null;
        CefPostDataElement element=CefPostDataElement.create();element.setToBytes(bytes.length,bytes);
        CefPostData data=CefPostData.create();data.addElement(element);
        CefRequest request=CefRequest.create();request.setURL(POST);request.setMethod("POST");request.setPostData(data);
        request.setHeaderMap(Map.of("Content-Type","application/json","Origin","https://mc.muxigame.com","X-Muxi-Terminal-Action","1"));
        browser.loadRequest(request);
    }
    /** Called synchronously before a main-frame request can contact an interactive auth endpoint. */
    static boolean beforeBrowse(CefBrowser browser,CefFrame frame,String target){
        if(frame==null || !frame.isMain() || !interactiveAuth(target))return false;
        Minecraft.getInstance().execute(()->{
            synchronized(TerminalPassportNavigation.class){
                if(currentView(browser))renew(browser);
                else if(TerminalBrowserSession.content()==browser)TerminalBrowserSession.accountError(browser,"请从终端重新打开账户");
            }
        });
        return true;
    }
    static boolean interactiveAuth(String url){
        try{
            URI uri=URI.create(url);String host=uri.getHost(),path=uri.getPath();
            if("account.muxigame.com".equalsIgnoreCase(host))return true;
            return "mc.muxigame.com".equalsIgnoreCase(host) && path!=null
                && (path.startsWith("/api/v1/auth/") && !path.equals("/api/v1/auth/terminal") && !path.equals("/api/v1/auth/terminal/exchange")
                    || path.equals("/login") || path.equals("/login.html"));
        }catch(IllegalArgumentException ignored){return false;}
    }
    private static boolean currentView(CefBrowser browser){
        return expected!=null && browser==expected && TerminalBrowserSession.content()==expected
            && TerminalBrowserSession.state().kind()==TerminalBrowserSession.Kind.ACCOUNT
            && TerminalBrowserSession.state().viewId()==viewId;
    }
    private static boolean live(){
        Minecraft mc=Minecraft.getInstance();
        return mc.player!=null && mc.getConnection()==gameConnection && mc.screen instanceof TerminalScreen;
    }
    static synchronized void clear(){
        expected=null;payload=null;expires=0;opening=false;gameConnection=null;viewId=0;retries=0;generation++;
        try{Class.forName("net.muxigame.core.client.TerminalPassportApi").getMethod("cancel").invoke(null);}
        catch(ReflectiveOperationException | LinkageError ignored){}
    }
    private static void fail(String message){
        MCEFBrowser browser=expected;clear();
        if(browser!=null)TerminalBrowserSession.accountError(browser,message);
    }
    private static void request(Consumer<String> callback){
        try{Class.forName("net.muxigame.core.client.TerminalPassportApi").getMethod("request",Consumer.class).invoke(null,callback);}
        catch(ReflectiveOperationException | LinkageError ignored){callback.accept("");}
    }
    static void open(){
        Minecraft mc=Minecraft.getInstance();MCEFBrowser shell=TerminalBrowserSession.current();
        String sourceUrl=shell==null?"":shell.getURL();long shellGeneration=TerminalBrowserSession.generation();Object connection=mc.getConnection();
        final long epoch;
        synchronized(TerminalPassportNavigation.class){if(opening)return;clear();opening=true;epoch=generation;}
        request(value->mc.execute(()->{
            synchronized(TerminalPassportNavigation.class){
                if(generation!=epoch)return;opening=false;
                if(mc.player==null || mc.getConnection()!=connection || !(mc.screen instanceof TerminalScreen) || shell==null
                    || TerminalBrowserSession.current()!=shell || !sourceUrl.equals(shell.getURL()) || TerminalBrowserSession.generation()!=shellGeneration)return;
                if(value==null || value.isEmpty()){TerminalBrowserSession.openAccountError("客户端账户暂不可用，请在客户端检查账户后重试");return;}
                MCEFBrowser account=TerminalBrowserSession.openAccountView(ENTRY);
                expected=account;payload=value;gameConnection=connection;viewId=TerminalBrowserSession.state().viewId();
                expires=System.nanoTime()+20_000_000_000L;
                scheduleValidation(account,generation);
            }
        }));
    }
    private static void renew(CefBrowser browser){
        if(!currentView(browser) || opening)return;
        if(!live()){fail("客户端账户连接已结束，请在客户端恢复账户");return;}
        if(++retries>1){fail("账户会话暂时无法续期，请返回后重试");return;}
        payload=null;opening=true;long epoch=++generation;
        TerminalBrowserSession.loaded(browser,ACCOUNT,true,"");
        request(value->Minecraft.getInstance().execute(()->{
            synchronized(TerminalPassportNavigation.class){
                if(generation!=epoch || !currentView(browser))return;
                opening=false;
                if(!live() || value==null || value.isEmpty()){fail("客户端账户已失效，请在客户端恢复账户");return;}
                payload=value;expires=System.nanoTime()+20_000_000_000L;
                browser.loadURL(ENTRY);scheduleValidation(expected,epoch);
            }
        }));
    }
    private static void scheduleValidation(MCEFBrowser browser,long epoch){
        CompletableFuture.runAsync(()->Minecraft.getInstance().execute(()->validate(browser,epoch)),CompletableFuture.delayedExecutor(30,TimeUnit.SECONDS));
    }
    private static synchronized void validate(MCEFBrowser browser,long epoch){
        if(epoch!=generation || !currentView(browser))return;
        if(!live()){fail("客户端账户连接已结束，请在客户端恢复账户");return;}
        if(opening || payload!=null){scheduleValidation(browser,epoch);return;}
        try{
            Consumer<Boolean> callback=valid->Minecraft.getInstance().execute(()->{
                synchronized(TerminalPassportNavigation.class){
                    if(epoch!=generation || !currentView(browser))return;
                    if(!live() || !Boolean.TRUE.equals(valid)){fail("客户端账户已失效，请在客户端恢复账户");return;}
                    scheduleValidation(browser,epoch);
                }
            });
            Class.forName("net.muxigame.core.client.TerminalPassportApi").getMethod("validate",Consumer.class).invoke(null,callback);
        }catch(ReflectiveOperationException | LinkageError ignored){fail("客户端账户组件需要更新，请返回后重试");}
    }
}
