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

@Mixin(targets="top.theillusivec4.champions.common.champion.ChampionBuilder", remap=false)
public abstract class TierBenefitsMixin {
    @ModifyArgs(method="lambda$applyModifiers$17", at=@At(value="INVOKE", target="Lnet/minecraft/world/entity/ai/attributes/AttributeModifier;<init>(Lnet/minecraft/resources/ResourceLocation;DLnet/minecraft/world/entity/ai/attributes/AttributeModifier$Operation;)V"))
    private static void muxi$half(Args args, LivingEntity entity, ModifierSetting setting, float strength, Holder.Reference<Attribute> holder) {
        args.set(1, ((Double)args.get(1)) * CompanionRules.effectFactor(entity));
    }
}
