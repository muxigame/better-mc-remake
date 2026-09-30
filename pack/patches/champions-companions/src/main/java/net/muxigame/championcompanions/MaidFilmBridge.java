package net.muxigame.championcompanions;

import com.github.tartaricacid.touhoulittlemaid.api.event.MaidAndItemTransformEvent;
import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.Tag;
import net.minecraft.world.entity.LivingEntity;
import net.neoforged.neoforge.common.NeoForge;
import java.util.Map;
import java.util.WeakHashMap;

/** Thin integration against Touhou Little Maid 1.5.3's documented transform events. */
public final class MaidFilmBridge {
    private static final Map<EntityMaid, CompoundTag> PENDING = new WeakHashMap<>();
    private MaidFilmBridge() {}

    public static void register() {
        NeoForge.EVENT_BUS.addListener(MaidFilmBridge::toItem);
        NeoForge.EVENT_BUS.addListener(MaidFilmBridge::toMaid);
    }

    private static void toItem(MaidAndItemTransformEvent.ToItem event) {
        event.getData().put(FilmChampionSnapshot.KEY, FilmChampionSnapshot.capture(event.getMaid()));
    }

    private static void toMaid(MaidAndItemTransformEvent.ToMaid event) {
        if (event.getData().contains(FilmChampionSnapshot.KEY, Tag.TAG_COMPOUND))
            PENDING.put(event.getMaid(), event.getData().getCompound(FilmChampionSnapshot.KEY).copy());
    }

    public static boolean restoreIfPending(LivingEntity entity) {
        if (!(entity instanceof EntityMaid maid)) return false;
        var snapshot = PENDING.remove(maid);
        if (snapshot == null) return false;
        if (!FilmChampionSnapshot.restore(maid, snapshot)) PENDING.put(maid, snapshot);
        return true; // never fall through into a fresh 33% roll for this revived maid
    }
}
