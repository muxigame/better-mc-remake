package net.muxigame.minigames.horse;

import com.google.gson.*;
import net.minecraft.core.*;
import net.minecraft.core.registries.Registries;
import net.minecraft.nbt.*;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.*;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.*;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.entity.animal.horse.*;
import net.minecraft.world.item.*;
import net.minecraft.world.level.*;
import net.minecraft.world.level.storage.LevelResource;
import net.muxigame.minigames.*;
import java.nio.*;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.util.*;

/** Durable native custody + service outbox. No mirror wallet and no source-horse cloning. */
public final class HorseTrading implements AutoCloseable {
    public static final String LOCK="muxi_horse_trade_lock_v1";
    private static final JsonObject POLICY=policy();
    private static JsonObject policy(){try(var input=HorseTrading.class.getResourceAsStream("/horse_trade_catalog_v1.json")){if(input==null)throw new IllegalStateException("Horse policy missing");return JsonParser.parseString(new String(input.readAllBytes(),StandardCharsets.UTF_8)).getAsJsonObject();}catch(java.io.IOException failure){throw new ExceptionInInitializerError(failure);}}
    private final MinecraftServer server;private final Path journal;
    private final HorseTradeTransport transport=new HorseTradeTransport();
    private JsonObject data;private final Map<Long,JsonObject> markets=new HashMap<>();
    private final Map<Long,Long> marketRevision=new HashMap<>();
    private final Set<String> busy=new HashSet<>();private final Set<Long> reading=new HashSet<>();
    private final Map<String,Long> retries=new HashMap<>();private boolean closed;
    public HorseTrading(MinecraftServer server){
        this.server=server;journal=server.getWorldPath(LevelResource.ROOT).resolve("data/muxi-horse-trades-v1.json");
        try{if(Files.exists(journal)){data=JsonParser.parseString(Files.readString(journal)).getAsJsonObject();if(data.get("schema").getAsInt()!=1)throw new IllegalArgumentException("Unknown trade journal");UUID.fromString(text(data,"realm"));if(jobs().size()>10000)throw new IllegalArgumentException("Trade journal exceeds recovery limit");}
            else{data=new JsonObject();data.addProperty("schema",1);data.addProperty("realm",UUID.randomUUID().toString());data.add("jobs",new JsonObject());data.add("rentals",new JsonObject());data.add("sold",new JsonObject());save();}
            // A previous process cannot continue a physical race. Preserve completed intent;
            // refund unfinished races after generated actors are retired on restart.
            for(var entry:rentals().entrySet()){var r=entry.getValue().getAsJsonObject();if(text(r,"state").equals("ENDED"))continue;if(!r.has("end"))r.addProperty("end","server_failure");r.addProperty("released",true);}save();
        }catch(Exception failure){throw new IllegalStateException("Horse commerce journal requires recovery; trading blocked",failure);}
    }
    private void thread(){if(!server.isSameThread())throw new IllegalStateException("Server thread required");}
    public boolean enabled(){return Boolean.getBoolean("muxi.horse.trading.enabled")&&transport.configured();}
    public String realm(){return text(data,"realm");}
    private JsonObject jobs(){return data.getAsJsonObject("jobs");}
    private JsonObject rentals(){return data.getAsJsonObject("rentals");}
    private static String text(JsonObject o,String key){return o.has(key)?o.get(key).getAsString():"";}
    private static String hash(String raw){try{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(raw.getBytes(StandardCharsets.UTF_8)));}catch(NoSuchAlgorithmException impossible){throw new AssertionError(impossible);}}
    private static UUID business(String id,String purpose){return UUID.nameUUIDFromBytes(("muxi-horse-trade:"+id+":"+purpose).getBytes(StandardCharsets.UTF_8));}
    private void save(){
        if(Boolean.getBoolean("muxi.horse.tradeQa.failJournal"))throw new IllegalStateException("Injected private trade journal failure");
        try{Files.createDirectories(journal.getParent());var temporary=journal.resolveSibling(journal.getFileName()+".tmp");var bytes=StandardCharsets.UTF_8.encode(data.toString());
            try(var file=FileChannel.open(temporary,StandardOpenOption.CREATE,StandardOpenOption.TRUNCATE_EXISTING,StandardOpenOption.WRITE)){while(bytes.hasRemaining())file.write(bytes);file.force(true);}
            Files.move(temporary,journal,StandardCopyOption.REPLACE_EXISTING,StandardCopyOption.ATOMIC_MOVE);
        }catch(Exception failure){throw new IllegalStateException("Trade journal unavailable; no new asset mutation permitted",failure);}
    }
    private JsonObject enqueue(long uid,String action,JsonObject payload,UUID operation,JsonObject local){
        thread();String id=operation.toString();var event=new JsonObject();event.addProperty("uid",uid);event.addProperty("realm",realm());event.addProperty("operation",id);event.addProperty("action",action);event.add("payload",payload.deepCopy());
        if(jobs().has(id)){var old=jobs().getAsJsonObject(id);if(!old.getAsJsonObject("event").equals(event))throw new IllegalStateException("Conflicting native trade replay");return old;}
        var job=new JsonObject();job.add("event",event);job.add("local",local==null?new JsonObject():local.deepCopy());job.addProperty("state","QUEUED");jobs().add(id,job);
        try{save();}catch(RuntimeException failure){jobs().remove(id);throw failure;}return job;
    }
    private JsonObject child(JsonObject parent,String action,JsonObject payload){var event=parent.getAsJsonObject("event");return enqueue(event.get("uid").getAsLong(),action,payload,business(text(event,"operation"),action),parent.getAsJsonObject("local"));}
    private static JsonObject object(String... fields){var result=new JsonObject();for(int i=0;i<fields.length;i+=2)result.addProperty(fields[i],fields[i+1]);return result;}
    private static UUID playerUuid(long uid){return UUID.nameUUIDFromBytes(("OfflinePlayer:"+uid).getBytes(StandardCharsets.UTF_8));}
    private long verified(ServerPlayer p){thread();GameRuntime.get(server).platform.requireAllowed(p);return TrustedAccounts.uid(p);}
    private void eligible(ServerPlayer p){if(!enabled())throw new IllegalStateException("马匹交易尚未开启");if(GameRuntime.get(server).memberships.owner(p.getUUID())!=null||HorseReturn.pending(p)||GameAreas.contains(p.level()))throw new IllegalStateException("请先退出比赛并恢复原位置");}
    public void refresh(ServerPlayer p){
        long uid;try{uid=TrustedAccounts.uid(p);}catch(IllegalArgumentException absent){return;}if(!transport.configured()||closed||!reading.add(uid))return;
        long revision=marketRevision.getOrDefault(uid,0L);
        transport.send("horse-market/"+uid+"?realm="+realm(),null,reply->server.execute(()->{reading.remove(uid);if(closed)return;if(marketRevision.getOrDefault(uid,0L)!=revision){var current=server.getPlayerList().getPlayer(playerUuid(uid));if(current!=null)refresh(current);return;}if(reply.status()==200)markets.put(uid,reply.body());}));
    }
    public static String playerError(String raw){
        if(raw==null||raw.isBlank())return "交易暂未完成，请刷新查看";
        String message=raw.toLowerCase(Locale.ROOT);
        if(message.contains("insufficient"))return "积分不足，请先积累小游戏积分";
        if(message.contains("registration"))return "赛马娘外观正在准备，请稍后刷新";
        if(message.contains("expired"))return "报价已过期，请重新查看确认";
        if(message.contains("reserved")||message.contains("rented")||message.contains("taken before"))return "这匹马已被预订，请选择其他马匹";
        if(message.contains("stable is full"))return "已拥有数量达到当前上限";
        if(raw.codePoints().anyMatch(c->c>=0x4e00&&c<=0x9fff)&&!raw.matches(".*[A-Za-z][A-Za-z0-9_:-]*/[A-Za-z0-9_/.-]+.*")&&!raw.matches(".*[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}.*"))return raw;
        return "交易暂未完成，请刷新核对订单；领取异常可在订单中撤销尚未交付的购买";
    }
    public void action(ServerPlayer p,String action,String value,UUID race){
        try{actionInternal(p,action,value,race);}catch(IllegalArgumentException failure){throw new IllegalArgumentException(playerError(failure.getMessage()),failure);}catch(IllegalStateException failure){throw new IllegalStateException(playerError(failure.getMessage()),failure);}
    }
    private void actionInternal(ServerPlayer p,String action,String value,UUID race){
        long uid=verified(p);
        if(action.equals("tradeRefresh")){refresh(p);return;}
        if(action.equals("tradeQuoteBuy")){eligible(p);if(value.startsWith("uma_"))UmamusumeCompanion.preflight(UmamusumeCatalog.offer(value));enqueue(uid,"quote",object("kind","BUY","sku",value),UUID.randomUUID(),home(p));return;}
        if(action.equals("tradeQuoteSell")){
            eligible(p);var horse=ownedMount(p);var source=descriptor(horse);var payload=new JsonObject();payload.addProperty("kind","SELL");payload.add("source",source);
            var local=home(p);local.addProperty("source",horse.getUUID().toString());local.add("descriptor",source);enqueue(uid,"quote",payload,UUID.randomUUID(),local);return;
        }
        if(action.equals("tradeQuoteRent")){
            if(!enabled()||race==null)throw new IllegalStateException("请先加入等待中的比赛房间");
            if(rental(race,p.getUUID())!=null)throw new IllegalStateException("本场已有租赁，请先取消");
            var local=home(p);local.addProperty("race",race.toString());enqueue(uid,"quote",object("kind","RENT","asset",value,"race",race.toString()),UUID.randomUUID(),local);return;
        }
        if(action.equals("tradeConfirm")){
            var quote=findQuote(uid,value);var response=quote.getAsJsonObject("receipt");String kind=text(response,"kind");var local=quote.getAsJsonObject("local").deepCopy();var payload=object("quote",value);String verb=kind.equals("BUY")?"buy":kind.equals("RENT")?"rent":"sale_prepare";
            UUID operation=business(value,"confirm");if(jobs().has(operation.toString()))return;
            if(!enabled())throw new IllegalStateException("马匹交易尚未开启");
            if(kind.equals("RENT")){if(race==null||!race.toString().equals(text(local,"race")))throw new IllegalStateException("租赁报价对应的房间已关闭");}
            else eligible(p);
            if(kind.equals("BUY")&&UmamusumeCatalog.isMaid(response.getAsJsonObject("body")))UmamusumeCompanion.preflight(response.getAsJsonObject("body"));
            if(kind.equals("SELL")){
                var horse=ownedMount(p);if(!horse.getUUID().toString().equals(text(local,"source"))||!descriptor(horse).equals(local.get("descriptor")))throw new IllegalStateException("马匹状态或装备已变化，请重新报价");
                var nbt=new CompoundTag();horse.saveWithoutId(nbt);String snapshot=nbt.toString();if(snapshot.length()>524288)throw new IllegalStateException("马匹数据过大，无法安全托管");
                local.addProperty("snapshot",snapshot);local.addProperty("proof",hash(snapshot));local.addProperty("dimension",horse.level().dimension().location().toString());local.addProperty("x",horse.getX());local.addProperty("y",horse.getY());local.addProperty("z",horse.getZ());payload.addProperty("snapshot",hash(snapshot));
                var job=enqueue(uid,verb,payload,operation,local);freeze(horse,job);persistWorld();
            }else enqueue(uid,verb,payload,operation,local);
            return;
        }
        if(action.equals("tradeCancelDelivery")){
            var job=jobs().getAsJsonObject(value);if(job==null||job.getAsJsonObject("event").get("uid").getAsLong()!=uid||!text(job.getAsJsonObject("event"),"action").equals("buy"))throw new IllegalArgumentException("购买订单不存在");
            var ack=jobs().getAsJsonObject(business(value,"grant_ack").toString());if(ack!=null)throw new IllegalStateException("已交付或正在确认交付，不能撤销");
            job.getAsJsonObject("local").addProperty("cancel",true);save();return;
        }
        throw new IllegalArgumentException("Unknown horse trade action");
    }
    private JsonObject findQuote(long uid,String quote){for(var entry:jobs().entrySet()){var job=entry.getValue().getAsJsonObject();if(job.getAsJsonObject("event").get("uid").getAsLong()==uid&&text(job.getAsJsonObject("event"),"action").equals("quote")&&job.has("receipt")&&text(job.getAsJsonObject("receipt"),"quote").equals(quote))return job;}throw new IllegalArgumentException("报价不存在，请刷新");}
    private JsonObject home(ServerPlayer p){var h=new JsonObject();h.addProperty("dimension",p.level().dimension().location().toString());h.addProperty("x",p.getX()+2);h.addProperty("y",p.getY());h.addProperty("z",p.getZ());h.addProperty("player",p.getUUID().toString());return h;}
    private Horse ownedMount(ServerPlayer p){
        if(!(p.getVehicle() instanceof Horse h)||!h.isTamed()||!p.getUUID().equals(h.getOwnerUUID())||h.isBaby()||h.getHealth()<h.getMaxHealth()-.01||!h.getActiveEffects().isEmpty()||locked(h)||h.getTags().contains(HorsePilot.MARKER))throw new IllegalStateException("请骑上自己已驯服、成年、健康的原版马，取消临时效果后再出售");return h;
    }
    public static boolean locked(Entity e){return e.getPersistentData().contains(LOCK);}
    private JsonObject descriptor(Horse h){
        var source=new JsonObject();source.addProperty("asset",h.getUUID().toString());source.addProperty("name",h.hasCustomName()?h.getName().getString():"未知马");source.addProperty("speed",h.getAttribute(Attributes.MOVEMENT_SPEED).getBaseValue());source.addProperty("jump",h.getAttribute(Attributes.JUMP_STRENGTH).getBaseValue());source.addProperty("health",h.getAttribute(Attributes.MAX_HEALTH).getBaseValue());source.addProperty("variant",h.getVariant().getId());source.addProperty("markings",h.getMarkings().getId());source.addProperty("saddled",h.isSaddled());source.addProperty("armored",!h.getBodyArmorItem().isEmpty());source.addProperty("owner",h.getOwnerUUID().toString());
        var nbt=new CompoundTag();h.saveWithoutId(nbt);source.addProperty("fingerprint",hash(source.toString()+nbt.getCompound("SaddleItem")+nbt.getCompound("body_armor_item")));return source;
    }
    private void freeze(net.minecraft.world.entity.Mob h,JsonObject job){
        var local=job.getAsJsonObject("local");h.ejectPassengers();h.setNoAi(true);h.setInvulnerable(true);h.setNoGravity(true);h.setDeltaMovement(0,0,0);h.getPersistentData().putString(LOCK,text(job.getAsJsonObject("event"),"operation"));h.setPos(local.get("x").getAsDouble(),local.get("y").getAsDouble(),local.get("z").getAsDouble());
    }
    private ServerLevel level(JsonObject local){var location=ResourceLocation.tryParse(text(local,"dimension"));return location==null?null:server.getLevel(ResourceKey.create(Registries.DIMENSION,location));}
    private boolean loaded(JsonObject local){
        var level=level(local);if(level==null)return false;int x=(int)Math.floor(local.get("x").getAsDouble())>>4,z=(int)Math.floor(local.get("z").getAsDouble())>>4;long pos=ChunkPos.asLong(x,z);
        if(!level.getForcedChunks().contains(pos)){local.addProperty("ticket",true);save();level.setChunkForced(x,z,true);}
        return level.getChunkSource().getChunkNow(x,z)!=null&&level.areEntitiesLoaded(pos)&&level.isPositionEntityTicking(new BlockPos(x*16+8,(int)local.get("y").getAsDouble(),z*16+8));
    }
    private Horse find(String id){UUID uuid=UUID.fromString(id);Horse result=null;for(var l:server.getAllLevels()){var e=l.getEntity(uuid);if(e!=null&&!e.isRemoved()){if(!(e instanceof Horse h)||result!=null)throw new IllegalStateException("Duplicate or mismatched horse UUID; recovery quarantined");result=h;}}return result;}
    private net.minecraft.world.entity.Mob findGrant(String id,String kind){
        if(!Set.of("HORSE","MAID").contains(kind))throw new IllegalStateException("Unknown delivery entity kind");
        UUID uuid=UUID.fromString(id);net.minecraft.world.entity.Mob result=null;
        for(var l:server.getAllLevels()){var e=l.getEntity(uuid);if(e!=null&&!e.isRemoved()){
            boolean expected=kind.equals("HORSE")?e instanceof Horse:net.minecraft.core.registries.BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString().equals("touhou_little_maid:maid");
            if(!expected||!(e instanceof net.minecraft.world.entity.Mob mob)||result!=null)throw new IllegalStateException("Duplicate or mismatched grant UUID; recovery quarantined");result=mob;
        }}return result;
    }
    private static boolean ownedGrant(net.minecraft.world.entity.Mob h,UUID owner){return h instanceof net.minecraft.world.entity.OwnableEntity ownable&&owner.equals(ownable.getOwnerUUID());}
    private static String grantKind(JsonObject local){return local.has("entityKind")?text(local,"entityKind"):"HORSE";}
    private void releaseTicket(JsonObject local){if(local.has("ticket")&&local.get("ticket").getAsBoolean()){var l=level(local);if(l!=null)l.setChunkForced((int)Math.floor(local.get("x").getAsDouble())>>4,(int)Math.floor(local.get("z").getAsDouble())>>4,false);local.addProperty("ticket",false);}}
    private void persistWorld(){server.saveEverything(true,true,true);}
    private void done(JsonObject job){job.addProperty("state","DONE");releaseTicket(job.getAsJsonObject("local"));save();}
    public boolean denyLoaded(Entity entity){thread();return data.getAsJsonObject("sold").has(entity.getUUID().toString());}
    public void tick(){
        thread();if(closed)return;
        for(var entry:List.copyOf(jobs().entrySet())){
            String id=entry.getKey();var job=entry.getValue().getAsJsonObject();String state=text(job,"state");
            if(state.equals("REJECTED")&&text(job.getAsJsonObject("event"),"action").equals("sale_prepare")&&!job.has("restored")){try{restoreSource(job);job.addProperty("restored",true);save();}catch(RuntimeException waiting){job.addProperty("error",waiting.getMessage());}continue;}
            if(state.equals("RECEIVED")){try{apply(job);}catch(RuntimeException failure){job.addProperty("error",failure.getMessage());if(server.getTickCount()%100==0)org.slf4j.LoggerFactory.getLogger("muxi-horse-trading").error("Horse custody remains pending {}",id,failure);}continue;}
            if(!state.equals("QUEUED")||!transport.configured()||busy.contains(id)||server.getTickCount()<retries.getOrDefault(id,0L))continue;
            var event=job.getAsJsonObject("event");var local=job.getAsJsonObject("local");
            if(text(event,"action").equals("rental_end")){String rentalId=text(event.getAsJsonObject("payload"),"rental");var runningJob=jobs().getAsJsonObject(business(rentalId,"rental_running").toString());if(runningJob!=null&&!text(runningJob,"state").equals("DONE"))continue;}
            if(text(event,"action").equals("sale_prepare")){
                if(!loaded(local))continue;var h=find(text(local,"source"));if(h==null){job.addProperty("error","原马未加载，托管暂停；不会克隆");continue;}freeze(h,job);
            }
            busy.add(id);retries.put(id,(long)server.getTickCount()+40);
            transport.send("horse-trades",event,reply->server.execute(()->{
                busy.remove(id);if(closed)return;
                if(reply.status()==200){job.add("receipt",reply.body());job.remove("error");job.addProperty("state","RECEIVED");save();long uid=event.get("uid").getAsLong();marketRevision.merge(uid,1L,Long::sum);markets.remove(uid);var p=server.getPlayerList().getPlayer(playerUuid(uid));if(p!=null){refresh(p);GameRuntime.get(server).platform.refresh(p);}}
                else{job.addProperty("error",reply.error());if(reply.status()>=400&&reply.status()<500){job.addProperty("state","REJECTED");save();}else save();}
            }));
        }
        for(var entry:rentals().entrySet()){var rental=entry.getValue().getAsJsonObject();if(text(rental,"state").equals("ENDED"))continue;if(!rental.has("end")&&text(rental,"state").equals("RESERVED")&&rental.get("expires").getAsLong()<=System.currentTimeMillis()/1000){rental.addProperty("end","reservation_expired");rental.addProperty("released",true);save();}if(rental.has("end")&&rental.has("released")&&rental.get("released").getAsBoolean())enqueue(rental.get("uid").getAsLong(),"rental_end",object("rental",entry.getKey(),"reason",text(rental,"end")),business(entry.getKey(),"rental_end"),new JsonObject());}
    }
    private void apply(JsonObject job){
        var event=job.getAsJsonObject("event");var receipt=job.getAsJsonObject("receipt");var local=job.getAsJsonObject("local");String action=text(event,"action");long uid=event.get("uid").getAsLong();
        switch(action){
            case "quote" -> {done(job);}
            case "buy" -> {
                if(Boolean.getBoolean("muxi.horse.tradeQa.pauseDelivery"))return;
                String asset=text(receipt,"asset");var body=receipt.getAsJsonObject("body");if(!local.has("asset")){local.addProperty("asset",asset);local.addProperty("entityKind",UmamusumeCatalog.isMaid(body)?"MAID":"HORSE");local.addProperty("birthPlanned",false);save();}
                if(!loaded(local))return;var h=findGrant(asset,grantKind(local));
                if(local.has("cancel")&&local.get("cancel").getAsBoolean()){
                    if(h!=null){if(!locked(h)||!text(event,"operation").equals(h.getPersistentData().getString(LOCK)))throw new IllegalStateException("Unrecognized grant cannot be removed");h.discard();persistWorld();}
                    child(job,"grant_cancel",object("asset",asset,"parent",text(receipt,"operation")));done(job);return;
                }
                if(h==null){
                    // A planned birth is never silently repeated after crash: its chunk must
                    // contain the saved provisional UUID, or the order stays quarantined.
                    if(local.get("birthPlanned").getAsBoolean())throw new IllegalStateException("待领实体缺失；保留订单，禁止自动复制");
                    var l=level(local);var at=new BlockPos((int)Math.floor(local.get("x").getAsDouble()),(int)Math.floor(local.get("y").getAsDouble()),(int)Math.floor(local.get("z").getAsDouble()));
                    double gx=local.get("x").getAsDouble(),gy=local.get("y").getAsDouble(),gz=local.get("z").getAsDouble();
                    if(!l.getBlockState(at.below()).isSolid()||!l.getBlockState(at).getCollisionShape(l,at).isEmpty()||!l.getBlockState(at.above()).getCollisionShape(l,at.above()).isEmpty()||!l.noCollision(new net.minecraft.world.phys.AABB(gx-.7,gy,gz-.7,gx+.7,gy+1.7,gz+.7)))throw new IllegalStateException("领取位置被占用，请清理原购买位置");
                    local.addProperty("birthPlanned",true);save();
                    if(UmamusumeCatalog.isMaid(body))h=UmamusumeCompanion.create(l,UUID.fromString(asset),playerUuid(uid),body,gx,gy,gz);
                    else{
                        var born=EntityType.HORSE.create(l);born.setUUID(UUID.fromString(asset));born.setTamed(true);born.setOwnerUUID(playerUuid(uid));born.setCustomName(Component.literal(text(body,"name")));born.getAttribute(Attributes.MOVEMENT_SPEED).setBaseValue(body.get("speed").getAsDouble());born.getAttribute(Attributes.JUMP_STRENGTH).setBaseValue(body.get("jump").getAsDouble());born.getAttribute(Attributes.MAX_HEALTH).setBaseValue(body.get("health").getAsDouble());born.setHealth(body.get("health").getAsFloat());
                        var appearance=new CompoundTag();born.saveWithoutId(appearance);appearance.putInt("Variant",body.get("variant").getAsInt()|(body.get("markings").getAsInt()<<8));born.load(appearance);born.equipSaddle(new ItemStack(Items.SADDLE),null);h=born;
                    }
                    freeze(h,job);if(!l.addFreshEntity(h))throw new IllegalStateException("Native grant spawn rejected");persistWorld();
                }
                if(!locked(h)||!text(event,"operation").equals(h.getPersistentData().getString(LOCK))||!ownedGrant(h,playerUuid(uid)))throw new IllegalStateException("Native grant provenance mismatch");
                if(UmamusumeCatalog.isMaid(body))UmamusumeCompanion.verify((com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid)h,playerUuid(uid),body);
                child(job,"grant_ack",object("asset",asset,"parent",text(receipt,"operation"),"proof",hash(realm()+":"+asset+":"+text(receipt,"operation"))));done(job);
            }
            case "grant_ack" -> {
                if(!loaded(local))return;var h=findGrant(text(receipt,"asset"),grantKind(local));if(h==null)throw new IllegalStateException("已确认交付的实体未加载，暂停；不会补发复制");
                String parent=text(event.getAsJsonObject("payload"),"parent");if(!locked(h)&&ownedGrant(h,playerUuid(uid))){done(job);return;}if(!parent.equals(h.getPersistentData().getString(LOCK)))throw new IllegalStateException("Delivered horse lock mismatch");
                h.getPersistentData().remove(LOCK);h.setNoAi(false);h.setInvulnerable(false);h.setNoGravity(false);persistWorld();done(job);
            }
            case "grant_cancel" -> {done(job);}
            case "sale_prepare" -> {
                if(!loaded(local))return;var h=find(text(local,"source"));if(h==null)throw new IllegalStateException("Escrow source absent; sale remains pending");
                if(!text(event,"operation").equals(h.getPersistentData().getString(LOCK)))throw new IllegalStateException("Escrow source lock mismatch");
                child(job,"sale_commit",object("sale",text(receipt,"sale"),"proof",text(local,"proof")));done(job);
            }
            case "sale_commit" -> {
                if(!loaded(local))return;String source=text(local,"source");
                data.getAsJsonObject("sold").addProperty(source,text(event.getAsJsonObject("payload"),"sale"));save();var h=find(source);
                if(h!=null){String expected=text(event.getAsJsonObject("payload"),"sale");if(!expected.equals(h.getPersistentData().getString(LOCK)))throw new IllegalStateException("Committed source cannot be removed without custody lock");h.discard();persistWorld();}done(job);
            }
            case "sale_cancel" -> {restoreSource(job);done(job);}
            case "rent" -> {
                String id=text(receipt,"rental");if(!rentals().has(id)){var r=receipt.deepCopy();r.addProperty("uid",uid);r.addProperty("player",playerUuid(uid).toString());var member=GameRuntime.get(server).memberships.owner(playerUuid(uid));if(member==null||!member.game().equals(HorseRooms.ID)||!member.team().session.toString().equals(text(receipt,"race"))){r.addProperty("end","prestart_cancel");r.addProperty("released",true);}rentals().add(id,r);save();}done(job);
            }
            case "rental_launch","rental_running" -> {var r=rentals().getAsJsonObject(text(receipt,"rental"));if(r!=null){r.addProperty("state",text(receipt,"state"));save();}done(job);}
            case "rental_end" -> {var r=rentals().getAsJsonObject(text(receipt,"rental"));if(r!=null){r.addProperty("state","ENDED");r.addProperty("refunded",receipt.get("refunded").getAsBoolean());save();}done(job);}
            default -> done(job);
        }
    }
    private void restoreSource(JsonObject job){
        var local=job.getAsJsonObject("local");if(!loaded(local))throw new IllegalStateException("Source recovery waiting for loaded chunk");var h=find(text(local,"source"));if(h==null)throw new IllegalStateException("原马缺失，禁止复制补偿");
        try{h.load(TagParser.parseTag(text(local,"snapshot")));h.getPersistentData().remove(LOCK);persistWorld();releaseTicket(local);save();var p=server.getPlayerList().getPlayer(UUID.fromString(text(local,"player")));if(p!=null&&p.getVehicle()==null&&p.level()==h.level()&&p.distanceToSqr(h)<64)p.startRiding(h,true);}
        catch(com.mojang.brigadier.exceptions.CommandSyntaxException failure){throw new IllegalStateException("Invalid durable source snapshot",failure);}
    }
    public JsonObject rental(UUID race,UUID player){for(var entry:rentals().entrySet()){var r=entry.getValue().getAsJsonObject();if(race.toString().equals(text(r,"race"))&&player.toString().equals(text(r,"player"))&&!text(r,"state").equals("ENDED")&&!r.has("end"))return r;}return null;}
    public HorseIdentity identity(UUID race,UUID player){var r=rental(race,player);if(r==null)return null;var b=r.getAsJsonObject("body");return new HorseIdentity(UUID.fromString(text(r,"asset")),text(b,"name"),b.get("speed").getAsDouble(),b.get("jump").getAsDouble(),b.get("health").getAsDouble(),true);}
    public boolean launch(UUID race,Collection<UUID> players){
        boolean ready=true;for(var player:players){var r=rental(race,player);if(r==null)continue;if(!text(r,"state").equals("LAUNCHING")&&!text(r,"state").equals("RACING")){enqueue(r.get("uid").getAsLong(),"rental_launch",object("rental",text(r,"rental"),"race",race.toString()),business(text(r,"rental"),"rental_launch"),new JsonObject());ready=false;}}return ready;
    }
    public void running(UUID race,Collection<UUID> players){for(var player:players){var r=rental(race,player);if(r!=null)enqueue(r.get("uid").getAsLong(),"rental_running",object("rental",text(r,"rental"),"race",race.toString()),business(text(r,"rental"),"rental_running"),new JsonObject());}}
    public void end(UUID race,UUID player,String reason,boolean released){
        thread();boolean changed=false;for(var entry:rentals().entrySet()){var r=entry.getValue().getAsJsonObject();if(!race.toString().equals(text(r,"race"))||(player!=null&&!player.toString().equals(text(r,"player")))||text(r,"state").equals("ENDED"))continue;
            if(!r.has("end")){r.addProperty("end",reason);changed=true;}if(released&&(!r.has("released")||!r.get("released").getAsBoolean())){r.addProperty("released",true);changed=true;}
        }if(changed)save();
    }
    public void released(UUID race){for(var entry:rentals().entrySet()){var r=entry.getValue().getAsJsonObject();if(race.toString().equals(text(r,"race"))&&r.has("end"))r.addProperty("released",true);}save();}
    public void cancelRental(UUID race,UUID player){var r=rental(race,player);if(r==null)throw new IllegalStateException("本场没有租赁");end(race,player,"prestart_cancel",true);}
    public boolean pendingConfirmation(UUID race){for(var entry:jobs().entrySet()){var j=entry.getValue().getAsJsonObject();if(text(j.getAsJsonObject("event"),"action").equals("rent")&&race.toString().equals(text(j.getAsJsonObject("local"),"race"))&&!Set.of("DONE","REJECTED").contains(text(j,"state")))return true;}return false;}
    public JsonObject snapshot(ServerPlayer p){
        var out=new JsonObject();out.addProperty("tradingEnabled",enabled());out.addProperty("realm",realm());long uid;try{uid=TrustedAccounts.uid(p);}catch(IllegalArgumentException unknown){return out;}
        if(markets.containsKey(uid))out.add("service",markets.get(uid).deepCopy());var own=new JsonArray();var entries=jobs().entrySet().stream().filter(e->e.getValue().getAsJsonObject().getAsJsonObject("event").get("uid").getAsLong()==uid).toList();for(var entry:entries.subList(Math.max(0,entries.size()-64),entries.size())){var job=entry.getValue().getAsJsonObject();var v=new JsonObject();v.addProperty("id",entry.getKey());v.addProperty("action",text(job.getAsJsonObject("event"),"action"));v.addProperty("state",text(job,"state"));if(job.has("receipt"))v.add("receipt",job.get("receipt").deepCopy());if(job.has("error"))v.addProperty("error",playerError(text(job,"error")));own.add(v);}out.add("orders",own);return out;
    }
    public void ui(ServerPlayer p,JsonObject ui,UUID race,boolean waiting){
        var state=snapshot(p);var page=TerminalUi.page(ui,"shop","马匹商店");var section=TerminalUi.section(page,"买养与单场租赁","购买后为自己的原版马，可养育、改名；竞赛使用属性副本，原马留在原地。整匹售出含鞍和马铠，先取下不想出售的装备。租赁只供本场，顶级租金高于本场全部玩家奖励。新交易当前"+(enabled()?"开放":"关闭")+"。");
        TerminalUi.action(section,"刷新积分与库存","tradeRefresh","",true);TerminalUi.action(section,"为正骑的自养马报价","tradeQuoteSell","",enabled()&&race==null);
        var catalog=state.has("service")?state.getAsJsonObject("service").getAsJsonArray("catalog"):POLICY.getAsJsonArray("catalog");
        for(var item:catalog){var b=item.getAsJsonObject();var card=TerminalUi.card(section,text(b,"name")+" · "+b.get("tier").getAsInt()+" 星",details(b)+" · 购买 "+b.get("purchase").getAsInt()+" 分 · 单场租 "+b.get("rent").getAsInt()+" 分");describePreview(card,b,UUID.nameUUIDFromBytes(("muxi-horse-lab-catalog-v1:"+text(b,"name")).getBytes(StandardCharsets.UTF_8)).toString());TerminalUi.action(card,"查看购买确认","tradeQuoteBuy",text(b,"sku"),enabled()&&race==null);}
        var companions=TerminalUi.section(page,"赛马娘兑换","兑换有主人的车万女仆，使用已审核 YSM 外观。档位、词条和价格购买前固定，随从强敌新增增益沿用现有半效；不提供女仆租赁或马匹转卖。领取在原购买位置，失败订单可继续恢复。");
        boolean maidReady=UmamusumeCompanion.installed()&&UmamusumeCompanion.registrationVerified()&&state.has("service")&&state.getAsJsonObject("service").has("maidCatalogReady")&&state.getAsJsonObject("service").get("maidCatalogReady").getAsBoolean();
        if(!maidReady)TerminalUi.card(companions,"等待外观准备","外观准备完成后开放兑换，当前展示固定商品。");
        var maidCatalog=state.has("service")&&state.getAsJsonObject("service").has("maidCatalog")?state.getAsJsonObject("service").getAsJsonArray("maidCatalog"):UmamusumeCatalog.offers();
        for(var item:maidCatalog){var b=item.getAsJsonObject();var card=TerminalUi.card(companions,text(b,"name"),UmamusumeCatalog.details(b)+" · 兑换 "+b.get("purchase").getAsLong()+" 分");describePreview(card,b,text(b,"sku"));TerminalUi.action(card,"查看兑换确认","tradeQuoteBuy",text(b,"sku"),enabled()&&maidReady&&race==null);}
        if(state.has("service")){var market=state.getAsJsonObject("service");TerminalUi.card(section,"统一积分",text(market,"balance"));
            var stock=TerminalUi.section(page,"赛方马池","进入等待房间后可租用；未开始取消或掉线全退，开始后主动退出或掉线不退。场地、服务器故障全退。预订十分钟未开赛全退。每匹池马同一时刻只租给一人。");
            for(var item:market.getAsJsonArray("pool")){var asset=item.getAsJsonObject();var b=asset.getAsJsonObject("body");var card=TerminalUi.card(stock,text(b,"name"),traits(b)+" · 租 "+b.get("rent").getAsInt()+" 分 · "+text(asset,"state"));describePreview(card,b,text(asset,"asset"));TerminalUi.action(card,"查看租赁确认","tradeQuoteRent",text(asset,"asset"),enabled()&&waiting&&text(asset,"state").equals("POOL"));}
        }
        var orders=TerminalUi.section(page,"报价与订单","报价两分钟有效；价格、属性和词条购买前固定。回执丢失会按同一订单重试，重新登录可继续领取。服务器故障未交付购买可全额撤销；已经交付自己的马不能按未交付退款。");
        var rows=state.getAsJsonArray("orders");if(rows!=null)for(int i=Math.max(0,rows.size()-15);i<rows.size();i++){var order=rows.get(i).getAsJsonObject();String action=text(order,"action");var card=TerminalUi.card(orders,actionLabel(action)+" · "+statusLabel(text(order,"state")),order.has("error")?playerError(text(order,"error")):"");
            if(order.has("receipt")){var receipt=order.getAsJsonObject("receipt");if(action.equals("quote")){var b=receipt.getAsJsonObject("body");describePreview(card,b,b.has("asset")?text(b,"asset"):UUID.nameUUIDFromBytes(("muxi-horse-lab-catalog-v1:"+text(b,"name")).getBytes(StandardCharsets.UTF_8)).toString());card.addProperty("title",text(b,"name")+" · "+kindLabel(text(receipt,"kind")));card.addProperty("text",details(b)+" · 确认金额 "+receipt.get("amount").getAsInt()+" 分"+(text(receipt,"kind").equals("SELL")?" · 整匹含现有鞍/铠出售，确认后原马归赛方":""));TerminalUi.action(card,"确认这笔交易","tradeConfirm",text(receipt,"quote"),enabled()&&receipt.get("expires").getAsLong()>System.currentTimeMillis()/1000&&!jobs().has(business(text(receipt,"quote"),"confirm").toString()));}else if(receipt.has("balanceAfter"))card.addProperty("text","交易后积分 "+text(receipt,"balanceAfter"));}
            if(action.equals("buy")&&!text(order,"state").equals("REJECTED"))TerminalUi.action(card,"撤销尚未交付购买","tradeCancelDelivery",text(order,"id"),true);
        }
        if(race!=null&&waiting&&rental(race,p.getUUID())!=null)TerminalUi.action(orders,"取消本场租马，全额退还","tradeCancelRental","",true);
    }
    /** Local vanilla illustrations; generic stock never claims its picture matches armor/color. */
    public static void describePreview(JsonObject card,JsonObject body,String stableId){
        if(UmamusumeCatalog.isMaid(body)){var appearance=new JsonObject();appearance.addProperty("entity","touhou_little_maid:maid");appearance.add("modelId",body.get("modelId"));appearance.add("texture",body.get("texture"));card.add("maidAppearance",appearance);var preview=new JsonObject();preview.addProperty("kind","resource");preview.addProperty("id","muxi_horse_racing:textures/gui/umamusume_preview/"+text(body,"sku")+".png");preview.addProperty("label",text(body,"name")+" · 本地外观预览");preview.addProperty("width",256);preview.addProperty("height",256);card.add("preview",preview);return;}
        String sku=body.has("sku")&&!body.get("sku").isJsonNull()?text(body,"sku"):"";boolean saddled=!body.has("saddled")||body.get("saddled").getAsBoolean();
        boolean armored=body.has("armored")&&body.get("armored").getAsBoolean();boolean exact=false;
        for(var item:POLICY.getAsJsonArray("catalog")){var template=item.getAsJsonObject();if(text(template,"sku").equals(sku)&&template.get("variant").equals(body.get("variant"))&&template.get("markings").equals(body.get("markings"))&&saddled&&!armored){exact=true;break;}}
        String image=exact?sku:"generic";card.addProperty("horseSku",sku.isEmpty()?"pool":sku);card.addProperty("horsePreviewId",stableId);
        var preview=new JsonObject();preview.addProperty("kind","resource");preview.addProperty("id","muxi_minigames:textures/gui/horse_preview/"+image+".png");
        preview.addProperty("label",exact?"原版马外观静态预览，不含骑手":"通用原版马示意；实际毛色和装备以描述为准，不含骑手");
        preview.addProperty("width",256);preview.addProperty("height",192);preview.addProperty("exactAppearance",exact);preview.addProperty("riderIncluded",false);card.add("preview",preview);
        var appearance=new JsonObject();appearance.addProperty("entity","minecraft:horse");
        for(String key:List.of("variant","markings","speed","jump","health"))if(body.has(key))appearance.add(key,body.get(key).deepCopy());
        appearance.addProperty("saddled",saddled);appearance.addProperty("armored",armored);appearance.addProperty("armorTypeKnown",!armored);
        card.add("horseAppearance",appearance);
    }
    private static String traits(JsonObject b){return b.has("traits")?String.join(" / ",b.getAsJsonArray("traits").asList().stream().map(JsonElement::getAsString).toList()):"";}
    private static String details(JsonObject b){if(UmamusumeCatalog.isMaid(b))return UmamusumeCatalog.details(b);return traits(b)+" · 速度 "+text(b,"speed")+" / 跳跃 "+text(b,"jump")+" / 生命 "+text(b,"health");}
    private static String kindLabel(String kind){return switch(kind){case "BUY"->"购买确认";case "SELL"->"整匹出售确认";default->"单场租赁确认";};}
    private static String statusLabel(String state){return switch(state){case "QUEUED"->"正在提交";case "RECEIVED"->"正在处理";case "DONE"->"已处理";default->"暂未完成";};}
    private static String actionLabel(String action){return switch(action){case "quote"->"交易报价";case "buy"->"购买订单";case "grant_ack"->"实体交付";case "grant_cancel"->"购买撤销";case "sale_prepare"->"出售托管";case "sale_commit"->"出售完成";case "sale_cancel"->"出售撤销";case "rent"->"单场租赁";case "rental_launch"->"租马准备";case "rental_running"->"租马开赛";case "rental_end"->"租赁结清";default->"交易处理";};}
    public JsonObject evidence(){return data.deepCopy();}
    public JsonObject previewMarket(JsonObject balance){var shop=new JsonObject();shop.addProperty("currency","minigame_points");shop.addProperty("quotesOnly",!enabled());shop.addProperty("tradingEnabled",enabled());shop.addProperty("maximumRaceReward",POLICY.get("maxRacePlayerReward").getAsInt());shop.add("balance",balance);var offers=new JsonArray();for(var item:POLICY.getAsJsonArray("catalog")){var offer=item.getAsJsonObject().deepCopy();offer.addProperty("horse",UUID.nameUUIDFromBytes(("muxi-horse-lab-catalog-v1:"+text(offer,"name")).getBytes(StandardCharsets.UTF_8)).toString());describePreview(offer,offer,text(offer,"horse"));offers.add(offer);}shop.add("offers",offers);shop.add("maidOffers",UmamusumeCatalog.offers());return shop;}
    public void close(){closed=true;save();transport.close();}
}
