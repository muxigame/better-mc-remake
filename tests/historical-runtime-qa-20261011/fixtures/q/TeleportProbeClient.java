package net.muxigame.localdebug;
import com.google.gson.*;
import net.minecraft.client.Minecraft;
import net.minecraft.resources.*;
import net.minecraft.core.registries.Registries;
import net.minecraft.world.level.Level;
import net.muxigame.core.client.waystones.*;
import net.muxigame.core.feature.waystones.*;
import net.muxigame.core.feature.waystones.network.*;
import net.neoforged.fml.common.Mod;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.client.event.ClientTickEvent;
import net.neoforged.neoforge.network.PacketDistributor;
import java.util.*;
@Mod(value="local_mc_debug_qa",dist=Dist.CLIENT)
public final class TeleportProbeClient {
 int last;final String role=System.getProperty("qa.local.role");static NetworkMapDetails.Snapshot saved;
 public TeleportProbeClient(){if(DebugFiles.enabled())NeoForge.EVENT_BUS.addListener(this::tick);}
 static Object field(Class<?> type,String name)throws Exception{var f=type.getDeclaredField(name);f.setAccessible(true);return f.get(null);}
 static JsonObject state(Minecraft mc)throws Exception{
  var row=new JsonObject();row.addProperty("mouseX",mc.mouseHandler.xpos());row.addProperty("mouseY",mc.mouseHandler.ypos());row.addProperty("guiWidth",mc.getWindow().getGuiScaledWidth());row.addProperty("guiHeight",mc.getWindow().getGuiScaledHeight());row.addProperty("guiScale",mc.getWindow().getGuiScale());row.addProperty("screen",mc.screen==null?"NONE":mc.screen.getClass().getName());row.addProperty("dimension",mc.level.dimension().location().toString());row.addProperty("near",WaystoneMapClient.nearSource());row.addProperty("entranceHint",WaystoneMapClient.entranceHint());row.addProperty("x",mc.player.getX());row.addProperty("y",mc.player.getY());row.addProperty("z",mc.player.getZ());row.addProperty("xpLevel",mc.player.experienceLevel);row.addProperty("xpTotal",mc.player.totalExperience);
  row.addProperty("viewDimension",String.valueOf(NativeStoneRenderer.viewDimension()));row.addProperty("query",String.valueOf(field(WaystoneMapClient.class,"query")));row.addProperty("pending",String.valueOf(field(WaystoneMapClient.class,"pending")));row.addProperty("receivedAt",(Integer)field(WaystoneMapClient.class,"receivedAt"));row.addProperty("tick",mc.player.tickCount);
  row.add("details",new Gson().toJsonTree(field(WaystoneMapClient.class,"details")));
  var session=xaero.map.WorldMapSession.getCurrentSession();if(session!=null&&session.isUsable()){var world=session.getMapProcessor().getMapWorld();var options=xaero.map.gui.GuiMapSwitching.getSortedDimensionOptions(world.getCurrentDimension());var dims=new JsonArray();for(var d:options.values)dims.add(d.location().toString());row.add("dropdown",dims);row.addProperty("selected",options.selected);}
  row.add("markers",new Gson().toJsonTree(field(StoneMarkerLayout.class,"previous")));
  if(mc.screen instanceof xaero.map.gui.GuiMap map){var opts=new JsonArray();for(var o:map.getRightClickOptions()){var x=new JsonObject();x.addProperty("label",o.getDisplayName().getString());opts.add(x);}row.add("rightClickOptions",opts);
   var f=xaero.map.gui.GuiMap.class.getDeclaredField("rightClickMenu");f.setAccessible(true);var menu=(xaero.map.gui.dropdown.rightclick.GuiRightClickMenu)f.get(map);
   if(menu!=null){row.addProperty("menuTargetClass",menu.getTarget().getClass().getName());var options=menu.getTarget().getRightClickOptions();var menuRows=new JsonArray();for(int i=0;i<options.size();i++){var o=options.get(i);var x=new JsonObject();x.addProperty("index",i);x.addProperty("label",o.getDisplayName().getString());x.addProperty("active",o.isActive());menuRows.add(x);}row.add("menuActionOptions",menuRows);
    if(menu.getTarget() instanceof xaero.map.element.HoveredMapElementHolder<?,?> holder&&holder.getElement() instanceof xaero.map.mods.gui.Waypoint waypoint)row.addProperty("menuTargetUuid",String.valueOf(StoneWaypointIdentity.get(waypoint.getOriginal())));
   }
  }
  return row;
 }
 void tick(ClientTickEvent.Post e){try{var c=DebugFiles.read("teleport-"+role+".json");if(c==null||c.get("id").getAsInt()<=last)return;last=c.get("id").getAsInt();var out=DebugFiles.result(last,role);try{var mc=Minecraft.getInstance();if(mc.player==null)throw new IllegalStateException("absent");switch(c.get("action").getAsString()){
  case "observe"->{}
  case "poll"->{WaystoneMapClient.poll();}
  case "open"->{var s=xaero.map.WorldMapSession.getCurrentSession();mc.setScreen(new xaero.map.gui.GuiMap(null,null,s.getMapProcessor(),mc.player));}
  case "close"->{mc.setScreen(null);}
  case "click"->{mc.screen.mouseClicked(c.get("x").getAsDouble(),c.get("y").getAsDouble(),c.get("button").getAsInt());mc.screen.mouseReleased(c.get("x").getAsDouble(),c.get("y").getAsDouble(),c.get("button").getAsInt());}
  case "scroll"->{mc.screen.mouseScrolled(c.get("x").getAsDouble(),c.get("y").getAsDouble(),0,c.get("amount").getAsDouble());}
  case "key"->{mc.screen.keyPressed(c.get("key").getAsInt(),0,0);}
  case "cursor"->{double x=c.get("x").getAsDouble()*mc.getWindow().getGuiScale(),y=c.get("y").getAsDouble()*mc.getWindow().getGuiScale();var m=net.minecraft.client.MouseHandler.class.getDeclaredMethod("onMove",long.class,double.class,double.class);m.setAccessible(true);m.invoke(mc.mouseHandler,mc.getWindow().getWindow(),x,y);out.addProperty("assistedNativeMouseHandler",true);}
  case "mouse"->{var m=net.minecraft.client.MouseHandler.class.getDeclaredMethod("onPress",long.class,int.class,int.class,int.class);m.setAccessible(true);m.invoke(mc.mouseHandler,mc.getWindow().getWindow(),c.get("button").getAsInt(),c.get("pressed").getAsBoolean()?1:0,0);out.addProperty("assistedNativeMouseHandler",true);}
  case "keyboard"->{mc.keyboardHandler.keyPress(mc.getWindow().getWindow(),c.get("key").getAsInt(),0,c.get("pressed").getAsBoolean()?1:0,0);out.addProperty("assistedNativeKeyboardHandler",true);}
  case "menu-select"->{var f=xaero.map.gui.GuiMap.class.getDeclaredField("rightClickMenu");f.setAccessible(true);var menu=(xaero.map.gui.dropdown.rightclick.GuiRightClickMenu)f.get(mc.screen);if(menu==null)throw new IllegalStateException("no native menu");menu.selectId(c.get("index").getAsInt(),true);out.addProperty("nativeMenuSelection",true);}


  case "warp"->{PacketDistributor.sendToServer(new WaystoneMapNetwork.Warp(UUID.randomUUID(),UUID.fromString(c.get("uid").getAsString())));}
  case "ui-warp"->{UUID uid=UUID.fromString(c.get("uid").getAsString());var n=WaystoneMapClient.entry(uid);if(n==null)throw new IllegalStateException("entry absent");WaystoneMapClient.teleport(uid,ResourceKey.create(Registries.DIMENSION,n.dimension()),n.pos());}
  case "browse"->{var w=xaero.map.WorldMapSession.getCurrentSession().getMapProcessor().getMapWorld();w.setCustomDimensionId(ResourceKey.create(Registries.DIMENSION,ResourceLocation.parse(c.get("dimension").getAsString())));}
  case "save"->{saved=(NetworkMapDetails.Snapshot)field(WaystoneMapClient.class,"details");}
  case "stale"->{var m=WaystoneMapClient.class.getDeclaredMethod("receive",NetworkMapDetails.Snapshot.class);m.setAccessible(true);m.invoke(null,saved);out.addProperty("assistedStaleDeliveryFixture",true);}
  case "screenshot"->{net.minecraft.client.Screenshot.grab(mc.gameDirectory,c.get("filename").getAsString(),mc.getMainRenderTarget(),x->{});}
  default->throw new IllegalArgumentException("action");
 }out.add("state",state(mc));out.addProperty("ok",true);out.addProperty("physicalOSInput",false);
 }catch(Throwable t){t.printStackTrace();out.addProperty("ok",false);out.addProperty("error",t.toString());}DebugFiles.write("teleport-result-"+role+"-"+last+".json",out);}catch(Exception x){x.printStackTrace();}}
}