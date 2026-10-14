package net.muxigame.minigames.horseqa;
import com.google.gson.*;
import net.minecraft.server.MinecraftServer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.neoforged.fml.ModList;
import net.muxigame.minigames.horse.*;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.event.server.ServerStartedEvent;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import java.nio.file.*;
import java.util.*;

/** Every catalog layout uses native Horse collisions, gravity, gate progression and water jumping. */
@EventBusSubscriber(modid="muxi_horse_racing")
public final class HorseCourseQA {
    private static MinecraftServer server;
    private static HorsePilot pilot;
    private static int index,ticks;
    private static boolean done;
    private static double maxY,minY;
    private static final UUID identity=UUID.fromString("4b636775-6865-49c4-a6ed-96d04813b611");
    private static HorseHistory history;
    private static final Path HISTORY=Path.of("course-history.json").toAbsolutePath();
    private static final JsonArray checks=new JsonArray(),runs=new JsonArray();
    private static void check(boolean pass,String label){JsonObject row=new JsonObject();row.addProperty("label",label);row.addProperty("passed",pass);checks.add(row);if(!pass)throw new IllegalStateException(label);}
    private static void begin(){int count=HorseCourses.testTemplates().size();var definition=index<count?HorseCourses.testTemplates().get(index):HorseCourses.get("hills-plains");var course=new HorseCourse(index*96,0,definition);course.build(server.getLevel(HorseCourse.DIMENSION));if(definition.hills()&&ModList.get().isLoaded("biomeswevegone")){check(ModList.get().isLoaded("ohthetreesyoullgrow"),"installed OTTYG dependency active");var leaf=server.getLevel(HorseCourse.DIMENSION).getBlockState(new BlockPos(index*96+4,68,definition.loop()?20:6)).getBlock();check(BuiltInRegistries.BLOCK.getKey(leaf).getNamespace().equals("biomeswevegone"),definition.id()+": generated scenery uses actual registered BWG block");}var horse=index<count?new HorseIdentity(identity,"Catalog QA",.25,.9,26,false):HorseMarket.enemy(HorseMarket.Config.preview(),index-count+1).identity();pilot=new HorsePilot(server.getLevel(HorseCourse.DIMENSION),course,horse,"wine_fox/01_taisho_maid","skin");maxY=minY=65;}
    @SubscribeEvent public static void started(ServerStartedEvent e){if(!Boolean.getBoolean("muxi.horse.courseQa"))return;server=e.getServer();try{check(Files.exists(Path.of("HORSE_QA_ONLY")),"own private world marker");check(server.getPlayerList().getPlayers().isEmpty(),"no real player assets in catalog QA");boolean denied=false;try{HorseCourses.get("../../other");}catch(IllegalArgumentException expected){denied=true;}check(denied,"unknown course ID rejected");history=new HorseHistory(HISTORY);begin();}catch(Throwable failure){finish(false,failure.toString());}}
    @SubscribeEvent public static void tick(ServerTickEvent.Post e){if(!Boolean.getBoolean("muxi.horse.courseQa")||done||pilot==null)return;try{ticks++;pilot.tick();maxY=Math.max(maxY,pilot.horse.getY());minY=Math.min(minY,pilot.horse.getY());if(ticks%10==0){JsonObject motion=new JsonObject();motion.addProperty("course",pilot.course.id);motion.addProperty("tick",pilot.ticks);motion.addProperty("gate",pilot.gate);motion.addProperty("x",pilot.horse.getX());motion.addProperty("y",pilot.horse.getY());motion.addProperty("z",pilot.horse.getZ());motion.addProperty("recoveries",pilot.recoveries);Files.writeString(Path.of("courses-motion.jsonl"),motion+"\n",StandardOpenOption.CREATE,StandardOpenOption.APPEND);}if(!pilot.finished&&!pilot.dnf)return;
        var course=pilot.course;JsonObject run=new JsonObject();run.addProperty("course",course.id);run.addProperty("enemyTier",index<HorseCourses.testTemplates().size()?0:index-HorseCourses.testTemplates().size()+1);run.addProperty("ticks",pilot.resultTicks());run.addProperty("jumps",pilot.jumps);run.addProperty("recoveries",pilot.recoveries);run.addProperty("gate",pilot.gate);run.addProperty("maxY",maxY);run.addProperty("minY",minY);runs.add(run);
        check(pilot.finished&&!pilot.dnf,course.id+": native horse completes course");check(pilot.gate==course.gates.size(),course.id+": every gate reached");check(pilot.jumps>=(course.definition.loop()?2:1),course.id+": obstacle jumps occur");check(pilot.recoveries<=1,course.id+": bounded recovery, no repeated stall");if(course.definition.hills())check(maxY>=68,course.id+": native horse climbs playable hill");history.record(new HorseHistory.Result(UUID.randomUUID(),pilot.identity.horseId(),pilot.identity.name(),course.id,course.version,pilot.resultTicks(),pilot.recoveries,false));check(history.top(course.id,course.version).size()==(index<HorseCourses.testTemplates().size()?1:index-HorseCourses.testTemplates().size()+2),course.id+": same UUID stored independently by course version");pilot.close();course.unload(server.getLevel(HorseCourse.DIMENSION));pilot=null;if(++index==HorseCourses.testTemplates().size()+5){check(new HorseHistory(HISTORY).top("hills-snow","1").size()==1,"course history survives reload");finish(true,"");}else begin();
    }catch(Throwable failure){finish(false,failure.toString());}}
    private static void finish(boolean pass,String error){if(done)return;done=true;try{if(pilot!=null){pilot.close();pilot.course.unload(server.getLevel(HorseCourse.DIMENSION));}JsonObject report=new JsonObject();report.addProperty("success",pass);report.addProperty("error",error);report.add("checks",checks);report.add("runs",runs);Files.writeString(Path.of("result.json"),new GsonBuilder().setPrettyPrinting().create().toJson(report));}catch(Exception failure){failure.printStackTrace();}server.halt(false);}
}
