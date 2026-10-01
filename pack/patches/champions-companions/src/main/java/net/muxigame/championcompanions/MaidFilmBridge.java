package net.muxigame.championcompanions;

import com.github.tartaricacid.touhoulittlemaid.api.event.MaidAndItemTransformEvent;
import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.Tag;
import net.minecraft.world.entity.LivingEntity;
import net.neoforged.neoforge.common.NeoForge;

/** Thin integration against Touhou Little Maid 1.5.3's documented transform events. */
public final class MaidFilmBridge {
    private MaidFilmBridge() {}

    public static void register() {
        NeoForge.EVENT_BUS.addListener(MaidFilmBridge::toItem);
        NeoForge.EVENT_BUS.addListener(MaidFilmBridge::toMaid);
    }

    private static void toItem(MaidAndItemTransformEvent.ToItem event) {
        // A second death can happen before the first restore tick. Carry the
        // original snapshot forward rather than capturing an empty attachment.
        var data = event.getMaid().getPersistentData();
        var snapshot = data.contains(FilmChampionSnapshot.KEY, Tag.TAG_COMPOUND)
            ? data.getCompound(FilmChampionSnapshot.KEY).copy()
            : FilmChampionSnapshot.capture(event.getMaid());
        event.getData().put(FilmChampionSnapshot.KEY, snapshot);
    }

    private static void toMaid(MaidAndItemTransformEvent.ToMaid event) {
        if (event.getData().contains(FilmChampionSnapshot.KEY, Tag.TAG_COMPOUND)) {
            var snapshot = event.getData().getCompound(FilmChampionSnapshot.KEY).copy();
            // Photos/smart slabs use Entity.load after this same event, which
            // replaces persistentData. Put pending into the incoming NBT too.
            var persistent = event.getData().getCompound("NeoForgeData").copy();
            persistent.put(FilmChampionSnapshot.KEY, snapshot.copy());
            persistent.putBoolean(CompanionRules.ROLLED, true);
            event.getData().put("NeoForgeData", persistent);
            // Native filmToMaid calls readAdditionalSaveData, not Entity.load.
            // Store on the entity: NeoForgeData survives saving/unloading even
            // if restoration cannot finish before the next save or shutdown.
            event.getMaid().getPersistentData().put(FilmChampionSnapshot.KEY,
                snapshot);
            // addFreshEntity fires Champions' new-entity lottery before Post
            // tick. AdditionalSaveData does not restore Entity's NeoForgeData.
            // Block that lottery now; restore() later reinstates original flags.
            event.getMaid().getPersistentData().putBoolean(CompanionRules.ROLLED, true);
        }
    }

    public static boolean restoreIfPending(LivingEntity entity) {
        if (!(entity instanceof EntityMaid maid)) return false;
        var data = maid.getPersistentData();
        if (!data.contains(FilmChampionSnapshot.KEY, Tag.TAG_COMPOUND)) return false;
        var snapshot = data.getCompound(FilmChampionSnapshot.KEY).copy();
        if (FilmChampionSnapshot.restore(maid, snapshot)) data.remove(FilmChampionSnapshot.KEY);
        return true; // never fall through into a fresh 33% roll for this revived maid
    }
}
