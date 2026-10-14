package net.muxigame.minigames.horseqa;
import com.google.gson.*;
import net.minecraft.network.chat.Component;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.server.MinecraftServer;
import net.minecraft.world.entity.*;
import net.minecraft.world.entity.animal.horse.Horse;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.item.*;
import net.minecraft.world.level.storage.LevelResource;
import net.minecraft.sounds.SoundSource;
import net.muxigame.minigames.horse.*;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.event.server.ServerStartedEvent;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import java.nio.file.*;
import java.util.*;

/** Physical transfers of generated fixtures only. Real JVM restart returns the persisted same UUID. */
@EventBusSubscriber(modid="muxi_horse_racing")
public final class HorseAssetQA {
    private static MinecraftServer server;
    private static HorseAssetLease leases;
    private static HorsePilot pilot;
    private static HorseCourse course;
    private static boolean done,resume;
    private static int ticks;
    private static final UUID HORSE=UUID.fromString("8a7e291c-6611-45aa-95cf-986f585966fe"),OWNER=UUID.fromString("fd31d345-c5c6-44d2-9b63-0b9ad0bb653f"),FIRST=UUID.fromString("a63bd675-2d96-4810-b4ba-6198e7e6b3de"),SECOND=UUID.fromString("b6f70d01-21b4-4c0b-a9d6-85f51c4bb0d3");
    private static final JsonArray checks=new JsonArray();
    private static void check(boolean pass,String label){JsonObject row=new JsonObject();row.addProperty("label",label);row.addProperty("passed",pass);checks.add(row);if(!pass)throw new IllegalStateException(label);}
    private static Horse original(){return (Horse)server.overworld().getEntity(HORSE);}
    private static void unchanged(){Horse horse=original();check(horse!=null&&horse.isAlive(),"original native horse returned alive to source dimension");check(horse.getUUID().equals(HORSE)&&OWNER.equals(horse.getOwnerUUID()),"stable horse and owner UUID preserved");check(horse.getCustomName().getString().equals("Physical asset fixture"),"source horse name preserved");check(Math.abs(horse.getHealth()-17)<.001&&Math.abs(horse.getMaxHealth()-26)<.001,"pre-race current health and max health restored");check(Math.abs(horse.getAttributeValue(Attributes.MOVEMENT_SPEED)-.25)<.00001&&Math.abs(horse.getAttributeValue(Attributes.JUMP_STRENGTH)-.9)<.00001,"source speed and jump restored");check(horse.isSaddled()&&horse.getBodyArmorItem().is(Items.DIAMOND_HORSE_ARMOR),"saddle and body armor restored");check(!horse.isNoAi()&&!horse.isInvulnerable()&&!horse.getTags().contains(HorseAssetLease.LEASE),"temporary protection and lease marker cleared");long count=0;for(var level:server.getAllLevels())for(Entity entity:level.getAllEntities())if(entity.getUUID().equals(HORSE)&&!entity.isRemoved())count++;check(count==1,"same horse UUID exists exactly once across loaded source and destination");}
    @SubscribeEvent public static void started(ServerStartedEvent e){if(!Boolean.getBoolean("muxi.horse.assetQa"))return;server=e.getServer();resume=Boolean.getBoolean("muxi.horse.assetResumeQa");try{
        check(Files.exists(Path.of("HORSE_QA_ONLY"))&&server.getPlayerList().getPlayers().isEmpty(),"own isolated server with no real players");
        if(!resume){Files.writeString(server.getWorldPath(LevelResource.ROOT).resolve("HORSE_ASSET_LAB"),"Generated fixtures only\n");leases=new HorseAssetLease(server);course=new HorseCourse(0,0,HorseCourses.get("hills-plains"));Horse horse=EntityType.HORSE.create(server.overworld());horse.setUUID(HORSE);horse.setOwnerUUID(OWNER);horse.setPos(.5,65,.5);horse.setTamed(true);horse.setAge(0);horse.setPersistenceRequired();horse.setCustomName(Component.literal("Physical asset fixture"));horse.getAttribute(Attributes.MAX_HEALTH).setBaseValue(26);horse.setHealth(17);horse.getAttribute(Attributes.MOVEMENT_SPEED).setBaseValue(.25);horse.getAttribute(Attributes.JUMP_STRENGTH).setBaseValue(.9);horse.equipSaddle(new ItemStack(Items.SADDLE),SoundSource.NEUTRAL);horse.setBodyArmorItem(new ItemStack(Items.DIAMOND_HORSE_ARMOR));server.overworld().addFreshEntity(horse);
            boolean denied=false;try{leases.checkout(FIRST,horse,course);}catch(IllegalStateException expected){denied=true;}check(denied,"unmarked ordinary horses rejected by physical trial");horse.addTag(HorseAssetLease.FIXTURE);server.saveEverything(false,true,true);HorseIdentity identity=HorseIdentity.read(horse);var entry=leases.checkout(FIRST,horse,course);check(Files.isRegularFile(leases.journal())&&entry.phase().equals("ACTIVE"),"durable snapshot precedes active lease");Horse moved=leases.horse(FIRST);check(moved.getUUID().equals(HORSE)&&original()==null,"native cross-dimension transfer moves original stable UUID");check(moved.getType()==EntityType.HORSE&&moved!=horse&&horse.isRemoved(),"native engine removes source incarnation and creates destination incarnation");check(leases.checkout(FIRST,moved,course).token().equals(FIRST),"same lease request is idempotent");denied=false;try{leases.checkout(SECOND,moved,course);}catch(IllegalStateException expected){denied=true;}check(denied,"second concurrent lease rejected");pilot=HorsePilot.leased(server.getLevel(HorseCourse.DIMENSION),course,identity,"wine_fox/01_taisho_maid","skin",leases,FIRST);check(pilot.horse==moved&&!moved.getTags().contains(HorsePilot.MARKER),"pilot uses actual borrowed entity and excludes disposable proxy marker");
        }else{leases=new HorseAssetLease(server);check(leases.pending().size()==1&&leases.pending().get(0).token().equals(SECOND),"actual JVM restart loads pending physical lease");try{leases.restore(SECOND);}catch(IllegalStateException waiting){check(leases.pending().size()==1,"not-yet-loaded horse remains pending without cloning snapshot");}}
    }catch(Throwable failure){finish(false,failure.toString());}}
    @SubscribeEvent public static void tick(ServerTickEvent.Post e){if(!Boolean.getBoolean("muxi.horse.assetQa")||done||leases==null)return;try{ticks++;
        if(resume){if(ticks<40)return;leases.restore(SECOND);unchanged();check(leases.pending().isEmpty(),"restart recovery closes pending lease durably");check(leases.restore(SECOND).phase().equals("CLOSED"),"duplicate restart return cannot create another horse");check(new HorseAssetLease(server).pending().isEmpty(),"closed lease survives journal reload");finish(true,"");return;}
        pilot.tick();if(!pilot.finished&&!pilot.dnf)return;check(pilot.finished&&!pilot.dnf&&pilot.recoveries<=1,"actual borrowed horse completes hilly native race");check(pilot.jumps>=2,"actual borrowed horse jumps fence and water ditch");pilot.close();check(leases.horse(FIRST).isAlive(),"pilot cleanup preserves borrowed horse");pilot=null;leases.restore(FIRST);unchanged();check(leases.restore(FIRST).phase().equals("CLOSED"),"repeated return is idempotent");check(leases.pending().isEmpty(),"first physical lease durably closed");Horse horse=original();leases.checkout(SECOND,horse,course);check(leases.pending().size()==1,"seed interrupted active lease for actual next JVM recovery");server.saveEverything(false,true,true);finish(true,"");
    }catch(Throwable failure){finish(false,failure.toString());}}
    private static void finish(boolean success,String error){if(done)return;done=true;try{if(pilot!=null)pilot.close();JsonObject out=new JsonObject();out.addProperty("success",success);out.addProperty("error",error);out.addProperty("kind","physical generated fixture lease; actual native cross-dimension transfer; no real assets");out.add("checks",checks);Files.writeString(Path.of("result.json"),new GsonBuilder().setPrettyPrinting().create().toJson(out));}catch(Throwable failure){failure.printStackTrace();}server.halt(false);}
}
