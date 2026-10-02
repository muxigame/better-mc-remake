package net.muxigame.terminal.qa.mixin;
import net.minecraft.SystemReport;
import oshi.SystemInfo;
import oshi.util.GlobalConfig;
import oshi.util.platform.windows.WmiQueryHandler;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
/** QA-only hardware-report query deadline; no auth, license, networking or gameplay hooks. */
@Mixin(value=SystemReport.class,remap=false)
public abstract class HardwareWmiTimeoutQAMixin {
 @Inject(method="putHardware(Loshi/SystemInfo;)V",at=@At("HEAD"))
 private void qa$boundedHardwareReport(SystemInfo info,CallbackInfo ci){
  GlobalConfig.set(GlobalConfig.OSHI_UTIL_WMI_TIMEOUT,2000);
  int actual=WmiQueryHandler.createInstance().getWmiTimeout();
  if(actual!=2000)throw new IllegalStateException("QA WMI query deadline was not applied: "+actual);
  System.out.println("QA_HARDWARE_WMI_TIMEOUT_MS="+actual);
 }
}
