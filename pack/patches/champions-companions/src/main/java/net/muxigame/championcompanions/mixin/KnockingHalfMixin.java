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

@Mixin(targets="top.theillusivec4.champions.common.affix.builtin.StatelessCombatAffixes.KnockingAffix", remap=false)
public abstract class KnockingHalfMixin {
    @ModifyArgs(method="applyKnockback", at=@At(value="INVOKE", target="Lnet/minecraft/world/entity/LivingEntity;knockback(DDD)V"))
    private static void muxi$halfKnockback(Args args, LivingEntity source, LivingEntity target, int strength) {
        args.set(0, ((Double)args.get(0)) * CompanionRules.effectFactor(source));
    }
    @ModifyArgs(method="applyKnockback", at=@At(value="INVOKE", target="Lnet/minecraft/world/effect/MobEffectInstance;<init>(Lnet/minecraft/core/Holder;II)V"))
    private static void muxi$halfSlowTime(Args args, LivingEntity source, LivingEntity target, int strength) {
        if (CompanionRules.isOwned(source)) args.set(1, Math.max(1, ((Integer)args.get(1)) / 2));
    }
    @Inject(method="applyKnockback", at=@At("HEAD"), cancellable=true)
    private static void muxi$dontKnockOwner(LivingEntity source, LivingEntity target, int strength, CallbackInfo ci) {
        if (CompanionRules.friendly(source, target)) ci.cancel();
    }
}
