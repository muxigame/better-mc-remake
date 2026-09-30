package net.muxigame.championcompanions.mixin;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.ai.attributes.Attribute;
import net.minecraft.core.Holder;
import net.muxigame.championcompanions.CompanionRules;
import top.theillusivec4.champions.api.champion.Champion;
import top.theillusivec4.champions.api.affix.EmptyAffixData;
import top.theillusivec4.champions.api.affix.handler.event.*;
import top.theillusivec4.champions.common.affix.builtin.*;
import top.theillusivec4.champions.common.data.ModifierSetting;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.invoke.arg.Args;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(targets="top.theillusivec4.champions.common.affix.builtin.StatelessCombatAffixes.ParalyzingAffix", remap=false)
public abstract class ParalyzingHalfMixin {
    // Halve proc probability, not both chance AND duration (which would quarter it).
    @Inject(method="lambda$registerHandlers$0", at=@At("HEAD"), cancellable=true)
    private static void muxi$halfProc(Champion champion, EmptyAffixData data, int strength, AttackEvent event, CallbackInfo ci) {
        if (CompanionRules.isOwned(champion.entity()) &&
            (CompanionRules.friendly(champion.entity(), event.target()) || champion.entity().getRandom().nextBoolean())) ci.cancel();
    }
}
