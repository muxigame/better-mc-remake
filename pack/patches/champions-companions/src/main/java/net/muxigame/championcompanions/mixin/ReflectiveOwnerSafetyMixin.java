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

@Mixin(targets="top.theillusivec4.champions.common.affix.builtin.ReflectiveAffix", remap=false)
public abstract class ReflectiveOwnerSafetyMixin {
    @Inject(method="lambda$registerHandlers$0", at=@At("HEAD"), cancellable=true)
    private static void muxi$dontReflectOwner(Champion champion, EmptyAffixData data, int strength, DamageEvent event, CallbackInfo ci) {
        if (CompanionRules.friendly(champion.entity(), event.source().getDirectEntity())) ci.cancel();
    }
}
