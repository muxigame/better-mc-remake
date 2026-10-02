package net.muxigame.terminal.smoke.mixin;
import net.muxigame.terminal.smoke.SSOFixture;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
@Mixin(targets="net.muxigame.core.client.TerminalPassportApi",remap=false)
public abstract class PassportRequestFixtureMixin {
    @Inject(method="request",at=@At("HEAD"),cancellable=true) private static void fixture(java.util.function.Consumer<String> callback,CallbackInfo ci){SSOFixture.requestCalls++;SSOFixture.pending=callback;ci.cancel();}
    @Inject(method="validate",at=@At("HEAD"),cancellable=true) private static void validate(java.util.function.Consumer<Boolean> callback,CallbackInfo ci){SSOFixture.validate(callback);ci.cancel();}
}
