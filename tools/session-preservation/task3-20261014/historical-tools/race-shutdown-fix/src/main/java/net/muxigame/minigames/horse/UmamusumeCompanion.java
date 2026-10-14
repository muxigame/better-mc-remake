package net.muxigame.minigames.horse;

import com.google.gson.*;
import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.level.ServerLevel;
import net.neoforged.fml.ModList;
import top.theillusivec4.champions.api.ChampionsApi;
import top.theillusivec4.champions.api.affix.AffixInstance;
import top.theillusivec4.champions.common.api.ChampionsRegistries;
import java.nio.file.Path;
import java.util.*;

/** Optional existing TLM/Champions adapter for permanent owned companions, not race copies. */
public final class UmamusumeCompanion {
    public static final String PRODUCT="muxi_umamusume_product_v1";
    private static final String ROLLED="muxi_champion_companions.rolled_v1",MIGRATED="muxi_champion_companions.migrated_v1";
    private UmamusumeCompanion(){}
    public static boolean installed(){return List.of("touhou_little_maid","yes_steve_model","champions","muxi_champion_companions").stream().allMatch(id->ModList.get().isLoaded(id));}
    public static boolean registrationVerified(){return Boolean.getBoolean("muxi.horse.maid.registrationVerified");}
    public static void preflight(JsonObject body){
        if(!registrationVerified())throw new IllegalStateException("赛马娘外观正在准备，暂未开放兑换");
        if(!installed())throw new IllegalStateException("赛马娘兑换需要现有女仆、YSM与强敌随从支持");
        // Delivery must preserve the advertised Champion tier when the native maid becomes film.
        try{Class.forName("net.muxigame.championcompanions.FilmChampionSnapshot");}catch(ClassNotFoundException failure){throw new IllegalStateException("强敌随从胶片持久化支持尚未加载，请等待服务器更新");}
        UmamusumeCatalog.verifyModelFiles(Path.of(System.getProperty("muxi.horse.maid.modelRoot","config/yes_steve_model/custom")),body);
        var api=ChampionsApi.get();if(api.getTier(ResourceLocation.parse(body.get("championTier").getAsString())).isEmpty())throw new IllegalStateException("Advertised Champion tier missing");
        for(var item:body.getAsJsonArray("affixes"))if(api.getAffixType(ResourceLocation.parse(item.getAsJsonObject().get("id").getAsString())).isEmpty())throw new IllegalStateException("Advertised Champion affix missing");
    }
    public static EntityMaid create(ServerLevel level,UUID asset,UUID owner,JsonObject body,double x,double y,double z){
        preflight(body);var maid=new EntityMaid(level);maid.setUUID(asset);maid.setPos(x,y,z);maid.setTame(true,false);maid.setOwnerUUID(owner);maid.setPersistenceRequired();maid.setNoAi(true);maid.setInvulnerable(true);maid.setNoGravity(true);
        maid.setCustomName(Component.literal(body.get("name").getAsString()));maid.setYsmModel(body.get("modelId").getAsString(),body.get("texture").getAsString(),maid.getCustomName());maid.setIsYsmModel(true);
        maid.getPersistentData().putBoolean(ROLLED,true);maid.getPersistentData().putBoolean(MIGRATED,true);maid.getPersistentData().putString(PRODUCT,body.toString());
        var api=ChampionsApi.get();var tier=api.getTier(ResourceLocation.parse(body.get("championTier").getAsString())).orElseThrow();var affixes=new ArrayList<AffixInstance>();
        for(var item:body.getAsJsonArray("affixes")){var a=item.getAsJsonObject();affixes.add(new AffixInstance(api.getAffixType(ResourceLocation.parse(a.get("id").getAsString())).orElseThrow(),a.get("strength").getAsInt()));}
        var result=ChampionsRegistries.builder().trySpawnWithAffixes(maid,tier,affixes,maid.getRandom(),ResourceLocation.parse("champions:modded_mob")).orElseThrow(()->new IllegalStateException("Fixed Champion grant denied"));
        if(!result.tier().id().equals(tier.id())||result.affixes().size()!=affixes.size())throw new IllegalStateException("Fixed Champion grant changed");
        for(var expected:affixes)if(result.affixes().stream().noneMatch(a->a.type()==expected.type()&&a.strength()==expected.strength()))throw new IllegalStateException("Fixed companion affix changed");
        maid.setHealth(maid.getMaxHealth());return maid;
    }
    public static void verify(EntityMaid maid,UUID owner,JsonObject body){
        UmamusumeCatalog.validateReceipt(body);
        if(!owner.equals(maid.getOwnerUUID())||!maid.isYsmModel()||!body.get("modelId").getAsString().equals(maid.getYsmModelId())||!body.get("texture").getAsString().equals(maid.getYsmModelTexture()))throw new IllegalStateException("Advertised native companion identity changed");
        var champion=ChampionsApi.get().getChampion(maid).orElseThrow(()->new IllegalStateException("Advertised Champion absent"));
        if(!champion.tier().id().toString().equals(body.get("championTier").getAsString())||champion.affixes().size()!=body.getAsJsonArray("affixes").size())throw new IllegalStateException("Advertised Champion tier changed");
        for(var item:body.getAsJsonArray("affixes")){var expected=item.getAsJsonObject();var type=ChampionsApi.get().getAffixType(ResourceLocation.parse(expected.get("id").getAsString())).orElseThrow();
            if(champion.affixes().stream().noneMatch(a->a.type()==type&&a.strength()==expected.get("strength").getAsInt()))throw new IllegalStateException("Advertised companion affix changed");
        }
    }
}
