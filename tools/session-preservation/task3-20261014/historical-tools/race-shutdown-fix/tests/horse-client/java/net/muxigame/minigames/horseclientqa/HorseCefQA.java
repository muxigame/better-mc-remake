package net.muxigame.minigames.horseclientqa;
import com.google.gson.*;
import net.minecraft.client.Minecraft;
import net.muxigame.minigames.client.TerminalGamesApi;
import net.muxigame.minigames.horse.HorseRooms;
import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

/** Uses the unchanged terminal's actual Chromium DOM and privileged native bridge. No OS input. */
public final class HorseCefQA {
    public static final boolean ENABLED=Boolean.getBoolean("muxi.horse.cefQa");
    private static volatile JsonObject dom;private static int wizard,readAt,resultFrames;private static boolean opened,startClicked,resultsOpened,exitClicked;
    private static final Gson JSON=new GsonBuilder().setPrettyPrinting().create();
    private static Object browser()throws Exception{return Class.forName("net.muxigame.terminal.client.TerminalBrowserSession").getMethod("content").invoke(null);}
    private static void js(String code)throws Exception{Object b=browser();if(b!=null)b.getClass().getMethod("executeJavaScript",String.class,String.class,int.class).invoke(b,code,b.getClass().getMethod("getURL").invoke(b),0);}
    public static void click(String selector)throws Exception{js("document.querySelector("+JSON.toJson(selector)+")?.click()");dom=null;}
    private static void action(String label)throws Exception{js("[...document.querySelectorAll('#mg-content [data-mg-action]')].find(b=>!b.disabled&&b.textContent.trim()==="+JSON.toJson(label)+")?.click()");dom=null;}
    public static void poll(int tick)throws Exception{
        if(!ENABLED)return;
        if(readAt>0&&tick>=readAt){readAt=0;Object b=browser();if(b!=null){Class<?> visitor=Class.forName("org.cef.callback.CefStringVisitor");Object callback=Proxy.newProxyInstance(visitor.getClassLoader(),new Class<?>[]{visitor},(p,m,args)->{if(m.getName().equals("visit")){var matcher=java.util.regex.Pattern.compile("data-horse-qa=\"([^\"]+)\"").matcher((String)args[0]);if(matcher.find())dom=JsonParser.parseString(new String(Base64.getDecoder().decode(matcher.group(1)),StandardCharsets.UTF_8)).getAsJsonObject();}return null;});b.getClass().getMethod("getSource",visitor).invoke(b,callback);}}
        if(tick%10!=0||browser()==null)return;
        js("(()=>{const q=s=>document.querySelector(s),v={url:location.href,text:q('#mg-content')?.textContent||'',notice:q('#mg-notice')?.textContent||'',gameHidden:q('#mg-games')?.hidden,create:!!q('[data-mg-create]'),createEnabled:!!q('[data-mg-create]')&&!q('[data-mg-create]').disabled,games:[...document.querySelectorAll('[data-mg-game]')].map(b=>b.dataset.mgGame),maps:[...document.querySelectorAll('[data-mg-map]')].map(b=>b.dataset.mgMap),choices:[...document.querySelectorAll('[data-mg-choice]')].map(b=>({key:b.dataset.mgChoice,value:b.dataset.mgValue,selected:b.getAttribute('aria-pressed')})),actions:[...document.querySelectorAll('#mg-content [data-mg-action]')].map(b=>({label:b.textContent.trim(),disabled:b.disabled}))};document.documentElement.setAttribute('data-horse-qa',btoa(unescape(encodeURIComponent(JSON.stringify(v)))));})()");readAt=tick+3;
    }
    private static boolean has(String key,String value){return dom!=null&&dom.has(key)&&dom.getAsJsonArray(key).asList().stream().anyMatch(e->e.getAsString().equals(value));}
    private static boolean actionEnabled(String label){return dom!=null&&dom.getAsJsonArray("actions").asList().stream().map(JsonElement::getAsJsonObject).anyMatch(e->label.equals(e.get("label").getAsString())&&!e.get("disabled").getAsBoolean());}
    public static void save(String name)throws Exception{if(dom!=null)Files.writeString(Path.of(name+".json"),JSON.toJson(dom));}
    public static boolean create()throws Exception{
        if(!opened){if(!"disabled:1".equals(System.getProperty("muxi_terminal.mcef_shutdown_guard")))throw new IllegalStateException("Installed MCEF global-kill guard not confirmed");if(!TerminalGamesApi.openApp(HorseRooms.ID,"lobby"))return false;opened=true;return false;}
        if(dom==null)return false;
        if(wizard==0&&dom.has("createEnabled")&&dom.get("createEnabled").getAsBoolean()){save("cef-lobby");click("[data-mg-create]");wizard++;return false;}
        if(wizard==1&&has("games",HorseRooms.ID)&&!dom.get("gameHidden").getAsBoolean()){click("[data-mg-game='"+HorseRooms.ID+"']");wizard++;return false;}
        if(wizard==2&&has("maps","stadium-oval")){save("cef-map-selection");click("[data-mg-map='stadium-oval']");wizard++;return false;}
        if(wizard==3&&actionEnabled("创建等待房间")){click("[data-mg-choice='"+HorseRooms.ID+":difficulty'][data-mg-value='5']");wizard++;return false;}
        if(wizard==4&&dom.getAsJsonArray("choices").asList().stream().map(JsonElement::getAsJsonObject).anyMatch(e->e.get("key").getAsString().equals(HorseRooms.ID+":difficulty")&&e.get("value").getAsString().equals("5")&&e.get("selected").getAsString().equals("true"))){save("cef-create-confirmation");action("创建等待房间");wizard++;return true;}
        return wizard==5;
    }
    public static boolean waitingReady()throws Exception{if(!actionEnabled("开始比赛"))return false;save("cef-waiting");return true;}
    public static boolean start()throws Exception{if(startClicked)return true;if(!waitingReady())return false;action("开始比赛");startClicked=true;return true;}
    public static boolean resultsReady()throws Exception{if(!resultsOpened){if(!TerminalGamesApi.openApp(HorseRooms.ID,"lobby"))return false;resultsOpened=true;dom=null;return false;}if(dom==null)return false;if(!actionEnabled("退出并恢复")){click("[data-mg-open-room='"+HorseRooms.ID+"']");return false;}if(!dom.get("text").getAsString().contains("比赛成绩"))return false;if(resultFrames++==0)js("[...document.querySelectorAll('#mg-content h2,#mg-content h3')].find(e=>e.textContent.includes('比赛成绩'))?.scrollIntoView({block:'start'})");if(resultFrames<20)return false;save("cef-results");return true;}
    public static void leave()throws Exception{if(exitClicked)return;action("退出并恢复");exitClicked=true;}
    public static void dismiss(){Minecraft.getInstance().setScreen(null);}
    public static void close()throws Exception{if(!ENABLED)return;save("cef-exited");Class.forName("net.muxigame.terminal.client.TerminalBrowserSession").getMethod("close").invoke(null);Minecraft.getInstance().setScreen(null);}
    private HorseCefQA(){}
}
