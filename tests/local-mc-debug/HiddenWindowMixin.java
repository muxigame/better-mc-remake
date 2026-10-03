package net.muxigame.localdebug.mixin;
import com.mojang.blaze3d.platform.Window;
import net.neoforged.fml.loading.ImmediateWindowHandler;
import org.lwjgl.glfw.GLFW;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
import java.util.function.*;

/** QA-only; no focus/activation calls and no forced visible Session2 window. */
@Mixin(value=Window.class,remap=false)
public abstract class HiddenWindowMixin {
    @Redirect(method="<init>",at=@At(value="INVOKE",target="Lnet/neoforged/fml/loading/ImmediateWindowHandler;setupMinecraftWindow(Ljava/util/function/IntSupplier;Ljava/util/function/IntSupplier;Ljava/util/function/Supplier;Ljava/util/function/LongSupplier;)J"))
    private long localDebugHidden(IntSupplier width,IntSupplier height,Supplier<String> title,LongSupplier monitor){
        if(!Boolean.getBoolean("qa.local.hidden"))return ImmediateWindowHandler.setupMinecraftWindow(width,height,title,monitor);
        GLFW.glfwWindowHint(GLFW.GLFW_VISIBLE,GLFW.GLFW_FALSE);GLFW.glfwWindowHint(GLFW.GLFW_FOCUSED,GLFW.GLFW_FALSE);GLFW.glfwWindowHint(GLFW.GLFW_FOCUS_ON_SHOW,GLFW.GLFW_FALSE);
        return GLFW.glfwCreateWindow(width.getAsInt(),height.getAsInt(),"Private local MC QA",0,0);
    }
}
