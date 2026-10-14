package net.muxigame.minigames.horseqa.mixin;
import org.cef.CefSettings;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.invoke.arg.Args;
import java.nio.file.*;
import java.util.*;
/** Test-only profile isolation before native CEF initialization. */
@Mixin(targets="com.cinemamod.mcef.CefUtil",remap=false)
public abstract class ShopCefProfileMixin {
 private static Path profile(){
  var root=Path.of(System.getProperty("qa.local.root")).toAbsolutePath().normalize();
  if(!Boolean.getBoolean("muxi.horse.shopClientQa")||!Files.isRegularFile(root.resolve("local-mc-owner.json")))throw new IllegalStateException("Missing private CEF owner");
  return root.resolve(System.getProperty("qa.local.role")).resolve("cef-profile");
 }
 private static String[] isolated(String[] original){
  var args=new ArrayList<>(Arrays.asList(original));args.removeIf(v->v.startsWith("--user-data-dir="));args.add("--user-data-dir="+profile());return args.toArray(String[]::new);
 }
 @ModifyArg(method="init",at=@At(value="INVOKE",target="Lorg/cef/CefApp;startup([Ljava/lang/String;)Z"),index=0)
 private static String[] startup(String[] original){return isolated(original);}
 @ModifyArgs(method="init",at=@At(value="INVOKE",target="Lorg/cef/CefApp;getInstance([Ljava/lang/String;Lorg/cef/CefSettings;)Lorg/cef/CefApp;"))
 private static void settings(Args args){
  try{var profile=profile();Files.createDirectories(profile);args.set(0,isolated(args.get(0)));CefSettings settings=args.get(1);settings.cache_path=profile.resolve("cache").toString();settings.log_file=profile.getParent().resolve("cef-debug.log").toString();settings.persist_session_cookies=false;
   var evidence=new com.google.gson.JsonObject();evidence.addProperty("userDataDir",profile.toString());evidence.addProperty("cachePath",settings.cache_path);evidence.addProperty("logFile",settings.log_file);evidence.addProperty("beforeCefInitialization",true);Files.writeString(profile.getParent().getParent().resolve("cef-profile-configured.json"),evidence.toString());
  }catch(Exception error){throw new IllegalStateException("Private CEF profile unavailable",error);}
 }
}
