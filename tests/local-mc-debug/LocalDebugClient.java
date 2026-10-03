package net.muxigame.localdebug;
import com.google.gson.*;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.*;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.client.multiplayer.resolver.ServerAddress;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.phys.*;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.client.event.ClientTickEvent;
import net.neoforged.neoforge.network.PacketDistributor;
import org.lwjgl.glfw.GLFW;

/** Native network actions from real Minecraft clients, adapted from EquipmentClientQA. */
@Mod(value="local_mc_debug_qa",dist=Dist.CLIENT)
public final class LocalDebugClient {
    final String role=System.getProperty("qa.local.role");boolean connecting,stopping;int ticks,last=-1,useUntil,completeAt;JsonObject pending,receipt;String receiptName;
    public LocalDebugClient(){if(DebugFiles.enabled())NeoForge.EVENT_BUS.addListener(this::tick);}
    JsonObject status(Minecraft mc){
        var row=new JsonObject();row.addProperty("runId",DebugFiles.RUN);row.addProperty("role",role);row.addProperty("tick",ticks);
        row.addProperty("screen",mc.screen==null?"NONE":mc.screen.getClass().getSimpleName());row.addProperty("connected",mc.player!=null&&mc.getConnection()!=null);
        row.addProperty("glfwVisible",GLFW.glfwGetWindowAttrib(mc.getWindow().getWindow(),GLFW.GLFW_VISIBLE)==1);
        row.addProperty("glfwFocused",GLFW.glfwGetWindowAttrib(mc.getWindow().getWindow(),GLFW.GLFW_FOCUSED)==1);
        row.addProperty("glRenderer",org.lwjgl.opengl.GL11.glGetString(org.lwjgl.opengl.GL11.GL_RENDERER));
        if(mc.player!=null){row.addProperty("uuid",mc.player.getUUID().toString());row.addProperty("playersSeen",mc.getConnection().getOnlinePlayers().size());row.addProperty("health",mc.player.getHealth());row.addProperty("dimension",mc.level.dimension().location().toString());row.addProperty("selected",mc.player.getInventory().selected);}
        return row;
    }
    void tick(ClientTickEvent.Post event){if(stopping)return;Minecraft mc=Minecraft.getInstance();ticks++;
        try{
            var urgent=DebugFiles.read("command-"+role+".json");if(urgent!=null&&urgent.get("id").getAsInt()>last&&urgent.get("type").getAsString().equals("stop")){stopping=true;mc.stop();return;}
            if(ticks%20==0)DebugFiles.write("status-"+role+".json",status(mc));
            if(receipt!=null){DebugFiles.write(receiptName,receipt);receipt=null;}
            if(useUntil>0&&ticks>=useUntil&&mc.player!=null&&mc.gameMode!=null){mc.options.keyUse.setDown(false);mc.gameMode.releaseUsingItem(mc.player);useUntil=0;}
            if(!connecting&&mc.screen instanceof TitleScreen){connecting=true;ConnectScreen.startConnecting(mc.screen,mc,ServerAddress.parseString("127.0.0.1:"+Integer.getInteger("qa.local.port")),new ServerData("Private local MC QA","127.0.0.1",ServerData.Type.OTHER),false,null);}
            if(pending==null){var command=DebugFiles.read("command-"+role+".json");if(command!=null&&command.get("id").getAsInt()>last){last=command.get("id").getAsInt();pending=command;completeAt=ticks+5;}}
            if(pending==null)return;
            if(pending.has("executed")){if(ticks<completeAt)return;receipt=DebugFiles.result(last,role);receipt.addProperty("ok",true);receipt.addProperty("nativeMinecraftClient",true);receipt.addProperty("physicalOSInput",false);receipt.add("status",status(mc));receiptName="result-"+role+"-"+last+".json";pending=null;DebugFiles.write(receiptName,receipt);receipt=null;return;}
            String type=pending.get("type").getAsString();if(type.equals("stop")){stopping=true;mc.stop();return;}
            if(mc.player==null||mc.gameMode==null)return;mc.setScreen(null);
            switch(type){
                case "observe"->{}
                case "select"->{int slot=pending.get("slot").getAsInt();if(slot<0||slot>8)throw new IllegalArgumentException("Hotbar slot outside 0..8");mc.player.getInventory().selected=slot;}
                case "drop"->mc.player.drop(pending.get("all").getAsBoolean());
                case "aim"->{Vec3 delta=new Vec3(pending.get("x").getAsDouble(),pending.get("y").getAsDouble(),pending.get("z").getAsDouble()).subtract(mc.player.getEyePosition());mc.player.setYRot((float)Math.toDegrees(Math.atan2(-delta.x,delta.z)));mc.player.setXRot((float)-Math.toDegrees(Math.atan2(delta.y,Math.sqrt(delta.x*delta.x+delta.z*delta.z))));}
                case "use"->{int duration=pending.get("duration").getAsInt();if(duration<1||duration>1200)throw new IllegalArgumentException("Duration outside 1..1200 ticks");mc.gameMode.useItem(mc.player,InteractionHand.MAIN_HAND);mc.options.keyUse.setDown(true);useUntil=ticks+duration;completeAt=useUntil+5;}
                case "block-interact"->{var p=new BlockPos(pending.get("x").getAsInt(),pending.get("y").getAsInt(),pending.get("z").getAsInt());mc.gameMode.useItemOn(mc.player,InteractionHand.MAIN_HAND,new BlockHitResult(p.getCenter(),Direction.UP,p,false));}
                case "chat-command"->mc.getConnection().sendCommand(pending.get("command").getAsString());
                case "game-action"->{Class<?> action=Class.forName("net.muxigame.minigames.GameNetwork$Action");Object payload=action.getConstructor(String.class,String.class,String.class).newInstance(pending.get("game").getAsString(),pending.get("action").getAsString(),pending.has("value")?pending.get("value").getAsString():"");PacketDistributor.sendToServer((CustomPacketPayload)payload);}
                default->throw new IllegalArgumentException("Unknown client callback "+type);
            }
            pending.addProperty("executed",true);
        }catch(java.io.IOException temporaryFileLock){System.out.println("LOCAL_MC_DEBUG_RETRY_FILE_IO "+temporaryFileLock.getClass().getSimpleName());}
        catch(Throwable failure){failure.printStackTrace();receipt=DebugFiles.result(last,role);receipt.addProperty("ok",false);receipt.addProperty("error",failure.toString());receiptName="result-"+role+"-"+last+".json";pending=null;try{DebugFiles.write(receiptName,receipt);receipt=null;}catch(java.io.IOException retryNextTick){}}
    }
}
