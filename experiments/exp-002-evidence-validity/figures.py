"""Static scientific figures from frozen scene parameters and audited run metrics."""
import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle
import numpy as np

from scenes import obstacle_x, spec, truth


def figures(root):
    out = root / "figures"
    out.mkdir(exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(2, 3, figsize=(12, 6), constrained_layout=True)
    for row, name in enumerate(("pin", "slider")):
        s = spec(name)
        for col, event in enumerate(("E0", "E3", "E4")):
            ax = axes[row, col]
            target = s["target_x"]
            half = 2 if name == "pin" else 3
            ox = obstacle_x(s, event)
            if name == "pin":
                ax.add_patch(Circle((target, 0), 2, facecolor="#4179ba", alpha=.65))
                height = 8
            else:
                ax.add_patch(Rectangle((target-3, -2), 6, 4, facecolor="#4179ba", alpha=.65))
                height = 10
            ax.add_patch(Rectangle((s["old_obstacle_x"]-2, -height/2), 4, height,
                                   facecolor="none", edgecolor="#888888", linestyle="--", linewidth=1.5))
            ax.add_patch(Rectangle((ox-2, -height/2), 4, height, facecolor="#e79b41", alpha=.75))
            gt = truth(s, event)
            ax.set_title(f"{name.capitalize()} / {event}: distance {gt['ocp_distance']:g} mm\n"
                         + ("allowed" if gt["feasible"] else "blocked (requires >= 3 mm)"))
            ax.set(xlim=(-4, 18), ylim=(-6, 6), aspect="equal", xlabel="X (mm)", ylabel="Y (mm)")
            ax.grid(alpha=.15)
            ax.text(.02, .97, "blue: candidate\norange: current obstacle\ndashed: old obstacle", transform=ax.transAxes,
                    va="top", fontsize=8, bbox=dict(facecolor="white", alpha=.85, edgecolor="none"))
    fig.suptitle("Controlled geometry change: target part unchanged; old clearance can expire\n"
                 "XY projections from verified scene parameters; base/auxiliary omitted")
    fig.savefig(out / "geometry-change.png", dpi=180)
    fig.savefig(out / "geometry-change.pdf")
    plt.close(fig)
    if not (root / "trials.csv").exists():
        return
    rows = list(csv.DictReader((root / "trials.csv").open()))
    result = json.loads((root / "analysis.json").read_text())
    labels = [f"{s}-{e}" for s in ("pin", "slider") for e in ("E0", "E1", "E3", "E4")]
    fig, ax = plt.subplots(figsize=(7.4, 5.8), constrained_layout=True)
    colors = {"success": "#d8eddf", "unsafe": "#f4c5bf", "other": "#f6e4b9"}
    for y, label in enumerate(labels):
        for x, p in enumerate(("N", "F", "A")):
            r = next(r for r in rows if r["id"] == f"{label}-{p}")
            success = r["full_success"] == "True"
            unsafe = r["unsafe_final"] == "True"
            intermediate = int(r["unsafe_boundary_count"]) > 0
            value = "PASS" if success else "UNSAFE" if unsafe else "FAIL/UNKNOWN"
            if intermediate:
                value += "\nunsafe during edit"
            rect = Rectangle((x-.5, y-.5), 1, 1, facecolor=colors["success" if success else "unsafe" if unsafe else "other"],
                             edgecolor="white", linewidth=2, hatch="//" if intermediate else None)
            ax.add_patch(rect)
            ax.text(x, y, value, ha="center", va="center", fontsize=9)
    ax.set(xlim=(-.5, 2.5), ylim=(7.5, -.5), xticks=range(3), yticks=range(8))
    ax.set_xticklabels(["N: full history", "F: query on demand", "A: full query refresh"])
    ax.set_yticklabels(labels)
    ax.set_title("All 24 assigned resumed trials\nHatching marks any unsafe placement at a saved tool boundary")
    ax.tick_params(length=0)
    fig.savefig(out / "all-trials.png", dpi=180)
    fig.savefig(out / "all-trials.pdf")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    policies = ("N", "F", "A")
    y = [result["by_policy"][p]["full_success"] for p in policies]
    axes[0].bar(policies, y, color=["#4179ba", "#5b9b74", "#dcaa51"])
    axes[0].set(ylim=(0, 9), ylabel="Successful trials / 8", title="Final task success (audited)")
    for i, v in enumerate(y):
        axes[0].text(i, v+.12, f"{v}/8", ha="center")
    costs = [result["by_policy"][p]["accounted_cny"] for p in policies]
    axes[1].bar(policies, costs, color=["#4179ba", "#5b9b74", "#dcaa51"])
    axes[1].set(ylabel="CNY for 8 resumed trials", title="API fee at registered tariff\nCheckpoint/development costs reported separately")
    for i, v in enumerate(costs):
        axes[1].text(i, v+.01, f"{v:.3f}", ha="center")
    fig.savefig(out / "quality-cost.png", dpi=180)
    fig.savefig(out / "quality-cost.pdf")
    plt.close(fig)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", type=Path, required=True)
    figures(p.parse_args().run.resolve())
