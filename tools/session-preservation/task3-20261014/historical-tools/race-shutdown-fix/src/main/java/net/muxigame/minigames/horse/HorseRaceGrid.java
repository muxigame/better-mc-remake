package net.muxigame.minigames.horse;

/** Eight runners in one row, eight independent lanes inside the formal oval. */
public final class HorseRaceGrid {
    public static final int FIELD_SIZE=8,COLUMNS=8,MAX_PLAYERS=4;
    public static final double LANE_SPACING=1.9,ROW_SPACING=4.0;
    public static double lane(int slot){validate(slot);return (slot%COLUMNS-(COLUMNS-1)/2.0)*LANE_SPACING;}
    public static int row(int slot){validate(slot);return slot/COLUMNS;}
    public static int npcCount(int players){if(players<1||players>MAX_PLAYERS)throw new IllegalArgumentException("Invalid player count");return FIELD_SIZE-players;}
    private static void validate(int slot){if(slot<0||slot>=FIELD_SIZE)throw new IllegalArgumentException("Invalid start slot");}
    private HorseRaceGrid(){}
}
