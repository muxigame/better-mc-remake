package net.muxigame.minigames.horse;

import com.google.gson.*;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

/** Atomic test ledger for inventory and mirrored minigame points. No production points adapter or entity grant. */
public final class HorseMarketSandbox {
    public record Stock(HorseMarket.Offer offer,UUID owner,String status,UUID renter,UUID race){}
    public record Operation(String payload,String result){}
    private static final class State {
        int schema=1;
        HorseMarket.Config config;
        Map<UUID,Long> points=new LinkedHashMap<>();
        Map<UUID,Stock> stock=new LinkedHashMap<>();
        Map<UUID,Operation> operations=new LinkedHashMap<>();
        Map<String,Long> settlements=new LinkedHashMap<>();
        Map<UUID,Long> raceRewards=new LinkedHashMap<>();
    }
    private final Path file;
    private final Gson gson=new GsonBuilder().setPrettyPrinting().create();
    private State state;
    public HorseMarketSandbox(Path lab,HorseMarket.Config config){
        lab=lab.toAbsolutePath().normalize();if(!Boolean.getBoolean("muxi.horse.economyLab")||!Files.isRegularFile(lab.resolve("HORSE_QA_ONLY")))throw new IllegalStateException("Economy writes require a marked isolated sandbox");
        file=lab.resolve("horse-market-sandbox.json");
        try{if(Files.exists(file)){state=gson.fromJson(Files.readString(file),State.class);validate(state);if(!state.config.equals(config))throw new IllegalStateException("Quote config changed; create a new sandbox");}else{state=new State();state.config=config;for(var offer:HorseMarket.catalog(config))state.stock.put(offer.horse(),new Stock(offer,null,"SHOP",null,null));save(state);}}catch(Exception failure){throw new IllegalStateException("Market sandbox cannot load; fail closed",failure);}
    }
    private static void validate(State s){if(s==null||s.schema!=1||s.config==null||s.points==null||s.stock==null||s.operations==null||s.settlements==null||s.raceRewards==null||s.stock.size()>4096||s.operations.size()>8192||s.points.values().stream().anyMatch(v->v==null||v<0))throw new IllegalStateException("Invalid sandbox ledger");HorseMarket.catalog(s.config);for(var e:s.stock.entrySet()){var v=e.getValue();if(v==null||v.offer()==null||!e.getKey().equals(v.offer().horse())||!Set.of("SHOP","OWNED","POOL","RENTED").contains(v.status())||v.status().equals("OWNED")&&v.owner()==null||v.status().equals("RENTED")&&(v.renter()==null||v.race()==null)||!v.status().equals("RENTED")&&(v.renter()!=null||v.race()!=null))throw new IllegalStateException("Invalid stock provenance");}}
    private void save(State next){validate(next);try{byte[] data=gson.toJson(next).getBytes(StandardCharsets.UTF_8);Path tmp=file.resolveSibling(file.getFileName()+".tmp");try(var channel=FileChannel.open(tmp,StandardOpenOption.CREATE,StandardOpenOption.TRUNCATE_EXISTING,StandardOpenOption.WRITE)){var buffer=java.nio.ByteBuffer.wrap(data);while(buffer.hasRemaining())channel.write(buffer);channel.force(true);}Files.move(tmp,file,StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);state=next;}catch(Exception failure){throw new IllegalStateException("Sandbox transaction was not committed",failure);}}
    private State copy(){return gson.fromJson(gson.toJson(state),State.class);}
    private static Stock stock(State s,UUID horse){var v=s.stock.get(horse);if(v==null)throw new IllegalArgumentException("Unknown stock UUID");return v;}
    private static void debit(State s,UUID player,long amount){Objects.requireNonNull(player);long value=s.points.getOrDefault(player,0L);if(amount<0||value<amount)throw new IllegalStateException("Insufficient mirrored points");s.points.put(player,value-amount);}
    private static void credit(State s,UUID player,long amount){Objects.requireNonNull(player);if(amount<0)throw new IllegalArgumentException("Negative credit");s.points.put(player,Math.addExact(s.points.getOrDefault(player,0L),amount));}
    private String transact(UUID token,String payload,java.util.function.Function<State,String> change){Objects.requireNonNull(token);var old=state.operations.get(token);if(old!=null){if(!old.payload().equals(payload))throw new IllegalStateException("Operation token payload mismatch");return old.result();}State next=copy();String result=change.apply(next);next.operations.put(token,new Operation(payload,result));save(next);return result;}
    public synchronized void seedMirroredPoints(UUID player,long points){if(player==null||points<0||state.points.containsKey(player))throw new IllegalArgumentException("One explicit fixture balance per account");State next=copy();next.points.put(player,points);save(next);}
    public synchronized long balance(UUID player){return state.points.getOrDefault(player,0L);}
    public synchronized List<Stock> stock(){return List.copyOf(state.stock.values());}
    public synchronized String registerOwnedFixture(UUID token,UUID player,HorseIdentity fixture){
        Objects.requireNonNull(player);if(fixture.sourceHorse())throw new IllegalArgumentException("Real horse imports require a future ownership protocol");
        return transact(token,"fixture|"+player+"|"+gson.toJson(fixture),s->{if(s.stock.containsKey(fixture.horseId()))throw new IllegalStateException("Duplicate horse inventory UUID");long rent=HorseMarket.attributeQuote(s.config,fixture);var offer=new HorseMarket.Offer(fixture.horseId(),fixture.name(),1,fixture.speed(),fixture.jump(),fixture.health(),List.of("速度 "+fixture.speed(),"跳跃 "+fixture.jump(),"体魄 "+fixture.health()),rent,Math.multiplyExact(rent,s.config.purchaseMultiplier()));s.stock.put(fixture.horseId(),new Stock(offer,player,"OWNED",null,null));return "OWNED_FIXTURE";});
    }
    public synchronized String buy(UUID token,UUID player,UUID horse){return transact(token,"buy|"+player+"|"+horse,s->{Stock item=stock(s,horse);if(!item.status().equals("SHOP"))throw new IllegalStateException("Horse already sold or in a pool");debit(s,player,item.offer().purchase());s.stock.put(horse,new Stock(item.offer(),player,"OWNED",null,null));return "OWNED";});}
    public synchronized String sellToPool(UUID token,UUID player,UUID horse){return transact(token,"sell|"+player+"|"+horse,s->{Stock item=stock(s,horse);if(!item.status().equals("OWNED")||!Objects.equals(item.owner(),player))throw new IllegalStateException("Only private owned stock can be sold; rental resale forbidden");credit(s,player,HorseMarket.buyback(s.config,item.offer().identity()));s.stock.put(horse,new Stock(item.offer(),null,"POOL",null,null));return "POOL";});}
    public synchronized String rent(UUID token,UUID player,UUID horse,UUID race){return transact(token,"rent|"+player+"|"+horse+"|"+race,s->{Objects.requireNonNull(race);Stock item=stock(s,horse);if(!item.status().equals("POOL"))throw new IllegalStateException("Horse unavailable or already rented");debit(s,player,item.offer().rent());s.stock.put(horse,new Stock(item.offer(),null,"RENTED",player,race));return "RENTED";});}
    public synchronized String returnRental(UUID token,UUID player,UUID horse,UUID race){return transact(token,"return|"+player+"|"+horse+"|"+race,s->{Stock item=stock(s,horse);if(!item.status().equals("RENTED")||!Objects.equals(item.renter(),player)||!Objects.equals(item.race(),race))throw new IllegalStateException("Rental identity mismatch");s.stock.put(horse,new Stock(item.offer(),null,"POOL",null,null));return "POOL";});}
    public synchronized String settle(UUID token,UUID player,UUID race,long award){return transact(token,"settle|"+player+"|"+race+"|"+award,s->{Objects.requireNonNull(race);String key=race+"|"+player;Long old=s.settlements.get(key);if(old!=null){if(old!=award)throw new IllegalStateException("Conflicting settlement");return "ALREADY_SETTLED";}long total=Math.addExact(s.raceRewards.getOrDefault(race,0L),award);if(award<0||total>s.config.maximumRaceReward())throw new IllegalStateException("Race reward budget exceeded");credit(s,player,award);s.settlements.put(key,award);s.raceRewards.put(race,total);return "SETTLED";});}
    public Path journal(){return file;}
}
