package net.muxigame.minigames.horseclientqa.mixin;

import net.minecraft.SystemReport;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** QA only: this host's WMI hardware diagnostics block before Minecraft startup. */
@Mixin(value=SystemReport.class,remap=false)
public abstract class SkipHardwareProbeMixin {
    @Inject(method="putHardware",at=@At("HEAD"),cancellable=true)
    private void task2$skipBlockedDiagnostics(CallbackInfo callback){callback.cancel();}
}
