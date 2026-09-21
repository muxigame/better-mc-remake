"""Merge a validated support policy into release metadata (never touches binaries)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from app.client_updates import validate_policy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    release = json.loads(args.release.read_text(encoding="utf-8-sig"))
    policy = json.loads(args.policy.read_text(encoding="utf-8-sig"))
    release.update(validate_policy(policy, str(release["version"])))
    # Support policy replaces the obsolete all-old-clients boolean.
    release["mandatory"] = False
    args.out.write_text(json.dumps(release, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
