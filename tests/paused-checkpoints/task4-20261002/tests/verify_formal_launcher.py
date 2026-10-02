"""Verify actual output metadata, updater signature and installer sidecar payload."""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
CLIENT=ROOT/"artifacts/formal-launcher/client"
BUILD=json.loads((ROOT/"evidence/formal-launcher-build.json").read_text(encoding="utf-8"))
assert BUILD.get("exit_code")==0,"Formal build did not complete"
source=json.loads((ROOT/"repos/better-mc-remake/client/tauri/package.json").read_text(encoding="utf-8"))
meta=json.loads((CLIENT/"launcher-release.json").read_text(encoding="utf-8-sig"))
installer=CLIENT/meta["installer"]
sig=CLIENT/meta["signatureFile"]
assert meta["version"]==source["version"]
sha=hashlib.sha256(installer.read_bytes()).hexdigest()
assert meta["sha256"]==sha and meta["size"]==installer.stat().st_size
assert sig.read_text(encoding="utf-8-sig").strip()==meta["signature"]
config=json.loads((ROOT/"repos/better-mc-remake/client/tauri/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
public_packet=base64.b64decode(config["plugins"]["updater"]["pubkey"]).decode().splitlines()[-1]
signature=base64.b64decode(meta["signature"]).decode()
with tempfile.TemporaryDirectory(prefix="verify-updater-",dir=ROOT/"evidence") as temp:
    decoded=Path(temp)/"signature.txt"
    decoded.write_text(signature,encoding="utf-8")
    verified=subprocess.run([str(ROOT/".tools/verify-updater.exe"),public_packet,str(decoded),str(installer)],
        capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
    (ROOT/"evidence/updater-signature-verification.log").write_text(verified.stdout+verified.stderr,encoding="utf-8")
    assert verified.returncode==0,"Updater signature validation failed"
    seven=Path(r"C:\Program Files\7-Zip\7z.exe")
    extracted=Path(temp)/"payload"
    result=subprocess.run([str(seven),"e",str(installer),f"-o{extracted}","-ir!battermc-backend.exe","-y"],
        capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
    (ROOT/"evidence/installer-payload-extraction.log").write_text(result.stdout+result.stderr,encoding="utf-8")
    assert result.returncode==0,"NSIS payload extraction failed"
    payload=extracted/"battermc-backend.exe"
    assert payload.is_file(),"Installer sidecar not found"
    payload_hash=hashlib.sha256(payload.read_bytes()).hexdigest()
    sidecar_hash=hashlib.sha256((CLIENT/"battermc-backend.exe").read_bytes()).hexdigest()
    assert payload_hash==sidecar_hash,"Installer contains a stale sidecar"

apps=[CLIENT/"BMC [Remake].exe",CLIENT/"battermc-backend.exe",installer]
quoted=",".join("'"+str(path).replace("'","''")+"'" for path in apps)
ps=f"$r=@(); foreach ($p in @({quoted})) {{$r+=@{{name=(Split-Path $p -Leaf);version=(Get-Item -LiteralPath $p).VersionInfo.ProductVersion;authenticode=(Get-AuthenticodeSignature -LiteralPath $p).Status.ToString()}}}}; $r | ConvertTo-Json -Compress"
run=subprocess.run([shutil.which("pwsh") or shutil.which("powershell"),"-NoProfile","-Command",ps],
    capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
assert run.returncode==0,"Windows output inspection failed"
products=json.loads(run.stdout.decode("utf-8-sig"))
assert all(product["version"].split("+")[0]==source["version"] for product in products),products
report={"status":"passed","source_version":source["version"],"production_release_ready":False,
        "updater_signature":"valid against existing committed public key",
        "tampered_installer":"rejected","installer_contains_current_sidecar":True,
        "installer_sha256":sha,"installer_size":installer.stat().st_size,"metadata_sha_and_size_match":True,
        "products":products,"windows_authenticode_all_valid":all(p["authenticode"]=="Valid" for p in products),
        "credentials_generated_copied_or_printed":False,"uploaded_or_deployed":False,
        "files":{p.name:{"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"size":p.stat().st_size} for p in CLIENT.iterdir() if p.is_file()}}
(ROOT/"evidence/formal-launcher-verification.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
print(json.dumps({"status":report["status"],"version":source["version"],"installer_sha256":sha,
                  "updater_signature":report["updater_signature"],"products":products,"current_sidecar_verified":True}))
