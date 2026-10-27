"""Value-only contracts for exact native feature operations and observations."""

from __future__ import annotations

import math


def _field(kind, required=False):
    return {"type": kind, **({"required": True} if required else {})}


ADVANCED_COMMAND_SCHEMAS = {
    "component.fillet": {
        "description": "按当前组件的边序号执行真实 ACIS 圆角（毫米）",
        "params": {"component": _field("string", True), "edges": _field("array<integer>", True),
                   "radius": _field("number", True)},
    },
    "component.chamfer": {
        "description": "按当前组件的边序号执行真实 ACIS 倒角（毫米）",
        "params": {"component": _field("string", True), "edges": _field("array<integer>", True),
                   "distance": _field("number", True), "distance2": _field("number")},
    },
    "component.extrude": {
        "description": "平面圆/矩形/多边形剖面沿法向拉伸成实体",
        "params": {"profile": _field("object", True), "plane": _field("string"),
                   "origin": _field("array<number>[3]"), "distance": _field("number", True)},
    },
    "component.revolve": {
        "description": "平面剖面绕指定轴旋转成实体（角度为度）",
        "params": {"profile": _field("object", True), "plane": _field("string"),
                   "origin": _field("array<number>[3]"), "axis": _field("array<number>[3]", True),
                   "axis_origin": _field("array<number>[3]"), "angle_degrees": _field("number", True)},
    },
    "component.sweep": {
        "description": "圆截面沿三维折线或圆弧路径扫掠成实体",
        "params": {"radius": _field("number", True), "path": _field("object", True)},
    },
}


def number(value, name, *, positive=False, nonzero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > 1e6:
        raise ValueError(f"{name}: finite numeric value in [-1000000, 1000000] required")
    if positive and value <= 0:
        raise ValueError(f"{name}: positive dimension required")
    if nonzero and value == 0:
        raise ValueError(f"{name}: nonzero value required")


def vector(value, name, size=3, *, nonzero=False):
    if not isinstance(value, list) or len(value) != size:
        raise ValueError(f"{name}: {size} coordinates required")
    for item in value:
        number(item, name)
    if nonzero and not any(value):
        raise ValueError(f"{name}: nonzero vector required")


def indices(value, name, *, empty=False):
    if not isinstance(value, list) or (not value and not empty) or len(value) > 1024:
        raise ValueError(f"{name}: bounded index list required")
    if any(isinstance(i, bool) or not isinstance(i, int) or i < 0 for i in value):
        raise ValueError(f"{name}: nonnegative integer indices required")
    if len(set(value)) != len(value):
        raise ValueError(f"{name}: duplicate indices")


def _profile(profile):
    if not isinstance(profile, dict):
        raise ValueError("profile: object required")
    kind = profile.get("kind")
    fields = {"circle": {"kind", "radius"}, "rectangle": {"kind", "width", "height"},
              "polygon": {"kind", "points"}}
    if kind not in fields or set(profile) != fields[kind]:
        raise ValueError("profile: circle(radius), rectangle(width,height), or polygon(points) required")
    if kind == "polygon":
        points = profile["points"]
        if not isinstance(points, list) or not 3 <= len(points) <= 256:
            raise ValueError("profile.points: 3 to 256 points required")
        for point in points:
            vector(point, "profile.points", 2)
    else:
        for key in fields[kind] - {"kind"}:
            number(profile[key], "profile." + key, positive=True)


def validate_advanced(action, params):
    schema = ADVANCED_COMMAND_SCHEMAS[action]["params"]
    if not isinstance(params, dict) or set(params) - set(schema):
        raise ValueError("Unknown native feature parameters")
    for key, spec in schema.items():
        if spec.get("required") and key not in params:
            raise ValueError(f"{key}: required parameter")
    if "component" in params and (not isinstance(params["component"], str) or not params["component"]):
        raise ValueError("component: nonempty asset reference required")
    for key in ("edges", "faces"):
        if key in params:
            indices(params[key], key)
    for key in ("radius", "distance2"):
        if key in params:
            number(params[key], key, positive=True)
    if "distance" in params:
        number(params["distance"], "distance", positive=action == "component.chamfer", nonzero=True)
    if "angle_degrees" in params:
        number(params["angle_degrees"], "angle_degrees", nonzero=True)
        if abs(params["angle_degrees"]) > 360:
            raise ValueError("angle_degrees: at most one complete rotation")
    for key in ("origin", "axis", "axis_origin"):
        if key in params:
            vector(params[key], key, nonzero=key == "axis")
    if "plane" in params and params["plane"] not in {"XY", "XZ", "YZ"}:
        raise ValueError("plane: XY, XZ, or YZ required")
    if "profile" in params:
        _profile(params["profile"])
    if "path" in params:
        path = params["path"]
        if not isinstance(path, dict) or path.get("kind") not in {"polyline", "arc"}:
            raise ValueError("path: polyline or arc required")
        if "points" in path:
            if set(path) != {"kind", "points"}:
                raise ValueError("path: unknown parameters")
            points = path["points"]
            if not isinstance(points, list) or not 2 <= len(points) <= 256:
                raise ValueError("path.points: 2 to 256 coordinates required")
            if path["kind"] == "arc" and len(points) != 3:
                raise ValueError("arc: start, middle, and end required")
            for point in points:
                vector(point, "path.points")
        else:
            if path.get("kind") != "arc" or set(path) != {"kind", "center", "normal", "radius", "start_angle", "sweep_angle"}:
                raise ValueError("arc: center, normal, radius, start_angle, and sweep_angle required")
            vector(path["center"], "path.center")
            vector(path["normal"], "path.normal", nonzero=True)
            number(path["radius"], "path.radius", positive=True)
            number(path["start_angle"], "path.start_angle")
            number(path["sweep_angle"], "path.sweep_angle", nonzero=True)
            if abs(path["sweep_angle"]) > 360:
                raise ValueError("path.sweep_angle: at most one rotation")


def validate_query(query, known):
    if not isinstance(query, dict):
        raise ValueError("query: object required")
    kind = query.get("kind")
    fields = {"kind", "component", "a", "b", "limit", "offset", "faces", "edges"}
    if set(query) - fields or kind not in {"inspect", "faces", "edges", "distance", "interference", "validate", "render_selection"}:
        raise ValueError("Unsupported geometry query")
    if kind in {"faces", "edges", "render_selection"} and "component" not in query:
        raise ValueError("component: required for local geometry query")
    if kind in {"distance", "interference"} and not all(key in query for key in ("a", "b")):
        raise ValueError("a and b: both component references are required")
    for key in ("component", "a", "b"):
        if key in query and (not isinstance(query[key], str) or query[key] not in known):
            raise ValueError("Unknown or stale component reference; inspect the current scene")
    for key in ("limit", "offset"):
        if key in query and (isinstance(query[key], bool) or not isinstance(query[key], int) or query[key] < 0):
            raise ValueError(f"{key}: nonnegative integer required")
    if query.get("limit", 50) > 1024:
        raise ValueError("limit: at most 1024")
    for key in ("faces", "edges"):
        if key in query:
            indices(query[key], key, empty=True)
