"""Private NeoForge runtime helpers extracted from the verified native MC QA runners.

No accounts, launcher broker, production config, downloads, process killing or MCP
connection are required. Sources stay read-only; every writable path is a new lab.
"""
from __future__ import annotations

import ctypes
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import uuid
import zipfile
import re


def read_json(path: Path):
    # Windows readers can briefly collide with atomic replacement.
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    for attempt in range(10):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(.02)


def new_lab(project: Path, instance: Path, sources: list[Path]):
    project = project.resolve(strict=True)
    instance = instance.resolve()
    allowed = (project / "build" / "local-mc-debug").resolve()
    if instance == allowed or allowed not in instance.parents:
        raise ValueError("Instance must be a NEW child of <project-root>/build/local-mc-debug")
    if instance.exists():
        raise ValueError("Instance already exists; keep its evidence and choose a new name")
    for source in sources:
        source = source.resolve(strict=True)
        if instance == source or instance in source.parents or source in instance.parents:
            raise ValueError("Instance and source runtimes/world must not overlap")
    instance.mkdir(parents=True)
    return project, instance


def owned_lab(instance: Path):
    root = instance.resolve(strict=True)
    marker = read_json(root / "local-mc-owner.json")
    if not marker or marker.get("schema") != 1 or not marker.get("runId"):
        raise ValueError("Not a local_mc_debug owned lab")
    if Path(marker["instanceRoot"]).resolve() != root:
        raise ValueError("Owner marker path does not match resolved instance")
    allowed = (Path(marker["projectRoot"]) / "build/local-mc-debug").resolve()
    if allowed not in root.parents:
        raise ValueError("Owner marker is outside its project QA root")
    roles=marker.get("roles",[])
    if not roles or len(set(roles))!=len(roles) or any(not isinstance(role,str) or not re.fullmatch(r"server|host|guest|client[0-9]{2,}",role) for role in roles):
        raise ValueError("Invalid declared role in owner marker")
    return root, marker


def resources():
    result = {"availableMemoryMiB": None, "gpuAdapters": [], "minecraftProcesses": [],
              "minecraftProcessInspection": "unavailable (optional psutil not installed)"}
    if os.name == "nt":
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in
                ("totalPhys", "availPhys", "totalPage", "availPage", "totalVirtual", "availVirtual", "extended")]
        memory = Memory(); memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            result["availableMemoryMiB"] = memory.availPhys // (1024 * 1024)
            result["totalMemoryMiB"] = memory.totalPhys // (1024 * 1024)
        # Registry GPU inventory avoids the WMI timeout that blocked 131.
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Video") as base:
                for index in range(winreg.QueryInfoKey(base)[0]):
                    key = winreg.EnumKey(base, index)
                    try:
                        with winreg.OpenKey(base, key + r"\0000") as adapter:
                            name = winreg.QueryValueEx(adapter, "DriverDesc")[0]
                            size = winreg.QueryValueEx(adapter, "HardwareInformation.qwMemorySize")[0]
                            if isinstance(size, bytes): size = int.from_bytes(size, "little")
                            row = {"name": str(name), "dedicatedMemoryMiB": int(size) // (1024 * 1024)}
                            if row not in result["gpuAdapters"]: result["gpuAdapters"].append(row)
                    except OSError:
                        continue
        except OSError:
            pass
    else:
        result["availableMemoryMiB"] = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // (1024 * 1024)
    try:
        import psutil
        result["minecraftProcessInspection"] = "psutil native process inspection; not a count of all Java"
        for process in psutil.process_iter(["pid", "name", "cmdline", "memory_info", "cwd"]):
            try:
                info = process.info
                if "java" not in (info["name"] or "").lower(): continue
                arguments = info["cmdline"] or []
                looks_mc = any("net.minecraft" in x or "BootstrapLauncher" in x or "neoforge/" in x.replace("\\", "/") for x in arguments)
                if not looks_mc:
                    for argument in arguments:
                        if not argument.startswith("@"): continue
                        path = Path(argument[1:])
                        if not path.is_absolute(): path = Path(info["cwd"] or ".") / path
                        if path.is_file() and path.stat().st_size < 256 * 1024:
                            content = path.read_text(encoding="utf-8", errors="replace")
                            if "BootstrapLauncher" in content or "net.minecraft" in content: looks_mc = True
                if looks_mc:
                    result["minecraftProcesses"].append({"pid": info["pid"], "residentMiB": info["memory_info"].rss // (1024 * 1024)})
            except (psutil.Error, OSError):
                continue
    except ImportError:
        pass
    result["gpuTelemetry"]=[]
    utility=shutil.which("nvidia-smi")
    if utility:
        try:
            query=subprocess.run([utility,"--query-gpu=index,name,memory.total,memory.free,memory.used,utilization.gpu","--format=csv,noheader,nounits"],capture_output=True,text=True,timeout=5)
            if query.returncode==0:
                for cells in csv.reader(query.stdout.splitlines()):
                    if len(cells)!=6:continue
                    index,name,total,free,used,utilization=[cell.strip() for cell in cells]
                    result["gpuTelemetry"].append({"index":index,"name":name,"totalMiB":int(total),"freeMiB":int(free),"usedMiB":int(used),"utilizationPercent":int(utilization)})
        except (OSError,ValueError,subprocess.TimeoutExpired):
            pass
    return result


def allowed(rules):
    if not rules: return True
    answer = False
    platform_name = "windows" if os.name == "nt" else "linux"
    for rule in rules:
        platform = rule.get("os", {})
        if platform.get("name", platform_name) != platform_name: continue
        if platform.get("arch", "x86_64") not in ("x86_64", "amd64"): continue
        if any((key == "has_custom_resolution") != value for key, value in rule.get("features", {}).items()): continue
        answer = rule.get("action") == "allow"
    return answer


def client_metadata(game, version, seen=None):
    seen = set() if seen is None else seen
    if version in seen: raise ValueError("Version inheritance cycle")
    seen.add(version)
    child = read_json(game / "versions" / version / (version + ".json"))
    if not child: raise ValueError("Missing/invalid installed version metadata: " + version)
    if "inheritsFrom" not in child: return child
    parent = client_metadata(game, child["inheritsFrom"], seen)
    merged = {**parent, **child}
    merged["libraries"] = parent.get("libraries", []) + child.get("libraries", [])
    merged["arguments"] = {key: parent.get("arguments", {}).get(key, []) + child.get("arguments", {}).get(key, []) for key in ("jvm", "game")}
    merged.setdefault("jar", parent.get("jar", child["inheritsFrom"]))
    return merged


def client_libraries(game, version, metadata):
    jars = [game / "libraries" / entry["downloads"]["artifact"]["path"] for entry in metadata["libraries"]
            if allowed(entry.get("rules")) and entry.get("downloads", {}).get("artifact")]
    client_version = metadata.get("jar", version)
    jars.append(game / "versions" / client_version / (client_version + ".jar"))
    jars = list(dict.fromkeys(jars))
    missing = [str(jar) for jar in jars if not jar.is_file()]
    if missing: raise ValueError("Installed client library missing: " + missing[0])
    return jars


def javac_classpath(server, game, neoforge, libraries):
    neo = server / f"libraries/net/neoforged/neoforge/{neoforge}/neoforge-{neoforge}-server.jar"
    mapped = server / "libraries/net/minecraft/server/1.21.1-20240808.144430/server-1.21.1-20240808.144430-srg.jar"
    mapped_client = game / "libraries/net/minecraft/client/1.21.1-20240808.144430/client-1.21.1-20240808.144430-srg.jar"
    neo_client = game / f"libraries/net/neoforged/neoforge/{neoforge}/neoforge-{neoforge}-client.jar"
    cp = [neo_client, mapped_client, neo, mapped, *libraries,
          *[p for p in (server / "libraries").rglob("*.jar") if "/net/minecraft/" not in p.as_posix() and p != neo]]
    if any(not p.is_file() for p in cp[:4]): raise ValueError("Installed mapped MC 1.21.1 + NeoForge server/client jars required")
    return os.pathsep.join(map(str, dict.fromkeys(cp)))


def argfile(path, arguments):
    path.write_text("\n".join('"' + str(value).replace("\\", "/").replace('"', '\\"') + '"' for value in arguments), encoding="utf-8")


def compile_agent(project, instance, jdk, classpath):
    sources = sorted((project / "tests/local-mc-debug").rglob("*.java"))
    if not sources: raise ValueError("QA Java sources are missing from project root")
    classes = instance / "qa-classes"; classes.mkdir()
    args = instance / "javac.args"
    argfile(args, ["--release", "21", "-encoding", "UTF-8", "-proc:none", "-classpath", classpath, "-d", classes, *sources])
    with (instance / "compile.log").open("w", encoding="utf-8") as log:
        completed=subprocess.run([str(jdk / "bin/javac.exe"), "@" + str(args)], stdout=log, stderr=subprocess.STDOUT)
    if completed.returncode: raise RuntimeError("QA compilation failed; inspect " + str(instance / "compile.log"))
    jar = instance / "local-mc-debug-QA-ONLY.jar"
    with zipfile.ZipFile(jar, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("META-INF/neoforge.mods.toml", 'modLoader="javafml"\nloaderVersion="[4,)"\nlicense="MIT"\n[[mods]]\nmodId="local_mc_debug_qa"\nversion="0.0.1"\ndisplayName="Local MC debug QA ONLY"\n[[mixins]]\nconfig="local_mc_debug.mixins.json"\n')
        archive.writestr("local_mc_debug.mixins.json", json.dumps({"required": True, "minVersion": "0.8", "package": "net.muxigame.localdebug.mixin", "compatibilityLevel": "JAVA_21", "mixins": ["HardwareWmiTimeoutQAMixin"], "client": ["HiddenWindowMixin","NormalStopRequestQAMixin"], "injectors": {"defaultRequire": 1}}))
        for file in classes.rglob("*.class"): archive.write(file, file.relative_to(classes).as_posix())
    return jar


def server_arguments(server, neoforge, memory, unix_temp):
    platform = "win" if os.name == "nt" else "unix"
    path = server / f"libraries/net/neoforged/neoforge/{neoforge}/{platform}_args.txt"
    arguments = path.read_text(encoding="utf-8").split()
    prefix = (server / "libraries").as_posix()
    arguments = [value.replace("libraries/", prefix + "/") if "libraries/" in value else value for value in arguments]
    arguments = [("-DlibraryDirectory=" + prefix) if value == "-DlibraryDirectory=libraries" else value for value in arguments]
    return ["-Xms256M", f"-Xmx{memory}M", "-XX:ActiveProcessorCount=3", "-Dfile.encoding=UTF-8",
            "-Djava.awt.headless=true", "-Djdk.net.unixdomain.tmpdir=" + str(unix_temp), *arguments, "nogui"]


def offline_uuid(name):
    digest = bytearray(hashlib.md5(("OfflinePlayer:" + name).encode()).digest())
    digest[6] = (digest[6] & 15) | 48; digest[8] = (digest[8] & 63) | 128
    return uuid.UUID(bytes=bytes(digest)).hex


def client_arguments(game, version, metadata, libraries, instance, name, memory, natives, unix_temp):
    values = {"auth_player_name": name, "auth_uuid": offline_uuid(name), "auth_access_token": "0",
              "version_name": version, "game_directory": str(instance), "assets_root": str(game / "assets"),
              "assets_index_name": metadata["assetIndex"]["id"], "clientid": "", "auth_xuid": "", "user_type": "legacy",
              "version_type": "release", "resolution_width": "960", "resolution_height": "540", "natives_directory": str(natives),
              "launcher_name": "local-mc-isolated-debug", "launcher_version": "1", "classpath": os.pathsep.join(map(str, libraries)),
              "library_directory": str(game / "libraries"), "classpath_separator": os.pathsep}
    def expand(items):
        output = []
        for item in items:
            if isinstance(item, dict):
                if not allowed(item.get("rules")): continue
                parts = item["value"] if isinstance(item["value"], list) else [item["value"]]
            else: parts = [item]
            for part in parts:
                for key, value in values.items(): part = part.replace("${" + key + "}", value)
                if "${" in part: raise ValueError("Unresolved installed client argument")
                output.append(part)
        return output
    return ["-Xms256M", f"-Xmx{memory}M", "-XX:ActiveProcessorCount=3", "-Dfile.encoding=UTF-8",
            "-Djdk.net.unixdomain.tmpdir=" + str(unix_temp), *expand(metadata["arguments"]["jvm"]),
            metadata["mainClass"], *expand(metadata["arguments"]["game"])]


def copy_inputs(lab, mods, data):
    (lab / "mods").mkdir(); (lab / "config").mkdir()
    names = set()
    for jar in mods:
        if jar.name.casefold() in names: raise ValueError("Duplicate mod jar filename: " + jar.name)
        names.add(jar.name.casefold()); shutil.copy2(jar, lab / "mods" / jar.name)
    for folder in data:
        if folder.name in ("mods", "config", "saves", "world", "libraries", "versions", "logs", "assets", "natives"):
            raise ValueError("Use explicit world/natives parameters; data-dir cannot be " + folder.name)
        shutil.copytree(folder, lab / folder.name)


def port_free(port):
    # Bind, rather than connect-only, catches occupied sockets that are not listening yet.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", port))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
