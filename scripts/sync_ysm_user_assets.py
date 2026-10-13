"""Offline inspection and explicitly local quarantine staging; no SSH/reload."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from app.ysm_asset_bridge import AssetBridge, next_run


class OfflineSnapshot:
    def __init__(self, snapshot, events):
        self.snapshot_file, self.events_file = snapshot, events

    def bridge_snapshot(self):
        return json.loads(self.snapshot_file.read_text(encoding="utf-8"))

    def events_after(self, seq):
        events = json.loads(self.events_file.read_text(encoding="utf-8")) if self.events_file else []
        return [e for e in events if e["seq"] > seq]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True, help="Trusted INTERNAL verified-blob snapshot, never user supplied")
    parser.add_argument("--events", type=Path)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--after-seq", type=int, default=0)
    parser.add_argument("--stage", action="store_true", help="Local Web/build quarantine only; does not activate")
    parser.add_argument("--at", default="03:00", help="Configured Asia/Shanghai daily wall clock")
    args = parser.parse_args()
    # This candidate CLI cannot copy to production or the existing public model tree.
    bridge = AssetBridge(OfflineSnapshot(args.snapshot, args.events),
                         source_root=args.source_root, target=args.target,
                         allowed_stage_root=ROOT / "build")
    result = bridge.run(after_seq=args.after_seq, dry_run=not args.stage)
    result["nextRun"] = next_run(datetime.now(timezone.utc), args.at).isoformat()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
