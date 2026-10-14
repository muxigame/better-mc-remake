package net.muxigame.minigames.horse;

import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.Registries;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.*;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.Vec3;
import java.util.*;

/** Deterministic private courses, with bounded hills and visible obstacles. No production world generation. */
public final class HorseCourse {
    public static final ResourceKey<Level> DIMENSION=ResourceKey.create(Registries.DIMENSION,ResourceLocation.fromNamespaceAndPath("muxi_minigames","horse_lab"));
    public static final ResourceKey<Level> STADIUM_DIMENSION=ResourceKey.create(Registries.DIMENSION,ResourceLocation.fromNamespaceAndPath("muxi_minigames","horse_stadium_lab"));
    public record Gate(Vec3 point,boolean jump){}
    public final String id,version;
    public final HorseCourses.Definition definition;
    public final List<Gate> gates;
    public final Vec3 start;
    private final double originX,originZ;
    private HorseStadium.Builder builder;private boolean manageTickets=true;
    public HorseCourse(double x,double z){
        this(x,z,HorseCourses.get("prototype-loop"));
    }
    public HorseCourse(double x,double z,HorseCourses.Definition definition){
        this.definition=definition;id=definition.id();version=definition.version();originX=x;originZ=z;
        start=stadium()?HorseStadium.point(-Math.PI/2,0).add(x,0,z):new Vec3(x+.5,65,z+.5);
        if(stadium()){gates=HorseStadium.gates(x,z);return;}
        if(!definition.hills())gates=List.of(g(x+13,z,true),g(x+20,z,false),g(x+36,z,false),g(x+36,z+26,false),g(x+21,z+26,true),g(x+12,z+26,false),g(x,z+26,false),g(x,z,false));
        else {
            var route=new ArrayList<Gate>();
            for(int dx:new int[]{8,16,24,32,40,48,56})route.add(hillyGate(x,z,dx,0,dx==8));
            if(definition.loop()){
                for(int dz:new int[]{12,24,40})route.add(hillyGate(x,z,56,dz,false));
                for(int dx:new int[]{50,40,32,24,16,8,0})route.add(hillyGate(x,z,dx,40,dx==50));
                for(int dz:new int[]{24,12,0})route.add(hillyGate(x,z,0,dz,false));
            }
            gates=List.copyOf(route);
        }
    }
    private static int hill(int dx){return 65+(dx>=12&&dx<=44?Math.min(dx-12,44-dx)/4:0);}
    private static Gate hillyGate(double x,double z,int dx,int dz,boolean jump){return new Gate(new Vec3(x+dx+.5,hill(dx),z+dz+.5),jump);}
    private static Gate g(double x,double z,boolean jump){return new Gate(new Vec3(x+.5,65,z+.5),jump);}
    public void build(ServerLevel level){build(level,true);}
    public void build(ServerLevel level,boolean manageTickets){
        this.manageTickets=manageTickets;
        if(!level.dimension().equals(dimension()))throw new IllegalArgumentException("Only the private horse lab may be generated");
        if(stadium()){if(builder!=null)throw new IllegalStateException("Already building");builder=new HorseStadium.Builder(level,(int)originX,(int)originZ,manageTickets);return;}
        int sx=(int)start.x,sz=(int)start.z;
        if(definition.hills()){buildHills(level,sx,sz);return;}
        for(int x=sx-5;x<=sx+41;x++)for(int z=sz-5;z<=sz+31;z++){
            for(int y=63;y<=64;y++)level.setBlock(new BlockPos(x,y,z),Blocks.STONE.defaultBlockState(),3);
            for(int y=65;y<=69;y++)level.setBlock(new BlockPos(x,y,z),Blocks.AIR.defaultBlockState(),3);
        }
        for(int z=sz-4;z<=sz+4;z++)level.setBlock(new BlockPos(sx+15,65,z),Blocks.OAK_FENCE.defaultBlockState(),3);
        for(int x=sx+18;x<=sx+20;x++)for(int z=sz+22;z<=sz+30;z++){
            level.setBlock(new BlockPos(x,64,z),Blocks.WATER.defaultBlockState(),3);
            level.setBlock(new BlockPos(x,63,z),Blocks.STONE.defaultBlockState(),3);
        }
        for(var gate:gates)level.setChunkForced(BlockPos.containing(gate.point).getX()>>4,BlockPos.containing(gate.point).getZ()>>4,true);
    }
    private BlockState surface(){return switch(definition.theme()){
        case "snow" -> Blocks.SNOW_BLOCK.defaultBlockState();case "desert" -> Blocks.SANDSTONE.defaultBlockState();
        case "jungle" -> Blocks.MOSS_BLOCK.defaultBlockState();default -> Blocks.GRASS_BLOCK.defaultBlockState();};}
    private BlockState optionalLeaves(){
        // Resolve installed content by registry identity; absent packs use an existing vanilla surface.
        String selected=switch(definition.theme()){case "snow"->"fir";case "desert"->"palm";case "jungle"->"mahogany";default->"blue_enchanted";};
        for(String name:List.of("biomeswevegone:"+selected+"_leaves","biomeswevegone:blue_enchanted_leaves")){
            var id=ResourceLocation.parse(name);if(BuiltInRegistries.BLOCK.containsKey(id))return BuiltInRegistries.BLOCK.get(id).defaultBlockState();
        }return definition.theme().equals("jungle")?Blocks.JUNGLE_LEAVES.defaultBlockState():Blocks.OAK_LEAVES.defaultBlockState();
    }
    private void buildHills(ServerLevel level,int sx,int sz){
        int depth=definition.loop()?46:6;
        for(int dx=-6;dx<=62;dx++)for(int dz=-6;dz<=depth;dz++){
            int y=hill(dx);for(int dy=62;dy<y;dy++)level.setBlock(new BlockPos(sx+dx,dy,sz+dz),(dy==y-1?surface():Blocks.STONE.defaultBlockState()),3);
            for(int dy=y;dy<=74;dy++)level.setBlock(new BlockPos(sx+dx,dy,sz+dz),Blocks.AIR.defaultBlockState(),3);
            // Mark the two straight running lanes without blocking them.
            if(dz==0||definition.loop()&&dz==40)level.setBlock(new BlockPos(sx+dx,y-1,sz+dz),Blocks.DIRT_PATH.defaultBlockState(),3);
        }
        for(int dz=-4;dz<=4;dz++)level.setBlock(new BlockPos(sx+10,65,sz+dz),Blocks.OAK_FENCE.defaultBlockState(),3);
        if(definition.loop())for(int dx=46;dx<=48;dx++)for(int dz=36;dz<=44;dz++)level.setBlock(new BlockPos(sx+dx,64,sz+dz),Blocks.WATER.defaultBlockState(),3);
        for(int dx:new int[]{4,20,36,52}){int y=hill(dx);int z=sz+(definition.loop()?20:6);for(int dy=0;dy<3;dy++)level.setBlock(new BlockPos(sx+dx,y+dy,z),Blocks.OAK_LOG.defaultBlockState(),3);for(int ox=-1;ox<=1;ox++)for(int oz=-1;oz<=1;oz++)level.setBlock(new BlockPos(sx+dx+ox,y+3,z+oz),optionalLeaves(),3);}
        forceFootprint(level,sx,sz,true);
    }
    private void forceFootprint(ServerLevel level,int sx,int sz,boolean force){int maxX=sx+(definition.hills()?62:41),maxZ=sz+(definition.hills()?(definition.loop()?46:6):31);for(int cx=(sx-6)>>4;cx<=maxX>>4;cx++)for(int cz=(sz-6)>>4;cz<=maxZ>>4;cz++)level.setChunkForced(cx,cz,force);}
    public boolean stadium(){return id.equals(HorseStadium.ID);}
    public ResourceKey<Level> dimension(){return stadium()?STADIUM_DIMENSION:DIMENSION;}
    public boolean advanceBuild(){return builder==null||builder.tick();}
    public HorseStadium.Builder builder(){return builder;}
    public Vec3 podium(){return stadium()?HorseStadium.podium(originX,originZ):start.add(0,7,-4);}
    public boolean hurdleAhead(Vec3 position,double lane,double distance){return stadium()&&HorseStadium.hurdleAhead(position,lane,originX,originZ,distance);}
    public Vec3 pointForLane(int gate,double lane){if(!stadium())return gates.get(gate).point();return HorseStadium.point(-Math.PI/2+2*Math.PI*(gate+1)/HorseStadium.GATES,lane).add(originX,0,originZ);}
    public Vec3 laneStart(double lane){return stadium()?HorseStadium.point(-Math.PI/2,lane).add(originX,0,originZ):start.add(0,0,lane);}
    public Vec3 gridStart(int slot){
        if(!stadium())throw new IllegalArgumentException("Formal oval grid only");
        return HorseStadium.point(-Math.PI/2-HorseRaceGrid.row(slot)*HorseRaceGrid.ROW_SPACING/HorseStadium.RX,HorseRaceGrid.lane(slot)).add(originX,0,originZ);
    }
    public void unload(ServerLevel level){if(builder!=null){builder.close();return;}if(!manageTickets)return;if(definition.hills())forceFootprint(level,(int)start.x,(int)start.z,false);else for(var gate:gates)level.setChunkForced(BlockPos.containing(gate.point).getX()>>4,BlockPos.containing(gate.point).getZ()>>4,false);}
}
