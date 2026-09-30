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

@Mixin(targets="top.theillusivec4.champions.common.affix.builtin.StatelessCombatAffixes.DampeningAffix", remap=false)
public abstract class DampeningHalfMixin {
    @ModifyArgs(method="lambda$registerHandlers$0", at=@At(value="INVOKE", target="Ltop/theillusivec4/champions/api/affix/handler/event/HurtEvent;setDamage(F)V"))
    private static void muxi$half(Args args, Champion champion, EmptyAffixData data, int strength, HurtEvent event) {
        if (CompanionRules.isOwned(champion.entity())) {
            float before = event.currentDamage();
            args.set(0, before + (((Float)args.get(0)) - before) * 0.5f);
        }
    }
}
