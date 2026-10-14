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
    final String role=System.getProperty("qa.local.role");boolean connecting,stopping;int ticks,last=-1,useUntil,completeAt;JsonObject pending,receipt;String receiptName;int requestedScreenshot=-1;
    long lastNearFullMillis;String lastNearFullDimension="";int lastNearFullX,lastNearFullZ;
    public LocalDebugClient(){if(DebugFiles.enabled()){NeoForge.EVENT_BUS.addListener(this::tick);NeoForge.EVENT_BUS.addListener(this::afterGui);}}
    void afterGui(net.neoforged.neoforge.client.event.RenderGuiEvent.Post event){
        if(requestedScreenshot<0)return;
        try{var mc=Minecraft.getInstance();if(mc.getOverlay()!=null)return;
            org.lwjgl.opengl.GL11.glFinish();String name="frame-"+role+"-"+requestedScreenshot+".png";
            try(var frame=net.minecraft.client.Screenshot.takeScreenshot(mc.getMainRenderTarget())){frame.writeToFile(java.nio.file.Path.of(System.getProperty("qa.local.root"),name));}
            requestedScreenshot=-1;pending.addProperty("executed",true);completeAt=ticks+5;
        }catch(Throwable failure){failure.printStackTrace();}
    }
    JsonObject status(Minecraft mc){
        var row=new JsonObject();row.addProperty("delayedChunks",ChunkDelayFixture.queued());row.addProperty("interceptedChunks",ChunkDelayFixture.intercepted);row.addProperty("releasedChunks",ChunkDelayFixture.released);row.addProperty("discardedChunks",ChunkDelayFixture.discarded);row.addProperty("chunkDelayMillis",ChunkDelayFixture.delayMillis);row.addProperty("runId",DebugFiles.RUN);row.addProperty("role",role);row.addProperty("tick",ticks);
        row.addProperty("screen",mc.screen==null?"NONE":mc.screen.getClass().getSimpleName());row.addProperty("connected",mc.player!=null&&mc.getConnection()!=null);
        row.addProperty("overlay",mc.getOverlay()==null?"NONE":mc.getOverlay().getClass().getSimpleName());row.addProperty("noRender",mc.noRender);row.addProperty("windowWidth",mc.getWindow().getWidth());row.addProperty("windowHeight",mc.getWindow().getHeight());
        row.addProperty("glfwVisible",GLFW.glfwGetWindowAttrib(mc.getWindow().getWindow(),GLFW.GLFW_VISIBLE)==1);
        row.addProperty("glfwFocused",GLFW.glfwGetWindowAttrib(mc.getWindow().getWindow(),GLFW.GLFW_FOCUSED)==1);
        row.addProperty("glRenderer",org.lwjgl.opengl.GL11.glGetString(org.lwjgl.opengl.GL11.GL_RENDERER));
        if(mc.player!=null){row.addProperty("uuid",mc.player.getUUID().toString());row.addProperty("playersSeen",mc.getConnection().getOnlinePlayers().size());row.addProperty("health",mc.player.getHealth());row.addProperty("dimension",mc.level.dimension().location().toString());row.addProperty("selected",mc.player.getInventory().selected);row.addProperty("renderDistance",mc.options.renderDistance().get());int near=0,far=0;for(int x=-24;x<=24;x++)for(int z=-24;z<=24;z++)if(mc.level.getChunkSource().getChunk((mc.player.getBlockX()>>4)+x,(mc.player.getBlockZ()>>4)+z,net.minecraft.world.level.chunk.status.ChunkStatus.FULL,false)!=null){far++;if(Math.abs(x)<=6&&Math.abs(z)<=6)near++;}row.addProperty("receivedRadius6Chunks",near);row.addProperty("receivedRadius24Chunks",far);}
        try {var c=Class.forName("com.elfmcys.yesstevemodel.oo0o0Oooo0OOO00OOo0OO000");
            row.addProperty("ysmRemaining",((Number)c.getMethod("OOo0O00Ooo00O0Ooo0OoOo0o").invoke(null)).intValue());
            var models=(java.util.Map<?,?>)c.getMethod("oOoo00O0o0oO0o0oO00OO0O0").invoke(null);
            row.addProperty("ysmLoaded",models.size());row.addProperty("ysmKeys",models.keySet().toString());
        }catch(Throwable e){row.addProperty("ysmProbeError",e.toString());}
        row.addProperty("chunkHold",ChunkDelayFixture.held);
        row.addProperty("renderDistance",mc.options.renderDistance().get());
        row.addProperty("simulationDistance",mc.options.simulationDistance().get());
        if(mc.player!=null){
            int cx=mc.player.getBlockX()>>4,cz=mc.player.getBlockZ()>>4;
            row.addProperty("x",mc.player.getX());row.addProperty("y",mc.player.getY());row.addProperty("z",mc.player.getZ());row.addProperty("chunkX",cx);row.addProperty("chunkZ",cz);
            row.addProperty("vehicle",mc.player.getVehicle()==null?"":mc.player.getVehicle().getUUID().toString());
            if(row.get("receivedRadius6Chunks").getAsInt()==169){lastNearFullMillis=System.currentTimeMillis();lastNearFullDimension=mc.level.dimension().location().toString();lastNearFullX=cx;lastNearFullZ=cz;}
        }
        row.addProperty("lastNearFullMillis",lastNearFullMillis);row.addProperty("lastNearFullDimension",lastNearFullDimension);row.addProperty("lastNearFullX",lastNearFullX);row.addProperty("lastNearFullZ",lastNearFullZ);
        return row;
    }
    void tick(ClientTickEvent.Post event){if(stopping)return;Minecraft mc=Minecraft.getInstance();ticks++;ChunkDelayFixture.tick();
        try{
            var urgent=DebugFiles.read("command-"+role+".json");if(urgent!=null&&urgent.get("id").getAsInt()>last&&urgent.get("type").getAsString().equals("stop")){stopping=true;mc.stop();return;}
            if(ticks%20==0)DebugFiles.write("status-"+role+".json",status(mc));
            if(receipt!=null){DebugFiles.write(receiptName,receipt);receipt=null;}
            if(useUntil>0&&ticks>=useUntil&&mc.player!=null&&mc.gameMode!=null){mc.options.keyUse.setDown(false);mc.gameMode.releaseUsingItem(mc.player);useUntil=0;}
            if(!connecting&&mc.screen instanceof TitleScreen){connecting=true;if(Boolean.getBoolean("local.debug.connectionStamp")){var stamp=new JsonObject();stamp.addProperty("connectRequestedUtcMillis",System.currentTimeMillis());stamp.addProperty("connectRequestedNanoTime",System.nanoTime());DebugFiles.write("client-connect-"+role+".json",stamp);}ConnectScreen.startConnecting(mc.screen,mc,ServerAddress.parseString("127.0.0.1:"+Integer.getInteger("qa.local.port")),new ServerData(System.getProperty("local.debug.serverName","Private local MC QA"),"127.0.0.1",ServerData.Type.OTHER),false,null);}
            if(pending==null){var command=DebugFiles.read("command-"+role+".json");if(command!=null&&command.get("id").getAsInt()>last){last=command.get("id").getAsInt();pending=command;completeAt=ticks+5;}}
            if(pending==null)return;
            if(pending.has("executed")){if(ticks<completeAt)return;receipt=DebugFiles.result(last,role);receipt.addProperty("ok",true);receipt.addProperty("nativeMinecraftClient",true);receipt.addProperty("physicalOSInput",false);receipt.add("status",status(mc));receiptName="result-"+role+"-"+last+".json";pending=null;DebugFiles.write(receiptName,receipt);receipt=null;return;}
            String type=pending.get("type").getAsString();if(type.equals("stop")){stopping=true;mc.stop();return;}
            if(type.equals("reconnect")){if(mc.player!=null)throw new IllegalStateException("Already connected");connecting=true;ConnectScreen.startConnecting(new TitleScreen(),mc,ServerAddress.parseString("127.0.0.1:"+Integer.getInteger("qa.local.port")),new ServerData("Private local MC QA","127.0.0.1",ServerData.Type.OTHER),false,null);pending.addProperty("executed",true);completeAt=ticks+5;return;}
            if(mc.player==null||mc.gameMode==null)return;
            switch(type){
                case "observe"->{}
                case "camera"->{mc.options.setCameraType(net.minecraft.client.CameraType.valueOf(pending.get("mode").getAsString()));}
                case "chunk-hold"->ChunkDelayFixture.hold(pending.get("held").getAsBoolean());
                case "respawn"->{if(!mc.player.isDeadOrDying())throw new IllegalStateException("Player is alive");mc.player.respawn();mc.setScreen(null);}
                case "dismiss-dialog"->{if(mc.screen instanceof ReceivingLevelScreen)throw new IllegalStateException("Cannot dismiss world loading screen");mc.setScreen(null);}
                case "chunk-delay"->ChunkDelayFixture.configure(pending.get("milliseconds").getAsLong());
                case "screenshot"->{requestedScreenshot=last;return;}

                case "disconnect"->{mc.getConnection().getConnection().disconnect(net.minecraft.network.chat.Component.literal("Private QA requested disconnect"));}

                case "select"->{int slot=pending.get("slot").getAsInt();if(slot<0||slot>8)throw new IllegalArgumentException("Hotbar slot outside 0..8");mc.player.getInventory().selected=slot;}
                case "drop"->mc.player.drop(pending.get("all").getAsBoolean());
                case "aim"->{Vec3 delta=new Vec3(pending.get("x").getAsDouble(),pending.get("y").getAsDouble(),pending.get("z").getAsDouble()).subtract(mc.player.getEyePosition());mc.player.setYRot((float)Math.toDegrees(Math.atan2(-delta.x,delta.z)));mc.player.setXRot((float)-Math.toDegrees(Math.atan2(delta.y,Math.sqrt(delta.x*delta.x+delta.z*delta.z))));}
                case "use"->{int duration=pending.get("duration").getAsInt();if(duration<1||duration>1200)throw new IllegalArgumentException("Duration outside 1..1200 ticks");mc.gameMode.useItem(mc.player,InteractionHand.MAIN_HAND);mc.options.keyUse.setDown(true);useUntil=ticks+duration;completeAt=useUntil+5;}
                case "block-interact"->{var p=new BlockPos(pending.get("x").getAsInt(),pending.get("y").getAsInt(),pending.get("z").getAsInt());mc.gameMode.useItemOn(mc.player,InteractionHand.MAIN_HAND,new BlockHitResult(p.getCenter(),Direction.UP,p,false));}
                case "chat-command"->mc.getConnection().sendCommand(pending.get("command").getAsString());
                case "game-action"->{Object payload;if(pending.has("request")){Class<?> action=Class.forName("net.muxigame.minigames.GameNetwork$TerminalAction");payload=action.getConstructor(String.class,String.class,String.class,String.class).newInstance(pending.get("request").getAsString(),pending.get("game").getAsString(),pending.get("action").getAsString(),pending.has("value")?pending.get("value").getAsString():"");}else{Class<?> action=Class.forName("net.muxigame.minigames.GameNetwork$Action");payload=action.getConstructor(String.class,String.class,String.class).newInstance(pending.get("game").getAsString(),pending.get("action").getAsString(),pending.has("value")?pending.get("value").getAsString():"");}PacketDistributor.sendToServer((CustomPacketPayload)payload);}
                default->throw new IllegalArgumentException("Unknown client callback "+type);
            }
            pending.addProperty("executed",true);
        }catch(java.io.IOException temporaryFileLock){System.out.println("LOCAL_MC_DEBUG_RETRY_FILE_IO "+temporaryFileLock.getClass().getSimpleName());}
        catch(Throwable failure){failure.printStackTrace();receipt=DebugFiles.result(last,role);receipt.addProperty("ok",false);receipt.addProperty("error",failure.toString());receiptName="result-"+role+"-"+last+".json";pending=null;try{DebugFiles.write(receiptName,receipt);receipt=null;}catch(java.io.IOException retryNextTick){}}
    }
}
