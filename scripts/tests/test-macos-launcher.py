"""Lifecycle regression checks without Docker or access to development data."""
import argparse
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("macos_launcher", Path(__file__).parents[1] / "macos.py")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


@unittest.skipUnless(sys.platform == "darwin", "macOS process identity and sockets")
class LauncherTests(unittest.TestCase):
    @contextmanager
    def workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            local = root / ".local/macos"
            backend = root / "systems/dbcad/CAD_DB/backend"
            backend.mkdir(parents=True)
            with patch.multiple(launcher, ROOT=root, LOCAL=local, SETTINGS=local / "settings.json",
                                STATE=local / "processes.json", BACKEND=backend):
                yield root, local, launcher.config()

    @contextmanager
    def listening_service(self, root, local, port, service="backend", legacy=False, foreign=False, app_dir=None):
        # Real isolated process/socket; module name and argv match the launcher.
        # No Docker, development models or actual backend imports are involved.
        server = "import socket,time\ns=socket.socket();s.bind(('127.0.0.1',PORT));s.listen();time.sleep(120)\n"
        if service == "gateway":
            script = root / "scripts/macos-gateway.cjs"
            script.parent.mkdir(exist_ok=True)
            script.write_text(f"require('node:net').createServer().listen({port}, '127.0.0.1');\n")
            argv = [shutil.which("node"), str(script)]
        elif foreign:
            script = root / "other-service.py"
            script.write_text(server.replace("PORT", str(port)))
            argv = [sys.executable, str(script)]
        else:
            (local / "uvicorn.py").write_text(server.replace("PORT", str(port)))
            app_dir = app_dir or (root / "CAD_DB/backend" if legacy else launcher.BACKEND)
            argv = [sys.executable, "-m", "uvicorn", "--app-dir", str(app_dir),
                    "app.main:app", "--host", "127.0.0.1", "--port", str(port)]
        process = subprocess.Popen(argv, cwd=local, start_new_session=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                if process.pid in launcher.listener_pids(port):
                    break
                if process.poll() is not None:
                    self.fail("Test listener exited before readiness")
                time.sleep(.05)
            else:
                self.fail("Test listener failed to bind")
            yield process
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)

    def test_missing_and_stale_records_recover_owned_current_and_legacy_backend(self):
        for legacy in (False, True):
            for stale in (False, True):
                with self.subTest(legacy=legacy, stale=stale), self.workspace() as (root, local, data):
                    with self.listening_service(root, local, data["ports"]["backend"], legacy=legacy) as process:
                        records = {"backend": {"pid": process.pid, "identity": "obsolete"}} if stale else {}
                        launcher.save(launcher.STATE, records)
                        recovered = launcher.recover_records(records)
                        self.assertEqual(recovered["backend"]["pid"], process.pid)
                        self.assertTrue(launcher.alive(recovered["backend"]))
                        launcher.stop(argparse.Namespace(database=False))
                        process.wait(timeout=5)
                        self.assertEqual(launcher.listener_pids(data["ports"]["backend"]), [])
                        self.assertEqual(json.loads(launcher.STATE.read_text()), {})

    def test_owned_backend_command_through_old_workspace_alias_is_recovered(self):
        with self.workspace() as (root, local, data):
            alias = root.parent / (root.name + "-alias")
            alias.symlink_to(root, target_is_directory=True)
            try:
                with self.listening_service(root, local, data["ports"]["backend"], app_dir=alias / "CAD_DB/backend") as process:
                    launcher.stop(argparse.Namespace(database=False))
                    process.wait(timeout=5)
            finally:
                alias.unlink()

    @unittest.skipUnless(shutil.which("node"), "Node gateway")
    def test_missing_record_recovers_owned_gateway(self):
        with self.workspace() as (root, local, data):
            with self.listening_service(root, local, data["ports"]["web"], service="gateway") as process:
                launcher.stop(argparse.Namespace(database=False))
                process.wait(timeout=5)
                self.assertEqual(launcher.listener_pids(data["ports"]["web"]), [])

    def test_foreign_listener_in_same_directory_survives_and_gets_actionable_error(self):
        with self.workspace() as (root, local, data):
            with self.listening_service(root, local, data["ports"]["backend"], foreign=True) as process:
                launcher.stop(argparse.Namespace(database=False))
                self.assertIsNone(process.poll())
                with self.assertRaisesRegex(RuntimeError, f"port {data['ports']['backend']}.*PID {process.pid}"):
                    launcher.check_ports(data)
                self.assertIsNone(process.poll())

    def test_matching_command_in_foreign_working_directory_is_not_adopted(self):
        with self.workspace() as (root, local, data):
            foreign = root / "foreign"
            foreign.mkdir()
            with self.listening_service(root, foreign, data["ports"]["backend"]) as process:
                launcher.stop(argparse.Namespace(database=False))
                self.assertIsNone(process.poll())

    def test_lifecycle_lock_rejects_overlap_and_releases_after_error(self):
        with self.workspace():
            with self.assertRaisesRegex(RuntimeError, "deliberate failure"):
                with launcher.lifecycle_lock():
                    with self.assertRaisesRegex(RuntimeError, "Another AI-DBCAD start/stop"):
                        with launcher.lifecycle_lock():
                            self.fail("Overlapping lifecycle operation acquired the lock")
                    raise RuntimeError("deliberate failure")
            with launcher.lifecycle_lock():
                pass

    def test_failed_gateway_start_cleans_only_the_backend_started_in_this_attempt(self):
        with self.workspace() as (root, local, data):
            web = root / "systems/dbcad/web/dist"
            web.mkdir(parents=True)
            (web / "index.html").touch()
            worker = root / "worker"
            worker.touch()
            script = root / "owned-backend.py"
            script.write_text("import time;time.sleep(120)\n")
            process = subprocess.Popen([sys.executable, str(script)], cwd=local, start_new_session=True)
            def fake_spawn(name, argv, settings, records):
                if name == "gateway":
                    raise RuntimeError("gateway failed")
                records[name] = {"pid": process.pid, "identity": launcher.identity(process.pid)}
                return process
            try:
                with patch.multiple(launcher, DBCAD=root / "systems/dbcad", WORKER=worker, PYTHON=Path(sys.executable)), \
                     patch.object(launcher, "run"), patch.object(launcher, "database"), \
                     patch.object(launcher, "wait_http"), patch.object(launcher, "spawn", side_effect=fake_spawn), \
                     patch.object(launcher.shutil, "which", return_value="/fake/node"):
                    with self.assertRaisesRegex(RuntimeError, "gateway failed"):
                        launcher.start(argparse.Namespace(open=False))
                process.wait(timeout=5)
                self.assertEqual(json.loads(launcher.STATE.read_text()), {})
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)

    def test_listening_port_is_not_selected(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            preferred = listener.getsockname()[1]
            self.assertNotEqual(launcher.free_port(preferred), preferred)

    def test_released_connection_does_not_block_restart(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            with socket.create_connection(("127.0.0.1", port)) as client:
                connection, _ = listener.accept()
                connection.close()  # server side enters TIME_WAIT
                client.recv(1)
        self.assertEqual(launcher.free_port(port), port)

    def test_config_is_private_and_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / ".local/macos"
            with patch.multiple(launcher, ROOT=root, LOCAL=local, SETTINGS=local / "settings.json"):
                first = launcher.config()
                self.assertEqual(first, launcher.config())
                self.assertGreater(len(first["api_password"]), 32)
                self.assertNotEqual(first["api_password"], first["neo4j_password"])
                self.assertEqual((local / "settings.json").stat().st_mode & 0o777, 0o600)

    def test_renamed_workspace_reuses_config_through_original_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "old-workspace"
            original.mkdir()
            local = original / ".local/macos"
            with patch.multiple(launcher, ROOT=original, LOCAL=local, SETTINGS=local / "settings.json"):
                before = launcher.config()
            renamed = Path(directory) / "new-workspace"
            original.rename(renamed)
            original.symlink_to(renamed.name, target_is_directory=True)
            local = renamed / ".local/macos"
            with patch.multiple(launcher, ROOT=renamed, LOCAL=local, SETTINGS=local / "settings.json"):
                self.assertEqual(before, launcher.config())
                self.assertEqual((local / "settings.json").stat().st_mode & 0o777, 0o600)

    def test_copied_config_cannot_adopt_another_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original"
            copied = Path(directory) / "copied"
            original.mkdir()
            copied.mkdir()
            local = original / ".local/macos"
            with patch.multiple(launcher, ROOT=original, LOCAL=local, SETTINGS=local / "settings.json"):
                before = launcher.config()
            local = copied / ".local/macos"
            launcher.save(local / "settings.json", before)
            with patch.multiple(launcher, ROOT=copied, LOCAL=local, SETTINGS=local / "settings.json"):
                with self.assertRaisesRegex(RuntimeError, "Workspace moved"):
                    launcher.config()

    def test_container_alias_is_accepted_but_foreign_or_missing_label_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "new-workspace"
            alias = Path(directory) / "old-workspace"
            foreign = Path(directory) / "other-workspace"
            root.mkdir()
            foreign.mkdir()
            alias.symlink_to(root.name, target_is_directory=True)
            for label in (str(root), str(alias), str(foreign), "", None):
                with self.subTest(label=label), patch.object(launcher, "ROOT", root):
                    labels = {} if label is None else {launcher.LABEL: label}
                    info = {"Config": {"Labels": labels}, "State": {"Running": True}}
                    result = subprocess.CompletedProcess([], 0, json.dumps([info]), "")
                    with patch.object(launcher.subprocess, "run", return_value=result):
                        if label in (str(root), str(alias)):
                            self.assertEqual(launcher.container_info({"container": "test-container"}), info)
                        else:
                            with self.assertRaisesRegex(RuntimeError, "another workspace"):
                                launcher.container_info({"container": "test-container"})

    def test_stale_identity_preserves_process_and_owned_identity_stops_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "owned-process.py"
            script.write_text("import time; time.sleep(120)\n")
            process = subprocess.Popen([sys.executable, str(script)], start_new_session=True)
            try:
                time.sleep(0.1)
                record = {"pid": process.pid, "identity": launcher.identity(process.pid)}
                with patch.multiple(launcher, ROOT=root, STATE=root / "state.json"):
                    launcher.save(launcher.STATE, {"backend": {**record, "identity": record["identity"] + " stale"}})
                    launcher.stop(argparse.Namespace(database=False))
                    self.assertIsNone(process.poll())
                    launcher.save(launcher.STATE, {"backend": record})
                    launcher.stop(argparse.Namespace(database=False))
                    self.assertIsNotNone(process.poll())
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
