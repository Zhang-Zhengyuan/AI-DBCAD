#!/usr/bin/env python3
"""Local macOS setup and lifecycle; keeps credentials and data in .local/macos."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local" / "macos"
DBCAD = ROOT / "systems" / "dbcad"
BACKEND = DBCAD / "CAD_DB" / "backend"
PYTHON = BACKEND / ".venv" / "bin" / "python"
WORKER = ROOT / "build" / "macos" / "Release" / "dbcad_worker"
SETTINGS = LOCAL / "settings.json"
STATE = LOCAL / "processes.json"
IMAGE = "neo4j:5.26.12-community"
# Persistent container identity: retain the legacy label across project renames.
LABEL = "org.inferracad.workspace"


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement avoids losing the last PID records on interruption.
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def lifecycle_lock():
    """Double-clicks and stop/start must not overwrite each other's PID records."""
    import fcntl

    LOCAL.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(LOCAL / "lifecycle.lock", os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another AI-DBCAD start/stop is in progress; wait for it to finish.") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


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


def same_workspace(path):
    """Accept an existing absolute alias, never a different checkout or missing label."""
    if not isinstance(path, str) or not path or not Path(path).is_absolute():
        return False
    try:
        return Path(path).resolve(strict=True).samefile(ROOT)
    except (OSError, RuntimeError):
        return False


def config():
    if SETTINGS.exists():
        data = json.loads(SETTINGS.read_text())
        if not same_workspace(data["root"]):
            raise RuntimeError("Workspace moved; stop the original instance before migrating its local configuration.")
        return data
    LOCAL.mkdir(parents=True, exist_ok=True, mode=0o700)
    LOCAL.chmod(0o700)
    ports = {}
    for key, preferred in {"backend": 18000, "web": 18080, "bolt": 17687, "neo4j": 17474}.items():
        ports[key] = free_port(preferred, ports.values())
    data = {
        "root": str(ROOT),
        "container": "ai-dbcad-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:10],
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
    run("npm", "ci", cwd=DBCAD / "web")
    run("npm", "run", "build", cwd=DBCAD / "web")
    print("macOS setup complete. Start with ./AI-DBCAD.command")


def container_info(data):
    result = subprocess.run(["docker", "inspect", data["container"]], capture_output=True, text=True)
    if result.returncode:
        return None
    info = json.loads(result.stdout)[0]
    if not same_workspace(info["Config"].get("Labels", {}).get(LABEL)):
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


def listener_pids(port):
    result = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                            capture_output=True, text=True)
    if result.returncode not in (0, 1):
        raise RuntimeError(f"Could not inspect listeners on port {port}.")
    return sorted({int(line) for line in result.stdout.splitlines() if line.isdigit()})


def owned_listener(pid, service, port):
    """Recover only our exact server shape, cwd and process-group leader.

    A PID or a matching port alone is never permission to terminate a process.
    The explicit legacy app-dir handles services left alive before the layout move.
    """
    before = identity(pid)
    if not before:
        return None
    command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    cwd = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                         capture_output=True, text=True)
    try:
        directories = [Path(line[1:]) for line in cwd.stdout.splitlines() if line.startswith("n")]
        if len(directories) != 1 or not directories[0].samefile(LOCAL) or os.getpgid(pid) != pid:
            return None
        argv = shlex.split(command.stdout.strip())
        if service == "gateway":
            matches = (len(argv) == 2 and Path(argv[0]).name == "node" and
                       Path(argv[1]).resolve() == ROOT / "scripts/macos-gateway.cjs")
        else:
            app_dir = Path(argv[argv.index("--app-dir") + 1]).resolve()
            matches = (Path(argv[0]).name.lower().startswith("python") and argv[1:3] == ["-m", "uvicorn"] and
                       "app.main:app" in argv and app_dir in {BACKEND, ROOT / "CAD_DB/backend"} and
                       argv[argv.index("--host") + 1] == "127.0.0.1" and
                       argv[argv.index("--port") + 1] == str(port))
        if matches and identity(pid) == before:
            return {"pid": pid, "identity": before, "service": service, "port": port}
    except (OSError, ValueError, IndexError):
        pass
    return None


def recover_records(records):
    records = records.copy()
    if not SETTINGS.exists():
        return records
    data = json.loads(SETTINGS.read_text())
    if not same_workspace(data.get("root")):
        return records
    for service, key in (("backend", "backend"), ("gateway", "web")):
        for pid in listener_pids(data["ports"][key]):
            record = owned_listener(pid, service, data["ports"][key])
            if record:
                existing = next((name for name, old in records.items() if old.get("pid") == pid), None)
                name = existing or (service if not alive(records.get(service, {})) else f"{service}-{pid}")
                records[name] = record
    return records


def stop_records(records):
    # Keep a durable recovery path if stopping is interrupted or a service refuses TERM.
    save(STATE, records)
    for name, record in records.items():
        if not alive(record):
            continue
        recovered_alias = (record.get("service") in {"backend", "gateway"} and
                           owned_listener(record["pid"], record["service"], record["port"]) is not None)
        if (str(ROOT) not in record["identity"] and not recovered_alias) or os.getpgid(record["pid"]) != record["pid"]:
            raise RuntimeError(f"Cannot verify ownership of {name}; refusing to stop it.")
        os.killpg(record["pid"], signal.SIGTERM)
        for _ in range(50):
            if not alive(record):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError(f"{name} did not stop; inspect it before restarting.")
    save(STATE, {})


def stop(args):
    records = json.loads(STATE.read_text()) if STATE.exists() else {}
    stop_records(recover_records(records))
    if args.database:
        data = config()
        if container_info(data):
            run("docker", "stop", data["container"], stdout=subprocess.DEVNULL)
    print("AI-DBCAD services stopped; saved models and database retained.")


def spawn(name, argv, data, records):
    log_path = LOCAL / f"{name}.log"
    with log_path.open("ab") as log:
        process = subprocess.Popen([str(arg) for arg in argv], cwd=LOCAL, env=environment(data),
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    records[name] = {"pid": process.pid, "identity": identity(process.pid)}
    save(STATE, records)
    time.sleep(0.2)
    if process.poll() is not None:
        raise RuntimeError(f"{name} exited. Inspect {log_path}")
    return process


def check_ports(data):
    for key in ("backend", "web"):
        port = data["ports"][key]
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError as exc:
                if exc.errno != errno.EADDRINUSE:
                    raise
                pids = ", ".join(map(str, listener_pids(port))) or "unknown"
                raise RuntimeError(f"{key} port {port} is already in use (PID {pids}). "
                                   "The listener could not be verified as this workspace's service and was not stopped.") from None


def wait_http(url, process, name, headers=None):
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError(f"{name} exited. Inspect {LOCAL / (name + '.log')}")
        try:
            request = urllib.request.Request(url, headers=headers or {})
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=2):
                return
        except (OSError, ValueError):
            time.sleep(0.5)
    raise RuntimeError(f"{name} did not become ready. Inspect {LOCAL / (name + '.log')}")


def start(args):
    if not PYTHON.exists() or not WORKER.exists() or not (DBCAD / "web/dist/index.html").exists():
        raise RuntimeError("Run python3 scripts/macos.py setup first.")
    for command in ("docker", "node"):
        if not shutil.which(command):
            raise RuntimeError(f"Missing prerequisite: {command}")
    run("docker", "info", "--format", "{{.ServerVersion}}", stdout=subprocess.DEVNULL)
    data = config()
    stop(argparse.Namespace(database=False))
    check_ports(data)
    database(data)
    records = {}
    try:
        backend = spawn("backend", [PYTHON, "-m", "uvicorn", "--app-dir", BACKEND, "app.main:app",
                                    "--host", "127.0.0.1", "--port", str(data["ports"]["backend"])], data, records)
        wait_http(f"http://127.0.0.1:{data['ports']['backend']}/api/v1/projects", backend, "backend",
                  {"X-API-Password": data["api_password"]})
        gateway = spawn("gateway", [shutil.which("node"), ROOT / "scripts/macos-gateway.cjs"], data, records)
        wait_http(f"http://127.0.0.1:{data['ports']['web']}/web/", gateway, "gateway")
    except BaseException:
        stop_records(records)
        raise
    url = f"http://127.0.0.1:{data['ports']['web']}/web/"
    print(f"AI-DBCAD: {url}")
    print(f"Local logs and saved configuration: {LOCAL}")
    if args.open:
        run("open", url)


def status(args):
    data = config()
    records = recover_records(json.loads(STATE.read_text()) if STATE.exists() else {})
    for name in ("backend", "gateway"):
        running = any(alive(record) for key, record in records.items() if record.get("service", key) == name)
        print(f"{name}: {'running' if running else 'stopped'}")
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
        if args.command in {"start", "stop"}:
            with lifecycle_lock():
                args.func(args)
        else:
            args.func(args)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"AI-DBCAD: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
