"""Build minimal source-only Docker contexts from committed Git blobs. No daemon/push."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"artifacts/docker-contexts"
OUT.mkdir(exist_ok=True)
CONFIG={
 "auth":{"repo":"muxi-auth","dockerfile":"Dockerfile","prefixes":("app/","web/","scripts/"),"files":{"requirements.txt"}},
 "api":{"repo":"better-mc-remake","dockerfile":"server/Dockerfile","prefixes":("server/app/",),
         "files":{"server/requirements.txt","server/launcher-release.json","server/site.json"}},
 "web":{"repo":"better-mc-remake","dockerfile":"server/web/Dockerfile","prefixes":("server/web/",),"files":set()},
}
manifest={"status":"contexts prepared; images NOT built","local_docker_daemon":"unavailable",
          "wsl_network":"Mirrored networking failed with 0x8007054f and fell back to None",
          "credentials_included":False,"uploads_or_deployment":False,"contexts":{}}


def git(repo,*args): return subprocess.check_output(["git","-C",str(ROOT/"repos"/repo),*args])


for name,spec in CONFIG.items():
    commit=git(spec["repo"],"rev-parse","HEAD").decode().strip()
    paths=git(spec["repo"],"ls-tree","-r","-z","--name-only",commit).decode().split("\0")
    selected={p for p in paths if p and (p in spec["files"] or p.startswith(spec["prefixes"]))}
    selected.discard(spec["dockerfile"])
    data={p:git(spec["repo"],"show",f"{commit}:{p}") for p in selected}
    data["Dockerfile"]=git(spec["repo"],"show",f"{commit}:{spec['dockerfile']}")
    data[".dockerignore"]=b".git\n.env\n**/__pycache__\n**/*.pyc\n**/data\n"
    assert not any(p.endswith(".env") or "private" in p.lower() or "credential" in p.lower() for p in data)
    archive=OUT/(name+".tar.gz")
    with tarfile.open(archive,"w:gz") as tar:
        for path,content in sorted(data.items()):
            info=tarfile.TarInfo(path);info.size=len(content);info.mode=0o644;info.mtime=0
            tar.addfile(info,io.BytesIO(content))
    tag="sso-local-"+name+":20261001-"+commit[:12]
    manifest["contexts"][name]={"source_repo":spec["repo"],"source_commit":commit,
      "archive":archive.relative_to(ROOT).as_posix(),"sha256":hashlib.sha256(archive.read_bytes()).hexdigest(),
      "tracked_source_files":len(data),"base_images":[line[5:] for line in data["Dockerfile"].decode().splitlines() if line.startswith("FROM ")],
      "build_command":f"docker buildx build --platform linux/amd64 --load -f Dockerfile -t {tag} {name}",
      "image_built":False,"files":{p:hashlib.sha256(c).hexdigest() for p,c in sorted(data.items())}}
(ROOT/"evidence/docker-contexts.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
print(json.dumps({name:{"files":value["tracked_source_files"],"sha256":value["sha256"],"image_built":False}
                  for name,value in manifest["contexts"].items()}))
