package net.muxigame.minigames.horse;

import com.mojang.brigadier.arguments.StringArgumentType;
import net.minecraft.commands.Commands;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.RegisterCommandsEvent;
import net.neoforged.neoforge.event.entity.player.PlayerEvent;
import net.neoforged.neoforge.event.entity.EntityJoinLevelEvent;
import net.neoforged.neoforge.event.server.*;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import net.neoforged.bus.api.EventPriority;
import net.muxigame.minigames.*;
import java.util.*;

/** Bootstrap isolated from terminal layout, Core and production configuration. */
public final class HorseRacing {
    private static final Map<MinecraftServer,HorseRooms> ACTIVE=new IdentityHashMap<>();
    private static final Map<MinecraftServer,Set<UUID>> LOGIN_RETURNS=new IdentityHashMap<>();
    private HorseRacing(){}
    public static void install(){
        var bus=NeoForge.EVENT_BUS;
        bus.addListener(EventPriority.HIGHEST,(PlayerEvent.PlayerLoggedOutEvent e)->{if(e.getEntity() instanceof ServerPlayer p){var game=ACTIVE.get(p.server);if(game!=null)game.disconnected(p);}});
        bus.addListener(EventPriority.HIGHEST,(net.neoforged.neoforge.event.entity.player.PlayerInteractEvent.EntityInteract e)->{if(HorseTrading.locked(e.getTarget()))e.setCanceled(true);});
        bus.addListener(EventPriority.HIGHEST,(net.neoforged.neoforge.event.entity.EntityMountEvent e)->{if(e.isMounting()&&HorseTrading.locked(e.getEntityBeingMounted()))e.setCanceled(true);});
        // Restoration must remain available even if a previously installed rider pack is absent.
        bus.addListener(EventPriority.HIGHEST,(PlayerEvent.PlayerLoggedInEvent e)->{if(e.getEntity() instanceof ServerPlayer p&&HorseReturn.pending(p)){
            try{HorseReturn.restore(p);}catch(RuntimeException waiting){LOGIN_RETURNS.computeIfAbsent(p.server,s->new HashSet<>()).add(p.getUUID());}
        }});
        bus.addListener((ServerTickEvent.Pre e)->{
            var server=e.getServer();for(var player:server.getPlayerList().getPlayers())if(server.getTickCount()%100==0)HorseReturn.keepSource(player);
            var pending=LOGIN_RETURNS.get(server);if(pending!=null)for(UUID id:List.copyOf(pending)){
                var player=server.getPlayerList().getPlayer(id);if(player==null||!HorseReturn.pending(player)){pending.remove(id);continue;}
                try{HorseReturn.restore(player);pending.remove(id);}catch(RuntimeException waiting){/* Durable marker remains; do not abort login while entity storage loads. */}
            }
        });
        bus.addListener((ServerStoppedEvent e)->LOGIN_RETURNS.remove(e.getServer()));
        bus.addListener((PlayerEvent.Clone e)->{if(e.getOriginal().getPersistentData().contains(HorseReturn.KEY))e.getEntity().getPersistentData().put(HorseReturn.KEY,e.getOriginal().getPersistentData().get(HorseReturn.KEY).copy());});
        bus.addListener((EntityJoinLevelEvent e)->{if(e.loadedFromDisk()&&!e.getLevel().isClientSide()){var game=ACTIVE.get(e.getLevel().getServer());if(game!=null&&game.denyLoaded(e.getEntity())){e.setCanceled(true);return;}}if(e.loadedFromDisk()&&!e.getLevel().isClientSide()&&(e.getLevel().dimension().equals(HorseCourse.DIMENSION)||e.getLevel().dimension().equals(HorseCourse.STADIUM_DIMENSION))&&e.getEntity().getTags().contains(HorsePilot.MARKER))e.setCanceled(true);});
        if(!net.neoforged.fml.ModList.get().isLoaded("touhou_little_maid")||!net.neoforged.fml.ModList.get().isLoaded("yes_steve_model"))return;
        GameAreas.register(HorseRooms.ID,HorseCourse.DIMENSION);GameAreas.register(HorseRooms.ID,HorseCourse.STADIUM_DIMENSION);
        bus.addListener((ServerStartedEvent e)->{var game=new HorseRooms(e.getServer());ACTIVE.put(e.getServer(),game);GameRuntime.get(e.getServer()).register(game);});
        bus.addListener((ServerTickEvent.Pre e)->{var game=ACTIVE.get(e.getServer());if(game!=null)try{game.tick();}catch(RuntimeException failure){if(e.getServer().getTickCount()%20==0)org.slf4j.LoggerFactory.getLogger("muxi-horse-prototype").error("Race result or return remains pending",failure);}});
        bus.addListener((ServerStoppingEvent e)->{var game=ACTIVE.remove(e.getServer());if(game!=null)game.close();});
        bus.addListener((RegisterCommandsEvent e)->{
            var root=Commands.literal("horseproto");for(String action:List.of("create","start","leave"))root.then(Commands.literal(action).executes(c->{var p=c.getSource().getPlayerOrException();GameRuntime.get(p.server).request(p,HorseRooms.ID,action,"");return 1;}));
            for(String action:List.of("join","invite","createMap","enemy"))root.then(Commands.literal(action).then(Commands.argument("value",StringArgumentType.word()).executes(c->{var p=c.getSource().getPlayerOrException();GameRuntime.get(p.server).request(p,HorseRooms.ID,action,StringArgumentType.getString(c,"value"));return 1;})));
            e.getDispatcher().register(root);
        });
    }
}
