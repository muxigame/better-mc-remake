import com.google.gson.*;
import java.lang.reflect.*;
import net.muxigame.minigames.horseclientqa.HorseCefQA;
import net.muxigame.terminal.client.TerminalBrowserSession;
/** Invokes the actual QA state machine against an explicitly synthetic disabled DOM button. */
public final class DisabledCreateTest {
 static void field(String name,Object value)throws Exception{var f=HorseCefQA.class.getDeclaredField(name);f.setAccessible(true);f.set(null,value);}
 static int wizard()throws Exception{var f=HorseCefQA.class.getDeclaredField("wizard");f.setAccessible(true);return f.getInt(null);}
 static JsonObject dom(boolean enabled){var d=new JsonObject();d.addProperty("create",true);d.addProperty("createEnabled",enabled);return d;}
 public static void main(String[] args)throws Exception{
  field("opened",true);field("wizard",0);field("dom",dom(false));
  if(HorseCefQA.create()||wizard()!=0||TerminalBrowserSession.clicks!=0)throw new AssertionError("Disabled initial button must not advance/click");
  field("dom",dom(true));
  if(HorseCefQA.create()||wizard()!=1||TerminalBrowserSession.clicks!=1)throw new AssertionError("Enabled button must advance exactly once");
  System.out.println("PASS actual QA disabled-to-enabled state transition; synthetic DOM/browser only");
 }
}
