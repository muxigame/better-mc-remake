package net.muxigame.minigames.horse;

import com.google.gson.*;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.*;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.storage.LevelResource;
import net.muxigame.minigames.*;
import java.util.*;

/** Dynamic minigame module. Player-owned horses are read-only sources for race copies. */
public final class HorseRooms implements GameModule,AutoCloseable {
    public static final String ID="horse_racing";
    private static final org.slf4j.Logger LOG=org.slf4j.LoggerFactory.getLogger("muxi-horse");
    public static final int ASSEMBLY_READY_TICKS=20,COUNTDOWN_TICKS=100,ASSEMBLY_TIMEOUT_TICKS=1800;
    private final MinecraftServer server;
    private final GameRuntime runtime;
    private final HorseHistory history;
    private final HorseMarket.Config market=HorseMarket.Config.preview();
    private final Map<UUID,Room> rooms=new LinkedHashMap<>();
    private final HorseArenaPool arenas;
    private final HorseTrading trading;private final Set<UUID> disconnecting=new HashSet<>();
    private static final class Room {
        final RoomTeam team;
        final HorseCourses.Definition definition;
        final Map<UUID,HorseIdentity> horses=new LinkedHashMap<>();
        final List<HorsePilot> pilots=new ArrayList<>();
        final Map<UUID,Object> settlementConnections=new HashMap<>();
        final Map<UUID,HorsePilot> playerPilots=new LinkedHashMap<>();
        final Map<UUID,HorseScores.Standing> scores=new LinkedHashMap<>();
        final Set<UUID> recorded=new HashSet<>();
        final Set<UUID> npcs=new HashSet<>();
        HorseArenaPool.Lease lease;HorseCourse course;String phase="WAITING";
        boolean skills=true;int laps=3,simulationPauseTick=-1;
        final Map<UUID,HorsePace.Style> styles=new HashMap<>();
        int enemyTier=3,preparingTick,enteredTick,countdownTick,readyTicks;String startIssue="";
        Room(UUID host,HorseCourses.Definition definition){team=new RoomTeam(host);this.definition=definition;}
    }
    public HorseRooms(MinecraftServer server){this.server=server;trading=new HorseTrading(server);runtime=GameRuntime.get(server);arenas=new HorseArenaPool(server,server.getWorldPath(LevelResource.ROOT).resolve("data/muxi-horse-arena-leases-v1.json"));history=new HorseHistory(server.getWorldPath(LevelResource.ROOT).resolve("data/muxi-horse-prototype-history.json"));}
    public String gameId(){return ID;}
    public String title(){return "赛马竞技场";}
    public Collection<RoomTeam> roomTeams(){return rooms.values().stream().map(r->r.team).toList();}
    public boolean roomWaiting(RoomTeam t){Room r=rooms.get(t.session);return r!=null&&r.phase.equals("WAITING");}
    public int roomCapacity(RoomTeam t){return 4;}
    private Room mine(ServerPlayer p){var owner=runtime.memberships.owner(p.getUUID());Room room=owner==null?null:rooms.get(owner.team().session);if(room==null)throw new IllegalArgumentException("请先加入赛马房间");return room;}
    private void host(ServerPlayer p,Room r){if(!r.team.host.equals(p.getUUID()))throw new IllegalArgumentException("仅房主可操作");}
    public void action(ServerPlayer p,String action,String value){
        if(!server.isSameThread()||p.server!=server)throw new IllegalStateException("Server thread required");
        if(!action.equals("leave")){runtime.requireParticipation(p);if(HorseReturn.pending(p))throw new IllegalStateException("请先恢复上次观赛位置");}
        if(action.startsWith("trade")){var membership=runtime.memberships.owner(p.getUUID());var room=membership==null?null:rooms.get(membership.team().session);UUID race=room==null?null:room.team.session;if(action.equals("tradeCancelRental")){if(room==null||!room.phase.equals("WAITING"))throw new IllegalStateException("只能在等待中取消租赁");trading.cancelRental(race,p.getUUID());}else{if(action.equals("tradeQuoteRent")&&(room==null||!room.phase.equals("WAITING")))throw new IllegalStateException("租赁需要等待房间");trading.action(p,action,value,race);}return;}
        switch(action){
            case "create","createMap","createConfigured" -> {
                if(rooms.size()>=4)throw new IllegalStateException("原型房间已满");
                int tier=3;String map=action.equals("createMap")?value:System.getProperty("muxi.horse.course",HorseStadium.ID);
                if(action.equals("createConfigured")){var config=JsonParser.parseString(value).getAsJsonObject();if((!config.keySet().equals(Set.of("mode","map","difficulty"))&&!config.keySet().equals(Set.of("mode","map","difficulty","skills","laps")))||!"obstacle".equals(TerminalUi.text(config,"mode")))throw new IllegalArgumentException("Unknown race mode");map=TerminalUi.text(config,"map");if(!HorseStadium.ID.equals(map))throw new IllegalArgumentException("Unknown stadium");tier=Integer.parseInt(TerminalUi.text(config,"difficulty"));HorseMarket.enemy(market,tier);}
                boolean selectedSkills=true;int selectedLaps=3;
                if(action.equals("createConfigured")){var config=JsonParser.parseString(value).getAsJsonObject();if(config.has("skills")){String selected=TerminalUi.text(config,"skills");if(!Set.of("on","off").contains(selected))throw new IllegalArgumentException("Invalid skills switch");selectedSkills=selected.equals("on");}if(config.has("laps")){selectedLaps=Integer.parseInt(TerminalUi.text(config,"laps"));if(selectedLaps<1||selectedLaps>5)throw new IllegalArgumentException("Invalid lap count");}}
                var definition=HorseCourses.get(map);
                Room r=new Room(p.getUUID(),definition);runtime.join(ID,r.team,p,4);if(runtime.platform.settlementEligible(p))r.settlementConnections.put(p.getUUID(),p.connection);r.horses.put(p.getUUID(),HorseIdentity.read(p));rooms.put(r.team.session,r);
                r.enemyTier=tier;
                r.skills=selectedSkills;r.laps=definition.id().equals(HorseStadium.ID)?selectedLaps:1;
            }
            case "join" -> {
                Room r=rooms.values().stream().filter(v->v.team.shortId().equalsIgnoreCase(value)).findFirst().orElseThrow(()->new IllegalArgumentException("房间不存在"));
                if(!r.phase.equals("WAITING"))throw new IllegalArgumentException("比赛已经开始");
                runtime.social.requireRoomJoin(r.team,p);
                if(!r.team.host.equals(p.getUUID())&&r.team.invites.getOrDefault(p.getUUID(),0L)<=server.getTickCount())throw new IllegalArgumentException("请先接受邀请");
                runtime.join(ID,r.team,p,4);if(runtime.platform.settlementEligible(p))r.settlementConnections.put(p.getUUID(),p.connection);r.horses.put(p.getUUID(),HorseIdentity.read(p));
            }
            case "invite" -> {
                Room r=mine(p);host(p,r);if(!r.phase.equals("WAITING"))throw new IllegalStateException("比赛已经开始");
                UUID target=UUID.fromString(value);if(server.getPlayerList().getPlayer(target)==null)throw new IllegalArgumentException("玩家离线");r.team.invites.put(target,(long)server.getTickCount()+6000);
            }
            case "start" -> {Room r=mine(p);host(p,r);if(!r.phase.equals("WAITING"))throw new IllegalStateException("比赛已开始");if(trading.pendingConfirmation(r.team.session))throw new IllegalStateException("请等待租赁交易回执");if(trading.launch(r.team.session,r.team.members))start(r);else r.phase="RENTAL_STARTING";}
            case "enemy" -> {Room r=mine(p);host(p,r);if(!r.phase.equals("WAITING"))throw new IllegalStateException("Race already started");int tier=Integer.parseInt(value);HorseMarket.enemy(market,tier);r.enemyTier=tier;}
            case "style" -> {var r=mine(p);if(!roomWaiting(r.team))throw new IllegalStateException("Race rules locked");r.styles.put(p.getUUID(),HorsePace.Style.valueOf(value));}
            case "skills" -> {var r=mine(p);host(p,r);if(!roomWaiting(r.team)||!Set.of("on","off").contains(value))throw new IllegalArgumentException("Race rules locked");r.skills=value.equals("on");}
            case "leave" -> {
                HorseReturn.restore(p);var owner=runtime.memberships.owner(p.getUUID());Room r=owner==null?null:rooms.get(owner.team().session);
                if(r!=null){LOG.info("Race session={} player={} leaving phase={} reason={}",r.team.session,p.getUUID(),r.phase,disconnecting.contains(p.getUUID())?"disconnect":"voluntary");trading.end(r.team.session,p.getUUID(),disconnecting.remove(p.getUUID())?"disconnect":"voluntary",false);var actor=r.playerPilots.get(p.getUUID());if(actor!=null)actor.close();trading.released(r.team.session);runtime.memberships.leave(r.team,p.getUUID());if(r.team.host.equals(p.getUUID())||r.team.members.isEmpty()||Set.of("PREPARING","BUILDING","ASSEMBLING","COUNTDOWN","START_FAILED").contains(r.phase))closeRoom(r,Set.of("PREPARING","BUILDING","ASSEMBLING","COUNTDOWN","START_FAILED").contains(r.phase)?"participant_left_before_start":"room_cancelled");}
            }
            default -> throw new IllegalArgumentException("未知原型操作");
        }
    }
    private void start(Room r){
        if(!Set.of("WAITING","RENTAL_STARTING").contains(r.phase))throw new IllegalStateException("比赛已经开始");
        for(UUID id:r.team.members){var rental=trading.identity(r.team.session,id);if(rental!=null)r.horses.put(id,rental);}
        var dimension=r.definition.id().equals(HorseStadium.ID)?HorseCourse.STADIUM_DIMENSION:HorseCourse.DIMENSION;
        var level=server.getLevel(dimension);if(level==null)throw new IllegalStateException("测试维度未加载");
        r.lease=arenas.reserve(r.team.session,dimension);r.course=new HorseCourse(r.lease.slot*256,0,r.definition);r.phase="PREPARING";r.preparingTick=server.getTickCount();
    }
    private void enterRace(Room r){
        var level=server.getLevel(r.course.dimension());
        try{
            arenas.activate(r.lease);
            int slot=0;for(UUID id:r.team.members){var pilot=entrant(r,r.horses.get(id),slot++);pilot.configure(r.laps,r.styles.getOrDefault(id,HorsePace.Style.FRONT),r.skills);r.playerPilots.put(id,pilot);}
            int count=r.course.stadium()?HorseRaceGrid.npcCount(r.team.members.size()):1;
            var enemy=HorseMarket.enemy(market,r.enemyTier).identity();
            for(int npc=0;npc<count;npc++){
                // Stable named NPC identities keep separate history records across repeat races.
                var identity=new HorseIdentity(UUID.nameUUIDFromBytes(("muxi-horse-enemy:"+r.enemyTier+":"+npc).getBytes(java.nio.charset.StandardCharsets.UTF_8)),enemy.name()+" · 赛方 "+(npc+1),enemy.speed(),enemy.jump(),enemy.health(),false);
                var pilot=entrant(r,identity,slot++);r.npcs.add(pilot.horse.getUUID());
            }
            for(UUID id:r.team.members){var player=server.getPlayerList().getPlayer(id);if(player==null)throw new IllegalStateException("成员离线");arenas.participant(r.lease,player);HorseReturn.capture(player);player.stopRiding();var podium=r.course.podium();if(!player.teleportTo(level,podium.x,podium.y,podium.z,Set.of(),0,r.course.stadium()?15:30)||player.level()!=level)throw new IllegalStateException("Race entry teleport was denied");player.setGameMode(GameType.SPECTATOR);EntryReadyNetwork.begin(player,r.team.session,2);}
            r.enteredTick=server.getTickCount();r.phase="ASSEMBLING";r.startIssue="entry_pending";
            LOG.info("Race session={} phase=ASSEMBLING players={} npcs={} racers={} arena={} courseVersion={}",r.team.session,r.team.members.size(),r.npcs.size(),r.pilots.size(),r.lease.slot,r.course.version);
        }catch(RuntimeException failure){for(UUID id:r.team.members){var player=server.getPlayerList().getPlayer(id);if(player!=null)try{HorseReturn.restore(player);}catch(RuntimeException recovery){failure.addSuppressed(recovery);}}r.pilots.forEach(HorsePilot::close);r.pilots.clear();r.course.unload(level);arenas.release(r.lease);r.phase="BUILD_FAILED";throw failure;}
    }
    private HorsePilot entrant(Room r,HorseIdentity identity,int slot){
        var level=server.getLevel(r.course.dimension());var offer=UmamusumeCatalog.offers().get(slot%6).getAsJsonObject();UmamusumeCatalog.verifyModelFiles(java.nio.file.Path.of(System.getProperty("muxi.horse.maid.modelRoot","config/yes_steve_model/custom")),offer);String model=offer.get("modelId").getAsString(),texture=offer.get("texture").getAsString();
        var pilot=r.course.stadium()?new HorsePilot(level,r.course,identity,model,texture,slot):new HorsePilot(level,r.course,identity,model,texture);
        if(!r.course.stadium())pilot.lane(slot<r.team.members.size()?slot*.8:2.4);pilot.rider.setCustomName(net.minecraft.network.chat.Component.literal(offer.get("name").getAsString()+" · 骑手"));pilot.configure(r.laps,HorsePace.Style.values()[slot%4],r.skills);pilot.hold();r.pilots.add(pilot);arenas.track(r.lease,pilot.horse);arenas.track(r.lease,pilot.rider);return pilot;
    }
    private String assemblyIssue(Room r){
        if(!arenas.ready(r.lease))return "course_chunks_not_ticking";
        if(r.pilots.isEmpty()||r.course.stadium()&&r.pilots.size()!=HorseRaceGrid.FIELD_SIZE)return "incomplete_field";
        for(UUID id:r.team.members){var p=server.getPlayerList().getPlayer(id);
            if(p==null||!p.connection.isAcceptingMessages())return "player_connection_pending:"+id;
            if(!p.isAlive()||!p.level().dimension().equals(r.course.dimension()))return "player_entry_pending:"+id;
            if(p.isChangingDimension())return "player_dimension_ack_pending:"+id;
            if(!EntryReadyNetwork.ready(p,r.team.session))return "client_chunks_pending:"+id;
        }
        for(var pilot:r.pilots){String issue=pilot.readiness();if(!issue.isEmpty())return issue+":"+pilot.horse.getUUID();}
        return "";
    }
    private void stageStart(Room r){
        r.pilots.forEach(HorsePilot::hold);String issue=assemblyIssue(r);int now=server.getTickCount();
        if(!issue.isEmpty()){
            r.readyTicks=0;if(!issue.equals(r.startIssue)){r.startIssue=issue;LOG.info("Race session={} phase={} waiting={}",r.team.session,r.phase,issue);}
            if(r.phase.equals("COUNTDOWN")){r.phase="ASSEMBLING";LOG.warn("Race session={} countdown reset reason={}",r.team.session,issue);}
        }else if(r.phase.equals("ASSEMBLING")){
            r.startIssue="";if(++r.readyTicks>=ASSEMBLY_READY_TICKS&&now-r.enteredTick>=40){r.phase="COUNTDOWN";r.countdownTick=now;LOG.info("Race session={} phase=COUNTDOWN racers={} countdownTicks={}",r.team.session,r.pilots.size(),COUNTDOWN_TICKS);}
        }else if(now-r.countdownTick>=COUNTDOWN_TICKS){
            EntryReadyNetwork.clear(r.team.session);r.pilots.forEach(HorsePilot::releaseStart);trading.running(r.team.session,r.team.members);r.phase="RUNNING";
            LOG.info("Race session={} phase=RUNNING racers={} players={} npcs={}",r.team.session,r.pilots.size(),r.team.members.size(),r.npcs.size());
            for(UUID id:r.team.members){var p=server.getPlayerList().getPlayer(id);if(p!=null)p.displayClientMessage(net.minecraft.network.chat.Component.literal("赛马 · 开跑！"),true);}
            return;
        }
        if(r.phase.equals("COUNTDOWN")&&now%20==0)for(UUID id:r.team.members){var p=server.getPlayerList().getPlayer(id);if(p!=null)p.displayClientMessage(net.minecraft.network.chat.Component.literal("赛马 · "+Math.max(1,(COUNTDOWN_TICKS-(now-r.countdownTick)+19)/20)+" 秒后开跑"),true);}
        if(r.phase.equals("ASSEMBLING")&&now-r.enteredTick>=ASSEMBLY_TIMEOUT_TICKS){r.phase="START_FAILED";EntryReadyNetwork.clear(r.team.session);LOG.error("Race session={} start failed reason={} racers={}; original return remains available through leave",r.team.session,r.startIssue,r.pilots.size());}
    }
    public void tick(){
        trading.tick();arenas.tick();
        for(Room r:List.copyOf(rooms.values())){
          if(r.phase.equals("RENTAL_STARTING")&&trading.launch(r.team.session,r.team.members))start(r);
          if(Set.of("PREPARING","BUILDING").contains(r.phase)&&server.getTickCount()-r.preparingTick>=3600){r.phase="BUILD_FAILED";r.course.unload(server.getLevel(r.course.dimension()));arenas.release(r.lease);LOG.error("Race session={} preparation timeout",r.team.session);}
          if(r.phase.equals("BUILD_FAILED"))trading.end(r.team.session,null,"build_failure",true);
          if(r.phase.equals("PREPARING")&&arenas.vacant(r.lease)){try{r.course.build(server.getLevel(r.course.dimension()),false);r.phase="BUILDING";}catch(RuntimeException failure){r.phase="BUILD_FAILED";arenas.release(r.lease);throw failure;}}
          if(r.phase.equals("BUILDING")){
            try{if(arenas.vacant(r.lease)&&r.course.advanceBuild())enterRace(r);}catch(RuntimeException failure){r.phase="BUILD_FAILED";arenas.release(r.lease);r.course.unload(server.getLevel(r.course.dimension()));org.slf4j.LoggerFactory.getLogger("muxi-horse").error("Private stadium build failed",failure);}
          }
          if(r.phase.equals("ASSEMBLING")||r.phase.equals("COUNTDOWN"))stageStart(r);
          if(r.phase.equals("RUNNING")){
            if(!arenas.ready(r.lease)){
                if(r.simulationPauseTick<0){r.simulationPauseTick=server.getTickCount();r.pilots.forEach(HorsePilot::hold);}
                r.startIssue="full_course_simulation_pending";
                if(server.getTickCount()-r.simulationPauseTick>=600){r.phase="RACE_FAILED";r.startIssue="full_course_simulation_timeout";}
                continue;
            }
            if(r.simulationPauseTick>=0){r.pilots.stream().filter(v->!v.finished&&!v.dnf).forEach(HorsePilot::releaseStart);r.simulationPauseTick=-1;r.startIssue="";}

            if(server.getTickCount()%100==0)arenas.checkpoint(r.lease,r.pilots.stream().flatMap(v->java.util.stream.Stream.<net.minecraft.world.entity.Entity>of(v.horse,v.rider)).toList());
            var order=r.pilots.stream().sorted(Comparator.comparingInt((HorsePilot v)->v.gate).reversed().thenComparingDouble(v->v.horse.position().distanceToSqr(v.course.pointForLane(Math.min(v.gate,v.course.gates.size()*v.laps-1)%v.course.gates.size(),HorseRaceGrid.lane(r.pilots.indexOf(v)))))).toList();
            for(int i=0;i<order.size();i++){order.get(i).rank=i+1;order.get(i).runners=order.size();}
            r.pilots.forEach(v->v.traffic(r.pilots));
            for(var pilot:r.pilots){pilot.tick();if(pilot.finished&&!r.recorded.contains(pilot.horse.getUUID())){
                history.record(new HorseHistory.Result(r.team.session,pilot.identity.horseId(),pilot.identity.name(),r.course.id,r.course.version+":laps="+r.laps+":skills="+r.skills,pilot.resultTicks(),pilot.recoveries,r.npcs.contains(pilot.horse.getUUID())));r.recorded.add(pilot.horse.getUUID());
            }}
            if(r.pilots.stream().allMatch(p->p.finished||p.dnf)){
                for(var standing:HorseScores.calculate(r.pilots.stream().map(p->new HorseScores.Run(p.horse.getUUID(),p.resultTicks(),p.finished)).toList()))r.scores.put(standing.horse(),standing);
                if(r.pilots.stream().noneMatch(p->p.finished)&&r.pilots.stream().anyMatch(p->!p.dnfReason.startsWith("recovery_limit:")&&!p.dnfReason.equals("race_timeout"))){r.pilots.forEach(HorsePilot::hold);r.phase="RACE_FAILED";r.startIssue="all_racers_dnf";LOG.error("Race session={} all racers DNF reasons={}; keeping failure visible until explicit leave",r.team.session,r.pilots.stream().map(p->p.horse.getUUID()+":"+p.dnfReason).toList());}
                else{r.phase="RETURNING";LOG.info("Race session={} phase=RETURNING finishers={} dnfs={} reasons={}",r.team.session,r.pilots.stream().filter(p->p.finished).count(),r.pilots.stream().filter(p->p.dnf).count(),r.pilots.stream().filter(p->p.dnf).map(p->p.horse.getUUID()+":"+p.dnfReason).toList());}
            }
          }
          if(r.phase.equals("RETURNING")){
                trading.end(r.team.session,null,"completed",false);
                for(UUID id:r.team.members){var player=server.getPlayerList().getPlayer(id);if(player!=null){var pilot=r.playerPilots.get(id);var standing=pilot==null?null:r.scores.get(pilot.horse.getUUID());if(standing!=null)runtime.complete(ID,r.team,player,standing.win(),standing.score(),r.enemyTier,standing.seconds());HorseReturn.restore(player);}}
                r.pilots.forEach(HorsePilot::close);r.course.unload(server.getLevel(r.course.dimension()));
                arenas.checkpoint(r.lease,r.pilots.stream().flatMap(v->java.util.stream.Stream.<net.minecraft.world.entity.Entity>of(v.horse,v.rider)).toList());arenas.release(r.lease);trading.released(r.team.session);r.phase="CLEANING";
          }
          if(r.phase.equals("CLEANING")&&arenas.released(r.lease))r.phase="FINISHED";
        }
    }
    public void disconnected(ServerPlayer p){disconnecting.add(p.getUUID());}
    public boolean denyLoaded(net.minecraft.world.entity.Entity entity){return trading.denyLoaded(entity);}
    public HorseTrading trading(){return trading;}
    private void closeRoom(Room r,String reason){
        EntryReadyNetwork.clear(r.team.session);
        LOG.info("Race session={} closing phase={} reason={} racers={}",r.team.session,r.phase,reason,r.pilots.size());
        trading.end(r.team.session,null,reason,false);
        for(UUID id:List.copyOf(r.team.members)){var p=server.getPlayerList().getPlayer(id);if(p!=null&&!reason.equals("server_failure"))HorseReturn.restore(p);}
        // ServerStopping may run after other mods unload their dimension-change listeners.
        // Keep the already-persisted return marker for login recovery instead of teleporting.
        if(r.lease!=null)arenas.checkpoint(r.lease,r.pilots.stream().flatMap(v->java.util.stream.Stream.<net.minecraft.world.entity.Entity>of(v.horse,v.rider)).toList());r.pilots.forEach(HorsePilot::close);arenas.release(r.lease);if(r.course!=null)r.course.unload(server.getLevel(r.course.dimension()));runtime.memberships.close(r.team);rooms.remove(r.team.session);trading.released(r.team.session);
    }
    public void close(){for(Room r:List.copyOf(rooms.values()))closeRoom(r,"server_failure");arenas.shutdown();trading.close();}
    public JsonObject snapshot(ServerPlayer p){
        JsonObject out=new JsonObject();JsonArray list=new JsonArray();for(Room r:rooms.values()){
            JsonObject row=new JsonObject();if(r.lease!=null){row.addProperty("arenaSlot",r.lease.slot);row.addProperty("arenaGeneration",r.lease.generation.toString());}row.addProperty("id",r.team.shortId());row.addProperty("phase",r.phase);row.addProperty("members",r.team.members.size());
            row.addProperty("mine",r.team.members.contains(p.getUUID()));row.addProperty("host",r.team.host.toString());row.addProperty("session",r.team.session.toString());row.addProperty("lobbyWaiting",roomWaiting(r.team));row.addProperty("mode","obstacle");row.addProperty("map",r.definition.id());row.addProperty("settlementEnabled",true);row.addProperty("settlementEligible",r.settlementConnections.get(p.getUUID())==p.connection);row.addProperty("firstPlacePoints",HorseScores.FIRST_POINTS);
            row.addProperty("course",r.definition.id());row.addProperty("version",r.definition.version());row.addProperty("theme",r.definition.theme());
            row.addProperty("laps",r.laps);row.addProperty("skills",r.skills);row.addProperty("enemyTier",r.enemyTier);row.addProperty("fieldSize",r.definition.id().equals(HorseStadium.ID)?HorseRaceGrid.FIELD_SIZE:r.team.members.size()+1);row.addProperty("npcCount",r.pilots.isEmpty()?(r.definition.id().equals(HorseStadium.ID)?HorseRaceGrid.npcCount(r.team.members.size()):1):r.npcs.size());row.addProperty("startIssue",r.startIssue);row.addProperty("countdownSeconds",r.phase.equals("COUNTDOWN")?Math.max(0,(COUNTDOWN_TICKS-(server.getTickCount()-r.countdownTick)+19)/20):0);row.addProperty("trackWidth",r.definition.id().equals(HorseStadium.ID)?HorseStadium.TRACK_HALF_WIDTH*2:0);
            if(r.course!=null&&r.course.builder()!=null){var build=r.course.builder();row.addProperty("buildingPlaced",build.placed());row.addProperty("buildingTotal",build.total());row.addProperty("buildMaxApplyMillis",build.maxApplyNanos/1e6);row.addProperty("buildTicks",build.applyTicks);}
            JsonArray horses=new JsonArray();for(var h:r.horses.values()){JsonObject horse=new JsonObject();horse.addProperty("uuid",h.horseId().toString());horse.addProperty("name",h.name());horse.addProperty("speed",h.speed());horse.addProperty("jump",h.jump());horse.addProperty("health",h.health());horse.addProperty("sourceHorse",h.sourceHorse());horses.add(horse);}row.add("horses",horses);
            JsonArray results=new JsonArray();for(var pilot:r.pilots){JsonObject result=new JsonObject();result.addProperty("name",pilot.identity.name());result.addProperty("horseUuid",pilot.horse.getUUID().toString());result.addProperty("npc",r.npcs.contains(pilot.horse.getUUID()));var standing=r.scores.get(pilot.horse.getUUID());if(standing!=null){result.addProperty("rank",standing.rank());result.addProperty("score",standing.score());result.addProperty("seconds",standing.seconds());result.addProperty("win",standing.win());}result.addProperty("ticks",pilot.resultTicks());result.addProperty("rawTicks",pilot.ticks);result.addProperty("nativeTick",pilot.horse.tickCount);result.addProperty("nativeTickWaits",pilot.nativeTickWaits);result.addProperty("trafficYieldTicks",pilot.trafficYieldTicks);result.addProperty("lap",Math.min(pilot.laps,1+pilot.gate/pilot.course.gates.size()));result.addProperty("style",pilot.pace.style.name());result.addProperty("stamina",pilot.pace.stamina);result.addProperty("pace",pilot.pace.pace);result.addProperty("skillFeedback",pilot.pace.feedback);result.addProperty("recoverySkillUses",pilot.pace.recoveryUses);result.addProperty("surgeSkillUses",pilot.pace.surgeUses);result.addProperty("penaltyTicks",pilot.penaltyTicks);result.add("recoveryEvents",new Gson().toJsonTree(pilot.recoveryEvents()));result.addProperty("finished",pilot.finished);result.addProperty("dnf",pilot.dnf);result.addProperty("dnfReason",pilot.dnfReason);result.addProperty("gate",pilot.gate);result.addProperty("horseAlive",pilot.horse.isAlive());result.addProperty("riderAlive",pilot.rider.isAlive());result.addProperty("riderMounted",pilot.rider.getVehicle()==pilot.horse);result.addProperty("horseY",pilot.horse.getY());result.addProperty("recoveries",pilot.recoveries);results.add(result);}row.add("results",results);list.add(row);
        }out.add("rooms",list);out.add("arenaPool",arenas.snapshot());var owner=runtime.memberships.owner(p.getUUID());var room=owner==null?null:rooms.get(owner.team().session);var selected=room==null?HorseCourses.get(System.getProperty("muxi.horse.course",HorseStadium.ID)):room.definition;
        out.add("courses",new Gson().toJsonTree(HorseCourses.list()));out.addProperty("historyCourse",selected.id());out.addProperty("historyVersion",selected.version()+":laps="+(room==null?3:room.laps)+":skills="+(room==null||room.skills));out.add("top10",new Gson().toJsonTree(history.top(selected.id(),selected.version()+":laps="+(room==null?3:room.laps)+":skills="+(room==null||room.skills))));out.addProperty("developmentAssetsReadOnly",true);out.addProperty("self",p.getUUID().toString());
        out.add("market",trading.previewMarket(runtime.platform.pointBalance(p)));out.add("trading",trading.snapshot(p));return out;
    }
    private static String phaseLabel(String phase){return switch(phase){case "WAITING"->"等待组队";case "RENTAL_STARTING"->"正在确认租马";case "PREPARING","BUILDING"->"准备赛场";case "ASSEMBLING"->"参赛者集合";case "COUNTDOWN"->"开始倒计时";case "RUNNING"->"比赛中";case "CLEANING"->"正在结算";case "FINISHED"->"已结束";case "START_FAILED","RACE_FAILED","BUILD_FAILED"->"本场暂停";default->"正在处理";};}
    public JsonObject terminalUi(ServerPlayer p,JsonObject state){
        var ui=TerminalUi.model();var page=TerminalUi.page(ui,"lobby","赛马竞技场");
        var owner=runtime.memberships.owner(p.getUUID());boolean mine=owner!=null&&rooms.containsKey(owner.team().session);Room current=mine?rooms.get(owner.team().session):null;
        var create=TerminalUi.section(page,"创建比赛","每场 8 匹马，最多 4 位玩家，空位由 NPC 补齐。八条独立路线起跑，16 格宽跑道；进场集合后倒计时 5 秒开跑。原版马与女仆骑手，同场越过围栏、跳墙与土丘。自养马保留原属性，未骑马时使用默认比赛马。当前比赛使用比赛副本，原马留在原世界。冠军 10 分；其他名次按排名 65%、相对冠军用时 35% 计分，至少 1 分。平台账号使用现有积分结算。");create.addProperty("role","create");
        var modes=new JsonArray();modes.add(TerminalUi.option("obstacle","障碍赛马"));TerminalUi.field(create,"mode","玩法",modes,"obstacle");
        var maps=new JsonArray();maps.add(TerminalUi.option(HorseStadium.ID,"木溪椭圆竞技场"));TerminalUi.field(create,"map","赛场",maps,HorseStadium.ID);
        var levels=new JsonArray();for(int i=1;i<=5;i++)levels.add(TerminalUi.option(Integer.toString(i),"强敌 "+i+" 档"));TerminalUi.field(create,"difficulty","对手",levels,"3");
        var button=TerminalUi.action(create,"创建等待房间","createConfigured","",owner==null&&!HorseReturn.pending(p));button.addProperty("jsonFields",true);var skillOptions=new JsonArray();skillOptions.add(TerminalUi.option("on","开启技能"));skillOptions.add(TerminalUi.option("off","关闭技能"));TerminalUi.field(create,"skills","本场技能",skillOptions,"on");
        var lapOptions=new JsonArray();for(int i=1;i<=5;i++)lapOptions.add(TerminalUi.option(Integer.toString(i),i+" 圈"));TerminalUi.field(create,"laps","圈数",lapOptions,"3");
        button.addProperty("fields","mode,map,difficulty,skills,laps");
        var list=TerminalUi.section(page,"等待中的房间","接受房主邀请后加入；比赛开始前留在原位置。");list.addProperty("role","rooms");
        for(var r:rooms.values()){var card=TerminalUi.card(list,"房间 "+r.team.shortId(),r.definition.title()+" · "+phaseLabel(r.phase)+" · "+r.team.members.size()+" / 4 人");TerminalUi.action(card,"加入房间","join",r.team.shortId(),owner==null&&roomWaiting(r.team)&&r.team.invites.getOrDefault(p.getUUID(),0L)>server.getTickCount());}
        if(mine){
            var room=TerminalUi.section(page,"我的赛马房间",current.definition.title()+" · "+phaseLabel(current.phase)+" · "+current.team.members.size()+" / 4 人。开始后在主席台观赛，结束时返回原位置与原马。");room.addProperty("role","room");
            if(current.phase.equals("PREPARING"))TerminalUi.card(room,"正在核对赛场","等待区块和实体完整加载；恢复未完成或有其他实体占用时不会复用。");
            if(current.phase.equals("ASSEMBLING"))TerminalUi.card(room,"正在集合参赛马",current.pilots.size()+" 匹马已入场；等待玩家进场确认和骑手就位。原马保留在原世界。");
            if(current.phase.equals("COUNTDOWN"))TerminalUi.card(room,"即将开跑",Math.max(0,(COUNTDOWN_TICKS-(server.getTickCount()-current.countdownTick)+19)/20)+" 秒 · 8 匹马已就位");
            if(current.phase.equals("START_FAILED")||current.phase.equals("RACE_FAILED"))TerminalUi.card(room,"本场未能正常开跑或完成","参赛状态异常，本场已暂停；请退出并恢复后重新建房。");
            if(current.phase.equals("CLEANING"))TerminalUi.card(room,"正在释放赛场","原马已归还，确认场地无玩家或比赛实体后显示最终成绩。");
            if(current.phase.equals("BUILDING")){var builder=current.course.builder();TerminalUi.card(room,"正在准备赛场",builder.placed()+" / "+builder.total()+" 方块");}
            if(current.phase.equals("BUILD_FAILED"))TerminalUi.card(room,"赛场准备失败","请退出并重新创建房间。");
            for(var style:HorsePace.Style.values())TerminalUi.action(room,"跑法 "+style.label(),"style",style.name(),roomWaiting(current.team));
            if(current.team.host.equals(p.getUUID()))TerminalUi.action(room,current.skills?"关闭技能":"开启技能","skills",current.skills?"off":"on",roomWaiting(current.team));
            TerminalUi.card(room,"比赛规则",current.laps+" 圈 · 技能"+(current.skills?"开启":"关闭")+" · 跑法 "+current.styles.getOrDefault(p.getUUID(),HorsePace.Style.FRONT).label());
            TerminalUi.action(room,"开始比赛","start","",roomWaiting(current.team)&&current.team.host.equals(p.getUUID()));
            if(current.phase.equals("RUNNING"))for(var runner:current.pilots){String skill=switch(runner.pace.feedback){case "rhythm_recovery"->"节奏恢复";case "curve_exit"->"出弯加速";case "final_surge"->"最后冲刺";default->"";};TerminalUi.card(room,runner.identity.name(),"第 "+Math.min(runner.laps,1+runner.gate/runner.course.gates.size())+" / "+runner.laps+" 圈 · 体力 "+(int)(runner.pace.stamina*100)+"% · "+skill);}
            TerminalUi.action(room,"退出并恢复","leave","",true).addProperty("safe",true);
            if(roomWaiting(current.team)&&current.team.host.equals(p.getUUID())){
                var settings=TerminalUi.section(page,"房间设置","对手档位决定 NPC 使用的名马属性。");settings.addProperty("role","settings");TerminalUi.field(settings,"roomDifficulty","对手难度",levels,Integer.toString(current.enemyTier));TerminalUi.action(settings,"保存难度","enemy","{roomDifficulty}",true);
                var invite=TerminalUi.section(page,"邀请在线玩家","可从统一好友和在线邀请入口邀请，也可选择下方在线玩家。");invite.addProperty("role","invite");
                for(var target:server.getPlayerList().getPlayers())if(!target.getUUID().equals(p.getUUID())){var card=TerminalUi.card(invite,target.getGameProfile().getName(),"在线");TerminalUi.action(card,"邀请","invite",target.getUUID().toString(),runtime.memberships.owner(target.getUUID())==null);}
            }
            if(current.phase.equals("FINISHED")){
                var result=TerminalUi.section(page,"比赛成绩","玩家马和 NPC 都计时。冠军 10 分，其他名次综合排名和用时至少 1 分。NPC 不领取积分；马匹交易尚未开放。");result.addProperty("role","result");
                var ordered=current.pilots.stream().sorted(Comparator.comparingInt(v->current.scores.get(v.horse.getUUID()).rank())).toList();
                for(var pilot:ordered){var standing=current.scores.get(pilot.horse.getUUID());TerminalUi.card(result,standing.rank()+" · "+pilot.identity.name(),(pilot.dnf?"未完成":String.format(Locale.ROOT,"%.2f 秒 · 恢复 %d 次",pilot.resultTicks()/20.0,pilot.recoveries))+" · "+standing.score()+" 分"+(current.npcs.contains(pilot.horse.getUUID())?" · NPC":""));}
                if(current.settlementConnections.get(p.getUUID())!=p.connection)TerminalUi.card(result,"积分说明","本场未绑定可结算的平台账号，成绩已记录；不产生兑换积分。");
            }
        }else if(HorseReturn.pending(p)){var recovery=TerminalUi.section(page,"恢复上次观赛位置","恢复完成后可以再次参加小游戏。");recovery.addProperty("role","recovery");TerminalUi.action(recovery,"恢复并退出","leave","",true).addProperty("safe",true);}
        var top=TerminalUi.section(page,"赛场历史前十",HorseCourses.get(state.get("historyCourse").getAsString()).title()+" · 当前赛道版本纪录");
        for(var item:state.getAsJsonArray("top10")){var result=item.getAsJsonObject();TerminalUi.card(top,result.get("horseName").getAsString(),String.format(Locale.ROOT,"%.2f 秒",result.get("ticks").getAsInt()/20.0));}
        var shopPage=TerminalUi.page(ui,"shop","名马预览");var shop=TerminalUi.section(shopPage,"名马属性","参考现实赛马命名，档位为游戏设定。属性和报价固定可见，目前仅供预览；交易尚未开放。");
        for(var offer:HorseMarket.catalog(market))TerminalUi.card(shop,offer.name()+" · "+offer.tier()+" 档",String.join(" / ",offer.traits())+" · 租 "+offer.rent()+" / 购 "+offer.purchase()+" 积分");trading.ui(p,ui,current==null?null:current.team.session,current!=null&&current.phase.equals("WAITING"));return ui;
    }
}
