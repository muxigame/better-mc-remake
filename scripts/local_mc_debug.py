"""Launch/observe/stop private real Minecraft clients and a dedicated server.

The launch metadata, native callbacks, WMI workaround and orderly shutdown are
adapted from the already verified native_pipeline_runtime / Outbreak QA runners.
This is not a production launcher, SSO proof, visual test or OS key injector.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

import local_mc_runtime as rt


def emit(event, **data):
    print(json.dumps({"event": event, **data}, ensure_ascii=False), flush=True)


def marker_save(root, marker):
    rt.write_json(root / "local-mc-owner.json", marker)


def command(root, role, body, timeout=30):
    root, marker = rt.owned_lab(root)
    if not isinstance(body,dict):raise ValueError("Callback input must be a JSON object")
    if role not in marker["roles"]: raise ValueError("Unknown owned role: " + role)
    path = root / "coordinator" / ("command-" + role + ".json")
    old = rt.read_json(path) or {}
    number = max(0, int(old.get("id", 0))) + 1
    if number >= 2_000_000_000: raise ValueError("Lab has already been asked to stop")
    payload = {**body, "id": number, "runId": marker["runId"]}
    if "type" not in payload: raise ValueError("Callback requires type")
    rt.write_json(path, payload)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = rt.read_json(root / "coordinator" / f"result-{role}-{number}.json")
        if result and result.get("runId") == marker["runId"]:
            if not result.get("ok"): raise RuntimeError("Native callback failed: " + str(result.get("error")))
            return result
        if (root / "stop-request.json").exists(): raise RuntimeError("Owned lab stop requested")
        time.sleep(.1)
    raise TimeoutError("Native callback timed out for " + role + "; inspect role boot.log and status")


def stop_files(root, marker):
    rt.write_json(root / "stop-request.json", {"runId": marker["runId"], "requestedAt": time.time(), "normalStopOnly": True})
    for role in marker["roles"]:
        rt.write_json(root / "coordinator" / f"command-{role}.json", {"id": 2_000_000_000, "runId": marker["runId"], "type": "stop"})


def value_at(data, path):
    for part in path.split("."):
        data = data[int(part)] if isinstance(data, list) else data[part]
    return data


def assert_receipt(result, expected):
    for path, value in expected.items():
        if value_at(result, path) != value: raise AssertionError(f"Receipt {path} does not equal {value!r}")


def wait_condition(label, predicate, timeout, processes=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(): return
        if processes:
            for role, process in processes.items():
                if process.poll() is not None: raise RuntimeError(f"Owned {role} exited before {label}: {process.returncode}")
        time.sleep(.2)
    raise TimeoutError(label + " timed out; inspect boot.log, logs/latest.log, crash-reports and coordinator/status-*.json")


def smoke(root, marker, processes, timeout):
    evidence = []
    def take(role, payload):
        result = command(root, role, payload, timeout)
        evidence.append(result); rt.write_json(root / "callback-receipts.json", evidence)
        return result
    server = take("server", {"type": "observe"})
    assert server["status"]["players"] == len(marker["clientNames"])
    assert all(p["network"] for p in server["status"]["playerStates"])
    for role in marker["clientNames"]:
        result = take(role, {"type": "observe"})
        assert result["nativeMinecraftClient"] and result["status"]["connected"]
        assert result["status"]["playersSeen"] == len(marker["clientNames"])
        if marker["hidden"]:
            assert not result["status"]["glfwVisible"] and not result["status"]["glfwFocused"]
    host = marker["clientNames"]["host"]
    take("server", {"type": "inventory-set", "player": host, "slot": 0, "item": "minecraft:diamond", "count": 3})
    take("host", {"type": "select", "slot": 0})
    take("host", {"type": "drop", "all": False})
    row = take("server", {"type": "observe"})
    assert row["status"]["byName"][host]["inventoryBySlot"]["0"]["count"] == 2, "Native Q did not remove exactly one"
    take("host", {"type": "drop", "all": True})
    row = take("server", {"type": "observe"})
    assert "0" not in row["status"]["byName"][host]["inventoryBySlot"], "Native Ctrl+Q did not remove the remaining stack"
    assert row["status"]["looseDiamonds"] == 3, "Native dropped stack count changed"
    emit("smoke_passed", realClients=len(marker["clientNames"]), nativeQAndCtrlQ=True, physicalOSInput=False)
    return {"passed": True, "receipts": len(evidence), "actualNetworkClients": len(marker["clientNames"]),
            "syntheticServerPlayers": False, "assistedInventoryFixture": True, "nativeQAndCtrlQ": True,
            "physicalOSInput": False, "visualAcceptance": False, "SSOAcceptance": False, "gameplayAcceptance": False}


def scenario(root, path, timeout):
    data = rt.read_json(path)
    if not data or not isinstance(data.get("steps"), list): raise ValueError("Scenario requires steps[]")
    evidence = []
    for step in data["steps"]:
        body = {key: value for key, value in step.items() if key not in ("role", "expect", "waitSeconds", "timeoutSeconds")}
        wait = step.get("waitSeconds", 0)
        if not isinstance(wait, (int, float)) or wait < 0 or wait > 60: raise ValueError("waitSeconds outside 0..60")
        end = time.monotonic() + wait
        while time.monotonic() < end:
            if (root / "stop-request.json").exists(): raise RuntimeError("Owned stop requested")
            time.sleep(.1)
        result = command(root, step["role"], body, step.get("timeoutSeconds", timeout))
        assert_receipt(result, step.get("expect", {}))
        evidence.append(result); rt.write_json(root / "callback-receipts.json", evidence)
    return {"passed": True, "scenario": path.name, "receipts": len(evidence), "physicalOSInput": False,
            "visualAcceptance": False, "SSOAcceptance": False, "assertions": sum(len(x.get("expect", {})) for x in data["steps"])}


def run(args):
    if args.clients < 1: raise ValueError("At least one client required; no fixed <4 Java/process limit")
    if not 1<=args.port<=65535:raise ValueError("Port outside 1..65535")
    if args.reserve_memory_mb<0 or args.gpu_per_client_mb<1 or len(args.client_name)>args.clients:raise ValueError("Invalid resource reservation/client-name count")
    if min(args.server_memory_mb, args.client_memory_mb) < 512: raise ValueError("MC heap reservation must be at least 512 MiB")
    server, game, jdk = [path.resolve(strict=True) for path in (args.server_runtime, args.client_game, args.java_home)]
    exe = "java.exe" if os.name == "nt" else "java"
    if not (jdk / "bin" / exe).is_file(): raise ValueError("Installed Java 21 JDK required")
    java_version = subprocess.run([str(jdk / "bin" / exe), "-version"], capture_output=True, text=True, check=True)
    if not re.search(r'version "21[.\"]', java_version.stderr + java_version.stdout): raise ValueError("This MC 1.21.1 QA runner requires Java 21")
    if not args.accept_eula: raise ValueError("Pass --accept-eula for this new private MC server")
    mods = [path.resolve(strict=True) for path in args.mod]
    data = [path.resolve(strict=True) for path in args.data_dir]
    rt.port_free(args.port)
    capacity = rt.resources()
    heap = args.server_memory_mb + args.clients * args.client_memory_mb
    required = int(heap * 1.25) + args.reserve_memory_mb
    if capacity["availableMemoryMiB"] is None or capacity["availableMemoryMiB"] < required:
        raise ValueError(f"Available physical memory {capacity['availableMemoryMiB']} MiB is below {required} MiB reservation; adjust heaps/client count after checking real MC workloads")
    gpu_budget=args.gpu_budget_mb
    if gpu_budget is None and capacity["gpuTelemetry"]:gpu_budget=max(row["freeMiB"] for row in capacity["gpuTelemetry"])
    if gpu_budget is not None and args.clients * args.gpu_per_client_mb > gpu_budget:
        raise ValueError("Client GPU reservation exceeds supplied available VRAM budget")
    capacity.update({"requestedClients": args.clients, "requestedHeapMiB": heap, "reservedPhysicalMiB": required,
                     "gpuBudgetMiB": gpu_budget, "gpuReservationPerClientMiB": args.gpu_per_client_mb,
                     "gpuBudgetSource": "operator" if args.gpu_budget_mb is not None else "nvidia-smi measured free" if gpu_budget is not None else "unmeasured; operator must assess GPU workload",
                     "gpuReservationIsEstimate": True, "fixedJavaCountLimit": False})
    emit("capacity", **capacity)
    sources = [server, game, jdk, *mods, *data]
    if args.world: sources.append(args.world)
    project, root = rt.new_lab(args.project_root, args.instance_root, sources)
    coordinator = root / "coordinator"; coordinator.mkdir()
    names = {}
    for index in range(args.clients):
        role = "host" if index == 0 else "guest" if index == 1 else f"client{index+1:02}"
        name = args.client_name[index] if index < len(args.client_name) else "DebugHost" if index == 0 else "DebugGuest" if index == 1 else f"DebugClient{index+1:02}"
        if not re.fullmatch(r"[A-Za-z0-9_]{1,16}", name) or name in names.values(): raise ValueError("Unique offline client names must be 1..16 ASCII letters/digits/underscore")
        names[role] = name
    marker = {"schema": 1, "runId": uuid.uuid4().hex, "projectRoot": str(project), "instanceRoot": str(root),
              "createdAt": time.time(), "roles": ["server", *names], "clientNames": names, "hidden": not args.visible,
              "serverPort": args.port, "productionMutation": False, "offlineLoopback": True,
              "processes": {}, "resources": capacity, "mode": args.mode,
              "inputArtifacts": [{"file": jar.name, "sha256": rt.sha256(jar)} for jar in mods]}
    marker["runtime"]={"serverRuntime":str(server),"clientGame":str(game),"javaHome":str(jdk),"javaVersion":(java_version.stderr+java_version.stdout).strip(),"version":args.version,"neoforge":args.neoforge}
    marker_save(root, marker)
    metadata = rt.client_metadata(game, args.version)
    libraries = rt.client_libraries(game, args.version, metadata)
    agent = rt.compile_agent(project, root, jdk, rt.javac_classpath(server, game, args.neoforge, libraries))
    marker["qaAgentSha256"]=rt.sha256(agent);marker_save(root,marker)
    natives = args.natives_dir.resolve(strict=True) if args.natives_dir else game / "versions" / args.version / (args.version + "-natives")
    if not natives.is_dir(): raise ValueError("Installed natives directory missing; pass --natives-dir")
    unix_temp = args.unix_temp.resolve() if args.unix_temp else root / "unix-temp"
    if not unix_temp.exists(): unix_temp.mkdir(parents=True)
    if len(str(unix_temp)) > 95:
        emit("unix_temp_warning", message="Use a short existing --unix-temp path if WEPoll/AF_UNIX fails; no machine-wide service restart")
    server_home = root / "server"; server_home.mkdir(); rt.copy_inputs(server_home, [*mods, agent], data)
    if args.world: shutil.copytree(args.world.resolve(strict=True), server_home / "qa-world")
    (server_home / "eula.txt").write_text("eula=true\n")
    props = f"server-ip=127.0.0.1\nserver-port={args.port}\nonline-mode=false\nenforce-secure-profile=false\nlevel-name=qa-world\ngamemode=survival\ndifficulty=normal\nview-distance=3\nsimulation-distance=3\nmax-tick-time=120000\nspawn-protection=0\nallow-flight=true\nspawn-monsters=false\nspawn-animals=false\n"
    if not args.world: props += 'level-type=minecraft:flat\ngenerate-structures=false\ngenerator-settings={"layers":[{"block":"minecraft:bedrock","height":1},{"block":"minecraft:stone","height":3}],"biome":"minecraft:plains"}\n'
    (server_home / "server.properties").write_text(props)
    # This explicitly isolates game membership QA from real launcher/account/SSO credentials.
    fixture_config = {"schema": 1, "features": {"identity": {"enabled": False}, "login": {"enabled": False}}}
    rt.write_json(server_home / "config/muxi-game-core.json", fixture_config)
    prefix = ["-Dqa.local.root=" + str(root), "-Dqa.local.runId=" + marker["runId"]]
    server_args = server_home / "launch.args"
    rt.argfile(server_args, [*prefix, "-Dqa.local.role=server", *rt.server_arguments(server, args.neoforge, args.server_memory_mb, unix_temp)])
    clients = {}
    for role, name in names.items():
        lab = root / role; lab.mkdir(); rt.copy_inputs(lab, [*mods, agent], data)
        rt.write_json(lab / "config/muxi-game-core.json", fixture_config)
        (lab / "config/fml.toml").write_text('earlyWindowControl = false\nearlyWindowProvider = ""\nversionCheck = false\n')
        (lab / "options.txt").write_text("lang:zh_cn\nmaxFps:30\nenableVsync:false\nonboardAccessibility:false\nsoundCategory_master:0.0\nfullscreen:false\npauseOnLostFocus:false\nrenderDistance:3\nsimulationDistance:3\ngraphicsMode:0\n")
        shutil.copytree(natives, lab / "natives")
        launch = [*prefix, f"-Dqa.local.role={role}", f"-Dqa.local.port={args.port}", "-Dqa.local.hidden=" + str(not args.visible).lower(),
                  *rt.client_arguments(game, args.version, metadata, libraries, lab, name, args.client_memory_mb, lab / "natives", unix_temp)]
        rt.argfile(lab / "launch.args", launch); clients[role] = lab
    processes = {}; logs = {}; report = {"runId": marker["runId"], "passed": False, "productionMutation": False,
                                         "instanceRoot": str(root), "normalStopOnly": True, "physicalOSInput": False, "visualAcceptance": False}
    def start(role, lab):
        logs[role] = (lab / "boot.log").open("w", encoding="utf-8")
        environment=dict(os.environ)
        for key in ("MUXI_TERMINAL_GAME_CREDENTIAL","MUXI_TERMINAL_CREDENTIAL_PIPE","MUXI_TERMINAL_CREDENTIAL_BROKER","MUXI_SSO_QA_CONTROL","MUXI_LAUNCHER_QA_CONTROL","MUXI_GAME_QA_CONTROL"):
            environment.pop(key,None)
        process = subprocess.Popen([str(jdk / "bin" / exe), "@" + str(lab / "launch.args")], cwd=lab, env=environment,
                                   stdin=subprocess.PIPE if role == "server" else subprocess.DEVNULL,
                                   stdout=logs[role], stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        processes[role] = process
        row = {"pid": process.pid, "root": str(lab), "startedAt": time.time()}
        try:
            import psutil
            row["processCreateTime"] = psutil.Process(process.pid).create_time()
        except (ImportError, OSError): pass
        marker["processes"][role] = row; marker_save(root, marker)
        emit("started", role=role, pid=process.pid, instance=str(lab))
    try:
        start("server", server_home)
        def ready():
            try: return "Done (" in (server_home / "boot.log").read_text(encoding="utf-8", errors="replace") and bool(rt.read_json(coordinator / "status-server.json"))
            except OSError: return False
        wait_condition("server Done + actual tick callback", ready, args.boot_timeout, processes)
        for role, lab in clients.items(): start(role, lab)
        def connected():
            status = rt.read_json(coordinator / "status-server.json")
            if not status or status.get("players") != args.clients: return False
            return all((rt.read_json(coordinator / f"status-{role}.json") or {}).get("connected") for role in names)
        wait_condition("real native client connections", connected, args.boot_timeout, processes)
        emit("ready", instance=str(root), actualNetworkClients=args.clients, roles=list(names))
        if args.mode == "smoke": report["acceptance"] = smoke(root, marker, processes, args.command_timeout)
        elif args.mode == "scenario":
            if not args.scenario: raise ValueError("--mode scenario requires --scenario")
            report["acceptance"] = scenario(root, args.scenario.resolve(strict=True), args.command_timeout)
        else:
            deadline = time.monotonic() + args.hold_seconds; last = 0
            while time.monotonic() < deadline and not (root / "stop-request.json").exists():
                if any(process.poll() is not None for process in processes.values()): raise RuntimeError("Owned MC exited during hold")
                if time.monotonic() - last > 15:
                    emit("hold", status=rt.read_json(coordinator / "status-server.json")); last = time.monotonic()
                time.sleep(.25)
            report["acceptance"] = {"passed": True, "scope": "connections and requested hold only", "gameplayAcceptance": False}
        report["passed"] = True
    except (Exception, KeyboardInterrupt) as failure:
        report["error"] = str(failure); emit("failed", error=str(failure), evidence=str(root))
    finally:
        stop_files(root, marker)
        server_process = processes.get("server")
        if server_process and server_process.poll() is None:
            try: server_process.stdin.write("stop\n"); server_process.stdin.flush()
            except OSError: pass
        deadline = time.monotonic() + args.shutdown_timeout
        while any(process.poll() is None for process in processes.values()) and time.monotonic() < deadline: time.sleep(.2)
        report["exitCodes"] = {role: process.poll() for role, process in processes.items()}
        report["normalExit"] = len(processes) == len(marker["roles"]) and all(code == 0 for code in report["exitCodes"].values())
        report["normalStopBlocked"] = [{"role": role, "pid": process.pid} for role, process in processes.items() if process.poll() is None]
        report["clientRenderers"]={role:(rt.read_json(coordinator/f"status-{role}.json") or {}).get("glRenderer") for role in names}
        report["passed"] = report["passed"] and report["normalExit"]
        for handle in logs.values(): handle.close()
        rt.write_json(root / "run-result.json", report); emit("result", **report)
    return 0 if report["passed"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    launch = sub.add_parser("run")
    launch.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    for name in ("instance-root", "server-runtime", "client-game", "java-home"): launch.add_argument("--" + name, type=Path, required=True)
    launch.add_argument("--version", default="BatterMC5Remake"); launch.add_argument("--neoforge", default="21.1.250")
    launch.add_argument("--port", type=int, required=True); launch.add_argument("--clients", type=int, default=2)
    launch.add_argument("--client-name", action="append", default=[])
    launch.add_argument("--mod", type=Path, action="append", default=[]); launch.add_argument("--data-dir", type=Path, action="append", default=[])
    launch.add_argument("--world", type=Path); launch.add_argument("--natives-dir", type=Path); launch.add_argument("--unix-temp", type=Path)
    launch.add_argument("--accept-eula", action="store_true"); launch.add_argument("--visible", action="store_true")
    launch.add_argument("--mode", choices=("smoke", "hold", "scenario"), default="smoke"); launch.add_argument("--scenario", type=Path)
    launch.add_argument("--server-memory-mb", type=int, default=2048); launch.add_argument("--client-memory-mb", type=int, default=2304)
    launch.add_argument("--reserve-memory-mb", type=int, default=2048)
    launch.add_argument("--gpu-budget-mb", type=int); launch.add_argument("--gpu-per-client-mb", type=int, default=512)
    launch.add_argument("--boot-timeout", type=int, default=180); launch.add_argument("--command-timeout", type=int, default=30)
    launch.add_argument("--shutdown-timeout", type=int, default=120); launch.add_argument("--hold-seconds", type=int, default=600)
    for operation in ("status", "stop", "command"):
        action = sub.add_parser(operation); action.add_argument("--instance-root", type=Path, required=True)
        if operation == "stop": action.add_argument("--wait-seconds", type=int, default=30)
        if operation == "command":
            action.add_argument("--role", required=True); action.add_argument("--json-file", type=Path, required=True)
            action.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        if args.operation == "run": return run(args)
        root, marker = rt.owned_lab(args.instance_root)
        if args.operation == "status":
            emit("status", owner=marker, roles={role: rt.read_json(root / "coordinator" / f"status-{role}.json") for role in marker["roles"]}, result=rt.read_json(root / "run-result.json")); return 0
        if args.operation == "command":
            if marker["mode"] != "hold": raise ValueError("External commands require --mode hold; do not race an automatic scenario controller")
            emit("callback", receipt=command(root, args.role, rt.read_json(args.json_file), args.timeout)); return 0
        stop_files(root, marker)
        deadline = time.monotonic() + args.wait_seconds
        while time.monotonic() < deadline:
            report = rt.read_json(root / "run-result.json")
            if report and report.get("runId") == marker["runId"]:
                emit("stop_result", result=report); return 0 if report.get("normalExit") else 1
            time.sleep(.2)
        emit("stop_requested", normalExitVerified=False, message="Normal stop requested only for marked lab; original runner must collect exit codes. Inspect the listed owned instances if blocked.")
        return 1
    except (Exception, KeyboardInterrupt) as error:
        emit("error", error=str(error)); return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
