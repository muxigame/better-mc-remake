package net.muxigame.championcompanions;

import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.ListTag;
import net.minecraft.nbt.Tag;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.entity.LivingEntity;
import top.theillusivec4.champions.api.ChampionsApi;
import top.theillusivec4.champions.api.affix.AffixInstance;
import top.theillusivec4.champions.common.api.ChampionsRegistries;
import java.util.ArrayList;

/** Pure Champion snapshot codec used by maid film and by the isolated runtime test. */
public final class FilmChampionSnapshot {
    public static final String KEY = "muxi_champion_companions.champion_film_v1";
    private static final String MIGRATED = "muxi_champion_companions.migrated_v1";
    private FilmChampionSnapshot() {}

    public static CompoundTag capture(LivingEntity entity) {
        var snapshot = new CompoundTag();
        snapshot.putBoolean("rolled", entity.getPersistentData().getBoolean(CompanionRules.ROLLED));
        snapshot.putBoolean("migrated", entity.getPersistentData().getBoolean(MIGRATED));
        ChampionsApi.get().getChampion(entity).ifPresent(champion -> {
            snapshot.putString("tier", champion.tier().id().toString());
            var affixes = new ListTag();
            for (var affix : champion.affixes()) {
                var id = ChampionsApi.get().getAffixTypeId(affix.type()).orElse(null);
                if (id == null) continue;
                var saved = new CompoundTag();
                saved.putString("id", id.toString());
                saved.put("data", affix.save());
                affixes.add(saved);
            }
            snapshot.put("affixes", affixes);
        });
        return snapshot;
    }

    public static boolean restore(LivingEntity entity, CompoundTag snapshot) {
        entity.getPersistentData().putBoolean(CompanionRules.ROLLED, snapshot.getBoolean("rolled"));
        entity.getPersistentData().putBoolean(MIGRATED, snapshot.getBoolean("migrated"));
        if (!snapshot.contains("tier", Tag.TAG_STRING)) return true;
        var api = ChampionsApi.get();
        var tierId = ResourceLocation.tryParse(snapshot.getString("tier"));
        if (tierId == null) return false;
        var tier = api.getTier(tierId).orElse(null);
        if (tier == null) return false;
        var affixes = new ArrayList<AffixInstance>();
        var list = snapshot.getList("affixes", Tag.TAG_COMPOUND);
        for (int i = 0; i < list.size(); i++) {
            var saved = list.getCompound(i);
            var affixId = ResourceLocation.tryParse(saved.getString("id"));
            if (affixId == null || !saved.contains("data", Tag.TAG_COMPOUND)) return false;
            var type = api.getAffixType(affixId).orElse(null);
            if (type == null) return false;
            affixes.add(AffixInstance.load(type, saved.getCompound("data")));
        }
        var restored = ChampionsRegistries.builder().trySpawnWithAffixes(entity, tier, affixes,
            entity.getRandom(), ResourceLocation.parse("champions:modded_mob"));
        if (restored.isEmpty()) return false;
        entity.getPersistentData().putBoolean(CompanionRules.ROLLED, true);
        entity.getPersistentData().putBoolean(MIGRATED, true);
        return true;
    }
}
