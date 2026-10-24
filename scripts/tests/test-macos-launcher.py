"""Lifecycle regression checks without Docker or access to development data."""
import argparse
import importlib.util
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
