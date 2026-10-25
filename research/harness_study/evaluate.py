"""Offline metrics, blinded provider grading and reproducible comparison plots."""

# ruff: noqa: E402 -- bootstrap paths for pinned, separately cloned research code
from __future__ import annotations
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import random
import subprocess
import sys
import time
from types import SimpleNamespace
import numpy as np

HERE = Path(__file__).resolve().parent
AGENTIC = HERE.parent / "agenticcad_edit"
NEURAL = HERE.parent / "neuralcad_edit"
sys.path[:0] = [str(AGENTIC), str(AGENTIC / ".local/upstream")]
from provider import Budget, Transport, load_credentials
from study import tasks, write_json
from experiment import current, ARMS

spec = importlib.util.spec_from_file_location("pilot_eval", AGENTIC / "evaluate.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


def chamfer(a, b):
    from scipy.spatial import cKDTree

    return float(cKDTree(b).query(a)[0].mean() + cKDTree(a).query(b)[0].mean())


def render(source, folder):
    if (folder / "render.done").exists():
        return json.loads((folder / "geometry.json").read_text())
    if (folder / "geometry-error.json").exists():
        return json.loads((folder / "geometry-error.json").read_text())
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with (folder / "render.log").open("w") as log:
            result = subprocess.run(
                [sys.executable, str(HERE / "geometry.py"), str(source), str(folder)],
                stdout=log,
                stderr=log,
                timeout=150,
            )
        if result.returncode or not (folder / "render.done").exists():
            raise RuntimeError("STEP validation/render failed")
        return json.loads((folder / "geometry.json").read_text())
    except Exception as e:
        value = {"strict_solid": False, "geometry_error": type(e).__name__}
        write_json(folder / "geometry-error.json", value)
        return value


def same_geometry(a, b):
    if not a.get("face_signatures") or not b.get("face_signatures"):
        return False
    if (a["faces"], a["edges"], a["solids"]) != (b["faces"], b["edges"], b["solids"]):
        return False
    tol = max(np.linalg.norm(a["bbox_size"]) * 1e-6, 1e-7)
    # Signatures are a conservative detector, not a proof of solid equality.
    for x, y in zip(a["face_signatures"], b["face_signatures"]):
        if x[0] != y[0] or not np.allclose(x[1:], y[1:], rtol=1e-6, atol=tol):
            return False
    return True


def make_sheet(folder, task):
    from PIL import Image, ImageDraw, ImageOps, ImageFont
    import matplotlib

    font = ImageFont.truetype(
        str(Path(matplotlib.get_data_path()) / "fonts/ttf/DejaVuSans.ttf"), 16
    )
    sheet = Image.new("RGB", (1800, 810), "white")
    draw = ImageDraw.Draw(sheet)
    draw.text((10, 8), task["request"], font=font, fill="black")
    for index, arm in enumerate(["input", *ARMS, "reference"]):
        draw.text((index * 360 + 10, 35), arm.upper(), font=font, fill="black")
        for row, view in enumerate(["toprightiso", "front"]):
            path = folder / "evaluation" / arm / (view + ".png")
            y = 65 + row * 365
            if path.exists():
                sheet.paste(
                    ImageOps.contain(Image.open(path).convert("RGB"), (360, 360)),
                    (index * 360, y),
                )
            else:
                draw.text(
                    (index * 360 + 25, y + 160), "NO OUTPUT", font=font, fill="black"
                )
    sheet.save(folder / "comparison.png")


def metrics(run, watch=False):
    import open3d as o3d
    from src.utils.evals_feature_geometric import (
        load_stl_as_point_cloud,
        _iou_impl,
        compute_chamfer_distance,
    )
    from transformers import pipeline
    from huggingface_hub import snapshot_download
    import torch

    torch.set_num_threads(2)
    # Mathematical equivalence to the published dense cdist implementation.
    rng = np.random.default_rng(42)
    a = rng.normal(size=(71, 3))
    b = rng.normal(size=(83, 3))
    assert np.isclose(chamfer(a, b), compute_chamfer_distance(a, b)[0], rtol=1e-12)
    weights = snapshot_download(
        "facebook/dinov2-base",
        revision="f9e44c814b77203eaa57a6bdbbd535f21ede1415",
        local_files_only=True,
    )
    extractor = pipeline(task="image-feature-extraction", model=weights, device="cpu")
    plan = json.loads((run / "plan.json").read_text())
    taskmap = {t["request"]: t for t in tasks([r["_id"] for r in plan["tasks"]])}

    def feature(folder):
        path = folder / "dino.npy"
        if not path.exists():
            np.save(
                path,
                extractor(str(folder / "toprightiso.png"), return_tensors=True)
                .squeeze()
                .numpy()
                .flatten(),
            )
        return np.load(path)

    def points(path):
        o3d.utility.random.seed(42)
        return load_stl_as_point_cloud(str(path), num_samples=10000)

    def cosine(a, b):
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

    while True:
        completed = 0
        for row in plan["tasks"]:
            rid = row["_id"]
            task = taskmap[rid]
            folder = run / rid
            if not any((folder / arm / "result.json").exists() for arm in ARMS):
                continue
            if all(
                (folder / "evaluation" / arm / "metrics.json").exists() for arm in ARMS
            ):
                completed += 3
                continue
            reference = pilot.reference_paths(rid)
            sources = {
                "input": Path(task["brep_start_path_step"]),
                "reference": reference["step"],
            }
            geoms = {}
            for arm, source in sources.items():
                dest = folder / "evaluation" / arm
                geoms[arm] = render(source, dest)
            target = points(reference["stl"])
            target_feature = feature(folder / "evaluation/reference")
            scale = float(np.linalg.norm(geoms["reference"]["bbox_size"])) or 1
            for arm in ["input", *ARMS]:
                output = folder / "evaluation" / arm
                metricfile = output / "metrics.json"
                if arm in ARMS and not (folder / arm / "result.json").exists():
                    continue
                if metricfile.exists():
                    if arm in ARMS:
                        completed += 1
                    continue
                record = (
                    json.loads((folder / arm / "result.json").read_text())
                    if arm in ARMS
                    else {"status": "input"}
                )
                source = (
                    sources["input"] if arm == "input" else folder / arm / "tmp.step"
                )
                output.mkdir(parents=True, exist_ok=True)
                value = {
                    "request": rid,
                    "arm": arm,
                    "run_status": record["status"],
                    "step_exists": source.exists(),
                }
                if source.exists():
                    geometry = (
                        geoms["input"] if arm == "input" else render(source, output)
                    )
                    value.update(
                        {k: v for k, v in geometry.items() if k != "face_signatures"}
                    )
                    if not geometry.get("geometry_error"):
                        pointcloud = points(output / "mesh.stl")
                        value.update(
                            cd=chamfer(pointcloud, target),
                            surface_iou=float(
                                _iou_impl(
                                    str(output / "mesh.stl"),
                                    str(reference["stl"]),
                                    SimpleNamespace(root_dir=""),
                                )
                            ),
                            dino=cosine(feature(output), target_feature),
                            suspected_no_op=same_geometry(geoms["input"], geometry),
                        )
                        value["normalized_cd"] = value["cd"] / scale
                else:
                    value["strict_solid"] = False
                write_json(metricfile, value)
                print(
                    json.dumps(
                        {k: v for k, v in value.items() if k not in ("solid_volumes",)}
                    ),
                    flush=True,
                )
                if arm in ARMS:
                    completed += 1
            make_sheet(folder, task)
        print("METRIC_EPISODES", completed, flush=True)
        if not watch or completed == len(plan["tasks"]) * 3:
            return
        time.sleep(15)


def judge_plan(run):
    config = json.loads(
        (NEURAL / ".local/upstream/src/config/edit_192_external.json").read_text()
    )
    rubrics = []

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if (
                    k == "prompt"
                    and isinstance(v, str)
                    and "score-instruction-understanding" in v
                ):
                    rubrics.append(v)
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(config)
    # Reuse the published licensed code prompt, adding an explicit blinded contract.
    prompt = (
        rubrics[0]
        + "\nThe candidate method is hidden. Do not infer its identity. Judge only visible request compliance. "
    )
    prompt += "Images marked INPUT are the original model, CANDIDATE is the proposed edit. No reference solution is supplied. "
    prompt += "Additional kernel measurements are factual. Unchanged candidates are instruction score 2. Do not reward similarity alone. "
    prompt += "Return only the two integer score fields named above. They must be integers from 1 to 7."
    plan = {
        "created": time.time(),
        "model": "Qwen3.6-27B",
        "prompt": prompt,
        "threshold": 5,
        "strict_solid_gate": True,
        "seed": 20261026,
        "reference_exposed": False,
        "generator_names_exposed": False,
        "rubric_source": "neuralCAD-Edit src/config/edit_192_external.json published prompt",
        "proxy_only": "Single same-family VLM rater; not human Acceptance; selection and visual bias remain.",
    }
    path = run / "judge-plan.json"
    if path.exists():
        return
    write_json(path, plan)


def judge(run, watch=False):
    judge_plan(run)
    plan = json.loads((run / "judge-plan.json").read_text())
    exp = json.loads((run / "plan.json").read_text())
    from src.vlms.vllm_openai import VLM

    settings = json.loads(
        (AGENTIC / ".local/upstream/src/config/edit_192_external.json").read_text()
    )["benchmark_models"]["qwen3.6-27b_cadquery-script"]
    helper = VLM.__new__(VLM)
    from src.vlms.base_vlm import BaseVLM

    BaseVLM.__init__(helper, settings, cache=False)
    helper.config = settings
    # Base loader parameters are required for video sampling.
    helper.fps = 0.5
    taskmap = {t["request"]: t for t in tasks([r["_id"] for r in exp["tasks"]])}
    pricing = json.loads((HERE / ".local/pricing.json").read_text())
    budget = Budget(HERE / ".local/budget.json", 100, pricing)

    def encode(path):
        return base64.b64encode(Path(path).read_bytes()).decode()

    def rate(item):
        rid, arm = item
        folder = run / rid
        out = folder / arm / "judge"
        dest = out / "rating.json"
        if dest.exists():
            return
        metric = folder / "evaluation" / arm / "metrics.json"
        if not metric.exists():
            return
        m = json.loads(metric.read_text())
        out.mkdir(parents=True, exist_ok=True)
        if not m.get("step_exists") or m.get("geometry_error"):
            write_json(
                dest,
                {
                    "instruction": 1,
                    "quality": 1,
                    "accepted_proxy": False,
                    "rated": False,
                    "reason": "no readable STEP",
                },
            )
            return
        if any((out / "api").glob("*-request.json")):
            # Never silently spend on a second rating of a failed paid attempt.
            return
        task = taskmap[rid]

        helper.fps = 0.5
        common = helper.create_messages(helper.load_task_info_dict(task), sys=None)[0][
            "content"
        ]
        content = [{"type": "text", "text": plan["prompt"]}] + common
        for name in ["input", arm]:
            label = "INPUT" if name == "input" else "CANDIDATE"
            content.append(
                {
                    "type": "text",
                    "text": label + " views: front, back, left, right, top, bottom",
                }
            )
            for view in ["front", "back", "left", "right", "top", "bottom"]:
                path = folder / "evaluation" / name / (view + ".png")
                if path.exists():
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64," + encode(path)
                            },
                        }
                    )
        evidence = {
            k: m.get(k)
            for k in [
                "brep_valid",
                "solids",
                "uncontained_faces",
                "volume",
                "bbox_size",
                "suspected_no_op",
            ]
        }
        content.append(
            {
                "type": "text",
                "text": "Candidate kernel measurements: " + json.dumps(evidence),
            }
        )
        transport = Transport(
            load_credentials(AGENTIC / ".local/provider.env"),
            budget,
            out / "api",
            "judge/" + rid + "/" + arm,
        )
        try:
            response = transport.create(
                model=plan["model"],
                messages=[{"role": "user", "content": content}],
                max_tokens=1024,
                temperature=0,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            text = response.choices[0].message.content or ""
            text = text.strip()
            if text.startswith("```"):
                text = "\n".join(text.splitlines()[1:-1])
            data = json.loads(text)
            instruction = data["score-instruction-understanding"]
            quality = data["score-quality"]
            if (
                type(instruction) is not int
                or type(quality) is not int
                or not 1 <= instruction <= 7
                or not 1 <= quality <= 7
            ):
                raise ValueError("Out-of-rubric score")
            value = {
                "instruction": instruction,
                "quality": quality,
                "strict_solid": m.get("strict_solid", False),
                "accepted_proxy": instruction >= 5
                and quality >= 5
                and m.get("strict_solid", False),
                "rated": True,
            }
            write_json(dest, value)
            print(rid, arm, value, flush=True)
        except Exception as error:
            write_json(out / "judge-error.json", {"error": type(error).__name__})
            print("JUDGE_ERROR", rid, arm, type(error).__name__, flush=True)

    jobs = [(r["_id"], a) for r in exp["tasks"] for a in ARMS]
    random.Random(plan["seed"]).shuffle(jobs)
    while True:
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(rate, jobs))
        done = sum((run / r / a / "judge/rating.json").exists() for r, a in jobs)
        print("JUDGED_EPISODES", done, flush=True)
        if not watch or done == len(jobs):
            return
        time.sleep(20)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["metrics", "judge", "judge-plan"])
    p.add_argument("--run", type=Path)
    p.add_argument("--watch", action="store_true")
    args = p.parse_args()
    run = args.run or current()
    if args.command == "judge-plan":
        judge_plan(run)
    elif args.command == "metrics":
        metrics(run, args.watch)
    else:
        judge(run, args.watch)


if __name__ == "__main__":
    main()
