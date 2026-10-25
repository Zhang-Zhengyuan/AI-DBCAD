#!/usr/bin/env python3
"""Local macOS setup and lifecycle; keeps credentials and data in .local/macos."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local" / "macos"
BACKEND = ROOT / "CAD_DB" / "backend"
PYTHON = BACKEND / ".venv" / "bin" / "python"
WORKER = ROOT / "build" / "macos" / "Release" / "dbcad_worker"
SETTINGS = LOCAL / "settings.json"
STATE = LOCAL / "processes.json"
IMAGE = "neo4j:5.26.12-community"
LABEL = "org.inferracad.workspace"


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Open with restrictive permissions before writing any credentials.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    path.chmod(0o600)


def free_port(preferred, excluded=()):
    for port in range(preferred, preferred + 100):
        if port in excluded:
            continue
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                pass
    raise RuntimeError(f"No available port near {preferred}")


def config():
    if SETTINGS.exists():
        data = json.loads(SETTINGS.read_text())
        if data["root"] != str(ROOT):
            raise RuntimeError("Workspace moved; stop the original instance before migrating its local configuration.")
        return data
    LOCAL.mkdir(parents=True, exist_ok=True, mode=0o700)
    LOCAL.chmod(0o700)
    ports = {}
    for key, preferred in {"backend": 18000, "web": 18080, "bolt": 17687, "neo4j": 17474}.items():
        ports[key] = free_port(preferred, ports.values())
    data = {
        "root": str(ROOT),
        "container": "inferracad-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:10],
        "image": IMAGE,
        "ports": ports,
        "api_password": secrets.token_urlsafe(32),
        "neo4j_password": secrets.token_urlsafe(32),
    }
    save(SETTINGS, data)
    return data


def environment(data):
    return {
        **os.environ,
        "CAD_DB_WORKER_EXE": str(WORKER),
        "CAD_DB_AI_CONFIG": os.environ.get("CAD_DB_AI_CONFIG", str(LOCAL / "ai.json") if (LOCAL / "ai.json").exists() else ""),
        "CAD_DB_COMPONENT_ROOT": str(LOCAL / "components"),
        "CAD_DB_API_PASSWORD": data["api_password"],
        "CAD_DB_NEO4J_URI": f"bolt://127.0.0.1:{data['ports']['bolt']}",
        "CAD_DB_NEO4J_USER": "neo4j",
        "CAD_DB_NEO4J_PASSWORD": data["neo4j_password"],
        "CAD_DB_NEO4J_DATABASE": "neo4j",
    }


def setup(args):
    for command in ("docker", "uv", "node", "npm", "xcrun"):
        if not shutil.which(command):
            raise RuntimeError(f"Missing prerequisite: {command}")
    header = ROOT / "_deps/vendor/nlohmann/json.hpp"
    if not header.exists():
        if not args.team_kit:
            raise RuntimeError("Pass --team-kit /path/to/DBCAD-Team-Kit.zip to install the JSON header.")
        with zipfile.ZipFile(args.team_kit) as archive:
            content = archive.read("_deps/vendor/nlohmann/json.hpp")
        header.parent.mkdir(parents=True, exist_ok=True)
        header.write_bytes(content)
    config()
    run("uv", "sync", "--frozen", "--extra", "dev", "--python", "3.12", cwd=BACKEND)
    run(ROOT / "scripts/build-macos.sh", "Release")
    run(WORKER, "selftest")
    run(WORKER.parent / "mesh_tests")
    run("npm", "ci", cwd=ROOT / "web")
    run("npm", "run", "build", cwd=ROOT / "web")
    print("macOS setup complete. Start with ./DBCAD.command")


def container_info(data):
    result = subprocess.run(["docker", "inspect", data["container"]], capture_output=True, text=True)
    if result.returncode:
        return None
    info = json.loads(result.stdout)[0]
    if info["Config"].get("Labels", {}).get(LABEL) != str(ROOT):
        raise RuntimeError("Container name belongs to another workspace; refusing to change it.")
    return info


def database(data):
    run("docker", "info", "--format", "{{.ServerVersion}}", stdout=subprocess.DEVNULL)
    info = container_info(data)
    if info is None:
        for directory in ("data", "logs"):
            (LOCAL / "neo4j" / directory).mkdir(parents=True, exist_ok=True)
        image = data["image"]
        if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode:
            run("docker", "pull", "--platform", "linux/arm64", image)
        run("docker", "create", "--name", data["container"], "--platform", "linux/arm64",
            "--label", f"{LABEL}={ROOT}",
            "-p", f"127.0.0.1:{data['ports']['bolt']}:7687",
            "-p", f"127.0.0.1:{data['ports']['neo4j']}:7474",
            "--env", "NEO4J_AUTH", "--env", "NEO4J_server_memory_heap_initial__size=256m",
            "--env", "NEO4J_server_memory_heap_max__size=768m",
            "--env", "NEO4J_server_memory_pagecache_size=256m",
            "--mount", f"type=bind,source={LOCAL / 'neo4j/data'},target=/data",
            "--mount", f"type=bind,source={LOCAL / 'neo4j/logs'},target=/logs", image,
            env={**os.environ, "NEO4J_AUTH": "neo4j/" + data["neo4j_password"]},
            stdout=subprocess.DEVNULL)
    if info is None or not info["State"]["Running"]:
        run("docker", "start", data["container"], stdout=subprocess.DEVNULL)
    # A real Bolt query is required; an HTTP health response alone is insufficient.
    check = """import os
from neo4j import GraphDatabase
with GraphDatabase.driver(os.environ['CAD_DB_NEO4J_URI'], auth=('neo4j', os.environ['CAD_DB_NEO4J_PASSWORD'])) as d:
    d.verify_connectivity()
    d.execute_query('RETURN 1 AS ready', database_='neo4j')
"""
    for _ in range(90):
        if subprocess.run([str(PYTHON), "-c", check], env=environment(data), capture_output=True).returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError(f"Neo4j did not become ready. Inspect {LOCAL / 'neo4j/logs'}")


def identity(pid):
    result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def alive(record):
    return bool(record.get("identity")) and identity(record["pid"]) == record["identity"]


def stop(args):
    records = json.loads(STATE.read_text()) if STATE.exists() else {}
    for name, record in records.items():
        if not alive(record):
            continue
        if str(ROOT) not in record["identity"] or os.getpgid(record["pid"]) != record["pid"]:
            raise RuntimeError(f"Cannot verify ownership of {name}; refusing to stop it.")
        os.killpg(record["pid"], signal.SIGTERM)
        for _ in range(50):
            if not alive(record):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError(f"{name} did not stop; inspect it before restarting.")
    save(STATE, {})
    if args.database:
        data = config()
        if container_info(data):
            run("docker", "stop", data["container"], stdout=subprocess.DEVNULL)
    print("DBCAD services stopped; saved models and database retained.")


def spawn(name, argv, data, records):
    log_path = LOCAL / f"{name}.log"
    with log_path.open("ab") as log:
        process = subprocess.Popen([str(arg) for arg in argv], cwd=LOCAL, env=environment(data),
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    time.sleep(0.2)
    if process.poll() is not None:
        raise RuntimeError(f"{name} exited. Inspect {log_path}")
    records[name] = {"pid": process.pid, "identity": identity(process.pid)}
    save(STATE, records)
    return process


def start(args):
    if not PYTHON.exists() or not WORKER.exists() or not (ROOT / "web/dist/index.html").exists():
        raise RuntimeError("Run python3 scripts/macos.py setup first.")
    for command in ("docker", "node"):
        if not shutil.which(command):
            raise RuntimeError(f"Missing prerequisite: {command}")
    run("docker", "info", "--format", "{{.ServerVersion}}", stdout=subprocess.DEVNULL)
    data = config()
    stop(argparse.Namespace(database=False))
    database(data)
    for key in ("backend", "web"):
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", data["ports"][key]))
    records = {}
    backend = spawn("backend", [PYTHON, "-m", "uvicorn", "--app-dir", BACKEND, "app.main:app",
                                "--host", "127.0.0.1", "--port", str(data["ports"]["backend"])], data, records)
    for _ in range(60):
        if backend.poll() is not None:
            raise RuntimeError(f"Backend exited. Inspect {LOCAL / 'backend.log'}")
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{data['ports']['backend']}/api/v1/projects",
                                             headers={"X-API-Password": data["api_password"]})
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=2):
                break
        except (OSError, ValueError):
            time.sleep(0.5)
    else:
        raise RuntimeError(f"Backend did not become ready. Inspect {LOCAL / 'backend.log'}")
    spawn("gateway", [shutil.which("node"), ROOT / "scripts/macos-gateway.cjs"], data, records)
    url = f"http://127.0.0.1:{data['ports']['web']}/web/"
    print(f"DBCAD: {url}")
    print(f"Local logs and saved configuration: {LOCAL}")
    if args.open:
        run("open", url)


def status(args):
    data = config()
    records = json.loads(STATE.read_text()) if STATE.exists() else {}
    for name in ("backend", "gateway"):
        print(f"{name}: {'running' if alive(records.get(name, {})) else 'stopped'}")
    info = container_info(data)
    print(f"Neo4j: {'running' if info and info['State']['Running'] else 'stopped'}")
    print(f"Web: http://127.0.0.1:{data['ports']['web']}/web/")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("setup")
    command.add_argument("--team-kit", type=Path)
    command.set_defaults(func=setup)
    command = commands.add_parser("start")
    command.add_argument("--open", action="store_true")
    command.set_defaults(func=start)
    command = commands.add_parser("stop")
    command.add_argument("--database", action="store_true")
    command.set_defaults(func=stop)
    commands.add_parser("status").set_defaults(func=status)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This launcher requires macOS.")
    try:
        args.func(args)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"DBCAD: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
