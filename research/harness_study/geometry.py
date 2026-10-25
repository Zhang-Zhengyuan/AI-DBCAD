"""Independent STEP validation and fixed-view rendering in an isolated process."""

import argparse
import json
from pathlib import Path
import cadquery as cq
from src.utils.cadquery_rendering import VIEW_PROJECTIONS, render_to_png


def inspect_step(source, folder):
    folder.mkdir(parents=True, exist_ok=True)
    model = cq.importers.importStep(str(source))
    shape = cq.Compound.makeCompound(model.vals())
    box = shape.BoundingBox()
    solids = shape.Solids()
    faces = shape.Faces()
    result = {
        "brep_valid": shape.isValid(),
        "solids": len(solids),
        "faces": len(faces),
        "edges": len(shape.Edges()),
        "volume": sum(s.Volume() for s in solids),
        "solid_volumes": [s.Volume() for s in solids],
        "bbox_min": [box.xmin, box.ymin, box.zmin],
        "bbox_max": [box.xmax, box.ymax, box.zmax],
        "bbox_size": [box.xlen, box.ylen, box.zlen],
        "uncontained_faces": len(faces) - sum(len(s.Faces()) for s in solids),
    }
    result["strict_solid"] = bool(
        result["brep_valid"]
        and solids
        and all(s.isValid() and s.Volume() > 1e-9 for s in solids)
        and result["uncontained_faces"] == 0
    )
    result["face_signatures"] = sorted(
        [
            [
                f.geomType(),
                f.Area(),
                *f.Center().toTuple(),
                f.BoundingBox().xmin,
                f.BoundingBox().ymin,
                f.BoundingBox().zmin,
                f.BoundingBox().xmax,
                f.BoundingBox().ymax,
                f.BoundingBox().zmax,
            ]
            for f in faces
        ]
    )
    cq.exporters.export(shape, str(folder / "mesh.stl"))
    (folder / "geometry.json").write_text(json.dumps(result, indent=2))
    for name, projection in VIEW_PROJECTIONS.items():
        render_to_png(shape, str(folder / (name + ".png")), projection, 512, 512)
    (folder / "render.done").touch()
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("source", type=Path)
    p.add_argument("folder", type=Path)
    a = p.parse_args()
    inspect_step(a.source, a.folder)
