"""Reuse the existing signing path in-memory; no credential copy, output or upload."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path(r"C:\Users\Administrator\WorkSpace\muxigame\better-mc-remake")
REPO=ROOT/"repos/better-mc-remake"
OUT=ROOT/"artifacts/formal-launcher"
KEYS={"TAURI_SIGNING_PRIVATE_KEY","TAURI_SIGNING_PRIVATE_KEY_PATH","TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
      "WINDOWS_SIGN_CERT_THUMBPRINT","WINDOWS_SIGN_COMMAND","WINDOWS_SIGN_TIMESTAMP_URL"}
env=os.environ.copy()
found=set()
for raw in (SOURCE/".env").read_text(encoding="utf-8-sig").splitlines():
    if not raw.strip() or raw.lstrip().startswith("#") or "=" not in raw: continue
    key,value=raw.split("=",1)
    key=key.strip()
    if key in KEYS:
        found.add(key)
        env.setdefault(key,value.strip().strip('"').strip("'"))
private_path=env.get("TAURI_SIGNING_PRIVATE_KEY_PATH","")
if private_path and not Path(private_path).is_absolute():
    private_path=str((SOURCE/private_path).resolve())
    env["TAURI_SIGNING_PRIVATE_KEY_PATH"]=private_path
report={"existing_signing_key_names":sorted(found),
        "updater_key_path_exists":bool(private_path and Path(private_path).is_file()),
        "authenticode_configured":bool(env.get("WINDOWS_SIGN_CERT_THUMBPRINT") or env.get("WINDOWS_SIGN_COMMAND")),
        "credentials_copied_generated_or_printed":False,"uploaded":False,"deployed":False}
report_file=ROOT/"evidence/formal-launcher-build.json"
report_file.write_text(json.dumps(report,indent=2),encoding="utf-8")
if not report["updater_key_path_exists"] and not env.get("TAURI_SIGNING_PRIVATE_KEY"):
    report["status"]="blocked: existing updater signing key not available"
    report_file.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(report["status"]);sys.exit(2)

# Reuse public compiler caches only. Credentials remain at their original path.
print("Copying existing compiler cache into isolated checkout",flush=True)
source_cache=SOURCE/"client/tauri/src-tauri/target/release"
target_cache=REPO/"client/tauri/src-tauri/target/release"
if source_cache.is_dir():
    shutil.copytree(source_cache,target_cache,dirs_exist_ok=True)
# The existing build expects this cached runtime resource beside the sidecar.
msquic=SOURCE/"client/tauri/src-tauri/binaries/msquic.dll"
binary_dir=REPO/"client/tauri/src-tauri/binaries"
binary_dir.mkdir(exist_ok=True)
if msquic.is_file():
    shutil.copy2(msquic,binary_dir/"msquic.dll")
    report["existing_msquic_resource_reused"]=True
env["DOTNET_CLI_HOME"]=str(ROOT/".dotnet")
env["DOTNET_CLI_TELEMETRY_OPTOUT"]="1"
env["CARGO_NET_OFFLINE"]="true"
env["npm_config_offline"]="true"
env["PYTHONUTF8"]="1"
pwsh=shutil.which("pwsh") or shutil.which("powershell")
command=[pwsh,"-NoProfile","-ExecutionPolicy","Bypass","-File",str(REPO/"scripts/build.ps1"),
         "-Target","client","-OutDir",str(OUT),"-SelfTest"]
redactions=sorted({env[k] for k in KEYS if env.get(k)},key=len,reverse=True)
def safe(line):
    for value in redactions: line=line.replace(value,"<redacted-signing-config>")
    return line
print("Running existing launcher build/signing mechanism offline",flush=True)
with (ROOT/"evidence/formal-launcher-build.log").open("w",encoding="utf-8") as log:
    process=subprocess.Popen(command,cwd=REPO,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW)
    for raw in iter(process.stdout.readline,b""):
        try:
            decoded=raw.decode("utf-8")
        except UnicodeDecodeError:
            decoded=raw.decode("gbk",errors="replace")
        line=safe(decoded)
        log.write(line);log.flush()
        if line.startswith("==="):
            print("Build stage advanced (see sanitized log)",flush=True)
    code=process.wait()
report.update({"exit_code":code,"status":"build completed" if code==0 else "build failed; see sanitized log",
    "installer_exists":(OUT/"client/BMC [Remake] setup.exe").is_file(),
    "updater_signature_exists":(OUT/"client/BMC [Remake] setup.exe.sig").is_file()})
report_file.write_text(json.dumps(report,indent=2),encoding="utf-8")
print(json.dumps(report),flush=True)
sys.exit(code)
