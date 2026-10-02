package net.muxigame.terminal.smoke.mixin;
import net.muxigame.terminal.smoke.SSOFixture;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
@Mixin(targets="net.muxigame.terminal.client.TerminalPassportNavigation",remap=false)
public abstract class PassportIdentityFixtureMixin {
    @Inject(method="open",at=@At("HEAD")) private static void enter(CallbackInfo ci){SSOFixture.enter();}
    @Inject(method="open",at=@At("RETURN")) private static void leave(CallbackInfo ci){SSOFixture.leave();}
}
