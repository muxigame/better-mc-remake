package net.muxigame.localdebug;
import com.google.gson.*;
import net.minecraft.server.level.*;
import net.minecraft.resources.*;
import net.minecraft.core.*;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.Component;
import net.minecraft.world.level.*;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.item.DyeColor;
import net.blay09.mods.waystones.api.*;
import net.muxigame.core.feature.waystones.network.*;
import net.neoforged.fml.common.Mod;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import java.util.*;
@Mod(value="local_mc_debug_qa",dist=Dist.DEDICATED_SERVER)
public final class TeleportProbeServer {
 int last; static final Map<String,Waystone> stones=new LinkedHashMap<>();
 public TeleportProbeServer(){if(DebugFiles.enabled())NeoForge.EVENT_BUS.addListener(this::tick);}
 static ResourceKey<Level> dim(String v){return ResourceKey.create(Registries.DIMENSION,ResourceLocation.parse(v));}
 static void platform(ServerLevel l,BlockPos p){for(int x=-2;x<=2;x++)for(int z=-2;z<=2;z++){l.setBlockAndUpdate(p.offset(x,-1,z),Blocks.STONE.defaultBlockState());for(int y=0;y<5;y++)l.setBlockAndUpdate(p.offset(x,y,z),Blocks.AIR.defaultBlockState());}}
 static Waystone place(ServerLevel l,BlockPos p,String name,boolean shared){platform(l,p);Waystone s=(shared?WaystonesAPI.placeSharestone(l,p,DyeColor.YELLOW):WaystonesAPI.placeWaystone(l,p,new WaystoneStyle(ResourceLocation.parse("waystones:waystone")))).orElseThrow();((MutableWaystone)s).setName(Component.literal(name));stones.put(name,s);return s;}
 static JsonObject state(net.minecraft.server.MinecraftServer server,ServerPlayer p){
  var out=new JsonObject();out.addProperty("dimension",p.level().dimension().location().toString());out.addProperty("x",p.getX());out.addProperty("y",p.getY());out.addProperty("z",p.getZ());out.addProperty("xpLevel",p.experienceLevel);out.addProperty("xpTotal",p.totalExperience);out.addProperty("xpProgress",p.experienceProgress);
  var cfg=NetworkPortalFacilities.config();long start=System.nanoTime();var source=NetworkPortalFacilities.source(p,cfg);out.addProperty("sourceMillis",(System.nanoTime()-start)/1e6);out.addProperty("near",source!=null);out.addProperty("radius",cfg.entranceRadius());out.addProperty("vertical",cfg.entranceVertical());out.addProperty("source",source==null?"":source.stone().getName().getString());
  var list=new JsonArray();for(var e:stones.entrySet()){var s=e.getValue();var row=new JsonObject();row.addProperty("name",e.getKey());row.addProperty("uuid",s.getWaystoneUid().toString());row.addProperty("dimension",s.getDimension().location().toString());row.addProperty("x",s.getPos().getX());row.addProperty("y",s.getPos().getY());row.addProperty("z",s.getPos().getZ());row.addProperty("actual",NetworkPortalFacilities.actualStone(server.getLevel(s.getDimension()),s));row.addProperty("linked",NetworkPortalFacilities.portalBadge(server,s,cfg));row.addProperty("activated",WaystonesAPI.isWaystoneActivated(p,s));var quote=NetworkMapDetails.quote(p,source,s,cfg);row.addProperty("cost",quote.cost());row.addProperty("quoted",quote.quoted());row.addProperty("affordable",quote.affordable());row.addProperty("denial",quote.denial());list.add(row);}out.add("stones",list);
  var games=new JsonArray();for(var l:server.getAllLevels())if(net.muxigame.minigames.GameAreas.contains(l))games.add(l.dimension().location().toString());out.add("gameDimensions",games);return out;
 }
 void tick(ServerTickEvent.Post e){try{var c=DebugFiles.read("teleport-server.json");if(c==null||c.get("id").getAsInt()<=last)return;last=c.get("id").getAsInt();var out=DebugFiles.result(last,"server");try{var server=e.getServer();var p=server.getPlayerList().getPlayerByName("10000");if(p==null)throw new IllegalStateException("real host absent");
  switch(c.get("action").getAsString()){
   case "setup"->{
    var l=server.overworld();place(l,new BlockPos(0,100,0),"Source",false);place(l,new BlockPos(970,100,0),"Normal",false);place(l,new BlockPos(1002,100,0),"Shared",true);place(l,new BlockPos(1005,100,0),"Linked",false);
    var f=new net.muxigame.core.feature.dimensions.WorldPortals.Frame(new BlockPos(1005,100,2),Direction.Axis.X,1);
    for(int x=0;x<4;x++)for(int y=0;y<5;y++)l.setBlock(f.at(x,y),(x==0||x==3||y==0||y==4?Blocks.STONE:net.muxigame.core.feature.dimensions.WorldPortals.block(1)).defaultBlockState(),18);
    net.muxigame.minigames.GameAreas.register("qa_registry_game",Level.NETHER);
    place(server.getLevel(Level.NETHER),new BlockPos(0,100,0),"GameStone",false);
    for(var player:server.getPlayerList().getPlayers()){for(var s:stones.values())WaystonesAPI.activateWaystone(player,s);player.setNoGravity(true);player.getAbilities().mayfly=true;player.getAbilities().flying=true;player.onUpdateAbilities();player.teleportTo(l,.5,100,.5,Set.of(),0,0);}
   }
   case "move"->{var l=server.getLevel(dim(c.has("dimension")?c.get("dimension").getAsString():"minecraft:overworld"));p.teleportTo(l,c.get("x").getAsDouble(),c.get("y").getAsDouble(),c.get("z").getAsDouble(),Set.of(),0,0);p.setNoGravity(true);p.getAbilities().flying=true;p.onUpdateAbilities();}
   case "xp"->{p.setExperienceLevels(0);p.setExperiencePoints(0);p.totalExperience=0;p.giveExperiencePoints(c.get("points").getAsInt());}
   case "fee"->{var cfg=net.blay09.mods.waystones.config.WaystonesConfig.getActive();cfg.teleports.enableCosts=true;cfg.teleports.enableCooldowns=false;cfg.teleports.warpRequirements=List.of(c.get("expression").getAsString());out.addProperty("assistedNativeFeeFixture",true);}
   case "activate"->{WaystonesAPI.activateWaystone(p,stones.get(c.get("stone").getAsString()));}
   case "break"->{var s=stones.get(c.get("stone").getAsString());server.getLevel(s.getDimension()).destroyBlock(s.getPos(),false);}
   case "cross-setup"->{var l=server.getLevel(dim("muxi_game_core:overworld"));var s=place(l,new BlockPos(20,100,20),"CrossDimension",false);WaystonesAPI.activateWaystone(p,s);}
   case "observe"->{}
   case "frame"->{var l=server.overworld();var f=new net.muxigame.core.feature.dimensions.WorldPortals.Frame(new BlockPos(1005,100,2),Direction.Axis.X,1);for(int x=0;x<4;x++)for(int y=0;y<5;y++)l.setBlock(f.at(x,y),(x==0||x==3||y==0||y==4?Blocks.STONE:net.muxigame.core.feature.dimensions.WorldPortals.block(1)).defaultBlockState(),18);out.addProperty("validFrame",net.muxigame.core.feature.dimensions.WorldPortals.valid(l,f,true));}

   default->throw new IllegalArgumentException("action");
  }out.add("state",state(server,p));out.addProperty("ok",true);
 }catch(Throwable t){t.printStackTrace();out.addProperty("ok",false);out.addProperty("error",t.toString());}DebugFiles.write("teleport-result-server-"+last+".json",out);}catch(Exception x){x.printStackTrace();}}
}
