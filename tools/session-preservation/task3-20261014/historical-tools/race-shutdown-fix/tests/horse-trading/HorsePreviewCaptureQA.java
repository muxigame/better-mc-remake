package net.muxigame.minigames.horsepreviewqa;

import com.google.gson.*;
import com.mojang.blaze3d.pipeline.TextureTarget;
import com.mojang.blaze3d.platform.NativeImage;
import com.mojang.blaze3d.systems.RenderSystem;
import com.mojang.blaze3d.vertex.*;
import net.minecraft.client.*;
import net.minecraft.client.gui.GuiGraphics;
import net.minecraft.client.gui.screens.*;
import net.minecraft.client.gui.screens.inventory.InventoryScreen;
import net.minecraft.client.renderer.MultiBufferSource;
import net.minecraft.core.registries.Registries;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.world.Difficulty;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.animal.horse.Horse;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.item.*;
import net.minecraft.world.level.*;
import net.minecraft.world.level.levelgen.*;
import net.minecraft.world.level.levelgen.presets.WorldPresets;
import net.muxigame.minigames.horse.HorseTrading;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.client.event.ClientTickEvent;
import org.joml.*;
import org.lwjgl.opengl.*;
import java.nio.file.*;

/** Actual vanilla HorseRenderer to PNG. Private, unsaved entities; no YSM rider or world assets. */
@EventBusSubscriber(modid="muxi_horse_racing",value=Dist.CLIENT)
public final class HorsePreviewCaptureQA {
    private static int ticks,index,closingTicks;private static boolean starting,closing,done;
    private static final JsonObject report=new JsonObject();private static final JsonArray renders=new JsonArray();
    private static JsonArray catalog;
    private static void finish(boolean pass,String error)throws Exception{
        report.addProperty("success",pass);report.addProperty("error",error);report.add("renders",renders);
        report.addProperty("renderer","net.minecraft.client.renderer.entity.HorseRenderer");report.addProperty("YSM",false);report.addProperty("worldEntitiesSpawned",0);
        report.addProperty("productionWrites",false);Files.writeString(Path.of("preview-result.json"),new GsonBuilder().setPrettyPrinting().create().toJson(report));
        closing=true;var mc=Minecraft.getInstance();if(mc.level!=null)mc.level.disconnect();mc.disconnect(new TitleScreen());
    }
    private static JsonObject render(Minecraft mc,JsonObject body,String sku)throws Exception{
        RenderSystem.assertOnRenderThread();Horse h=EntityType.HORSE.create(mc.level);if(h==null)throw new IllegalStateException("Horse unavailable");
        h.setAge(0);h.setTamed(true);h.setOwnerUUID(mc.player.getUUID());h.setNoAi(true);
        var nbt=new CompoundTag();h.saveWithoutId(nbt);nbt.putInt("Variant",body.get("variant").getAsInt()|(body.get("markings").getAsInt()<<8));h.load(nbt);
        h.getAttribute(Attributes.MOVEMENT_SPEED).setBaseValue(body.get("speed").getAsDouble());h.getAttribute(Attributes.JUMP_STRENGTH).setBaseValue(body.get("jump").getAsDouble());h.getAttribute(Attributes.MAX_HEALTH).setBaseValue(body.get("health").getAsDouble());h.setHealth(body.get("health").getAsFloat());h.equipSaddle(new ItemStack(Items.SADDLE),null);
        // equipSaddle's network flag is only synchronized on a server level.
        // This private display entity never joins a world: set the corresponding
        // vanilla flag as an inventory preview would, while keeping its saddle stack.
        var flag=net.minecraft.world.entity.animal.horse.AbstractHorse.class.getDeclaredMethod("setFlag",int.class,boolean.class);flag.setAccessible(true);flag.invoke(h,4,true);
        h.yBodyRot=h.yBodyRotO=135;h.yHeadRot=h.yHeadRotO=135;h.setYRot(135);h.yRotO=135;
        if(!mc.getEntityRenderDispatcher().getRenderer(h).getClass().getName().equals("net.minecraft.client.renderer.entity.HorseRenderer"))throw new IllegalStateException("Expected actual vanilla HorseRenderer");
        if(h.getVariant().getId()!=body.get("variant").getAsInt()||h.getMarkings().getId()!=body.get("markings").getAsInt()||!h.isSaddled())throw new IllegalStateException("Catalog appearance mismatch");
        int framebuffer=GL11.glGetInteger(GL30.GL_FRAMEBUFFER_BINDING);int[] viewport=new int[4];GL11.glGetIntegerv(GL11.GL_VIEWPORT,viewport);
        boolean depth=GL11.glIsEnabled(GL11.GL_DEPTH_TEST),blend=GL11.glIsEnabled(GL11.GL_BLEND),cull=GL11.glIsEnabled(GL11.GL_CULL_FACE),scissor=GL11.glIsEnabled(GL11.GL_SCISSOR_TEST);int[] scissorBox=new int[4];GL11.glGetIntegerv(GL11.GL_SCISSOR_BOX,scissorBox);
        var shader=RenderSystem.getShader();float[] color=RenderSystem.getShaderColor().clone();TextureTarget target=null;
        RenderSystem.backupProjectionMatrix();RenderSystem.getModelViewStack().pushMatrix();
        JsonObject row=new JsonObject();row.addProperty("sku",sku);row.add("body",body.deepCopy());row.addProperty("variantName",h.getVariant().getSerializedName());row.addProperty("markingsName",h.getMarkings().name());row.addProperty("baseTexture",mc.getEntityRenderDispatcher().getRenderer(h).getTextureLocation(h).toString());
        try(var vertices=new ByteBufferBuilder(262144)){
            target=new TextureTarget(256,192,true,Minecraft.ON_OSX);target.setClearColor(0,0,0,0);target.clear(Minecraft.ON_OSX);target.bindWrite(true);
            RenderSystem.disableScissor();RenderSystem.getModelViewStack().identity();RenderSystem.applyModelViewMatrix();RenderSystem.setProjectionMatrix(new Matrix4f().setOrtho(0,256,192,0,1000,21000),VertexSorting.ORTHOGRAPHIC_Z);RenderSystem.setShaderColor(1,1,1,1);
            var gui=new GuiGraphics(mc,MultiBufferSource.immediate(vertices));gui.pose().translate(0,0,-11000);
            InventoryScreen.renderEntityInInventory(gui,128,174,57,new Vector3f(),new Quaternionf().rotationZ((float)java.lang.Math.PI).rotateX(.15f),new Quaternionf().rotationX(.15f),h);gui.flush();
            try(var pixels=new NativeImage(256,192,false)){
                RenderSystem.activeTexture(GL13.GL_TEXTURE0);RenderSystem.bindTexture(target.getColorTextureId());pixels.downloadTexture(0,false);pixels.flipY();
                int visible=0,minX=256,minY=192,maxX=-1,maxY=-1;for(int y=0;y<192;y++)for(int x=0;x<256;x++)if((pixels.getPixelRGBA(x,y)>>>24)>8){visible++;minX=java.lang.Math.min(minX,x);minY=java.lang.Math.min(minY,y);maxX=java.lang.Math.max(maxX,x);maxY=java.lang.Math.max(maxY,y);}
                if(visible<1000||minX<2||minY<2||maxX>253||maxY>189)throw new IllegalStateException("Empty or clipped native horse: "+visible+" bounds "+minX+","+minY+","+maxX+","+maxY);
                Path file=Path.of("preview-images",sku+".png");Files.createDirectories(file.getParent());pixels.writeToFile(file);row.addProperty("png",file.toString());row.addProperty("visiblePixels",visible);row.addProperty("bounds",minX+","+minY+","+maxX+","+maxY);
            }
        }finally{
            if(target!=null)target.destroyBuffers();GL30.glBindFramebuffer(GL30.GL_FRAMEBUFFER,framebuffer);RenderSystem.viewport(viewport[0],viewport[1],viewport[2],viewport[3]);RenderSystem.restoreProjectionMatrix();RenderSystem.getModelViewStack().popMatrix();RenderSystem.applyModelViewMatrix();
            if(depth)RenderSystem.enableDepthTest();else RenderSystem.disableDepthTest();if(blend)RenderSystem.enableBlend();else RenderSystem.disableBlend();if(cull)RenderSystem.enableCull();else RenderSystem.disableCull();RenderSystem.enableScissor(scissorBox[0],scissorBox[1],scissorBox[2],scissorBox[3]);if(!scissor)RenderSystem.disableScissor();RenderSystem.setShader(()->shader);RenderSystem.setShaderColor(color[0],color[1],color[2],color[3]);
        }
        var card=new JsonObject();HorseTrading.describePreview(card,body,sku);if(!card.getAsJsonObject("horseAppearance").get("variant").equals(body.get("variant")))throw new IllegalStateException("Native UI descriptor mismatch");row.add("card",card);return row;
    }
    @SubscribeEvent public static void tick(ClientTickEvent.Post event){if(!Boolean.getBoolean("muxi.horse.previewCaptureQa")||done)return;var mc=Minecraft.getInstance();try{
        ticks++;if(closing){if(mc.level!=null||mc.getSingleplayerServer()!=null||++closingTicks<30)return;done=true;mc.stop();return;}
        if(ticks>3600||Files.exists(Path.of("request-normal-close.json")))throw new IllegalStateException("Bounded private capture deadline");if(mc.getOverlay()!=null)return;
        if(!starting){if(!(mc.screen instanceof TitleScreen))return;if(!Files.exists(Path.of("HORSE_QA_ONLY")))throw new IllegalStateException("Private capture marker missing");try(var in=HorsePreviewCaptureQA.class.getResourceAsStream("/horse_trade_catalog_v1.json")){catalog=JsonParser.parseString(new String(in.readAllBytes(),java.nio.charset.StandardCharsets.UTF_8)).getAsJsonObject().getAsJsonArray("catalog");}
            starting=true;mc.createWorldOpenFlows().createFreshLevel("horse-preview-private",new LevelSettings("Horse preview only",GameType.CREATIVE,false,Difficulty.PEACEFUL,false,new GameRules(),WorldDataConfiguration.DEFAULT),new WorldOptions(7331L,false,false),a->a.registryOrThrow(Registries.WORLD_PRESET).getHolderOrThrow(WorldPresets.FLAT).value().createWorldDimensions(),mc.screen);return;}
        if(mc.level==null||mc.player==null||mc.screen instanceof ReceivingLevelScreen||ticks%10!=0)return;
        if(index<catalog.size()){var body=catalog.get(index).getAsJsonObject();renders.add(render(mc,body,body.get("sku").getAsString()));index++;return;}
        if(index==catalog.size()){var generic=catalog.get(0).getAsJsonObject().deepCopy();generic.add("sku",JsonNull.INSTANCE);generic.addProperty("variant",0);generic.addProperty("markings",0);renders.add(render(mc,generic,"generic"));index++;return;}
        var pool=catalog.get(0).getAsJsonObject().deepCopy();pool.add("sku",JsonNull.INSTANCE);pool.addProperty("armored",true);var card=new JsonObject();HorseTrading.describePreview(card,pool,"pool-test");if(card.getAsJsonObject("preview").get("exactAppearance").getAsBoolean()||!card.getAsJsonObject("horseAppearance").get("armored").getAsBoolean())throw new IllegalStateException("Generic stock falsely described exact preview");report.addProperty("poolGenericContractVerified",true);finish(true,"");
    }catch(Throwable failure){try{finish(false,failure.toString());}catch(Exception ignored){done=true;mc.stop();}}}
}
