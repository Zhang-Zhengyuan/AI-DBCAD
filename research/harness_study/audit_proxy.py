"""Post-hoc audit of every VLM-positive candidate; no generation or regrading."""

import json
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent


def audit(run, episodes):
    records = []
    for row in episodes:
        if row["proxy"] is not True:
            continue
        folder = run / row["request"] / "evaluation"
        geometries = {
            k: json.loads((folder / k / "geometry.json").read_text())
            for k in ["input", row["arm"], "reference"]
        }
        before = np.array(geometries["input"]["bbox_size"])
        candidate = np.array(geometries[row["arm"]]["bbox_size"])
        reference = np.array(geometries["reference"]["bbox_size"])
        # A flag for review, not a universal acceptance rule. The reference
        # confirms that these requests preserved the stock's outer dimensions.
        reference_preserves = bool(np.allclose(before, reference, rtol=1e-6, atol=1e-6))
        error = float(np.max(np.abs(candidate - before) / np.maximum(before, 1e-8)))
        records.append(
            {
                "request": row["request"],
                "arm": row["arm"],
                "proxy_scores": [row["instruction"], row["quality"]],
                "input_bbox_size": before.tolist(),
                "candidate_bbox_size": candidate.tolist(),
                "reference_bbox_size": reference.tolist(),
                "reference_preserves_outer_extents": reference_preserves,
                "maximum_relative_axis_extent_change": error,
                "large_unrequested_extent_change_flag": reference_preserves
                and error > 0.1,
                "interpretation": "Post-hoc review flag; not an expert acceptance label. No claim that an unflagged candidate is correct.",
            }
        )
    return {
        "reviewed": "Every proxy-positive candidate",
        "n": len(records),
        "large_extent_change_flags": sum(
            x["large_unrequested_extent_change_flag"] for x in records
        ),
        "cases": records,
        "method": "Compare exact B-Rep bounding-box extents; reference used only after inference, not exposed to any editor or primary judge.",
    }
