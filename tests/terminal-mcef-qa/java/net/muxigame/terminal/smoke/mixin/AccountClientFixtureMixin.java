package net.muxigame.terminal.smoke.mixin;
import com.cinemamod.mcef.MCEFBrowser;
import net.muxigame.terminal.client.TerminalBrowserSession;
import net.muxigame.terminal.smoke.SSOFixture;
import org.spongepowered.asm.mixin.*;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
@Mixin(targets="net.muxigame.terminal.client.TerminalViewClient",remap=false)
public abstract class AccountClientFixtureMixin {
    @Shadow @Final MCEFBrowser browser;
    @Inject(method="<init>",at=@At("RETURN")) private void fixture(TerminalBrowserSession.Kind kind,String url,CallbackInfo ci){if(kind==TerminalBrowserSession.Kind.ACCOUNT)SSOFixture.install(browser);}
}
