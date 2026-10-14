package net.muxigame.minigames.horse;
import java.util.*;

/** Versioned course catalog. A new terrain/layout revision gets a new history namespace. */
public final class HorseCourses {
    public record Definition(String id,String version,String title,String theme,boolean hills,boolean loop) {
        public Definition{if(!id.matches("[a-z0-9-]{1,48}")||!version.matches("[a-zA-Z0-9.-]{1,24}"))throw new IllegalArgumentException("Invalid course identity");}
    }
    private static final Map<String,Definition> COURSES=new LinkedHashMap<>();
    static {
        add(new Definition(HorseStadium.ID,"3","木溪椭圆竞技场","stadium",false,true));
        add(new Definition("prototype-loop","1","基础环形障碍场","plains",false,true));
        for(String theme:List.of("plains","snow","desert","jungle"))add(new Definition("hills-"+theme,"1","丘陵障碍 · "+theme,theme,true,true));
        add(new Definition("hills-straight","1","丘陵直线障碍","plains",true,false));
    }
    private HorseCourses(){}
    private static void add(Definition definition){if(COURSES.putIfAbsent(definition.id(),definition)!=null)throw new IllegalStateException("Duplicate course");}
    public static Definition get(String id){var value=COURSES.get(id);if(value==null)throw new IllegalArgumentException("Unknown horse course: "+id);return value;}
    public static List<Definition> list(){return List.copyOf(COURSES.values());}
    public static List<Definition> testTemplates(){return list().stream().filter(d->!d.id().equals(HorseStadium.ID)).toList();}
}
