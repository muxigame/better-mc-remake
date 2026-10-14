package net.muxigame.minigames.horse;

import java.util.*;

/** Server-derived standings. NPCs compete for rank but have no platform account. */
public final class HorseScores {
    public static final int FIRST_POINTS=10;
    public record Run(UUID horse,int ticks,boolean finished) {
        public Run {Objects.requireNonNull(horse);if(ticks<0||finished&&ticks==0)throw new IllegalArgumentException("Invalid race time");}
    }
    public record Standing(UUID horse,int rank,int score,int seconds,boolean win) {}
    public static List<Standing> calculate(Collection<Run> input) {
        if(input.isEmpty()||input.size()>HorseRaceGrid.FIELD_SIZE)throw new IllegalArgumentException("Race must contain 1..8 entrants");
        var seen=new HashSet<UUID>();for(var run:input)if(!seen.add(run.horse()))throw new IllegalArgumentException("Duplicate entrant");
        var runs=input.stream().sorted(Comparator.comparingInt((Run r)->r.finished()?0:1).thenComparingInt(r->r.finished()?r.ticks():Integer.MAX_VALUE).thenComparing(r->r.horse().toString())).toList();
        int best=runs.getFirst().finished()?runs.getFirst().ticks():0;
        var standings=new ArrayList<Standing>();
        for(int i=0;i<runs.size();i++){
            var run=runs.get(i);boolean win=i==0&&run.finished();int score=1;
            if(win)score=FIRST_POINTS;
            else if(run.finished())score=Math.clamp((int)Math.floor(FIRST_POINTS*(.65*(runs.size()-i)/runs.size()+.35*best/run.ticks())),1,FIRST_POINTS-1);
            standings.add(new Standing(run.horse(),i+1,score,(int)Math.min(Integer.MAX_VALUE,((long)run.ticks()+19)/20),win));
        }
        return List.copyOf(standings);
    }
    private HorseScores(){}
}
