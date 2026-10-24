"""Controlled feedback-channel probe, separate from editing benchmark scores.

Tests whether auto-fit rendering hides scale/translation changes. This is a
verification diagnostic, not an end-to-end editing improvement claim.
"""

# ruff: noqa: E402 -- bootstrap paths for pinned, separately cloned research code
from __future__ import annotations
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import inspect
import json
from pathlib import Path
import random
import sys
import time

HERE = Path(__file__).resolve().parent
AGENTIC = HERE.parent / "agenticcad_edit"
sys.path[:0] = [str(AGENTIC), str(AGENTIC / ".local/upstream")]
from provider import Budget, Transport, load_credentials
from study import write_json


def register(folder):
    if (folder / "plan.json").exists():
        return
    folder.mkdir(parents=True, exist_ok=True)
    write_json(
        folder / "plan.json",
        {
            "registered": time.time(),
            "seed": 202610252,
            "model": "Qwen3.6-27B",
            "contexts": 3,
            "edits": ["scale_origin_2x", "translate_x12"],
            "outcomes": ["correct", "noop"],
            "channels": ["autofit", "locked", "numeric"],
            "judgments": 36,
            "max_new_spend_cny": 2,
            "hypothesis": "Auto-fit can erase absolute scale/position changes; shared camera or exact measurements recover evidence.",
            "main_benchmark_separate": True,
            "no_editor_model_calls": True,
            "not_an_end_to_end_ablation": True,
        },
    )


def generate(folder):
    import cadquery as cq
    from src.utils import cadquery_rendering as rendering
    import numpy as np
    from PIL import Image

    # Preserve material/projection/raster settings; change only camera calibration.
    source = inspect.getsource(rendering.render_to_png)
    locked = source.replace("def render_to_png(", "def render_locked(").replace(
        "highlight=None):", "highlight=None, frame=None):"
    )
    locked = locked.replace(
        "    view.FitAll()",
        """    context.Erase(ais_shape, False)
    calibrator = AIS_Shape(frame.wrapped)
    context.Display(calibrator, AIS_Shaded, 0, True)
    view.FitAll()
    context.Remove(calibrator, False)
    context.Display(ais_shape, AIS_Shaded, 0, True)""",
    )
    namespace = dict(rendering.__dict__)
    exec(locked, namespace)
    frame = cq.Workplane("XY").box(64, 48, 40).val()
    shapes = [
        cq.Workplane("XY")
        .box(20, 12, 6)
        .faces(">Z")
        .workplane()
        .center(4, 0)
        .hole(4)
        .val(),
        cq.Workplane("XY")
        .circle(7)
        .extrude(10)
        .union(cq.Workplane("XY").box(20, 4, 4))
        .val(),
        cq.Compound.makeCompound(
            [
                cq.Workplane("XY").box(9, 12, 6).translate((-7, 0, 0)).val(),
                cq.Workplane("XY").box(6, 6, 12).translate((7, 0, 0)).val(),
            ]
        ),
    ]
    records = []
    for shape_id, shape in enumerate(shapes):
        for edit in ["scale_origin_2x", "translate_x12"]:
            for outcome in ["correct", "noop"]:
                candidate = (
                    shape
                    if outcome == "noop"
                    else shape.scale(2)
                    if edit == "scale_origin_2x"
                    else shape.translate((12, 0, 0))
                )
                prefix = f"shape{shape_id}-{edit}-{outcome}"
                dest = folder / prefix
                dest.mkdir(exist_ok=True)
                for kind, value in [("input", shape), ("candidate", candidate)]:
                    rendering.render_to_png(
                        value,
                        str(dest / (kind + "-autofit.png")),
                        width=512,
                        height=512,
                    )
                    namespace["render_locked"](
                        value,
                        str(dest / (kind + "-locked.png")),
                        width=512,
                        height=512,
                        frame=frame,
                    )
                diffs = {}
                for channel in ["autofit", "locked"]:
                    a = np.asarray(
                        Image.open(dest / ("input-" + channel + ".png")).convert("RGB"),
                        dtype=float,
                    )
                    b = np.asarray(
                        Image.open(dest / ("candidate-" + channel + ".png")).convert(
                            "RGB"
                        ),
                        dtype=float,
                    )
                    diffs[channel] = {
                        "pixel_mean_absolute_difference_0_255": float(
                            np.abs(a - b).mean()
                        ),
                        "changed_pixel_fraction": float(
                            (np.abs(a - b).max(axis=2) > 5).mean()
                        ),
                    }

                def bounds(s):
                    bb = s.BoundingBox()
                    return {
                        "min": [bb.xmin, bb.ymin, bb.zmin],
                        "max": [bb.xmax, bb.ymax, bb.zmax],
                    }

                records.append(
                    {
                        "id": prefix,
                        "shape_id": shape_id,
                        "edit": edit,
                        "correct": outcome == "correct",
                        "pixel_differences": diffs,
                        "input_bbox": bounds(shape),
                        "candidate_bbox": bounds(candidate),
                    }
                )
    write_json(folder / "cases.json", records)
    write_json(
        folder / "renderer.json",
        {
            "upstream_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "change": "camera FitAll uses the same reference frame; no geometry/material/projection differences",
            "frame_size": [64, 48, 40],
        },
    )


def judge(folder):
    cases = json.loads((folder / "cases.json").read_text())
    pricing = json.loads((HERE / ".local/pricing.json").read_text())
    budget = Budget(HERE / ".local/budget.json", 100, pricing)
    plan = json.loads((folder / "plan.json").read_text())
    jobs = [(c, channel) for c in cases for channel in plan["channels"]]
    random.Random(plan["seed"]).shuffle(jobs)

    def rate(pair):
        case, channel = pair
        dest = folder / case["id"] / channel
        dest.mkdir(exist_ok=True)
        if (dest / "rating.json").exists() or any(
            (dest / "api").glob("*-request.json")
        ):
            return
        data = json.loads(budget.path.read_text())
        spent = sum(
            c.get("estimated_cny", 0)
            for c in data["calls"]
            if c["label"].startswith("observation/")
        )
        if spent >= plan["max_new_spend_cny"]:
            return
        request = (
            "Scale the original model uniformly by factor 2 about the global origin."
            if case["edit"] == "scale_origin_2x"
            else "Translate the original model by exactly +12 units along the global X axis; preserve its dimensions."
        )
        text = (
            "You verify a CAD edit from two images, original then candidate. Requested edit: "
            + request
        )
        text += " Rendering uses " + (
            "the same fixed world camera and scale for both images."
            if channel == "locked"
            else "automatic fit to each model, same view direction."
        )
        text += ' Return only JSON {"satisfied":true/false/null,"confidence":0.0,"reason":"brief evidence"}. Use null when available observations cannot determine the answer. Do not assume an action was executed merely because it was requested.'
        content = [{"type": "text", "text": text}]
        kind = "locked" if channel == "locked" else "autofit"
        for name in ["input", "candidate"]:
            path = folder / case["id"] / (name + "-" + kind + ".png")
            content += [
                {"type": "text", "text": name.upper()},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + base64.b64encode(path.read_bytes()).decode()
                    },
                },
            ]
        if channel == "numeric":
            content.append(
                {
                    "type": "text",
                    "text": "Exact kernel bounding boxes: "
                    + json.dumps(
                        {
                            "input": case["input_bbox"],
                            "candidate": case["candidate_bbox"],
                        }
                    ),
                }
            )
        transport = Transport(
            load_credentials(AGENTIC / ".local/provider.env"),
            budget,
            dest / "api",
            "observation/" + case["id"] + "/" + channel,
        )
        try:
            r = transport.create(
                model=plan["model"],
                messages=[{"role": "user", "content": content}],
                max_tokens=512,
                temperature=0,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            text = r.choices[0].message.content.strip()
            if text.startswith("```"):
                text = "\n".join(text.splitlines()[1:-1])
            result = json.loads(text)
            if (
                result.get("satisfied") is not None
                and type(result["satisfied"]) is not bool
            ):
                raise ValueError("Invalid judgment")
            result["ground_truth"] = case["correct"]
            result["correct"] = result["satisfied"] is case["correct"]
            result["channel"] = channel
            result["case"] = case["id"]
            write_json(dest / "rating.json", result)
            print(case["id"], channel, result, flush=True)
        except Exception as error:
            write_json(dest / "error.json", {"error": type(error).__name__})

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(rate, jobs))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["register", "generate", "judge"])
    args = p.parse_args()
    folder = HERE / ".local/observation-probe"
    globals()[args.command](folder)
