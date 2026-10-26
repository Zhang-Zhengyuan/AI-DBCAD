"""Aggregate frozen attempts; never select a best outcome or repeat an API call."""

# ruff: noqa: E402 -- bootstrap paths for pinned, separately cloned research code
from __future__ import annotations
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from experiment import current, ARMS
from audit_proxy import audit

LABELS = {
    "neuralcad": "neuralCAD script + input fix",
    "agentic": "AgenticCADedit",
    "itercad": "IterCAD-STEP adapter",
}
COLORS = {"neuralcad": "#7c8795", "agentic": "#365e9d", "itercad": "#37887d"}


def wilson(k, n):
    if not n:
        return [0.0, 1.0]
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [float(max(0, c - h)), float(min(1, c + h))]


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def aggregate(run, allow_incomplete=False):
    plan = read_json(run / "plan.json")
    rows = []
    inputs = []
    for task in plan["tasks"]:
        rid = task["_id"]
        control = read_json(run / rid / "evaluation/input/metrics.json", {})
        inputs.append({"request": rid, **control})
        for arm in ARMS:
            result = read_json(run / rid / arm / "result.json")
            metric = read_json(run / rid / "evaluation" / arm / "metrics.json")
            rating = read_json(run / rid / arm / "judge/rating.json")
            if not allow_incomplete and (
                result is None or metric is None or rating is None
            ):
                raise RuntimeError(f"Incomplete episode: {rid}/{arm}")
            if result is None:
                continue
            metric = metric or {}
            rating = rating or {}
            responses = [
                read_json(p)
                for p in sorted((run / rid / arm / "api").glob("*-response.json"))
            ]
            prompt = sum(
                (x.get("usage") or {}).get("prompt_tokens", 0) for x in responses
            )
            completion = sum(
                (x.get("usage") or {}).get("completion_tokens", 0) for x in responses
            )
            error_text = (
                (run / rid / arm / "error.log").read_text()
                if (run / rid / arm / "error.log").exists()
                else ""
            )
            infrastructure = next(
                (
                    e
                    for e in [
                        "RateLimitError",
                        "APITimeoutError",
                        "InternalServerError",
                        "APIConnectionError",
                    ]
                    if e in error_text
                ),
                None,
            )
            rows.append(
                {
                    "request": rid,
                    "arm": arm,
                    "modality": task["modality"],
                    "difficulty": task["difficulty"],
                    "assembly": task["assembly"],
                    "parametric": task["parametric"],
                    "status": result["status"],
                    "infrastructure_error": infrastructure,
                    "calls": result.get("calls", len(responses)),
                    "seconds": result.get("seconds"),
                    "confirmed_cost_cny": (prompt * 3 + completion * 18) / 1e6,
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "step_exists": metric.get("step_exists", False),
                    "brep_valid": metric.get("brep_valid", False),
                    "strict_solid": metric.get("strict_solid", False),
                    "solids": metric.get("solids"),
                    "cd": metric.get("cd"),
                    "normalized_cd": metric.get("normalized_cd"),
                    "surface_iou": metric.get("surface_iou"),
                    "dino": metric.get("dino"),
                    "suspected_no_op": metric.get("suspected_no_op", False),
                    "instruction": rating.get("instruction"),
                    "quality": rating.get("quality"),
                    "proxy": rating.get("accepted_proxy"),
                    "judge_rated": rating.get("rated", False),
                    "geometry_error": metric.get("geometry_error"),
                }
            )
    by_arm = {}
    for arm in ARMS:
        items = [r for r in rows if r["arm"] == arm]
        n = len(items)
        k = sum(r["proxy"] is True for r in items)
        known = sum(r["proxy"] is not None for r in items)
        valid = [r for r in items if r["strict_solid"]]

        def average(key, subset):
            values = [r[key] for r in subset if r.get(key) is not None]
            return float(np.mean(values)) if values else None

        by_arm[arm] = {
            "label": LABELS[arm],
            "n": n,
            "exported": sum(r["step_exists"] for r in items),
            "strict_solids": len(valid),
            "accepted_proxy": k,
            "proxy_unknown": n - known,
            "proxy_rate_lower_bound": k / n if n else None,
            "proxy_wilson_95": wilson(k, n),
            "proxy_range_including_unknown": [k / n, (k + n - known) / n]
            if n
            else None,
            "status_counts": dict(Counter(r["status"] for r in items)),
            "infrastructure_errors": dict(
                Counter(
                    r["infrastructure_error"]
                    for r in items
                    if r["infrastructure_error"]
                )
            ),
            "suspected_no_ops": sum(r["suspected_no_op"] for r in items),
            "confirmed_cost_cny": sum(r["confirmed_cost_cny"] for r in items),
            "calls": sum(r["calls"] for r in items),
            "mean_calls": average("calls", items),
            "median_seconds": float(
                np.median([r["seconds"] for r in items if r["seconds"] is not None])
            )
            if n
            else None,
            "median_cd_readable_only": float(
                np.median([r["cd"] for r in items if r["cd"] is not None])
            )
            if any(r["cd"] is not None for r in items)
            else None,
            "mean_dino_readable_only": average("dino", items),
            "mean_surface_iou_readable_only": average("surface_iou", items),
            "coverage_weighted_dino": sum(r["dino"] or 0 for r in items) / n
            if n
            else None,
            "solid_gated_ncd_auc_0_to_point1": sum(
                max(0, 1 - (r["normalized_cd"] or 0) / 0.1)
                if r["strict_solid"] and r["normalized_cd"] is not None
                else 0
                for r in items
            )
            / n
            if n
            else None,
        }
    # Paired bootstrap resamples task IDs, preserving all arms for each task.
    rng = np.random.default_rng(20261027)
    paired = []
    if len(rows) == len(plan["tasks"]) * 3:
        table = {(r["request"], r["arm"]): r for r in rows}
        n = len(plan["tasks"])
        indices = rng.integers(0, n, size=(20000, n))
        complete_ids = [
            t["_id"]
            for t in plan["tasks"]
            if not any(table[(t["_id"], a)]["infrastructure_error"] for a in ARMS)
        ]
        for ai, a in enumerate(ARMS):
            for b in ARMS[ai + 1 :]:
                delta = np.array(
                    [
                        float(table[(t["_id"], a)]["proxy"] is True)
                        - float(table[(t["_id"], b)]["proxy"] is True)
                        for t in plan["tasks"]
                    ]
                )
                boots = delta[indices].mean(axis=1)
                from scipy.stats import binomtest

                wins = int((delta > 0).sum())
                losses = int((delta < 0).sum())
                exact_p = (
                    float(binomtest(wins, wins + losses, p=0.5).pvalue)
                    if wins + losses
                    else 1.0
                )
                paired.append(
                    {
                        "a": a,
                        "b": b,
                        "proxy_rate_difference": float(delta.mean()),
                        "paired_bootstrap95": np.quantile(
                            boots, [0.025, 0.975]
                        ).tolist(),
                        "mcnemar_exact_two_sided_p": exact_p,
                        "discordant_tasks": wins + losses,
                        "infrastructure_clean_common_n": len(complete_ids),
                        "clean_common_proxy_difference": float(
                            np.mean(
                                [
                                    float(table[(rid, a)]["proxy"] is True)
                                    - float(table[(rid, b)]["proxy"] is True)
                                    for rid in complete_ids
                                ]
                            )
                        )
                        if complete_ids
                        else None,
                    }
                )
    ledger = read_json(HERE / ".local/budget.json")
    cost = {
        "limit_cny": ledger["limit_cny"],
        "confirmed_cny": sum(c.get("estimated_cny", 0) for c in ledger["calls"]),
        "conservative_charged_or_reserved_cny": sum(
            c["charged_or_reserved_cny"] for c in ledger["calls"]
        ),
        "requests": len(ledger["calls"]),
        "pricing": ledger["pricing"],
        "status_counts": dict(Counter(c["status"] for c in ledger["calls"])),
    }
    stages = Counter()
    for c in ledger["calls"]:
        stage = (
            "native"
            if c["label"].startswith("native-ai/")
            else "observation"
            if c["label"].startswith("observation/")
            else "judge"
            if c["label"].startswith("judge/")
            else "generation"
        )
        stages[stage] += c.get("estimated_cny", 0)
    cost["confirmed_by_stage"] = dict(stages)
    probe_root = HERE / ".local/observation-probe"
    cases = read_json(probe_root / "cases.json", [])
    ratings = {}
    for c in cases:
        for channel in ["autofit", "locked", "numeric"]:
            value = read_json(probe_root / c["id"] / channel / "rating.json")
            if value:
                ratings[(c["id"], channel)] = value
    common = [
        c["id"]
        for c in cases
        if all((c["id"], ch) in ratings for ch in ["autofit", "locked", "numeric"])
    ]
    probe = {
        "planned": 36,
        "observed": len(ratings),
        "complete_case_pairs": len(common),
        "channels": {},
        "cases": cases,
    }
    for channel in ["autofit", "locked", "numeric"]:
        subset = [v for (rid, ch), v in ratings.items() if ch == channel]
        probe["channels"][channel] = {
            "rated": len(subset),
            "correct": sum(v["correct"] for v in subset),
            "abstained": sum(v.get("satisfied") is None for v in subset),
            "complete_case_correct": sum(
                ratings[(rid, channel)]["correct"] for rid in common
            ),
            "unknown_transport": len(cases) - len(subset),
        }
    return {
        "run": run.name,
        "implementation_sha256": {
            str(path.relative_to(HERE.parents[1])): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in [
                HERE / "experiment.py",
                HERE / "evaluate.py",
                HERE / "geometry.py",
                HERE / "observation_probe.py",
                HERE / "audit_proxy.py",
                HERE / "summarize.py",
                HERE.parent / "agenticcad_edit/uv.lock",
                HERE.parents[1] / "systems/dbcad/CAD_DB/backend/app/ai_budget.py",
            ]
        },
        "plan": plan,
        "episodes": rows,
        "proxy_audit": audit(run, rows),
        "input_controls": inputs,
        "arms": by_arm,
        "paired": paired,
        "budget": cost,
        "observation": probe,
    }


def figures(data, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 12,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "savefig.dpi": 180,
        }
    )
    names = ["neuralCAD\nscript + input fix", "AgenticCADedit", "IterCAD\nSTEP adapter"]
    x = np.arange(3)
    colors = [COLORS[a] for a in ARMS]
    fig, axes = plt.subplots(2, 2, figsize=(10, 8), layout="constrained")
    axes = axes.flatten()
    for i, a in enumerate(ARMS):
        s = data["arms"][a]
        n = s["n"]
        rate = s["accepted_proxy"] / n
        ci = s["proxy_wilson_95"]
        axes[0].bar(
            i - 0.17,
            100 * s["strict_solids"] / n,
            width=0.3,
            color=colors[i],
            alpha=0.35,
            label="Usable solid" if i == 0 else None,
        )
        axes[0].bar(
            i + 0.17,
            100 * rate,
            width=0.3,
            color=colors[i],
            label="VLM proxy accepted" if i == 0 else None,
        )
        axes[0].errorbar(
            i + 0.17,
            rate * 100,
            yerr=[[100 * (rate - ci[0])], [100 * (ci[1] - rate)]],
            color="#293441",
            capsize=4,
        )
        axes[0].text(
            i + 0.17,
            rate * 100 + 4,
            f"{s['accepted_proxy']}/{n}",
            ha="center",
            fontsize=11,
        )
        axes[1].bar(i, s["coverage_weighted_dino"], color=colors[i])
        axes[1].text(
            i,
            s["coverage_weighted_dino"] + 0.025,
            f"{s['coverage_weighted_dino']:.3f}",
            ha="center",
        )
        axes[3].bar(i, s["mean_calls"], color=colors[i])
        axes[3].text(i, s["mean_calls"] + 0.08, f"{s['mean_calls']:.2f}", ha="center")
        axes[2].bar(i, s["confirmed_cost_cny"], color=colors[i])
        axes[2].text(
            i,
            s["confirmed_cost_cny"] + 0.12,
            f"{s['confirmed_cost_cny']:.2f}",
            ha="center",
        )
    for ax in axes:
        ax.set_xticks(x, names)
        ax.set_xlabel("Harness")
        ax.grid(axis="y", alpha=0.15)
        ax.set_axisbelow(True)
    axes[0].set(
        title="Usable solids and VLM proxy\n(proxy has audit violations)",
        ylabel="Task rate (%)",
        ylim=(0, 110),
    )
    axes[0].legend(fontsize=10.5)
    axes[1].set(
        title="Coverage-weighted DINO similarity",
        ylabel="Mean cosine; missing outputs = 0",
        ylim=(0, 1.08),
    )
    axes[2].set(title="Generation cost (reported usage)", ylabel="RMB (CNY)")
    axes[3].set(
        title="Mean model calls / reservations", ylabel="Calls per task", ylim=(0, 7)
    )
    fig.suptitle("neuralCAD-Edit · Qwen3.6-27B · 24 paired tasks", fontsize=15)
    fig.supxlabel(
        "2026-10-25 · six-call cap · Wilson intervals on VLM proxy\nTwo of three passes have extent violations. Cost excludes uncertain charges.",
        fontsize=10.5,
    )
    fig.savefig(output / "comparison.png")
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(8, 5), layout="constrained")
    thresholds = np.geomspace(1e-4, 1, 250)
    for arm in ARMS:
        rows = [r for r in data["episodes"] if r["arm"] == arm]
        values = np.array(
            [
                r["normalized_cd"]
                if r["strict_solid"] and r["normalized_cd"] is not None
                else np.inf
                for r in rows
            ]
        )
        ax.plot(
            thresholds,
            [(values <= t).mean() * 100 for t in thresholds],
            label=LABELS[arm],
            color=COLORS[arm],
            lw=2,
        )
    control = np.array([r.get("normalized_cd", np.inf) for r in data["input_controls"]])
    ax.plot(
        thresholds,
        [(control <= t).mean() * 100 for t in thresholds],
        label="Unedited input control",
        color="#3d4145",
        ls="--",
    )
    ax.set(
        xscale="log",
        xlabel="Surface CD / reference bounding-box diagonal (dimensionless)",
        ylabel="Fraction of all tasks within tolerance (%)",
        title="Solid-gated tolerance recall; no alignment",
        ylim=(0, 105),
    )
    ax.grid(alpha=0.2)
    ax.legend(fontsize=10.5)
    fig.supxlabel(
        "Failures count as misses. Similarity can reward an unchanged input; this is not an edit-completion metric.",
        fontsize=9,
    )
    fig.savefig(output / "tolerance-recall.png")
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(10, 5.3), layout="constrained")
    probe = data["observation"]
    channels = ["autofit", "locked", "numeric"]
    labels = [
        "Auto-fit",
        "Fixed\ncamera",
        "Auto-fit +\nmeasurements",
    ]
    n = probe["complete_case_pairs"]
    vals = [
        probe["channels"][ch]["complete_case_correct"] / n * 100 if n else 0
        for ch in channels
    ]
    axes[0].bar(np.arange(3), vals, color=["#8994a1", "#365e9d", "#37887d"])
    axes[0].set(
        xticks=np.arange(3),
        xticklabels=labels,
        ylabel="Correct verification decisions (%)",
        ylim=(0, 115),
        title=f"Verification accuracy\n{n} complete paired cases",
    )
    for i, ch in enumerate(channels):
        axes[0].text(
            i,
            vals[i] + 3,
            f"{probe['channels'][ch]['complete_case_correct']}/{n}",
            ha="center",
        )
    correct = [c for c in probe["cases"] if c["correct"]]
    av = [
        c["pixel_differences"]["autofit"]["pixel_mean_absolute_difference_0_255"]
        for c in correct
    ]
    lv = [
        c["pixel_differences"]["locked"]["pixel_mean_absolute_difference_0_255"]
        for c in correct
    ]
    for i, (a, b) in enumerate(zip(av, lv)):
        axes[1].plot([0, 1], [a, b], color="#abb5bf", lw=1)
        axes[1].scatter([0, 1], [a, b], color=["#8994a1", "#365e9d"], s=25)
    axes[1].set(
        xticks=[0, 1],
        xticklabels=["Auto-fit", "Fixed camera"],
        ylabel="Mean pixel difference (0–255)",
        title="Visible before/after change\nCorrect scale / translation edits",
    )
    fig.suptitle("Observation quality is part of the editing loop", fontsize=15)
    fig.supxlabel(
        "Synthetic controlled diagnostic, Qwen3.6-27B. Paired transport-complete subset; no end-to-end editor improvement claimed.",
        fontsize=9,
    )
    fig.savefig(output / "observation-probe.png")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    run = args.run or current()
    data = aggregate(run, args.allow_incomplete)
    output = HERE / "output"
    output.mkdir(exist_ok=True)
    (output / "results.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    )
    if data["episodes"]:
        with (output / "episodes.csv").open("w") as f:
            writer = csv.DictWriter(f, fieldnames=list(data["episodes"][0]))
            writer.writeheader()
            writer.writerows(data["episodes"])
    figures(data, output)
    print(
        json.dumps(
            {
                "run": data["run"],
                "episodes": len(data["episodes"]),
                "arms": data["arms"],
                "budget": data["budget"],
                "observation": {
                    k: v for k, v in data["observation"].items() if k != "cases"
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
