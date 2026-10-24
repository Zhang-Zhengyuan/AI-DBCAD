"""Round 1: registered common-task comparison of three research harnesses."""

# ruff: noqa: E402 -- bootstrap paths for pinned, separately cloned research code
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import contextlib
from datetime import datetime
import importlib.util
import io
import json
from pathlib import Path
import random
import subprocess
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
LOCAL = HERE / ".local"
AGENTIC = HERE.parent / "agenticcad_edit"
NEURAL = HERE.parent / "neuralcad_edit"
ITERCAD = LOCAL / "itercad"
sys.path[:0] = [str(AGENTIC), str(AGENTIC / ".local/upstream")]
from provider import (
    Budget,
    BudgetExceeded,
    Transport,
    is_budget_exceeded,
    load_credentials,
)
from study import tasks, write_json

ARMS = ["neuralcad", "agentic", "itercad"]


def register():
    import pandas as pd

    requests = pd.read_csv(
        AGENTIC
        / ".local/upstream/consolidated_data/edits_v2_df_requests_2026_07_22.csv"
    )
    rng = random.Random(202610251)
    chosen = []
    modalities = ["text", "interactive", "draw-temp", "draw-static"]
    actual = sorted(requests.modality.unique())
    if set(modalities) != set(actual):
        raise ValueError(f"Inspect actual modality labels: {actual}")
    categories = [(False, False), (False, True), (True, False), (True, True)]
    for m, modality in enumerate(modalities):
        for d, difficulty in enumerate(["easy", "medium", "hard"]):
            cell = m * 3 + d
            for category in [(0, 1), (2, 3), (0, 2), (1, 3)][cell % 4]:
                assembly, parametric = categories[category]
                group = requests.loc[
                    (requests.modality == modality)
                    & (requests.difficulty == difficulty)
                    & (requests.assembly == assembly)
                    & (requests.parametric == parametric)
                ]
                rows = group.to_dict("records")
                rng.shuffle(rows)
                row = rows[0]
                chosen.append(
                    {
                        k: row[k]
                        for k in [
                            "_id",
                            "modality",
                            "difficulty",
                            "assembly",
                            "parametric",
                            "text",
                        ]
                    }
                )
    rng.shuffle(chosen)
    folder = LOCAL / ("primary-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    folder.mkdir(parents=True)
    plan = {
        "created": datetime.now().isoformat(),
        "seed": 202610251,
        "tasks": chosen,
        "arms": ARMS,
        "model": "Qwen3.6-27B",
        "max_iters": 6,
        "max_tokens": 4096,
        "temperature": 0.6,
        "top_p": 0.95,
        "thinking": True,
        "fps": 0.5,
        "budget_cny": 100,
        "generation_stop_cny": 83,
        "episode_soft_stop_cny": 99.0,
        "model_weight_revision": "provider alias, unverified",
        "commits": {
            "neuralcad": "68d1acceda0743e364f45fb7823f7cf39c530ba5",
            "agentic": "c084034cdcf462025a60ee70c5ba94577428e5ca",
            "itercad": "55998c4a4537573f67ca241dedc52ee2840652ca",
        },
        "adaptations": [
            "IterCAD-Edit imports input STEP through a minimal original program",
            "IterCAD receives the same request video/transcript as other methods",
            "IterCAD pass@1, no ground-truth-based best-of-K or GT feedback",
            "Common max model calls and output tokens; shared API provider",
            "Baseline forwards input STEP to original executor via its input_file parameter",
            "Monetary stops are retained as budget-censored outcomes",
        ],
        "primary_outcome": "Blinded VLM acceptance proxy gated by valid positive-volume solids",
        "secondary": [
            "original CD / surface-voxel IoU / DINO",
            "no-op and solid defects",
            "cost",
            "paired uncertainty",
        ],
        "no_human_acceptance_claim": True,
    }
    write_json(folder / "plan.json", plan)
    write_json(LOCAL / "current.json", {"run": str(folder)})
    counts = (
        requests.loc[requests["_id"].isin([r["_id"] for r in chosen])]
        .groupby(["modality", "difficulty"])
        .size()
    )
    print(
        json.dumps(
            {
                "run": str(folder),
                "tasks": len(chosen),
                "episodes": len(chosen) * 3,
                "counts": {"/".join(k): int(v) for k, v in counts.items()},
            }
        )
    )


def current():
    return Path(json.loads((LOCAL / "current.json").read_text())["run"])


def config(plan):
    value = json.loads(
        (AGENTIC / ".local/upstream/src/config/edit_192_external.json").read_text()
    )["benchmark_models"]["qwen3.6-27b_cadquery-script"].copy()
    value.update(
        model=plan["model"],
        max_tokens=plan["max_tokens"],
        **{"1m_token_cost_input": 3.0, "1m_token_cost_output": 18.0},
    )
    return value


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def worker(run, request_id, arm, offline=False):
    # Multiple launchers may share a run. Lock the whole episode, then recheck.
    import fcntl

    folder = run / request_id / arm
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "episode.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _worker(run, request_id, arm, offline)


def _worker(run, request_id, arm, offline=False):
    # Import MCP before task-specific stream redirects (see prior logging regression).
    import mcp.client.stdio  # noqa: F401 -- initialize MCP logging before redirects
    from src.vlms.base_vlm import BaseVLM as AgentBase
    from src.vlms.vllm_openai import VLM as ApiVLM

    plan = json.loads((run / "plan.json").read_text())
    allocation = run / "allocation-amendment.json"
    if allocation.exists():
        plan["generation_stop_cny"] = json.loads(allocation.read_text())[
            "new_generation_soft_stop"
        ]
    settings = config(plan)
    folder = run / request_id / arm
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / "result.json").exists():
        return
    if any((folder / "api").glob("*-request.json")):
        raise RuntimeError(
            "Unfinished paid attempt exists; preserve it and inspect before resume"
        )
    pricing = json.loads((LOCAL / "pricing.json").read_text())
    budget = Budget(LOCAL / "budget.json", 100, pricing)
    transport = Transport(
        load_credentials(AGENTIC / ".local/provider.env"),
        budget,
        folder / "api",
        request_id + "/" + arm,
    )
    episode_cost = 0.0
    fake_calls = 0

    def call(messages, tools=None):
        nonlocal episode_cost, fake_calls
        if offline:
            from openai.types.chat import ChatCompletion

            fake_calls += 1
            if arm == "agentic":
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"test-{fake_calls}",
                            "type": "function",
                            "function": {
                                "name": "inspect" if fake_calls == 1 else "finish",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            elif arm == "neuralcad":
                code = f"def my_cad_function(args):\n    return cq.importers.importStep({task['brep_start_path_step']!r})\n"
                message = {
                    "role": "assistant",
                    "content": json.dumps(
                        {"complete": fake_calls > 1, "my_cad_function": code}
                    ),
                }
            else:
                message = {
                    "role": "assistant",
                    "content": f"```python\nimport cadquery as cq\nr = cq.importers.importStep({task['brep_start_path_step']!r})\n```"
                    if fake_calls == 1
                    else "<DONE>",
                }
            return ChatCompletion.model_validate(
                {
                    "id": "offline",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "offline",
                    "choices": [
                        {"index": 0, "message": message, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "total_tokens": 120,
                    },
                }
            )
        if budget.summary()["charged_or_reserved_cny"] >= plan["generation_stop_cny"]:
            raise BudgetExceeded(
                "Generation allocation reached; preserve room for evaluation and UI"
            )
        if episode_cost >= plan["episode_soft_stop_cny"]:
            raise BudgetExceeded("Episode monetary cap reached")
        kwargs = dict(
            model=settings["model"],
            messages=messages,
            max_tokens=settings["max_tokens"],
            temperature=settings["temperature"],
            top_p=settings["top_p"],
            extra_body={"chat_template_kwargs": settings["chat_template_kwargs"]},
        )
        if tools is not None:
            kwargs.update(tools=tools, tool_choice="auto")
        response = transport.create(**kwargs)
        if response.usage:
            episode_cost += (
                response.usage.prompt_tokens * 3 + response.usage.completion_tokens * 18
            ) / 1e6
        return response

    class AgentVLM(ApiVLM):
        def __init__(self):
            AgentBase.__init__(self, settings, cache=False)
            self.config = settings.copy()

        def _chat_create(self, messages, tools=None):
            return call(messages, tools)

    task = tasks([request_id])[0]
    started = time.monotonic()
    record = {
        "request": request_id,
        "arm": arm,
        "model": plan["model"],
        "status": "running",
    }
    try:
        with (
            (folder / "harness.log").open("w") as log,
            contextlib.redirect_stdout(log),
            contextlib.redirect_stderr(log),
        ):
            if arm == "agentic":
                model = AgentVLM()
                record["upstream_result"] = model.cadquery_mcp(
                    "Edit the existing model according to the provided instructions.",
                    task,
                    str(
                        AGENTIC / ".local/upstream/src/harnesses/cadquery_mcp/server.py"
                    ),
                    str(folder),
                    plan["max_iters"],
                )
            elif arm == "neuralcad":
                original = module(
                    "original_neural_base",
                    NEURAL / ".local/upstream/src/vlms/base_vlm.py",
                )

                class OriginalVLM(original.BaseVLM):
                    create_messages = ApiVLM.create_messages
                    load_image = ApiVLM.load_image

                    def __init__(self):
                        super().__init__(settings, cache=False)
                        self.config = settings.copy()

                    def load_video(self, path):
                        from PIL import Image

                        encoded = []
                        for frame in super().load_video(path):
                            buffer = io.BytesIO()
                            Image.fromarray(frame).save(buffer, format="PNG")
                            encoded.append(base64.b64encode(buffer.getvalue()).decode())
                        return encoded

                    def run_cadquery_script(
                        self, script, harness_script_file, output_dir, input_file=None
                    ):
                        # Interoperability repair only; preserves the original stateless loop.
                        return super().run_cadquery_script(
                            script,
                            harness_script_file,
                            output_dir,
                            input_file=task["brep_start_path_step"],
                        )

                    def generate_response(
                        self, messages, output_path=None, return_token_counts=False
                    ):
                        response = call(messages)
                        msg = response.choices[0].message
                        text = msg.content or ""
                        if text.startswith("```json"):
                            text = "\n".join(text.splitlines()[1:-1])
                        try:
                            parsed = json.loads(text)
                        except ValueError:
                            parsed = text
                        usage = response.usage
                        return original.GenerateResponseResult(
                            response_json=parsed,
                            response_text=text,
                            thinking_text=getattr(msg, "reasoning_content", "") or "",
                            token_counts={
                                "input_tokens": usage.prompt_tokens,
                                "output_tokens": usage.completion_tokens,
                                "thinking_tokens": 0,
                            },
                        )

                model = OriginalVLM()
                record["upstream_result"] = model.cadquery_script(
                    "Edit the existing model according to the provided instructions.",
                    task,
                    str(NEURAL / ".local/upstream/src/harnesses/cadquery_script.py"),
                    str(folder),
                    plan["max_iters"],
                )
            else:
                sys.path.insert(0, str(ITERCAD / "eval"))
                evaluator = module("itercad_evaluator", ITERCAD / "eval/evalution.py")
                helper = AgentVLM()
                common = helper.create_messages(
                    helper.load_task_info_dict(task), sys=None
                )[0]["content"]

                class IterClient:
                    def __init__(self, cfg):
                        self.system_prompt = evaluator.SYSTEM_PROMPT

                    def chat(self, messages, **kwargs):
                        adapted = json.loads(json.dumps(messages))
                        initial = adapted[1]["content"]
                        if isinstance(initial, str):
                            adapted[1]["content"] = [
                                {"type": "text", "text": initial}
                            ] + common
                        response = call(adapted)
                        return response.choices[0].message.content or "", None

                evaluator.GeneratorClient = IterClient
                source = folder / "input_program.py"
                source.write_text(
                    f"import cadquery as cq\nr = cq.importers.importStep({task['brep_start_path_step']!r})\n"
                )
                sample = {
                    "uid": request_id,
                    "input_code_path": str(source),
                    "instruction_quantitative": "Edit the existing model according to the request information supplied below.",
                }
                result = evaluator.process_itercad_edit(
                    sample,
                    {"temp_dir": str(folder / "temp"), "max_turns": plan["max_iters"]},
                )
                write_json(folder / "itercad_trace.json", result)
                if result.get("last_valid_code"):
                    ok, message, shape = evaluator.execute_code(
                        result["last_valid_code"]
                    )
                    if ok:
                        import cadquery as cq

                        cq.exporters.export(shape, str(folder / "tmp.step"))
                record["upstream_result"] = {
                    k: v
                    for k, v in result.items()
                    if k not in ["all_turns_data", "last_valid_code"]
                }
        record["status"] = "exported" if (folder / "tmp.step").exists() else "no_output"
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        record.update(
            status="budget_stop" if is_budget_exceeded(error) else "error",
            error_type=type(error).__name__,
        )
        (folder / "error.log").write_text(traceback.format_exc())
    record.update(
        seconds=time.monotonic() - started,
        calls=transport.counter,
        estimated_cny=episode_cost,
        step_exists=(folder / "tmp.step").exists(),
    )
    write_json(folder / "result.json", record)
    print(json.dumps(record), flush=True)


def batch(run, workers, start_index=0, end_index=None):
    plan = json.loads((run / "plan.json").read_text())
    jobs = []
    for index, row in enumerate(plan["tasks"]):
        order = ARMS[index % 3 :] + ARMS[: index % 3]
        jobs.extend((row["_id"], arm) for arm in order)

    def launch(job):
        rid, arm = job
        folder = run / rid / arm
        folder.mkdir(parents=True, exist_ok=True)
        if (folder / "result.json").exists():
            return {"request": rid, "arm": arm, "status": "existing"}
        with (folder / "worker-console.log").open("w") as output:
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(HERE / "experiment.py"),
                        "worker",
                        "--run",
                        str(run),
                        "--request",
                        rid,
                        "--arm",
                        arm,
                    ],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=1800,
                )
            except subprocess.TimeoutExpired:
                write_json(
                    folder / "result.json",
                    {
                        "request": rid,
                        "arm": arm,
                        "status": "episode_timeout",
                        "timeout_seconds": 1800,
                        "calls": len(list((folder / "api").glob("*-request.json"))),
                        "seconds": 1800,
                        "step_exists": (folder / "tmp.step").exists(),
                    },
                )
                return json.loads((folder / "result.json").read_text())
        if not (folder / "result.json").exists():
            write_json(
                folder / "result.json",
                {
                    "request": rid,
                    "arm": arm,
                    "status": "worker_failure",
                    "exit_code": result.returncode,
                },
            )
        return json.loads((folder / "result.json").read_text())

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(launch, job): job for job in jobs[start_index:end_index]}
        for future in as_completed(futures):
            try:
                print(json.dumps(future.result()), flush=True)
            except Exception as error:
                print(
                    json.dumps(
                        {"worker_error": type(error).__name__, "job": futures[future]}
                    ),
                    flush=True,
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("register")
    p = sub.add_parser("batch")
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--run", type=Path)
    p.add_argument("--start-index", type=int, default=0)
    p.add_argument("--end-index", type=int)
    p = sub.add_parser("worker")
    p.add_argument("--request", required=True)
    p.add_argument("--arm", choices=ARMS, required=True)
    p.add_argument("--run", type=Path)
    p.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.command == "register":
        register()
    elif args.command == "batch":
        batch(args.run or current(), args.workers, args.start_index, args.end_index)
    else:
        worker(args.run or current(), args.request, args.arm, args.offline)


if __name__ == "__main__":
    main()
