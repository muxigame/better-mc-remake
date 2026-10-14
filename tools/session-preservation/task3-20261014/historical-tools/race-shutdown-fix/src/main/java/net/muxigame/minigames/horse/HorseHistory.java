package net.muxigame.minigames.horse;

import com.google.gson.*;
import java.io.IOException;
import java.nio.file.*;
import java.util.*;

/** Best time per source UUID and course version, with the horse name captured at race time. */
public final class HorseHistory {
    public record Result(UUID session,UUID horseId,String horseName,String course,String version,int ticks,int recoveries,boolean npc){}
    private final Path file;
    private final Map<String,Result> best=new HashMap<>();
    private static final Gson GSON=new GsonBuilder().setPrettyPrinting().create();
    public HorseHistory(Path file){
        this.file=file;
        if(Files.exists(file))try{for(var row:JsonParser.parseString(Files.readString(file)).getAsJsonArray()){
            Result result=GSON.fromJson(row,Result.class);validate(result);best.put(key(result),result);
        }}catch(Exception failure){throw new IllegalStateException("Refuse to overwrite unreadable horse history",failure);}
    }
    private static String key(Result r){return r.course()+"|"+r.version()+"|"+r.horseId();}
    private static void validate(Result r){if(r==null||r.session()==null||r.horseId()==null||r.horseName()==null||r.course()==null||r.version()==null||r.ticks()<=0||r.recoveries()<0)throw new IllegalArgumentException("Invalid horse result");}
    public void record(Result result){
        validate(result);String key=key(result);Result old=best.get(key);
        if(old!=null&&old.ticks()<=result.ticks())return;
        if(old==null&&best.size()>=2000)throw new IllegalStateException("Prototype horse history capacity reached");
        var proposed=new HashMap<>(best);proposed.put(key,result);
        try{
            Files.createDirectories(file.getParent());Path temporary=file.resolveSibling(file.getFileName()+".tmp");
            Files.writeString(temporary,GSON.toJson(proposed.values().stream().sorted(Comparator.comparing(HorseHistory::key)).toList()));
            Files.move(temporary,file,StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);
        }catch(IOException failure){throw new IllegalStateException("Horse result remains pending",failure);}
        best.clear();best.putAll(proposed);
    }
    public List<Result> top(String course,String version){return best.values().stream().filter(r->r.course().equals(course)&&r.version().equals(version)).sorted(Comparator.comparingInt(Result::ticks).thenComparing(r->r.horseId().toString())).limit(10).toList();}
}
