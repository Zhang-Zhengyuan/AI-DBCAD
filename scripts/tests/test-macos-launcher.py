"""Lifecycle regression checks without Docker or access to development data."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
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
