"""EXP-002: genuine AgenticCADedit MCP tools, adapted frozen-checkpoint loop.

No modifications to the fixed upstream. All paid attempts share one durable ledger.
Raw transcripts and artifacts are confined to ignored runs/. No automatic paid retries.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
AGENT = ROOT / "research/agenticcad_edit"
UPSTREAM = AGENT / ".local/upstream"
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(UPSTREAM))

# Bind MCP logging outside per-attempt stream redirects.
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from provider import Budget, Transport, load_credentials
from src.vlms.base_vlm import BaseVLM
from src.vlms.vllm_openai import VLM
from scenes import EVENTS, compound, geometry_error, initial_code, refresh_code, score, shapes, spec, task, truth


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def upstream_instruction():
    tree = ast.parse((UPSTREAM / "src/vlms/base_vlm.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "cadquery_mcp":
            for line in node.body:
                if isinstance(line, ast.Assign) and any(getattr(t, "id", "") == "conversation_instruction" for t in line.targets):
                    return ast.literal_eval(line.value)
    raise RuntimeError("Official MCP instruction not found")


def settings(run):
    conf = json.loads((UPSTREAM / "src/config/edit_192_external.json").read_text())["benchmark_models"]["qwen3.6-27b_cadquery-mcp"].copy()
    conf.update(model=run["model"], max_tokens=run["max_tokens"], backend="cv2")
    return conf


class Agent(VLM):
    def __init__(self, run, transport):
        self.config = settings(run)
        BaseVLM.__init__(self, self.config, cache=False)
        self.transport = transport

    def _chat_create(self, messages, tools=None):
        c = self.config
        return self.transport.create(model=c["model"], messages=messages, tools=tools,
            tool_choice="auto", max_tokens=c["max_tokens"], temperature=c["temperature"],
            top_p=c["top_p"], extra_body={"chat_template_kwargs": c["chat_template_kwargs"]})


class Server:
    def __init__(self, folder):
        self.folder = folder
        self.events = []

    async def __aenter__(self):
        self.stack = contextlib.AsyncExitStack()
        self.log = self.stack.enter_context((self.folder / "mcp-stderr.log").open("w"))
        params = StdioServerParameters(command=sys.executable,
            args=[str(UPSTREAM / "src/harnesses/cadquery_mcp/server.py")])
        read, write = await self.stack.enter_async_context(stdio_client(params, errlog=self.log))
        self.session = await self.stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        self.tools = (await self.session.list_tools()).tools
        return self

    async def __aexit__(self, *args):
        dump(self.folder / "tool-events.json", self.events)
        await self.stack.__aexit__(*args)

    async def call(self, name, args, actor="agent"):
        started = time.monotonic()
        result = await asyncio.wait_for(self.session.call_tool(name, args), timeout=120)
        images = []
        text = "".join(getattr(b, "text", "") for b in result.content)
        for i, b in enumerate(result.content):
            if b.type == "image":
                import base64
                filename = self.folder / f"tool-{len(self.events):03d}-{i}.png"
                filename.write_bytes(base64.b64decode(b.data))
                images.append(dict(path=filename.name, sha256=sha(filename)))
        self.events.append(dict(actor=actor, name=name, args=args, text=text,
            images=images, is_error=result.isError, elapsed_s=time.monotonic()-started))
        dump(self.folder / "tool-events.json", self.events)
        if actor == "agent" and name in {"run_step", "undo", "reset"}:
            # Snapshot every visible editing boundary; never feed audit data back.
            await self.call("export_step", {"output_dir": str(self.folder / "states" / f"tool-{len(self.events)-1:03d}")}, "state_audit")
        return result


async def host_step(server, code, actor="host"):
    result = await server.call("run_step", {"code": code}, actor)
    text = "".join(getattr(b, "text", "") for b in result.content)
    data = json.loads(text)
    if not data.get("ok"):
        raise RuntimeError(f"Host tool failed: {text}")
    return data


async def loop(agent, server, messages, folder, max_calls, checkpoint_mode=False):
    tools = agent.build_tool_specs(server.tools)
    measured = set()
    for _ in range(max_calls):
        turn = await asyncio.to_thread(agent.request_tool_turn, messages, tools)
        messages.extend(m.model_dump(exclude_none=True) if hasattr(m, "model_dump") else m for m in turn.messages)
        dump(folder / "messages.json", messages)
        if not turn.tool_calls:
            return "no_tool_call"
        pending_images = []
        done = False
        for tc in turn.tool_calls:
            if tc.name == "finish":
                done = True
                # Unlike the upstream loop, retain a valid finish tool response so the
                # same conversation can be resumed through an OpenAI-compatible API.
                messages.append(agent.create_tool_result_message(tc.id, "Paused/completed."))
                continue
            result = await server.call(tc.name, tc.args)
            result_text = agent.tool_result_text(result)
            messages.append(agent.create_tool_result_message(tc.id, result_text))
            pending_images.extend(agent.tool_result_images(result))
            if checkpoint_mode and tc.name == "run_step" and json.loads(result_text).get("ok"):
                tree = ast.parse(tc.args["code"])
                measured.update(n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                                and n.attr in {"distance", "intersect"})
        if pending_images:
            messages.append(agent.create_image_user_message(pending_images))
        dump(folder / "messages.json", messages)
        if checkpoint_mode and measured == {"distance", "intersect"}:
            # A controller-defined pause after real successful query calls. Do not
            # wait for finish, which can make the model drift into executing the edit.
            # Eligibility still needs an unchanged-geometry check and trace review.
            return "checkpoint_ready"
        if done:
            return "finished"
    return "iteration_limit"


def new_agent(run, folder, label):
    budget = Budget(Path(run["run_root"]) / "budget.json", run["budget_cny"], run["pricing"])
    return Agent(run, Transport(load_credentials(AGENT / ".local/provider.env"), budget, folder / "api", label))


def start_record(folder, run, **extra):
    folder.mkdir(parents=True, exist_ok=False)
    record = dict(status="running", started_at=datetime.now(timezone.utc).isoformat(),
                  protocol_sha256=sha(Path(run["run_root"]) / "run.json"),
                  source_hashes={p.name: sha(p) for p in (HERE / "pilot.py", HERE / "scenes.py")}, **extra)
    dump(folder / "record.json", record)
    return record


async def checkpoint(root, run, scene, dev):
    label = f"{'dev' if dev else 'primary'}-{scene}" + run.get("checkpoint_suffix", "")
    folder = root / "checkpoints" / label
    if folder.exists():
        raise RuntimeError("Checkpoint exists: never retry or overwrite a paid attempt")
    s = spec(scene, dev)
    record = start_record(folder, run, kind="checkpoint", scene=s)
    started = time.monotonic()
    messages = []
    try:
        agent = new_agent(run, folder, "checkpoint/" + label)
        async with Server(folder) as server:
            await host_step(server, initial_code(s), "initialization")
            messages = agent.create_messages([upstream_instruction(), task(s),
                "First perform a preflight only: inspect the real scene, compute the candidate placement's clearance "
                "and collision checks, and state your short intended action and the measurements supporting it. "
                "Do not move or modify any geometry yet. Call finish() when this preflight is ready; "
                "a later message will ask you to execute the edit. scene_version=0."], sys=agent.config["system_prompt"])
            record["status"] = await loop(agent, server, messages, folder, run["checkpoint_max_calls"], checkpoint_mode=True)
            await server.call("export_step", {"output_dir": str(folder)}, "audit")
            import cadquery as cq
            actual = cq.importers.importStep(str(folder / "tmp.step")).val()
            record["preflight_unchanged"] = geometry_error(actual, compound(shapes(s))) < 1e-5
            record["truth_at_observation"] = truth(s, "E0")
    except Exception as exc:
        record.update(status="exception", error_type=type(exc).__name__, error=str(exc))
        (folder / "exception.log").write_text(traceback.format_exc())
    finally:
        record["elapsed_s"] = time.monotonic() - started
        dump(folder / "messages.json", messages)
        dump(folder / "record.json", record)
        print(json.dumps(dict(checkpoint=label, **record), ensure_ascii=False), flush=True)


async def trial(root, run, cell, dev):
    scene, event, policy = cell
    prefix = "dev" if dev else "primary"
    label = f"{scene}-{event}-{policy}"
    folder = root / prefix / label
    if folder.exists():
        raise RuntimeError("Paid trial exists: inspect it; no implicit reruns")
    cp = root / "checkpoints" / (f"{prefix}-{scene}" + run.get("checkpoint_suffix", ""))
    checkpoint_record = json.loads((cp / "record.json").read_text())
    if checkpoint_record["status"] != "checkpoint_ready" or not checkpoint_record["preflight_unchanged"]:
        raise RuntimeError("Unusable checkpoint; preserve it and amend explicitly")
    s = spec(scene, dev)
    record = start_record(folder, run, kind=prefix, scene=scene, event=event, policy=policy,
                          checkpoint_sha256=sha(cp / "messages.json"))
    started = time.monotonic()
    messages = []
    try:
        agent = new_agent(run, folder, f"{prefix}/{label}")
        async with Server(folder) as server:
            await host_step(server, initial_code(s), "initialization")
            # Replay genuine preflight run_step calls to reproduce the checkpoint namespace.
            # Queries/rendering need not be rerun to reconstruct bindings; no model calls here.
            for e in json.loads((cp / "tool-events.json").read_text()):
                if e["actor"] == "agent" and e["name"] in {"run_step", "undo", "reset"}:
                    await server.call(e["name"], e["args"], "checkpoint_replay")
            if event != "E0":
                await host_step(server, initial_code(s, event), "external_editor")
            # Audit current geometry before any resumed action; never fed to the agent.
            await server.call("export_step", {"output_dir": str(folder / "before")}, "audit")
            import cadquery as cq
            actual = cq.importers.importStep(str(folder / "before/tmp.step")).val()
            record["current_state_error_mm3"] = geometry_error(actual, compound(shapes(s, event)))
            if record["current_state_error_mm3"] > 1e-5:
                raise RuntimeError("Current-state initialization mismatch")
            if policy == "N":
                messages = copy.deepcopy(json.loads((cp / "messages.json").read_text()))
            else:
                messages = agent.create_messages([upstream_instruction(), task(s)], sys=agent.config["system_prompt"])
            notice = ("Session resumed. No external change; current scene_version=0." if event == "E0" else
                      "Session resumed after an external CAD edit committed. Current scene_version=1.")
            messages.append({"role": "user", "content": notice + " Continue and complete the original edit now. "
                             "All tools access the current scene and the original requirements remain active."})
            if policy == "A":
                prefetch = []
                for name, args in [("inspect", {}), ("list_faces", {"limit": 200}),
                                   ("list_edges", {"limit": 200}), ("run_step", {"code": refresh_code(s)}),
                                   ("render_views", {"views": ["toprightiso", "top"]})]:
                    result = await server.call(name, args, "prefetch")
                    prefetch.append(name + ": " + agent.tool_result_text(result))
                    images = agent.tool_result_images(result)
                    if images:
                        messages.append(agent.create_image_user_message(images))
                messages.append({"role": "user", "content": "Current-state refresh (tool outputs):\n" + "\n".join(prefetch)})
            dump(folder / "resume-input.json", messages)
            record["status"] = await loop(agent, server, messages, folder, run["resume_max_calls"])
            await server.call("export_step", {"output_dir": str(folder)}, "audit")
            record["score"] = score(folder / "tmp.step", s, event, record["status"])
    except Exception as exc:
        record.update(status="exception", error_type=type(exc).__name__, error=str(exc))
        (folder / "exception.log").write_text(traceback.format_exc())
    finally:
        record["elapsed_s"] = time.monotonic() - started
        dump(folder / "messages.json", messages)
        dump(folder / "record.json", record)
        print(json.dumps(dict(trial=label, **record), ensure_ascii=False), flush=True)


def init(args):
    root = args.run.resolve()
    root.mkdir(parents=True, exist_ok=False)
    pricing = json.loads((AGENT / ".local/pricing.json").read_text())
    cells = [(s, e, p) for s in ("pin", "slider") for e in EVENTS for p in ("N", "F", "A")]
    random.Random(20261026).shuffle(cells)
    run = dict(experiment_id="EXP-002", run_root=str(root), created_at=datetime.now(timezone.utc).isoformat(),
               budget_cny=100, model=pricing["model"], pricing=pricing, max_tokens=4096,
               checkpoint_max_calls=8, resume_max_calls=12, seed=20261026, checkpoint_suffix="-v4",
               source_commit=git("rev-parse", "HEAD"), upstream_commit=git("-C", str(UPSTREAM), "rev-parse", "HEAD"),
               source_hashes={p.name: sha(p) for p in (HERE / "pilot.py", HERE / "scenes.py")},
               lock_sha256=sha(AGENT / "uv.lock"), code_dirty=bool(git("status", "--porcelain")),
               protocol="v4-adapted-MCP-frozen-checkpoints", primary_cells=cells,
               development_cells=[("pin", "E0", "N"), ("pin", "E3", "F"),
                                  ("slider", "E1", "F"), ("slider", "E4", "N")],
               limitations=["One harness adaptation and one API model alias; exact weight revision unknown",
                            "Synthetic short frozen checkpoints, not a natural long-horizon chain",
                            "A refreshes a declared query family, not every possible B-rep property",
                            "CNY accounting uses user-supplied 2026-10-25 tariff, not a final provider invoice"])
    dump(root / "run.json", run)
    (root / "source.patch").write_text(git("diff", "HEAD"))
    for p in (HERE / "pilot.py", HERE / "scenes.py"):
        (root / p.name).write_bytes(p.read_bytes())
    Budget(root / "budget.json", 100, pricing).summary()
    print(root)


async def execute(args):
    root = args.run.resolve()
    run = json.loads((root / "run.json").read_text())
    if args.command == "checkpoint":
        await checkpoint(root, run, args.scene, args.dev)
    elif args.command == "trial":
        await trial(root, run, (args.scene, args.event, args.policy), args.dev)
    elif args.command == "batch":
        if not (root / "freeze.json").exists():
            raise RuntimeError("Freeze the reviewed protocol before the primary batch")
        frozen = json.loads((root / "freeze.json").read_text())
        if frozen["run_sha256"] != sha(root / "run.json") or any(
                sha(HERE / name) != value for name, value in frozen["source_hashes"].items()):
            raise RuntimeError("Frozen protocol/code changed; preserve and amend explicitly")
        for cell in run["primary_cells"]:
            folder = root / "primary" / "-".join(cell)
            if folder.exists():
                # Existing attempts are never repeated, whether successful or failed.
                continue
            await trial(root, run, cell, False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "checkpoint", "trial", "batch"])
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--scene", choices=["pin", "slider"])
    parser.add_argument("--event", choices=EVENTS)
    parser.add_argument("--policy", choices=["N", "F", "A"])
    parser.add_argument("--dev", action="store_true")
    args = parser.parse_args()
    if args.command in {"checkpoint", "trial"} and not args.scene:
        parser.error("--scene required")
    if args.command == "trial" and (not args.event or not args.policy):
        parser.error("--event and --policy required")
    if args.command == "init":
        init(args)
    else:
        asyncio.run(execute(args))


if __name__ == "__main__":
    main()
