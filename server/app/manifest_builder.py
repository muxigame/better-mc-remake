from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


Log = Callable[[str], None]


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    glob = pattern.replace("\\", "/").removeprefix("./")
    output = "^"
    index = 0
    while index < len(glob):
        if glob[index:index + 2] == "**":
            if glob[index:index + 3] == "**/":
                output += "(?:.*/)?"
                index += 3
            else:
                output += ".*"
                index += 2
        elif glob[index] == "*":
            output += "[^/]*"
            index += 1
        elif glob[index] == "?":
            output += "[^/]"
            index += 1
        else:
            output += re.escape(glob[index])
            index += 1
    return re.compile(output + "$", re.IGNORECASE)


def compact(value):
    if isinstance(value, list):
        return [compact(item) for item in value]
    if isinstance(value, dict):
        return {key: compact(item) for key, item in value.items() if item is not None}
    return value


def hash_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, fallback=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        if fallback is not None:
            return fallback
        raise


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(compact(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def reconcile_overlay_policies(files: list[dict], spec: dict, log: Log) -> None:
    overlay_paths = {
        item["path"].replace("\\", "/").lstrip("/").casefold()
        for item in spec.get("overlays", [])
    }
    known = {item["path"].casefold() for item in files}
    for item in files:
        if item["path"].casefold() in overlay_paths and item["policy"] == "Managed":
            item["policy"] = "Seed"
            log(f'  策略调整 {item["path"]}：Managed -> Seed（该文件存在键级硬配置）')
    for overlay_path in overlay_paths - known:
        log(f"  !! 硬配置指向的文件不在分发清单里：{overlay_path}")


def publish_files(
    root: Path,
    publish_dir: Path,
    files: list[dict],
    mapped: list[tuple[dict, bytes]],
    use_hard_links: bool,
    log: Log,
) -> None:
    publish_dir.mkdir(parents=True, exist_ok=True)
    mapped_paths = {item[0]["to"].replace("\\", "/").casefold() for item in mapped}
    copied = linked = skipped = 0

    for item in files:
        if item["path"].casefold() in mapped_paths:
            continue
        source = root.joinpath(*item["path"].split("/"))
        destination = publish_dir.joinpath(*item["path"].split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file():
            source_stat = source.stat()
            destination_stat = destination.stat()
            if destination_stat.st_size == item["size"] and destination_stat.st_mtime_ns >= source_stat.st_mtime_ns:
                skipped += 1
                continue
        if use_hard_links:
            try:
                destination.unlink(missing_ok=True)
                os.link(source, destination)
                linked += 1
                continue
            except OSError:
                pass
        shutil.copy2(source, destination)
        copied += 1

    for mapping, content in mapped:
        destination = publish_dir.joinpath(*mapping["to"].replace("\\", "/").split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        copied += 1

    expected = {str(publish_dir.joinpath(*item["path"].split("/")).resolve()).casefold() for item in files}
    removed = 0
    for existing in sorted(publish_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if existing.is_file() and str(existing.resolve()).casefold() not in expected:
            existing.unlink()
            removed += 1
        elif existing.is_dir():
            try:
                existing.rmdir()
            except OSError:
                pass
    log(f"发布目录 {publish_dir}：硬链接 {linked}，复制 {copied}，未变 {skipped}，清理 {removed}")


def build_manifest(
    spec: dict,
    spec_path: Path,
    publish_dir: Path | None = None,
    use_hard_links: bool = False,
    log: Log = print,
) -> tuple[dict, dict]:
    started = time.perf_counter()
    root = Path(spec["root"]).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"整合包目录不存在：{root}")
    log(f"扫描 {root}")

    includes = [{**rule, "regex": glob_to_regex(rule["glob"])} for rule in spec.get("include", [])]
    excludes = [glob_to_regex(pattern) for pattern in spec.get("exclude", [])]
    chosen: list[tuple[Path, str, dict]] = []
    excluded = 0
    for full in root.rglob("*"):
        if full.is_symlink() or not full.is_file():
            continue
        relative = full.relative_to(root).as_posix()
        if any(rule.match(relative) for rule in excludes):
            excluded += 1
            continue
        include = next((rule for rule in includes if rule["regex"].match(relative)), None)
        if include:
            chosen.append((full, relative, include))
    log(f"命中 {len(chosen)} 个文件，排除 {excluded} 个")

    cache_file = spec_path.resolve().parent / f'.hashcache-{spec["pack"]["id"]}.json'
    cache = read_json(cache_file, {})
    fresh_cache: dict[str, dict] = {}

    def inspect(entry: tuple[Path, str, dict]) -> tuple[dict, bool]:
        full, relative, rule = entry
        info = full.stat()
        mtime_ms = info.st_mtime_ns / 1_000_000
        previous = cache.get(relative)
        is_cached = bool(previous and previous.get("size") == info.st_size and previous.get("mtimeMs") == mtime_ms)
        sha1 = previous["sha1"] if is_cached else hash_file(full)
        fresh_cache[relative] = {"size": info.st_size, "mtimeMs": mtime_ms, "sha1": sha1}
        item = {
            "path": relative,
            "size": info.st_size,
            "sha1": sha1,
            "policy": rule.get("policy", "Managed"),
            "group": rule.get("group"),
            "label": rule.get("label"),
        }
        return compact(item), is_cached

    with ThreadPoolExecutor(max_workers=max(4, os.cpu_count() or 4)) as pool:
        inspected = list(pool.map(inspect, chosen))
    files = [item for item, _ in inspected]
    cached = sum(1 for _, hit in inspected if hit)
    hashed = len(inspected) - cached

    mapped: list[tuple[dict, bytes]] = []
    for mapping in spec.get("map", []):
        source = root.joinpath(*mapping["from"].replace("\\", "/").split("/"))
        if not source.is_file():
            log(f'  !! 映射源不存在，跳过：{mapping["from"]}')
            continue
        content = source.read_bytes()
        if mapping.get("rewriteVersionId"):
            try:
                data = json.loads(content.decode("utf-8-sig"))
                old = data.get("id")
                if old != spec["minecraft"]["versionId"]:
                    data["id"] = spec["minecraft"]["versionId"]
                    content = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
                    log(f'  版本 id："{old}" -> "{data["id"]}"')
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                log(f"  !! 版本 id 改写失败，按原样发布：{error}")
        files.append({
            "path": mapping["to"].replace("\\", "/"),
            "size": len(content),
            "sha1": hashlib.sha1(content).hexdigest(),
            "policy": mapping.get("policy", "Managed"),
        })
        mapped.append((mapping, content))
        log(f'  映射 {mapping["from"]} -> {mapping["to"]}')

    try:
        cache_file.write_text(json.dumps(fresh_cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    files.sort(key=lambda item: item["path"].casefold())
    reconcile_overlay_policies(files, spec, log)

    pack = copy_dict(spec["pack"])
    pack["released"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    manifest = compact({
        "schema": 1,
        "pack": pack,
        "minecraft": copy_dict(spec.get("minecraft", {})),
        "java": copy_dict(spec.get("java", {})),
        "servers": copy_dict(spec.get("servers", [])),
        "launcher": copy_dict(spec.get("launcher")),
        "files": files,
        "prune": copy_dict(spec.get("prune", [])),
        "overlays": copy_dict(spec.get("overlays", [])),
        "notice": spec.get("notice"),
    })
    if publish_dir:
        publish_files(root, publish_dir.resolve(), files, mapped, use_hard_links, log)
    stats = {
        "files": len(files),
        "bytes": sum(item["size"] for item in files),
        "hashed": hashed,
        "cached": cached,
        "seconds": time.perf_counter() - started,
    }
    return manifest, stats


def copy_dict(value):
    return json.loads(json.dumps(value)) if value is not None else None
