import net.muxigame.core.client.TerminalCredentialBrokerClient;
import java.net.URI;
import java.net.http.*;
import java.time.Duration;
import java.util.UUID;
import java.nio.charset.StandardCharsets;
import net.muxigame.terminal.smoke.NativeIssuerEndpoint;
/** Small actual Java pipe/HTTPS probe. No Minecraft, CEF or identity substitute. */
public final class JavaProofProbe {
 public static void main(String[] args)throws Exception{
  String phase="pipe";
  try{
   String credential=Boolean.getBoolean("muxi.sso.slowPipeReader")?"":TerminalCredentialBrokerClient.fetch(System.getenv("MUXI_TERMINAL_CREDENTIAL_PIPE"),System.getenv("MUXI_TERMINAL_CREDENTIAL_BROKER")).get();
   if(Boolean.getBoolean("muxi.sso.slowPipeReader")){
    String pipe=System.getenv("MUXI_TERMINAL_CREDENTIAL_PIPE"),secret=System.getenv("MUXI_TERMINAL_CREDENTIAL_BROKER");
    try(var channel=new java.io.RandomAccessFile("\\\\.\\pipe\\"+pipe,"rw")){
     channel.write((secret+"\n").getBytes(StandardCharsets.US_ASCII));Thread.sleep(250);
     StringBuilder line=new StringBuilder();for(int i=0;i<55;i++){int v=channel.read();if(v==10||v<0)break;line.append((char)v);}credential=line.toString();
    }
   }
   if(!credential.matches("[A-Za-z0-9_-]{54}"))throw new IllegalStateException("empty-native-pipe-response");
   phase="endpoint";
   URI endpoint=NativeIssuerEndpoint.rewrite(URI.create("https://account.muxigame.com/oauth/userinfo"));
   phase="https";
   var client=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).followRedirects(HttpClient.Redirect.NEVER).build();
   var request=HttpRequest.newBuilder(endpoint).timeout(Duration.ofSeconds(3)).header("Authorization","Bearer "+credential).GET().build();
   var response=client.sendAsync(request,HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8)).get();
   boolean shape=response.statusCode()==200 && response.body().contains("\"muxi_uid\"");
   System.out.println("{\"phase\":\"https\",\"status\":"+response.statusCode()+",\"validAccountShape\":"+shape+",\"minecraftStarted\":false}");
   if(!shape)System.exit(2);
  }catch(Throwable error){
   StringBuilder chain=new StringBuilder();for(Throwable e=error;e!=null;e=e.getCause()){if(chain.length()>0)chain.append(',');chain.append('\"').append(e.getClass().getName()).append('\"');}
   System.out.println("{\"phase\":\""+phase+"\",\"errorTypes\":["+chain+"],\"minecraftStarted\":false}");System.exit(1);
  }
 }
}
