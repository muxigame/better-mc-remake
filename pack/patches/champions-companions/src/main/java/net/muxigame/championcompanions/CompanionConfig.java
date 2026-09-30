package net.muxigame.championcompanions;

import net.minecraft.world.entity.Entity;
import net.neoforged.neoforge.common.ModConfigSpec;

/** Independent probability for the four exact owned companion types only. */
public final class CompanionConfig {
    public static final String FILE_NAME = "muxi-champion-companions-server.toml";
    public static final ModConfigSpec SPEC;
    public static final ModConfigSpec.DoubleValue OWNED_SPAWN_CHANCE;

    static {
        var builder = new ModConfigSpec.Builder();
        builder.push("spawning");
        OWNED_SPAWN_CHANCE = builder
            .comment("Chance (0-1) for an owned Touhou maid or modular golem to become a champion.",
                "Independent of Champions spawnChance. One attempt per saved entity; no rerolls.",
                "Natural tier distribution is unchanged; owned companions use 50/25/15/7/3.")
            .defineInRange("ownedCompanionChance", 0.33, 0.0, 1.0);
        builder.pop();
        SPEC = builder.build();
    }

    private CompanionConfig() {}

    public static float chanceFor(Entity entity, float naturalChance) {
        return CompanionRules.isOwned(entity) ? OWNED_SPAWN_CHANCE.get().floatValue() : naturalChance;
    }
}
