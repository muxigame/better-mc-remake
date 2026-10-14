package net.muxigame.horseunifiedqa.mixin;
import com.google.gson.GsonBuilder;
import net.minecraft.client.Minecraft;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.ModifyArgs;
import org.spongepowered.asm.mixin.injection.invoke.arg.Args;
import java.nio.file.*;
import java.util.*;

/** Private fixture paths only; leaves the original native engine, flags and lifecycle intact. */
@Mixin(targets="com.cinemamod.mcef.CefUtil",remap=false)
public abstract class CefPrivatePathsMixin {
 @ModifyArgs(method="init",at=@At(value="INVOKE",target="Lorg/cef/CefApp;getInstance([Ljava/lang/String;Lorg/cef/CefSettings;)Lorg/cef/CefApp;"))
 private static void qa$privatePaths(Args args)throws Exception {
  if(!Boolean.getBoolean("muxi.horse.unifiedQa"))return;
  String role=System.getProperty("qa.local.role","");if(!Set.of("host","guest").contains(role))throw new IllegalStateException("Private client role required");
  Path root=Path.of(System.getProperty("qa.local.root",".")).toAbsolutePath().normalize();
  Path home=Minecraft.getInstance().gameDirectory.toPath().toAbsolutePath().normalize();
  if(!home.equals(root.resolve(role)))throw new IllegalStateException("Exact private client root required");
  Path data=home.resolve("mcef-private/user-data"),log=home.resolve("logs/cef-native.log");Files.createDirectories(data);Files.createDirectories(log.getParent());
  String[] original=args.get(0);var flags=new ArrayList<>(Arrays.asList(original));
  if(flags.stream().anyMatch(f->f.startsWith("--user-data-dir=")||f.startsWith("--log-file=")))throw new IllegalStateException("Unexpected pre-existing native path flags");
  flags.add("--user-data-dir="+data);flags.add("--log-file="+log);args.set(0,flags.toArray(String[]::new));
  Object settings=args.get(1);settings.getClass().getField("log_file").set(settings,log.toString());
  var receipt=new LinkedHashMap<String,Object>();receipt.put("role",role);receipt.put("instanceRoot",root.toString());receipt.put("userDataDir",data.toString());receipt.put("nativeLog",log.toString());receipt.put("nativeEngineUnchanged",true);receipt.put("originalFlags",original);receipt.put("configuredBeforeNativeInit",true);
  Files.writeString(home.resolve("mcef-private-paths.json"),new GsonBuilder().setPrettyPrinting().create().toJson(receipt));
 }
}
