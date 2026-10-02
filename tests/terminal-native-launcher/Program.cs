using System.Diagnostics;
using System.Net;
using System.Reflection;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json.Nodes;
using BatterMC.Core;
using BatterMC.Launcher;

// QA executable only. Actual RpcHost, broker, OAuth HTTP, DPAPI and native env.
// Config contains public endpoints/paths only. Launcher-only control stays in env.
var config=JsonNode.Parse(await File.ReadAllTextAsync(args[0]))!.AsObject();
var controlUrl=config["controller"]!.GetValue<string>();
var controlSecret=Environment.GetEnvironmentVariable("MUXI_LAUNCHER_QA_CONTROL")??throw new InvalidOperationException("Native launcher capability absent");
using var controller=new HttpClient{Timeout=TimeSpan.FromSeconds(8)};
var privatePaths=LauncherPaths.At(config["launcherState"]!.GetValue<string>());privatePaths.EnsureDataDir();
using var host=new RpcHost(privatePaths,new LauncherSettings(),new LocalState());
long selectedUid=0;int rotations=0;long selectedVersion=-1;
using var transport=new ActualIssuerTransport(config["authURL"]!.GetValue<string>(),config["siteURL"]!.GetValue<string>(),config["spki"]!.GetValue<string>(),async tokens=>{
    rotations++;var value=new JsonObject{["uid"]=selectedUid,["access_token"]=tokens["access_token"]!.GetValue<string>(),["refresh_token"]=tokens["refresh_token"]!.GetValue<string>()};
    await Control("launcher-rotated",value);
});
Field("_accountHttp").SetValue(host,new HttpClient(transport,false));
using var stop=new CancellationTokenSource();
await SelectCurrent();
var boundUid=selectedUid;
var credentials=new List<string>();
await using var broker=(TerminalCredentialBroker)Method("CreateTerminalCredentialBroker").Invoke(host,[boundUid.ToString(System.Globalization.CultureInfo.InvariantCulture),credentials])!;
var initial=(string?)await (Task<string?>)Method("MintTerminalCredentialAsync").Invoke(host,[CancellationToken.None])!;
if(args.Contains("--probe-only")){
    int passed=0;
    async Task<JsonObject> PublicControl(string action){using var r=new HttpRequestMessage(HttpMethod.Post,controlUrl){Content=new StringContent(new JsonObject{["action"]=action}.ToJsonString(),Encoding.UTF8,"application/json")};r.Headers.Add("X-Muxi-QA-Control",Environment.GetEnvironmentVariable("MUXI_GAME_QA_CONTROL"));using var response=await controller.SendAsync(r);response.EnsureSuccessStatusCode();return JsonNode.Parse(await response.Content.ReadAsStringAsync())!.AsObject();}
    async Task<string> Fetch(TerminalCredentialBroker source){using var pipe=new System.IO.Pipes.NamedPipeClientStream(".",source.PipeName,System.IO.Pipes.PipeDirection.InOut,System.IO.Pipes.PipeOptions.Asynchronous);await pipe.ConnectAsync(5000);await pipe.WriteAsync(Encoding.ASCII.GetBytes(source.Secret+"\n"));await pipe.FlushAsync();using var reader=new StreamReader(pipe,Encoding.ASCII);return await reader.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(6))??"";}
    async Task VerifyProof(string credential){using var r=new HttpRequestMessage(HttpMethod.Post,"https://account.muxigame.com/api/launcher/minecraft/terminal-proof"){Content=new StringContent(new JsonObject{["challenge"]=new string('A',43),["requestId"]=Guid.NewGuid().ToString()}.ToJsonString(),Encoding.UTF8,"application/json")};r.Headers.Authorization=new("MuxiTerminal",credential);using var actual=new HttpClient(transport,false);using var response=await actual.SendAsync(r);if(response.StatusCode!=HttpStatusCode.OK)throw new InvalidOperationException("Actual issuer rejected pipe credential");}
    void Check(bool value,string message){if(!value)throw new InvalidOperationException(message);passed++;}
    async Task VerifyJavaProof(TerminalCredentialBroker source,bool slowReader=false){
        var p=new ProcessStartInfo(config["java"]!.GetValue<string>()){UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true};
        foreach(var value in new[]{"-Djavax.net.ssl.trustStore="+config["truststore"]!.GetValue<string>(),"-Djavax.net.ssl.trustStorePassword=isolated-synthetic","-Dmuxi.sso.nativeAuthURL="+config["authURL"]!.GetValue<string>(),"-cp",config["javaClasses"]!.GetValue<string>(),"JavaProofProbe"})p.ArgumentList.Add(value);
        p.ArgumentList.Insert(0,"-javaagent:"+config["javaAgent"]!.GetValue<string>());
        p.ArgumentList.Insert(0,"-Djava.net.preferIPv6Addresses=system");
        if(slowReader)p.ArgumentList.Insert(0,"-Dmuxi.sso.slowPipeReader=true");
        p.Environment.Remove("MUXI_LAUNCHER_QA_CONTROL");p.Environment.Remove("MUXI_GAME_QA_CONTROL");TerminalCredentialEnvironment.Apply(p,null,source.PipeName,source.Secret);
        using var child=Process.Start(p)!;var output=child.StandardOutput.ReadToEndAsync();var stderr=child.StandardError.ReadToEndAsync();
        try{await child.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(15));}catch{if(!child.HasExited)child.Kill(true);throw;}
        // Probe emits only status and exception type, never bodies, tokens or capability.
        var diagnostic=await output;if(diagnostic.Contains(source.Secret))throw new InvalidOperationException("Native Java probe redaction failure");Console.WriteLine(diagnostic.Trim());
        if(child.ExitCode!=0)throw new InvalidOperationException("Actual Java native proof failed; inspect redacted diagnostic");passed++;
    }
    using(var actualSite=new HttpClient(transport,false)){
        using var response=await actualSite.GetAsync("https://mc.muxigame.com/account.html");
        Check(response.StatusCode==HttpStatusCode.SeeOther && response.Headers.Location is { } location && location.Scheme=="https" && location.Host=="account.muxigame.com" && location.AbsolutePath=="/oauth/authorize","actual website front-channel redirect keeps canonical issuer origin");
    }
    bool gameDenied=false;try{await PublicControl("launcher-session");}catch(HttpRequestException denied){gameDenied=denied.StatusCode==HttpStatusCode.Forbidden;}Check(gameDenied,"game capability cannot retrieve launcher tokens");
    Check(TerminalCredentialEnvironment.Valid(initial),"initial actual bootstrap");
    var first=await Fetch(broker);Check(TerminalCredentialEnvironment.Valid(first),"actual factory serves named pipe");await VerifyProof(first);passed++;
    var concurrent=await Fetch(broker);await VerifyProof(first);await VerifyProof(concurrent);Check(first!=concurrent,"overlapping heartbeat/opening proof credentials remain valid and fresh");
    await VerifyJavaProof(broker);await VerifyJavaProof(broker,true);
    await PublicControl("expire-access");var renewed=await Fetch(broker);Check(TerminalCredentialEnvironment.Valid(renewed),"actual 401 refresh pipe retry");await VerifyProof(renewed);passed++;
    await VerifyJavaProof(broker);
    var stored=AccountStore.Load(privatePaths.AccountFile);Check(rotations==1 && stored?.AccessToken==(string?)Field("_accountToken").GetValue(host),"actual refresh persisted by DPAPI");
    await PublicControl("switch-account");await SelectCurrent();Check(selectedUid!=boundUid && await Fetch(broker)=="","actual A broker denied after B selected");
    var nextCredentials=new List<string>();await using var nextBroker=(TerminalCredentialBroker)Method("CreateTerminalCredentialBroker").Invoke(host,[selectedUid.ToString(System.Globalization.CultureInfo.InvariantCulture),nextCredentials])!;
    var next=await Fetch(nextBroker);Check(TerminalCredentialEnvironment.Valid(next),"actual B broker succeeds");await VerifyProof(next);passed++;await VerifyJavaProof(nextBroker);
    await (Task<JsonNode>)Method("AccountLogoutAsync").Invoke(host,[])!;Check(await Fetch(nextBroker)=="" && !File.Exists(privatePaths.AccountFile),"actual logout drops broker and DPAPI");
    await (Task)Method("RevokeTerminalCredentialsAsync").Invoke(host,[])!;
    await File.WriteAllTextAsync(config["report"]!.GetValue<string>(),new JsonObject{["success"]=true,["assertions"]=passed,["actualRpcHost"]=true,["actualIssuerHTTPS"]=true,["actualNamedPipe"]=true,["actualDPAPI"]=true,["minecraftStarted"]=false,["productionMutation"]=false}.ToJsonString());
    Console.WriteLine($"PASS: actual RpcHost + issuer TLS + native pipe refresh/switch/logout assertions={passed}; Minecraft not started");return 0;
}
var poll=Task.Run(async()=>{
    while(!stop.IsCancellationRequested){try{await Task.Delay(300,stop.Token);var status=await Control("launcher-state",new JsonObject());if(status["version"]!.GetValue<long>()!=selectedVersion)await SelectCurrent();}catch(OperationCanceledException){return;}catch{if(!stop.IsCancellationRequested)throw;}}
});
var processInfo=new ProcessStartInfo(config["java"]!.GetValue<string>()){UseShellExecute=false,CreateNoWindow=false,WorkingDirectory=config["gameDir"]!.GetValue<string>()};
processInfo.ArgumentList.Add("@"+config["argFile"]!.GetValue<string>());
processInfo.Environment.Remove("MUXI_LAUNCHER_QA_CONTROL");
processInfo.Environment.Remove("MUXI_SSO_QA_CONTROL");
if(Environment.GetEnvironmentVariable("MUXI_GAME_QA_CONTROL") is {} gameControl)processInfo.Environment["MUXI_SSO_QA_CONTROL"]=gameControl;
processInfo.Environment.Remove("MUXI_GAME_QA_CONTROL");
TerminalCredentialEnvironment.Apply(processInfo,initial,broker.PipeName,broker.Secret);
if(await Console.In.ReadLineAsync()!="GO")throw new InvalidOperationException("Owned process job was not assigned");
using var game=Process.Start(processInfo)??throw new InvalidOperationException("Native game did not start");
Console.WriteLine($"QA_REAL_LAUNCHER_STARTED pid={Environment.ProcessId} gamePid={game.Id} boundUid={boundUid}");
try{await game.WaitForExitAsync().WaitAsync(TimeSpan.FromMinutes(5));}
catch(TimeoutException){if(!game.HasExited)game.Kill(true);throw;}
finally{
    stop.Cancel();try{await poll;}catch{}
    await broker.DisposeAsync();
    await (Task)Method("RevokeTerminalCredentialsAsync").Invoke(host,[])!;
    var nativeSession=AccountStore.Load(privatePaths.AccountFile);
    var result=new JsonObject{["launcherUid"]=boundUid,["actualRpcHost"]=true,["actualBroker"]=true,["actualDPAPI"]=true,["dpapiSessionRetainedAtGameExit"]=nativeSession is not null,["actualRefreshRotations"]=rotations,["gameExitCode"]=game.HasExited?game.ExitCode:-1,["launcherPid"]=Environment.ProcessId,["gamePid"]=game.Id,["selectedVersion"]=selectedVersion,["productionMutation"]=false};
    await File.WriteAllTextAsync(config["report"]!.GetValue<string>(),result.ToJsonString());
}
return game.ExitCode;

FieldInfo Field(string name)=>typeof(RpcHost).GetField(name,BindingFlags.Instance|BindingFlags.NonPublic)!;
MethodInfo Method(string name)=>typeof(RpcHost).GetMethod(name,BindingFlags.Instance|BindingFlags.NonPublic)!;
async Task<JsonObject> Control(string action,JsonObject value){
    value["action"]=action;using var request=new HttpRequestMessage(HttpMethod.Post,controlUrl){Content=new StringContent(value.ToJsonString(),Encoding.UTF8,"application/json")};
    request.Headers.Add("X-Muxi-Launcher-QA-Control",controlSecret);using var response=await controller.SendAsync(request);response.EnsureSuccessStatusCode();return JsonNode.Parse(await response.Content.ReadAsStringAsync())!.AsObject();
}
async Task SelectCurrent(){
    var seed=await Control("launcher-session",new JsonObject());
    selectedVersion=seed["version"]!.GetValue<long>();
    if(seed["revoked"]!.GetValue<bool>()){
        await (Task<JsonNode>)Method("AccountLogoutAsync").Invoke(host,[])!;return;
    }
    selectedUid=seed["uid"]!.GetValue<long>();
    lock(Field("_accountSessionLock").GetValue(host)!){
        Field("_terminalAccountGeneration").SetValue(host,(long)Field("_terminalAccountGeneration").GetValue(host)!+1);
        Field("_account").SetValue(host,null);Field("_player").SetValue(host,null);
        Field("_accountToken").SetValue(host,seed["access_token"]!.GetValue<string>());Field("_accountRefreshToken").SetValue(host,seed["refresh_token"]!.GetValue<string>());Field("_lastRefresh").SetValue(host,default(DateTimeOffset));
    }
    await (Task)Method("LoadAccountAsync").Invoke(host,[])!;
    Method("PersistAccountSession").Invoke(host,[]);
    await (Task)Method("RevokeTerminalCredentialsAsync").Invoke(host,[])!;
}

sealed class ActualIssuerTransport:DelegatingHandler{
    readonly Uri auth,site;readonly Func<JsonObject,Task> rotated;
    public ActualIssuerTransport(string authUrl,string siteUrl,string pin,Func<JsonObject,Task> onRotation){
        auth=new(authUrl);site=new(siteUrl);rotated=onRotation;
        var expected=Convert.FromBase64String(pin);
        InnerHandler=new HttpClientHandler{UseProxy=false,AllowAutoRedirect=false,ServerCertificateCustomValidationCallback=(_,certificate,_,errors)=>{
            if(certificate is null || errors.HasFlag(System.Net.Security.SslPolicyErrors.RemoteCertificateNameMismatch) || DateTime.UtcNow<certificate.NotBefore.ToUniversalTime() || DateTime.UtcNow>certificate.NotAfter.ToUniversalTime())return false;
            using var key=certificate.GetRSAPublicKey();return key is not null && CryptographicOperations.FixedTimeEquals(expected,SHA256.HashData(key.ExportSubjectPublicKeyInfo()));
        }};
    }
    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken token){
        var original=request.RequestUri!;var target=original.Host=="account.muxigame.com"?auth:original.Host=="mc.muxigame.com"?site:throw new InvalidOperationException("Unexpected native issuer origin");
        request.RequestUri=new Uri(target,original.PathAndQuery);var response=await base.SendAsync(request,token);
        if(original.AbsolutePath=="/oauth/token" && response.IsSuccessStatusCode){
            var text=await response.Content.ReadAsStringAsync(token);var tokens=JsonNode.Parse(text)!.AsObject();await rotated(tokens);
            // ReadAsStringAsync buffers the same response; actual RpcHost still parses it itself.
        }
        return response;
    }
}
