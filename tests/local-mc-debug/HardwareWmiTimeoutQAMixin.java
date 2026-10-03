package net.muxigame.localdebug.mixin;
import net.minecraft.SystemReport;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import oshi.util.GlobalConfig;
import oshi.SystemInfo;
import oshi.util.platform.windows.WmiQueryHandler;

/** QA-only fix before OSHI hardware probing; never restarts WMI/VM services. */
@Mixin(value=SystemReport.class,remap=false)
public abstract class HardwareWmiTimeoutQAMixin {
    @Inject(method="putHardware(Loshi/SystemInfo;)V",at=@At("HEAD"))
    private void localDebugWmi(SystemInfo info,CallbackInfo ci){if(!System.getProperty("qa.local.runId","").isBlank()){GlobalConfig.set(GlobalConfig.OSHI_UTIL_WMI_TIMEOUT,2000);int actual=WmiQueryHandler.createInstance().getWmiTimeout();if(actual!=2000)throw new IllegalStateException("QA WMI timeout was not applied: "+actual);System.out.println("QA_HARDWARE_WMI_TIMEOUT_MS="+actual);}}
}
