package net.muxigame.terminal.smoke.mixin;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
@Mixin(targets="net.muxigame.core.client.TerminalPassportApi",remap=false)
public abstract class NativeClientTransportMixin {
 @ModifyArg(method="*",at=@At(value="INVOKE",target="Ljava/net/http/HttpRequest;newBuilder(Ljava/net/URI;)Ljava/net/http/HttpRequest$Builder;"),index=0)
 private static java.net.URI qaEndpoint(java.net.URI original){
  try{var result=net.muxigame.terminal.smoke.NativeIssuerEndpoint.rewrite(original);net.muxigame.terminal.smoke.NativeProofDiagnostics.record("endpoint","canonicalIssuer="+"account.muxigame.com".equals(original.getHost())+" loopbackTLS="+(("localhost".equals(result.getHost())||"127.0.0.1".equals(result.getHost()))&&"https".equals(result.getScheme())));return result;}
  catch(RuntimeException error){net.muxigame.terminal.smoke.NativeProofDiagnostics.record("endpoint","errorTypes="+net.muxigame.terminal.smoke.NativeProofDiagnostics.errors(error));throw error;}
 }
 @Inject(method="request",at=@At("HEAD"))
 private static void qaRequest(java.util.function.Consumer<String> callback,org.spongepowered.asm.mixin.injection.callback.CallbackInfo ci){
  var mc=net.minecraft.client.Minecraft.getInstance();var connection=mc.getConnection();
  boolean channel=connection!=null&&net.neoforged.neoforge.network.registration.NetworkRegistry.hasChannel(connection,net.muxigame.core.feature.login.TerminalPassportNetwork.Request.TYPE.id());
  net.muxigame.terminal.smoke.NativeProofDiagnostics.record("request","player="+(mc.player!=null)+" connection="+(connection!=null)+" requestChannel="+channel+" nativePipeShape="+net.muxigame.core.client.TerminalCredentialBrokerClient.valid(System.getenv("MUXI_TERMINAL_CREDENTIAL_PIPE"),System.getenv("MUXI_TERMINAL_CREDENTIAL_BROKER")));
 }
 @Redirect(method="*",at=@At(value="INVOKE",target="Lnet/muxigame/core/client/TerminalCredentialBrokerClient;fetch(Ljava/lang/String;Ljava/lang/String;)Ljava/util/concurrent/CompletableFuture;"))
 private static java.util.concurrent.CompletableFuture<String> qaFetch(String pipe,String secret){
  var future=net.muxigame.core.client.TerminalCredentialBrokerClient.fetch(pipe,secret);
  future.whenComplete((credential,error)->net.muxigame.terminal.smoke.NativeProofDiagnostics.record("pipe","responseShape="+(credential!=null&&credential.matches("[A-Za-z0-9_-]{54}"))+" errorTypes="+net.muxigame.terminal.smoke.NativeProofDiagnostics.errors(error)));
  return future;
 }
 @Inject(method="bind",at=@At("HEAD"))
 private static void qaProofStart(org.spongepowered.asm.mixin.injection.callback.CallbackInfo ci){net.muxigame.terminal.smoke.NativeProofDiagnostics.record("binding-build","entered=true");}
 @Inject(method="finish",at=@At("HEAD"))
 private static void qaFinish(String value,org.spongepowered.asm.mixin.injection.callback.CallbackInfo ci){net.muxigame.terminal.smoke.NativeProofDiagnostics.record("native-result","nonEmpty="+(value!=null&&!value.isEmpty()));}
 @Redirect(method="*",at=@At(value="INVOKE",target="Ljava/net/http/HttpClient;sendAsync(Ljava/net/http/HttpRequest;Ljava/net/http/HttpResponse$BodyHandler;)Ljava/util/concurrent/CompletableFuture;"))
 private static java.util.concurrent.CompletableFuture<java.net.http.HttpResponse<String>> qaSend(java.net.http.HttpClient client,java.net.http.HttpRequest request,java.net.http.HttpResponse.BodyHandler<String> handler){
  final java.util.concurrent.CompletableFuture<java.net.http.HttpResponse<String>> future;
  try{future=client.sendAsync(request,handler);}
  catch(RuntimeException error){net.muxigame.terminal.smoke.NativeProofDiagnostics.record("http-sync","errorTypes="+net.muxigame.terminal.smoke.NativeProofDiagnostics.errors(error));throw error;}
  future.whenComplete((response,error)->net.muxigame.terminal.smoke.NativeProofDiagnostics.record("http","status="+(response==null?-1:response.statusCode())+" errorTypes="+net.muxigame.terminal.smoke.NativeProofDiagnostics.errors(error)));
  return future;
 }
}
