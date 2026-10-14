package net.muxigame.horseunifiedqa;
import com.google.gson.*;
import net.minecraft.client.*;
import net.minecraft.client.gui.screens.*;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.client.multiplayer.resolver.ServerAddress;
import net.minecraft.world.entity.Entity;
import org.lwjgl.glfw.GLFW;
import net.minecraft.world.entity.animal.horse.Horse;
import net.muxigame.minigames.client.TerminalGamesApi;
import net.muxigame.minigames.horse.HorsePilot;
import net.muxigame.minigames.horseclientqa.HorseCefQA;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.client.event.ClientTickEvent;
import java.nio.file.*;

/** Read actual renderer output and use real disconnect/reconnect; keep the player camera. */
@EventBusSubscriber(modid="muxi_horse_racing",value=Dist.CLIENT)
public final class HorseUnifiedClientQA {
    public static int mountedDraws;
    static final Path ROOT=Path.of(System.getProperty("qa.local.root","."));
    static final String ROLE=System.getProperty("qa.local.role","");
    static int last=-1,ticks;static boolean follow,quiesced;static long focusDeadline;
    static void write(String name,Object value)throws Exception{Path p=ROOT.resolve("coordinator").resolve(name),tmp=p.resolveSibling(name+".tmp");Files.writeString(tmp,new GsonBuilder().setPrettyPrinting().create().toJson(value));Files.move(tmp,p,StandardCopyOption.REPLACE_EXISTING,StandardCopyOption.ATOMIC_MOVE);}
    static void look(Minecraft mc){if(!follow||mc.player==null||!mc.player.isSpectator())return;for(Entity e:mc.level.entitiesForRendering())if(e instanceof Horse h&&h.getTags().contains(HorsePilot.MARKER)){
        double x=h.getX()+5,y=h.getY()+4,z=h.getZ()+7,dx=h.getX()-x,dz=h.getZ()-z,dy=h.getY()+1.5-y;mc.player.setPos(x,y,z);mc.player.setYRot((float)Math.toDegrees(Math.atan2(-dx,dz)));mc.player.setXRot((float)-Math.toDegrees(Math.atan2(dy,Math.hypot(dx,dz))));mc.player.setOldPosAndRot();break;}}
    @SubscribeEvent public static void tick(ClientTickEvent.Post event){if(!Boolean.getBoolean("muxi.horse.unifiedQa"))return;var mc=Minecraft.getInstance();try{
        ticks++;if(focusDeadline!=0&&System.nanoTime()>focusDeadline){GLFW.glfwHideWindow(mc.getWindow().getWindow());focusDeadline=0;}if(mc.screen instanceof AccessibilityOnboardingScreen){mc.options.onboardAccessibility=false;mc.options.save();mc.setScreen(new TitleScreen());}
        if(!quiesced){if(mc.player!=null&&ticks%40==0)TerminalGamesApi.request();HorseCefQA.poll(ticks);look(mc);}
        var status=new JsonObject();status.addProperty("ticks",ticks);status.addProperty("qaQuiesced",quiesced);status.addProperty("mountedYsmDraws",mountedDraws);status.addProperty("screen",mc.screen==null?"NONE":mc.screen.getClass().getSimpleName());status.addProperty("overlay",mc.getOverlay()==null?"NONE":mc.getOverlay().getClass().getSimpleName());status.addProperty("connected",mc.player!=null);status.addProperty("windowActive",mc.isWindowActive());if(mc.player!=null){status.addProperty("dimension",mc.level.dimension().location().toString());status.addProperty("uuid",mc.player.getUUID().toString());status.addProperty("name",mc.player.getGameProfile().getName());status.addProperty("alive",mc.player.isAlive());status.addProperty("health",mc.player.getHealth());var pos=new JsonObject();pos.addProperty("x",mc.player.getX());pos.addProperty("y",mc.player.getY());pos.addProperty("z",mc.player.getZ());status.add("position",pos);if(mc.player.getVehicle()!=null)status.addProperty("vehicleUuid",mc.player.getVehicle().getUUID().toString());}
        if(ticks%40==0)write("horse-status-"+ROLE+".json",status);
        Path input=ROOT.resolve("coordinator/horse-client-"+ROLE+".json");if(!Files.isRegularFile(input))return;var command=JsonParser.parseString(Files.readString(input)).getAsJsonObject();int id=command.get("id").getAsInt();if(id<=last)return;last=id;var result=new JsonObject();result.addProperty("id",id);
        try{switch(command.get("type").getAsString()){
            case "show-for-terminal"->{if(!ROLE.equals("host")||!Boolean.getBoolean("muxi.horse.allowFocusedQa"))throw new IllegalStateException("A coordinated focused-host QA window is required");GLFW.glfwShowWindow(mc.getWindow().getWindow());GLFW.glfwFocusWindow(mc.getWindow().getWindow());focusDeadline=System.nanoTime()+java.util.concurrent.TimeUnit.SECONDS.toNanos(45);}
            case "hide-after-terminal"->{GLFW.glfwHideWindow(mc.getWindow().getWindow());focusDeadline=0;}
            case "quiesce-terminal"->{quiesced=true;follow=false;GLFW.glfwHideWindow(mc.getWindow().getWindow());focusDeadline=0;mc.setScreen(null);HorseCefQA.close();var session=Class.forName("net.muxigame.terminal.client.TerminalBrowserSession");result.addProperty("terminalShellAbsent",session.getMethod("current").invoke(null)==null);result.addProperty("terminalContentAbsent",session.getMethod("content").invoke(null)==null);result.addProperty("quiescedAtTick",ticks);}
            case "observe"->result.add("app",JsonParser.parseString(TerminalGamesApi.snapshotJson()));
            case "cef-create"->{result.addProperty("done",HorseCefQA.create());HorseCefQA.save("cef-create-progress");}
            case "cef-start"->result.addProperty("done",HorseCefQA.start());
            case "cef-results"->result.addProperty("done",HorseCefQA.resultsReady());
            case "cef-leave"->HorseCefQA.leave();
            case "cef-dismiss"->HorseCefQA.dismiss();
            case "cef-close"->HorseCefQA.close();
            case "follow"->follow=true;
            case "screenshot"->{String name=command.get("name").getAsString();if(!name.matches("[a-zA-Z0-9_-]+"))throw new IllegalArgumentException("Local basename only");try(var image=Screenshot.takeScreenshot(mc.getMainRenderTarget())){image.writeToFile(ROOT.resolve(ROLE+"-"+name+".png"));}}
            case "disconnect"->{follow=false;if(mc.level!=null)mc.level.disconnect();mc.disconnect(new TitleScreen());}
            case "reconnect"->{follow=false;ConnectScreen.startConnecting(mc.screen,mc,ServerAddress.parseString("127.0.0.1:"+Integer.getInteger("qa.local.port")),new ServerData("Private horse reconnect","127.0.0.1",ServerData.Type.OTHER),false,null);}
            default->throw new IllegalArgumentException("Unknown client callback");
        }result.addProperty("ok",true);result.add("status",status);}catch(Throwable failure){result.addProperty("ok",false);result.addProperty("error",failure.toString());failure.printStackTrace();}
        write("horse-client-"+ROLE+"-result-"+id+".json",result);
    }catch(java.io.IOException temporaryLock){}catch(Throwable failure){failure.printStackTrace();}}
}
