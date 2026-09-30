"""Validate/deploy only the Champions health script; no restart or publication."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
ARCHIVE = ROOT / "better-mc-remake/pack/archive"
SOURCE = ROOT / "better-mc-remake/pack/source/Better MC Remake [FORGE]"
RELATIVE = Path("kubejs/server_scripts/muxi_champions_health.js")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_mod(root: Path) -> None:
    jars = list((root / "mods").glob("*champions*.jar"))
    if len(jars) != 1 or "21.1.1.7" not in jars[0].name:
        raise RuntimeError(f"Unexpected Champions installation: {root}")
    with ZipFile(jars[0]) as jar:
        health = json.loads(jar.read("data/minecraft/modifier_setting/generic.max_health.json"))
        assert health["enable"] is True
        assert health["modifier"] == {"operation": "add_multiplied_total", "value": 0.35}
        levels = sorted(json.loads(jar.read(name))["level"] for name in jar.namelist()
                        if name.startswith("data/champions/champions/tier/") and name.endswith(".json"))
        assert levels == [1, 2, 3, 4, 5], levels
        builder = jar.read("top/theillusivec4/champions/common/champion/ChampionBuilder.class")
        assert b"\x01_\x01_modifier" in builder
    print("VERIFIED Champions version/native health rule/five tiers:", root.relative_to(ROOT))


def run_tests() -> None:
    subprocess.run(["node", "--check", str(HERE / RELATIVE.name)], check=True)
    subprocess.run(["node", str(HERE / "test_champions_health.cjs")], check=True)
    libraries = ROOT / "bmc5server/libraries"
    cp = [next((ROOT / "bmc5server/mods").glob("rhino-*.jar")),
          libraries / "org/slf4j/slf4j-api/2.0.9/slf4j-api-2.0.9.jar",
          libraries / "com/google/code/gson/gson/2.10.1/gson-2.10.1.jar",
          libraries / "it/unimi/dsi/fastutil/8.5.12/fastutil-8.5.12.jar"]
    classpath = os.pathsep.join(str(p) for p in cp)
    compiler_path = shutil.which("javac")
    if compiler_path is None:
        raise RuntimeError("JDK compiler not found")
    compiler = Path(compiler_path).resolve()
    runtime = compiler.with_name("java.exe" if os.name == "nt" else "java")
    if not runtime.is_file():
        raise RuntimeError("Matching JDK runtime not found: " + str(runtime))
    with tempfile.TemporaryDirectory(prefix="champions-health-rhino-") as temp:
        subprocess.run([str(compiler), "--release", "21", "-cp", classpath, "-d", temp,
                        str(HERE / "RhinoSyntaxCheck.java")], check=True)
        subprocess.run([str(runtime), "-cp", temp + os.pathsep + classpath, "RhinoSyntaxCheck",
                        str(HERE / RELATIVE.name)], check=True)


def protected_files() -> dict[Path, str]:
    result: dict[Path, str] = {}
    for root in [ROOT / "bmc5server", SOURCE]:
        paths = list((root / "kubejs").rglob("*"))
        paths += [root / "config/champions-server.toml", root / "config/tacz-server.toml",
                  root / "config/l2configs/modulargolems-server.toml"]
        for path in paths:
            if path.is_file() and path != root / RELATIVE:
                result[path] = sha(path.read_bytes())
    p = ROOT / "better-mc-remake/pack/packspec.json"
    result[p] = sha(p.read_bytes())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restore-vanilla", action="store_true")
    args = parser.parse_args()
    for root in [ROOT / "bmc5server", SOURCE]:
        verify_mod(root)
    run_tests()
    payload = (HERE / RELATIVE.name).read_bytes()
    if args.restore_vanilla:
        assert payload.count(b"const dynamicEnabled = true") == 1
        payload = payload.replace(b"const dynamicEnabled = true", b"const dynamicEnabled = false")
    paths = [ROOT / "bmc5server" / RELATIVE, SOURCE / RELATIVE]
    before = {p: p.read_bytes() if p.exists() else None for p in paths}
    changes = [p for p in paths if before[p] != payload]
    print("MODE:", "restore-native" if args.restore_vanilla else "dynamic-health")
    print("CHANGES:", [str(p.relative_to(ROOT)) for p in changes])
    if not args.apply or not changes:
        print("No deployment changes written")
        return
    protected = protected_files()
    backup = ARCHIVE / ("champions-health-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    backup.mkdir(parents=True, exist_ok=False)
    records = []
    for p, data in before.items():
        rel = p.relative_to(ROOT)
        records.append({"path": rel.as_posix(), "existed": data is not None,
                        "sha256": sha(data) if data is not None else None})
        if data is not None:
            saved = backup / "before" / rel
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(data)
    (backup / "before.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    written = []
    try:
        for p in changes:
            current = p.read_bytes() if p.exists() else None
            if current != before[p]:
                raise RuntimeError(f"Concurrent modification: {p}")
            p.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix=p.name + ".", suffix=".tmp", dir=p.parent, delete=False) as out:
                temporary = Path(out.name)
                out.write(payload)
            try:
                os.replace(temporary, p)
            finally:
                temporary.unlink(missing_ok=True)
            written.append(p)
            assert p.read_bytes() == payload
        for p, digest in protected.items():
            if sha(p.read_bytes()) != digest:
                raise RuntimeError(f"Protected file changed during deployment: {p}")
    except Exception:
        for p in written:
            # Do not overwrite a concurrent edit made after our own write.
            if p.exists() and p.read_bytes() == payload:
                if before[p] is None:
                    p.unlink()
                else:
                    p.write_bytes(before[p])
        raise
    report = {"mode": "restore-native" if args.restore_vanilla else "dynamic-health",
              "files": [{"path": p.relative_to(ROOT).as_posix(), "sha256": sha(p.read_bytes())} for p in paths],
              "protected_files_unchanged": len(protected), "server_restarted": False,
              "published_to_oss": False, "in_game_test": False}
    (backup / "after.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("VERIFIED:", len(paths), "deployed files;", len(protected), "existing files unchanged")
    print("BACKUP:", backup)
    print("No restart, reload, JAR/core changes, packspec edits or OSS publication")


if __name__ == "__main__":
    main()
