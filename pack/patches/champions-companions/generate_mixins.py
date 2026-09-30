"""Generate narrowly-scoped half-effect injection classes against the pinned Champions jar."""
from pathlib import Path
import json

HERE = Path(__file__).resolve().parent
OUT = HERE / 'src/main/java/net/muxigame/championcompanions/mixin'
OUT.mkdir(parents=True, exist_ok=True)
PREFIX = 'top.theillusivec4.champions.'
BASE = PREFIX + 'common.affix.builtin.'
imports = '''package net.muxigame.championcompanions.mixin;
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
'''
manifest = []
classes = ['OwnedEligibilityMixin', 'OneRollMixin', 'TierDistributionMixin']
constructor = 'Lnet/minecraft/world/entity/ai/attributes/AttributeModifier;<init>(Lnet/minecraft/resources/ResourceLocation;DLnet/minecraft/world/entity/ai/attributes/AttributeModifier$Operation;)V'

def emit(name, target, body, checks):
    classes.append(name)
    (OUT / (name + '.java')).write_text(imports + '\n@Mixin(targets="' + target + '", remap=false)\npublic abstract class ' + name + ' {\n' + body + '\n}\n', encoding='utf-8')
    manifest.extend(dict(target=target, **c) for c in checks)

def modify_args(name, target, method, invoke, params, statements, static=True):
    body = '    @ModifyArgs(method="' + method + '", at=@At(value="INVOKE", target="' + invoke + '"))\n'
    body += '    private ' + ('static ' if static else '') + 'void muxi$half(Args args, ' + params + ') {\n        ' + statements + '\n    }'
    emit(name, target, body, [dict(method=method, invoke=invoke)])

modify_args('TierBenefitsMixin', PREFIX+'common.champion.ChampionBuilder', 'lambda$applyModifiers$17', constructor,
    'LivingEntity entity, ModifierSetting setting, float strength, Holder.Reference<Attribute> holder',
    'args.set(1, ((Double)args.get(1)) * CompanionRules.effectFactor(entity));')
modify_args('HastyHalfMixin', BASE+'spawn_tick_affixes.HastyAffix', 'applySpeed', constructor,
    'LivingEntity entity, int strength',
    'args.set(1, ((Double)args.get(1)) * CompanionRules.effectFactor(entity));')
modify_args('LivelyHalfMixin', BASE+'LivelyAffix', 'lambda$registerHandlers$1',
    'Lnet/minecraft/world/entity/LivingEntity;heal(F)V',
    'Champion champion, LivelyAffix.Data data, int strength, TickEvent event',
    'args.set(0, ((Float)args.get(0)) * (float)CompanionRules.effectFactor(champion.entity()));')
modify_args('ReflectiveHalfMixin', BASE+'ReflectiveAffix', 'lambda$registerHandlers$0',
    'Lnet/minecraft/world/entity/LivingEntity;hurt(Lnet/minecraft/world/damagesource/DamageSource;F)Z',
    'Champion champion, EmptyAffixData data, int strength, DamageEvent event',
    'args.set(1, ((Float)args.get(1)) * (float)CompanionRules.effectFactor(champion.entity()));')
for name, target, data in [('DampeningHalfMixin','StatelessCombatAffixes.DampeningAffix','EmptyAffixData'),
                            ('AdaptableHalfMixin','AdaptableAffix','AdaptableAffix.Data')]:
    modify_args(name, BASE+target, 'lambda$registerHandlers$0',
        'Ltop/theillusivec4/champions/api/affix/handler/event/HurtEvent;setDamage(F)V',
        f'Champion champion, {data} data, int strength, HurtEvent event',
        'if (CompanionRules.isOwned(champion.entity())) {\n'
        '            float before = event.currentDamage();\n'
        '            args.set(0, before + (((Float)args.get(0)) - before) * 0.5f);\n'
        '        }')

emit('ShieldingHalfMixin', BASE+'ShieldingAffix', '''    @Redirect(method="lambda$registerHandlers$1", at=@At(value="INVOKE",
        target="Ltop/theillusivec4/champions/api/affix/handler/event/HurtEvent;cancel()V"))
    private static void muxi$halfBlock(HurtEvent receiver, Champion champion,
            ShieldingAffix.Data data, int strength, HurtEvent event) {
        if (CompanionRules.isOwned(champion.entity())) receiver.setDamage(receiver.currentDamage() * 0.5f);
        else receiver.cancel();
    }''', [dict(method='lambda$registerHandlers$1', invoke='Ltop/theillusivec4/champions/api/affix/handler/event/HurtEvent;cancel()V')])

knock='Lnet/minecraft/world/entity/LivingEntity;knockback(DDD)V'
slow='Lnet/minecraft/world/effect/MobEffectInstance;<init>(Lnet/minecraft/core/Holder;II)V'
emit('KnockingHalfMixin', BASE+'StatelessCombatAffixes.KnockingAffix', '''    @ModifyArgs(method="applyKnockback", at=@At(value="INVOKE", target="'''+knock+'''"))
    private static void muxi$halfKnockback(Args args, LivingEntity source, LivingEntity target, int strength) {
        args.set(0, ((Double)args.get(0)) * CompanionRules.effectFactor(source));
    }
    @ModifyArgs(method="applyKnockback", at=@At(value="INVOKE", target="'''+slow+'''"))
    private static void muxi$halfSlowTime(Args args, LivingEntity source, LivingEntity target, int strength) {
        if (CompanionRules.isOwned(source)) args.set(1, Math.max(1, ((Integer)args.get(1)) / 2));
    }
    @Inject(method="applyKnockback", at=@At("HEAD"), cancellable=true)
    private static void muxi$dontKnockOwner(LivingEntity source, LivingEntity target, int strength, CallbackInfo ci) {
        if (CompanionRules.friendly(source, target)) ci.cancel();
    }''', [dict(method='applyKnockback', invoke=knock), dict(method='applyKnockback', invoke=slow)])

for name, target, method in [('ParalyzingHalfMixin','ParalyzingAffix','lambda$registerHandlers$0'),
                             ('WoundingHalfMixin','WoundingAffix','lambda$registerHandlers$2')]:
    emit(name, BASE+'StatelessCombatAffixes.'+target, '''    // Halve proc probability, not both chance AND duration (which would quarter it).
    @Inject(method="'''+method+'''", at=@At("HEAD"), cancellable=true)
    private static void muxi$halfProc(Champion champion, EmptyAffixData data, int strength, AttackEvent event, CallbackInfo ci) {
        if (CompanionRules.isOwned(champion.entity()) &&
            (CompanionRules.friendly(champion.entity(), event.target()) || champion.entity().getRandom().nextBoolean())) ci.cancel();
    }''', [dict(method=method)])

# The reflected amount hook above must also avoid retaliation against the owner.
emit('ReflectiveOwnerSafetyMixin', BASE+'ReflectiveAffix', '''    @Inject(method="lambda$registerHandlers$0", at=@At("HEAD"), cancellable=true)
    private static void muxi$dontReflectOwner(Champion champion, EmptyAffixData data, int strength, DamageEvent event, CallbackInfo ci) {
        if (CompanionRules.friendly(champion.entity(), event.source().getDirectEntity())) ci.cancel();
    }''', [dict(method='lambda$registerHandlers$0')])

resources=HERE/'src/main/resources'
(resources/'muxi_champion_companions.mixins.json').write_text(json.dumps({
    'required':True,'minVersion':'0.8','package':'net.muxigame.championcompanions.mixin',
    'compatibilityLevel':'JAVA_21','mixins':classes,'injectors':{'defaultRequire':1}},indent=2)+'\n')
(HERE/'injection-targets.json').write_text(json.dumps(manifest,indent=2)+'\n')
print('Generated', len(classes)-2, 'effect mixins;', len(classes), 'mixins total')
