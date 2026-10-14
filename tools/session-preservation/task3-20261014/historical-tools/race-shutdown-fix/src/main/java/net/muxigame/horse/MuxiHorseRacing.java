package net.muxigame.horse;
import net.neoforged.fml.common.Mod;
import net.neoforged.bus.api.IEventBus;
@Mod("muxi_horse_racing")
public final class MuxiHorseRacing {
    public MuxiHorseRacing(IEventBus ignored) { net.muxigame.minigames.horse.HorseRacing.install(); net.muxigame.minigames.horse.EntryReadyNetwork.install(ignored); }
}
