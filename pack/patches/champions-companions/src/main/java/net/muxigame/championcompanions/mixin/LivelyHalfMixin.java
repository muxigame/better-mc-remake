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

@Mixin(targets="top.theillusivec4.champions.common.affix.builtin.LivelyAffix", remap=false)
public abstract class LivelyHalfMixin {
    @ModifyArgs(method="lambda$registerHandlers$1", at=@At(value="INVOKE", target="Lnet/minecraft/world/entity/LivingEntity;heal(F)V"))
    private static void muxi$half(Args args, Champion champion, LivelyAffix.Data data, int strength, TickEvent event) {
        args.set(0, ((Float)args.get(0)) * (float)CompanionRules.effectFactor(champion.entity()));
    }
}
