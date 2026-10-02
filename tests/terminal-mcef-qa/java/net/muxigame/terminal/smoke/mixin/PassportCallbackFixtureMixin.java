package net.muxigame.terminal.smoke.mixin;
import net.minecraft.util.thread.BlockableEventLoop;
import net.muxigame.terminal.smoke.SSOFixture;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
@Mixin(value=BlockableEventLoop.class,remap=false)
public abstract class PassportCallbackFixtureMixin {
    @ModifyVariable(method="execute",at=@At("HEAD"),argsOnly=true) private Runnable fixture(Runnable task){return task.getClass().getName().startsWith("net.muxigame.terminal.client.TerminalPassportNavigation")?SSOFixture.wrap(task):task;}
}
