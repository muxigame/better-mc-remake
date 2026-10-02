package net.muxigame.terminal.smoke.mixin;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import net.minecraft.server.level.ServerPlayer;
import net.muxigame.core.feature.login.TerminalPassportNetwork;
import net.muxigame.minigames.TrustedAccounts;
import com.google.gson.*;
import java.nio.file.*;
@Mixin(targets="net.muxigame.core.feature.login.TerminalPassportServer",remap=false)
public abstract class NativeServerTraceMixin {
 private static final JsonArray EVENTS=new JsonArray();
 private static long socialUid(ServerPlayer player){try{return TrustedAccounts.socialUid(player);}catch(IllegalArgumentException absent){return -1;}}
 private static synchronized void record(String event,ServerPlayer player,boolean legalTicket){
  var row=new JsonObject();row.addProperty("atUTC",java.time.Instant.now().toString());row.addProperty("event",event);row.addProperty("uid",player.getGameProfile().getName());row.addProperty("listener",System.identityHashCode(player.connection));row.addProperty("actualOnlinePlayer",player.server.getPlayerList().getPlayer(player.getUUID())==player);row.addProperty("acceptingMessages",player.connection!=null && player.connection.isAcceptingMessages());row.addProperty("socialUid",socialUid(player));row.addProperty("isOp",player.server.getPlayerList().isOp(player.getGameProfile()));row.addProperty("legalTicket",legalTicket);EVENTS.add(row);
  try{Files.writeString(Path.of("native-listener-events.json"),EVENTS.toString());}catch(Exception error){throw new IllegalStateException("Unable to record native listener QA");}
 }
 @Inject(method="admit",at=@At("RETURN")) private static void admitted(ServerPlayer player,CallbackInfo ci){record("actual-login-event",player,false);}
 @Inject(method="request",at=@At("HEAD")) private static void request(ServerPlayer player,TerminalPassportNetwork.Request packet,CallbackInfo ci){record("actual-game-request-packet",player,false);}
 @Inject(method="reply",at=@At("HEAD")) private static void reply(ServerPlayer player,String requestId,String ticket,CallbackInfo ci){record("actual-game-result-packet",player,ticket.matches("[A-Za-z0-9_-]{43}"));}
 @Inject(method="disconnect",at=@At("RETURN")) private static void disconnected(ServerPlayer player,CallbackInfo ci){record("actual-logout-revoked",player,false);}
}
