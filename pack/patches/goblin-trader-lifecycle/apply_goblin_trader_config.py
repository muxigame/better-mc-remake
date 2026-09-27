from __future__ import annotations

import argparse
import json
from pathlib import Path


PACK = Path(__file__).resolve().parents[2]
CONFIG = PACK / "source" / "Better MC Remake [FORGE]" / "config"


def main() -> None:
    parser = argparse.ArgumentParser(description="Disable automatic merchant targeting and named-goblin persistence")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    client_path = CONFIG / "better_client" / "client.json"
    client = json.loads(client_path.read_text(encoding="utf-8"))
    client["enableTradingHud"] = False

    goblin_path = CONFIG / "goblintraders-entities.toml"
    goblin = goblin_path.read_text(encoding="utf-8")
    old = "preventDespawnIfNamed = true"
    new = "preventDespawnIfNamed = false"
    if old in goblin:
        goblin = goblin.replace(old, new, 1)
    elif new not in goblin:
        raise RuntimeError("Goblin Traders persistence setting not found")

    if args.apply:
        client_path.write_text(json.dumps(client, ensure_ascii=False, indent="\t") + "\n", encoding="utf-8")
        goblin_path.write_text(goblin, encoding="utf-8")

    if client["enableTradingHud"] or new not in goblin:
        raise RuntimeError("goblin trader settings were not applied")
    print("goblin trader settings verified" if not args.apply else "goblin trader settings applied")


if __name__ == "__main__":
    main()
