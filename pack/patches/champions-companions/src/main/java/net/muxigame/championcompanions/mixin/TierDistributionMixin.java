package net.muxigame.championcompanions.mixin;

import net.muxigame.championcompanions.CompanionRules;
import org.objectweb.asm.Opcodes;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Redirect;

@Mixin(targets="top.theillusivec4.champions.common.champion.ChampionSpawnHandler", remap=false)
public abstract class TierDistributionMixin {
    // The natural table is returned unchanged; OneRollMixin supplies the entity context
    // so only the four owned companion IDs receive 50/25/15/7/3.
    @Redirect(method="selectTier", at=@At(value="FIELD",
        target="Ltop/theillusivec4/champions/common/champion/ChampionSpawnHandler;DEFAULT_TIER_WEIGHTS:[I",
        opcode=Opcodes.GETSTATIC))
    private static int[] muxi$ownedTierWeights() {
        return CompanionRules.tierWeights(new int[]{50, 25, 15, 8, 2});
    }
}
