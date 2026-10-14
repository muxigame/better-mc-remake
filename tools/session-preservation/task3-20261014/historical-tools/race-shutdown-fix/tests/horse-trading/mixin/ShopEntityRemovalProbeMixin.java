package net.muxigame.minigames.horseqa.mixin;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.animal.horse.Horse;
import com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import java.nio.file.*;
/** Isolated diagnostic only; does not change removal or delivery behavior. */
@Mixin(Entity.class)
public abstract class ShopEntityRemovalProbeMixin {
 @Inject(method="setRemoved",at=@At("TAIL")) private void removed(Entity.RemovalReason reason,CallbackInfo ci){
  if(!Boolean.getBoolean("muxi.horse.shopClientQa")||!"server".equals(System.getProperty("qa.local.role")))return;
  var entity=(Entity)(Object)this;if(!(entity instanceof Horse)&&!(entity instanceof EntityMaid))return;
  try{var out=new StringBuilder(entity.getUUID()+" "+entity.getType()+" "+reason+" "+entity.level().dimension().location()+" "+entity.position()+"\n");for(var frame:Thread.currentThread().getStackTrace())out.append(frame).append('\n');Files.writeString(Path.of(System.getProperty("qa.local.root")).resolve("shop-entity-removals.log"),out.toString(),StandardOpenOption.CREATE,StandardOpenOption.APPEND);}catch(Exception ignored){}
 }
}
