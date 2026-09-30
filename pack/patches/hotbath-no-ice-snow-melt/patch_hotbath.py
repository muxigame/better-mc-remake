"""Disable only Hot Bath 4.1.0's periodic ice/snow scan.

The input is pinned to the original NeoForge 1.21.1 JAR. No upstream source,
world files, configuration, or unrelated class entries are rewritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import zipfile


ORIGINAL_SHA256 = "54e87d946936af0b2da7e1e5b0d8bd775f5ea7f3e3c3680870677dadd653c74c"
TARGET_CLASS = "com/crabmod/hotbath/events/IceSnowMeltHandler.class"
TARGET_METHOD = "onLevelTick"
TARGET_DESCRIPTOR = "(Lnet/neoforged/neoforge/event/tick/LevelTickEvent$Post;)V"
MARKER = "META-INF/muxi-hotbath-no-ice-snow-melt.json"


class Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def take(self, count: int) -> bytes:
        end = self.pos + count
        if count < 0 or end > len(self.data):
            raise ValueError("Truncated class file")
        result = self.data[self.pos:end]
        self.pos = end
        return result

    def u1(self) -> int:
        return self.take(1)[0]

    def u2(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def u4(self) -> int:
        return struct.unpack(">I", self.take(4))[0]


def locate_code(data: bytes) -> tuple[int, int, bytes]:
    """Return the exact target Code attribute range and payload."""
    r = Reader(data)
    if r.u4() != 0xCAFEBABE:
        raise ValueError("Not a JVM class file")
    r.take(4)  # minor and major version
    pool_count = r.u2()
    strings: dict[int, str] = {}
    index = 1
    widths = {3: 4, 4: 4, 5: 8, 6: 8, 7: 2, 8: 2, 9: 4, 10: 4,
              11: 4, 12: 4, 15: 3, 16: 2, 17: 4, 18: 4, 19: 2, 20: 2}
    while index < pool_count:
        tag = r.u1()
        if tag == 1:
            strings[index] = r.take(r.u2()).decode("utf-8", errors="replace")
        elif tag in widths:
            r.take(widths[tag])
            if tag in (5, 6):
                index += 1
        else:
            raise ValueError(f"Unsupported constant-pool tag: {tag}")
        index += 1
    r.take(6)  # access flags, this class, superclass
    r.take(2 * r.u2())
    matches: list[tuple[int, int, bytes]] = []
    for kind in ("field", "method"):
        for _ in range(r.u2()):
            access = r.u2()
            name = strings.get(r.u2())
            descriptor = strings.get(r.u2())
            target = (kind == "method" and name == TARGET_METHOD
                      and descriptor == TARGET_DESCRIPTOR)
            if target and (not access & 0x0008 or access & (0x0100 | 0x0400)):
                raise ValueError("Target is not a concrete static method")
            for _ in range(r.u2()):
                start = r.pos
                attr_name = strings.get(r.u2())
                payload = r.take(r.u4())
                if target and attr_name == "Code":
                    matches.append((start, r.pos, payload))
    for _ in range(r.u2()):
        r.take(2)
        r.take(r.u4())
    if r.pos != len(data) or len(matches) != 1:
        raise ValueError("Expected one exact target method and a complete class")
    return matches[0]


def patch_class(original: bytes) -> bytes:
    start, end, code = locate_code(original)
    if len(code) < 8:
        raise ValueError("Invalid Code attribute")
    # Keep the original local-slot count. No operand stack, branches, handlers,
    # stack-map frames, or debug offsets are needed for a single RETURN.
    max_locals = struct.unpack(">H", code[2:4])[0]
    replacement = struct.pack(">HHI", 0, max_locals, 1) + b"\xb1\0\0\0\0"
    patched = (original[:start + 2] + struct.pack(">I", len(replacement))
               + replacement + original[end:])
    new_start, new_end, new_code = locate_code(patched)
    if new_code != replacement:
        raise AssertionError("Replacement Code verification failed")
    if original[:start] != patched[:new_start] or original[end:] != patched[new_end:]:
        raise AssertionError("Unrelated class bytes were changed")
    return patched


def verify(original: Path, patched: Path) -> dict[str, object]:
    original_bytes = original.read_bytes()
    if hashlib.sha256(original_bytes).hexdigest() != ORIGINAL_SHA256:
        raise ValueError("Original JAR hash does not match the pinned release")
    with zipfile.ZipFile(original) as before, zipfile.ZipFile(patched) as after:
        before_names = before.namelist()
        after_names = after.namelist()
        if len(set(before_names)) != len(before_names) or len(set(after_names)) != len(after_names):
            raise ValueError("Duplicate ZIP entries are not supported")
        if set(after_names) != set(before_names) | {MARKER}:
            raise ValueError("Unexpected added or removed entries")
        expected = patch_class(before.read(TARGET_CLASS))
        changed: list[str] = []
        for name in before_names:
            old, new = before.read(name), after.read(name)
            if old != new:
                changed.append(name)
            if new != (expected if name == TARGET_CLASS else old):
                raise ValueError(f"Unexpected change: {name}")
        if changed != [TARGET_CLASS]:
            raise ValueError(f"Unexpected change set: {changed}")
        if after.testzip() is not None:
            raise ValueError("ZIP CRC check failed")
        marker = json.loads(after.read(MARKER))
        if marker["originalSha256"] != ORIGINAL_SHA256:
            raise ValueError("Patch provenance mismatch")
    return {
        "originalSha256": ORIGINAL_SHA256,
        "patchedSha256": hashlib.sha256(patched.read_bytes()).hexdigest(),
        "changedClassEntries": changed,
        "unchangedOriginalEntries": len(before_names) - 1,
        "method": TARGET_METHOD + TARGET_DESCRIPTOR,
        "newBytecode": "RETURN",
    }


def build(original: Path, output: Path) -> dict[str, object]:
    if original.resolve() == output.resolve():
        raise ValueError("In-place patching is not allowed; use a separate output")
    if output.exists():
        raise FileExistsError(output)
    if hashlib.sha256(original.read_bytes()).hexdigest() != ORIGINAL_SHA256:
        raise ValueError("Unsupported Hot Bath JAR; refusing to patch another version")
    with zipfile.ZipFile(original) as source:
        if any(n.upper().startswith("META-INF/") and n.upper().endswith((".SF", ".RSA", ".DSA", ".EC"))
               for n in source.namelist()):
            raise ValueError("Refusing to modify a signed JAR")
        replacement = patch_class(source.read(TARGET_CLASS))
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "x") as target:
            for info in source.infolist():
                target.writestr(info, replacement if info.filename == TARGET_CLASS else source.read(info))
            marker = zipfile.ZipInfo(MARKER, date_time=(1980, 1, 1, 0, 0, 0))
            marker.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(marker, json.dumps({
                "patch": "muxi-hotbath-no-ice-snow-melt-v1",
                "originalSha256": ORIGINAL_SHA256,
                "upstream": "https://github.com/crabsatellite/hotBath",
                "upstreamLicense": "GPL-3.0",
                "class": TARGET_CLASS,
                "method": TARGET_METHOD + TARGET_DESCRIPTOR,
                "change": "Replace only the periodic ice/snow scan entry with RETURN",
            }, indent=2) + "\n")
    return verify(original, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("patched", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    report = (verify(args.original, args.patched) if args.verify_only
              else build(args.original, args.patched))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
