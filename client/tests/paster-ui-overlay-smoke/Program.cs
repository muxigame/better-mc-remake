using System.Text.Json;
using BatterMC.Core;
using BatterMC.Protocol;
var audit=Path.GetFullPath(args[0]);
using var json=JsonDocument.Parse(File.ReadAllText(args[1]));
const string relative="config/PasterDream-Client.toml", key="HUD/enable mod ui";
var specs=json.RootElement.GetProperty("overlays").EnumerateArray().Where(e=>e.GetProperty("path").GetString()==relative).Select(e=>JsonSerializer.Deserialize<ConfigOverlaySpec>(e.GetRawText(),new JsonSerializerOptions{PropertyNameCaseInsensitive=true})!).ToArray();
void Check(bool pass,string name){if(!pass)throw new Exception(name);Console.WriteLine("PASS "+name);}
Check(specs.Length==2 && specs[1].SeedKeys[key].ValueKind==JsonValueKind.False,"UI false migration is registered");
var original="# keep comment\n[HUD]\n\"enable mod ui\" = true\n\"paster health hud\" = false\n\"san hud\" = true\n[Music]\nbgm = true\n";
foreach(var previous in new[]{"unrecorded","true","false"}){
 var paths=LauncherPaths.At(Path.Combine(audit,previous));var file=paths.ResolveGameFile(relative);Directory.CreateDirectory(Path.GetDirectoryName(file)!);File.WriteAllText(file,original);
 var state=new LocalState();if(previous!="unrecorded")state.MarkOverlaySeed(relative,key,previous);
 var results=ConfigOverlay.ApplyAll(specs,paths,state);
 Check(results.All(x=>x.Error==null) && File.ReadAllText(file)==original.Replace("\"enable mod ui\" = true","\"enable mod ui\" = false"),previous+": only UI changes");
 state.Save(paths.StateFile);state=LocalState.Load(paths.StateFile);
 Check(!state.NeedsOverlaySeed(relative,key,"false"),previous+": migration ledger persists");
 Check(ConfigOverlay.ApplyAll(specs,paths,state).All(x=>x.Changed==0),previous+": repeat is idempotent");
 File.Delete(file);ConfigOverlay.ApplyAll(specs,paths,state);
 Check(File.ReadAllText(file).Contains("\"enable mod ui\" = false") && !File.ReadAllText(file).Contains("health"),previous+": deleted config restores only UI despite old ledger");
}
var fresh=LauncherPaths.At(Path.Combine(audit,"fresh"));var freshState=new LocalState();ConfigOverlay.ApplyAll(specs,fresh,freshState);
var content=File.ReadAllText(fresh.ResolveGameFile(relative));Check(content.Trim()=="[HUD]\n\"enable mod ui\" = false","fresh install initializes only UI");
Console.WriteLine("All PasterDream UI migration checks passed.");
