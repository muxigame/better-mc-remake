package net.muxigame.minigames.horse;

import com.google.gson.*;
import java.net.*;
import java.net.http.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;
import java.util.function.Consumer;

/** Same service credential as game settlement, with a separate commerce endpoint. */
final class HorseTradeTransport implements AutoCloseable {
    record Reply(int status,JsonObject body,String error){}
    private final URI base;private final String key;
    private final HttpClient http=HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).connectTimeout(Duration.ofSeconds(4)).followRedirects(HttpClient.Redirect.NEVER).build();
    private final ExecutorService worker=Executors.newThreadPerTaskExecutor(Thread.ofVirtual().name("muxi-horse-trades-",0).factory());
    HorseTradeTransport(){
        var env=System.getenv();key=env.getOrDefault("MUXI_GAME_PLATFORM_KEY",env.getOrDefault("MUXI_TASK_POINTS_KEY",""));
        String raw=env.getOrDefault("MUXI_GAME_PLATFORM_URL",env.getOrDefault("MUXI_TASK_POINTS_URL","").replace("/task-claims","/"));URI valid=null;
        try{var url=URI.create(raw);boolean local=Set.of("localhost","127.0.0.1","::1","[::1]").contains(Objects.toString(url.getHost(),""));
            if(key.length()>=32&&url.getHost()!=null&&url.getUserInfo()==null&&url.getQuery()==null&&url.getFragment()==null&&"/api/internal/game/".equals(url.getPath())&&("https".equals(url.getScheme())||local&&"http".equals(url.getScheme())))valid=url;
        }catch(IllegalArgumentException ignored){}base=valid;
    }
    boolean configured(){return base!=null;}
    void send(String path,JsonObject event,Consumer<Reply> done){
        worker.submit(()->{
            Reply reply;
            try{var request=HttpRequest.newBuilder(base.resolve(path)).timeout(Duration.ofSeconds(6)).header("x-muxi-server-key",key);
                if(event==null)request.GET();else request.header("Content-Type","application/json").POST(HttpRequest.BodyPublishers.ofString(event.toString()));
                var response=http.send(request.build(),HttpResponse.BodyHandlers.ofString());
                if(response.body().length()>262144)throw new IllegalArgumentException("Commerce response too large");
                var body=JsonParser.parseString(response.body()).getAsJsonObject();reply=new Reply(response.statusCode(),body,body.has("detail")?body.get("detail").getAsString():"");
            }catch(Exception failure){reply=new Reply(503,new JsonObject(),failure.getClass().getSimpleName());}
            done.accept(reply);
        });
    }
    public void close(){worker.shutdownNow();}
}
