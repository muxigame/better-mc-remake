"""Extend local evidence packages after supported builds. No push/deploy."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
subprocess.run(["python",str(ROOT/"tests/audit_preservation.py")],check=True)
d=json.loads((ROOT/"VERIFICATION.json").read_text(encoding="utf-8"))
formal=json.loads((ROOT/"evidence/formal-launcher-verification.json").read_text(encoding="utf-8"))
assert formal["status"]=="passed"
docker=json.loads((ROOT/"evidence/docker-contexts.json").read_text(encoding="utf-8"))
for context in docker["contexts"].values():
    assert context["source_commit"]==d["repositories"][context["source_repo"]]["review_commit"]
for name in d["repositories"]:
    status=subprocess.check_output(["git","-c",f"core.excludesFile={ROOT/'evidence/empty-git-ignore'}",
        "-C",str(ROOT/"repos"/name),"status","--porcelain"])
    assert not status.strip(),name
    d["repositories"][name]["working_tree_clean"]=True
d["updated_utc"]=datetime.now(timezone.utc).isoformat()
d["validation"].update({"launcher_full_tauri_nsis":"passed, local review version 1.2.3",
    "updater_signature":"valid against committed public key; tampered installer rejected",
    "installer_current_sidecar_payload":"verified by extraction and SHA-256",
    "launcher_version_coherence":"source/sidecar/Tauri/NSIS/metadata all 1.2.3",
    "launcher_ui_tests":23,"installer_hooks":"passed",
    "windows_authenticode":"NotSigned for all three application EXEs",
    "linux_docker_images":"not built: daemon unavailable and WSL networking failure; minimal contexts prepared"})
d["formal_launcher_verification"]=formal
adapter=json.loads((ROOT/"evidence/terminal-container-adapter.json").read_text(encoding="utf-8"))
d["terminal_container_adapter"]=adapter
d.pop("terminal_changed",None)
d.pop("pending_navigation_owner",None)
d["published_terminal_changed"]=False
d["original_terminal_checkout_changed"]=False
d["isolated_terminal_adapter_created"]=True
d["navigation_owner"]="Parent owns container, animation and four-regression integration"
d["validation"].update({"terminal_container_adapter_assertions":84,
    "terminal_container_actual_dependency_compilation":"passed",
    "combined_native_mcef_child_sso_runtime":"not run; parent merged integration QA required"})
d["artifact_versions"]["terminal_container_adapter"]="0.2.1, isolated review only; unique release version required"
d["remaining_gates"]=["Supported authorized linux/amd64 builder and image runtime validation",
    "Existing authorized Authenticode signing configuration if Windows code signing is required",
    "Production config correspondence and authorization for missing Core LoginGate setup",
    "OP/home/minigame coordinated versions and deployment window",
    "Parent integration of delivered container, small SSO adapter, animation and four regression files; combined native MCEF child-view SSO QA",
    "New unique release versions and final rebuild after integration",
    "Explicit authorization for uploads/deployments, SSO enablement, Core/game-server restart and rollback",
    "Production HTTPS actual launcher/game/player-center acceptance"]
paths=[ROOT/"DEPLOYMENT.md",ROOT/"BUILD-AND-RELEASE.md",ROOT/"SSO_CONTAINER_ADAPTER.md",
    ROOT/adapter["patch"],ROOT/adapter["artifact"],*sorted((ROOT/"evidence").glob("*.json")),
    *sorted((ROOT/"evidence").glob("*.log")),*sorted((ROOT/"artifacts/docker-contexts").glob("*.tar.gz")),
    *sorted((ROOT/"artifacts/formal-launcher/client").glob("*"))]
for path in paths:
    if path.is_file():
        d["files"][path.relative_to(ROOT).as_posix()]={"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"size":path.stat().st_size}
(ROOT/"VERIFICATION.json").write_text(json.dumps(d,indent=2,ensure_ascii=False),encoding="utf-8")
with zipfile.ZipFile(ROOT/"artifacts/sso-companion-review.zip","w",compression=zipfile.ZIP_DEFLATED) as z:
    included=[ROOT/"DEPLOYMENT.md",ROOT/"BUILD-AND-RELEASE.md",ROOT/"SSO_CONTAINER_ADAPTER.md",ROOT/"VERIFICATION.json",ROOT/adapter["patch"],
        *sorted((ROOT/"artifacts").glob("*-terminal-sso.patch")),*sorted((ROOT/"evidence").glob("*.json")),
        *sorted((ROOT/"evidence").glob("*.log")),*sorted((ROOT/"artifacts/docker-contexts").glob("*.tar.gz")),
        *sorted((ROOT/"tests").glob("*.py")),ROOT/"tests/terminal-login.test.cjs",ROOT/"tests/verify_updater_signature.rs"]
    for path in included: z.write(path,path.relative_to(ROOT).as_posix())
with zipfile.ZipFile(ROOT/"artifacts/sso-companion-review.zip","r") as z:
    assert z.testzip() is None
print(json.dumps({"formal_installer_sha256":formal["installer_sha256"],
    "review_zip_sha256":hashlib.sha256((ROOT/"artifacts/sso-companion-review.zip").read_bytes()).hexdigest(),
    "linux_images_built":False,"all_review_worktrees_clean":True}))
