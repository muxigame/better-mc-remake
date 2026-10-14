package net.muxigame.minigames.horse;
/** Geometry shared with offline regression; ellipse radius needs the physical polar angle. */
public final class HorseGeometry {
 public static int standTier(double x,double z,double rx,double rz){
  if(rx<=0||rz<=0)throw new IllegalArgumentException("Invalid stand ellipse");
  double angle=Math.atan2(z,x);
  double inner=1/Math.sqrt(Math.pow(Math.cos(angle)/rx,2)+Math.pow(Math.sin(angle)/rz,2));
  return (int)Math.floor((Math.hypot(x,z)-inner)/2);
 }
 private HorseGeometry(){}
}
