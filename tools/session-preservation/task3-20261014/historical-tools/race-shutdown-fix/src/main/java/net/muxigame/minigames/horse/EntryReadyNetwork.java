package net.muxigame.minigames.horse;
import java.util.*;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.resources.ResourceLocation;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.neoforge.network.PacketDistributor;
import net.neoforged.neoforge.network.event.RegisterPayloadHandlersEvent;

/** Ephemeral, connection-bound readiness; a client report never replaces server checks. */
public final class EntryReadyNetwork {
    public record Probe(UUID session,String dimension,int x,int z,int radius) implements CustomPacketPayload {
        public static final Type<Probe> TYPE=new Type<>(ResourceLocation.fromNamespaceAndPath("muxi_horse_racing","entry_probe"));
        public static final StreamCodec<RegistryFriendlyByteBuf,Probe> CODEC=StreamCodec.of((b,p)->{b.writeUUID(p.session);b.writeUtf(p.dimension,128);b.writeInt(p.x);b.writeInt(p.z);b.writeVarInt(p.radius);},b->new Probe(b.readUUID(),b.readUtf(128),b.readInt(),b.readInt(),b.readVarInt()));
        public Type<Probe> type(){return TYPE;}
    }
    public record Reply(UUID session,boolean ready) implements CustomPacketPayload {
        public static final Type<Reply> TYPE=new Type<>(ResourceLocation.fromNamespaceAndPath("muxi_horse_racing","entry_ready"));
        public static final StreamCodec<RegistryFriendlyByteBuf,Reply> CODEC=StreamCodec.of((b,p)->{b.writeUUID(p.session);b.writeBoolean(p.ready);},b->new Reply(b.readUUID(),b.readBoolean()));
        public Type<Reply> type(){return TYPE;}
    }
    private static final Map<ServerPlayer,State> waiting=new WeakHashMap<>();
    private static final class State {final Probe probe; final Object connection; int ack=-1000; State(ServerPlayer p,Probe probe){this.probe=probe;connection=p.connection;}}
    public static void begin(ServerPlayer p,UUID session,int radius){if(radius<1||radius>8)throw new IllegalArgumentException("Entry radius");waiting.put(p,new State(p,new Probe(session,p.level().dimension().location().toString(),p.getBlockX()>>4,p.getBlockZ()>>4,radius)));}
    public static boolean ready(ServerPlayer p,UUID session){var s=waiting.get(p);if(s==null||!s.probe.session.equals(session)||s.connection!=p.connection)return false;int tick=p.server.getTickCount();if(tick%20==0)PacketDistributor.sendToPlayer(p,s.probe);return tick-s.ack<=40&&atEntry(p,s);}
    private static boolean atEntry(ServerPlayer p,State s){return p.isAlive()&&!p.isChangingDimension()&&p.connection.isAcceptingMessages()&&p.level().dimension().location().toString().equals(s.probe.dimension)&&Math.abs((p.getBlockX()>>4)-s.probe.x)<=1&&Math.abs((p.getBlockZ()>>4)-s.probe.z)<=1;}
    public static void acknowledge(ServerPlayer p,Reply reply){var s=waiting.get(p);if(s!=null&&s.connection==p.connection&&s.probe.session.equals(reply.session)){boolean ready=reply.ready&&atEntry(p,s);if(ready&&s.ack<0)org.slf4j.LoggerFactory.getLogger("muxi-entry").info("Entry session={} clientChunksReady radius={} requestedView={}",reply.session,s.probe.radius,p.requestedViewDistance());s.ack=ready?p.server.getTickCount():-1000;}}
    public static void clear(UUID session){waiting.entrySet().removeIf(e->e.getValue().probe.session.equals(session));}
    public static void install(IEventBus bus){bus.addListener((RegisterPayloadHandlersEvent e)->{
        var r=e.registrar("entry-ready-1");
        r.playToClient(Probe.TYPE,Probe.CODEC,(p,c)->c.enqueueWork(()->net.muxigame.minigames.horse.client.EntryReadyClient.receive(p)));
        r.playToServer(Reply.TYPE,Reply.CODEC,(p,c)->c.enqueueWork(()->{if(c.player() instanceof ServerPlayer player)acknowledge(player,p);}));
    });}
}
