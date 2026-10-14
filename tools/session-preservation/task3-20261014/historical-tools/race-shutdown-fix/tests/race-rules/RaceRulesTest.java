import net.muxigame.minigames.horse.*;
public class RaceRulesTest {
 static int checks;
 static void check(boolean value,String message){checks++;if(!value)throw new AssertionError(message);}
 public static void main(String[] args){
  for(int i=0;i<8;i++){
   check(Math.abs(HorseRaceGrid.lane(i))+.7<=8,"lane fits horse inside track");
   for(int j=i+1;j<8;j++)check(Math.abs(HorseRaceGrid.lane(i)-HorseRaceGrid.lane(j))>=1.9-1e-12,"independent lanes");
  }
  check(HorseGeometry.standTier(62.5,-32.8,88,52)<0,"native gate13 must not be a stand");
  for(int laneIndex=0;laneIndex<8;laneIndex++)for(int i=0;i<256;i++){
   double angle=i*Math.PI*2/256,lane=HorseRaceGrid.lane(laneIndex),nx=Math.cos(angle)/78,nz=Math.sin(angle)/42,n=Math.hypot(nx,nz);
   double x=.5+78*Math.cos(angle)+lane*nx/n,z=.5+42*Math.sin(angle)+lane*nz/n;
   for(double dx:new double[]{-.7,.7})for(double dz:new double[]{-.7,.7})check(HorseGeometry.standTier(Math.floor(x+dx),Math.floor(z+dz),88,52)<0,"stand never overrides horse corridor");
  }
  double[] early=new double[4],late=new double[4];
  for(var style:HorsePace.Style.values())for(boolean enabled:new boolean[]{false,true}){
   var p=new HorsePace(style,enabled);double last=p.pace;
   for(int tick=0;tick<6000;tick++){
    double progress=(double)tick/6000;
    double speed=p.tick(progress,4,8,tick%200==0);
    check(speed>=.75&&speed<=1.25,"bounded speed");
    check(Math.abs(speed-last)<=.00400001,"bounded acceleration");
    check(p.stamina>=0&&p.stamina<=1,"bounded stamina");last=speed;
    if(tick==400)early[style.ordinal()]=speed;
    if(tick==4800)late[style.ordinal()]=speed;
   }
   check(p.recoveryUses<=1&&p.surgeUses<=1,"once per whole race, not once per lap");
   if(!enabled)check(p.recoveryUses==0&&p.curveUses==0&&p.surgeUses==0,"skills off has no activations");
   if(enabled)check(p.surgeUses==1,"final surge fires once");
  }
  check(early[0]>early[3],"lead and chase differ early");
  check(late[3]>late[0],"chase conserves for later");
  var curve=new HorsePace(HorsePace.Style.FRONT,true);
  for(int i=0;i<100;i++)curve.tick(.3,1,8,true);
  check(curve.curveUses==1,"repeated curve signal cannot farm boost");
  System.out.println("PASS "+checks+" assertions");
 }
}
