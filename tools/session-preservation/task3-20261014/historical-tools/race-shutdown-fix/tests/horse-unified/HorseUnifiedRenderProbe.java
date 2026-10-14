package net.muxigame.horseunifiedqa.mixin;
import net.muxigame.horseunifiedqa.HorseUnifiedClientQA;
import com.mojang.blaze3d.vertex.PoseStack;
import net.minecraft.client.renderer.MultiBufferSource;
import net.minecraft.world.entity.Mob;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
@Mixin(targets="com.github.tartaricacid.touhoulittlemaid.client.renderer.entity.EntityMaidRenderer",remap=false)
public abstract class HorseUnifiedRenderProbe {
    @Inject(method="render(Lnet/minecraft/world/entity/Mob;FFLcom/mojang/blaze3d/vertex/PoseStack;Lnet/minecraft/client/renderer/MultiBufferSource;I)V",at=@At(value="INVOKE",target="Lcom/github/tartaricacid/touhoulittlemaid/geckolib3/geo/IGeoEntityRenderer;geoRender(Lnet/minecraft/world/entity/Entity;FFLcom/mojang/blaze3d/vertex/PoseStack;Lnet/minecraft/client/renderer/MultiBufferSource;I)V",shift=At.Shift.AFTER))
    private void horse$draw(Mob maid,float yaw,float partial,PoseStack pose,MultiBufferSource buffers,int light,CallbackInfo ci){if(Boolean.getBoolean("muxi.horse.unifiedQa")&&maid.isPassenger())HorseUnifiedClientQA.mountedDraws++;}
}
