package dev.xkmc.modulargolems.init.registrate;
// TEST ONLY fixture for the three attribute holders consumed by the real JS.
// This class is never included in the compatibility mod or installed in the pack.
import net.minecraft.core.Holder;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.entity.ai.attributes.Attribute;
public final class GolemTypes {
    public static final Entry GOLEM_REGEN=new Entry("regen");
    public static final Entry GOLEM_SWEEP=new Entry("sweep");
    public static final Entry DYNAMIC_REDUCTION=new Entry("dynamic_reduction");
    public record Entry(String name) {
        public Holder<Attribute> holder(){return BuiltInRegistries.ATTRIBUTE.getHolder(ResourceLocation.fromNamespaceAndPath("modulargolems",name)).orElseThrow();}
    }
}
