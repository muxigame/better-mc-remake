package muxi.gunpackprobe;

import com.google.gson.*;
import com.mojang.authlib.GameProfile;
import com.tacz.guns.api.TimelessAPI;
import com.tacz.guns.api.entity.IGunOperator;
import com.tacz.guns.api.entity.ShootResult;
import com.tacz.guns.api.item.IGun;
import com.tacz.guns.api.item.builder.GunItemBuilder;
import com.tacz.guns.api.item.builder.AmmoItemBuilder;
import com.tacz.guns.crafting.GunSmithTableRecipe;
import net.minecraft.network.Connection;
import net.minecraft.network.protocol.Packet;
import net.minecraft.network.protocol.PacketFlow;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.*;
import net.minecraft.server.network.*;
import net.minecraft.server.players.PlayerList;
import net.minecraft.world.level.GameType;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.server.ServerStartedEvent;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import java.nio.file.*;
import java.util.*;

/** QA-only fresh loopback world; synthetic real ServerPlayer, no network/client acceptance. */
@Mod("muxi_gunpack_probe")
public final class GunpackNativeQA {
    private final Map<String,Object> result=new LinkedHashMap<>(),guns=new TreeMap<>();
    private final List<Map<String,Object>> shots=new ArrayList<>();
    private List<String> selected;private ServerPlayer player;private MinecraftServer server;
    private int index,frame,delay,ammoBefore,afterShot,reserveBefore;private boolean finished;
    public GunpackNativeQA(){
        if(!Boolean.getBoolean("muxi.gunpack.native.qa"))throw new IllegalStateException("QA-only mod refuses normal startup");
        NeoForge.EVENT_BUS.addListener(this::started);NeoForge.EVENT_BUS.addListener(this::tick);
    }
    private void check(boolean pass,String label){if(!pass)throw new AssertionError(label);}
    @SuppressWarnings("unchecked")
    private void started(ServerStartedEvent event){
        server=event.getServer();try{
            check("127.0.0.1".equals(server.getLocalIp())&&Files.isRegularFile(Path.of("qa-guns.json")),"isolated fixture required");
            selected=new ArrayList<>();for(var id:JsonParser.parseString(Files.readString(Path.of("qa-guns.json"))).getAsJsonArray())selected.add(id.getAsString());
            check(!selected.isEmpty(),"selected list cannot be empty");
            for(var entry:TimelessAPI.getAllCommonGunIndex()){
                var data=entry.getValue().getGunData();var row=new LinkedHashMap<String,Object>();
                row.put("ammo",data.getAmmoId().toString());row.put("ammoPresent",TimelessAPI.getCommonAmmoIndex(data.getAmmoId()).isPresent());
                row.put("gunDataScript",data.getScript()==null?null:data.getScript().toString());
                row.put("parsedLuaTablePresent",entry.getValue().getScript()!=null);guns.put(entry.getKey().toString(),row);
            }
            result.put("guns",guns);var recipes=new TreeMap<String,Object>();
            for(var entry:server.getRecipeManager().getRecipes())if(entry.value() instanceof GunSmithTableRecipe recipe){
                var row=new LinkedHashMap<String,Object>();row.put("outputEmpty",recipe.getOutput().isEmpty());var gun=IGun.getIGunOrNull(recipe.getOutput());
                if(gun!=null)row.put("gunId",gun.getGunId(recipe.getOutput()).toString());
                row.put("materialCounts",recipe.getInputs().stream().map(i->i.getCount()).toList());
                row.put("materialMatchCounts",recipe.getInputs().stream().map(i->i.getIngredient().getItems().length).toList());recipes.put(entry.id().toString(),row);
            }
            result.put("recipeDetails",recipes);result.put("nativeShots",shots);
            for(String name:selected){var gun=TimelessAPI.getCommonGunIndex(ResourceLocation.parse(name)).orElseThrow();
                if(gun.getGunData().getScript()!=null)check(gun.getScript()!=null,"GunData.script failed to resolve: "+name+" "+gun.getGunData().getScript());}
            var profile=new GameProfile(UUID.randomUUID(),"GunNativeQA");player=new ServerPlayer(server,server.overworld(),profile,ClientInformation.createDefault());
            Connection transport=new Connection(PacketFlow.SERVERBOUND);new io.netty.channel.embedded.EmbeddedChannel(transport);
            player.connection=new ServerGamePacketListenerImpl(server,transport,player,CommonListenerCookie.createInitial(profile,false)){
                @Override public void send(Packet<?> packet){}
                @Override public java.net.SocketAddress getRemoteAddress(){return new java.net.InetSocketAddress("127.0.0.1",23458);}
            };
            player.setGameMode(GameType.SURVIVAL);player.setPos(0,70,0);player.setInvulnerable(true);
            var field=PlayerList.class.getDeclaredField("players");field.setAccessible(true);((List<ServerPlayer>)field.get(server.getPlayerList())).add(player);
            var ids=PlayerList.class.getDeclaredField("playersByUUID");ids.setAccessible(true);((Map<UUID,ServerPlayer>)ids.get(server.getPlayerList())).put(player.getUUID(),player);
            server.overworld().addNewPlayer(player);
            for(int x=-1;x<=1;x++)for(int z=-1;z<=1;z++)server.overworld().setChunkForced(x,z,true);
        }catch(Throwable error){finish(error);}
    }
    private void tick(ServerTickEvent.Post event){
        if(finished||player==null)return;try{
            // The fixture has no socket; drive the vanilla player tick normally triggered by its connection.
            player.doTick();
            var id=ResourceLocation.parse(selected.get(index));
            if(frame==0){
                var data=TimelessAPI.getCommonGunIndex(id).orElseThrow().getGunData();
                var stack=GunItemBuilder.create().setId(id).setAmmoCount(3).setAmmoInBarrel(true).setFireMode(data.getFireModeSet().getFirst()).build(player.registryAccess());
                check(!stack.isEmpty(),"native gun build: "+id);player.getInventory().selected=0;player.getInventory().setItem(0,stack);
                player.getInventory().setItem(2,AmmoItemBuilder.create().setId(data.getAmmoId()).setCount(64).build());
                var operator=IGunOperator.fromLivingEntity(player);operator.initialData();operator.draw(player::getMainHandItem);
                delay=Math.max(60,(int)Math.ceil(data.getDrawTime()*20)+20);ammoBefore=IGun.getIGunOrNull(stack).getCurrentAmmoCount(stack);
            }
            if(frame==delay){
                var shot=IGunOperator.fromLivingEntity(player).shoot(()->player.getXRot(),()->player.getYRot());
                var row=new LinkedHashMap<String,Object>();row.put("gun",id.toString());row.put("nativeShoot",shot.toString());row.put("magazineBefore",ammoBefore);shots.add(row);
                check(shot==ShootResult.SUCCESS,"native shoot failed: "+id+" "+shot);
            }
            if(frame==delay+15){
                var stack=player.getMainHandItem();var gun=IGun.getIGunOrNull(stack);int after=gun.getCurrentAmmoCount(stack);
                // Some bolt implementations consume the chamber first; require consumption from total rounds.
                boolean consumed=after<ammoBefore||!gun.hasBulletInBarrel(stack);
                shots.getLast().put("magazineAfter",after);shots.getLast().put("nativeAmmoConsumed",consumed);
                check(consumed,"native shooting did not consume ammunition: "+id);
                afterShot=after;reserveBefore=player.getInventory().getItem(2).getCount();
                IGunOperator.fromLivingEntity(player).bolt();
            }
            if(frame==delay+40)IGunOperator.fromLivingEntity(player).reload();
            if(frame==delay+280){
                var stack=player.getMainHandItem();int reloaded=IGun.getIGunOrNull(stack).getCurrentAmmoCount(stack);
                int reserveAfter=player.getInventory().getItem(2).getCount();
                shots.getLast().put("nativeReloadMagazine",reloaded);shots.getLast().put("reserveBefore",reserveBefore);shots.getLast().put("reserveAfter",reserveAfter);
                check(reloaded>afterShot&&reserveAfter<reserveBefore,"native reload/consume reserve failed: "+id+" "+afterShot+" -> "+reloaded+" reserve "+reserveBefore+" -> "+reserveAfter);
                index++;frame=0;
                if(index==selected.size())finish(null);return;
            }
            frame++;
        }catch(Throwable error){finish(error);}
    }
    private void finish(Throwable error){
        if(finished)return;finished=true;
        try{result.put("success",error==null);result.put("networkClientAcceptance",false);result.put("selectedGuns",selected);
            if(error!=null){result.put("error",error.toString());error.printStackTrace();}
            Files.writeString(Path.of("probe-result.json"),new GsonBuilder().setPrettyPrinting().create().toJson(result));
        }catch(Exception failure){failure.printStackTrace();}server.halt(false);
    }
}
