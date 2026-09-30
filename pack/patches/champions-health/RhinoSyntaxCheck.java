import dev.latvian.mods.rhino.Context;
import dev.latvian.mods.rhino.ContextFactory;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Parse the actual production script with the installed game's Rhino engine. */
public final class RhinoSyntaxCheck {
    public static void main(String[] args) throws Exception {
        if (args.length != 1) throw new IllegalArgumentException("Expected script path");
        Path script = Path.of(args[0]);
        Context context = new ContextFactory().enter();
        context.compileString(Files.readString(script, StandardCharsets.UTF_8),
                script.getFileName().toString(), 1, null);
        System.out.println("PASS: installed Rhino 2101.2.8 parses production script");
    }
}
