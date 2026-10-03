package net.muxigame.terminal.smoke.mixin;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
/** QA-only HTTPS address rewrite, preserving actual token, HTTP method, body, TLS and callbacks. */
@Mixin(targets="net.muxigame.terminal.client.TerminalFriendsTransport",remap=false)
public abstract class NativeFriendsTransportMixin {
 @ModifyArg(method="request",at=@At(value="INVOKE",target="Ljava/net/http/HttpRequest;newBuilder(Ljava/net/URI;)Ljava/net/http/HttpRequest$Builder;"),index=0)
 private java.net.URI endpoint(java.net.URI original){
  String target=System.getProperty("muxi.sso.nativeSiteURL");
  if(target==null || !target.matches("https://localhost:[0-9]{1,5}") || !"https".equals(original.getScheme()) || !"mc.muxigame.com".equals(original.getHost()) || original.getPort()!=-1 || original.getRawUserInfo()!=null || !original.getPath().startsWith("/api/v1/player/social"))throw new IllegalStateException("Unexpected native social fixture target");
  return java.net.URI.create(target.replace("https://localhost:","https://127.0.0.1:")).resolve(original.getRawPath());
 }
}
