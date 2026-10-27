"""EXP-003 registration, original-framework execution and offline scoring.

No paid call occurs without the `--execute` flag. Runs and episode directories
are append-only; an existing attempt is never silently retried or overwritten.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import signal
import subprocess
import sys
import time
import traceback

from suite import cases, fingerprint, public_case, suite_manifest
from upstreams import PINS, dump, module, run_candidate, verify_checkout

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def slug(value):
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return clean[:80] + "-" + hashlib.sha256(value.encode()).hexdigest()[:8]


def neural_tasks(config):
    """Reuse the verified request-only loader, with explicit external data paths."""
    path = Path(config["upstreams"]["agentic"])
    sys.path.insert(0, str(path))
    study = module("exp003_request_loader", ROOT / "research/agenticcad_edit/study.py")
    study.UPSTREAM = path
    study.DATA = Path(config["data_dir"])
    return study.tasks()


def validate_config(config):
    if not 0 < float(config["budget_cny"]) <= 300:
        raise ValueError("EXP-003 authorization is a new hard cap of CNY 300; never reset another ledger")
    if not config.get("ledger"):
        raise ValueError("A root-managed shared budget ledger is required")
    if not config.get("models"):
        raise ValueError("At least one explicitly priced model is required")
    labels = []
    for model in config["models"]:
        labels.append(model["label"])
        pricing = model["pricing"]
        for key in ["input_cny_per_million", "output_cny_per_million", "context_tokens"]:
            minimum_ok = float(pricing[key]) >= 0 if model.get("local") and key != "context_tokens" else float(pricing[key]) > 0
            if not minimum_ok:
                raise ValueError(f"Verified positive pricing field required: {key}")
    if len(set(labels)) != len(labels):
        raise ValueError("Model labels must be unique")
    for candidate in config.get("candidates", ["agentic", "itercad"]):
        if candidate not in PINS:
            raise ValueError(f"Unknown original framework {candidate}")
        verify_checkout(config["upstreams"][candidate], PINS[candidate])


def register(config_path, output):
    config = read(config_path)
    validate_config(config)
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Run directory exists; registration never overwrites runs")
    output.mkdir(parents=True)
    manifest = suite_manifest()
    dump(output / "suite-frozen.json", manifest)
    # Private runtime paths are local run metadata, never shared results.
    dump(output / "config.json", config)
    neural = neural_tasks(config) if config.get("data_dir") else []
    if config.get("data_dir") and len(neural) != 192:
        raise ValueError(f"Expected all 192 released neuralCAD requests; got {len(neural)}")
    dump(output / "neural-requests.json", neural)
    plan = {
        "experiment": "EXP-003", "protocol_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "seed": int(config.get("seed", 20261027)), "suite_sha256": manifest["sha256"],
        "source_hashes": {p.name: digest(p) for p in HERE.glob("*.py")},
        "upstream_commits": PINS,
        "models": [{k: v for k, v in m.items() if k != "credentials"} for m in config["models"]],
        "candidates": config.get("candidates", ["agentic", "itercad"]),
        "capability_cases": manifest["case_count"], "capability_stages_per_arm": manifest["stage_count"],
        "neural_allocated": 192, "neural_loaded": len(neural),
        "pass_k": 1, "ground_truth_feedback": False,
        "paid_calls_started": False, "budget_cny": float(config["budget_cny"]),
        "notes": [
            "Official original loops, API transport replacement only; source/STEP adaptations are labelled",
            "CadQuery/OCP original-framework selection is separate from native-ACIS parity acceptance",
            "Each semantic request uses the original per-request loop; geometry is inherited across stages",
            "This capability suite is not a natural long-horizon dialogue benchmark",
            "Model API aliases do not establish fixed weight revisions or official IterCAD checkpoint identity",
        ],
    }
    dump(output / "run.json", plan)
    print(json.dumps({"run": str(output), "cases": len(manifest["cases"]),
                      "stages_per_arm": manifest["stage_count"], "neural_requests": len(neural)}, ensure_ascii=False))


def verify_run(root):
    plan, config = read(root / "run.json"), read(root / "config.json")
    if read(root / "suite-frozen.json")["sha256"] != plan["suite_sha256"]:
        raise ValueError("Frozen suite mismatch")
    for name, expected in plan["source_hashes"].items():
        if digest(HERE / name) != expected:
            raise ValueError(f"Experiment implementation changed after registration: {name}; register a new run")
    return plan, config


def model_by_label(config, label):
    return next(m for m in config["models"] if m["label"] == label)


def episode_folder(root, model, candidate, case, stage):
    return root / "episodes" / slug(model) / candidate / case / stage


def budget_exceeded(error):
    return type(error).__name__ in {"BudgetExceeded", "TransportBudgetExceeded"} or any(
        budget_exceeded(nested) for nested in getattr(error, "exceptions", ()))


def usage_from_files(folder, model):
    """Include failed attempts with saved requests but missing/unknown responses."""
    input_tokens = output_tokens = 0
    responses = list((folder / "api").glob("call-*-response.json"))
    requests = list((folder / "api").glob("call-*-request.json"))
    for path in responses:
        usage = read(path).get("usage") or {}
        input_tokens += usage.get("prompt_tokens", 0) or 0
        output_tokens += usage.get("completion_tokens", 0) or 0
    price = model["pricing"]
    return {"calls": len(requests), "responses": len(responses),
            "uncertain_calls": max(0, len(requests) - len(responses)),
            "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens},
            "estimated_cny": (input_tokens * price["input_cny_per_million"] + output_tokens * price["output_cny_per_million"]) / 1e6}


def create_input(case, index, previous, folder, candidate):
    from geometry import export_step, load_step, shape_of, trusted_shape
    import cadquery as cq
    if index == 0:
        source = case["initial_code"]
        shape = trusted_shape(source)
        representation = "authored_source"
    else:
        previous_result = read(previous / "result.json")
        previous_step = Path(previous_result.get("output_step", previous / "tmp.step"))
        if not previous_step.is_file():
            raise RuntimeError("predecessor_missing_output")
        shape = load_step(previous_step)
        previous_code = previous_result.get("source_path")
        if candidate == "itercad" and previous_code and Path(previous_code).is_file():
            source = Path(previous_code).read_text()
            representation = "actual_generated_source"
        else:
            source = f"import cadquery as cq\nr = cq.importers.importStep({str(previous_step)!r})\n"
            representation = "actual_step_snapshot"
        manual = case.get("manual_after_s1")
        if manual:
            namespace = {"cq": cq, "r": cq.Workplane("XY").newObject([shape])}
            exec(compile(manual["code"], "<exp003-human-edit>", "exec"), namespace, namespace)
            shape = shape_of(namespace["r"])
            if case["group"] == "external_source" and representation == "actual_generated_source":
                source += "\n# Human edit applied to the actual preceding result.\n" + manual["code"] + "\n"
            else:
                representation = "human_updated_step_snapshot"
    input_path = folder / "input.step"
    export_step(shape, input_path)
    if representation in {"human_updated_step_snapshot", "actual_step_snapshot"}:
        source = f"import cadquery as cq\nr = cq.importers.importStep({str(input_path)!r})\n"
    source_path = folder / "input_program.py"
    source_path.write_text(source)
    return input_path, source_path, representation


def stage_episode(root, model_label, candidate, case_id, stage_id):
    plan, config = verify_run(root)
    model = model_by_label(config, model_label)
    case = next(c for c in read(root / "suite-frozen.json")["cases"] if c["id"] == case_id)
    index = next(i for i, stage in enumerate(case["stages"]) if stage["id"] == stage_id)
    folder = episode_folder(root, model_label, candidate, case_id, stage_id)
    folder.mkdir(parents=True, exist_ok=False)
    record = {"model": model_label, "candidate": candidate, "case": case_id, "stage": stage_id,
              "group": case["group"], "status": "running", "started_at": datetime.now(timezone.utc).isoformat()}
    dump(folder / "result.json", record)
    started = time.monotonic()
    try:
        previous = episode_folder(root, model_label, candidate, case_id, "s1")
        input_path, source_path, representation = create_input(case, index, previous, folder, candidate)
        instructions = [s["instruction"] for s in case["stages"][:index + 1]]
        instruction = "Units are millimetres. Work on the current model; preserve unrelated geometry.\n"
        if index:
            instruction += "Earlier requirements still apply:\n" + "\n".join(instructions[:-1]) + "\n"
            if case.get("manual_after_s1"):
                instruction += "External human edit: " + case["manual_after_s1"]["description"] + "\n"
        instruction += "Current request:\n" + instructions[-1]
        (folder / "instruction.txt").write_text(instruction)
        request = {"id": f"{case_id}-{stage_id}", "label": f"EXP003/{model_label}/{candidate}/{case_id}/{stage_id}",
                   "instruction": instruction, "input_step": str(input_path), "source_path": str(source_path)}
        record.update(input_step=str(input_path), input_representation=representation)
        dump(folder / "request.json", request)
        record.update(run_candidate(config, model, candidate, folder, request))
        record["status"] = "completed"
    except Exception as error:
        record.update(status="budget_censored" if budget_exceeded(error) else "failed",
                      error_type=type(error).__name__, error=str(error)[:2000])
        (folder / "exception.log").write_text(traceback.format_exc())
    finally:
        record.update(usage_from_files(folder, model), elapsed_s=time.monotonic() - started,
                      ended_at=datetime.now(timezone.utc).isoformat())
        dump(folder / "result.json", record)
    return record


def neural_episode(root, model_label, candidate, request_id):
    plan, config = verify_run(root)
    selected = read(root / "selection.json")
    if (selected["model"], selected["candidate"]) != (model_label, candidate):
        raise ValueError("neural192 execution must match the explicitly frozen selection")
    model = model_by_label(config, model_label)
    task = next(t for t in read(root / "neural-requests.json") if t["request"] == request_id)
    folder = root / "neural192" / slug(model_label) / candidate / slug(request_id)
    folder.mkdir(parents=True, exist_ok=False)
    record = {"model": model_label, "candidate": candidate, "request": request_id,
              "status": "running", "started_at": datetime.now(timezone.utc).isoformat()}
    dump(folder / "result.json", record)
    started = time.monotonic()
    try:
        request = {"id": request_id, "label": f"EXP003/neural192/{model_label}/{candidate}/{request_id}",
                   "instruction": "Edit the existing model according to the supplied original editing request.",
                   "task": task, "input_step": task["brep_start_path_step"]}
        if candidate == "itercad":
            # Explicitly labelled STEP/multimodal adapter, not official source Edit.
            source = folder / "input_program.py"
            source.write_text(f"import cadquery as cq\nr = cq.importers.importStep({task['brep_start_path_step']!r})\n")
            sys.path.insert(0, str(config["upstreams"]["agentic"]))
            from src.vlms.base_vlm import BaseVLM
            from src.vlms.vllm_openai import VLM
            settings = {"backend": "cv2", "fps": 0.5, "resolution": 720, "system_prompt": ""}
            helper = VLM.__new__(VLM)
            BaseVLM.__init__(helper, settings, cache=False)
            helper.config = settings
            content = helper.create_messages(helper.load_task_info_dict(task), sys=None)[0]["content"]
            request.update(source_path=str(source), extra_content=content)
        record.update(run_candidate(config, model, candidate, folder, request), status="completed")
    except Exception as error:
        record.update(status="budget_censored" if budget_exceeded(error) else "failed",
                      error_type=type(error).__name__, error=str(error)[:2000])
        (folder / "exception.log").write_text(traceback.format_exc())
    finally:
        record.update(usage_from_files(folder, model), elapsed_s=time.monotonic() - started,
                      ended_at=datetime.now(timezone.utc).isoformat())
        dump(folder / "result.json", record)
    return record


def _child(command, folder, timeout):
    """Bound only the process group created for this experiment episode."""
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "controller.log").open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
            return {"controller_exit": code}
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            return {"controller_exit": "timeout"}


def batch(root, args):
    plan, config = verify_run(root)
    candidates = [args.candidate] if args.candidate else plan["candidates"]
    models = [args.model] if args.model else [m["label"] for m in config["models"]]
    commands = []
    if args.track == "capabilities":
        selected = read(root / "suite-frozen.json")["cases"]
        if args.case:
            selected = [c for c in selected if c["id"] == args.case]
        for model in models:
            for case in selected:
                for candidate in candidates:
                    # Each request inherits the actual previous request output.
                    for stage in case["stages"]:
                        dest = episode_folder(root, model, candidate, case["id"], stage["id"])
                        command = [sys.executable, str(HERE / "experiment.py"), "stage", "--run", str(root),
                                   "--model", model, "--candidate", candidate, "--case", case["id"],
                                   "--stage", stage["id"], "--execute"]
                        commands.append((dest, command))
    else:
        selected = read(root / "selection.json")
        if args.candidate and args.candidate != selected["candidate"]:
            raise ValueError("Candidate differs from frozen selection")
        if args.model and args.model != selected["model"]:
            raise ValueError("Model differs from frozen selection")
        candidates, models = [selected["candidate"]], [selected["model"]]
        requests = read(root / "neural-requests.json")
        if len(requests) != 192:
            raise ValueError("Full released request manifest must be registered before neural192 execution")
        # Request order is fixed independently of outputs, so budget censoring is auditable.
        random.Random(plan["seed"]).shuffle(requests)
        for task in requests:
            for model in models:
                for candidate in candidates:
                    dest = root / "neural192" / slug(model) / candidate / slug(task["request"])
                    command = [sys.executable, str(HERE / "experiment.py"), "neural", "--run", str(root),
                               "--model", model, "--candidate", candidate, "--request", task["request"], "--execute"]
                    commands.append((dest, command))
    launched = 0
    for dest, command in commands:
        if (dest / "result.json").exists():
            continue
        if args.limit is not None and launched >= args.limit:
            break
        if not args.execute:
            print(json.dumps({"planned": command, "existing_attempt": dest.exists()}))
            continue
        if dest.exists():
            raise RuntimeError(f"Incomplete attempt directory exists; inspect instead of silently retrying: {dest}")
        controller = root / "controllers" / fingerprint(command)[:20]
        result = _child(command, controller, args.timeout)
        # A timeout/crash remains in the allocated denominator and is never retried.
        if not (dest / "result.json").exists():
            dest.mkdir(parents=True, exist_ok=True)
            dump(dest / "result.json", {"status": "controller_failed", **result, "command": command})
        elif result["controller_exit"] != 0:
            record = read(dest / "result.json")
            record.update(status="controller_failed", **result)
            dump(dest / "result.json", record)
        launched += 1
        print(json.dumps({"episode": str(dest.relative_to(root)), **result}), flush=True)
        # Stop at an exhausted/uncertain ledger instead of manufacturing many failed reservations.
        record = read(dest / "result.json")
        if record.get("status") == "budget_censored":
            break


def offline(output):
    """Construct/round-trip every trusted stage and human operation; zero API calls."""
    from geometry import diagnostics, export_step, load_step, score, shape_of, trusted_shape
    import cadquery as cq
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = []
    for case in cases():
        folder = output / case["id"]
        initial = trusted_shape(case["initial_code"])
        export_step(initial, folder / "initial.step")
        for index, stage in enumerate(case["stages"]):
            expected = trusted_shape(stage["reference_code"])
            path = folder / f"{stage['id']}.step"
            export_step(expected, path)
            scored = score(path, stage["reference_code"], folder / "initial.step", case["tolerance"])
            if not scored["geometry_success"]:
                raise AssertionError((case["id"], stage["id"], scored))
            if index == 0 and case.get("manual_after_s1"):
                namespace = {"cq": cq, "r": cq.Workplane("XY").newObject([expected])}
                exec(case["manual_after_s1"]["code"], namespace, namespace)
                human = shape_of(namespace["r"])
                if not diagnostics(human)["brep_valid"]:
                    raise AssertionError("Invalid trusted human operation")
                export_step(human, folder / "human.step")
            report.append({"case": case["id"], "stage": stage["id"], **scored})
    dump(output / "offline.json", {"api_calls": 0, "suite": suite_manifest()["sha256"], "stages": report})
    print(json.dumps({"output": str(output), "verified_stages": len(report), "api_calls": 0}))


def score_run(root):
    from geometry import diagnostics, load_step, score
    plan, config = verify_run(root)
    rows = []
    for model in config["models"]:
        for candidate in plan["candidates"]:
            for case in read(root / "suite-frozen.json")["cases"]:
                for stage in case["stages"]:
                    folder = episode_folder(root, model["label"], candidate, case["id"], stage["id"])
                    record = read(folder / "result.json") if (folder / "result.json").is_file() else {"status": "not_run"}
                    output = record.get("output_step", folder / "tmp.step")
                    grade = score(output, stage["reference_code"], folder / "input.step", case["tolerance"])
                    if candidate == "itercad" and record.get("final_turn_executable") is False:
                        grade["geometry_success"] = False
                        grade["final_turn_failure"] = True
                    row = {"track": "capabilities", "model": model["label"], "candidate": candidate,
                           "case": case["id"], "stage": stage["id"], "group": case["group"],
                           "status": record["status"], "elapsed_s": record.get("elapsed_s"),
                           "calls": record.get("calls"), "usage": record.get("usage"),
                           "estimated_cny": record.get("estimated_cny"), "uncertain_calls": record.get("uncertain_calls"), **grade}
                    if folder.exists():
                        dump(folder / "score.json", row)
                    rows.append(row)
    for model in config["models"]:
        for candidate in plan["candidates"]:
            selection = read(root / "selection.json") if (root / "selection.json").is_file() else None
            if not selection or (model["label"], candidate) != (selection["model"], selection["candidate"]):
                continue
            request_manifest = read(root / "neural-requests.json")
            for task in request_manifest:
                folder = root / "neural192" / slug(model["label"]) / candidate / slug(task["request"])
                record = read(folder / "result.json") if (folder / "result.json").is_file() else {"status": "not_run"}
                path = Path(record.get("output_step", folder / "tmp.step"))
                row = {"track": "neural192", "model": model["label"], "candidate": candidate,
                       "request": task["request"], "status": record["status"], "semantic_success": None,
                       "calls": record.get("calls"), "usage": record.get("usage"),
                       "estimated_cny": record.get("estimated_cny"), "uncertain_calls": record.get("uncertain_calls"), "strict_solid": False}
                if path.is_file():
                    try:
                        info = diagnostics(load_step(path))
                        row.update(geometry=info, strict_solid=bool(info["brep_valid"] and info["solid_count"]
                                   and info["all_solids_valid"] and info["positive_solids"] and not info["loose_face_count"]))
                    except Exception as error:
                        row["import_error"] = type(error).__name__
                if folder.exists():
                    dump(folder / "score.json", row)
                rows.append(row)
    dump(root / "scores.json", rows)
    return rows


def summarize(root):
    rows = read(root / "scores.json")
    groups = defaultdict(list)
    for row in rows:
        groups[(row["track"], row["model"], row["candidate"], row.get("group", "all"))].append(row)
    summary = []
    for (track, model, candidate, group), items in sorted(groups.items()):
        completed = [x for x in items if x["status"] != "not_run"]
        chains = defaultdict(list)
        for item in items:
            if "case" in item:
                chains[item["case"]].append(item)
        summary.append({"track": track, "model": model, "candidate": candidate, "group": group,
                        "allocated": len(items), "attempted": len(completed),
                        "geometry_success": sum(x.get("geometry_success", False) for x in items),
                        "strict_solids": sum(x.get("strict_solid", False) for x in items),
                        "allocated_chains": len(chains),
                        "passed_chains": sum(all(x.get("geometry_success", False) for x in stages) for stages in chains.values()),
                        "estimated_cny": sum(x.get("estimated_cny") or 0 for x in items),
                        "uncertain_calls": sum(x.get("uncertain_calls") or 0 for x in items),
                        "not_run": len(items) - len(completed),
                        "semantic_acceptance_neural192": "not measured; no VLM self-rating substituted"})
    dump(root / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def select(root, model, candidate, reason):
    plan, config = verify_run(root)
    model_by_label(config, model)
    if candidate not in plan["candidates"]:
        raise ValueError("Selection must be a registered candidate")
    target = root / "selection.json"
    if target.exists():
        raise FileExistsError("Selection is frozen; use a new run for a different decision")
    dump(target, {"model": model, "candidate": candidate, "reason": reason,
                  "created_at": datetime.now(timezone.utc).isoformat(),
                  "capability_scores_sha256": digest(root / "scores.json") if (root / "scores.json").is_file() else None,
                  "neural_allocated": 192})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    reg = sub.add_parser("register")
    reg.add_argument("--config", type=Path, required=True)
    reg.add_argument("--run", type=Path, required=True)
    off = sub.add_parser("offline")
    off.add_argument("--output", type=Path, required=True)
    choose = sub.add_parser("select")
    choose.add_argument("--run", type=Path, required=True)
    choose.add_argument("--model", required=True)
    choose.add_argument("--candidate", choices=list(PINS), required=True)
    choose.add_argument("--reason", required=True)
    for name in ["stage", "neural", "batch", "score", "summarize"]:
        s = sub.add_parser(name)
        s.add_argument("--run", type=Path, required=True)
        if name in {"stage", "neural", "batch"}:
            s.add_argument("--model", required=name != "batch")
            s.add_argument("--candidate", choices=list(PINS), required=name != "batch")
            s.add_argument("--execute", action="store_true")
        if name == "stage":
            s.add_argument("--case", required=True)
            s.add_argument("--stage", required=True, choices=["s1", "s2"])
        if name == "neural":
            s.add_argument("--request", required=True)
        if name == "batch":
            s.add_argument("--track", choices=["capabilities", "neural192"], default="capabilities")
            s.add_argument("--case")
            s.add_argument("--limit", type=int)
            s.add_argument("--timeout", type=int, default=1200)
    args = p.parse_args()
    if args.command == "register":
        register(args.config, args.run)
    elif args.command == "offline":
        offline(args.output)
    elif args.command == "select":
        select(args.run.resolve(), args.model, args.candidate, args.reason)
    elif args.command in {"stage", "neural"}:
        if not args.execute:
            p.error("Paid episodes require --execute; registration does not authorize spending")
        root = args.run.resolve()
        result = (stage_episode(root, args.model, args.candidate, args.case, args.stage)
                  if args.command == "stage" else neural_episode(root, args.model, args.candidate, args.request))
        print(json.dumps(result, ensure_ascii=False, default=str))
    elif args.command == "batch":
        batch(args.run.resolve(), args)
    elif args.command == "score":
        score_run(args.run.resolve())
    elif args.command == "summarize":
        summarize(args.run.resolve())


if __name__ == "__main__":
    main()
