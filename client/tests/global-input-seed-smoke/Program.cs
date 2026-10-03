using System.Text.Json;
using BatterMC.Core;
using BatterMC.Protocol;
var audit=Path.GetFullPath(args[0]);Directory.CreateDirectory(audit);
using var json=JsonDocument.Parse(File.ReadAllText(args[1]));
var el=json.RootElement.GetProperty("overlays").EnumerateArray().Single(e=>e.GetProperty("path").GetString()=="options.txt");
var full=JsonSerializer.Deserialize<ConfigOverlaySpec>(el.GetRawText(),new JsonSerializerOptions{PropertyNameCaseInsensitive=true})!;
string[] keys=["key_key.swapOffhand","key_key.playerlist","key_key.tacz.interact.desc","key_key.toms_storage.open_terminal","key_key.muxi_game_core.challenge_wheel"];
string[] targets=["key.keyboard.tab","key.keyboard.tab:CONTROL","key.keyboard.f","key.keyboard.t:ALT","key.keyboard.b:ALT"];
string[] legacy=["key.keyboard.f","key.keyboard.tab","key.keyboard.o","key.keyboard.b:ALT","key.keyboard.b"];
int checks=0;void Check(bool pass,string name){checks++;if(!pass)throw new Exception(name);Console.WriteLine("PASS "+name);}
for(int i=0;i<keys.Length;i++)Check(full.SeedKeys[keys[i]].GetString()==targets[i]&&!full.Enforce.ContainsKey(keys[i]),"one-time input seed "+keys[i]);
// Exercise the unchanged production launcher against only the five new seed revisions.
var spec=new ConfigOverlaySpec{Path=full.Path,Format=full.Format,CreateIfMissing=full.CreateIfMissing,SeedKeys=keys.ToDictionary(k=>k,k=>full.SeedKeys[k])};
var paths=LauncherPaths.At(Path.Combine(audit,"old-client"));var file=paths.ResolveGameFile(spec.Path);Directory.CreateDirectory(Path.GetDirectoryName(file)!);
var original="# keep comment\r\nlang:zh_cn\r\nkey_key.forward:key.keyboard.up\r\nkey_key.custom.action:key.keyboard.p:SHIFT\r\nkey_key.sophisticatedbackpacks.open_backpack:key.keyboard.b\r\nunknownModChoice:keep\r\nresourcePacks:[\"vanilla\",\"file/custom.zip\"]\r\n"+string.Join("\r\n",keys.Select((k,i)=>k+":"+legacy[i]))+"\r\n";
File.WriteAllText(file,original);var state=new LocalState();
// A legacy launcher has already recorded previous seed values, including Alt+B storage.
for(int i=0;i<keys.Length;i++)state.MarkOverlaySeed(spec.Path,keys[i],JsonSerializer.Serialize(legacy[i]));
var r=ConfigOverlay.Apply(spec,paths,state);var migrated=File.ReadAllText(file);
var expected=original;for(int i=0;i<keys.Length;i++)expected=expected.Replace(keys[i]+":"+legacy[i]+"\r\n",keys[i]+":"+targets[i]+"\r\n");
Check(r.Error==null&&r.Changed==5&&migrated==expected,"old client migrates exactly five input rows with unrelated custom keys and all other text preserved");
state.Save(paths.StateFile);state=LocalState.Load(paths.StateFile);
for(int i=0;i<keys.Length;i++)Check(!state.NeedsOverlaySeed(spec.Path,keys[i],JsonSerializer.Serialize(targets[i])),"revision persisted "+keys[i]);
r=ConfigOverlay.Apply(spec,paths,state);Check(r.Changed==0&&File.ReadAllText(file)==migrated,"second launch is idempotent");
var custom=migrated.Replace("key_key.swapOffhand:key.keyboard.tab\r\n","key_key.swapOffhand:key.keyboard.g\r\n").Replace("key_key.tacz.interact.desc:key.keyboard.f\r\n","key_key.tacz.interact.desc:key.mouse.2\r\n");
File.WriteAllText(file,custom);r=ConfigOverlay.Apply(spec,paths,state);Check(r.Changed==0&&File.ReadAllText(file)==custom,"later player remapping is preserved after seed revision");
File.WriteAllText(file,original);r=ConfigOverlay.Apply(spec,paths,new LocalState());Check(r.Error==null&&File.ReadAllText(file)==expected,"unrecorded legacy installation also receives five new seed values");
var result=JsonSerializer.Serialize(new{success=true,checks,scope="actual launcher ConfigOverlay and LocalState; isolated old-client file fixtures",realInput=false});File.WriteAllText(Path.Combine(audit,"result.json"),result);Console.WriteLine(result);
