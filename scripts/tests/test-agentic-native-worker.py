#!/usr/bin/env python3
"""Real ACIS feature/query and STEP round-trip regression (no network/LLM).

Usage: python3 scripts/tests/test-agentic-native-worker.py --worker <binary>
All models are synthetic and all outputs live in a temporary directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import queue
import subprocess
import tempfile
import threading
from pathlib import Path


class Worker:
    def __init__(self, binary: Path, spool: Path, components: list[dict] | None = None):
        self.stderr = tempfile.TemporaryFile(mode="w+t")
        self.process = subprocess.Popen(
            [str(binary), "serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.stderr, text=True, bufsize=1,
        )
        self.lines: queue.Queue = queue.Queue()
        self.sequence = 0
        self.spool = spool
        self.spool.mkdir(parents=True, exist_ok=True)

        def read_lines():
            for line in self.process.stdout:
                try:
                    value = json.loads(line)
                except ValueError:
                    continue  # Native diagnostic messages are not protocol replies.
                if isinstance(value, dict) and "id" in value:
                    self.lines.put(value)
            self.lines.put(None)

        threading.Thread(target=read_lines, daemon=True).start()
        self.request("open", spool=str(spool), components=components or [])

    def request(self, op: str, *, fails: bool = False, **kwargs):
        self.sequence += 1
        self.process.stdin.write(json.dumps(dict(id=self.sequence, op=op, **kwargs)) + "\n")
        self.process.stdin.flush()
        response = self.lines.get(timeout=45)
        assert response is not None, f"worker exited: {self.process.poll()}"
        assert response["id"] == self.sequence, response
        assert response["ok"] is not fails, response
        return response if fails else response.get("result", {})

    def command(self, feature: str, *, fails: bool = False, **params):
        return self.request("exec", fails=fails, command=dict(action="component." + feature, params=params))

    def create(self, feature: str, **params) -> str:
        added = self.command(feature, **params)["added"]
        assert len(added) == 1
        item = added[0]
        assert item["triangles"] > 0 and item["faces_skipped"] == 0, item
        for field in ("sat", "glb", "graph"):
            assert (self.spool / item[field]).stat().st_size > 0
        return item["sid"]

    def query(self, kind: str, **params):
        return self.request("query", query=dict(kind=kind, **params))

    def summary(self, sid: str):
        value = self.query("inspect", component=sid)["components"][0]
        assert value["valid"] and value["solids"] == 1, value
        return value

    def close(self):
        try:
            self.request("close")
            assert self.process.wait(timeout=10) == 0
        finally:
            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=10)
            self.stderr.close()


def near(actual: float, expected: float, rel: float = 1e-6):
    assert math.isclose(actual, expected, rel_tol=rel, abs_tol=1e-7), (actual, expected)


def run(binary: Path, directory: Path):
    worker = Worker(binary, directory / "session")
    try:
        cube = worker.create("create", kind="cube", width=10, height=10, depth=10)
        cube_sat = worker.spool / f"{cube}.sat"
        original_sat = hashlib.sha256(cube_sat.read_bytes()).hexdigest()
        summary = worker.summary(cube)
        near(summary["volume"], 1000)
        near(summary["area"], 600)
        assert summary["faces"] == 6 and summary["edges"] == 12
        faces = worker.query("faces", component=cube)["items"]
        edges = worker.query("edges", component=cube)["items"]
        assert len(faces) == 6 and len(edges) == 12
        for face in faces:
            near(face["area"], 100)
            near(sum(n * n for n in face["normal"]), 1)
            assert face["center_kind"] == "planar_area_centroid"
        for edge in edges:
            near(edge["length"], 10)
        paged = worker.query("faces", component=cube, limit=2, offset=3)
        assert [item["index"] for item in paged["items"]] == [3, 4]
        selection = worker.query("render_selection", component=cube, faces=[0], edges=[0])["selections"]
        assert len(selection) == 2
        face_selection, edge_selection = selection
        assert len(face_selection["indices"]) == 6 and len(face_selection["positions"]) == 12
        face = faces[0]
        for i in range(0, len(face_selection["positions"]), 3):
            point = face_selection["positions"][i:i + 3]
            near(sum((point[j] - face["center"][j]) * face["normal"][j] for j in range(3)), 0)
        assert len(edge_selection["edge_positions"]) == 6
        selected_line = edge_selection["edge_positions"]
        near(math.dist(selected_line[:3], selected_line[3:]), edges[0]["length"])
        assert hashlib.sha256(cube_sat.read_bytes()).hexdigest() == original_sat

        # Both rejected input and a genuine native feature failure preserve state.
        worker.command("fillet", fails=True, component=cube, edges=[999], radius=1)
        worker.command("chamfer", fails=True, component=cube, edges=[0], distance=100)
        worker.command("fillet", fails=True, component=cube, edges=list(range(12)), radius=100)
        worker.request("query", fails=True, query=dict(kind="faces", component="missing"))
        worker.request("query", fails=True, query=dict(kind="edges", component=cube, offset=-1))
        near(worker.summary(cube)["volume"], 1000)
        assert hashlib.sha256(cube_sat.read_bytes()).hexdigest() == original_sat
        worker.command("fillet", component=cube, edges=[0], radius=1)
        rounded = worker.summary(cube)
        near(rounded["volume"], 1000 - (1 - math.pi / 4) * 10)
        assert rounded["faces"] == 7

        bevel = worker.create("create", kind="cube", width=10, height=10, depth=10)
        worker.command("chamfer", component=bevel, edges=[0], distance=1)
        near(worker.summary(bevel)["volume"], 995)
        hollow = worker.create("create", kind="cube", width=10, height=10, depth=10)
        worker.command("shell", component=hollow, faces=[0], thickness=-1)
        near(worker.summary(hollow)["volume"], 424)

        other = worker.create("create", kind="cube", width=10, height=10, depth=10, position=[12, 0, 0])
        near(worker.query("distance", a=bevel, b=other)["distance"], 2)
        assert not worker.query("interference", a=bevel, b=other)["interferes"]
        worker.command("translate", component=other, offset=[-5, 0, 0])
        intersection = worker.query("interference", a=bevel, b=other)
        assert intersection["interferes"]
        near(intersection["intersection_volume"], 295)

        rectangle = worker.create("extrude", profile=dict(kind="rectangle", width=10, height=6), distance=5)
        near(worker.summary(rectangle)["volume"], 300)
        for plane, expected_size in (("XY", [4, 4, 5]), ("XZ", [4, 5, 4]), ("YZ", [5, 4, 4])):
            sid = worker.create("extrude", profile=dict(kind="circle", radius=2),
                                plane=plane, origin=[20, 0, 0], distance=5)
            cylinder = worker.summary(sid)
            near(cylinder["volume"], 20 * math.pi)
            for actual, expected in zip(cylinder["size"], expected_size):
                near(actual, expected)
            assert cylinder["volume_relative_accuracy"] <= 1e-6
            circles = [edge for edge in worker.query("edges", component=sid)["items"] if edge["type"] == "circle"]
            assert len(circles) == 2
            for edge in circles:
                near(edge["radius"], 2)
                near(edge["length"], 4 * math.pi)
        polygon = worker.create("extrude", profile=dict(kind="polygon", points=[[0, 0], [5, 0], [0, 4]]), distance=5)
        near(worker.summary(polygon)["volume"], 50)
        concave = worker.create("extrude", profile=dict(kind="polygon", points=[[0, 0], [4, 0], [4, 2], [2, 2], [2, 4], [0, 4]]), distance=3)
        near(worker.summary(concave)["volume"], 36)
        for angle in (90, 360):
            ring = worker.create("revolve", profile=dict(kind="rectangle", width=2, height=4),
                                 plane="XZ", origin=[5, 0, 0], axis=[0, 0, 1],
                                 axis_origin=[0, 0, 0], angle_degrees=angle)
            near(worker.summary(ring)["volume"], 80 * math.pi * angle / 360)
        straight = worker.create("sweep", radius=1, path=dict(kind="polyline", points=[[0, 0, 0], [0, 0, 10]]))
        near(worker.summary(straight)["volume"], 10 * math.pi)
        bent = worker.create("sweep", radius=1, path=dict(kind="polyline", points=[[0, 0, 0], [0, 0, 10], [10, 0, 10]]))
        near(worker.summary(bent)["volume"], 20 * math.pi)
        for path in (dict(kind="arc", points=[[10, 0, 0], [10 / math.sqrt(2), 10 / math.sqrt(2), 0], [0, 10, 0]]),
                     dict(kind="arc", center=[0, 0, 0], normal=[0, 0, 1], radius=10, start_angle=0, sweep_angle=90),
                     dict(kind="arc", center=[0, 0, 0], normal=[0, 0, 1], radius=10, start_angle=0, sweep_angle=-90)):
            arc = worker.create("sweep", radius=1, path=path)
            near(worker.summary(arc)["volume"], 5 * math.pi ** 2)
        before_failed_create = worker.query("inspect")
        worker.command("extrude", fails=True, profile=dict(kind="circle", radius=2), distance=0)
        worker.command("revolve", fails=True, profile=dict(kind="rectangle", width=2, height=4), axis=[0, 0, 0])
        worker.command("sweep", fails=True, radius=1, path=dict(kind="polyline", points=[[0, 0, 0], [0, 0, 0]]))
        assert worker.query("inspect") == before_failed_create
        assert worker.query("validate")["valid"]
        expected_step_volume = rounded["volume"] + 300
    finally:
        worker.close()

    # Actual InterOp translation and independent STEP -> SAT -> worker readback.
    step_file = directory / "assembly.step"
    sat_files = [worker.spool / f"{sid}.sat" for sid in (cube, rectangle)]
    exported = subprocess.run([str(binary), "export", "--sat-list", ",".join(map(str, sat_files)),
                               "--step-out", str(step_file)], capture_output=True, text=True, timeout=60)
    assert exported.returncode == 0, exported.stderr
    assert step_file.read_bytes().startswith(b"ISO-10303-21;")
    imported = directory / "readback"
    restored = subprocess.run([str(binary), "import", "--sat", str(step_file), "--outdir", str(imported)],
                              capture_output=True, text=True, timeout=60)
    assert restored.returncode == 0, restored.stderr
    manifest = json.loads((imported / "manifest.json").read_text())
    assert len(manifest["components"]) == 2
    components = [dict(sid=item["id"], file=item["sat"]) for item in manifest["components"]]
    readback = Worker(binary, imported, components)
    try:
        scene = readback.query("validate")
        assert scene["valid"] and scene["solids"] == 2
        near(scene["volume"], expected_step_volume)
    finally:
        readback.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="dbcad-agent-native-") as temporary:
        run(args.worker.resolve(), Path(temporary))
    print("PASS: native queries, real selections, feature failures, five features + shell, STEP round-trip")
