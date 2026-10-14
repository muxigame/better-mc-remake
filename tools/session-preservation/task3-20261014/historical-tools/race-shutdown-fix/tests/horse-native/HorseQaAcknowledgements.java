package net.muxigame.minigames.horseqa;
import net.minecraft.server.network.ServerGamePacketListenerImpl;
import net.minecraft.network.Connection;
import net.minecraft.network.protocol.game.ServerboundAcceptTeleportationPacket;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import java.nio.file.*;

/** Legacy engine fixtures need explicit synthetic acknowledgements for the new entry gate. */
@EventBusSubscriber(modid="muxi_horse_racing")
public final class HorseQaAcknowledgements {
 @SubscribeEvent public static void tick(ServerTickEvent.Post event)throws Exception {
  if(Boolean.getBoolean("muxi.horse.startQa")||!Files.exists(Path.of("HORSE_QA_ONLY")))return;
  var channel=Connection.class.getDeclaredField("channel");channel.setAccessible(true);
  var teleport=ServerGamePacketListenerImpl.class.getDeclaredField("awaitingTeleport");teleport.setAccessible(true);
  for(var p:event.getServer().getPlayerList().getPlayers())if(p.isChangingDimension()&&channel.get(p.connection.getConnection()) instanceof io.netty.channel.embedded.EmbeddedChannel)p.connection.handleAcceptTeleportPacket(new ServerboundAcceptTeleportationPacket(teleport.getInt(p.connection)));
 }
 private HorseQaAcknowledgements(){}
}
