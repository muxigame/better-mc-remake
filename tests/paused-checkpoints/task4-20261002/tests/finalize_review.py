"""Create local, source-only commits and verified review packages. Never push/tag."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
REPOS=("muxi-auth","better-mc-remake","muxi-game-core")


def git(repo,*args):
    return subprocess.check_output(["git","-c",f"core.excludesFile={ROOT/'evidence/empty-git-ignore'}",
        "-c","user.name=Codex","-c","user.email=codex@localhost","-C",str(repo),*args])


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def unittest_count(log):
    # PowerShell adds CRLF/BOM depending on execution profile.
    text=(ROOT/"evidence"/log).read_text(encoding="utf-8-sig")
    match=re.search(r"Ran (\d+) tests",text)
    assert match and text.rstrip().endswith("OK"),log
    return int(match.group(1))


subprocess.run(["python",str(ROOT/"tests/audit_preservation.py")],check=True)
summary={"created_utc":datetime.now(timezone.utc).isoformat(),"final_target":"https://mc.muxigame.com/account.html",
         "session_cookie":"bmc_session","repositories":{},"validation":{
          "auth_tests":unittest_count("auth-tests.log"),"site_tests":unittest_count("site-tests.log"),
          "cross_app_tests":unittest_count("end-to-end-tests.log"),"local_https_nginx_tests":unittest_count("https-nginx-chain.log")},
         "production_sso_enabled":False,"production_config_changed":False,"game_server_restarted":False,
         "pushed":False,"published":False,"terminal_changed":False,
         "pending_navigation_owner":"01a0f671-5e2f-7052-823d-cd6ee244d035",
         "remaining_gates":["Production config correspondence and Core LoginGate setup",
            "OP/home/minigame coordinated release window", "Trusted terminal shell child-view integration",
            "New release versions, signed full launcher installer and Linux Docker images",
            "Production HTTPS real launcher/game/player-center acceptance"],"files":{}}
core_log=(ROOT/"evidence/core-build.log").read_text(encoding="utf-8")
assert "Game Core Java self-tests: 10032 passed" in core_log
summary["validation"]["core_java_assertions"]=10032
launcher_log=(ROOT/"evidence/launcher-selftests.log").read_text(encoding="utf-8")
assert "\u901a\u8fc7 348\uff0c\u5931\u8d25 0" in launcher_log
summary["validation"]["launcher_selftests"]=348
summary["validation"]["launcher_self_contained_publish"]="passed with msquic.dll; unsigned sidecar review only"
summary["validation"]["production_https_or_real_player_chain"]="not accepted"
summary["validation"]["prior_mcef_runtime"]="Previous terminal task passed local native synthetic handoff; not rerun"
for name in REPOS:
    repo=ROOT/"repos"/name
    baseline=json.loads((ROOT/"evidence/preservation-and-config.json").read_text(encoding="utf-8"))["baselines"][name]
    git(repo,"add","--all")
    patch=ROOT/"artifacts"/(name+"-terminal-sso.patch")
    patch.write_bytes(git(repo,"diff","--cached",baseline,"--binary"))
    assert patch.stat().st_size
    git(repo,"diff","--cached","--check")
    if git(repo,"diff","--cached","--name-only").strip():
        git(repo,"commit","-m","feat(sso): prepare gated terminal player-center handoff")
    commit=git(repo,"rev-parse","HEAD").decode().strip()
    # A fresh baseline index verifies patch applicability independently of the review checkout.
    check=ROOT/"evidence"/(name+"-check-index")
    import os
    env={**os.environ,"GIT_INDEX_FILE":str(check)}
    subprocess.run(["git","-C",str(repo),"read-tree",baseline],env=env,check=True)
    subprocess.run(["git","-C",str(repo),"apply","--cached","--check",str(patch)],env=env,check=True)
    check.unlink()
    summary["repositories"][name]={"baseline":baseline,"review_commit":commit,
        "branch":"sso-companion-review","patch":patch.relative_to(ROOT).as_posix(),"patch_sha256":digest(patch),
        "fresh_baseline_apply_check":"passed","working_tree_clean":not git(repo,"status","--porcelain").strip()}
    archive=ROOT/"artifacts"/(name+"-sso-review.tar.gz")
    git(repo,"archive","--format=tar.gz",f"--output={archive}",commit)
core=ROOT/"artifacts/muxi-game-core-1.12.0-sso-review.jar"
if not core.is_file():
    shutil.copy2(ROOT/"repos/muxi-game-core/build/libs/muxi-game-core-1.12.0.jar",core)
assert digest(core)=="7ccc172eb9f0806cdd1bccb9e95b4c34a3256b0b8d67130093dc6426609cc944"
summary["artifact_versions"]={"core":"1.12.0, review-only; new release version required",
    "launcher":"1.2.3, review-only; new release version required",
    "unchanged_terminal":"0.2.1/c674e73656b7e7b51f507d21a2afed5fc276ab4b"}
for path in [ROOT/"DEPLOYMENT.md",core,*sorted((ROOT/"artifacts").glob("*.patch")),
             *sorted((ROOT/"artifacts").glob("*.tar.gz")),*sorted((ROOT/"artifacts/launcher-sidecar").glob("*")),
             *sorted((ROOT/"evidence").glob("*.log")),ROOT/"evidence/preservation-and-config.json"]:
    if path.is_file(): summary["files"][path.relative_to(ROOT).as_posix()]={"sha256":digest(path),"size":path.stat().st_size}
(ROOT/"VERIFICATION.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
with zipfile.ZipFile(ROOT/"artifacts/sso-companion-review.zip","w",compression=zipfile.ZIP_DEFLATED) as archive:
    for path in [ROOT/"DEPLOYMENT.md",ROOT/"VERIFICATION.json",*sorted((ROOT/"artifacts").glob("*-terminal-sso.patch")),
                 *sorted((ROOT/"evidence").glob("*.log")),ROOT/"evidence/preservation-and-config.json",*sorted((ROOT/"tests").glob("*.py")),ROOT/"tests/terminal-login.test.cjs"]:
        archive.write(path,path.relative_to(ROOT).as_posix())
print(json.dumps({"validation":summary["validation"],"review_commits":{name:info["review_commit"] for name,info in summary["repositories"].items()},
                  "review_zip_sha256":digest(ROOT/"artifacts/sso-companion-review.zip")},ensure_ascii=True))
