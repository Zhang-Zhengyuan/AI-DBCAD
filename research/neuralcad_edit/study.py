"""Local, offline-first entry points for the pinned neuralCAD-Edit release."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
LOCAL = HERE / ".local"
UPSTREAM = LOCAL / "upstream"
UPSTREAM_COMMIT = "68d1acceda0743e364f45fb7823f7cf39c530ba5"
WORKSPACE = LOCAL / "workspace"
CONFIG = LOCAL / "config.local.json"
DINO_REVISION = "f9e44c814b77203eaa57a6bdbbd535f21ede1415"
sys.path.insert(0, str(UPSTREAM))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def configure():
    actual = subprocess.check_output(["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"], text=True).strip()
    if actual != UPSTREAM_COMMIT:
        raise RuntimeError(f"Expected upstream {UPSTREAM_COMMIT}, got {actual}")
    candidates = list((LOCAL / "data").glob("*/mongita_db"))
    if (LOCAL / "data/mongita_db").exists():
        candidates.append(LOCAL / "data/mongita_db")
    if len(candidates) != 1:
        raise RuntimeError("Extract the verified official archive with data.py --extract first")
    source = candidates[0].parent
    if not WORKSPACE.exists():
        # APFS clones isolate future asset/DB writes without duplicating all blocks.
        cloned = subprocess.run(["cp", "-cR", str(source), str(WORKSPACE)], capture_output=True)
        if cloned.returncode:
            shutil.copytree(source, WORKSPACE, dirs_exist_ok=True)
    if not (WORKSPACE / "mongita_db").is_dir():
        raise RuntimeError("Working copy is incomplete; inspect it before configuring")
    # Upgrade only links produced during initial preparation; never reset a DB.
    for item in source.iterdir():
        target = WORKSPACE / item.name
        if target.is_symlink() and target.resolve() == item.resolve():
            target.unlink()
            cloned = subprocess.run(["cp", "-cR", str(item), str(target)], capture_output=True)
            if cloned.returncode:
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, target)
    config = json.loads((UPSTREAM / "src/config/edit_192_external.json").read_text())
    config["storage_dir"]["path"] = str(WORKSPACE)
    for model in config["rating_models"].values():
        model["skip"] = True  # Offline preparation never starts a paid rater.
    write_json(CONFIG, config)
    import imageio_ffmpeg
    binaries = LOCAL / "bin"
    binaries.mkdir(exist_ok=True)
    ffmpeg = binaries / "ffmpeg"
    if not ffmpeg.exists():
        ffmpeg.symlink_to(imageio_ffmpeg.get_ffmpeg_exe())
    print(f"Configured working database and local paths: {CONFIG}")


def open_database():
    from src.utils.db import DatabaseManager
    if not CONFIG.exists():
        raise RuntimeError("Run configure after downloading the data")
    config = json.loads(CONFIG.read_text())
    return DatabaseManager(config)


def doctor():
    packages = ["cadquery", "cadquery-ocp", "mongita", "open3d", "probreg", "torch", "torchvision",
                "transformers", "decord2", "openai", "anthropic", "google-genai", "jupyterlab"]
    versions = {name: importlib.metadata.version(name) for name in packages}
    import cadquery, open3d, probreg, decord, torch
    from src.vlms import openai, anthropic, gemini
    from src.utils import evals_feature_geometric, extract_features
    report = {"python": sys.version.split()[0], "architecture": platform.machine(), "packages": versions,
              "upstream": subprocess.check_output(["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"], text=True).strip(),
              "config_exists": CONFIG.exists(), "api_calls": 0}
    write_json(LOCAL / "doctor.json", report)
    print(json.dumps(report, indent=2))


def weights():
    from huggingface_hub import snapshot_download
    path = snapshot_download("facebook/dinov2-base", revision=DINO_REVISION,
                             allow_patterns=["config.json", "preprocessor_config.json", "model.safetensors"])
    write_json(LOCAL / "dino-revision.json", {"repo": "facebook/dinov2-base", "revision": DINO_REVISION})
    print(f"Pinned DINOv2 cache: {path}")


def catalog():
    db = open_database()
    try:
        requests = list(db.requests.find({"request_type": "edit"}))
        counts = {name: getattr(db, name).count_documents({}) for name in ["requests", "edits", "breps", "users", "ratings"]}
        report = {"counts": counts,
                  "modality": dict(Counter(row.get("modality") for row in requests)),
                  "difficulty": dict(Counter(row.get("difficulty") for row in requests)),
                  "request_ids": [row["_id"] for row in requests]}
        write_json(LOCAL / "catalog.json", report)
        print(json.dumps({k: v for k, v in report.items() if k != "request_ids"}, indent=2))
        return report
    finally:
        db.close_connection()


def select_request(db, request_id=None):
    for row in db.requests.find({"request_type": "edit"}):
        if request_id and row["_id"] != request_id:
            continue
        if not request_id and row.get("modality") != "text":
            continue
        brep = db.breps.find_one({"_id": row["brep_start"]})
        paths = brep.get("step", [])
        if paths:
            step = WORKSPACE / paths[0]
            if step.is_file():
                return row, step
    raise RuntimeError("No matching request with an available STEP input")


def smoke(request_id=None):
    import cadquery as cq
    import numpy as np
    from PIL import Image, ImageOps, ImageDraw
    import open3d as o3d
    from src.utils.cadquery_rendering import VIEW_PROJECTIONS, render_to_png
    from src.utils.evals_feature_geometric import compute_chamfer_distance, pair_cosine_similarity, iou

    output = LOCAL / "runs" / ("smoke-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True)
    db = open_database()
    try:
        request, source = select_request(db, request_id)
        shape = cq.importers.importStep(str(source))
        assert shape.val().isValid(), "Input STEP invalid"
        input_volume = sum(s.Volume() for s in shape.solids().vals())
        function = output / "passthrough.py"
        function.write_text(f"def my_cad_function(args):\n    model = cq.importers.importStep({str(source)!r})\n    return cq.Compound.makeCompound(model.vals())\n")
        command = [sys.executable, str(UPSTREAM / "src/harnesses/cadquery_script.py"),
                   "--output_dir", str(output), "--function_file", str(function), "--input_file", str(source)]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        (output / "harness.log").write_text(completed.stdout + "\n" + completed.stderr)
        assert completed.returncode == 0, "Official harness exited; inspect harness.log"
        step = output / "tmp.step"
        assert step.is_file(), "Official harness produced no STEP"
        restored = cq.importers.importStep(str(step))
        assert restored.val().isValid(), "Output STEP invalid"
        output_volume = sum(s.Volume() for s in restored.solids().vals())
        relative_volume_drift = abs(output_volume - input_volume) / max(abs(input_volume), 1e-12)
        # STEP translation/healing is not byte/geometry lossless. Verify scale and
        # topology separately, and keep the measured drift in the evidence.
        assert len(shape.solids().vals()) == len(restored.solids().vals()), "Solid count changed"
        before, after = shape.val().BoundingBox(), restored.val().BoundingBox()
        assert np.allclose([before.xlen, before.ylen, before.zlen],
                           [after.xlen, after.ylen, after.zlen], rtol=1e-4, atol=1e-5), "STEP scale changed"
        assert relative_volume_drift < 0.001, "STEP volume drift exceeds the 0.1% smoke tolerance"
        stl = output / "tmp.stl"
        cq.exporters.export(restored, str(stl))
        for name, projection in VIEW_PROJECTIONS.items():
            render_to_png(restored.val(), output / f"tmp_{name}.png", proj=projection, width=512, height=512)
        for name in VIEW_PROJECTIONS:
            image = Image.open(output / f"tmp_{name}.png").convert("RGB")
            assert image.size == (512, 512)
            assert (np.array(image) < 235).any(), f"Blank render: {name}"
        mesh = o3d.io.read_triangle_mesh(str(stl))
        o3d.utility.random.seed(42)
        points = np.asarray(mesh.sample_points_uniformly(1000).points)
        same_cd = compute_chamfer_distance(points, points)[0]
        shifted_cd = compute_chamfer_distance(points, points + np.array([10, 0, 0]))[0]
        assert same_cd == 0 and shifted_cd > 0
        proxy = type("Paths", (), {"root_dir": str(output)})()
        same_iou = iou("tmp.stl", "tmp.stl", proxy, timeout_seconds=60)
        assert np.isclose(same_iou, 1.0), f"Unexpected self IoU: {same_iou}"
        feature = db.breps.find_one({"_id": request["brep_start"]}).get("feature_dino")
        assert feature is not None, "No published DINO feature for input"
        same_dino = float(pair_cosine_similarity(feature, feature))
        assert np.isclose(same_dino, 1.0)
        video = next((WORKSPACE / value for key, value in request.items()
                      if isinstance(value, str) and value.endswith(".mp4") and (WORKSPACE / value).is_file()), None)
        if video is None:
            video = next((WORKSPACE / value for r in db.requests.find({}) for value in r.values()
                          if isinstance(value, str) and value.endswith(".mp4") and (WORKSPACE / value).is_file()), None)
        import decord
        assert video is not None
        reader = decord.VideoReader(str(video))
        frame_shape = reader[0].asnumpy().shape
        assert len(reader) > 0 and len(frame_shape) == 3
        contact = Image.new("RGB", (4 * 256, 2 * 280), "white")
        draw = ImageDraw.Draw(contact)
        for index, name in enumerate(VIEW_PROJECTIONS):
            thumb = ImageOps.contain(Image.open(output / f"tmp_{name}.png"), (256, 256))
            x, y = (index % 4) * 256, (index // 4) * 280
            contact.paste(thumb, (x, y + 24)); draw.text((x + 8, y + 5), name, fill="black")
        contact.save(output / "views.png")
        report = {"request_id": request["_id"], "input_step": str(source), "input_volume": input_volume,
                  "output_volume": output_volume, "views": list(VIEW_PROJECTIONS),
                  "relative_volume_drift": relative_volume_drift, "volume_tolerance": 0.001,
                  "chamfer_same_points": same_cd, "chamfer_shifted_points": shifted_cd,
                  "iou_same_mesh": same_iou, "dino_same_published_feature": same_dino,
                  "video_frames": len(reader), "video_frame_shape": list(frame_shape),
                  "api_calls": 0, "kind": "environment sanity, not model performance", "output": str(output)}
        write_json(output / "report.json", report)
        write_json(LOCAL / "smoke.json", report)
        print(json.dumps(report, indent=2))
    finally:
        db.close_connection()


def features():
    """Exercise the official DINO extractor on released and newly rendered images."""
    from functools import partial
    from unittest.mock import patch
    import numpy as np
    from transformers import pipeline
    from huggingface_hub import snapshot_download
    from src.utils.db import DatabaseManager
    from src.utils import extract_features
    from src.utils.evals_feature_geometric import pair_cosine_similarity
    db = open_database()
    output = LOCAL / "runs" / ("features-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True)
    probe = None
    try:
        request, _ = select_request(db)
        brep = db.breps.find_one({"_id": request["brep_start"]})
        reference_image = WORKSPACE / db.get_brep_images(request["brep_start"])[0]
        smoke_report = json.loads((LOCAL / "smoke.json").read_text())
        candidate_image = Path(smoke_report["output"]) / "tmp_toprightiso.png"
        revision = DINO_REVISION
        weights = snapshot_download("facebook/dinov2-base", revision=revision, local_files_only=True)
        config = json.loads(CONFIG.read_text())
        config["storage_dir"]["path"] = str(output)
        config["dino_model"] = weights
        probe = DatabaseManager(config)
        infos = []
        for key, image in [("released", reference_image), ("rendered", candidate_image)]:
            probe.breps.insert_one({"_id": key})
            infos.append({"brep_id": key, "frame_path": str(image)})
        # Upstream config says cpu, but extract_dino omits the pipeline device.
        # Enforce the configured CPU device without changing its feature logic.
        with patch.object(extract_features, "pipeline", partial(pipeline, device="cpu")):
            extract_features.extract_dino(probe, config, infos)
        released = probe.breps.find_one({"_id": "released"})["feature_dino"]
        rendered = probe.breps.find_one({"_id": "rendered"})["feature_dino"]
        assert len(released) == len(rendered) and np.isfinite(rendered).all()
        self_similarity = float(pair_cosine_similarity(rendered, rendered))
        assert np.isclose(self_similarity, 1)
        report = {"model": "facebook/dinov2-base", "revision": revision, "device": "cpu",
                  "feature_dimension": len(rendered), "same_image_similarity": self_similarity,
                  "recomputed_vs_published_feature": float(pair_cosine_similarity(released, brep["feature_dino"])),
                  "api_calls": 0, "output": str(output)}
        write_json(LOCAL / "features.json", report)
        print(json.dumps(report, indent=2))
    finally:
        if probe:
            probe.close_connection()
        db.close_connection()


def leaderboard():
    import numpy as np
    import pandas as pd
    db = open_database()
    try:
        notebook = json.loads((UPSTREAM / "src/notebooks/leaderboard.ipynb").read_text())
        source = next("".join(cell["source"]) for cell in notebook["cells"]
                      if "def process_single_rating" in "".join(cell.get("source", [])))
        namespace = {"np": np, "pd": pd, "dbm": db, "show": lambda *_: None}
        exec(compile(source, "upstream/leaderboard.ipynb:published-score-cell", "exec"), namespace)
        frame = namespace["df_pivot_scores"]
        write_json(LOCAL / "published-leaderboard.json", frame.to_dict(orient="index"))
        frame.to_csv(LOCAL / "published-leaderboard.csv")
        counts = namespace["df_filtered"].groupby("edit_user").size().to_dict()
        write_json(LOCAL / "published-leaderboard-counts.json", counts)
        print("Re-aggregated released outputs/ratings; no model inference or new human evaluation")
    finally:
        db.close_connection()


def infer(args):
    import pandas as pd
    db = open_database()
    try:
        request, _ = select_request(db, args.request)
        # Preserve the released baseline inputs, with a request-side allowlist.
        # Do not add target geometry or extra input views as an unreported change.
        released = pd.read_parquet(WORKSPACE / "parquets/val_edit_all.parquet")
        columns = ["request", "request_type", "file_name", "brep_start_path",
                   "request_video_30fps_720p_audio", "request_transcript", "request_events", "request_text", "views"]
        selected = released.loc[released["request"] == request["_id"], columns]
        if len(selected) != 1:
            raise RuntimeError("Expected exactly one matching row in the released task parquet")
        tasks = LOCAL / "tasks" / "val_edit_single.parquet"
        tasks.parent.mkdir(exist_ok=True)
        selected.to_parquet(tasks, index=False)
        config = json.loads(CONFIG.read_text())
        model = config["benchmark_models"][args.model]
        model.setdefault("function_kwargs", {})["max_iters"] = args.max_iters
        runtime_config = LOCAL / "config.inference.json"
        write_json(runtime_config, config)
        command = [sys.executable, str(UPSTREAM / "src/scripts_benchmark_inference/run_harness.py"),
                   "--config", str(runtime_config), "--input", str(tasks), "--userId", args.model,
                   "--harness", str(UPSTREAM / "src/harnesses/cadquery_script.py"),
                   "--output_dir", str(LOCAL / "model_outputs"), "--n-rows", "1"]
        key = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}[model["family"]]
        print(json.dumps({"request": request["_id"], "model": args.model, "max_iters": args.max_iters,
                          "credential_variable": key, "credential_present": bool(os.environ.get(key)),
                          "execute": args.execute, "command": command}, indent=2))
        if args.execute:
            if not os.environ.get(key):
                raise RuntimeError(f"Set {key} in the local environment first")
            subprocess.run(command, cwd=UPSTREAM, check=True)
    finally:
        db.close_connection()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("configure", "doctor", "catalog", "leaderboard", "features", "weights"):
        commands.add_parser(name)
    command = commands.add_parser("smoke"); command.add_argument("--request")
    command = commands.add_parser("infer")
    command.add_argument("--request", required=True)
    command.add_argument("--model", default="gpt-5.2_cadquery-script")
    command.add_argument("--max-iters", type=int, default=10)
    command.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.command == "infer":
        infer(args)
    elif args.command == "smoke":
        smoke(args.request)
    else:
        globals()[args.command]()


if __name__ == "__main__":
    main()
