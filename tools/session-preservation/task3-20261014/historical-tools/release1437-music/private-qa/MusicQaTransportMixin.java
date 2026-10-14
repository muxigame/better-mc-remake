package net.muxigame.musiccompat.qa.mixin;
import net.muxigame.musiccompat.qa.MusicQaEndpoint;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.ModifyVariable;
@Mixin(targets="net.muxigame.musiccompat.burn.BurnHttp",remap=false)
public abstract class MusicQaTransportMixin {
 @ModifyVariable(method="send",at=@At("HEAD"),argsOnly=true,ordinal=0)
 private static java.net.http.HttpRequest rewrite(java.net.http.HttpRequest request){return MusicQaEndpoint.rewrite(request);}
}
