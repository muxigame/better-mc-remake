package net.muxigame.identity;

import com.donutello.simplenicknames.SimpleNicknamesMain;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.common.util.FakePlayer;
import net.neoforged.neoforge.event.CommandEvent;
import net.neoforged.neoforge.event.entity.player.PlayerEvent;
import net.neoforged.neoforge.event.server.ServerStoppedEvent;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentLinkedQueue;

/** Server-only display bridge. This is NOT an authentication replacement. */
@Mod("muxi_identity")
public final class MuxiIdentity {
    private static final Logger LOG = LoggerFactory.getLogger("muxi-identity");
    private static final Set<String> NICK_COMMANDS = Set.of("nick", "setnick", "randomnick", "unnick");
    private final HttpClient http;
    private final String endpoint;
    private final String serverKey;
    private final int refreshTicks;
    private final Set<UUID> pending = ConcurrentHashMap.newKeySet();
    private final ConcurrentLinkedQueue<Result> completed = new ConcurrentLinkedQueue<>();
    private int ticks;
    private volatile boolean stopped;

    private record Result(UUID player, String uid, int status, String body) {}

    public MuxiIdentity() {
        JsonObject config;
        try (var reader = Files.newBufferedReader(Path.of("config/muxi-identity-bridge.json"), StandardCharsets.UTF_8)) {
            config = JsonParser.parseReader(reader).getAsJsonObject();
            endpoint = config.get("endpoint").getAsString();
            serverKey = config.get("serverKey").getAsString();
            refreshTicks = 20 * Math.max(10, Math.min(600, config.has("refreshSeconds") ? config.get("refreshSeconds").getAsInt() : 60));
            URI uri = URI.create(endpoint);
            boolean secure = "https".equals(uri.getScheme());
            boolean loopback = "http".equals(uri.getScheme()) && Set.of("127.0.0.1", "localhost").contains(uri.getHost());
            if ((!secure && !loopback) || !endpoint.endsWith("/") || uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null || serverKey.length() < 32) {
                throw new IllegalArgumentException();
            }
        } catch (Exception ignored) {
            // Do not include parser/input exceptions: they may contain credentials.
            throw new IllegalStateException("Configure server-only config/muxi-identity-bridge.json with HTTPS endpoint and a dedicated 32+ character serverKey.");
        }
        http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(4)).followRedirects(HttpClient.Redirect.NEVER).build();
        NeoForge.EVENT_BUS.addListener(this::onLogin);
        NeoForge.EVENT_BUS.addListener(this::onTick);
        NeoForge.EVENT_BUS.addListener(this::onCommand);
        NeoForge.EVENT_BUS.addListener(this::onStop);
        LOG.info("UID identity / Simple Nicknames bridge loaded; refresh interval {}s", refreshTicks / 20);
    }

    private void onLogin(PlayerEvent.PlayerLoggedInEvent event) {
        // Mod automation/NPC fake players are not platform users and often have
        // no real network connection. Do not disconnect or authenticate them.
        if (event.getEntity() instanceof ServerPlayer player && !(player instanceof FakePlayer)) queue(player);
    }

    private void queue(ServerPlayer player) {
        String uid = player.getGameProfile().getName();
        if (!uid.matches("[1-9][0-9]{4,15}")) {
            player.connection.disconnect(Component.literal("请使用最新版 muxi 启动器。游戏登录身份为平台 UID。"));
            return;
        }
        UUID id = player.getUUID();
        UUID expected = UUID.nameUUIDFromBytes(("OfflinePlayer:" + uid).getBytes(StandardCharsets.UTF_8));
        if (!id.equals(expected)) {
            player.connection.disconnect(Component.literal("游戏 UUID 与平台 UID 不匹配。"));
            return;
        }
        if (stopped || pending.size() >= 32 || !pending.add(id)) return;
        HttpRequest request = HttpRequest.newBuilder(URI.create(endpoint + uid)).timeout(Duration.ofSeconds(6))
            .header("Accept", "application/json").header("X-Muxi-Server-Key", serverKey).GET().build();
        http.sendAsync(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8))
            .whenComplete((response, error) -> {
                if (!stopped) completed.add(new Result(id, uid, error == null ? response.statusCode() : 0,
                    error == null && response.body().length() <= 8192 ? response.body() : ""));
            });
    }

    private void onTick(ServerTickEvent.Post event) {
        MinecraftServer server = event.getServer();
        if (++ticks % 20 != 0) return;
        Result result;
        while ((result = completed.poll()) != null) {
            pending.remove(result.player());
            ServerPlayer player = server.getPlayerList().getPlayer(result.player());
            if (player == null || !player.getGameProfile().getName().equals(result.uid())) continue;
            if (result.status() == 404) {
                player.connection.disconnect(Component.literal("未找到该 UID，请先创建并登录 muxi 账户。"));
                continue;
            }
            if (result.status() != 200) {
                LOG.warn("Profile lookup for UID {} failed (HTTP {}); existing nickname retained.", result.uid(), result.status());
                continue;
            }
            try {
                JsonObject profile = JsonParser.parseString(result.body()).getAsJsonObject();
                if (!result.uid().equals(profile.get("loginName").getAsString())
                    || !result.uid().equals(profile.get("uid").getAsString())
                    || !result.player().toString().equals(profile.get("offlineUuid").getAsString())) throw new IllegalArgumentException();
                String display = profile.get("displayName").getAsString().strip();
                if (display.isEmpty() || display.codePointCount(0, display.length()) > 64
                    || display.codePoints().anyMatch(c -> Character.isISOControl(c) || Character.getType(c) == Character.FORMAT || c == 0xA7)) throw new IllegalArgumentException();
                var manager = SimpleNicknamesMain.NICKNAME_MANAGER;
                if (!display.equals(manager.getRawNickname(player))) {
                    // Native API, not a generated command. No command/format injection.
                    manager.setNickname(player, display);
                    LOG.info("Platform display name synchronized for UID {}", result.uid());
                }
            } catch (Exception ignored) {
                LOG.warn("Rejected invalid identity response for UID {}", result.uid());
            }
        }
        if (ticks % refreshTicks == 0) server.getPlayerList().getPlayers().forEach(this::queue);
    }

    private void onCommand(CommandEvent event) {
        var source = event.getParseResults().getContext().getSource();
        if (!source.isPlayer()) return; // Console remains available for recovery.
        String command = event.getParseResults().getReader().getString().strip().split("\\s+", 2)[0].toLowerCase(Locale.ROOT);
        command = command.substring(command.lastIndexOf(':') + 1);
        if (NICK_COMMANDS.contains(command)) {
            source.sendFailure(Component.literal("请在 muxi 账户中心修改昵称，游戏内会自动同步。"));
            event.setCanceled(true);
        }
    }

    private void onStop(ServerStoppedEvent event) {
        stopped = true;
        pending.clear();
        completed.clear();
        http.shutdownNow();
    }
}
