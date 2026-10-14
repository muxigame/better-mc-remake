import java.net.URI;
import java.net.http.HttpRequest;
import net.muxigame.musiccompat.qa.MusicQaEndpoint;
public class EndpointTest {
 static int checks=0;
 static void yes(boolean x){if(!x)throw new AssertionError();checks++;}
 static HttpRequest post(String s){return HttpRequest.newBuilder(URI.create(s)).POST(HttpRequest.BodyPublishers.ofString("{}")).header("Authorization","Bearer offline-fixture").build();}
 public static void main(String[] args){
  System.setProperty("muxi.music.qa.enabled","true");System.setProperty("muxi.sso.realNative","true");System.setProperty("muxi.sso.nativeAuthURL","https://127.0.0.1:38837");
  var p=MusicQaEndpoint.rewrite(post("https://account.muxigame.com/api/launcher/music/playback"));yes(p.uri().toString().equals("https://127.0.0.1:38837/api/launcher/music/playback"));yes(p.headers().firstValue("Authorization").orElse("").equals("Bearer offline-fixture"));yes(p.headers().firstValue("X-Muxi-Music-QA").orElse("").length()>=32);
  var oauth=post("https://account.muxigame.com/oauth/token");yes(MusicQaEndpoint.rewrite(oauth)==oauth);
  var oss=post("https://muxigame-music-cn-hangzhou.oss-cn-hangzhou.aliyuncs.com/media/10000/x.mp3");yes(MusicQaEndpoint.rewrite(oss)==oss);
  System.setProperty("muxi.sso.nativeAuthURL","https://example.com");try{MusicQaEndpoint.rewrite(post("https://account.muxigame.com/api/launcher/music/playback"));throw new AssertionError();}catch(IllegalStateException expected){checks++;}
  System.out.println("Endpoint offline checks passed: "+checks);
 }
}
