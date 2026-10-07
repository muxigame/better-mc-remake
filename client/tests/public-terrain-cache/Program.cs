using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using System.Security.Cryptography;
using BatterMC.Core;
using BatterMC.Protocol;

class Program
{
    sealed class ArchiveHandler(byte[] archive) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken ct)
            => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content=new ByteArrayContent(archive) });
    }
    static int checks;
    static void Assert(bool value, string label) { if (!value) throw new Exception(label); checks++; Console.WriteLine("PASS " + label); }
    static string Hash(string p) => Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(p)));
    static void Reject(Action action, string label)
    {
        try { action(); } catch (Exception ex) when (ex is InvalidDataException || ex is IOException) { checks++; Console.WriteLine("PASS " + label); return; }
        throw new Exception("Did not reject " + label);
    }
    static void Main(string[] args)
    {
        var root = Path.GetFullPath(args[0]); Directory.CreateDirectory(root);
        var paths = LauncherPaths.At(root); paths.EnsureCreated();
        foreach (var item in new[] { (args[2], "flight.jar"), (args[3], "dh.jar") })
            File.Copy(item.Item1, paths.ResolveGameFile(item.Item2));
        var archiveBytes=File.ReadAllBytes(args[1]);
        using (var downloader=new Downloader(new HttpClient(new ArchiveHandler(archiveBytes))))
            downloader.DownloadAllAsync(new[] { new DownloadItem { Url="https://offline.invalid/public.zip", TargetPath=paths.ResolveGameFile("public.zip"), ExpectedSize=archiveBytes.Length, ExpectedSha1=Convert.ToHexStringLower(SHA1.HashData(archiveBytes)) } },null,CancellationToken.None).GetAwaiter().GetResult();
        Assert(Hash(paths.ResolveGameFile("public.zip"))==Hash(args[1]),"actual downloader pins archive bytes before import");
        var s = new PublicTerrainCacheSpec {
            PackagePath="public.zip", PackageSha256=Hash(args[1]), GeneratorPath="flight.jar", GeneratorSha256=Hash(args[2]),
            DhPath="dh.jar", DhSha256=Hash(args[3]), DemSha256="62a0f366475ed22641b05826ee3f5167f6015a42ec2ee3abeaf347a9620b1682",
            HashedSeed="h1qnkdsfn63tg", ServerFolderKey="ReleaseOfflineTest"
        };
        var manifest = new PackManifest(); manifest.PublicTerrainCaches.Add(s);
        Reject(() => PublicTerrainCache.ValidateDistribution(manifest), "missing managed archive rejected");
        manifest.Files.Add(new ManagedFile { Path="public.zip", Policy=FilePolicy.Managed });
        PublicTerrainCache.ValidateDistribution(manifest);
        var roundtrip=PackManifest.FromJson(manifest.ToJson())!;
        Assert(roundtrip.PublicTerrainCaches.Single().PackageSha256==s.PackageSha256,"source-generated manifest roundtrip");
        manifest.Prune.Add("Distant_Horizons_server_data");
        Reject(() => PublicTerrainCache.ValidateDistribution(manifest), "cache prune rejected"); manifest.Prune.Clear();
        manifest.Files.Add(new ManagedFile { Path="Distant_Horizons_server_data/test/db", Policy=FilePolicy.Managed });
        Reject(() => PublicTerrainCache.ValidateDistribution(manifest), "managed player database rejected"); manifest.Files.RemoveAt(1);
        manifest.Overlays.Add(new ConfigOverlaySpec { Path="Distant_Horizons_server_data/player/db" });
        Reject(() => PublicTerrainCache.ValidateDistribution(manifest), "player cache overlay rejected"); manifest.Overlays.Clear();
        Assert(PublicTerrainCache.Install(paths,s)=="public terrain installed", "clean formal package install");
        var destination=Directory.GetFiles(paths.GameDir,"DistantHorizons.sqlite",SearchOption.AllDirectories).Single();
        Assert(Hash(destination)=="2d24d131673a64893ba4f8ace92358690007410b53ce50b9a931ad8084be749c","accepted database unchanged");
        File.WriteAllText(destination,"player-owned-data");
        Assert(PublicTerrainCache.Install(paths,s).Contains("preserved"), "existing player cache skipped");
        Assert(File.ReadAllText(destination)=="player-owned-data", "player cache bytes preserved");
        File.Delete(destination); File.WriteAllText(destination+"-wal","player-wal");
        Assert(PublicTerrainCache.Install(paths,s).Contains("preserved")&&!File.Exists(destination), "orphan sidecar preserved");
        File.Delete(destination+"-wal");
        s.ServerFolderKey="../escape"; Reject(()=>PublicTerrainCache.Install(paths,s),"folder traversal rejected"); s.ServerFolderKey="ReleaseOfflineTest";
        s.RoomSlot=1;Reject(()=>PublicTerrainCache.Install(paths,s),"uncovered room rejected");s.RoomSlot=0;
        s.MetresPerBlock=1;Reject(()=>PublicTerrainCache.Install(paths,s),"legacy metre scale rejected");s.MetresPerBlock=2;
        s.HashedSeed="differentSeed";Reject(()=>PublicTerrainCache.Install(paths,s),"seed mismatch rejected");s.HashedSeed="h1qnkdsfn63tg";
        s.DemSha256=new string('0',64);Reject(()=>PublicTerrainCache.Install(paths,s),"DEM mismatch rejected");s.DemSha256="62a0f366475ed22641b05826ee3f5167f6015a42ec2ee3abeaf347a9620b1682";
        s.DhSha256=new string('0',64);Reject(()=>PublicTerrainCache.Install(paths,s),"DH mismatch rejected");s.DhSha256=Hash(args[3]);
        s.GeneratorSha256=new string('0',64);Reject(()=>PublicTerrainCache.Install(paths,s),"generator mismatch rejected");s.GeneratorSha256=Hash(args[2]);
        s.PackageSha256=new string('0',64);Reject(()=>PublicTerrainCache.Install(paths,s),"download corruption rejected");s.PackageSha256=Hash(args[1]);
        File.Copy(args[4],paths.ResolveGameFile("old-qa.zip"));s.PackagePath="old-qa.zip";s.PackageSha256=Hash(args[4]);
        Reject(()=>PublicTerrainCache.Install(paths,s),"old QA-bound package rejected");
        s.PackagePath="public.zip";s.PackageSha256=Hash(args[1]);
        s.ServerFolderKey="木兮 公共地形";Assert(PublicTerrainCache.Install(paths,s)=="public terrain installed","safe Unicode server folder supported");s.ServerFolderKey="ReleaseOfflineTest";
        using(var z=ZipFile.Open(paths.ResolveGameFile("public.zip"),ZipArchiveMode.Update)){using var w=new StreamWriter(z.CreateEntry("../private.txt").Open());w.Write("bad");}
        s.PackageSha256=Hash(paths.ResolveGameFile("public.zip"));Reject(()=>PublicTerrainCache.Install(paths,s),"extra/traversal archive member rejected");
        Assert(!Directory.GetFiles(paths.GameDir,".public-terrain-*.tmp",SearchOption.AllDirectories).Any(),"no staging residue");
        Assert(!File.Exists(destination),"all rejected imports left destination absent");
        File.Copy(args[5],paths.ResolveGameFile("neutral.zip"));
        File.Copy(args[6],paths.ResolveGameFile("flight2.jar"));
        s.PackagePath="neutral.zip";s.PackageSha256=Hash(args[5]);s.GeneratorPath="flight2.jar";s.GeneratorSha256=Hash(args[6]);
        s.BindOnConnect=true;s.ServerIdentity="https://mc.muxigame.com/default";s.HashedSeed="";s.ServerFolderKey="";
        var before=Directory.GetFiles(paths.GameDir,"DistantHorizons.sqlite",SearchOption.AllDirectories).Length;
        Assert(PublicTerrainCache.Install(paths,s).Contains("staged"),"neutral package stages first-connection policy");
        Assert(Directory.GetFiles(paths.GameDir,"DistantHorizons.sqlite",SearchOption.AllDirectories).Length==before,"staging does not guess or create target world database");
        s.HashedSeed="h1qnkdsfn63tg";Reject(()=>PublicTerrainCache.Install(paths,s),"dynamic binding rejects fixed QA seed");s.HashedSeed="";
        s.ServerIdentity="https://wrong.invalid/default";Reject(()=>PublicTerrainCache.Install(paths,s),"dynamic binding rejects arbitrary service");s.ServerIdentity="https://mc.muxigame.com/default";
        Assert(PublicTerrainCache.IsOfficialControlSource("https://mc.muxigame.com/api/v1/manifest"),"official HTTPS control source accepted");
        foreach(var url in new[]{"http://mc.muxigame.com/api/v1/manifest","https://mc.muxigame.com.evil.invalid/api/v1/manifest","https://mc.muxigame.com/api/v1/manifest?other=1"}) Assert(!PublicTerrainCache.IsOfficialControlSource(url),"untrusted control source rejected");
        manifest.Servers.Add(new ServerEntry());manifest.OfficialFlightSourceVerified=true;
        var settings=new LauncherSettings { UpdateBaseUrl="https://mc.muxigame.com" };
        var process=new System.Diagnostics.ProcessStartInfo();
        PublicTerrainCache.ApplyLaunchContext(process,paths,manifest,settings,"127.0.0.1:23456",true);
        Assert(process.Environment["MUXI_FLIGHT_CACHE_ENDPOINT"]=="127.0.0.1:23456","existing authenticated route binds exact endpoint");
        Assert(process.Environment["MUXI_FLIGHT_CACHE_POLICY_SHA256"]==Hash(paths.ResolveGameFile("config/muxi-flight-public-cache.json")),"launch policy digest bound");
        PublicTerrainCache.ApplyLaunchContext(process,paths,manifest,settings,"127.0.0.1:23456",false);
        Assert(!process.Environment.ContainsKey("MUXI_FLIGHT_CACHE_SERVICE"),"missing existing broker removes inherited context");
        manifest.OfficialFlightSourceVerified=false;
        PublicTerrainCache.ApplyLaunchContext(process,paths,manifest,settings,"127.0.0.1:23456",true);
        Assert(!process.Environment.ContainsKey("MUXI_FLIGHT_CACHE_SERVICE"),"unverified manifest cannot grant binding context");
        manifest.OfficialFlightSourceVerified=true;
        Assert(!PackManifest.FromJson(manifest.ToJson())!.OfficialFlightSourceVerified,"remote manifest cannot serialize trusted provenance");
        Console.WriteLine("PASSED " + checks + " checks");
    }
}
