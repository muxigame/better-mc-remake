package net.muxigame.terminal.qa.mixin;
import com.cinemamod.mcef.MCEFDownloader;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;
@Mixin(value=MCEFDownloader.class,remap=false)
public abstract class OfflineMcefMixin {
 @Inject(method="downloadJavaCefChecksum",at=@At("HEAD"),cancellable=true)
 private void qa$existing(CallbackInfoReturnable<Boolean> result){result.setReturnValue(true);}
}
