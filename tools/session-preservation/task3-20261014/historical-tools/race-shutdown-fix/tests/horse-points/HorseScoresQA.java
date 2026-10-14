import net.muxigame.minigames.horse.HorseScores;
import java.util.*;
public final class HorseScoresQA {
    static int checks;
    static UUID id(int n){return new UUID(0,n);}
    static void check(boolean value){if(!value)throw new AssertionError("Score check "+checks);checks++;}
    static HorseScores.Run run(int n,int ticks,boolean finished){return new HorseScores.Run(id(n),ticks,finished);}
    public static void main(String[] args){
        var runs=List.of(run(1,100,true),run(2,120,true),run(3,160,true),run(4,200,true),run(5,1200,false));
        var result=HorseScores.calculate(runs);
        check(result.getFirst().score()==10&&result.getFirst().win());
        for(int i=1;i<5;i++)check(result.get(i).score()>=1&&result.get(i).score()<10&&!result.get(i).win());
        check(result.getLast().score()==1&&result.getLast().seconds()==60);
        var shuffled=new ArrayList<>(runs);Collections.reverse(shuffled);check(result.equals(HorseScores.calculate(shuffled)));
        check(HorseScores.calculate(List.of(run(2,100,true),run(1,100,true))).getFirst().horse().equals(id(1)));
        check(HorseScores.calculate(List.of(run(1,100,true))).getFirst().score()==10);
        check(HorseScores.calculate(List.of(run(1,300,false),run(2,200,false))).stream().allMatch(s->s.score()==1&&!s.win()));
        var eight=new ArrayList<HorseScores.Run>();for(int n=1;n<=8;n++)eight.add(run(n,100+n*20,n<8));
        var full=HorseScores.calculate(eight);check(full.size()==8&&full.getFirst().score()==10&&full.getLast().score()==1);
        check(full.stream().allMatch(v->v.score()>=1&&v.score()<=10));
        var nine=new ArrayList<>(eight);nine.add(run(9,300,true));try{HorseScores.calculate(nine);throw new AssertionError("Ninth entrant accepted");}catch(IllegalArgumentException expected){checks++;}
        int previous=10;for(int time=101;time<=10000;time+=17){int score=HorseScores.calculate(List.of(run(1,100,true),run(2,time,true))).getLast().score();check(score<=previous&&score>=1);previous=score;}
        for(var invalid:List.of(List.<HorseScores.Run>of(),List.of(run(1,100,true),run(1,200,true)))){try{HorseScores.calculate(invalid);throw new AssertionError();}catch(IllegalArgumentException expected){checks++;}}
        try{run(1,0,true);throw new AssertionError();}catch(IllegalArgumentException expected){checks++;}
        check(HorseScores.calculate(List.of(run(1,Integer.MAX_VALUE,true))).getFirst().seconds()==107374183);
        System.out.println("HorseScoresQA: "+checks+" checks passed");
    }
}
