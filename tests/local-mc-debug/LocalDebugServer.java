package net.muxigame.localdebug;
import com.google.gson.*;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.item.ItemStack;
import net.neoforged.fml.common.Mod;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.tick.ServerTickEvent;

/** Observes actual server player connections; never creates synthetic ServerPlayers. */
@Mod(value="local_mc_debug_qa",dist=Dist.DEDICATED_SERVER)
public final class LocalDebugServer {
    int last=-1;JsonObject receipt;String receiptName;
    public LocalDebugServer(){if(DebugFiles.enabled())NeoForge.EVENT_BUS.addListener(this::tick);}
    static JsonObject player(ServerPlayer p){
        var row=new JsonObject();row.addProperty("name",p.getGameProfile().getName());row.addProperty("uuid",p.getUUID().toString());
        row.addProperty("network",p.connection.getConnection().getRemoteAddress() instanceof java.net.InetSocketAddress);
        row.addProperty("dimension",p.level().dimension().location().toString());row.addProperty("health",p.getHealth());
        row.addProperty("mode",p.gameMode.getGameModeForPlayer().getName());row.addProperty("selected",p.getInventory().selected);
        var inventory=new JsonArray();var slots=new JsonObject();for(int i=0;i<p.getInventory().getContainerSize();i++){ItemStack stack=p.getInventory().getItem(i);if(stack.isEmpty())continue;var item=new JsonObject();item.addProperty("slot",i);item.addProperty("item",BuiltInRegistries.ITEM.getKey(stack.getItem()).toString());item.addProperty("count",stack.getCount());inventory.add(item);slots.add(Integer.toString(i),item);}row.add("inventory",inventory);row.add("inventoryBySlot",slots);return row;
    }
    static JsonObject status(MinecraftServer server){
        var row=new JsonObject();row.addProperty("runId",DebugFiles.RUN);row.addProperty("tick",server.getTickCount());row.addProperty("players",server.getPlayerList().getPlayerCount());
        var players=new JsonArray();var byName=new JsonObject();for(var p:server.getPlayerList().getPlayers()){var state=player(p);players.add(state);byName.add(p.getGameProfile().getName(),state);}row.add("playerStates",players);row.add("byName",byName);int dropped=0;for(var entity:server.overworld().getAllEntities())if(entity instanceof net.minecraft.world.entity.item.ItemEntity item&&item.isAlive()&&item.getItem().is(net.minecraft.world.item.Items.DIAMOND))dropped+=item.getItem().getCount();row.addProperty("looseDiamonds",dropped);return row;
    }
    void tick(ServerTickEvent.Post e){
        var server=e.getServer();
        try{
            if(server.getTickCount()%20==0)DebugFiles.write("status-server.json",status(server));
            if(receipt!=null){DebugFiles.write(receiptName,receipt);receipt=null;}
            var command=DebugFiles.read("command-server.json");if(command==null||command.get("id").getAsInt()<=last)return;
            int id=command.get("id").getAsInt();last=id;receipt=DebugFiles.result(id,"server");receiptName="result-server-"+id+".json";
            try{
                String type=command.get("type").getAsString();
                switch(type){
                    case "observe"->{}
                    case "execute"->{String text=command.get("command").getAsString();server.getCommands().performPrefixedCommand(server.createCommandSourceStack(),text);receipt.addProperty("assistedServerCommand",true);}
                    case "inventory-set"->{var p=server.getPlayerList().getPlayerByName(command.get("player").getAsString());if(p==null)throw new IllegalArgumentException("Player absent");int slot=command.get("slot").getAsInt(),count=command.get("count").getAsInt();if(slot<0||slot>=p.getInventory().getContainerSize()||count<0||count>64)throw new IllegalArgumentException("Invalid inventory fixture");var key=ResourceLocation.parse(command.get("item").getAsString());var item=BuiltInRegistries.ITEM.getOptional(key).orElseThrow();if(count>new ItemStack(item).getMaxStackSize())throw new IllegalArgumentException("Count exceeds item limit");p.getInventory().setItem(slot,count==0?ItemStack.EMPTY:new ItemStack(item,count));p.inventoryMenu.broadcastChanges();receipt.addProperty("assistedInventoryFixture",true);}
                    case "game-snapshot"->{var p=server.getPlayerList().getPlayerByName(command.get("player").getAsString());if(p==null)throw new IllegalArgumentException("Player absent");Class<?> runtime=Class.forName("net.muxigame.minigames.GameRuntime");Object instance=runtime.getMethod("get",MinecraftServer.class).invoke(null,server);Object snapshot=runtime.getMethod("snapshot",ServerPlayer.class,String.class).invoke(instance,p,command.has("game")?command.get("game").getAsString():"");receipt.add("snapshot",(JsonElement)snapshot);}
                    case "stop"->{receipt.addProperty("ok",true);receipt.add("status",status(server));DebugFiles.write(receiptName,receipt);receipt=null;server.halt(false);return;}
                    default->throw new IllegalArgumentException("Unknown server callback "+type);
                }
                receipt.addProperty("ok",true);receipt.add("status",status(server));
            }catch(Throwable failure){receipt.addProperty("ok",false);receipt.addProperty("error",failure.toString());}
            DebugFiles.write(receiptName,receipt);receipt=null;
        }catch(java.io.IOException temporaryFileLock){System.out.println("LOCAL_MC_DEBUG_RETRY_FILE_IO "+temporaryFileLock.getClass().getSimpleName());}
    }
}
