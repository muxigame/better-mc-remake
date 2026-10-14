package net.muxigame.minigames.horse;

import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Mob;
import net.minecraft.world.entity.animal.horse.Horse;
import net.minecraft.world.level.Level;

/** Optional installed TLM adapter; no bundled third-party classes, models or assets. */
public final class MaidRider {
    public static final String WINEFOX_SEAT="muxi_horse_winefox_seat_v1";
    private MaidRider(){}
    public static Mob create(ServerLevel level,Horse horse,String model,String texture){
        try {
            Class<?> type=Class.forName("com.github.tartaricacid.touhoulittlemaid.entity.passive.EntityMaid");
            Mob maid=(Mob)type.getConstructor(Level.class).newInstance(level);
            maid.addTag(HorsePilot.MARKER);
            maid.setPos(horse.getX(),horse.getY(),horse.getZ());maid.setNoAi(true);maid.setPersistenceRequired();
            maid.setInvulnerable(true);maid.setCustomName(Component.literal("原型骑手（外观暂选）"));
            // Component style is already synchronized by vanilla. Only this generated rider gets a seat marker.
            if(model.equals("wine_fox/01_taisho_maid"))maid.setCustomName(maid.getCustomName().copy().withStyle(style->style.withInsertion(WINEFOX_SEAT)));
            if(!model.isBlank()){
                type.getMethod("setYsmModel",String.class,String.class,Component.class).invoke(maid,model,texture,Component.literal("原型外观暂选"));
                type.getMethod("setIsYsmModel",boolean.class).invoke(maid,true);
            }
            if(!level.addFreshEntity(maid)||!maid.startRiding(horse,true)){maid.discard();throw new IllegalStateException("Maid could not mount horse");}
            return maid;
        }catch(ReflectiveOperationException failure){throw new IllegalStateException("Installed TLM 1.5.3 rider API required",failure);}
    }
}
