package net.muxigame.terminal.smoke.mixin;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
@Mixin(targets="net.muxigame.core.feature.login.TerminalPassportServer",remap=false)
public abstract class NativeServerTransportMixin {
 @ModifyArg(method="post",at=@At(value="INVOKE",target="Ljava/net/http/HttpRequest;newBuilder(Ljava/net/URI;)Ljava/net/http/HttpRequest$Builder;"),index=0)
 private static java.net.URI qaEndpoint(java.net.URI original){return net.muxigame.terminal.smoke.NativeIssuerEndpoint.rewrite(original);}
}
