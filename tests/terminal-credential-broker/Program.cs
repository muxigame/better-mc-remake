using System.Diagnostics;
using BatterMC.Core;

var javaHome = args[0];
var coreClient = args[1];
var home = Path.GetDirectoryName(typeof(Program).Assembly.Location)!;
var classes = Path.Combine(home, "java-classes"); Directory.CreateDirectory(classes);
var fixture = Path.Combine(Directory.GetCurrentDirectory(), "BrokerQa.java");
await Run(Path.Combine(javaHome,"bin","javac.exe"), ["-d",classes,coreClient,fixture]);
int calls=0;
bool revoked=false;
var broker = new TerminalCredentialBroker(token => Task.FromResult<string?>(revoked ? null : new string((char)('a'+Interlocked.Increment(ref calls)),54)));
try
{
    await Verify(broker.PipeName,broker.Secret,new string('b',54));
    await Verify(broker.PipeName,broker.Secret,new string('c',54));
    await Verify(broker.PipeName,new string('w',43),"");
    if(calls!=2)throw new Exception("Unauthorized capability reached credential factory");
    await Run(Path.Combine(javaHome,"bin","java.exe"),["-cp",classes,"BrokerQa"],new Dictionary<string,string>{{"QA_PIPE",broker.PipeName},{"QA_CAPABILITY",broker.Secret},{"QA_EXPECTED",""},{"QA_MODE","parallel"}});
    if(calls!=4)throw new Exception("Concurrent native renewal did not reach the actual broker twice");
    revoked=true;
    await Verify(broker.PipeName,broker.Secret,"");
    await Verify("invalid-remote-pipe",broker.Secret,"");
    if(!TerminalCredentialEnvironment.ValidAccessToken(new string('a',54)) || TerminalCredentialEnvironment.ValidAccessToken(new string('a',43)))throw new Exception("Original account access was confused with old terminal credentials");
    var process=new ProcessStartInfo();
    process.Environment[TerminalCredentialEnvironment.PipeVariable]="inherited-pipe";
    process.Environment[TerminalCredentialEnvironment.BrokerVariable]="inherited-capability";
    TerminalCredentialEnvironment.Apply(process,null);
    if(process.Environment.ContainsKey(TerminalCredentialEnvironment.PipeVariable)||process.Environment.ContainsKey(TerminalCredentialEnvironment.BrokerVariable))throw new Exception("Inherited renewal authority survived");
    TerminalCredentialEnvironment.Apply(process,null,broker.PipeName,broker.Secret);
    if(process.ArgumentList.Count!=0||process.Environment[TerminalCredentialEnvironment.BrokerVariable]!=broker.Secret||broker.ToString().Contains(broker.Secret))throw new Exception("Capability escaped native environment or redaction");
}
finally { await broker.DisposeAsync(); }
await Verify(broker.PipeName,broker.Secret,"");
Console.WriteLine("PASS: actual C# to Java named-pipe renewal, original account access transport, wrong capability, revocation, disposal, native environment and redaction (9 cases)");

async Task Verify(string pipe,string capability,string expected)
{
    var env=new Dictionary<string,string>{{"QA_PIPE",pipe},{"QA_CAPABILITY",capability},{"QA_EXPECTED",expected}};
    await Run(Path.Combine(javaHome,"bin","java.exe"),["-cp",classes,"BrokerQa"],env);
}
async Task Run(string file,string[] arguments,Dictionary<string,string>? env=null)
{
    var info=new ProcessStartInfo(file){UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true};
    foreach(var argument in arguments)info.ArgumentList.Add(argument);
    if(env is not null)foreach(var entry in env)info.Environment[entry.Key]=entry.Value;
    using var process=Process.Start(info)!;
    var output=process.StandardOutput.ReadToEndAsync();var error=process.StandardError.ReadToEndAsync();
    try { await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(15)); }
    catch { if(!process.HasExited)process.Kill(true);throw; }
    if(process.ExitCode!=0)throw new Exception("Native test process failed; "+await error);
    if(env is not null && ((await output).Contains(env["QA_CAPABILITY"]) || (!string.IsNullOrEmpty(env["QA_EXPECTED"]) && (await output).Contains(env["QA_EXPECTED"]))))throw new Exception("Credential leaked in native test output");
}
