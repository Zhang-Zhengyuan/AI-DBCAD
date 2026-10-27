"""Deterministic, public CAD requests and trusted reference constructors.

This module contains no model calls. Reference programs are used only to create
initial conditions and for offline scoring, never as generation feedback.
"""
from __future__ import annotations

import copy
import hashlib
import json

VERSION = "exp003-capabilities-v1"


def _program(body: str) -> str:
    return "import cadquery as cq\n" + body.strip() + "\n"


def program(kind: str, **p) -> str:
    """Construct the requested geometry independently from any agent output."""
    if kind == "block":
        s = f"r = cq.Workplane('XY').box({p['width']},30,8)"
        if p.get("hole"):
            s += f".faces('>Z').workplane().hole({p['hole']})"
    elif kind == "hex":
        s = f"r = cq.Workplane('XY').polygon(6,24).extrude({p['height']})"
        if p.get("hole"):
            s += f".faces('>Z').workplane().hole({p['hole']})"
    elif kind == "bore":
        s = f"r = cq.Workplane('XY').box(50,36,{p['height']}).faces('>Z').workplane().hole({p['hole']})"
    elif kind == "fillet":
        s = f"r = cq.Workplane('XY').box({p['width']},30,12).edges('|Z').fillet({p['radius']})"
    elif kind == "chamfer":
        s = f"r = cq.Workplane('XY').box(40,30,12).edges('|Z').chamfer({p['distance']})"
        if p.get("hole"):
            s += f".faces('>Z').workplane().hole({p['hole']})"
    elif kind == "pattern":
        s = ("r = cq.Workplane('XY').box(70,50,8).faces('>Z').workplane()"
             f".pushPoints([(-20,-12),(-20,12),(20,-12),(20,12)]).hole({p['hole']})")
        if p.get("centre_hole"):
            s += f"\nr = r.faces('>Z').workplane().hole({p['centre_hole']})"
    elif kind == "revolve":
        s = ("r = cq.Workplane('XZ').polyline("
             f"[(0,0),(10,0),(10,{p['flange']}),({p['shaft']},{p['flange']}),"
             f"({p['shaft']},30),(0,30)]).close().revolve(360,(0,0),(0,1))")
    elif kind == "pipe":
        s = ("path = (cq.Workplane('XZ').moveTo(0,0).lineTo(0,20)"
             ".threePointArc((5.857864376269049,34.14213562373095),(20,40))"
             f".lineTo({p['end_x']},40).wire())\n"
             f"r = cq.Workplane('XY').circle({p['outer']}).circle(3).sweep(path)")
    elif kind == "boss":
        s = f"r = cq.Workplane('XY').box(50,40,8).faces('>Z').workplane().hole({p['hole']})"
        if p.get("boss"):
            s += "\nr = r.union(cq.Workplane('XY').circle(4).extrude(6).translate((18,12,4)))"
    elif kind == "fixture":
        s = (f"part = cq.Workplane('XY').box(40,30,8).faces('>Z').workplane().hole({p['hole']})\n"
             f"fixture = cq.Workplane('XY').box(8,8,8).translate(({p['fixture_x']},0,0))\n"
             "r = cq.Compound.makeCompound([part.val(),fixture.val()])")
    elif kind == "slot":
        s = f"r = cq.Workplane('XY').box(60,40,8).faces('>Z').workplane().hole({p['hole']})"
        if p.get("slot"):
            s += "\nr = r.cut(cq.Workplane('XY').box(4,12,20).translate((22,0,0)))"
    elif kind == "placed":
        s = ("part = cq.Workplane('XY').box(40,30,8)"
             f".faces('>Z').workplane().hole({p['hole']}).translate(({p['x']},0,0))\n"
             "fixture = cq.Workplane('XY').box(8,8,8).translate((70,0,0))\n"
             "r = cq.Compound.makeCompound([part.val(),fixture.val()])")
    else:
        raise ValueError(f"Unknown reference family {kind}")
    return _program(s)


def _case(uid, family, params, stages, group="source", manual=None):
    current = copy.deepcopy(params)
    initial = program(family, **current)
    output_stages = []
    for index, (instruction, updates) in enumerate(stages):
        current.update(updates)
        if index == 1 and manual:
            current.update(manual["reference_updates"])
        output_stages.append({
            "id": f"s{index + 1}", "instruction": instruction,
            "reference_code": program(family, **current),
        })
    return {
        "id": uid, "group": group, "family": family,
        "initial_code": initial, "stages": output_stages,
        "manual_after_s1": manual,
        "units": "mm", "tolerance": {"bbox_mm": 0.02, "volume_relative": 1e-5, "volume_absolute_mm3": 1e-3},
    }


def cases():
    """Twelve two-stage chains; no selection based on observed model success."""
    return [
        _case("source-block", "block", dict(width=40, hole=0), [
            ("Increase only the block's X width from 40 to 50 mm; keep Y=30 and Z=8, centred at the origin.", dict(width=50)),
            ("Add one centred diameter-10 mm through hole parallel to Z; preserve the 50 by 30 by 8 mm block.", dict(hole=10)),
        ]),
        _case("source-extrude", "hex", dict(height=8, hole=0), [
            ("Increase the regular hexagonal prism extrusion from 8 to 12 mm along +Z. Preserve its six-sided diameter-24 mm polygon footprint and bottom plane Z=0.", dict(height=12)),
            ("Add a centred diameter-6 mm through hole parallel to Z; preserve the prism footprint, height and placement.", dict(hole=6)),
        ]),
        _case("source-bore", "bore", dict(height=8, hole=6), [
            ("Enlarge only the centred Z through bore from diameter 6 to 10 mm; preserve the 50 by 36 by 8 mm outer block.", dict(hole=10)),
            ("Increase the centred block thickness along Z from 8 to 12 mm; preserve the X/Y dimensions and centred diameter-10 through bore.", dict(height=12)),
        ]),
        _case("source-fillet", "fillet", dict(width=40, radius=2), [
            ("Change the four vertical edge fillets from radius 2 to 3 mm. Preserve the centred 40 by 30 by 12 mm envelope and all other edges.", dict(radius=3)),
            ("Increase the centred body's X width from 40 to 50 mm; preserve Y=30, Z=12 and the four radius-3 vertical fillets.", dict(width=50)),
        ]),
        _case("source-chamfer", "chamfer", dict(distance=1, hole=0), [
            ("Change the four vertical edge equal-distance chamfers from 1 to 2 mm; preserve the centred 40 by 30 by 12 mm envelope.", dict(distance=2)),
            ("Add a centred diameter-8 mm Z through hole while preserving every existing chamfer and outer dimension.", dict(hole=8)),
        ]),
        _case("source-pattern", "pattern", dict(hole=4, centre_hole=0), [
            ("Enlarge all four Z through holes from diameter 4 to 6 mm. Their XY centres remain (+/-20,+/-12); preserve the 70 by 50 by 8 mm plate.", dict(hole=6)),
            ("Add a fifth diameter-8 mm Z through hole at XY=(0,0). Preserve all four existing holes and the plate.", dict(centre_hole=8)),
        ]),
        _case("source-revolve", "revolve", dict(flange=8, shaft=6), [
            ("Increase the coaxial shaft radius from 6 to 7 mm, preserving flange radius 10 mm, flange thickness 8 mm, total height 30 mm and Z=0 bottom.", dict(shaft=7)),
            ("Increase flange thickness from 8 to 10 mm; preserve flange radius 10 mm, shaft radius 7 mm and total height 30 mm.", dict(flange=10)),
        ]),
        _case("source-pipe", "pipe", dict(outer=5, end_x=50), [
            ("Increase the hollow swept pipe outer radius from 5 to 6 mm, preserving inner radius 3 mm and its existing centreline exactly.", dict(outer=6)),
            ("Extend only the horizontal outlet along +X so the centreline endpoint changes from (50,0,40) to (60,0,40). Preserve both radii, the vertical inlet and the curved bend.", dict(end_x=60)),
        ]),
        _case("external-source-boss", "boss", dict(hole=6, boss=False), [
            ("Enlarge the centred Z through bore from diameter 6 to 8 mm; preserve the 50 by 40 by 8 mm block.", dict(hole=8)),
            ("Enlarge that centred bore from diameter 8 to 10 mm. Preserve the human-added off-centre boss and all outer dimensions.", dict(hole=10)),
        ], "external_source", {
            "description": "A human added a radius-4 mm, height-6 mm boss with bottom at (18,12,4). This new feature must remain.",
            "code": "r = r.union(cq.Workplane('XY').circle(4).extrude(6).translate((18,12,4)))",
            "reference_updates": {"boss": True},
        }),
        _case("external-source-fixture", "fixture", dict(hole=6, fixture_x=65), [
            ("Enlarge only the centred bore in the main block from diameter 6 to 8 mm. Preserve both bodies and placements.", dict(hole=8)),
            ("Enlarge the main block's bore from diameter 8 to 10 mm; preserve the other cube at its current human-updated position.", dict(hole=10)),
        ], "external_source", {
            "description": "A human moved the separate 8-mm fixture cube +10 mm along X, from centre X=65 to X=75. Preserve its new placement.",
            "code": "solids = r.solids().vals() if hasattr(r, 'solids') else r.Solids()\nr = cq.Compound.makeCompound([s.translate((10,0,0)) if s.Center().x > 50 else s for s in solids])",
            "reference_updates": {"fixture_x": 75},
        }),
        _case("external-step-slot", "slot", dict(hole=6, slot=False), [
            ("Enlarge only the centred Z through bore from diameter 6 to 8 mm; preserve the centred 60 by 40 by 8 mm plate.", dict(hole=8)),
            ("Enlarge the centred Z through bore from diameter 8 to 10 mm. Preserve the human-added through slot centred at (22,0) and all outer dimensions.", dict(hole=10)),
        ], "external_step", {
            "description": "A human cut a 4 by 12 mm rectangular Z through slot centred at XY=(22,0). The current STEP is authoritative; no complete updated authored program is available.",
            "code": "r = r.cut(cq.Workplane('XY').box(4,12,20).translate((22,0,0)))",
            "reference_updates": {"slot": True},
        }),
        _case("external-step-placement", "placed", dict(hole=6, x=0), [
            ("Enlarge the main block's Z through bore from diameter 6 to 8 mm. Preserve the separate fixture and both placements.", dict(hole=8)),
            ("Enlarge the main block's through bore to diameter 10 mm at its current centre XY=(5,0). Preserve the human-shifted block position and the separate fixture at X=70.", dict(hole=10)),
        ], "external_step", {
            "description": "A human translated only the main block +5 mm in X. The separate fixture is still centred at X=70. Current STEP geometry is authoritative.",
            "code": "solids = r.solids().vals() if hasattr(r, 'solids') else r.Solids()\nr = cq.Compound.makeCompound([s.translate((5,0,0)) if s.Center().x < 40 else s for s in solids])",
            "reference_updates": {"x": 5},
        }),
    ]


def public_case(case):
    """Request metadata safe to expose to the generator, excluding all oracles."""
    return {k: v for k, v in case.items() if k not in {"stages", "manual_after_s1"}} | {
        "stages": [{k: v for k, v in stage.items() if k != "reference_code"} for stage in case["stages"]],
        "manual_after_s1": None if not case["manual_after_s1"] else {
            "description": case["manual_after_s1"]["description"],
        },
    }


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def suite_manifest():
    data = cases()
    return {"version": VERSION, "cases": data, "case_count": len(data),
            "stage_count": sum(len(c["stages"]) for c in data), "sha256": fingerprint(data)}
