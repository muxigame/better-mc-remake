package net.muxigame.minigames;

import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

/** Server-authenticated connection identities; terminal social trust never grants game settlement admission. */
public final class TrustedAccounts {
    private record Admission(long uid,Object connection) {}
    private static final Map<MinecraftServer,Map<UUID,Admission>> ACCOUNTS=new ConcurrentHashMap<>();
    private static final Map<MinecraftServer,Map<UUID,Admission>> TERMINAL_ACCOUNTS=new ConcurrentHashMap<>();
    private TrustedAccounts() {}
    public static void admit(ServerPlayer player) {
        String name=player.getGameProfile().getName();
        if(!name.matches("[1-9][0-9]{4,15}") || !UUID.nameUUIDFromBytes(("OfflinePlayer:"+name).getBytes(StandardCharsets.UTF_8)).equals(player.getUUID()))
            throw new IllegalArgumentException("Invalid verified account identity");
        ACCOUNTS.computeIfAbsent(player.server,s->new ConcurrentHashMap<>()).put(player.getUUID(),new Admission(Long.parseLong(name),player.connection));
    }
    public static long uid(ServerPlayer player) {
        return uid(player,ACCOUNTS);
    }
    /** Called only after Auth consumes a terminal proof and verifies the correlated UID/session/ticket. */
    public static void admitTerminal(ServerPlayer player,long verifiedUid) {
        if(!player.server.isSameThread())throw new IllegalArgumentException("Terminal identity must be bound on the server thread");
        String name=player.getGameProfile().getName();
        if(!name.equals(Long.toString(verifiedUid)) || !name.matches("[1-9][0-9]{4,15}")
            || !UUID.nameUUIDFromBytes(("OfflinePlayer:"+name).getBytes(StandardCharsets.UTF_8)).equals(player.getUUID()))
            throw new IllegalArgumentException("Invalid verified terminal identity");
        requireCurrent(player);
        TERMINAL_ACCOUNTS.computeIfAbsent(player.server,s->new ConcurrentHashMap<>()).put(player.getUUID(),new Admission(verifiedUid,player.connection));
    }
    /** Social-only terminal trust or the existing join-grant trust, always scoped to the active transport. */
    public static long socialUid(ServerPlayer player) {
        try{return uid(player,TERMINAL_ACCOUNTS);}catch(IllegalArgumentException absent){return uid(player);}
    }
    private static void requireCurrent(ServerPlayer player) {
        if(player.server.getPlayerList().getPlayer(player.getUUID())!=player || player.connection==null || !player.connection.isAcceptingMessages())
            throw new IllegalArgumentException("Inactive verified account connection");
    }
    private static long uid(ServerPlayer player,Map<MinecraftServer,Map<UUID,Admission>> purpose) {
        requireCurrent(player);
        var account=purpose.getOrDefault(player.server,Map.of()).get(player.getUUID());
        if(account==null || account.connection()!=player.connection)throw new IllegalArgumentException("小游戏需要通过既有账号验证，请重新从启动器进入");
        return account.uid();
    }
    public static void revoke(ServerPlayer player) { revoke(player,ACCOUNTS);revoke(player,TERMINAL_ACCOUNTS); }
    private static void revoke(ServerPlayer player,Map<MinecraftServer,Map<UUID,Admission>> purpose) { var accounts=purpose.get(player.server);if(accounts!=null)accounts.computeIfPresent(player.getUUID(),(id,account)->account.connection()==player.connection?null:account); }
    static void close(MinecraftServer server) { ACCOUNTS.remove(server);TERMINAL_ACCOUNTS.remove(server); }
}
