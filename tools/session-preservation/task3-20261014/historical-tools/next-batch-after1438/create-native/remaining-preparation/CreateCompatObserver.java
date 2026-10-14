package net.muxigame.localdebug;
import com.google.gson.*;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.chunk.LevelChunk;
import net.minecraft.core.registries.BuiltInRegistries;
import net.neoforged.fml.ModList;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import java.lang.reflect.*;
@Mod("local_mc_debug_qa")
public final class CreateCompatObserver {
 private int last;
 public CreateCompatObserver(){if(DebugFiles.enabled())NeoForge.EVENT_BUS.addListener(this::tick);}
 private static Object call(Object o,String name)throws Exception{return o.getClass().getMethod(name).invoke(o);}
 public void tick(ServerTickEvent.Post event){
  try{var request=DebugFiles.read("create-compat-request.json");if(request==null)return;int id=request.get("id").getAsInt();if(id<=last)return;last=id;
   var result=DebugFiles.result(id,"server");
   try{if(!"snapshot".equals(request.get("action").getAsString()))throw new IllegalArgumentException("read-only snapshot only");result.add("snapshot",snapshot(event.getServer()));result.addProperty("ok",true);result.addProperty("readOnly",true);result.addProperty("noChunkLoads",true);}catch(Throwable e){result.addProperty("ok",false);result.addProperty("error",e.toString());}
   DebugFiles.write("create-compat-result-"+id+".json",result);
  }catch(Throwable e){System.err.println("CREATE_COMPAT_QA_OBSERVER_ERROR "+e);}
 }
 static JsonObject snapshot(MinecraftServer server)throws Exception{
  var out=new JsonObject();var versions=new JsonObject();for(String id:new String[]{"sliceanddice","interiors","create","farmersdelight","sable","sablecompanion","aeronautics"})versions.addProperty(id,ModList.get().getModContainerById(id).map(v->v.getModInfo().getVersion().toString()).orElse("NOT_REGISTERED"));out.add("versions",versions);
  var players=new JsonArray();for(var p:server.getPlayerList().getPlayers()){var row=new JsonObject();row.addProperty("uuid",p.getUUID().toString());row.addProperty("dimension",p.level().dimension().location().toString());row.addProperty("health",p.getHealth());row.addProperty("x",p.getX());row.addProperty("y",p.getY());row.addProperty("z",p.getZ());row.addProperty("riding",p.isPassenger());var root=p.getRootVehicle();row.addProperty("rootUuid",root.getUUID().toString());row.addProperty("rootType",BuiltInRegistries.ENTITY_TYPE.getKey(root.getType()).toString());var inv=new JsonArray();for(int i=0;i<p.getInventory().getContainerSize();i++){var stack=p.getInventory().getItem(i);if(stack.isEmpty())continue;var x=new JsonObject();x.addProperty("slot",i);x.addProperty("item",BuiltInRegistries.ITEM.getKey(stack.getItem()).toString());x.addProperty("count",stack.getCount());inv.add(x);}row.add("inventory",inv);players.add(row);}out.add("players",players);
  var bodies=new JsonArray();Class<?> api=Class.forName("dev.ryanhcode.sable.api.sublevel.SubLevelContainer");
  for(ServerLevel level:server.getAllLevels()){Object container=api.getMethod("getContainer",ServerLevel.class).invoke(null,level);for(Object body:(Iterable<?>)call(container,"getAllSubLevels")){var row=new JsonObject();row.addProperty("uuid",call(body,"getUniqueId").toString());row.addProperty("name",String.valueOf(call(body,"getName")));row.addProperty("dimension",level.dimension().location().toString());row.addProperty("pose",call(body,"logicalPose").toString());row.addProperty("bounds",call(body,"boundingBox").toString());Object plot=call(body,"getPlot");var blocks=new JsonArray();int count=0;for(Object holder:(Iterable<?>)call(plot,"getLoadedChunks")){Object chunk=call(holder,"getChunk");if(!(chunk instanceof LevelChunk c))throw new IllegalStateException("unsupported loaded chunk holder ABI");for(BlockEntity be:c.getBlockEntities().values()){if(++count>512)throw new IllegalStateException("bounded QA body exceeded512 block entities");var x=new JsonObject();x.addProperty("pos",be.getBlockPos().toShortString());x.addProperty("type",BuiltInRegistries.BLOCK_ENTITY_TYPE.getKey(be.getType()).toString());x.addProperty("state",be.getBlockState().toString());x.addProperty("nbt",be.saveWithFullMetadata(server.registryAccess()).toString());blocks.add(x);}}row.add("loadedBlockEntities",blocks);bodies.add(row);}}
  out.add("sableBodies",bodies);
  var recipes=new JsonArray();for(var holder:server.getRecipeManager().getRecipes()){String id=holder.id().toString();if(!id.contains("cocoa")&&!id.contains("custard"))continue;var x=new JsonObject();x.addProperty("id",id);x.addProperty("class",holder.value().getClass().getName());var ingredients=new JsonArray();for(var ingredient:holder.value().getIngredients()){var items=new JsonArray();for(var stack:ingredient.getItems())items.add(BuiltInRegistries.ITEM.getKey(stack.getItem()).toString());ingredients.add(items);}x.add("ingredients",ingredients);var fluids=new JsonArray();try{Object list=holder.value().getClass().getMethod("getFluidIngredients").invoke(holder.value());for(Object value:(Iterable<?>)list){var fluid=(net.neoforged.neoforge.fluids.crafting.SizedFluidIngredient)value;var f=new JsonObject();f.addProperty("amount",fluid.amount());var candidates=new JsonArray();for(var stack:fluid.getFluids())candidates.add(BuiltInRegistries.FLUID.getKey(stack.getFluid()).toString());f.add("fluidIds",candidates);fluids.add(f);}}catch(NoSuchMethodException noFluid){x.addProperty("fluidRecipeApi",false);}x.add("fluidIngredients",fluids);recipes.add(x);}out.add("containerRecipeCandidates",recipes);return out;
 }
}
