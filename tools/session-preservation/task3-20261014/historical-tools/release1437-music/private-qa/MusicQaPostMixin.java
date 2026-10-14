package net.muxigame.musiccompat.qa.mixin;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import net.muxigame.musiccompat.burn.BurnCoordinator;
import java.util.function.Consumer;
@Mixin(targets="net.muxigame.musiccompat.burn.OssBurnBackend",remap=false)
public abstract class MusicQaPostMixin {
 private static final java.util.concurrent.atomic.AtomicBoolean muxiPrivatePostConsumed=new java.util.concurrent.atomic.AtomicBoolean();
 @Inject(method="upload",at=@At("HEAD"))
 private void once(BurnCoordinator.Ticket ticket,BurnCoordinator.Checked audio,BurnCoordinator.Cancellation cancellation,Consumer<BurnCoordinator.Progress> progress,CallbackInfo callback)throws java.io.IOException{
  if(!Boolean.getBoolean("muxi.music.qa.enabled")||!ticket.jobId().equals("ea8bbe98-43f0-462c-bd51-fec644838448")||audio.bytes()!=132344||!muxiPrivatePostConsumed.compareAndSet(false,true))throw new java.io.IOException("qa_exact_once_post_refused");
 }
}
