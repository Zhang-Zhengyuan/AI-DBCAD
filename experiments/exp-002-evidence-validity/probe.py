"""Post-hoc two-arm diagnostic; never mix these attempts into primary rates.

The chosen case is the first primary N case with a public, demonstrably false
old-clearance claim. Hold complete history and a generic reminder constant;
add either a version echo or a genuine current relevant geometry measurement.
One attempt per arm, no retries. Not a statistically powered causal estimate.
"""
import argparse
import asyncio
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import traceback

from pilot import Server, dump, host_step, loop, new_agent, sha, start_record
from scenes import initial_code, refresh_code, score, spec


async def probe(root, arm):
    run = json.loads((root / "run.json").read_text())
    folder = root / "diagnostic" / arm
    record = start_record(folder, run, kind="posthoc_diagnostic", scene="slider", event="E3", arm=arm,
                          probe_source_sha256=sha(Path(__file__)))
    s = spec("slider")
    cp = root / "checkpoints" / ("primary-slider" + run["checkpoint_suffix"])
    started = time.monotonic()
    messages = copy.deepcopy(json.loads((cp / "messages.json").read_text()))
    try:
        agent = new_agent(run, folder, "diagnostic/" + arm)
        async with Server(folder) as server:
            await host_step(server, initial_code(s), "initialization")
            for e in json.loads((cp / "tool-events.json").read_text()):
                if e["actor"] == "agent" and e["name"] in {"run_step", "undo", "reset"}:
                    await server.call(e["name"], e["args"], "checkpoint_replay")
            await host_step(server, initial_code(s, "E3"), "external_editor")
            messages.append({"role": "user", "content":
                "Session resumed after an external CAD edit committed. Current scene_version=1. "
                "Continue and complete the original edit now. All tools access the current scene "
                "and the original requirements remain active."})
            messages.append({"role": "user", "content":
                "Check the current scene before executing any saved plan. "
                "Here is an additional live tool response; other current queries remain available."})
            code = refresh_code(s) if arm == "relevant" else "print({'scene_version': scene_version})"
            response = await server.call("run_step", {"code": code}, "prefetch")
            messages.append({"role": "user", "content": "Live run_step output:\n" + agent.tool_result_text(response)})
            dump(folder / "resume-input.json", messages)
            record["status"] = await loop(agent, server, messages, folder, run["resume_max_calls"])
            await server.call("export_step", {"output_dir": str(folder)}, "audit")
            record["score"] = score(folder / "tmp.step", s, "E3", record["status"])
    except Exception as exc:
        record.update(status="exception", error_type=type(exc).__name__, error=str(exc))
        (folder / "exception.log").write_text(traceback.format_exc())
    finally:
        record["elapsed_s"] = time.monotonic() - started
        dump(folder / "record.json", record)
        dump(folder / "messages.json", messages)
        print(json.dumps(record, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--arm", choices=["reminder", "relevant"], required=True)
    args = p.parse_args()
    asyncio.run(probe(args.run.resolve(), args.arm))
