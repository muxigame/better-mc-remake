"""Verify SSO scope, unchanged parallel code, and report configuration keys only."""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path(r"C:\Users\Administrator\WorkSpace\muxigame")
IGNORE=ROOT/"evidence/empty-git-ignore"
IGNORE.write_text("",encoding="utf-8")
BASE={"muxi-auth":"8e74098bcb82166f0cad5679ae074695a061252d",
      "better-mc-remake":"ffdf0eb3f5dd1fa397c293755582ba6720d5a55c",
      "muxi-game-core":"d265d829bc5ea263cbed8e40137fd7ed4a1ed9ff"}
ALLOW={
 "muxi-auth":{".env.example","README.md","app/config.py","app/main.py","app/terminal_sso.py","tests/test_terminal_sso.py"},
 "better-mc-remake":{".env.example","client/core/GameLauncher.cs","client/core/OfflineAuth.cs","client/core/TerminalCredentialEnvironment.cs",
    "client/sidecar/RpcHost.cs","client/sidecar/SelfTest.cs","server/README.md","server/app/main.py","server/app/oidc.py",
    "server/web/nginx-container.conf","server/web/terminal-login.html","server/web/terminal-login.js"},
 "muxi-game-core":{"README.md","src/main/java/net/muxigame/core/MuxiGameCore.java",
    "src/main/java/net/muxigame/core/client/MuxiGameCoreClient.java","src/main/java/net/muxigame/core/client/TerminalPassportApi.java",
    "src/main/java/net/muxigame/core/feature/login/ConnectionAdmissions.java","src/main/java/net/muxigame/core/feature/login/LoginGate.java",
    "src/main/java/net/muxigame/core/feature/login/TerminalPassportNetwork.java","src/main/java/net/muxigame/core/feature/login/TerminalPassportServer.java",
    "tests/java/net/muxigame/core/ConnectionAdmissionsSelfTest.java","tests/java/net/muxigame/core/CoreSelfTest.java"}}


def git(repo,*args):
    return subprocess.check_output(["git","-c",f"core.excludesFile={IGNORE}","-c","core.quotePath=false","-C",str(repo),*args])


report={"baselines":BASE,"scope":{},"parallel_working_copies":{},"configuration_key_presence_only":{},
        "credential_values_extracted_or_printed":False}
for name,baseline in BASE.items():
    repo=ROOT/"repos"/name
    changes=set(git(repo,"diff",baseline,"--name-only").decode().splitlines())
    changes.update(git(repo,"ls-files","--others","--exclude-standard").decode().splitlines())
    assert not changes-ALLOW[name],(name,changes-ALLOW[name])
    checked=0
    preserved={}
    for name_path in git(repo,"ls-tree","-r","--name-only",baseline).decode().splitlines():
        if name_path in ALLOW[name]: continue
        expected=git(repo,"show",f"{baseline}:{name_path}")
        actual=(repo/name_path).read_bytes()
        # Git checkout may convert text to CRLF; compare canonical text bytes.
        assert actual.replace(b"\r\n",b"\n")==expected.replace(b"\r\n",b"\n"),(name,name_path)
        checked+=1
        if any(part in name_path for part in ("player_platform","account.","TaskPointsBridge","DailyTasksFeature","platform_admission","account_settings","account_binding")):
            preserved[name_path]=hashlib.sha256(expected).hexdigest()
    report["scope"][name]={"changed_paths":sorted(changes),"unchanged_tracked_files_verified":checked,"selected_preserved_sha256":preserved}
    original=SOURCE/name
    report["parallel_working_copies"][name]={"head":git(original,"rev-parse","HEAD").decode().strip(),
        "status":git(original,"status","--short").decode().splitlines()}

# Parse key names only; deliberately never split out or print secret values.
for rel,keys in (("muxi-auth/.env",["MUXI_MC_PROFILE_KEY","MUXI_BMC_WEB_CLIENT_ID","MUXI_BMC_WEB_CLIENT_SECRET","MUXI_TERMINAL_SSO_ENABLED"]),
                 ("better-mc-remake/.env",["BMC_AUTH_CLIENT_ID","BMC_AUTH_CLIENT_SECRET","BMC_PUBLIC_URL","BMC_TERMINAL_SSO_ENABLED","MUXI_TERMINAL_GAME_CREDENTIAL"])):
    path=SOURCE/rel
    try:
        names={line.partition("=")[0].strip() for line in path.read_text(encoding="utf-8-sig").splitlines()
               if line.strip() and not line.lstrip().startswith("#") and "=" in line}
        report["configuration_key_presence_only"][rel]={key:key in names for key in keys}
    except (PermissionError,FileNotFoundError):
        report["configuration_key_presence_only"][rel]={"readable_file":False}
path=SOURCE/"bmc5server/config/muxi-game-core.json"
try:
    data=json.loads(path.read_text(encoding="utf-8-sig"))
    login=data.get("features",{}).get("login",{})
    report["configuration_key_presence_only"]["bmc5server/config/muxi-game-core.json"]={key:key in login for key in ("enabled","endpoint","serverKey")}
    report["configuration_key_presence_only"]["bmc5server/config/muxi-game-core.json"]["login_section_present"]="login" in data.get("features",{})
except (PermissionError,FileNotFoundError):
    report["configuration_key_presence_only"]["bmc5server/config/muxi-game-core.json"]={"readable_file":False}
(ROOT/"evidence/preservation-and-config.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
print(json.dumps({"scope":{name:item["unchanged_tracked_files_verified"] for name,item in report["scope"].items()},
                  "configuration_key_presence_only":report["configuration_key_presence_only"]},ensure_ascii=False))
