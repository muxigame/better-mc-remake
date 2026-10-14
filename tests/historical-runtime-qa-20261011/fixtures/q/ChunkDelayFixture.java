package net.muxigame.localdebug;
import java.util.ArrayDeque;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientPacketListener;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.network.protocol.game.ClientboundLevelChunkWithLightPacket;

/** Private QA only: delay application of received chunk packets, never system networking. */
public final class ChunkDelayFixture {
    private record Waiting(ClientPacketListener listener, ClientLevel level, ClientboundLevelChunkWithLightPacket packet,long due){}
    private static final ArrayDeque<Waiting> queue=new ArrayDeque<>();
    public static boolean releasing,held;public static long delayMillis;public static int intercepted,released,discarded;
    public static boolean accept(ClientPacketListener listener,ClientboundLevelChunkWithLightPacket packet){
        var mc=Minecraft.getInstance();
        if(!mc.isSameThread()||releasing||(!held&&delayMillis==0)||mc.level==null||mc.level.dimension().location().getNamespace().equals("minecraft"))return false;
        if(queue.size()>=4096)throw new IllegalStateException("QA chunk delay bounded queue exceeded");
        queue.add(new Waiting(listener,mc.level,packet,System.nanoTime()+delayMillis*1_000_000));intercepted++;return true;
    }
    public static void tick(){
        var mc=Minecraft.getInstance();int budget=16;
        while(!queue.isEmpty()&&budget-->0){var next=queue.peek();
            if(next.level()!=mc.level||next.listener()!=mc.getConnection()){queue.remove();discarded++;continue;}
            if(held||next.due()>System.nanoTime())break;
            queue.remove();releasing=true;try{next.listener().handleLevelChunkWithLight(next.packet());released++;}finally{releasing=false;}
        }
    }
    public static int queued(){return queue.size();}
    public static void hold(boolean value){held=value;}
    public static void configure(long ms){if(ms<0||ms>120000)throw new IllegalArgumentException("Delay outside 0..120s");delayMillis=ms;}
}
