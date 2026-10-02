package net.muxigame.championcompanions.qa;

import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import com.github.tartaricacid.touhoulittlemaid.item.ItemFilm;
import com.github.tartaricacid.touhoulittlemaid.api.event.MaidAndItemTransformEvent;
import com.google.gson.GsonBuilder;
import net.minecraft.core.BlockPos;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.item.ItemStack;
import net.muxigame.championcompanions.*;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import top.theillusivec4.champions.api.ChampionsApi;
import top.theillusivec4.champions.api.affix.AffixInstance;
import top.theillusivec4.champions.common.api.ChampionsRegistries;
import net.minecraft.resources.ResourceLocation;
import java.nio.file.*;
import java.util.*;

/** Actual EntityMaid, ItemFilm, Champions and entity NBT. Test-only fresh world. */
@Mod("muxi_maid_native_qa")
public final class MaidFilmNativeQA {
    private final UUID owner=UUID.fromString("00000000-0000-0000-0000-000000000456");
    private final List<String> passed=new ArrayList<>();
    private EntityMaid maid,lastRevived;private int ticks;private double maxHealth,damage;private int modifiers;private CompoundTag champion;private String model;
    public MaidFilmNativeQA(){
        if(!Boolean.getBoolean("muxi.maid.native.qa"))throw new IllegalStateException("QA-only mod");
        NeoForge.EVENT_BUS.addListener(this::tick);
        NeoForge.EVENT_BUS.addListener((MaidAndItemTransformEvent.ToMaid event)->lastRevived=event.getMaid());
    }
    private void check(boolean pass,String label){if(!pass)throw new AssertionError(label);passed.add(label);}
    private void near(double actual,double expected,String label){check(Math.abs(actual-expected)<.002,label+": "+actual+" == "+expected);}
    private EntityMaid create(ServerLevel level){var entity=new EntityMaid(level);entity.setOwnerUUID(owner);entity.setNoAi(true);entity.setNoGravity(true);entity.setInvulnerable(true);entity.setPos(.5,80,.5);entity.setCustomName(Component.literal("MaidNativeQA"));entity.getPersistentData().putBoolean(CompanionRules.ROLLED,true);return entity;}
    private ItemStack film(EntityMaid original){var item=ItemFilm.maidToFilm(original);original.discard();return item;}
    private EntityMaid revive(ServerLevel level,ItemStack item){lastRevived=null;ItemFilm.filmToMaid(item,level,new BlockPos(0,80,0),null);check(lastRevived!=null,"native filmToMaid created real maid");var revived=lastRevived;revived.setNoAi(true);revived.setNoGravity(true);revived.setInvulnerable(true);return revived;}
    private EntityMaid saveLoad(ServerLevel level,EntityMaid original){var nbt=original.saveWithoutId(new CompoundTag());original.discard();var loaded=new EntityMaid(level);loaded.load(nbt);loaded.setNoAi(true);loaded.setNoGravity(true);loaded.setInvulnerable(true);check(level.addFreshEntity(loaded),"saved real maid reload inserted");return loaded;}
    private void unchanged(){
        check(owner.equals(maid.getOwnerUUID()),"owner UUID retained");check(model.equals(maid.getModelId()),"model retained");
        check(champion.equals(FilmChampionSnapshot.capture(maid)),"tier, affixes, strength and flags retained");
        near(maid.getMaxHealth(),maxHealth,"max-health multiplier does not stack");near(maid.getAttributeValue(Attributes.ATTACK_DAMAGE),damage,"attack multiplier does not stack");
        check(maid.getAttribute(Attributes.MAX_HEALTH).getModifiers().size()==modifiers,"health modifier count stable");
        check(!maid.getPersistentData().contains(FilmChampionSnapshot.KEY),"successful restore clears durable pending snapshot");
    }
    private void tick(ServerTickEvent.Post event){var server=event.getServer();if(!server.getLocalIp().equals("127.0.0.1"))throw new IllegalStateException("fresh loopback QA only");
        try{ticks++;var level=server.overworld();
            if(ticks==10){
                level.getChunk(0,0);level.setChunkForced(0,0,true);
                CompanionConfig.OWNED_SPAWN_CHANCE.set(1.0);maid=create(level);check(level.addFreshEntity(maid),"actual maid inserted");
                var api=ChampionsApi.get();var affixes=List.of(new AffixInstance(api.getAffixType(ResourceLocation.parse("champions:dampening")).orElseThrow(),2));
                check(ChampionsRegistries.builder().trySpawnWithAffixes(maid,api.getTierByLevel(4).orElseThrow(),affixes,maid.getRandom(),ResourceLocation.parse("champions:modded_mob")).isPresent(),"actual Champions builder applies tier4");
            }
            if(ticks==50){
                champion=FilmChampionSnapshot.capture(maid);model=maid.getModelId();maxHealth=maid.getMaxHealth();damage=maid.getAttributeValue(Attributes.ATTACK_DAMAGE);modifiers=maid.getAttribute(Attributes.MAX_HEALTH).getModifiers().size();
                maid=revive(level,film(maid));
                check(maid.getPersistentData().contains(FilmChampionSnapshot.KEY),"native revival stores durable pending before first tick");
                check(ChampionsApi.get().getChampion(maid).isEmpty(),"rolled guard blocks join lottery before restore");
                maid=revive(level,film(maid));check(ChampionsApi.get().getChampion(maid).isEmpty(),"rapid second death does not reroll");
                maid=saveLoad(level,maid);check(maid.getPersistentData().contains(FilmChampionSnapshot.KEY),"pending survives real entity save/load before restore");
            }
            if(ticks==90){unchanged();maid=saveLoad(level,maid);}
            if(ticks==110)unchanged();
            if(ticks>=120&&ticks<320&&ticks%20==0){unchanged();maid=revive(level,film(maid));}
            if(ticks==340){
                unchanged();maid.discard();var normal=create(level);check(level.addFreshEntity(normal),"ordinary rolled maid inserted");
                normal=revive(level,film(normal));check(ChampionsApi.get().getChampion(normal).isEmpty(),"ordinary failed-roll maid stays ordinary at join");maid=normal;
            }
            if(ticks==370){check(ChampionsApi.get().getChampion(maid).isEmpty(),"ordinary maid never rerolls after restore");finish(server,null);}
        }catch(Throwable failure){finish(server,failure);}
    }
    private void finish(MinecraftServer server,Throwable failure){
        try{var data=new LinkedHashMap<String,Object>();data.put("success",failure==null);data.put("passed",passed);data.put("maxHealth",maxHealth);data.put("damage",damage);data.put("healthModifierCount",modifiers);data.put("realEntityMaid",true);data.put("realItemFilm",true);data.put("realChampionsBuilder",true);data.put("ticks",ticks);if(failure!=null){failure.printStackTrace();data.put("error",failure.toString());}Files.writeString(Path.of("maid-native-result.json"),new GsonBuilder().setPrettyPrinting().create().toJson(data));}catch(Exception e){e.printStackTrace();}server.halt(false);
    }
}
