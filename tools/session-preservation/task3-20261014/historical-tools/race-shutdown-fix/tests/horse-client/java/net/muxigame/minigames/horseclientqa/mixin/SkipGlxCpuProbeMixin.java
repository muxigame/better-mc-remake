package net.muxigame.minigames.horseclientqa.mixin;

import com.mojang.blaze3d.platform.GLX;
import oshi.hardware.CentralProcessor;
import oshi.hardware.HardwareAbstractionLayer;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Redirect;

/** QA only: GLX catches unavailable CPU diagnostics and still initializes the real GL renderer. */
@Mixin(value=GLX.class,remap=false)
public abstract class SkipGlxCpuProbeMixin {
    @Redirect(method="_init",at=@At(value="INVOKE",target="Loshi/hardware/HardwareAbstractionLayer;getProcessor()Loshi/hardware/CentralProcessor;"))
    private static CentralProcessor task2$skipBlockedCpu(HardwareAbstractionLayer ignored){
        throw new IllegalStateException("Private QA omits blocked WMI CPU diagnostics");
    }
}
