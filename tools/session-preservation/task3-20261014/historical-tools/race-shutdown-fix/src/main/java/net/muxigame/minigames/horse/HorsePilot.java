package net.muxigame.minigames.horse;

import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.*;
import net.minecraft.world.entity.animal.horse.Horse;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.item.*;
import net.minecraft.sounds.SoundSource;
import net.minecraft.util.Mth;
import net.minecraft.world.phys.Vec3;
import net.muxigame.minigames.horse.mixin.HorseControlAccess;

/** Server-directed vanilla Horse physics. Only freshly generated prototype horses can enter. */
public final class HorsePilot {
    public static final String MARKER="muxi_horse_prototype";
    public final Horse horse;
    public final Mob rider;
    public final HorseIdentity identity;
    public final HorseCourse course;
    private final boolean generated;
    public record Recovery(int tick,String reason,int gate,double x,double y,double z,
                           int returnGate,double returnX,double returnY,double returnZ,boolean restored) {}
    private final java.util.List<Recovery> recoveryEvents=new java.util.ArrayList<>();
    public java.util.List<Recovery> recoveryEvents(){return java.util.List.copyOf(recoveryEvents);}
    public int gate,ticks,penaltyTicks,jumps,recoveries,remounts;
    public boolean finished,dnf;
    private int stagnant,jumpCooldown,safeGate,mountMissingTicks,lastNativeTick=-1,lastGateTick;
    public int nativeTickWaits;
    public int laps=1,rank=1,runners=1;
    public HorsePace pace=new HorsePace(HorsePace.Style.FRONT,false);
    public void configure(int laps,HorsePace.Style style,boolean skills){if(ticks!=0||laps<1||laps>5)throw new IllegalArgumentException("Invalid race rules");this.laps=laps;pace=new HorsePace(style,skills);}
    public double progress(){return Math.min(1,(double)gate/(course.gates.size()*laps));}
    public String dnfReason="";
    private Vec3 sampled,safe;
    private double lane,laneRatio=1,trafficTarget=1,trafficPace=1;
    public int trafficYieldTicks;
    public HorsePilot(ServerLevel level,HorseCourse course,HorseIdentity identity,String model,String texture){
        this(level,course,identity,model,texture,null,-1);
    }
    public HorsePilot(ServerLevel level,HorseCourse course,HorseIdentity identity,String model,String texture,int slot){
        this(level,course,identity,model,texture,null,slot);
    }
    public static HorsePilot leased(ServerLevel level,HorseCourse course,HorseIdentity identity,String model,String texture,HorseAssetLease leases,java.util.UUID token){
        return new HorsePilot(level,course,identity,model,texture,leases.horse(token),-1);
    }
    private HorsePilot(ServerLevel level,HorseCourse course,HorseIdentity identity,String model,String texture,Horse existing,int slot){
        this.course=course;this.identity=identity;
        generated=existing==null;
        horse=generated?EntityType.HORSE.create(level):existing;if(horse==null)throw new IllegalStateException("Horse unavailable");
        if(!generated&&(!horse.getTags().contains(HorseAssetLease.FIXTURE)||!horse.getTags().contains(HorseAssetLease.LEASE)||horse.level()!=level))throw new IllegalStateException("Only a sandbox lease may adopt an existing horse");
        if(generated)horse.addTag(MARKER);horse.setTamed(true);horse.setAge(0);horse.setPersistenceRequired();
        horse.setCustomName(Component.literal(identity.name()));horse.setInvulnerable(true);
        horse.getAttribute(Attributes.MOVEMENT_SPEED).setBaseValue(identity.speed());
        horse.getAttribute(Attributes.JUMP_STRENGTH).setBaseValue(identity.jump());
        horse.getAttribute(Attributes.MAX_HEALTH).setBaseValue(identity.health());horse.setHealth(horse.getMaxHealth());
        horse.equipSaddle(new ItemStack(Items.SADDLE),SoundSource.NEUTRAL);
        horse.removeAllGoals(goal->true);horse.setNoAi(false);
        ((HorseControlAccess)horse).muxi$horseControl(new HorseMoveControl(horse));
        Vec3 spawn=slot<0?course.start:course.gridStart(slot);if(slot>=0)lane=HorseRaceGrid.lane(slot);
        laneRatio=course.stadium()?laneLengthRatio():1;
        horse.setPos(spawn.x,spawn.y,spawn.z);horse.setYRot(-90);safe=sampled=spawn;
        if(generated&&!level.addFreshEntity(horse))throw new IllegalStateException("Horse spawn failed");
        try{rider=MaidRider.create(level,horse,model,texture);}catch(RuntimeException failure){if(generated)horse.discard();throw failure;}
    }
    /** Freeze only generated race copies until native entry and stable mounts are confirmed. */
    public void hold(){horse.setNoAi(true);horse.setSpeed(0);horse.xxa=0;horse.zza=0;horse.setDeltaMovement(0,horse.getDeltaMovement().y,0);}
    public String readiness(){
        if(!horse.isAlive())return "horse_unavailable:"+horse.getRemovalReason();
        if(!rider.isAlive())return "rider_unavailable:"+rider.getRemovalReason();
        var level=(ServerLevel)horse.level();
        if(rider.level()!=level||level.getEntity(horse.getUUID())!=horse||level.getEntity(rider.getUUID())!=rider)return "actors_not_tracked";
        if(!level.isPositionEntityTicking(horse.blockPosition()))return "actor_chunk_not_ticking";
        if(rider.getVehicle()!=horse){rider.startRiding(horse,true);return "rider_mount_pending";}
        if(level.noCollision(horse,horse.getBoundingBox().move(0,-.15,0))||Math.abs(horse.getY()-safe.y)>.5)return "horse_not_on_start_floor";
        return "";
    }
    public void releaseStart(){horse.setNoAi(false);sampled=horse.position();stagnant=0;mountMissingTicks=0;}
    private void fail(String reason){dnf=true;dnfReason=reason;org.slf4j.LoggerFactory.getLogger("muxi-horse").warn("Race actor DNF horse={} rider={} ticks={} gate={} reason={} position={} horseRemoval={} riderRemoval={}",horse.getUUID(),rider.getUUID(),ticks,gate,reason,horse.position(),horse.getRemovalReason(),rider.getRemovalReason());}
    /** Local physical traffic sensing. Never changes a position or another room's runner. */
    public void traffic(java.util.Collection<HorsePilot> field){
        trafficTarget=1;if(finished||dnf)return;
        Vec3 direction=course.pointForLane(gate%course.gates.size(),lane).subtract(horse.position());
        double length=direction.horizontalDistance();if(length<.001)return;
        double dx=direction.x/length,dz=direction.z/length;
        for(var other:field)if(other!=this&&other.course==course&&other.horse.isAlive()){
            Vec3 offset=other.horse.position().subtract(horse.position());double ahead=offset.x*dx+offset.z*dz,side=Math.abs(offset.x*dz-offset.z*dx);
            if(ahead>.05&&ahead<3&&side<horse.getBbWidth()+.25&&Math.abs(offset.y)<1.5)
                trafficTarget=Math.min(trafficTarget,Math.max(.35,Math.min(1,(ahead-1.5)/1.5)));
        }
    }
    /** Called once per server tick. Vanilla MoveControl/travel supplies gravity and collisions. */
    public void tick(){
        if(finished||dnf)return;
        // Room callbacks do not prove vanilla entity physics ran. Never award
        // progress or invoke stuck recovery while the entity is not ticking.
        if(!((ServerLevel)horse.level()).isPositionEntityTicking(horse.blockPosition())||horse.tickCount==lastNativeTick){
            nativeTickWaits++;if(nativeTickWaits>=600)fail("native_entity_tick_timeout");return;
        }
        lastNativeTick=horse.tickCount;nativeTickWaits=0;ticks++;
        if(!horse.isAlive()||!rider.isAlive()||horse.level()!=rider.level()){fail(!horse.isAlive()?"horse_unavailable":!rider.isAlive()?"rider_unavailable":"actor_dimension_mismatch");return;}
        if(rider.getVehicle()!=horse){
            mountMissingTicks++;
            if(mountMissingTicks%10==1){remounts++;rider.startRiding(horse,true);}
            if(rider.getVehicle()!=horse){if(mountMissingTicks>=60)fail("rider_mount_timeout");return;}
        }
        mountMissingTicks=0;
        Vec3 target=course.pointForLane(gate%course.gates.size(),lane),delta=target.subtract(horse.position());
        double strength=horse.getAttributeValue(Attributes.JUMP_STRENGTH);
        double peakTicks=strength/.08;
        double jumpDistance=Mth.clamp(horse.getDeltaMovement().horizontalDistance()*peakTicks+.4*(1-Math.pow(.91,peakTicks))/.09,3,8);
        boolean approach=course.stadium()?course.hurdleAhead(horse.position(),lane,jumpDistance):course.gates.get(gate%course.gates.size()).jump()&&delta.horizontalDistance()<1.8;
        if(delta.horizontalDistance()<.8&&Math.abs(delta.y)<(course.stadium()?6:2)){
            if(!course.gates.get(gate%course.gates.size()).jump()&&horse.onGround()){safe=target;safeGate=gate+1;}
            gate++;lastGateTick=ticks;
            if(gate==course.gates.size()*laps){finished=true;horse.setNoAi(true);horse.setDeltaMovement(Vec3.ZERO);return;}
            target=course.pointForLane(gate%course.gates.size(),lane);delta=target.subtract(horse.position());
        }
        float wanted=(float)(Mth.atan2(delta.z,delta.x)*180/Math.PI)-90;
        horse.setYRot(horse.getYRot()+Mth.clamp(Mth.wrapDegrees(wanted-horse.getYRot()),-12,12));
        horse.setYBodyRot(horse.getYRot());horse.setYHeadRot(horse.getYRot());
        rider.setYRot(horse.getYRot());rider.setYBodyRot(horse.getYRot());rider.setYHeadRot(horse.getYRot());
        horse.setSpeed((float)horse.getAttributeValue(Attributes.MOVEMENT_SPEED));
        if(jumpCooldown>0)jumpCooldown--;
        if(horse.onGround()&&jumpCooldown==0&&(approach||horse.horizontalCollision)){
            double angle=horse.getYRot()*Math.PI/180;
            // Same 0.4 forward impulse used by vanilla ridden horse jumps; jump height is the horse attribute.
            horse.setDeltaMovement(horse.getDeltaMovement().add(-Math.sin(angle)*.4,strength,Math.cos(angle)*.4));
            horse.setIsJumping(true);horse.hasImpulse=true;jumpCooldown=24;jumps++;
        }
        double multiplier=pace.tick(progress(),rank,runners,gate%course.gates.size()%24==1&&ticks%20==0);
        // Equalize the length of the eight parallel ovals without changing source attributes.
        if(course.stadium())multiplier*=laneRatio;
        trafficPace+=Math.max(-.025,Math.min(.01,trafficTarget-trafficPace));
        if(trafficPace<.99)trafficYieldTicks++;
        multiplier=Math.min(1.4,multiplier)*trafficPace;
        horse.getMoveControl().setWantedPosition(target.x,horse.getY(),target.z,multiplier);
        if(horse.onGround())horse.setIsJumping(false);
        if(ticks%20==0){
            double moved=horse.position().subtract(sampled).horizontalDistance();sampled=horse.position();
            stagnant=moved<.25?stagnant+20:0;
            if(horse.getY()<62||horse.isInWater())fail(horse.getY()<62?"below_track":"water");
            else if(stagnant>=200)fail("traffic_or_route_stall");
        }
        if(ticks-lastGateTick>200)fail("route_progress_timeout");
        if(ticks>2400*laps)fail("race_timeout");
    }
    private double laneLengthRatio(){
        double center=0,parallel=0;
        for(int i=0;i<HorseStadium.GATES;i++){
            double a=-Math.PI/2+2*Math.PI*i/HorseStadium.GATES,b=a+2*Math.PI/HorseStadium.GATES;
            center+=HorseStadium.point(a,0).distanceTo(HorseStadium.point(b,0));
            parallel+=HorseStadium.point(a,lane).distanceTo(HorseStadium.point(b,lane));
        }
        return parallel/center;
    }
    public int resultTicks(){return ticks+penaltyTicks;}
    public void lane(double offset){if(ticks!=0||Math.abs(offset)>3)throw new IllegalArgumentException("Invalid start lane");lane=offset;laneRatio=course.stadium()?laneLengthRatio():1;safe=sampled=course.laneStart(lane);horse.setPos(safe.x,safe.y,safe.z);horse.setYRot(-90);}
    public void close(){
        // Do not trust a name/UUID supplied by a caller to choose cleanup targets.
        if(rider.getTags().contains(MARKER)){rider.stopRiding();rider.discard();}
        if(generated&&horse.getTags().contains(MARKER))horse.discard();
        else if(!generated){horse.setNoAi(true);horse.setDeltaMovement(Vec3.ZERO);}
    }
}
