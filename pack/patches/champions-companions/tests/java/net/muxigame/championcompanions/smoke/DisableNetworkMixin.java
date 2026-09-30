package net.muxigame.championcompanions.smoke.mixin;

import java.net.InetAddress;
import net.minecraft.server.network.ServerConnectionListener;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Redirect;

/** Test-only: keep the disposable server headless when loopback selectors are unavailable. */
@Mixin(targets="net.minecraft.server.dedicated.DedicatedServer", remap=false)
public abstract class DisableNetworkMixin {
    @Redirect(method="initServer", at=@At(value="INVOKE",
        target="Lnet/minecraft/server/network/ServerConnectionListener;startTcpServerListener(Ljava/net/InetAddress;I)V"))
    private void muxi$skipTcpListener(ServerConnectionListener listener, InetAddress address, int port) {
        // The smoke test only exercises server lifecycle and entity logic.
    }
}
