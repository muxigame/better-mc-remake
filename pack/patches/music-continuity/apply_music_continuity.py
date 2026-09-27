from __future__ import annotations

import argparse
import json
from pathlib import Path


PACK = Path(__file__).resolve().parents[2]
CONFIG = PACK / "source" / "Better MC Remake [FORGE]" / "config"


def main() -> None:
    parser = argparse.ArgumentParser(description="Keep background music playing through ordinary environment changes")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    biome_path = CONFIG / "biomemusic.json"
    biome = json.loads(biome_path.read_text(encoding="utf-8"))
    biome["smartMusic"]["smartMusic"] = False
    biome["stopMusicForRecords"]["stopMusicForRecords"] = False

    paster_path = CONFIG / "PasterDream-Client.toml"
    paster = paster_path.read_text(encoding="utf-8")
    old = '"bgm use song complete mode" = false'
    new = '"bgm use song complete mode" = true'
    if old in paster:
        paster = paster.replace(old, new, 1)
    elif new not in paster:
        raise RuntimeError("PasterDream song-complete setting not found")

    if args.apply:
        biome_path.write_text(json.dumps(biome, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paster_path.write_text(paster, encoding="utf-8")

    if biome["smartMusic"]["smartMusic"] or biome["stopMusicForRecords"]["stopMusicForRecords"] or new not in paster:
        raise RuntimeError("music continuity settings were not applied")
    print("music continuity settings verified" if not args.apply else "music continuity settings applied")


if __name__ == "__main__":
    main()
