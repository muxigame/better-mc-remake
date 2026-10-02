using BatterMC.Core;
using BatterMC.Protocol;
using System.Text.Json;
using System.Text.Json.Nodes;

var pack = JsonNode.Parse(File.ReadAllText(args[0]))!.AsObject();
var overlay = pack["overlays"]!.AsArray().Select(n => n!.AsObject())
    .Single(n => n["path"]!.GetValue<string>() == "CustomSkinLoader/CustomSkinLoader.json");
var spec = JsonSerializer.Deserialize<ConfigOverlaySpec>(overlay.ToJsonString(),
    new JsonSerializerOptions { PropertyNameCaseInsensitive = true })!;
void Check(bool ok, string message) { if (!ok) throw new Exception(message); }
foreach (var old in new[] {
    """{"loadlist":[{"name":"old-local","type":"Legacy","skin":"same.png"}],"enableLocalProfileCache":true,"threadPoolSize":3,"enableCape":false,"customSetting":"keep"}""",
    "{}",
    "" }) {
    var repaired = ConfigOverlay.ApplyJson(old, spec.Enforce, out var changed);
    var config = JsonNode.Parse(repaired)!;
    Check(config["loadlist"]!.AsArray().Count == 1, "one authoritative source");
    Check(config["loadlist"]![0]!["root"]!.GetValue<string>() == "https://mc.muxigame.com/api/v1/skins/csl/", "UID-specific API root");
    Check(!config["enableLocalProfileCache"]!.GetValue<bool>(), "old local profile cannot override UID skins");
    if (old.Contains("customSetting")) {
        Check(config["threadPoolSize"]!.GetValue<int>() == 3 && !config["enableCape"]!.GetValue<bool>(), "player preferences retained");
        Check(config["customSetting"]!.GetValue<string>() == "keep", "unrelated keys retained");
    }
    var again = ConfigOverlay.ApplyJson(repaired, spec.Enforce, out var repeated);
    Check(again == repaired && repeated == 0, "launch refresh is idempotent");
}
Console.WriteLine("PASS: actual manifest + launcher overlay repair stale/missing skin source, retain unrelated preferences, and remain idempotent.");
var optionsOverlay = pack["overlays"]!.AsArray().Select(n => n!.AsObject())
    .Single(n => n["path"]!.GetValue<string>() == "options.txt");
var optionsSpec = JsonSerializer.Deserialize<ConfigOverlaySpec>(optionsOverlay.ToJsonString(),
    new JsonSerializerOptions { PropertyNameCaseInsensitive = true })!;
var options = "resourcePacks:[\"vanilla\",\"file/PCL2 Skin.zip\",\"file/other.zip\",\"PCL2 Skin.zip\"]\nfov:0\n";
var cleaned = ConfigOverlay.RemoveListEntries(options, optionsSpec.RemoveFromList, out var removed);
Check(!cleaned.Contains("PCL2 Skin.zip") && removed == 2, "only known global PCL skin pack disabled");
Check(cleaned.Contains("file/other.zip") && cleaned.Contains("fov:0"), "other packs/options retained");
Check(ConfigOverlay.RemoveListEntries(cleaned, optionsSpec.RemoveFromList, out var removedAgain) == cleaned && removedAgain == 0, "resource-pack migration idempotent");
Console.WriteLine("PASS: PCL global default-skin pack disabled by exact entries; unrelated packs retained; no files deleted.");

// Unused file/state adapters allow exercising the real production JSON engine
// without launching the application or accessing any user's installation.
namespace BatterMC.Core {
    public sealed class LauncherPaths { public string ResolveGameFile(string path) => throw new NotSupportedException(); }
    public sealed class LocalState {
        public bool NeedsOverlaySeed(string path, string key, string value) => throw new NotSupportedException();
        public void MarkOverlaySeed(string path, string key, string value) => throw new NotSupportedException();
    }
    public static class AtomicFile { public static void WriteAllText(string path, string value) => throw new NotSupportedException(); }
    public static class Log { public static void Warn(string message) {} public static void Info(string message) {} }
}
