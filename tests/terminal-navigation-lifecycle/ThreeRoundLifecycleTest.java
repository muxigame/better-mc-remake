package net.muxigame.terminal.client;

import com.cinemamod.mcef.MCEFBrowser;
import net.minecraft.client.Minecraft;
import net.muxigame.core.client.TerminalPassportApi;
import org.cef.browser.CefFrame;
import java.util.function.Consumer;

/** Plain Java lifecycle regression against actual Navigation; CEF/MC/API are explicit fixtures. */
public final class ThreeRoundLifecycleTest {
    private static int passed;
    private static final Minecraft mc=Minecraft.getInstance();
    private static final String ACCOUNT=NavigationAdapterTest.ACCOUNT;
    private static final String ENTRY=NavigationAdapterTest.ENTRY;
    private static final String BODY=NavigationAdapterTest.BODY;
    private static void check(String message,boolean value){if(!value)throw new AssertionError(message);passed++;}
    private static void reset(){NavigationAdapterTest.reset();}
    private static void reply(){NavigationAdapterTest.reply(BODY);}
    private static void load(MCEFBrowser browser,String url){NavigationAdapterTest.load(browser,url,200);}
    private static MCEFBrowser signedIn(){var browser=NavigationAdapterTest.ready();load(browser,ENTRY);load(browser,ACCOUNT);return browser;}
    private static void removed(){mc.screen=null;TerminalPassportNavigation.clear();}
    private static void reopened(){mc.screen=new TerminalScreen();TerminalPassportNavigation.resume();}
    private static void noLogin(MCEFBrowser browser){
        check("no interactive auth navigation",browser.navigations.stream().noneMatch(TerminalPassportNavigation::interactiveAuth));
        check("no interactive auth request",browser.requests.stream().noneMatch(r->TerminalPassportNavigation.interactiveAuth(r.getURL())));
    }
    private static Consumer<Boolean> validation(MCEFBrowser browser)throws Exception{
        NavigationAdapterTest.validate(browser);return TerminalPassportApi.validation;
    }

    public static void main(String[] args)throws Exception{
        // Round A: close/removal and automatic reopen, source switch denied.
        reset();var shell=TerminalBrowserSession.current();var first=signedIn();
        check("first native account authorized",TerminalPassportNavigation.authorized(first));
        var lateHeartbeat=validation(first);int before=TerminalPassportApi.requests;
        TerminalPassportNavigation.clear();removed(); // Actual onClose + removed may both disarm.
        check("removed retained view has no authority",!TerminalPassportNavigation.authorized(first));
        check("removed view cannot send retained cookies",!TerminalPassportNavigation.permitsResource(first,ACCOUNT));
        check("native pending callback cancelled",TerminalPassportApi.callback==null);
        reopened();check("reopen requests exactly one fresh native exchange",TerminalPassportApi.requests==before+1);
        check("reopening retains exact child and shell",TerminalBrowserSession.content()==first&&TerminalBrowserSession.current()==shell);
        check("renewal denies cookies until fresh payload",!TerminalPassportNavigation.permitsResource(first,ACCOUNT));
        lateHeartbeat.accept(false);mc.flush();
        check("late old heartbeat cannot cancel new reopening",TerminalPassportApi.callback!=null&&TerminalBrowserSession.error.isEmpty());
        reply();
        // A queued old account load completion can arrive after the new native payload.
        NavigationAdapterTest.client.handler.onLoadEnd(first,new CefFrame(ACCOUNT,true),200);mc.flush();
        check("old account completion cannot fail current native reopening",TerminalBrowserSession.error.isEmpty()&&first.getURL().equals(ENTRY));
        NavigationAdapterTest.client.handler.onLoadError(first,new CefFrame(ACCOUNT,true),org.cef.handler.CefLoadHandler.ErrorCode.ERR_FAILED,"old fixture response",ACCOUNT);mc.flush();
        check("old account error cannot fail fresh native reopening",TerminalBrowserSession.error.isEmpty()&&first.getURL().equals(ENTRY));
        load(first,ENTRY);load(first,ACCOUNT);
        check("automatic reopening authorizes ordinary account",TerminalPassportNavigation.authorized(first));
        check("automatic reopening has exactly two native POSTs",first.requests.size()==2);
        noLogin(first);

        // Closing during a pending renewal cancels only that generation.
        removed();reopened();var cancelled=TerminalPassportApi.callback;
        removed();reopened();var current=TerminalPassportApi.callback;int oldPosts=first.requests.size();
        cancelled.accept(BODY);mc.flush();
        check("late removed-screen payload does not POST",first.requests.size()==oldPosts);
        check("late payload leaves current callback",TerminalPassportApi.callback==current);
        reply();load(first,ENTRY);load(first,ACCOUNT);
        check("current reopening alone obtains next native POST",first.requests.size()==oldPosts+1);
        var selectedAHeartbeat=validation(first);selectedAHeartbeat.accept(false);mc.flush();
        check("source account switch invalidates A view",first.getURL().equals(TerminalPassportNavigation.ERROR));
        check("A retained-cookie resource blocked after switch",!TerminalPassportNavigation.permitsResource(first,ACCOUNT));
        noLogin(first);

        // Round B: whole view/client lifetime replaces identity objects and rejects old callbacks.
        removed();reopened();var preRestartReply=TerminalPassportApi.callback;
        var previousConnection=mc.connection;reset();var second=signedIn();
        check("restart fixture replaces game connection",mc.connection!=previousConnection);
        check("restart replaces CEF identity despite reused numeric id",second!=first&&second.getIdentifier()==first.getIdentifier());
        preRestartReply.accept(BODY);mc.flush();
        check("pre-restart callback cannot replace B child",TerminalBrowserSession.content()==second&&TerminalPassportNavigation.authorized(second));
        NavigationAdapterTest.client.handler.onLoadEnd(first,new CefFrame(ENTRY,true),200);mc.flush();
        check("reused CEF id cannot deliver old POST",first.requests.size()==oldPosts+1&&second.requests.size()==1);
        check("B uses only fresh lifetime POST",second.requests.size()==1);
        noLogin(second);
        var bHeartbeat=validation(second);bHeartbeat.accept(true);mc.flush();
        check("valid B heartbeat preserves edits without reloading",second.requests.size()==1&&TerminalPassportNavigation.authorized(second));
        removed();reopened();reply();load(second,ENTRY);load(second,ACCOUNT);
        check("B reopening remains ordinary account",TerminalPassportNavigation.authorized(second)&&second.requests.size()==2);
        noLogin(second);

        // Round B revoke: native refusal stays a native error, no interactive fallback.
        var revoke=validation(second);revoke.accept(false);mc.flush();
        check("revocation blanks retained view",second.getURL().equals(TerminalPassportNavigation.ERROR));
        check("revocation removes file/native authority",!TerminalPassportNavigation.authorized(second));
        check("revocation blocks cookie resources",!TerminalPassportNavigation.permitsResource(second,ACCOUNT));
        int retainedPosts=second.requests.size();
        check("revoked history navigation is intercepted",TerminalPassportNavigation.beforeBrowse(second,new CefFrame(ACCOUNT,true),ACCOUNT));mc.flush();
        check("history retry asks native broker only",TerminalPassportApi.callback!=null);
        NavigationAdapterTest.reply("");
        check("denied history retry emits no POST",second.requests.size()==retainedPosts);
        check("denied reopening stays blank with native error",second.getURL().equals(TerminalPassportNavigation.ERROR)&&!TerminalBrowserSession.error.isEmpty());
        check("denied reopening cannot restore cookies",!TerminalPassportNavigation.permitsResource(second,ACCOUNT));
        noLogin(second);
        System.out.println("PASS: actual-source three-round navigation lifecycle assertions="+passed+"; explicit CEF/MC/API fixtures; Minecraft/CEF not started; real acceptance not claimed");
    }
}
