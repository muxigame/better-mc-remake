package net.muxigame.terminal.qa.mixin;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
import java.util.*;
/** QA MOD ONLY: map exactly the player site to this run's loopback TLS port and match only its ephemeral public SPKI. */
@Mixin(targets="com.cinemamod.mcef.CefUtil",remap=false)
public abstract class DirectTlsCefMixin {
    private static String[] args(String[] original){
        if(!Boolean.getBoolean("muxi.sso.directTls"))return original;
        String port=System.getProperty("muxi.sso.sitePort"),pin=System.getProperty("muxi.sso.spki");
        if(port==null || !port.matches("[0-9]{1,5}") || pin==null || !pin.matches("[A-Za-z0-9+/]{43}="))throw new IllegalStateException("Invalid isolated TLS fixture");
        var result=new ArrayList<>(Arrays.asList(original));
        result.add("--host-resolver-rules=MAP mc.muxigame.com:443 127.0.0.1:"+port+",MAP * ~NOTFOUND,EXCLUDE localhost");
        result.add("--ignore-certificate-errors-spki-list="+pin);result.add("--no-proxy-server");
        result.add("--user-data-dir="+new java.io.File("sso-cef-private-profile").getAbsolutePath());
        System.out.println("QA_DIRECT_TLS_ARGS_SCOPED=true");
        return result.toArray(String[]::new);
    }
    @ModifyArg(method="init",at=@At(value="INVOKE",target="Lorg/cef/CefApp;startup([Ljava/lang/String;)Z"),index=0)
    private static String[] startup(String[] original){return args(original);}
    @ModifyArg(method="init",at=@At(value="INVOKE",target="Lorg/cef/CefApp;getInstance([Ljava/lang/String;Lorg/cef/CefSettings;)Lorg/cef/CefApp;"),index=0)
    private static String[] instance(String[] original){return args(original);}
}
