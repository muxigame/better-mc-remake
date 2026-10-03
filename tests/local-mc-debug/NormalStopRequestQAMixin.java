package net.muxigame.localdebug.mixin;
import net.minecraft.client.Minecraft;
import java.nio.file.*;
import com.google.gson.JsonParser;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** Normal close remains available while the client is waiting for a level/screen. */
@Mixin(value=Minecraft.class,remap=false)
public abstract class NormalStopRequestQAMixin {
    private long localDebugNextStopCheck;
    @Inject(method="runTick(Z)V",at=@At("HEAD"))
    private void localDebugNormalStop(boolean render,CallbackInfo ci){
        String run=System.getProperty("qa.local.runId","");if(run.isBlank())return;
        long now=System.nanoTime();if(now<localDebugNextStopCheck)return;localDebugNextStopCheck=now+500_000_000L;
        try{var path=Path.of(System.getProperty("qa.local.root")).resolve("stop-request.json");if(!Files.isRegularFile(path))return;var request=JsonParser.parseString(Files.readString(path)).getAsJsonObject();if(run.equals(request.get("runId").getAsString()))Minecraft.getInstance().stop();}catch(Exception transientRead){}
    }
}
