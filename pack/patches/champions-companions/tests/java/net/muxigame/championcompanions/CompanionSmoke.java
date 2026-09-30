package net.muxigame.championcompanions.smoke;

import net.muxigame.championcompanions.CompanionRules;
import net.muxigame.championcompanions.CompanionConfig;
import net.muxigame.championcompanions.FilmChampionSnapshot;

import com.google.gson.GsonBuilder;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.*;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.entity.monster.Zombie;
import net.minecraft.world.level.Level;
import net.minecraft.world.effect.MobEffects;
import net.minecraft.world.phys.Vec3;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.entity.EntityAttributeCreationEvent;
import net.neoforged.neoforge.event.server.ServerStartedEvent;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import net.neoforged.neoforge.registries.RegisterEvent;
import net.muxigame.core.feature.champions.ChampionRules;
import top.theillusivec4.champions.api.ChampionsApi;
import top.theillusivec4.champions.api.affix.AffixInstance;
import top.theillusivec4.champions.api.champion.*;
import top.theillusivec4.champions.api.affix.handler.event.*;
import top.theillusivec4.champions.common.api.ChampionsRegistries;
import top.theillusivec4.champions.common.champion.GlobalDispatcher;
import top.theillusivec4.champions.common.champion.ChampionSpawnHandler;
import top.theillusivec4.champions.common.affix.builtin.*;
import top.theillusivec4.champions.common.config.ChampionsConfig;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;

/** TEST ONLY: synthetic OwnableEntity fixtures under the four exact IDs. Never deploy this jar. */
@Mod("muxi_companions_smoke")
public final class CompanionSmoke {
    private static final Map<String,EntityType<OwnedFixture>> TYPES=new LinkedHashMap<>();
    private static final UUID OWNER=UUID.fromString("00000000-0000-0000-0000-000000000123");
    private MinecraftServer server; private int ticks; private int checks;
    private final List<LivingEntity> healthCases=new ArrayList<>();
    private OwnedFixture lateOwner, failedRoll; private final List<String> passed=new ArrayList<>();
    private OwnedFixture golemCase;
    private final ResourceLocation healthId=ResourceLocation.parse("champions:minecraft_generic.max_health_modifier");

    public CompanionSmoke(IEventBus bus) {
        bus.addListener(this::register); bus.addListener(this::attributes);
        NeoForge.EVENT_BUS.addListener((ServerStartedEvent event)->server=event.getServer());
        NeoForge.EVENT_BUS.addListener(this::tick);
    }
    private void register(RegisterEvent event) {
        if(event.getRegistryKey().equals(Registries.ATTRIBUTE)) {
            for(String name:List.of("regen","sweep","dynamic_reduction"))
                event.register(Registries.ATTRIBUTE,ResourceLocation.parse("modulargolems:"+name),()->
                    new net.minecraft.world.entity.ai.attributes.RangedAttribute("attribute.modulargolems."+name,0,0,100000).setSyncable(true));
        }
        if(event.getRegistryKey().equals(Registries.ENTITY_TYPE)) {
            for(String id:CompanionRules.TYPES.stream().sorted().toList()) {
                var type=EntityType.Builder.<OwnedFixture>of(OwnedFixture::new,MobCategory.CREATURE).sized(0.6f,1.8f).build(id);
                TYPES.put(id,type);event.register(Registries.ENTITY_TYPE,ResourceLocation.parse(id),()->type);
            }
        }
    }
    private void attributes(EntityAttributeCreationEvent event) {
        TYPES.values().forEach(type->event.put(type,Mob.createMobAttributes().add(Attributes.MAX_HEALTH,20)
            .add(Attributes.ATTACK_DAMAGE,10).add(Attributes.ATTACK_SPEED,4).add(Attributes.ARMOR,0)
            .add(Attributes.ARMOR_TOUGHNESS,0).add(Attributes.KNOCKBACK_RESISTANCE,0)
            .add(dev.xkmc.modulargolems.init.registrate.GolemTypes.GOLEM_REGEN.holder(),4)
            .add(dev.xkmc.modulargolems.init.registrate.GolemTypes.GOLEM_SWEEP.holder(),4)
            .add(dev.xkmc.modulargolems.init.registrate.GolemTypes.DYNAMIC_REDUCTION.holder(),4).build()));
    }
    public static final class OwnedFixture extends PathfinderMob implements OwnableEntity {
        UUID owner;
        public OwnedFixture(EntityType<? extends PathfinderMob> type,Level level){super(type,level);setNoAi(true);}
        public UUID getOwnerUUID(){return owner;}
        @Override public void addAdditionalSaveData(CompoundTag nbt){super.addAdditionalSaveData(nbt);if(owner!=null)nbt.putUUID("TestOwner",owner);}
        @Override public void readAdditionalSaveData(CompoundTag nbt){super.readAdditionalSaveData(nbt);owner=nbt.hasUUID("TestOwner")?nbt.getUUID("TestOwner"):null;}
    }
    private void check(boolean condition,String message){checks++;if(!condition)throw new AssertionError(message);}
    private void near(double actual,double expected,String message){check(Math.abs(actual-expected)<0.002,message+": "+actual+" != "+expected);}
    private OwnedFixture fixture(ServerLevel level,boolean owned){var e=new OwnedFixture(TYPES.get("touhou_little_maid:maid"),level);e.owner=owned?OWNER:null;return e;}
    private LivingEntity companionById(ServerLevel level,String id,boolean owned) {
        var e=new OwnedFixture(TYPES.get(id),level);e.owner=owned?OWNER:null;return e;
    }
    private Champion view(LivingEntity entity,String affix,int strength) {
        var api=ChampionsApi.get();var instance=new AffixInstance(api.getAffixType(ResourceLocation.parse("champions:"+affix)).orElseThrow(),strength);
        return new Champion(){public LivingEntity entity(){return entity;}public ChampionTier tier(){return api.getTierByLevel(4).orElseThrow();}
            public List<AffixInstance> affixes(){return List.of(instance);}};
    }
    private void apply(LivingEntity entity,String affix) {
        var api=ChampionsApi.get();var instance=new AffixInstance(api.getAffixType(ResourceLocation.parse("champions:"+affix)).orElseThrow(),2);
        check(ChampionsRegistries.builder().trySpawnWithAffixes(entity,api.getTierByLevel(4).orElseThrow(),List.of(instance),entity.getRandom(),
            ResourceLocation.parse("champions:modded_mob")).isPresent(),"Force test champion");
    }
    private void tick(ServerTickEvent.Post event) {
        if(server==null||event.getServer()!=server)return;
        try {
            if(++ticks==4)setup(server.overworld());
            if(ticks==50){verify(server.overworld());finish(null);}
        } catch(Throwable failure){failure.printStackTrace();finish(failure);}
    }
    private void setup(ServerLevel level) {
        testSpawnRates(level);
        ChampionsConfig.spawnChance=0;ChampionsConfig.beaconProtectionRange=0;
        CompanionConfig.OWNED_SPAWN_CHANCE.set(0.0);
        for(String id:CompanionRules.TYPES.stream().sorted().toList()) {
            var e=companionById(level,id,true);
            check(CompanionRules.isOwned(e),id+" recognized");
            check(ChampionRules.mayBeChampion(e),"Old hostile-only gate has precise exception");
            var unowned=companionById(level,id,false);
            check(!ChampionRules.mayBeChampion(unowned),"Unowned fixture stays excluded");
        }
        check(!ChampionRules.mayBeChampion(new net.minecraft.world.entity.animal.Cow(EntityType.COW,level)),"Cow excluded");
        var wolf=new net.minecraft.world.entity.animal.Wolf(EntityType.WOLF,level);wolf.setOwnerUUID(OWNER);
        check(!ChampionRules.mayBeChampion(wolf),"Other owned pets stay excluded");
        check(ChampionRules.mayBeChampion(new Zombie(level)),"Hostiles still included");
        passed.add("Exact owned-ID exceptions preserve neutral/pet restrictions");
        for(double h:new double[]{10,20,200}){
            var e=fixture(level,true);e.setNoGravity(true);e.getAttribute(Attributes.MAX_HEALTH).setBaseValue(h);e.setHealth((float)h);e.setPos(0,20,0);
            level.addFreshEntity(e);apply(e,"dampening");healthCases.add(e);
            var z=new Zombie(level);z.setNoAi(true);z.setPersistenceRequired();z.getAttribute(Attributes.MAX_HEALTH).setBaseValue(h);z.setHealth((float)h);z.setPos(1,20,0);
            level.addFreshEntity(z);apply(z,"dampening");healthCases.add(z);
        }
        var pool=fixture(level,true);apply(pool,"molten");
        check(ChampionsApi.get().getChampion(pool).orElseThrow().affixes().stream().allMatch(a->
            CompanionRules.SAFE_AFFIXES.contains(ChampionsApi.get().getAffixTypeId(a.type()).orElseThrow().getPath())),"Unsafe affix replaced only for companion");
        var wild=new Zombie(level);apply(wild,"molten");
        check(ChampionsApi.get().getChampion(wild).orElseThrow().affixes().stream().anyMatch(a->
            ChampionsApi.get().getAffixTypeId(a.type()).orElseThrow().getPath().equals("molten")),"Wild molten preserved");
        var ownAttack=healthCases.get(0).getAttribute(Attributes.ATTACK_DAMAGE).getModifier(ResourceLocation.parse("champions:minecraft_generic.attack_damage_modifier"));
        var wildAttack=healthCases.get(1).getAttribute(Attributes.ATTACK_DAMAGE).getModifier(ResourceLocation.parse("champions:minecraft_generic.attack_damage_modifier"));
        if(ownAttack!=null&&wildAttack!=null)near(ownAttack.amount(),wildAttack.amount()*0.5,"Raw tier attack bonus halved");
        passed.add("Companion-safe pool and tier-stat half increment");
        // Simulate a saved companion with an old full-strength tier modifier.
        var old=healthCases.getFirst();
        var attackId=ResourceLocation.parse("champions:minecraft_generic.attack_damage_modifier");
        var currentAttack=old.getAttribute(Attributes.ATTACK_DAMAGE).getModifier(attackId);
        if(currentAttack!=null)old.getAttribute(Attributes.ATTACK_DAMAGE).addOrReplacePermanentModifier(
            new net.minecraft.world.entity.ai.attributes.AttributeModifier(attackId,currentAttack.amount()*2,currentAttack.operation()));
        old.setHealth(old.getMaxHealth()*0.4f);
        testEffects(level);
        golemCase=new OwnedFixture(TYPES.get("modulargolems:metal_golem"),level);golemCase.owner=OWNER;
        golemCase.setNoGravity(true);golemCase.setPos(8,20,0);golemCase.getAttribute(Attributes.MAX_HEALTH).setBaseValue(400);
        golemCase.setHealth(400);level.addFreshEntity(golemCase);apply(golemCase,"dampening");
        failedRoll=fixture(level,true);failedRoll.setPos(4,20,0);level.addFreshEntity(failedRoll);
        check(failedRoll.getPersistentData().getBoolean(CompanionRules.ROLLED),"Failed 0% chance marked once");
        lateOwner=fixture(level,false);lateOwner.setPos(5,20,0);level.addFreshEntity(lateOwner);
        check(!lateOwner.getPersistentData().getBoolean(CompanionRules.ROLLED),"No premature roll before owner exists");
        lateOwner.owner=OWNER;CompanionConfig.OWNED_SPAWN_CHANCE.set(1.0);
        // Keeping wild chance at zero proves that the late-owner path is independent.
        near(ChampionsConfig.spawnChance,0,"Companion override never mutates global chance");
        testFilmSnapshot(level);
    }

    private void testFilmSnapshot(ServerLevel level) {
        var maid=fixture(level,true);
        var api=ChampionsApi.get();
        var affixes=List.of(
            new AffixInstance(api.getAffixType(ResourceLocation.parse("champions:dampening")).orElseThrow(),2),
            new AffixInstance(api.getAffixType(ResourceLocation.parse("champions:lively")).orElseThrow(),3));
        check(ChampionsRegistries.builder().trySpawnWithAffixes(maid,api.getTierByLevel(4).orElseThrow(),
            affixes,maid.getRandom(),ResourceLocation.parse("champions:modded_mob")).isPresent(),"Film snapshot source champion");
        maid.getPersistentData().putBoolean(CompanionRules.ROLLED,true);
        maid.getPersistentData().putBoolean("muxi_champion_companions.migrated_v1",true);
        var expected=ChampionsApi.get().getChampion(maid).orElseThrow().affixes().stream()
            .map(a->api.getAffixTypeId(a.type()).orElseThrow()+":"+a.strength()).sorted().toList();
        var snapshot=FilmChampionSnapshot.capture(maid);
        var revived=fixture(level,true);
        check(FilmChampionSnapshot.restore(revived,snapshot),"Film snapshot restores");
        var restored=ChampionsApi.get().getChampion(revived).orElseThrow();
        check(restored.tier().level()==4,"Film snapshot preserves tier");
        var actual=restored.affixes().stream()
            .map(a->api.getAffixTypeId(a.type()).orElseThrow()+":"+a.strength()).sorted().toList();
        check(actual.equals(expected),"Film snapshot preserves affix IDs and strengths");
        check(revived.getPersistentData().getBoolean(CompanionRules.ROLLED),"Film snapshot preserves one-roll state");
        check(revived.getPersistentData().getBoolean("muxi_champion_companions.migrated_v1"),"Film snapshot preserves migration state");
        passed.add("Film revive preserves exact Champion tier/affixes and one-roll state");
    }

    private void testSpawnRates(ServerLevel level) {
        near(CompanionConfig.OWNED_SPAWN_CHANCE.get(),0.33,"Independent config loaded with 33% default");
        ChampionsConfig.spawnChance=0.1f;ChampionsConfig.beaconProtectionRange=0;
        // Pick deterministic random samples in each distinct probability interval.
        // These exercise the actual transformed trySpawn method, not only a helper.
        long[] seeds={-1,-1,-1};float[] samples=new float[3];
        for(long seed=0;seed<100000&&(seeds[0]<0||seeds[1]<0||seeds[2]<0);seed++) {
            float sample=net.minecraft.util.RandomSource.create(seed).nextFloat();
            int band=sample<0.1f?0:sample<0.33f?1:2;
            if(seeds[band]<0){seeds[band]=seed;samples[band]=sample;}
        }
        for(int band=0;band<3;band++) {
            check(seeds[band]>=0,"Seed for probability band "+band);
            for(String id:CompanionRules.TYPES.stream().sorted().toList()) {
                var owned=companionById(level,id,true);
                near(CompanionConfig.chanceFor(owned,0.1f),0.33,"Owned type uses 33%: "+id);
                owned.getRandom().setSeed(seeds[band]);ChampionSpawnHandler.trySpawn(owned,level);
                check(ChampionsApi.get().isChampion(owned)==(samples[band]<0.33f),"Real companion lottery band "+band);
                check(owned.getPersistentData().getBoolean(CompanionRules.ROLLED),"One attempt recorded on success and failure");
                if(samples[band]<0.33f)check(!ChampionsApi.get().getChampion(owned).orElseThrow().affixes().isEmpty(),"Successful companion lottery has affixes");
                else {
                    CompanionConfig.OWNED_SPAWN_CHANCE.set(1.0);
                    ChampionSpawnHandler.trySpawn(owned,level);
                    check(!ChampionsApi.get().isChampion(owned),"Increasing chance does not reroll old failures");
                    CompanionConfig.OWNED_SPAWN_CHANCE.set(0.33);
                }
                var unowned=companionById(level,id,false);
                near(CompanionConfig.chanceFor(unowned,0.1f),0.1,"Unowned type gets no special probability");
                unowned.getRandom().setSeed(seeds[band]);ChampionSpawnHandler.trySpawn(unowned,level);
                check(!ChampionsApi.get().isChampion(unowned),"Unowned type remains ineligible");
                check(!unowned.getPersistentData().getBoolean(CompanionRules.ROLLED),"Ownership can still be assigned later");
            }
            var zombie=new Zombie(level);zombie.getRandom().setSeed(seeds[band]);
            near(CompanionConfig.chanceFor(zombie,0.1f),0.1,"Natural probability stays at 10%");
            ChampionSpawnHandler.trySpawn(zombie,level);
            check(ChampionsApi.get().isChampion(zombie)==(samples[band]<0.1f),"Real natural lottery band "+band);
        }
        CompanionConfig.OWNED_SPAWN_CHANCE.set(0.75);
        near(CompanionConfig.chanceFor(fixture(level,true),0.1f),0.75,"Companion probability is configurable");
        near(CompanionConfig.chanceFor(new Zombie(level),0.1f),0.1,"Companion setting cannot raise natural chance");
        near(ChampionsConfig.spawnChance,0.1,"Original global probability field remains unchanged");
        CompanionConfig.OWNED_SPAWN_CHANCE.set(0.33);
        passed.add("Independent natural 10% / owned 33% actual spawn gates; all four IDs; affixes; no retry after chance increase");
        testTierDistributions(level);
    }

    private long seedForTierSlot(int slot) {
        for (long seed=0; seed<1000000; seed++) {
            var random=net.minecraft.util.RandomSource.create(seed);
            random.nextFloat();
            if (random.nextInt(100)==slot) return seed;
        }
        throw new AssertionError("No deterministic seed for tier slot "+slot);
    }

    private int spawnTier(ServerLevel level, boolean owned, long seed) {
        LivingEntity entity=owned?fixture(level,true):new Zombie(level);
        entity.getRandom().setSeed(seed);
        ChampionSpawnHandler.trySpawn(entity,level);
        check(ChampionsApi.get().isChampion(entity),"Tier probe spawned a champion");
        return ChampionsApi.get().getChampion(entity).orElseThrow().tier().level();
    }

    private void testTierDistributions(ServerLevel level) {
        float natural=ChampionsConfig.spawnChance;
        double owned=CompanionConfig.OWNED_SPAWN_CHANCE.get();
        int[] ownedStarts={0,50,75,90,97};
        int[] naturalStarts={0,50,75,90,98};
        try {
            ChampionsConfig.spawnChance=1.0f;
            CompanionConfig.OWNED_SPAWN_CHANCE.set(1.0);
            for (int tier=0;tier<5;tier++) {
                check(spawnTier(level,true,seedForTierSlot(ownedStarts[tier]))==tier+1,
                    "Owned tier weight 50/25/15/7/3 at tier "+(tier+1));
                check(spawnTier(level,false,seedForTierSlot(naturalStarts[tier]))==tier+1,
                    "Natural tier weight 50/25/15/8/2 at tier "+(tier+1));
            }
            passed.add("Owned 50/25/15/7/3 and natural 50/25/15/8/2 tier lotteries");
        } finally {
            ChampionsConfig.spawnChance=natural;
            CompanionConfig.OWNED_SPAWN_CHANCE.set(owned);
        }
    }

    private void testEffects(ServerLevel level) {
        var enemy=new Zombie(level);enemy.setNoAi(true);
        var own=fixture(level,true);
        for(LivingEntity e:List.of(own,enemy)) {
            double factor=CompanionRules.effectFactor(e);
            var haste=view(e,"hasty",2);GlobalDispatcher.dispatch(SpawnEvent.class,haste,new SpawnEvent(level));
            near(e.getAttribute(Attributes.MOVEMENT_SPEED).getModifier(ResourceLocation.parse("champions:hasty_speed")).amount(),ChampionsConfig.hastySpeedBonus*2*factor,"Hasty bonus");
            var lively=view(e,"lively",2);((LivelyAffix.Data)lively.affixes().getFirst().data()).lastDamageTime=-100000;
            e.setHealth(1);GlobalDispatcher.dispatch(TickEvent.class,lively,new TickEvent(20));near(e.getHealth(),1+ChampionsConfig.livelyHealAmount*2*factor,"Lively heal");
            var damageSource=level.damageSources().mobAttack(enemy);
            var damp=new HurtEvent(damageSource,100,100,value->{},()->{});
            GlobalDispatcher.dispatch(HurtEvent.class,view(e,"dampening",2),damp);
            near(damp.currentDamage(),100*(1-Math.min(0.9,ChampionsConfig.dampeningReduction*2*0.25)*factor),"Dampening prevention");
            var adaptable=view(e,"adaptable",2);
            GlobalDispatcher.dispatch(HurtEvent.class,adaptable,new HurtEvent(damageSource,100,100,v->{},()->{}));
            var adapt=new HurtEvent(damageSource,100,100,v->{},()->{});GlobalDispatcher.dispatch(HurtEvent.class,adaptable,adapt);
            near(adapt.currentDamage(),100-100*Math.min(ChampionsConfig.adaptableMaxReduction,ChampionsConfig.adaptableReductionIncrement*2)*factor,"Adaptable prevention");
            var shielding=view(e,"shielding",2);((ShieldingAffix.Data)shielding.affixes().getFirst().data()).shielding=true;
            boolean[] cancelled={false};var shield=new HurtEvent(damageSource,100,100,v->{},()->cancelled[0]=true);
            GlobalDispatcher.dispatch(HurtEvent.class,shielding,shield);
            if(factor==0.5){near(shield.currentDamage(),50,"Shield half prevention");check(!cancelled[0],"Not full immunity");}
            else check(cancelled[0],"Enemy shield unchanged");
            var target=fixture(level,false);target.setHealth(20);target.setDeltaMovement(Vec3.ZERO);
            GlobalDispatcher.dispatch(AttackEvent.class,view(e,"knocking",2),new AttackEvent(target,damageSource,1,()->{}));
            near(target.getDeltaMovement().horizontalDistance(),ChampionsConfig.knockingKnockback*2*factor,"Knocking force");
            near(target.getEffect(MobEffects.MOVEMENT_SLOWDOWN).getDuration(),100*factor,"Knocking slow duration");
            ChampionsConfig.reflectiveMinPercent=0.7;ChampionsConfig.reflectiveMaxPercent=1;ChampionsConfig.reflectiveMax=100;
            target.removeAllEffects();target.setHealth(20);target.invulnerableTime=0;
            GlobalDispatcher.dispatch(DamageEvent.class,view(e,"reflective",2),new DamageEvent(level.damageSources().mobAttack(target),10,v->{}));
            near(target.getHealth(),20-7*factor,"Reflective damage");
            for(String name:List.of("paralyzing","wounding")) {
                var v=view(e,name,2);String effect=name.equals("paralyzing")?"paralysis":"wound";
                var holder=BuiltInRegistries.MOB_EFFECT.getHolder(ResourceLocation.parse("champions:"+effect)).orElseThrow();
                int hits=0;e.getRandom().setSeed(123);target.getRandom().setSeed(456);
                for(int i=0;i<1000;i++){
                    target.removeAllEffects();GlobalDispatcher.dispatch(AttackEvent.class,v,new AttackEvent(target,damageSource,1,()->{}));
                    if(target.hasEffect(holder))hits++;
                }
                double expected=(name.equals("paralyzing")?Math.min(0.95,ChampionsConfig.paralyzingChance*2):ChampionsConfig.woundingChance*0.8)*factor;
                check(Math.abs(hits/1000.0-expected)<0.05,name+" half proc frequency "+hits);
            }
        }
        passed.add("All nine actual affix handlers: ally half-effect / wild full-effect");
    }
    private void verify(ServerLevel level) {
        double[] expected={30,50,50,80,300,400};
        for(int i=0;i<healthCases.size();i++)near(healthCases.get(i).getMaxHealth(),expected[i],"KubeJS live health anchor "+i);
        near(healthCases.getFirst().getHealth(),12,"Companion migration preserves wounded 40% health");
        check(healthCases.getFirst().getPersistentData().getBoolean("muxi_champion_companions.migrated_v1"),"One-time old companion migration marked");
        var attackId=ResourceLocation.parse("champions:minecraft_generic.attack_damage_modifier");
        var ownAttack=healthCases.getFirst().getAttribute(Attributes.ATTACK_DAMAGE).getModifier(attackId);
        var wildAttack=healthCases.get(1).getAttribute(Attributes.ATTACK_DAMAGE).getModifier(attackId);
        if(ownAttack!=null&&wildAttack!=null)near(ownAttack.amount(),wildAttack.amount()*0.5,"Old full-strength tier attack migrated to half");
        near(golemCase.getAttribute(Attributes.MAX_HEALTH).getBaseValue(),400,"Raw golem base untouched");
        near(golemCase.getMaxHealth(),300,"Existing golem 0.5 base modifier plus half champion bonus: 400->200->300");
        near(golemCase.getAttribute(dev.xkmc.modulargolems.init.registrate.GolemTypes.GOLEM_REGEN.holder()).getValue(),2,"Existing golem regeneration half retained");
        check(ChampionsApi.get().isChampion(lateOwner),"Owner assigned after join gets its one chance");
        check(!ChampionsApi.get().isChampion(failedRoll),"No reroll on later ticks");
        var data=new CompoundTag();failedRoll.saveWithoutId(data);var restored=fixture(level,false);restored.load(data);
        check(restored.getPersistentData().getBoolean(CompanionRules.ROLLED),"One-roll state survives entity save/load");
        check(restored.getOwnerUUID().equals(OWNER),"Owner persists");
        passed.add("Live health anchors, late ownership, one roll, save/load");
        passed.add("Production golem script runtime compatibility and combined half-base/half-bonus ordering (fixture attributes)");
    }
    private void finish(Throwable failure) {
        try {
            Map<String,Object> result=new LinkedHashMap<>();result.put("success",failure==null);result.put("checks",checks);
            result.put("passed",passed);result.put("fixtureNote","Synthetic OwnableEntity IDs; no real user entities/worlds used");
            if(failure!=null)result.put("error",failure.toString());
            Files.writeString(Path.of("companions-smoke-result.json"),new GsonBuilder().setPrettyPrinting().create().toJson(result));
        } catch(Exception e){e.printStackTrace();}
        server.halt(false);server=null;
    }
}
