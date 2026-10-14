using System.Security.Cryptography;
using System.Text.Json;
using BatterMC.Core;
using BatterMC.Protocol;
var root=Path.GetFullPath(args[0]);var manifest=PackManifest.FromJson(File.ReadAllText(args[1]))!;
var changed=manifest.Files.Where(f=>f.Url!=null && f.Url.Contains("/releases/1.4.39/",StringComparison.Ordinal)).ToList();
var seed=manifest.Files.Single(f=>f.Path=="config/abridged.json");manifest.Files=changed.Append(seed).ToList();manifest.Prune=new(){"mods"};manifest.Overlays.Clear();
var paths=LauncherPaths.At(root);paths.EnsureCreated();var state=new LocalState{InstalledPackVersion="1.4.38"};using var dl=new Downloader(parallel:5);var engine=new SyncEngine(paths,state,new LauncherSettings(),dl);await engine.FetchManifestAsync("https://mc.muxigame.com",CancellationToken.None);
foreach(var f in changed){var p=paths.ResolveGameFile(f.Path);Directory.CreateDirectory(Path.GetDirectoryName(p)!);File.WriteAllText(p,"stale bytes");state.SeededFiles.Add(f.Path);state.InstalledFiles.Add(f.Path);}
foreach(var name in new[]{"muxi-music-compat-0.4.1-repair-candidate.2.jar","muxi-terminal-0.2.4-window-ui.3.jar"}){var rel="mods/"+name;File.WriteAllText(paths.ResolveGameFile(rel),"old tracked");state.InstalledFiles.Add(rel);}
var sp=paths.ResolveGameFile(seed.Path);Directory.CreateDirectory(Path.GetDirectoryName(sp)!);File.WriteAllText(sp,"user custom unchanged seed");state.SeedRevisions[seed.Path]=seed.Sha1;state.SeededFiles.Add(seed.Path);
var up=paths.ResolveGameFile("mods/user-untracked.jar");File.WriteAllText(up,"user owned fixture");
var plan=engine.Plan(manifest,null,CancellationToken.None);if(plan.Downloads.Count!=2||plan.Deletions.Count!=2)throw new Exception("Expected2downloads2deletions");await engine.ApplyAsync(plan,null,CancellationToken.None);
foreach(var f in changed)if(Convert.ToHexStringLower(SHA1.HashData(File.ReadAllBytes(paths.ResolveGameFile(f.Path))))!=f.Sha1)throw new Exception("Bad public bytes "+f.Path);
if(File.ReadAllText(sp)!="user custom unchanged seed"||File.ReadAllText(up)!="user owned fixture")throw new Exception("Unrelated user data changed");
if(!engine.Plan(manifest,null,CancellationToken.None).IsEmpty)throw new Exception("Second sync not empty");var result=new{passed=true,downloads=2,oldTrackedRemoved=2,sameNameManagedReplacements=0,unchangedSeedPreserved=true,untrackedFilePreserved=true,secondSyncEmpty=true,source="Byte-identical current SyncEngine Downloader LocalState; actual public downloads; private fixture"};File.WriteAllText(Path.Combine(root,"result.json"),JsonSerializer.Serialize(result));Console.WriteLine(JsonSerializer.Serialize(result));
