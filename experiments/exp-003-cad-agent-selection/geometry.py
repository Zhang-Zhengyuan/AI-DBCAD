"""Offline-only exact geometry oracle; no model self-ratings or GT feedback."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path


def shape_of(value):
    import cadquery as cq
    if isinstance(value, cq.Assembly):
        return value.toCompound()
    if isinstance(value, cq.Workplane):
        vals = value.vals()
        shapes = [v for v in vals if isinstance(v, cq.Shape)]
        if not shapes:
            raise ValueError("No shape in Workplane")
        return shapes[0] if len(shapes) == 1 else cq.Compound.makeCompound(shapes)
    if isinstance(value, cq.Shape):
        return value
    raise TypeError(f"Unsupported geometry type {type(value).__name__}")


def trusted_shape(code):
    """Only repository-authored reference/manual programs may call this helper."""
    namespace = {}
    exec(compile(code, "<exp003-trusted-reference>", "exec"), namespace, namespace)
    return shape_of(namespace["r"])


def load_step(path):
    import cadquery as cq
    return shape_of(cq.importers.importStep(str(path)))


def export_step(shape, path):
    import cadquery as cq
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(shape_of(shape), str(path))


def bounds(shape):
    b = shape.BoundingBox()
    return [b.xmin, b.ymin, b.zmin, b.xmax, b.ymax, b.zmax]


def diagnostics(shape):
    solids = list(shape.Solids())
    solid_faces = {face.hashCode() for solid in solids for face in solid.Faces()}
    loose_faces = [face for face in shape.Faces() if face.hashCode() not in solid_faces]
    return {
        "brep_valid": bool(shape.isValid()), "solid_count": len(solids),
        "all_solids_valid": all(s.isValid() for s in solids),
        "positive_solids": all(math.isfinite(s.Volume()) and s.Volume() > 0 for s in solids),
        "loose_face_count": len(loose_faces), "volume_mm3": float(shape.Volume()),
        "bbox_mm": bounds(shape), "face_count": len(shape.Faces()), "edge_count": len(shape.Edges()),
    }


def symmetric_difference(a, b):
    # Volume inclusion-exclusion avoids an OCCT CUT failure on coincident swept
    # inner faces: CUT can return the whole original solid even while COMMON
    # correctly returns the full intersection. This identity is independently
    # checked on equal, disjoint and intentionally wrong shapes by the tests.
    overlap = float(a.intersect(b).Volume())
    if not math.isfinite(overlap):
        raise ValueError("Non-finite intersection volume")
    return max(0.0, float(a.Volume()) + float(b.Volume()) - 2 * overlap)


def score(actual_path, reference_code, input_path=None, tolerance=None):
    """Score a final STEP without making any value available to the generator."""
    tolerance = tolerance or {"bbox_mm": 0.02, "volume_relative": 1e-5, "volume_absolute_mm3": 1e-3}
    path = Path(actual_path)
    if not path.is_file():
        return {"geometry_success": False, "reason": "missing_output", "output_present": False}
    try:
        actual, expected = load_step(path), trusted_shape(reference_code)
        info, expected_info = diagnostics(actual), diagnostics(expected)
        delta = symmetric_difference(actual, expected)
        limit = max(tolerance["volume_absolute_mm3"], abs(expected.Volume()) * tolerance["volume_relative"])
        bbox_error = max(abs(a - b) for a, b in zip(bounds(actual), bounds(expected)))
        valid = (info["brep_valid"] and info["solid_count"] > 0 and info["all_solids_valid"]
                 and info["positive_solids"] and info["loose_face_count"] == 0)
        preserved_error = None
        if input_path and Path(input_path).is_file():
            # Material shared by source and independently specified target must remain.
            protected = load_step(input_path).intersect(expected)
            preserved_error = max(0.0, float(protected.Volume()) - float(protected.intersect(actual).Volume()))
        return {
            "geometry_success": bool(valid and delta <= limit and bbox_error <= tolerance["bbox_mm"]
                                     and info["solid_count"] == expected_info["solid_count"]),
            "output_present": True, "actual": info, "expected": expected_info,
            "symmetric_difference_mm3": delta, "volume_tolerance_mm3": limit,
            "bbox_max_error_mm": bbox_error, "preserved_material_missing_mm3": preserved_error,
            "unrequested_material_mm3": max(0.0, float(actual.Volume()) - float(actual.intersect(expected).Volume())),
            "output_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    except Exception as error:
        return {"geometry_success": False, "output_present": True, "reason": "oracle_or_import_error",
                "error_type": type(error).__name__, "error": str(error)[:1000]}
