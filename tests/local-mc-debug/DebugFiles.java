package net.muxigame.localdebug;
import com.google.gson.*;
import java.nio.file.*;
import java.io.IOException;
import java.util.UUID;

/** The coordinator protocol is deliberately restricted to a marked private QA lab. */
final class DebugFiles {
    static final Path ROOT=Path.of(System.getProperty("qa.local.root", "missing-local-qa-root"));
    static final String RUN=System.getProperty("qa.local.runId", "");
    static boolean enabled(){
        if(RUN.isBlank())return false;
        try{var owner=JsonParser.parseString(Files.readString(ROOT.resolve("local-mc-owner.json"))).getAsJsonObject();return RUN.equals(owner.get("runId").getAsString())&&ROOT.toAbsolutePath().normalize().toString().equals(owner.get("instanceRoot").getAsString());}
        catch(Exception unavailable){return false;}
    }
    static JsonObject read(String name){try{var row=JsonParser.parseString(Files.readString(ROOT.resolve("coordinator").resolve(name))).getAsJsonObject();return row.has("runId")&&RUN.equals(row.get("runId").getAsString())?row:null;}catch(IOException|JsonParseException transientRead){return null;}}
    static void write(String name,JsonObject value)throws IOException{
        Path target=ROOT.resolve("coordinator").resolve(name),tmp=target.resolveSibling(target.getFileName()+"."+UUID.randomUUID()+".tmp");
        Files.writeString(tmp,new GsonBuilder().setPrettyPrinting().create().toJson(value));
        for(int attempt=0;;attempt++)try{Files.move(tmp,target,StandardCopyOption.REPLACE_EXISTING);return;}catch(AccessDeniedException busy){if(attempt>=4)throw busy;try{Thread.sleep(10);}catch(InterruptedException interrupted){Thread.currentThread().interrupt();throw busy;}}
    }
    static JsonObject result(int id,String role){var row=new JsonObject();row.addProperty("id",id);row.addProperty("role",role);row.addProperty("runId",RUN);return row;}
    private DebugFiles(){}
}
