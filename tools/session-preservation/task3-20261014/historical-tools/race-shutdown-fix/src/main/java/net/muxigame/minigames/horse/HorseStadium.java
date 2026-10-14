package net.muxigame.minigames.horse;

import net.minecraft.core.*;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.level.block.*;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.*;
import net.minecraft.world.phys.Vec3;
import java.util.*;
import java.util.concurrent.*;

/** Original, deterministic modular architecture. Planning never accesses a world. */
public final class HorseStadium {
    public static final String ID="stadium-oval";
    public static final int RX=78,RZ=42,GATES=96;
    public static final double TRACK_HALF_WIDTH=8.0;
    private static final double[] HURDLES={-1.12,1.20,3.60};
    private static final ExecutorService PLANNER=Executors.newSingleThreadExecutor(r->{Thread t=new Thread(r,"muxi-horse-scene-planner");t.setDaemon(true);return t;});
    private static final CompletableFuture<Blueprint> PLAN=CompletableFuture.supplyAsync(HorseStadium::plan,PLANNER);
    public record Placement(int x,int y,int z,BlockState state){}
    public record Blueprint(List<Placement> blocks,long planningNanos,Map<String,Integer> modules){}
    public static Vec3 point(double angle,double lane){
        double nx=Math.cos(angle)/RX,nz=Math.sin(angle)/RZ,n=Math.hypot(nx,nz);
        return new Vec3(.5+RX*Math.cos(angle)+lane*nx/n,65,.5+RZ*Math.sin(angle)+lane*nz/n);
    }
    public static List<HorseCourse.Gate> gates(double x,double z){
        List<HorseCourse.Gate> out=new ArrayList<>();
        for(int i=1;i<=GATES;i++){
            double a=-Math.PI/2+2*Math.PI*i/GATES;Vec3 p=point(a,0);
            boolean jump=false;
            for(double h:HURDLES){double arc=wrap(h-a);if(arc>=0&&arc*speed(a)<3.6)jump=true;}
            out.add(new HorseCourse.Gate(p.add(x,0,z),jump));
        }return List.copyOf(out);
    }
    public static boolean hurdleAhead(Vec3 position,double lane,double ox,double oz,double distance){
        double angle=Math.atan2((position.z-oz-.5)/RZ,(position.x-ox-.5)/RX);
        for(double hurdle:HURDLES){double arc=wrap(hurdle-angle);
            if(arc<Math.PI/4&&point(hurdle,lane).add(ox,0,oz).subtract(position).horizontalDistance()<=distance)return true;
        }
        return false;
    }
    private static double wrap(double a){while(a<0)a+=Math.PI*2;while(a>=Math.PI*2)a-=Math.PI*2;return a;}
    private static double speed(double a){return Math.hypot(RX*Math.sin(a),RZ*Math.cos(a));}
    public static Vec3 podium(double x,double z){return new Vec3(x+.5,81,z-62+.5);}
    private static Blueprint plan(){
        long begin=System.nanoTime();var b=new Draft();
        b.module("terrain",()->terrain(b));b.module("track",()->track(b));
        b.module("stands",()->stands(b));b.module("grandstand",()->grandstand(b));
        b.module("gardens",()->gardens(b));b.module("obstacles",()->obstacles(b));
        var blocks=new ArrayList<>(b.blocks.values());
        blocks.sort(Comparator.comparingInt((Placement p)->p.x>>4).thenComparingInt(p->p.z>>4).thenComparingInt(Placement::y).thenComparingInt(Placement::x).thenComparingInt(Placement::z));
        if(blocks.size()>360000)throw new IllegalStateException("Scene placement bound exceeded");
        return new Blueprint(List.copyOf(blocks),System.nanoTime()-begin,Map.copyOf(b.modules));
    }
    private static final class Draft {
        final Map<Long,Placement> blocks=new HashMap<>();final Map<String,Integer> modules=new LinkedHashMap<>();
        void put(int x,int y,int z,Block b){put(x,y,z,b.defaultBlockState());}
        void put(int x,int y,int z,BlockState s){if(Math.abs(x)>112||Math.abs(z)>82||y<63||y>91)throw new IllegalArgumentException("Scene bounds");blocks.put(BlockPos.asLong(x,y,z),new Placement(x,y,z,s));}
        void box(int x1,int y1,int z1,int x2,int y2,int z2,Block b){for(int x=x1;x<=x2;x++)for(int y=y1;y<=y2;y++)for(int z=z1;z<=z2;z++)put(x,y,z,b);}
        void module(String name,Runnable f){int before=blocks.size();f.run();modules.put(name,blocks.size()-before);}
    }
    private static void terrain(Draft b){
        for(int x=-109;x<=109;x++)for(int z=-79;z<=79;z++){
            double radius=x*x/(110.0*110)+z*z/(80.0*80);
            // The dedicated flat dimension already supplies the stone/dirt/grass foundation.
            if(radius>.84&&radius<.89)b.put(x,64,z,Blocks.DIRT_PATH);
        }
    }
    private static void track(Draft b){
        for(int x=-RX-11;x<=RX+11;x++)for(int z=-RZ-11;z<=RZ+11;z++){
            double a=Math.atan2(z/(double)RZ,x/(double)RX);Vec3 p=point(a,0);double d=Math.hypot(x+.5-p.x,z+.5-p.z);
            // Clear earlier layout stands/rails from the expanded racing corridor.
            if(d<=TRACK_HALF_WIDTH+2)for(int y=65;y<=85;y++)b.put(x,y,z,Blocks.AIR);
            if(d<=TRACK_HALF_WIDTH)b.put(x,64,z,Blocks.PACKED_MUD);
            else if(d<=TRACK_HALF_WIDTH+1.1)b.put(x,64,z,Blocks.WHITE_CONCRETE);
            if(d>TRACK_HALF_WIDTH+1.1&&d<=TRACK_HALF_WIDTH+2.0)b.put(x,65,z,Blocks.SMOOTH_SANDSTONE_SLAB);
            if(d<=TRACK_HALF_WIDTH&&Math.abs(x)<=1&&z<0)b.put(x,64,z,((x+z)&1)==0?Blocks.WHITE_CONCRETE:Blocks.BLACK_CONCRETE);
        }
        // Low inner rails keep the presidential view clear; every eighth post is decorative.
        for(int i=0;i<192;i++){double a=i*Math.PI*2/192;Vec3 p=point(a,-(TRACK_HALF_WIDTH+1.6));b.put((int)Math.floor(p.x),65,(int)Math.floor(p.z),Blocks.SMOOTH_SANDSTONE_SLAB);}
        for(int x:new int[]{-5,5}){b.box(x,65,-RZ-8,x,70,-RZ-8,Blocks.STRIPPED_SPRUCE_LOG);b.put(x,71,-RZ-8,Blocks.LANTERN);}
    }
    private static void stands(Draft b){
        for(int x=-RX-27;x<=RX+27;x++)for(int z=-RZ-27;z<=RZ+27;z++){
            double a=Math.atan2(z,x);int tier=HorseGeometry.standTier(x,z,RX+10,RZ+10);
            if(tier<0||tier>=8||z<-RZ-8&&Math.abs(x)<19)continue;
            int top=65+tier*2;
            b.put(x,top-1,z,tier%2==0?Blocks.SMOOTH_SANDSTONE:Blocks.STONE_BRICKS);
            boolean aisle=Math.abs(x)<3||Math.abs(z)<3;
            Direction face=Math.abs(Math.cos(a))>Math.abs(Math.sin(a))?(x>0?Direction.WEST:Direction.EAST):(z>0?Direction.NORTH:Direction.SOUTH);
            if(!aisle)b.put(x,top,z,Blocks.SPRUCE_STAIRS.defaultBlockState().setValue(StairBlock.FACING,face));
            else b.put(x,top,z,Blocks.SMOOTH_SANDSTONE);
            if(tier==7){b.put(x,top+1,z,Blocks.STONE_BRICK_WALL);if((x+z)%11==0)b.put(x,top+2,z,Blocks.LANTERN);if((x&3)==0||(z&3)==0)b.box(x,65,z,x,top-2,z,Blocks.STONE_BRICKS);}
        }
        // Four repeating facade pavilions and lamps, outside the viewing cone.
        for(int x:new int[]{-78,78})for(int z:new int[]{-44,44}){
            b.box(x-2,65,z-2,x+2,66,z+2,Blocks.STONE_BRICKS);
            for(int dx:new int[]{-2,2})for(int dz:new int[]{-2,2})b.box(x+dx,67,z+dz,x+dx,83,z+dz,Blocks.SMOOTH_SANDSTONE);
            b.box(x-3,84,z-3,x+3,84,z+3,Blocks.CUT_COPPER);b.put(x,85,z,Blocks.SEA_LANTERN);
        }
    }
    private static void grandstand(Draft b){
        b.box(-18,78,-75,18,79,-62,Blocks.SMOOTH_SANDSTONE);
        b.box(-16,80,-74,16,80,-62,Blocks.POLISHED_ANDESITE);
        b.box(-7,80,-69,7,80,-63,Blocks.RED_CARPET);
        // Presidential seats face the oval; the centre spectator spawn sits immediately in front.
        for(int x=-12;x<=12;x+=3)b.put(x,81,-65,Blocks.DARK_OAK_STAIRS.defaultBlockState().setValue(StairBlock.FACING,Direction.SOUTH));
        for(int x:new int[]{-17,17}){b.box(x,80,-73,x,89,-73,Blocks.QUARTZ_PILLAR);b.box(x,65,-63,x,89,-63,Blocks.QUARTZ_PILLAR);}
        b.box(-19,90,-76,19,90,-61,Blocks.CUT_COPPER);
        b.box(-18,91,-75,18,91,-62,Blocks.SMOOTH_SANDSTONE_SLAB);
        for(int x=-14;x<=14;x+=7)b.put(x,89,-66,Blocks.LANTERN.defaultBlockState().setValue(LanternBlock.HANGING,true));
        for(int x=-17;x<=17;x++)if(Math.abs(x)>3)b.put(x,81,-61,Blocks.SMOOTH_SANDSTONE_SLAB);
        for(int z=-78;z<=-63;z++){int y=65+(z+78);for(int x:new int[]{-21,21})b.box(x-1,y-1,z,x+1,y-1,z,Blocks.SMOOTH_SANDSTONE);}
    }
    private static void gardens(Draft b){
        // Structured paths, parterres, and planted groves rather than scattered single trees.
        for(int x=-40;x<=40;x++)for(int z=-17;z<=17;z++)if(Math.abs(z)<=1||Math.abs(x)<=1)b.put(x,64,z,Blocks.SMOOTH_SANDSTONE);
        for(int x:new int[]{-31,-12,12,31})for(int z:new int[]{-12,12}){
            b.box(x-4,64,z-3,x+4,64,z+3,Blocks.MOSS_BLOCK);
            for(int dx=-4;dx<=4;dx++)for(int dz=-3;dz<=3;dz++){
                if(Math.abs(dx)==4||Math.abs(dz)==3)b.put(x+dx,65,z+dz,Blocks.FLOWERING_AZALEA);
                else if((dx+dz)%3==0)b.put(x+dx,65,z+dz,z>0?Blocks.CORNFLOWER:Blocks.PINK_TULIP);
            }
        }
        b.box(-5,64,-4,5,64,4,Blocks.SMOOTH_SANDSTONE);b.box(-3,64,-2,3,64,2,Blocks.WATER);b.put(0,65,0,Blocks.SEA_LANTERN);
        for(int base:new int[]{-24,24})for(int[] p:new int[][]{{-3,-3},{2,-2},{-1,3},{4,3}})tree(b,base+p[0],p[1],3);
        for(int base:new int[]{-95,95})for(int[] p:new int[][]{{-3,-7},{3,-3},{-2,3},{3,8}})tree(b,base+p[0],p[1],4);
        for(int x:new int[]{-58,-38,38,58})for(int z:new int[]{-68,68}){
            for(int[] p:new int[][]{{-3,-2},{2,-2},{0,3}})tree(b,x+p[0],z+p[1],4);
            for(int dx=-5;dx<=5;dx++)for(int dz=-5;dz<=5;dz++)if((dx*dx+dz*dz)<25&&(dx+dz)%3==0){
                int px=x+dx,pz=z+dz;
                // The perimeter path cannot support grass. Keep tree trunks as planned;
                // invalid decorative plants otherwise drop unowned seeds during building.
                var ground=b.blocks.get(BlockPos.asLong(px,64,pz));
                if((ground==null||ground.state.is(Blocks.GRASS_BLOCK))&&!b.blocks.containsKey(BlockPos.asLong(px,65,pz)))b.put(px,65,pz,Blocks.SHORT_GRASS);
            }
        }
    }
    private static void tree(Draft b,int x,int z,int h){
        b.box(x,65,z,x,65+h,z,Blocks.OAK_LOG);
        BlockState leaf=Blocks.OAK_LEAVES.defaultBlockState().setValue(LeavesBlock.PERSISTENT,true);
        for(int dy=h-1;dy<=h+1;dy++)for(int dx=-2;dx<=2;dx++)for(int dz=-2;dz<=2;dz++)if(Math.abs(dx)+Math.abs(dz)+(dy==h+1?1:0)<=3)b.put(x+dx,65+dy,z+dz,leaf);
    }
    private static void obstacles(Draft b){
        for(int i=0;i<HURDLES.length;i++){
            double a=HURDLES[i];
            for(double lane=-TRACK_HALF_WIDTH+.2;lane<=TRACK_HALF_WIDTH-.2;lane+=.2){Vec3 p=point(a,lane);int x=(int)Math.floor(p.x),z=(int)Math.floor(p.z);
                if(i==0)b.put(x,65,z,Blocks.OAK_FENCE.defaultBlockState().setValue(FenceBlock.NORTH,true).setValue(FenceBlock.SOUTH,true));
                else if(i==1)b.put(x,65,z,Blocks.WHITE_CONCRETE);
                else {
                    // A traversable one-block dirt mound with grass shoulders, using real collisions.
                    for(double da=-.03;da<=.03;da+=.015){Vec3 q=point(a+da,lane);b.put((int)Math.floor(q.x),65,(int)Math.floor(q.z),Math.abs(lane)<3?Blocks.COARSE_DIRT:Blocks.GRASS_BLOCK);}
                }
            }
            for(double lane:new double[]{-TRACK_HALF_WIDTH-1.2,TRACK_HALF_WIDTH+1.2}){Vec3 p=point(a,lane);int x=(int)Math.floor(p.x),z=(int)Math.floor(p.z);b.box(x,65,z,x,68,z,Blocks.STRIPPED_SPRUCE_LOG);b.put(x,69,z,Blocks.LANTERN);}
        }
    }

    /** World mutation is confined to bounded server ticks; chunk generation is never joined. */
    public static final class Builder {
        private final ServerLevel level;private final int ox,oz;private final boolean manageTickets;
        private final List<ChunkPos> chunks=new ArrayList<>();
        private int ticket,index;private Blueprint plan;private boolean cancelled;
        public long maxApplyNanos,totalApplyNanos;public int applyTicks;
        public Builder(ServerLevel level,int x,int z){this(level,x,z,true);}
        public Builder(ServerLevel level,int x,int z,boolean manageTickets){this.level=level;this.manageTickets=manageTickets;ox=x;oz=z;for(int cx=(x-112)>>4;cx<=(x+112)>>4;cx++)for(int cz=(z-82)>>4;cz<=(z+82)>>4;cz++)chunks.add(new ChunkPos(cx,cz));if(!manageTickets)ticket=chunks.size();}
        public boolean tick(){
            if(cancelled)throw new IllegalStateException("Scene build cancelled");
            if(plan==null){if(!PLAN.isDone())return false;plan=PLAN.join();}
            for(int n=0;n<2&&ticket<chunks.size();n++){ChunkPos p=chunks.get(ticket++);level.setChunkForced(p.x,p.z,true);}
            long begin=System.nanoTime();int placed=0;
            while(index<plan.blocks.size()&&placed<768&&System.nanoTime()-begin<2_500_000){
                Placement p=plan.blocks.get(index);int x=ox+p.x,z=oz+p.z;
                if(level.getChunkSource().getChunkNow(x>>4,z>>4)==null)break;
                level.setBlock(new BlockPos(x,p.y,z),p.state,2);index++;placed++;
            }
            long elapsed=System.nanoTime()-begin;maxApplyNanos=Math.max(maxApplyNanos,elapsed);totalApplyNanos+=elapsed;applyTicks++;
            // Loaded neighbours can accept placements without being entity-ticking chunks.
            // A fast outside runner reaches those chunks first: all tickets must be issued before start.
            return index==plan.blocks.size()&&ticket==chunks.size();
        }
        public int placed(){return index;}
        public int total(){return plan==null?0:plan.blocks.size();}
        public Blueprint blueprint(){return plan;}
        public void close(){if(cancelled)return;cancelled=true;if(!manageTickets)return;for(int i=0;i<ticket;i++){ChunkPos p=chunks.get(i);level.setChunkForced(p.x,p.z,false);}}
    }
    private HorseStadium(){}
}
