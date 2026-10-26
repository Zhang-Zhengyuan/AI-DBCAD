"""Summarize every allocated trial; manual public-trace labels stay explicit."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys

from scenes import score, spec


def analyze(root):
    run = json.loads((root / "run.json").read_text())
    ledger = json.loads((root / "budget.json").read_text())
    annotations_path = root / "annotations.json"
    annotations = json.loads(annotations_path.read_text()) if annotations_path.exists() else {}
    rows = []
    for scene, event, policy in run["primary_cells"]:
        label = f"{scene}-{event}-{policy}"
        folder = root / "primary" / label
        record = json.loads((folder / "record.json").read_text()) if (folder / "record.json").exists() else {}
        calls = [c for c in ledger["calls"] if c["label"] == f"primary/{label}"]
        tools = json.loads((folder / "tool-events.json").read_text()) if (folder / "tool-events.json").exists() else []
        a = annotations.get(label, {})
        sc = record.get("score", {})
        # Audit every exported intermediate shape. Do not infer trajectory safety from the final STEP.
        unsafe_boundaries, intermediate_other = [], []
        for path in sorted((folder / "states").glob("*/tmp.step")):
            q = score(path, spec(scene), event, "finished")
            if q["unsafe_placement"]:
                unsafe_boundaries.append(path.parent.name)
            if q["final_position"] == "other":
                intermediate_other.append(path.parent.name)
        row = dict(id=label, scene=scene, event=event, policy=policy, status=record.get("status", "not_run"),
                   feasible=sc.get("truth", {}).get("feasible", event in {"E0", "E1"}),
                   geometry_success=sc.get("geometry_success", False), full_success=a.get("full_success", "unknown"),
                   final_position=sc.get("final_position", "unknown"), unsafe_final=sc.get("unsafe_placement", False),
                   unsafe_boundary_count=len(unsafe_boundaries), intermediate_other_count=len(intermediate_other),
                   stale_evidence_error=a.get("stale_evidence_error", "unknown"),
                   fresh_check=a.get("fresh_check", "unknown"), public_old_value_reused=a.get("public_old_value_reused", "unknown"),
                   calls=len(calls), prompt_tokens=sum(c.get("usage", {}).get("prompt_tokens", 0) for c in calls),
                   completion_tokens=sum(c.get("usage", {}).get("completion_tokens", 0) for c in calls),
                   accounted_cny=sum(c["charged_or_reserved_cny"] for c in calls),
                   elapsed_s=record.get("elapsed_s", 0),
                   agent_tools=sum(t["actor"] == "agent" for t in tools),
                   prefetch_tools=sum(t["actor"] == "prefetch" for t in tools),
                   tool_errors=sum(t["actor"] == "agent" and ('"ok": false' in t["text"] or t["is_error"]) for t in tools),
                   agent_tool_s=sum(t["elapsed_s"] for t in tools if t["actor"] == "agent"),
                   prefetch_s=sum(t["elapsed_s"] for t in tools if t["actor"] == "prefetch"),
                   audit_tool_s=sum(t["elapsed_s"] for t in tools if "audit" in t["actor"]),
                   trace_sha256=hashlib.sha256((folder / "messages.json").read_bytes()).hexdigest() if (folder / "messages.json").exists() else "missing")
        rows.append(row)
    result = dict(experiment_id="EXP-002", run_id=root.name, assigned=len(rows),
                  attempted=sum(r["status"] != "not_run" for r in rows),
                  primary_status=dict(Counter(r["status"] for r in rows)),
                  budget=dict(limit_cny=ledger["limit_cny"], calls=len(ledger["calls"]),
                              accounted_cny=sum(c["charged_or_reserved_cny"] for c in ledger["calls"]),
                              completed_estimate_cny=sum(c.get("estimated_cny", 0) for c in ledger["calls"]),
                              unknown_calls=sum(c["status"] != "completed" for c in ledger["calls"])),
                  by_policy={})
    for policy in ("N", "F", "A"):
        subset = [r for r in rows if r["policy"] == policy]
        result["by_policy"][policy] = dict(assigned=len(subset),
            geometry_success=sum(r["geometry_success"] for r in subset),
            full_success=sum(r["full_success"] is True for r in subset),
            unknown_labels=sum(r["full_success"] == "unknown" for r in subset),
            unsafe_final=sum(r["unsafe_final"] for r in subset),
            trials_with_unsafe_boundary=sum(r["unsafe_boundary_count"] > 0 for r in subset),
            stale_evidence_errors=sum(r["stale_evidence_error"] is True for r in subset),
            calls=sum(r["calls"] for r in subset),
            prompt_tokens=sum(r["prompt_tokens"] for r in subset),
            completion_tokens=sum(r["completion_tokens"] for r in subset),
            accounted_cny=sum(r["accounted_cny"] for r in subset),
            agent_tools=sum(r["agent_tools"] for r in subset),
            prefetch_tools=sum(r["prefetch_tools"] for r in subset),
            mean_elapsed_s=sum(r["elapsed_s"] for r in subset)/len(subset),
            prefetch_s=sum(r["prefetch_s"] for r in subset))
    (root / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    with (root / "trials.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", type=Path, required=True)
    analyze(p.parse_args().run.resolve())
