package net.muxigame.minigames.horse;

/** Original deterministic server rules. No external game's numerical values. */
public final class HorsePace {
    public enum Style { LEAD, FRONT, MIDDLE, CHASE; public String label(){return switch(this){case LEAD->"领跑";case FRONT->"前列";case MIDDLE->"中段";case CHASE->"后追";};} }
    public final Style style;public final boolean skills;
    public double stamina=1,pace=1;
    public String feedback="";
    public int recoveryUses,curveUses,surgeUses;
    private int boostTicks,curveCooldown,feedbackTicks;
    public HorsePace(Style style,boolean skills){this.style=style;this.skills=skills;}
    public double tick(double progress,int rank,int runners,boolean curveExit){
        if(progress<0||progress>1||rank<1||rank>runners)throw new IllegalArgumentException("Invalid race state");
        if(feedbackTicks>0)feedbackTicks--;else feedback="";if(curveCooldown>0)curveCooldown--;
        double desired=switch(style){
            case LEAD -> progress<.65?1.08:1.0;
            case FRONT -> progress<.55?1.02:1.08;
            case MIDDLE -> progress<.55?.94:progress<.8?1.10:1.13;
            case CHASE -> progress<.7?.90:1.19;
        };
        int target=switch(style){case LEAD->1;case FRONT->Math.max(1,runners/3);case MIDDLE->Math.max(1,runners/2);case CHASE->progress<.7?runners:1;};
        if(rank>target)desired+=.025;
        stamina=Math.max(0,stamina-(.00010+Math.max(0,pace-1)*.0007));
        if(skills){
            if(stamina<.55&&progress<.85&&recoveryUses==0){stamina=Math.min(1,stamina+.18);recoveryUses++;feedback="rhythm_recovery";feedbackTicks=40;}
            if(curveExit&&curveCooldown==0&&stamina>.2){boostTicks=Math.max(boostTicks,30);curveCooldown=400;curveUses++;feedback="curve_exit";feedbackTicks=40;}
            if(progress>=.85&&surgeUses==0&&stamina>.15){boostTicks=Math.max(boostTicks,80);stamina-=.15;surgeUses++;feedback="final_surge";feedbackTicks=40;}
        }
        if(boostTicks>0){boostTicks--;desired+=.08;}
        if(stamina<.15)desired=Math.min(desired,.90);
        desired=Math.max(.75,Math.min(1.25,desired));
        pace+=Math.max(-.004,Math.min(.004,desired-pace));return pace;
    }
}
