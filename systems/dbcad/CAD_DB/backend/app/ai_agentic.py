"""Official AgenticCADedit loop embedded through an ACIS-native tool adapter.

The API process owns model requests, accounting and the private preview. A
credential-free subprocess runs the pinned upstream loop and a thin MCP proxy.
The only IPC is a private per-job Unix socket; ACIS remains inside the worker.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
import json
import os
from pathlib import Path
import signal
import tempfile
from typing import Awaitable, Callable

from .ai_cad_facade import ADAPTER_VERSION, CadSession


UPSTREAM_REVISION = "c084034cdcf462025a60ee70c5ba94577428e5ca"
MAX_RPC_BYTES = 32 * 1024 * 1024

NATIVE_PROMPT = """You are the AgenticCADedit ACIS-native CAD editing assistant.
Use the provided persistent tools in this rhythm: inspect -> locate -> highlight
the selection -> edit a small step -> inspect and render -> verify -> finish.
Changes are private previews until the human applies them. Preserve unrelated
parts and the user's active dimensions. Finish with a Chinese summary; set
success=false for unsupported or impossible requirements. Never claim that
executable code or a changed hash proves the requested geometry is correct.

This is an explicit native LANGUAGE ADAPTATION, NOT CadQuery compatibility.
run_step interprets bounded Python syntax. There is no Python exec, import,
filesystem, network, arbitrary attribute access, function/class definition or
while loop. Ordinary assignments, arithmetic, if, for over range/list (at most
256 items), and one-generator list comprehensions are supported. print, range,
len, min, max, abs, round, sum, str, int, float and math.pi/tau/e and ordinary
trigonometric/sqrt/radians/degrees functions are available. Keep each step small.
Variables persist; each geometry-changing step is atomic and undo restores its
geometry AND bindings. Pure queries do not create undo entries.

All dimensions are mm and angles degrees in the world frame. A cad component is
an immutable asset reference. An operation returns a NEW reference and replaces
the current scene component: ALWAYS rebind `part = cad.translate(part, ...)`.
Old references become stale; inspect current IDs rather than reusing them.
All newly created parts enter the scene. Boolean tools are consumed. The whole
scene is the preview; assigning `result` alone does not delete other components.
If there is one initial component it is bound to result. For multiple components
use cad.scene() and cad.component(id_or_unique_name). Never invent IDs.

Native cad API (all geometry and measurements come from ACIS):
cad.scene() -> list of component references
cad.component(id_or_unique_name) -> reference; cad.id(part) -> current component ID
cad.box(width,height,depth,center=[0,0,0]) -> part (X,Y,Z sizes; CENTERED)
cad.sphere(radius,center=[0,0,0]); cad.cylinder(radius,height,center=[0,0,0])
cad.cone(radius,height,center=[0,0,0]); cad.torus(major_radius,minor_radius,center=[0,0,0])
Cylinder/cone height is along Z, CENTERED at center.
cad.translate(part,offset=[x,y,z]); cad.rotate(part,axis=[x,y,z],angle_degrees=...)
Rotation is around a world-origin axis. cad.scale(part,factor) scales at origin.
cad.delete(part); cad.unite(target,tool); cad.subtract(target,tool); cad.intersect(target,tool)
cad.extrude(profile,distance,plane='XY',origin=[0,0,0])
cad.revolve(profile,angle_degrees=360,plane='XY',origin=[0,0,0],axis=[0,1,0],axis_origin=[0,0,0])
Profiles: {'kind':'rectangle','width':w,'height':h}, {'kind':'circle','radius':r},
or {'kind':'polygon','points':[[u,v],...]}. Explicitly choose plane/origin.
Plane coordinates (u,v) map to world axes: XY u=X,v=Y,normal=+Z; XZ u=X,v=Z,
normal=-Y; YZ u=Y,v=Z,normal=+X. Rectangles and circles are centred on the
profile origin; polygon points are raw local [u,v], NOT re-centred. Extrusion
grows along plane_normal * distance (negative distance flips the side). Revolve
axis and axis_origin are world coordinates; the default axis is +Y.
cad.sweep(radius,path) sweeps a circular cross-section. Path is
{'kind':'polyline','points':[[x,y,z],...]} or {'kind':'arc','points':[start,mid,end]}
or {'kind':'arc','center':[x,y,z],'normal':[nx,ny,nz],'radius':bend_radius,
'start_angle':degrees,'sweep_angle':degrees}. Outer radius is the tube section.
cad.edges(part,limit=512,offset=0); cad.faces(part,limit=512,offset=0) -> list of
actual entity dictionaries, with current local index/type/center and geometry.
cad.nearest_edge(part,[x,y,z]); cad.nearest_face(part,[x,y,z]) -> current local
index by nearest entity center. Ambiguous/truncated choices fail rather than guess.
cad.fillet(part,edges=[current_indices],radius=r)
cad.chamfer(part,edges=[current_indices],distance=d,distance2=optional)
cad.distance(a,b) -> exact distance dictionary
cad.interference(a,b) -> exact intersection-volume dictionary
cad.validate(part=optional); cad.inspect() -> authoritative current properties

Tool list_faces/list_edges can paginate and accept a component ID. Indices are
local to a component and can change after edits; query again. render_views uses
list_faces/list_edges centers for red highlights of the actual selected entity.
Use both rendered views and exact measurements to verify an edit. Do not use an
unchanged component hash as evidence that surrounding space is still clear.
Example rhythm, NOT a task answer:
  run_step("plate = cad.box(40,30,8); cutter = cad.cylinder(3,12)")
  run_step("plate = cad.subtract(plate,cutter); print(cad.validate(plate))")
  list_edges(component=the_current_ID); render_views(...); inspect(); finish(...)
"""


@dataclass
class AgenticCallbacks:
    execute: Callable[[str, dict], Awaitable[dict]]
    query: Callable[[dict], Awaitable[dict]]
    render: Callable[[list, list, list], Awaitable[list]]
    model: Callable[[list, list], Awaitable[dict]]
    export_step: Callable[[str], Awaitable[dict]] | None = None
    checkpoint: Callable[[], object] | None = None


async def _maybe_await(value):
    return await value if inspect.isawaitable(value) else value


class AgenticBridge:
    """Parent-side dispatch, usable independently for deterministic testing."""

    def __init__(self, job, callbacks, output_dir):
        self.job, self.callbacks = job, callbacks
        self.session = CadSession(job, callbacks)
        self.output_dir = Path(output_dir).resolve()
        self.completed = None
        self.fatal = None
        self.connections = set()
        self.lock = asyncio.Lock()
        self.step_exported = False

    async def checkpoint(self):
        self.session.persist()
        if self.callbacks.checkpoint:
            await _maybe_await(self.callbacks.checkpoint())

    async def dispatch(self, operation, payload):
        if getattr(self.job, "cancelled", False):
            raise asyncio.CancelledError("Native AI job cancelled")
        if not isinstance(payload, dict):
            raise ValueError("Native RPC payload must be an object")
        if operation == "model":
            messages, tools = payload["messages"], payload["tools"]
            if not isinstance(messages, list) or not isinstance(tools, list):
                raise ValueError("Model messages and tools must be lists")
            self.job.messages = messages
            try:
                result = await self.callbacks.model(messages, tools)
                message = result["choices"][0]["message"]
                self.job.messages = messages + [message]
                await self.checkpoint()
                return result
            except BaseException as error:
                self.fatal = error
                raise
        if operation == "complete":
            self.completed = {k: payload.get(k) for k in ("finished", "success", "exit_reason", "summary")}
            self.job.messages = payload.get("messages", self.job.messages)
            await self.checkpoint()
            return {"recorded": True}
        if operation != "tool":
            raise ValueError("Unknown native RPC operation")
        name, args = payload.get("name"), payload.get("arguments", {})
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be an object")
        async with self.lock:
            if name == "run_step":
                if set(args) != {"code"}:
                    raise ValueError("run_step requires only code")
                result = await self.session.run_step(args["code"])
            elif name == "inspect":
                if args:
                    raise ValueError("inspect accepts no arguments")
                result = await self.session.inspect()
            elif name in {"list_faces", "list_edges"}:
                if set(args) - {"component", "limit", "offset"}:
                    raise ValueError("Unknown entity query argument")
                result = await self.session.list_entities("faces" if name == "list_faces" else "edges", **args)
            elif name == "render_views":
                if set(args) - {"views", "highlight_faces", "highlight_edges"}:
                    raise ValueError("Unknown render argument")
                result = await self.session.render(**args)
            elif name in {"undo", "reset"}:
                if args:
                    raise ValueError(f"{name} accepts no arguments")
                result = await getattr(self.session, name)()
            elif name == "export_step":
                destination = Path(args.get("output_dir", "")).resolve()
                if destination != self.output_dir:
                    raise ValueError("STEP export is restricted to this job's private output directory")
                if self.callbacks.export_step:
                    result = await self.callbacks.export_step(str(destination))
                    self.step_exported = bool(result.get("ok"))
                else:
                    result = {"ok": False, "error": "Native STEP export is unavailable; SAT preview assets remain authoritative"}
            else:
                raise ValueError("Unknown native CAD tool")
            public_result = {k: v for k, v in result.items() if k != "images"} if isinstance(result, dict) else result
            self.job.trace.append({
                "step": len(self.job.trace) + 1, "tool": name,
                "outcome": "error" if isinstance(result, dict) and result.get("ok") is False else "ok",
                "detail": (args.get("code", "") if name == "run_step" else json.dumps(public_result, ensure_ascii=False))[:1600],
            })
            await self.checkpoint()
            return result

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.connections.add(task)
        try:
            data = await asyncio.wait_for(reader.readline(), 30)
            if not data.endswith(b"\n") or len(data) > MAX_RPC_BYTES:
                raise ValueError("Malformed or excessive native RPC request")
            request = json.loads(data)
            result = await self.dispatch(request["operation"], request.get("payload", {}))
            response = {"ok": True, "result": result}
        except asyncio.CancelledError:
            writer.close()
            await writer.wait_closed()
            self.connections.discard(task)
            return
        except Exception as error:
            response = {"ok": False, "error": f"{type(error).__name__}: {error}"[:1600]}
        try:
            encoded = json.dumps(response, ensure_ascii=False, allow_nan=False).encode() + b"\n"
            if len(encoded) > MAX_RPC_BYTES:
                encoded = b'{"ok":false,"error":"Native RPC response exceeds limit"}\n'
            writer.write(encoded)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            self.connections.discard(task)


async def _drain(stream, tail):
    while data := await stream.read(8192):
        tail.append(data.decode(errors="replace"))
        if len(tail) > 8:
            del tail[0]


async def run_agentic(job, config, callbacks: AgenticCallbacks):
    """Run one segment; leave status/publication decisions to ``ai_editor``.

    Required private config: ``agentic_upstream`` (pinned checkout),
    ``render_python`` (environment with upstream dependencies). No new backend
    dependency is required. The parent model callback owns all model budgets.
    """
    upstream = Path(config["agentic_upstream"]).resolve(strict=True)
    # Preserve a virtualenv's executable symlink: resolving it would select the
    # base interpreter and lose the environment containing MCP/OpenAI/VTK.
    python = Path(config["render_python"]).absolute()
    if not python.is_file():
        raise ValueError("Configured research Python executable is missing")
    if not (upstream / "src/vlms/base_vlm.py").is_file():
        raise ValueError("Configured AgenticCADedit checkout has no official loop")
    application = Path(__file__).parent
    # Short pathname also meets macOS sockaddr_un's path-length limit. The
    # temporary directory is private (0700); the socket and specs are 0600.
    with tempfile.TemporaryDirectory(prefix="rcad-", dir="/tmp") as folder:
        directory = Path(folder)
        output = directory / "exports"
        output.mkdir(mode=0o700)
        socket_path = directory / "bridge.sock"
        bridge = AgenticBridge(job, callbacks, output)
        server = await asyncio.start_unix_server(bridge.handle, path=socket_path, limit=MAX_RPC_BYTES + 1)
        os.chmod(socket_path, 0o600)
        wrapper = directory / "native_mcp.py"
        wrapper.write_text(
            "import sys\nsys.path.insert(0, " + repr(str(application)) + ")\n"
            "from ai_agentic_mcp import main\nmain(" + repr(str(socket_path)) + ")\n"
        )
        wrapper.chmod(0o600)
        spec_path = directory / "runner.json"
        spec_path.write_text(json.dumps({
            "socket": str(socket_path), "upstream": str(upstream),
            "mcp_script": str(wrapper), "output_dir": str(output),
            "instruction": job.instruction, "prompt": NATIVE_PROMPT,
            "messages": job.messages, "max_calls": max(1, min(int(config.get("max_calls", 20)), 100)),
        }, ensure_ascii=False))
        spec_path.chmod(0o600)
        process = None
        drains = []
        tail = []
        try:
            # This subprocess has no provider key or API/Neo4j environment.
            environment = {k: v for k, v in os.environ.items()
                           if k in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT", "DISPLAY"}}
            environment["PYTHONUNBUFFERED"] = "1"
            process = await asyncio.create_subprocess_exec(
                str(python), str(application / "ai_agentic_runner.py"), str(spec_path),
                env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            drains = [asyncio.create_task(_drain(process.stdout, tail)),
                      asyncio.create_task(_drain(process.stderr, tail))]
            wait_task = asyncio.create_task(process.wait())
            while process.returncode is None:
                if getattr(job, "cancelled", False):
                    raise asyncio.CancelledError("Native agent cancelled")
                try:
                    await asyncio.wait_for(asyncio.shield(wait_task), 0.25)
                except asyncio.TimeoutError:
                    continue
            await asyncio.gather(*drains)
            if bridge.fatal is not None:
                raise bridge.fatal
            if process.returncode or bridge.completed is None:
                job.trace.append({"step": len(job.trace) + 1, "tool": "official_runner", "outcome": "error",
                                  "detail": "".join(tail)[-1600:]})
                raise RuntimeError("Official AgenticCADedit runner failed; native trace retained")
            return {
                **bridge.completed, "framework": "AgenticCADedit",
                "upstream_revision": UPSTREAM_REVISION, "adapter": ADAPTER_VERSION,
                "step_exported": bridge.step_exported,
            }
        finally:
            if process is not None and process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except asyncio.TimeoutError:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.wait()
            server.close()
            await server.wait_closed()
            for task in list(bridge.connections):
                task.cancel()
            if bridge.connections:
                await asyncio.gather(*list(bridge.connections), return_exceptions=True)
            for task in drains:
                if not task.done():
                    task.cancel()
            if drains:
                await asyncio.gather(*drains, return_exceptions=True)
            await bridge.checkpoint()
