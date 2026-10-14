package net.muxigame.minigames.horse;

import com.google.gson.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;

/** Fixed approved products; immutable order bodies never roll a skin, tier or affix. */
public final class UmamusumeCatalog {
    private static final JsonObject POLICY=read();
    private UmamusumeCatalog(){}
    private static JsonObject read(){try(var input=UmamusumeCatalog.class.getResourceAsStream("/umamusume_catalog_v1.json")){
        if(input==null)throw new IllegalStateException("Umamusume catalog missing");
        var policy=JsonParser.parseString(new String(input.readAllBytes(),StandardCharsets.UTF_8)).getAsJsonObject();
        var skus=new HashSet<String>();var models=new HashSet<String>();
        if(!policy.get("modelIdsFinalized").getAsBoolean()||policy.getAsJsonArray("catalog").size()!=6)throw new IllegalStateException("Approved model IDs not finalized");
        for(var item:policy.getAsJsonArray("catalog")){var b=item.getAsJsonObject();
            if(!skus.add(b.get("sku").getAsString())||!models.add(b.get("modelId").getAsString())||!b.get("modelId").getAsString().matches("10_umamusume/umamusume_[a-z_]+_approved")||!b.get("texture").getAsString().equals("skin")||!isMaid(b)||b.get("purchase").getAsLong()<1||b.get("tier").getAsInt()<1||b.get("tier").getAsInt()>5)throw new IllegalStateException("Invalid fixed Umamusume product");
        }return policy;
    }catch(Exception failure){throw new IllegalStateException("Umamusume catalog invalid",failure);}}
    public static JsonArray offers(){return POLICY.getAsJsonArray("catalog").deepCopy();}
    public static boolean isMaid(JsonObject b){return b.has("entityKind")&&"MAID".equals(b.get("entityKind").getAsString());}
    public static JsonObject offer(String sku){for(var b:offers())if(b.getAsJsonObject().get("sku").getAsString().equals(sku))return b.getAsJsonObject();throw new IllegalArgumentException("Unknown Umamusume SKU");}
    public static void validateReceipt(JsonObject body){
        var known=offer(body.get("sku").getAsString());
        for(var key:List.of("name","entityKind","modelId","texture","tier","purchase","championTier","affixes","traits","modelFilesSha256","modelRelease"))if(!known.get(key).equals(body.get(key)))throw new IllegalStateException("Fixed companion receipt changed: "+key);
    }
    public static void verifyModelFiles(Path root,JsonObject body){
        validateReceipt(body);root=root.toAbsolutePath().normalize();
        String id=body.get("modelId").getAsString(),leaf=id.substring(id.indexOf('/')+1);
        // Verified runtime ID is 10_umamusume; the frozen archive still uses its category path.
        for(var folder:List.of(root.resolve(id).normalize(),root.resolve("赛马娘").resolve(leaf).normalize())){
            if(!folder.startsWith(root))throw new IllegalStateException("Model outside distribution root");
            try{boolean matches=true;for(var e:body.getAsJsonObject("modelFilesSha256").entrySet()){
                var file=folder.resolve(e.getKey()).normalize();
                if(!file.startsWith(folder)||!Files.isRegularFile(file)||!HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(file))).equals(e.getValue().getAsString())){matches=false;break;}
            }if(matches)return;
            }catch(java.io.IOException|java.security.NoSuchAlgorithmException failure){throw new IllegalStateException("Approved model files unavailable",failure);}
        }
        throw new IllegalStateException("赛马娘模型未分发或文件不一致，请稍后刷新");
    }
    public static String details(JsonObject body){return "强敌 "+body.get("tier").getAsInt()+" 档 · "+String.join(" / ",body.getAsJsonArray("traits").asList().stream().map(JsonElement::getAsString).toList())+" · 固定词条，随从增益沿用半效规则";}
}
