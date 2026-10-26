"""Synthetic millimetre scenes and an independent final-geometry oracle."""
from __future__ import annotations

import json
import math

import cadquery as cq

EVENTS = ("E0", "E1", "E3", "E4")


def spec(scene, development=False):
    if scene not in {"pin", "slider"}:
        raise ValueError(scene)
    shift = 30 if development else 0
    return dict(scene=scene, shift=shift, initial_x=(-14 if scene == "pin" else -12) + shift,
                target_x=(0 if scene == "pin" else 4) + shift, threshold=3.0,
                old_obstacle_x=(9 if scene == "pin" else 14) + shift)


def obstacle_x(s, event):
    if event not in EVENTS:
        raise ValueError(event)
    return s["old_obstacle_x"] + ({"E3": -7, "E4": -2.5}.get(event, 0)
                                  if s["scene"] == "pin" else
                                  {"E3": -6, "E4": -2.5}.get(event, 0))


def shapes(s, event="E0", placed=False):
    x = s["target_x"] if placed else s["initial_x"]
    if s["scene"] == "pin":
        movable = cq.Workplane("XY").circle(2).extrude(4).translate((x, 0, 2)).val()
        obstacle = cq.Workplane("XY").box(4, 8, 6).translate((obstacle_x(s, event), 0, 4)).val()
    else:
        movable = cq.Workplane("XY").box(6, 4, 4).translate((x, 0, 4)).val()
        obstacle = cq.Workplane("XY").box(4, 10, 8).translate((obstacle_x(s, event), 0, 4)).val()
    fixture = cq.Workplane("XY").box(40, 30, 2).translate((s["shift"], 0, -3)).val()
    auxiliary = cq.Workplane("XY").box(2, 2, 2).translate(
        (s["shift"] - 15, -10 if event == "E1" else -12, 1)).val()
    return dict(movable=movable, obstacle=obstacle, fixture=fixture, auxiliary=auxiliary)


def compound(parts):
    return cq.Compound.makeCompound(list(parts.values()))


def initial_code(s, event="E0"):
    """All canonical geometry bindings are refreshed together after an external edit."""
    x, ox = s["initial_x"], obstacle_x(s, event)
    movable = (f'cq.Workplane("XY").circle(2).extrude(4).translate(({x},0,2))'
               if s["scene"] == "pin" else
               f'cq.Workplane("XY").box(6,4,4).translate(({x},0,4))')
    size = "4,8,6" if s["scene"] == "pin" else "4,10,8"
    return f'''movable = {movable}
obstacle = cq.Workplane("XY").box({size}).translate(({ox},0,4))
fixture = cq.Workplane("XY").box(40,30,2).translate(({s['shift']},0,-3))
auxiliary = cq.Workplane("XY").box(2,2,2).translate(({s['shift']-15},{-10 if event == 'E1' else -12},1))
parts = dict(movable=movable, obstacle=obstacle, fixture=fixture, auxiliary=auxiliary)
scene_version = {0 if event == 'E0' else 1}
result = cq.Compound.makeCompound([p.val() for p in parts.values()])
'''


def task(s):
    return f'''Edit the existing four-part scene. Units: mm. Move only `movable` by a pure X translation
so its centre X becomes {s['target_x']}; keep Y/Z, shape, and orientation unchanged.
The placement is allowed only if its minimum surface distance to `obstacle` is at least
{s['threshold']} mm AND it intersects none of the other parts. If the specified placement
is infeasible, keep the scene unchanged and clearly report that it is blocked; do not
choose another destination or modify a fixed part. A valid placement must actually be made.
The live namespace contains cq, movable, obstacle, fixture, auxiliary (CadQuery Workplanes),
parts (a dict of those names), result (the whole scene), and scene_version.
Use `.val()` for a Workplane shape and `.distance(other_shape)` for a shape distance.
For intersection use shape_a.intersect(shape_b).Volume() (zero means no overlapping volume).
There is no Shape.intersects method. A failed run_step rolls back every new variable
binding from that step; recreate any needed temporary variable after a failure.
After an edit update parts['movable'] and rebuild result from all current parts, e.g.
result = cq.Compound.makeCompound([p.val() for p in parts.values()]).
The final `result` is exported and independently graded. Current tools, including run_step,
inspect and render_views, remain available throughout. Report a short public outcome before finish().'''


def truth(s, event):
    p = shapes(s, event, placed=True)
    # Analytic distance is independent of CadQuery's distance implementation.
    half = 2 if s["scene"] == "pin" else 3
    gap = obstacle_x(s, event) - 2 - (s["target_x"] + half)
    distance = p["movable"].distance(p["obstacle"])
    overlap = p["movable"].intersect(p["obstacle"]).Volume()
    assert math.isclose(distance, max(0, gap), abs_tol=1e-7), (s, event, gap, distance)
    return dict(analytic_signed_gap=gap, ocp_distance=distance, overlap_volume=overlap,
                feasible=distance >= s["threshold"] - 1e-7 and overlap < 1e-7,
                version=0 if event == "E0" else 1)


def geometry_error(actual, expected):
    """Symmetric solid-volume difference; oracle never uses model self-ratings."""
    return max(0.0, actual.cut(expected).Volume()) + max(0.0, expected.cut(actual).Volume())


def score(path, s, event, status):
    gt = truth(s, event)
    actual = cq.importers.importStep(str(path)).val()
    before = compound(shapes(s, event))
    placed = compound(shapes(s, event, placed=True))
    before_error, target_error = geometry_error(actual, before), geometry_error(actual, placed)
    position = "target" if target_error < 1e-5 else "initial" if before_error < 1e-5 else "other"
    valid = actual.isValid()
    success = valid and status == "finished" and position == ("target" if gt["feasible"] else "initial")
    # Blocked language is audited separately; geometry alone does not establish a justified refusal.
    return dict(truth=gt, final_position=position, before_error_mm3=before_error,
                target_error_mm3=target_error, valid=valid, geometry_success=success,
                unsafe_placement=position == "target" and not gt["feasible"])


# A uses one task-declared family: candidate-vs-every-fixed-part distance/intersection,
# complete inspect/faces/edges (untruncated for these scenes), plus the same render tools.
# This is full relevant refresh, not a claim to serialize every possible CAD property.
REFRESH_CODE = '''candidate_now = movable.translate((TARGET_X-movable.val().Center().x,0,0))
print({'scene_version': scene_version, 'candidate_center': candidate_now.val().Center().toTuple(),
       'checks': {k: {'distance': candidate_now.val().distance(v.val()),
                      'overlap_mm3': candidate_now.val().intersect(v.val()).Volume()}
                  for k,v in parts.items() if k != 'movable'}})'''


def refresh_code(s):
    return REFRESH_CODE.replace("TARGET_X", str(s["target_x"]))
