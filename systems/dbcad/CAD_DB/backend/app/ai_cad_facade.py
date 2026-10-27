"""Bounded native CAD program language for the AgenticCADedit ACIS adapter.

This is deliberately not CadQuery compatibility. Programs use ``cad`` operations
over immutable asset references; all geometry and exact queries are callbacks to
the native worker. No generated program is passed to Python ``exec``.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import asdict, dataclass
import math
import operator

from .command_result import manifest_from_result
from .v1_store import SceneComponent


ADAPTER_VERSION = "agenticcadedit-acis/1"
MAX_ITEMS = 512


@dataclass(frozen=True)
class CadRef:
    """An immutable component asset reference, never a native pointer."""

    ref: str


def encode(value):
    if isinstance(value, CadRef):
        return {"$cad": value.ref}
    if isinstance(value, tuple):
        return {"$tuple": [encode(v) for v in value]}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        if any(not isinstance(k, str) or k.startswith("$") for k in value):
            raise ValueError("Program dictionaries require ordinary string keys")
        return {k: encode(v) for k, v in value.items()}
    if value is None or isinstance(value, (str, bool, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Non-finite program values are not supported")
        return value
    raise ValueError("Only CAD references and JSON values may be stored")


def decode(value):
    if isinstance(value, dict):
        if set(value) == {"$cad"}:
            return CadRef(str(value["$cad"]))
        if set(value) == {"$tuple"}:
            return tuple(decode(v) for v in value["$tuple"])
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


def finite(value, name="value", *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{name} must be numeric")
    if not math.isfinite(value) or abs(value) > 1e6 or (positive and value <= 0):
        raise ValueError(f"{name} must be finite, bounded" + (" and positive" if positive else ""))
    return float(value)


def vector(value, name="vector"):
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError(f"{name} must contain three coordinates")
    return [finite(v, name) for v in value]


class CadSession:
    """Native scene and bounded-program state owned by a single preview job.

    ``callbacks.execute(action, params)`` returns a native worker command result.
    ``callbacks.query(request)`` and ``callbacks.render(views, faces, edges)``
    inspect the current ``job.manifest``. The session only updates private preview
    fields; publication remains the existing apply/base_version contract.
    """

    def __init__(self, job, callbacks):
        self.job, self.callbacks = job, callbacks
        saved = getattr(job, "agentic_state", None) or {}
        if saved and saved.get("adapter") != ADAPTER_VERSION:
            raise ValueError("Unsupported native CAD checkpoint adapter")
        self.bindings = decode(saved.get("bindings", {}))
        self.history = copy.deepcopy(saved.get("history", []))
        if not saved and len(job.manifest) == 1:
            self.bindings["result"] = CadRef(job.manifest[0].hash_sat)
        self.persist()

    def persist(self):
        self.job.agentic_state = {
            "adapter": ADAPTER_VERSION,
            "bindings": encode(self.bindings),
            "history": copy.deepcopy(self.history),
        }

    def snapshot(self):
        return {
            "manifest": [asdict(c) for c in self.job.manifest],
            "bindings": encode(self.bindings),
            "projection_count": len(self.job.projections),
        }

    def restore(self, snapshot):
        self.job.manifest = [SceneComponent(**c) for c in snapshot["manifest"]]
        self.bindings = decode(copy.deepcopy(snapshot["bindings"]))
        self.job.projections = self.job.projections[: snapshot["projection_count"]]

    def reference(self, value):
        ref = value.ref if isinstance(value, CadRef) else value
        if not isinstance(ref, str) or ref not in {c.hash_sat for c in self.job.manifest}:
            raise ValueError("Unknown or stale component reference; inspect the current scene")
        return ref

    async def execute(self, action, params):
        previous = list(self.job.manifest)
        output = await self.callbacks.execute(action, params)
        manifest, projections, added, _ = manifest_from_result(previous, output)
        self.job.manifest = manifest
        self.job.projections.extend(projections)
        modified = output.get("modified", [])
        if modified:
            return CadRef(modified[0]["sat_bytes"])
        if added:
            return CadRef(added[0].hash_sat)
        return None

    async def inspect(self):
        result = await self.callbacks.query({"kind": "inspect"})
        return {
            **result,
            "adapter": ADAPTER_VERSION,
            "language": "bounded native cad Python; not CadQuery",
            "bindings": encode(self.bindings),
            "selected_at_start": self.job.selected,
            "undo_steps": len(self.history),
            "stage": "isolated preview; not a published project version",
        }

    async def list_entities(self, kind, component=None, limit=50, offset=0):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 512:
            raise ValueError("limit must be between 1 and 512")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be nonnegative")
        request = {"kind": kind, "limit": limit, "offset": offset}
        if component is not None:
            request["component"] = self.reference(component)
        return await self.callbacks.query(request)

    async def run_step(self, code):
        before, projections = self.snapshot(), list(self.job.projections)
        old_history = list(self.history)
        interpreter = Program(self)
        try:
            await interpreter.run(code)
            # Validate serializability before accepting any program state.
            encode(self.bindings)
            changed = before["manifest"] != [asdict(c) for c in self.job.manifest]
            if changed:
                self.history.append(before)
            self.persist()
            return {"ok": True, "stdout": interpreter.stdout, "changed_geometry": changed,
                    "info": await self.inspect(), "n_steps": len(self.history)}
        except BaseException as error:
            self.restore(before)
            self.job.projections = projections
            self.history = old_history
            self.persist()
            if not isinstance(error, Exception):
                raise
            return {"ok": False, "error": f"{type(error).__name__}: {error}",
                    "stdout": interpreter.stdout, "rolled_back": True,
                    "n_steps": len(self.history)}

    async def undo(self):
        if not self.history:
            return {"ok": False, "error": "Nothing to undo"}
        self.restore(self.history.pop())
        self.persist()
        return {"ok": True, "info": await self.inspect(), "n_steps": len(self.history)}

    async def reset(self):
        self.job.manifest = []
        self.job.projections = []
        self.bindings = {}
        self.history = []
        self.persist()
        return {"ok": True, "info": await self.inspect()}

    async def render(self, views=None, highlight_faces=None, highlight_edges=None):
        allowed = {"toprightiso", "isometric", "front", "back", "left", "right", "top", "bottom"}
        views = [views] if isinstance(views, str) else views or ["toprightiso"]
        if not isinstance(views, list) or not 1 <= len(views) <= 7 or set(views) - allowed:
            raise ValueError("Unknown or excessive render views")
        for points in (highlight_faces or [], highlight_edges or []):
            if not isinstance(points, list) or len(points) > 64:
                raise ValueError("At most 64 highlighted entities are supported")
            for point in points:
                vector(point, "highlight center")
        images = await self.callbacks.render(views, highlight_faces or [], highlight_edges or [])
        if not isinstance(images, list) or not images:
            raise ValueError("Renderer returned no PNG views")
        return {"ok": True, "images": images, "views": views}


class NativeCAD:
    """Only methods in METHODS are callable by generated programs."""

    METHODS = {
        "scene", "component", "id", "box", "sphere", "cylinder", "cone", "torus",
        "translate", "rotate", "scale", "delete", "unite", "subtract", "intersect",
        "extrude", "revolve", "sweep", "fillet", "chamfer", "faces", "edges",
        "nearest_edge", "nearest_face", "distance", "interference", "validate", "inspect",
    }

    def __init__(self, session):
        self.session = session

    async def call(self, name, args, kwargs):
        if name not in self.METHODS:
            raise ValueError(f"Unsupported cad operation: {name}")
        return await getattr(self, name)(*args, **kwargs)

    async def scene(self):
        return [CadRef(c.hash_sat) for c in self.session.job.manifest]

    async def component(self, identifier):
        if not isinstance(identifier, str):
            raise ValueError("component requires an ID or unique component name")
        matches = [c for c in self.session.job.manifest if c.hash_sat == identifier or c.name == identifier]
        if len(matches) != 1:
            raise ValueError("Component is missing or its name is ambiguous")
        return CadRef(matches[0].hash_sat)

    async def id(self, component):
        return self.session.reference(component)

    async def _primitive(self, kind, params, center):
        return await self.session.execute("component.create", {
            "kind": kind, **{k: finite(v, k, positive=True) for k, v in params.items()},
            "position": vector(center, "center"),
        })

    async def box(self, width, height, depth, center=(0, 0, 0)):
        return await self._primitive("cube", {"width": width, "height": height, "depth": depth}, center)

    async def sphere(self, radius, center=(0, 0, 0)):
        return await self._primitive("sphere", {"radius": radius}, center)

    async def cylinder(self, radius, height, center=(0, 0, 0)):
        return await self._primitive("cylinder", {"radius": radius, "height": height}, center)

    async def cone(self, radius, height, center=(0, 0, 0)):
        return await self._primitive("cone", {"radius": radius, "height": height}, center)

    async def torus(self, major_radius, minor_radius, center=(0, 0, 0)):
        return await self._primitive("torus", {"major_radius": major_radius, "minor_radius": minor_radius}, center)

    async def translate(self, component, offset):
        return await self.session.execute("component.translate", {
            "component": self.session.reference(component), "offset": vector(offset),
        })

    async def rotate(self, component, axis, angle_degrees):
        axis = vector(axis)
        if sum(v * v for v in axis) == 0:
            raise ValueError("Rotation axis must be nonzero")
        return await self.session.execute("component.rotate", {
            "component": self.session.reference(component), "axis": axis,
            "angle_degrees": finite(angle_degrees),
        })

    async def scale(self, component, factor):
        return await self.session.execute("component.scale", {
            "component": self.session.reference(component), "factor": finite(factor, positive=True),
        })

    async def delete(self, component):
        return await self.session.execute("component.delete", {"component": self.session.reference(component)})

    async def _boolean(self, action, target, tool):
        a, b = self.session.reference(target), self.session.reference(tool)
        if a == b:
            raise ValueError("Boolean needs two distinct current components")
        return await self.session.execute("boolean." + action, {"targets": [a], "tool": b})

    async def unite(self, target, tool):
        return await self._boolean("unite", target, tool)

    async def subtract(self, target, tool):
        return await self._boolean("subtract", target, tool)

    async def intersect(self, target, tool):
        return await self._boolean("intersect", target, tool)

    def _profile(self, profile):
        if not isinstance(profile, dict) or profile.get("kind") not in {"rectangle", "circle", "polygon"}:
            raise ValueError("profile.kind must be rectangle, circle or polygon")
        profile = copy.deepcopy(profile)
        kind = profile["kind"]
        fields = {"rectangle": {"kind", "width", "height"}, "circle": {"kind", "radius"},
                  "polygon": {"kind", "points"}}[kind]
        if set(profile) != fields:
            raise ValueError("Profile fields do not match its kind")
        if kind == "polygon":
            points = profile["points"]
            if not isinstance(points, (list, tuple)) or not 3 <= len(points) <= 128:
                raise ValueError("Polygon requires 3 to 128 points")
            profile["points"] = []
            for point in points:
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    raise ValueError("Profile points require two coordinates")
                profile["points"].append([finite(v) for v in point])
        else:
            for key in fields - {"kind"}:
                profile[key] = finite(profile[key], key, positive=True)
        return profile

    async def extrude(self, profile, distance, plane="XY", origin=(0, 0, 0)):
        if plane not in {"XY", "XZ", "YZ"}:
            raise ValueError("plane must be XY, XZ or YZ")
        distance = finite(distance)
        if distance == 0:
            raise ValueError("Extrusion distance must be nonzero")
        return await self.session.execute("component.extrude", {
            "profile": self._profile(profile), "plane": plane, "origin": vector(origin), "distance": distance,
        })

    async def revolve(self, profile, angle_degrees=360, plane="XY", origin=(0, 0, 0),
                      axis=(0, 1, 0), axis_origin=(0, 0, 0)):
        if plane not in {"XY", "XZ", "YZ"}:
            raise ValueError("plane must be XY, XZ or YZ")
        angle = finite(angle_degrees)
        if not 0 < abs(angle) <= 360:
            raise ValueError("Revolution angle must be nonzero and at most 360 degrees")
        direction = vector(axis)
        if sum(v * v for v in direction) == 0:
            raise ValueError("Revolution axis must be nonzero")
        return await self.session.execute("component.revolve", {
            "profile": self._profile(profile), "plane": plane, "origin": vector(origin),
            "angle_degrees": angle, "axis": direction, "axis_origin": vector(axis_origin),
        })

    async def sweep(self, radius, path):
        if not isinstance(path, dict) or path.get("kind") not in {"polyline", "arc"}:
            raise ValueError("path.kind must be polyline or arc")
        path = copy.deepcopy(path)
        allowed = {"kind", "points", "center", "normal", "radius", "start_angle", "sweep_angle"}
        if set(path) - allowed:
            raise ValueError("Unknown sweep path fields")
        if "points" in path:
            if not isinstance(path["points"], (list, tuple)) or not 2 <= len(path["points"]) <= 128:
                raise ValueError("Sweep requires 2 to 128 path points")
            path["points"] = [vector(p) for p in path["points"]]
        for field in ("center", "normal"):
            if field in path:
                path[field] = vector(path[field])
        for field in ("radius", "start_angle", "sweep_angle"):
            if field in path:
                path[field] = finite(path[field])
        return await self.session.execute("component.sweep", {"radius": finite(radius, positive=True), "path": path})

    def _indices(self, edges):
        if isinstance(edges, int) and not isinstance(edges, bool):
            edges = [edges]
        if not isinstance(edges, (list, tuple)) or not 1 <= len(edges) <= 512:
            raise ValueError("edges must contain current edge indices")
        if any(isinstance(i, bool) or not isinstance(i, int) or i < 0 for i in edges):
            raise ValueError("Edge indices must be nonnegative integers")
        return list(dict.fromkeys(edges))

    async def fillet(self, component, edges, radius):
        return await self.session.execute("component.fillet", {
            "component": self.session.reference(component), "edges": self._indices(edges),
            "radius": finite(radius, positive=True),
        })

    async def chamfer(self, component, edges, distance, distance2=None):
        params = {"component": self.session.reference(component), "edges": self._indices(edges),
                  "distance": finite(distance, positive=True)}
        if distance2 is not None:
            params["distance2"] = finite(distance2, positive=True)
        return await self.session.execute("component.chamfer", params)

    async def faces(self, component, limit=512, offset=0):
        return (await self.session.list_entities("faces", component, limit, offset)).get("items", [])

    async def edges(self, component, limit=512, offset=0):
        return (await self.session.list_entities("edges", component, limit, offset)).get("items", [])

    async def _nearest(self, kind, component, point):
        point = vector(point)
        result = await self.session.list_entities(kind, component, 512, 0)
        if result.get("truncated") or result.get("next_offset") is not None:
            raise ValueError("Nearest selection needs an untruncated topology query")
        candidates = []
        for entity in result.get("items", []):
            center = entity.get("center")
            if isinstance(center, dict):
                center = [center[k] for k in ("x", "y", "z")]
            center = vector(center, "entity center")
            candidates.append((sum((a - b) ** 2 for a, b in zip(center, point)), entity["index"]))
        candidates.sort()
        if not candidates:
            raise ValueError("No selectable entities")
        if len(candidates) > 1 and math.isclose(candidates[0][0], candidates[1][0], abs_tol=1e-12):
            raise ValueError("Nearest entity is ambiguous; inspect and select an explicit index")
        return candidates[0][1]

    async def nearest_edge(self, component, point):
        return await self._nearest("edges", component, point)

    async def nearest_face(self, component, point):
        return await self._nearest("faces", component, point)

    async def _pair(self, kind, a, b):
        return await self.session.callbacks.query({"kind": kind,
            "a": self.session.reference(a), "b": self.session.reference(b)})

    async def distance(self, a, b):
        return await self._pair("distance", a, b)

    async def interference(self, a, b):
        return await self._pair("interference", a, b)

    async def validate(self, component=None):
        request = {"kind": "validate"}
        if component is not None:
            request["component"] = self.session.reference(component)
        return await self.session.callbacks.query(request)

    async def inspect(self):
        return await self.session.inspect()


class Program:
    """Small asynchronous AST interpreter; no arbitrary Python runtime access."""

    BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
              ast.Pow: operator.pow}
    COMPARE = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
               ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
               ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}
    MATH = {name: getattr(math, name) for name in (
        "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sqrt", "radians", "degrees", "ceil", "floor")}

    def __init__(self, session):
        self.session, self.cad = session, NativeCAD(session)
        self.steps, self.stdout = 0, ""

    def tick(self):
        self.steps += 1
        if self.steps > 10000:
            raise ValueError("Program operation limit reached")

    def name(self, value):
        if not isinstance(value, ast.Name) or value.id.startswith("_") or value.id in {"cad", "math"}:
            raise ValueError("Assignments require an ordinary, non-reserved variable name")
        return value.id

    def bounded(self, value):
        if isinstance(value, (int, float)) and (not math.isfinite(value) or abs(value) > 1e12):
            raise ValueError("Program number exceeds its finite bound")
        if isinstance(value, (list, tuple, dict, str)) and len(value) > (16000 if isinstance(value, str) else MAX_ITEMS):
            raise ValueError("Program collection limit reached")
        return value

    async def run(self, code):
        if not isinstance(code, str) or len(code) > 20000:
            raise ValueError("run_step requires at most 20000 code characters")
        tree = ast.parse(code, mode="exec")
        if sum(1 for _ in ast.walk(tree)) > 2500:
            raise ValueError("Program syntax limit reached")
        for statement in tree.body:
            await self.statement(statement)

    async def statement(self, node):
        self.tick()
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            self.session.bindings[self.name(node.targets[0])] = await self.expression(node.value)
        elif isinstance(node, ast.AugAssign):
            name = self.name(node.target)
            old = self.session.bindings[name]
            value = await self.expression(node.value)
            self.session.bindings[name] = self.binary(node.op, old, value)
        elif isinstance(node, ast.Expr):
            await self.expression(node.value)
        elif isinstance(node, ast.For):
            name, iterable = self.name(node.target), await self.expression(node.iter)
            if not isinstance(iterable, (list, tuple, range)) or len(iterable) > 256 or node.orelse:
                raise ValueError("for requires at most 256 items and no else clause")
            for item in iterable:
                self.session.bindings[name] = item
                for statement in node.body:
                    await self.statement(statement)
        elif isinstance(node, ast.If):
            branch = node.body if await self.expression(node.test) else node.orelse
            for statement in branch:
                await self.statement(statement)
        elif isinstance(node, ast.Pass):
            return
        else:
            raise ValueError(f"Unsupported native program statement: {type(node).__name__}")

    def binary(self, op, left, right):
        if isinstance(op, ast.Pow) and (not isinstance(right, (int, float)) or abs(right) > 12):
            raise ValueError("Power exponent exceeds limit")
        if isinstance(op, ast.Mult):
            for a, b in ((left, right), (right, left)):
                if isinstance(a, (str, list, tuple)) and isinstance(b, int) and len(a) * max(0, b) > MAX_ITEMS:
                    raise ValueError("Repeated collection exceeds limit")
        operation = self.BINARY.get(type(op))
        if operation is None:
            raise ValueError("Unsupported arithmetic operation")
        return self.bounded(operation(left, right))

    async def expression(self, node):
        self.tick()
        if isinstance(node, ast.Constant):
            if node.value is not None and not isinstance(node.value, (str, bool, int, float)):
                raise ValueError("Unsupported literal")
            return self.bounded(node.value)
        if isinstance(node, ast.Name):
            if node.id.startswith("_") or node.id not in self.session.bindings:
                raise ValueError(f"Unknown program variable: {node.id}")
            return self.session.bindings[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            result = [await self.expression(item) for item in node.elts]
            return self.bounded(tuple(result) if isinstance(node, ast.Tuple) else result)
        if isinstance(node, ast.Dict):
            if any(key is None for key in node.keys):
                raise ValueError("Dictionary expansion is unsupported")
            result = {}
            for key, value in zip(node.keys, node.values):
                name = await self.expression(key)
                if not isinstance(name, str) or name.startswith("$"):
                    raise ValueError("Dictionary keys must be ordinary strings")
                result[name] = await self.expression(value)
            return self.bounded(result)
        if isinstance(node, ast.ListComp):
            if len(node.generators) != 1 or node.generators[0].is_async:
                raise ValueError("List comprehensions support one bounded synchronous generator")
            generator = node.generators[0]
            name = self.name(generator.target)
            values = await self.expression(generator.iter)
            if not isinstance(values, (list, tuple, range)) or len(values) > MAX_ITEMS:
                raise ValueError("Comprehension input exceeds its collection bound")
            missing = object()
            previous = self.session.bindings.get(name, missing)
            result = []
            try:
                for item in values:
                    self.tick()
                    self.session.bindings[name] = item
                    include = True
                    for condition in generator.ifs:
                        if not await self.expression(condition):
                            include = False
                            break
                    if include:
                        result.append(await self.expression(node.elt))
            finally:
                if previous is missing:
                    self.session.bindings.pop(name, None)
                else:
                    self.session.bindings[name] = previous
            return self.bounded(result)
        if isinstance(node, ast.BinOp):
            return self.binary(node.op, await self.expression(node.left), await self.expression(node.right))
        if isinstance(node, ast.UnaryOp):
            value = await self.expression(node.operand)
            operation = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Not: operator.not_}.get(type(node.op))
            if operation is None:
                raise ValueError("Unsupported unary operation")
            return self.bounded(operation(value))
        if isinstance(node, ast.BoolOp):
            result = await self.expression(node.values[0])
            for value in node.values[1:]:
                if (isinstance(node.op, ast.And) and not result) or (isinstance(node.op, ast.Or) and result):
                    break
                result = await self.expression(value)
            return result
        if isinstance(node, ast.Compare):
            left = await self.expression(node.left)
            for operation, item in zip(node.ops, node.comparators):
                right = await self.expression(item)
                comparator = self.COMPARE.get(type(operation))
                if comparator is None or not comparator(left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return await self.expression(node.body if await self.expression(node.test) else node.orelse)
        if isinstance(node, ast.Subscript):
            container = await self.expression(node.value)
            if not isinstance(container, (str, tuple, list, dict)):
                raise ValueError("Only ordinary collections support indexing")
            return container[await self.expression(node.slice)]
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "math" and node.attr in {"pi", "e", "tau"}:
                return getattr(math, node.attr)
            raise ValueError("Attribute access is restricted to documented CAD/math calls")
        if isinstance(node, ast.JoinedStr):
            parts = []
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    if value.format_spec is not None:
                        raise ValueError("Formatted strings do not support format specifications")
                    parts.append(str(await self.expression(value.value)))
                else:
                    parts.append(await self.expression(value))
            return self.bounded("".join(parts))
        if isinstance(node, ast.Call):
            if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
                raise ValueError("Argument expansion is unsupported")
            args = [await self.expression(a) for a in node.args]
            kwargs = {k.arg: await self.expression(k.value) for k in node.keywords}
            target = node.func
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                if target.value.id == "cad":
                    return await self.cad.call(target.attr, args, kwargs)
                if target.value.id == "math" and target.attr in self.MATH:
                    return self.bounded(self.MATH[target.attr](*args, **kwargs))
            if isinstance(target, ast.Name):
                if target.id == "print":
                    if kwargs:
                        raise ValueError("print only supports positional values")
                    self.stdout = (self.stdout + " ".join(str(a) for a in args) + "\n")[:8000]
                    return None
                if target.id == "range":
                    value = range(*args, **kwargs)
                    if len(value) > 256:
                        raise ValueError("range exceeds 256 items")
                    return value
                builtins = {"len": len, "min": min, "max": max, "abs": abs, "round": round,
                            "sum": sum, "str": str, "int": int, "float": float}
                if target.id in builtins:
                    return self.bounded(builtins[target.id](*args, **kwargs))
            raise ValueError("Only documented CAD, math and bounded numeric builtins are callable")
        raise ValueError(f"Unsupported native program expression: {type(node).__name__}")
