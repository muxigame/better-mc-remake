package net.muxigame.core.client;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientPacketListener;
import net.muxigame.core.feature.login.TerminalPassportNetwork;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.neoforge.client.event.ClientPlayerNetworkEvent;
import net.neoforged.neoforge.client.event.ClientTickEvent;
import net.neoforged.neoforge.network.PacketDistributor;
import net.neoforged.neoforge.network.registration.NetworkRegistry;
import java.net.URI;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

/** One existing launcher access token is passed unchanged to Auth and the website. */
public final class TerminalPassportApi {
    private static final HttpClient HTTP=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).followRedirects(HttpClient.Redirect.NEVER).build();
    private static final String PIPE=System.getenv("MUXI_TERMINAL_CREDENTIAL_PIPE");
    private static final String CAPABILITY=System.getenv("MUXI_TERMINAL_CREDENTIAL_BROKER");
    private static Pending pending;
    private static volatile ClientPacketListener authorizedConnection;
    private static volatile String gameSession="";
    private static volatile long verifiedUid;
    private static volatile String lastAccess="";
    private static long nextValidation;
    private static final class Pending {
        final String id=UUID.randomUUID().toString();
        final ClientPacketListener connection;
        final Consumer<String> callback;
        final long expires=System.nanoTime()+25_000_000_000L;
        String session="",access="";
        long uid;
        Pending(ClientPacketListener connection,Consumer<String> callback){this.connection=connection;this.callback=callback;}
        @Override public String toString(){return "TerminalAccessPending[account token=<redacted>]";}
    }
    private TerminalPassportApi() {}
    public static void register(IEventBus bus){
        TerminalPassportNetwork.clientReceiver(TerminalPassportApi::receive);
        bus.addListener(TerminalPassportApi::tick);
        bus.addListener(TerminalPassportApi::logout);
    }
    public static void request(Consumer<String> callback){
        var mc=Minecraft.getInstance();var connection=mc.getConnection();
        if(pending!=null || mc.player==null || connection==null || !TerminalCredentialBrokerClient.valid(PIPE,CAPABILITY)
            || !NetworkRegistry.hasChannel(connection,TerminalPassportNetwork.Request.TYPE.id())){callback.accept("");return;}
        Pending value=new Pending(connection,callback);pending=value;
        PacketDistributor.sendToServer(new TerminalPassportNetwork.Request(value.id,"",0));
    }
    private static boolean current(Pending value){return pending==value && Minecraft.getInstance().player!=null && Minecraft.getInstance().getConnection()==value.connection;}
    private static void receive(TerminalPassportNetwork.Result packet){
        var value=pending;
        if(value==null || !value.id.equals(packet.requestId()) || !current(value))return;
        if(packet.status()==1 && value.session.isEmpty() && packet.gameSession().matches("[0-9a-f-]{36}") && packet.uid()>=10000){
            value.session=packet.gameSession();value.uid=packet.uid();
            bind(value);
        }else if(packet.status()==2 && value.session.equals(packet.gameSession()) && value.uid==packet.uid()){
            // Account switch/logout during the HTTPS/MC round trip cannot open an old account page.
            TerminalCredentialBrokerClient.fetch(PIPE,CAPABILITY).whenComplete((access,error)->Minecraft.getInstance().execute(()->{
                if(!current(value))return;
                if(error!=null || !validAccess(access)){
                    PacketDistributor.sendToServer(new TerminalPassportNetwork.Request(UUID.randomUUID().toString(),value.session,2));
                    revoke();finish("");return;
                }
                authorizedConnection=value.connection;gameSession=value.session;verifiedUid=value.uid;lastAccess=access;
                JsonObject payload=new JsonObject();payload.addProperty("accessToken",access);payload.addProperty("uid",value.uid);
                payload.addProperty("gameSession",value.session);finish(payload.toString());
            }));
        }else{finish("");}
    }
    private static void bind(Pending value){
        TerminalCredentialBrokerClient.fetch(PIPE,CAPABILITY).thenCompose(access->{
            if(!validAccess(access))throw new IllegalStateException();
            value.access=access;
            JsonObject body=new JsonObject();body.addProperty("requestId",value.id);body.addProperty("gameSession",value.session);
            var request=HttpRequest.newBuilder(URI.create("https://account.muxigame.com/api/launcher/minecraft/terminal-context/bind"))
                .timeout(Duration.ofSeconds(4)).header("Authorization","Bearer "+access).header("Content-Type","application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body.toString(),StandardCharsets.UTF_8)).build();
            return HTTP.sendAsync(request,HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        }).whenComplete((response,error)->Minecraft.getInstance().execute(()->{
            if(!current(value))return;
            try{
                if(error!=null || response.statusCode()!=200 || response.body().length()>4096)throw new IllegalStateException();
                var body=JsonParser.parseString(response.body()).getAsJsonObject();
                if(body.get("uid").getAsLong()!=value.uid || !body.get("requestId").getAsString().equals(value.id)
                    || !body.get("gameSession").getAsString().equals(value.session))throw new IllegalStateException();
                PacketDistributor.sendToServer(new TerminalPassportNetwork.Request(value.id,value.session,1));
            }catch(RuntimeException ignored){finish("");}
        }));
    }
    private static boolean validAccess(String value){return value!=null && value.matches("[A-Za-z0-9_-]{43}");}
    /** CEF/native HTTP thread only: re-read the broker so stale cookies/tokens cannot select another account. */
    public static String freshAccessToken(){
        var connection=authorizedConnection;
        if(connection==null || Minecraft.getInstance().getConnection()!=connection || Minecraft.getInstance().player==null)return "";
        try{
            String access=TerminalCredentialBrokerClient.fetch(PIPE,CAPABILITY).get(7,TimeUnit.SECONDS);
            if(validAccess(access) && authorizedConnection==connection && Minecraft.getInstance().getConnection()==connection)return access;
        }catch(Exception ignored){}
        Minecraft.getInstance().execute(TerminalPassportApi::revoke);
        return "";
    }
    public static void validate(Consumer<Boolean> callback){
        var connection=Minecraft.getInstance().getConnection();
        if(connection==null || authorizedConnection!=connection){callback.accept(false);return;}
        TerminalCredentialBrokerClient.fetch(PIPE,CAPABILITY).whenComplete((access,error)->Minecraft.getInstance().execute(()->{
            boolean valid=error==null && validAccess(access) && authorizedConnection==connection && Minecraft.getInstance().getConnection()==connection;
            if(!valid)revoke();
            callback.accept(valid);
        }));
    }
    private static void revoke(){
        var connection=authorizedConnection;String session=gameSession;
        authorizedConnection=null;gameSession="";lastAccess="";verifiedUid=0;
        if(connection!=null && connection==Minecraft.getInstance().getConnection() && !session.isEmpty())
            PacketDistributor.sendToServer(new TerminalPassportNetwork.Request(UUID.randomUUID().toString(),session,2));
    }
    public static void cancel(){pending=null;}
    private static void finish(String payload){var value=pending;pending=null;if(value!=null)value.callback.accept(payload);}
    private static void tick(ClientTickEvent.Post event){
        long now=System.nanoTime();
        if(pending!=null && now>pending.expires)finish("");
        if(authorizedConnection!=null && now>nextValidation && pending==null){
            nextValidation=now+15_000_000_000L;
            validate(valid->{if(valid)TerminalCredentialBrokerClient.fetch(PIPE,CAPABILITY).whenComplete((access,error)->Minecraft.getInstance().execute(()->{
                if(error==null && validAccess(access) && !access.equals(lastAccess) && pending==null && authorizedConnection!=null)request(value->{});
            }));});
        }
    }
    private static void logout(ClientPlayerNetworkEvent.LoggingOut event){pending=null;authorizedConnection=null;gameSession="";lastAccess="";verifiedUid=0;}
}
