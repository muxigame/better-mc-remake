package net.muxigame.minigames.horse;

import java.nio.charset.StandardCharsets;
import java.util.*;

/** Visible fixed offers and provisional configurable quotes. Names are tributes, not real-world rankings. */
public final class HorseMarket {
    public record Config(long unitRent,long maximumRaceReward,int purchaseMultiplier,int buybackPercent) {
        public Config{if(unitRent<1||unitRent>1_000_000||maximumRaceReward<0||maximumRaceReward>100_000_000||purchaseMultiplier<2||purchaseMultiplier>100||buybackPercent<1||buybackPercent>90)throw new IllegalArgumentException("Invalid horse market config");if(Math.multiplyExact(unitRent,8)<=maximumRaceReward)throw new IllegalArgumentException("Top horse rent must exceed all rewards in one race");}
        public static Config preview(){return new Config(Long.getLong("muxi.horse.quote.unitRent",2000L),HorseScores.FIRST_POINTS,Integer.getInteger("muxi.horse.quote.purchaseMultiple",10),Integer.getInteger("muxi.horse.quote.buybackPercent",60));}
    }
    public record Offer(UUID horse,String name,int tier,double speed,double jump,double health,List<String> traits,long rent,long purchase) {
        public Offer{traits=List.copyOf(traits);}
        public HorseIdentity identity(){return new HorseIdentity(horse,name,speed,jump,health,false);}
    }
    private HorseMarket(){}
    public static long attributeQuote(Config config,HorseIdentity horse){
        if(!Double.isFinite(horse.speed())||!Double.isFinite(horse.jump())||!Double.isFinite(horse.health())||horse.speed()<=0||horse.speed()>.6||horse.jump()<=0||horse.jump()>1.5||horse.health()<1||horse.health()>100)throw new IllegalArgumentException("Unsupported horse attributes");
        double quality=.65*horse.speed()/.225+.25*horse.jump()/.8+.1*horse.health()/24;
        return Math.max(1,(long)Math.ceil(config.unitRent()*quality));
    }
    public static long buyback(Config config,HorseIdentity horse){return Math.multiplyExact(Math.multiplyExact(attributeQuote(config,horse),config.purchaseMultiplier()),config.buybackPercent())/100;}
    public static List<Offer> catalog(Config config){
        String[] ids={"Seabiscuit","Winx","Secretariat","Frankel","Equinox"},names={"海饼干","云丝","秘书处","范高尔","春秋分"};
        double[] speeds={.17,.21,.25,.29,.3375},jumps={.6,.7,.8,.9,1.0},health={18,22,26,28,30};
        var offers=new ArrayList<Offer>();
        for(int index=0;index<5;index++){
            UUID id=UUID.nameUUIDFromBytes(("muxi-horse-lab-catalog-v1:"+ids[index]).getBytes(StandardCharsets.UTF_8));
            var identity=new HorseIdentity(id,names[index],speeds[index],jumps[index],health[index],false);
            long rent=Math.multiplyExact(attributeQuote(config,identity),1L<<index);
            offers.add(new Offer(id,names[index],index+1,speeds[index],jumps[index],health[index],List.of("速度 "+speeds[index],"跳跃 "+jumps[index],"体魄 "+(int)health[index]),rent,Math.multiplyExact(rent,config.purchaseMultiplier())));
        }
        if(offers.getLast().rent()<=config.maximumRaceReward())throw new IllegalArgumentException("Top rental reward invariant violated");
        return List.copyOf(offers);
    }
    public static Offer enemy(Config config,int tier){if(tier<1||tier>5)throw new IllegalArgumentException("Enemy tier must be 1..5");return catalog(config).get(tier-1);}
}
