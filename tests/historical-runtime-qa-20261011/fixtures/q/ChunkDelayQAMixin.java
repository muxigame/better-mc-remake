package net.muxigame.localdebug.mixin;
import net.minecraft.client.multiplayer.ClientPacketListener;
import net.minecraft.network.protocol.game.ClientboundLevelChunkWithLightPacket;
import net.muxigame.localdebug.ChunkDelayFixture;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
@Mixin(ClientPacketListener.class)
public abstract class ChunkDelayQAMixin {
    @Inject(method="handleLevelChunkWithLight",at=@At("HEAD"),cancellable=true)
    private void delay(ClientboundLevelChunkWithLightPacket packet,CallbackInfo ci){
        if(ChunkDelayFixture.accept((ClientPacketListener)(Object)this,packet))ci.cancel();
    }
}
