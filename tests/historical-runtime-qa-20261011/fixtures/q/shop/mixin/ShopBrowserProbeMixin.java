package net.muxigame.minigames.horseqa.mixin;
import net.muxigame.minigames.horseqa.UmamusumeShopClientQA;
import net.muxigame.terminal.client.TerminalBrowserSession;
import org.cef.CefClient;
import org.spongepowered.asm.mixin.*;import org.spongepowered.asm.mixin.injection.*;import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
/** Test-only observer installed before the built-in browser creates its JS context. */
@Mixin(targets="net.muxigame.terminal.client.TerminalViewClient",remap=false)
public abstract class ShopBrowserProbeMixin {
 @Shadow @Final private CefClient client;
 @Inject(method="<init>",at=@At("TAIL")) private void horseShopObserve(TerminalBrowserSession.Kind kind,String url,CallbackInfo ci){
  if(Boolean.getBoolean("muxi.horse.shopClientQa")&&kind==TerminalBrowserSession.Kind.BUILTIN)client.addMessageRouter(UmamusumeShopClientQA.makeRouter());
 }
}
