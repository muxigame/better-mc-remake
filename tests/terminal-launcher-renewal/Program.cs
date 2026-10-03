using System.Net;
using System.Reflection;
using System.Text;
using System.Text.Json.Nodes;
using BatterMC.Core;
using BatterMC.Launcher;

// Only synthetic tokens and a private temporary account store. The handler makes no network request.
var root=Path.Combine(Path.GetTempPath(),"muxi-launcher-renewal-"+Guid.NewGuid().ToString("N"));
var paths=LauncherPaths.At(root);paths.EnsureDataDir();
var host=new RpcHost(paths,new LauncherSettings(),new LocalState());
var handler=new SyntheticIssuer();
Field("_accountHttp").SetValue(host,new HttpClient(handler));
int passed=0;
try
{
    Select(51001,"synthetic-expired","synthetic-refresh");
    var value=await Mint();
    Check(value==new string('r',54),"401 refresh/retry returns the exact refreshed account access token");
    Check(handler.Refreshes==1 && handler.Bootstraps==2,"one native refresh followed by one retry");
    Check(Get<string>("_accountToken")==new string('r',54) && Get<string>("_accountRefreshToken")=="synthetic-rotated","actual launcher rotates native account tokens");
    var session=AccountStore.Load(paths.AccountFile)!;
    Check(session.AccessToken==new string('r',54) && session.RefreshToken=="synthetic-rotated","native DPAPI store persists the rotated pair");
    Check(!handler.BootstrapHeaders.Any(x=>x.Contains("refresh") || x.Contains("rotated")),"refresh authority never reaches bootstrap headers");

    Select(51001,new string('l',54),"synthetic-refresh");
    Check(await Mint()==new string('l',54),"live account access is returned unchanged");
    var before=handler.Bootstraps;
    await (Task)Method("RevokeTerminalCredentialAsync").Invoke(host,[new string('l',54)])!;
    Check(handler.Bootstraps==before && Get<string>("_accountToken")==new string('l',54),"game close does not revoke the shared account session");
    Select(51001,new string('l',54),"synthetic-refresh");handler.WrongUid=true;
    Check(await Mint()==null,"issuer UID mismatch rejected");handler.WrongUid=false;
    Select(51001,"synthetic-expired","synthetic-revoked");handler.DenyRefresh=true;
    Check(await Mint()==null,"revoked native account cannot authorize the native broker");handler.DenyRefresh=false;

    Select(51001,"synthetic-expired","synthetic-refresh");handler.HoldRefresh=true;
    var refresh=Refresh();await handler.RefreshArrived.Task.WaitAsync(TimeSpan.FromSeconds(5));
    Select(51002,"synthetic-account-b","synthetic-refresh-b");handler.ReleaseRefresh.TrySetResult();
    Check(!await refresh,"late refresh fails after account switch");
    Check(Get<string>("_accountToken")=="synthetic-account-b" && Get<string>("_accountRefreshToken")=="synthetic-refresh-b","late account A cannot replace selected B");handler.HoldRefresh=false;

    Select(51001,new string('l',54),"synthetic-refresh");handler.HoldBootstrap=true;
    var mint=Mint();await handler.BootstrapArrived.Task.WaitAsync(TimeSpan.FromSeconds(5));
    Select(51002,"synthetic-account-b","synthetic-refresh-b");handler.ReleaseBootstrap.TrySetResult();
    Check(await mint==null,"in-flight mint cannot cross a selected UID/generation");handler.HoldBootstrap=false;

    Select(51001,new string('l',54),"synthetic-refresh");handler.HoldProfile=true;
    var profile=(Task)Method("LoadPlayerProfileAsync").Invoke(host,[])!;await handler.ProfileArrived.Task.WaitAsync(TimeSpan.FromSeconds(5));
    Select(51002,"synthetic-account-b","synthetic-refresh-b");Field("_player").SetValue(host,new JsonObject{["uid"]=51002L});handler.ReleaseProfile.TrySetResult();
    bool rejected=false;try{await profile;}catch(InvalidOperationException){rejected=true;}
    Check(rejected && Get<JsonObject>("_player")?["uid"]?.GetValue<long>()==51002,"late A profile cannot overwrite B");

    Select(51001,new string('l',54),"synthetic-refresh");
    var generation=Get<long>("_terminalAccountGeneration");
    Method("DropAccountSession").Invoke(host,[false]);
    Check(Get<long>("_terminalAccountGeneration")>generation && await Mint()==null,"logout immediately cancels native authority");
    Check(!File.Exists(paths.AccountFile),"logout clears only private DPAPI fixture store");
    Console.WriteLine($"PASS: actual RpcHost native refresh/UID/switch/revoke assertions={passed}");
}
finally
{
    host.Dispose();
    // A known, newly created leaf under Windows Temp, no user launcher data.
    if(Directory.Exists(root) && root.StartsWith(Path.Combine(Path.GetTempPath(),"muxi-launcher-renewal-"),StringComparison.OrdinalIgnoreCase))Directory.Delete(root,true);
}

FieldInfo Field(string name)=>typeof(RpcHost).GetField(name,BindingFlags.Instance|BindingFlags.NonPublic)!;
MethodInfo Method(string name)=>typeof(RpcHost).GetMethod(name,BindingFlags.Instance|BindingFlags.NonPublic)!;
T? Get<T>(string name)=>(T?)Field(name).GetValue(host);
void Select(long uid,string access,string refresh)
{
    lock(Field("_accountSessionLock").GetValue(host)!)
    {
        Field("_terminalAccountGeneration").SetValue(host,Get<long>("_terminalAccountGeneration")+1);
        Field("_accountToken").SetValue(host,access);Field("_accountRefreshToken").SetValue(host,refresh);
        Field("_account").SetValue(host,new JsonObject{["muxi_uid"]=uid});Field("_lastRefresh").SetValue(host,default(DateTimeOffset));
    }
}
Task<string?> Mint()=>(Task<string?>)Method("GetTerminalAccessTokenAsync").Invoke(host,[CancellationToken.None])!;
Task<bool> Refresh()=>(Task<bool>)Method("RefreshAccountAsync").Invoke(host,[])!;
void Check(bool value,string name){if(!value)throw new Exception(name);passed++;}

sealed class SyntheticIssuer:HttpMessageHandler
{
 public int Refreshes,Bootstraps;public bool WrongUid,DenyRefresh,HoldRefresh,HoldBootstrap,HoldProfile;
 public List<string> BootstrapHeaders=new();
 public TaskCompletionSource ProfileArrived=new(TaskCreationOptions.RunContinuationsAsynchronously),ReleaseProfile=new(TaskCreationOptions.RunContinuationsAsynchronously);
 public TaskCompletionSource RefreshArrived=new(TaskCreationOptions.RunContinuationsAsynchronously),ReleaseRefresh=new(TaskCreationOptions.RunContinuationsAsynchronously),BootstrapArrived=new(TaskCreationOptions.RunContinuationsAsynchronously),ReleaseBootstrap=new(TaskCreationOptions.RunContinuationsAsynchronously);
 protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage r,CancellationToken ct)
 {
  if(r.RequestUri!.AbsolutePath=="/api/v1/player/profile")
  {
   if(HoldProfile){ProfileArrived.TrySetResult();await ReleaseProfile.Task.WaitAsync(ct);}
   return Json(HttpStatusCode.OK,"{\"player\":{\"uid\":51001}}");
  }
  if(r.RequestUri!.AbsolutePath=="/oauth/token")
  {
   Refreshes++;if(HoldRefresh){RefreshArrived.TrySetResult();await ReleaseRefresh.Task.WaitAsync(ct);}
   return Json(DenyRefresh?HttpStatusCode.Unauthorized:HttpStatusCode.OK,new JsonObject{["access_token"]=new string('r',54),["refresh_token"]="synthetic-rotated"}.ToJsonString());
  }
  if(r.RequestUri.AbsolutePath=="/oauth/userinfo")
  {
   Bootstraps++;var bearer=r.Headers.Authorization?.Parameter??"";BootstrapHeaders.Add(bearer);
   if(HoldBootstrap){BootstrapArrived.TrySetResult();await ReleaseBootstrap.Task.WaitAsync(ct);}
   if(bearer=="synthetic-expired")return Json(HttpStatusCode.Unauthorized,"{}");
   return Json(HttpStatusCode.OK,new JsonObject{["muxi_uid"]=WrongUid?51002:51001}.ToJsonString());
  }
  throw new Exception("Unapproved synthetic endpoint");
 }
 static HttpResponseMessage Json(HttpStatusCode status,string value)=>new(status){Content=new StringContent(value,Encoding.UTF8,"application/json")};
}
