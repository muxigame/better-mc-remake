package net.muxigame.championcompanions;

import dev.architectury.event.EventResult;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.LivingEntity;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.fml.ModContainer;
import net.neoforged.fml.ModList;
import net.neoforged.fml.common.Mod;
import net.neoforged.fml.config.ModConfig;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.tick.EntityTickEvent;
import top.theillusivec4.champions.api.ChampionsApi;
import top.theillusivec4.champions.api.affix.AffixInstance;
import top.theillusivec4.champions.api.event.ChampionEvents;
import top.theillusivec4.champions.common.champion.ChampionSpawnHandler;
import top.theillusivec4.champions.common.api.ChampionsRegistries;
import java.util.ArrayList;
import java.util.HashSet;

@Mod("muxi_champion_companions")
public final class CompanionChampions {
    private static final String MIGRATED = "muxi_champion_companions.migrated_v1";
    private final boolean maidLoaded;

    public CompanionChampions(IEventBus ignored, ModContainer container) {
        maidLoaded = ModList.get().isLoaded("touhou_little_maid");
        container.registerConfig(ModConfig.Type.SERVER, CompanionConfig.SPEC, CompanionConfig.FILE_NAME);
        NeoForge.EVENT_BUS.addListener(this::onTick);
        if (maidLoaded) MaidFilmBridge.register();
        ChampionEvents.SPAWN.register((champion, tier, context) -> {
            if (!CompanionRules.isOwned(champion.entity())) return EventResult.pass();
            var api = ChampionsApi.get();
            var used = new HashSet<String>();
            var rejected = new ArrayList<AffixInstance>();
            for (var affix : context.getAffixesSnapshot()) {
                var id = api.getAffixTypeId(affix.type()).orElse(null);
                if (id != null && "champions".equals(id.getNamespace()) && CompanionRules.SAFE_AFFIXES.contains(id.getPath())) {
                    used.add(id.getPath());
                } else rejected.add(affix);
            }
            // Keep the rolled rank/count/strength; replace only companion-unsafe effects.
            for (var affix : rejected) {
                context.removeAffixByType(affix.type().getClass());
                var choices = new ArrayList<>(CompanionRules.SAFE_AFFIXES.stream().filter(s -> !used.contains(s)).sorted().toList());
                if (choices.isEmpty()) continue;
                var selected = choices.get(champion.entity().getRandom().nextInt(choices.size()));
                api.getAffixType(ResourceLocation.fromNamespaceAndPath("champions", selected)).ifPresent(type -> {
                    context.addAffix(new AffixInstance(type, affix.strength()));
                    used.add(selected);
                });
            }
            return EventResult.pass();
        });
    }

    private void onTick(EntityTickEvent.Post event) {
        // Owner assignment can happen AFTER EntityJoinLevelEvent (altar/taming/assembly).
        // No world scans, and only one roll per saved entity, not a new roll each second.
        var entity = event.getEntity();
        if (!(entity instanceof LivingEntity living)
                || !(entity.level() instanceof ServerLevel level) || !living.isAlive()
                || !CompanionRules.isOwned(entity)) return;
        if (maidLoaded && MaidFilmBridge.restoreIfPending(living)) return;
        if (entity.tickCount % 20 != 0) return;
        var existing = ChampionsApi.get().getChampion(living);
        if (existing.isPresent()) {
            // Old saves can still contain pre-restriction, full-strength champions.
            // Rebuild once at the SAME tier; native teardown removes stale goals and
            // modifiers, our spawn filter replaces unsafe affixes, and our hooks
            // apply half benefits. Preserve health percentage instead of healing.
            if (!entity.getPersistentData().getBoolean(MIGRATED)) {
                var champion = existing.get();
                float ratio = living.getMaxHealth() > 0 ? living.getHealth() / living.getMaxHealth() : 1;
                var rebuilt = ChampionsRegistries.builder().trySpawnWithAffixes(living, champion.tier(),
                    new ArrayList<>(champion.affixes()), living.getRandom(), ResourceLocation.parse("champions:modded_mob"));
                if (rebuilt.isPresent()) {
                    living.setHealth(living.getMaxHealth() * Math.clamp(ratio, 0f, 1f));
                    entity.getPersistentData().putBoolean(MIGRATED, true);
                }
            }
            entity.getPersistentData().putBoolean(CompanionRules.ROLLED, true);
            return;
        }
        if (entity.getPersistentData().getBoolean(CompanionRules.ROLLED)) return;
        ChampionSpawnHandler.trySpawn(living, level);
    }
}
