package net.muxigame.minigames.horseclientqa.mixin;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;
@Mixin(targets="com.cinemamod.mcef.MCEFDownloader",remap=false)
public abstract class OfflineMcefMixin {
 @Inject(method="downloadJavaCefChecksum",at=@At("HEAD"),cancellable=true)
 private void qa$existing(CallbackInfoReturnable<Boolean> result){result.setReturnValue(true);}
}
