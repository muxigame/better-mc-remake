from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[2]
MODS = ROOT / "source" / "Better MC Remake [FORGE]" / "mods"
EXPECTED_SHA256 = "4ea3fa9e7c33abaf9dc135e88b4c07992c6e8f03d61c828b2c6cdfa6be32957c"
OLD_NESTED = "META-INF/jars/flywheel-neoforge-1.21.1-1.0.4.jar"
METADATA = "META-INF/jarjar/metadata.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_one(pattern: str) -> Path:
    found = list(MODS.glob(pattern))
    if len(found) != 1:
        raise RuntimeError(f"expected one {pattern}, found {len(found)}")
    return found[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove Vanillin's obsolete embedded Flywheel 1.0.4")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    vanillin = find_one("vanillin-neoforge-1.21.1-1.1.3.jar")
    create = find_one("*create-1.21.1-6.0.10.jar")
    current = digest(vanillin)
    if current != EXPECTED_SHA256:
        with zipfile.ZipFile(vanillin) as archive:
            metadata = json.loads(archive.read(METADATA))
            if OLD_NESTED not in archive.namelist() and metadata.get("jars") == []:
                print(f"already patched: {vanillin.name} sha256={current}")
                return
        raise RuntimeError(f"unexpected Vanillin SHA-256: {current}")

    with zipfile.ZipFile(create) as archive:
        create_metadata = json.loads(archive.read(METADATA))
    if not any(item.get("version", {}).get("artifactVersion") == "1.0.6"
               and "flywheel" in item.get("path", "") for item in create_metadata.get("jars", [])):
        raise RuntimeError("Create does not provide embedded Flywheel 1.0.6")

    with tempfile.TemporaryDirectory(dir=vanillin.parent) as temp_dir:
        output = Path(temp_dir) / vanillin.name
        with zipfile.ZipFile(vanillin) as source, zipfile.ZipFile(output, "w") as target:
            for info in source.infolist():
                if info.filename == OLD_NESTED:
                    continue
                data = source.read(info)
                if info.filename == METADATA:
                    metadata = json.loads(data)
                    metadata["jars"] = []
                    data = (json.dumps(metadata, indent=2) + "\n").encode()
                target.writestr(info, data)
        with zipfile.ZipFile(output) as check:
            if OLD_NESTED in check.namelist() or json.loads(check.read(METADATA)).get("jars") != []:
                raise RuntimeError("patched Vanillin verification failed")
        if args.apply:
            backup = ROOT / "archive" / "vanillin-flywheel-1.0.4-before-patch.jar"
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                shutil.copy2(vanillin, backup)
            output.replace(vanillin)
            print(f"patched: {vanillin.name} sha256={digest(vanillin)} backup={backup}")
        else:
            print(f"verified patch plan: {vanillin.name} -> sha256={digest(output)}")


if __name__ == "__main__":
    main()
