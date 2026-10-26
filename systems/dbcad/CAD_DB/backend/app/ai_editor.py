"""Agentic inspect/execute/render/undo loop over isolated ACIS scene manifests.

Only apply() publishes a version. Model output never becomes Python or a shell
command: all geometry passes through the existing native command contract.
"""

from __future__ import annotations
import asyncio
import base64
from dataclasses import asdict, dataclass, field
import json
import logging
import math
import os
from pathlib import Path
import tempfile
import time
from uuid import uuid4

import httpx
from fastapi import HTTPException

from .ai_budget import Budget, BudgetExceeded
from .command_result import manifest_from_result
from .component_store import get_component_store
from .config import settings
from .entity_projection import write_projections
from .events_hub import hub
from .v1_store import SceneComponent, get_v1_store
from .worker_client import get_worker_gateway
from .worker_pool import get_worker_pool

logger = logging.getLogger(__name__)
NATIVE_TIMEOUT_SECONDS = 60


def configuration():
    if not settings.ai_config:
        raise HTTPException(503, "AI 编辑未配置，请设置 CAD_DB_AI_CONFIG")
    config = json.loads(Path(settings.ai_config).read_text())
    if not config.get("base_url", "").startswith("https://"):
        raise HTTPException(503, "AI provider requires HTTPS")
    credentials = {}
    for line in Path(config["credentials_file"]).read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            credentials[key.strip()] = value.strip()
    if not credentials.get("CAD_API_KEY"):
        raise HTTPException(503, "AI provider key is missing")
    config["api_key"] = credentials["CAD_API_KEY"]
    config["budget"] = Budget(config["ledger"], config["limit_cny"], config["pricing"])
    return config


def availability():
    try:
        config = configuration()
        return {
            "enabled": True,
            "model": config["pricing"]["model"],
            "budget": config["budget"].summary(),
            "capabilities": [
                "图元创建",
                "平移/旋转/缩放",
                "布尔并/差/交",
                "删除",
                "预览与撤销",
            ],
        }
    except Exception:
        return {"enabled": False, "model": None, "budget": None, "capabilities": []}


@dataclass
class Job:
    id: str
    project: str
    base: int | None
    instruction: str
    selected: list[str]
    original: list
    manifest: list
    created: float = field(default_factory=time.time)
    status: str = "running"
    summary: str = ""
    trace: list = field(default_factory=list)
    history: list = field(default_factory=list)
    projections: list = field(default_factory=list)
    cancelled: bool = False
    calls: int = 0
    cost: float = 0.0
    version: int | None = None
    task: asyncio.Task | None = None
    trace_dir: str | None = None
    messages: list = field(default_factory=list)
    segment_calls: int = 0
    segment_limit: int = 20
    pause_reason: str | None = None
    model: str = ""
    ledger: str = ""

    def public(self):
        return {
            "id": self.id,
            "project_id": self.project,
            "base_version": self.base,
            "instruction": self.instruction,
            "status": self.status,
            "summary": self.summary,
            "trace": self.trace,
            "calls": self.calls,
            "segment_calls": self.segment_calls,
            "segment_limit": self.segment_limit,
            "pause_reason": self.pause_reason,
            "can_resume": self.status == "paused" and self.pause_reason in {"round_limit", "budget_limit"} and not self.cancelled,
            "cancel_requested": self.cancelled,
            "estimated_cny": self.cost,
            "version": self.version,
            "scene": {
                "project_id": self.project,
                "version": self.base or 0,
                "components": [c.to_scene_json() for c in self.manifest],
            }
            if self.status in ("paused", "ready", "applied")
            else None,
        }


jobs: dict[str, Job] = {}


def _head(project):
    head = get_v1_store().latest(project)
    return head.number if head else None, list(head.components) if head else []


def find_job(project, job_id):
    job = jobs.get(job_id)
    if job is None:
        job = restore_job(project, job_id)
        if job:
            make_room()
            jobs[job.id] = job
    if job is None or job.project != project:
        raise HTTPException(404, "AI job not found (no saved checkpoint for this job)")
    return job


def check_running(project):
    if sum(j.status == "running" for j in jobs.values()) >= 2:
        raise HTTPException(429, "已有两个 AI 编辑任务在运行")
    if any(j.project == project and j.status == "running" for j in jobs.values()):
        raise HTTPException(409, "此项目已有 AI 编辑任务")


def make_room():
    for key in list(jobs):
        if len(jobs) < 100:
            return
        if jobs[key].status in {"failed", "cancelled", "applied"}:
            jobs.pop(key)
    if len(jobs) >= 100:
        raise HTTPException(429, "未完成的 AI 预览过多，请先应用或放弃已有预览")


async def start(project, base, instruction, selected):
    config = configuration()
    check_running(project)
    head, components = _head(project)
    if base != head:
        raise HTTPException(409, {"reason": "stale_base_version", "current_head": head})
    if set(selected) - {c.hash_sat for c in components}:
        raise HTTPException(422, "所选组件不属于当前版本")
    make_room()
    job = Job(
        uuid4().hex, project, base, instruction, selected, components, list(components)
    )
    job.trace_dir = config.get("trace_dir")
    job.model = config["pricing"]["model"]
    job.ledger = str(config["budget"].path.resolve())
    jobs[job.id] = job
    job.task = asyncio.create_task(run(job, config))
    return job


async def resume(job, instruction=""):
    # A paused status may become visible while finally is still releasing its worker.
    if job.status == "paused" and job.task and not job.task.done():
        await asyncio.shield(job.task)
    if job.status != "paused" or job.pause_reason not in {"round_limit", "budget_limit"} or job.cancelled:
        raise HTTPException(409, "此任务不能继续；只有已暂停的未完成预览可以续作")
    head, _ = _head(job.project)
    if head != job.base:
        raise HTTPException(409, {"reason": "stale_base_version", "current_head": head})
    check_running(job.project)
    config = configuration()
    if (job.model != config["pricing"]["model"] or
            job.ledger != str(config["budget"].path.resolve())):
        raise HTTPException(409, "模型或预算账本已改变，不能把旧任务当成新额度继续")
    entries = config["budget"].update(lambda d: [c for c in d["calls"] if c["label"] == "native-ai/" + job.id])
    if any(c["status"] != "completed" for c in entries):
        raise HTTPException(409, "上次调用费用或结果尚未确认，请先核对账本，不能自动重试")
    job.cost = max(job.cost, sum(c["charged_or_reserved_cny"] for c in entries))
    job.calls = max(job.calls, len(entries))
    if job.cost >= float(config.get("job_soft_limit_cny", 4)):
        raise HTTPException(402, "本任务累计费用上限已到；继续不会重置费用额度")
    budget = config["budget"].summary()
    pricing = config["pricing"]
    reservation = (pricing["context_tokens"] * pricing["input_cny_per_million"] + 2048 * pricing["output_cny_per_million"]) / 1e6
    if budget["charged_or_reserved_cny"] + reservation > budget["limit_cny"]:
        raise HTTPException(402, "预算账本可用额度不足，本次未发起模型调用；请先核对预算")
    if not job.messages:
        raise HTTPException(409, "缺少完整对话检查点，不能伪装为原任务续作")
    assets = get_component_store()
    if any(assets.read("sat", c.hash_sat) is None for c in job.manifest):
        raise HTTPException(409, "预览的几何资产不完整，无法继续")
    job.messages.append({"role": "user", "content":
        "继续同一个未完成的 CAD 任务。当前预览和之前的工具结果均保留；先检查当前状态，"
        "在已有成果上继续，保留符合要求的部分，不要因为上一段用完调用次数就推倒重建。"
        "只有真正完成并验证原要求后才调用 finish；否则系统会保留进度再次暂停。"
        + ("\n本次补充要求：" + instruction.strip() if instruction.strip() else "")})
    job.trace.append({"step": len(job.trace) + 1, "tool": "resume", "outcome": "ok",
                      "detail": "继续同一任务，保留预览与累计费用" + ("；补充要求：" + instruction.strip() if instruction.strip() else "")})
    job.status, job.pause_reason, job.summary = "running", None, "正在从未完成预览继续"
    job.segment_calls = 0
    job.task = asyncio.create_task(run(job, config))
    return job


def inspect(job):
    assets = get_component_store()
    components = []
    for c in job.manifest:
        counts = {}
        if c.graph:
            payload = assets.read("graph", c.graph)
            if payload:
                graph = json.loads(payload)
                for node in graph.get("nodes", []):
                    kind = "/".join(node.get("labels", [])) or str(
                        node.get("type", "entity")
                    )
                    counts[kind] = counts.get(kind, 0) + 1
        components.append({**c.to_scene_json(), "entity_counts": counts})
    return {
        "components": components,
        "selected_at_start": job.selected,
        "undo_steps": len(job.history),
        "stage": "preview; no project version has been published",
    }


def validate_command(action, params, job):
    from .api.v1.commands import COMMAND_SCHEMAS, _validate, _referenced_components

    _validate(action, params)
    allowed = [
        "component.create",
        "component.translate",
        "component.rotate",
        "component.scale",
        "component.delete",
        "boolean.unite",
        "boolean.subtract",
        "boolean.intersect",
    ]
    if action not in allowed:
        raise ValueError("Unsupported AI action")
    schema = COMMAND_SCHEMAS[action]["params"]
    if set(params) - set(schema):
        raise ValueError("Unknown command parameters")
    for key, value in params.items():
        kind = schema[key]["type"]
        if kind in ("number", "integer"):
            if (
                isinstance(value, bool)
                or not isinstance(value, (float, int))
                or not math.isfinite(value)
                or abs(value) > 1e6
            ):
                raise ValueError(f"{key}: finite numeric value required")
            if kind == "integer" and not isinstance(value, int):
                raise ValueError(f"{key}: integer required")
        if kind.startswith("array<number>"):
            if (
                not isinstance(value, list)
                or len(value) != 3
                or any(
                    isinstance(v, bool)
                    or not isinstance(v, (int, float))
                    or not math.isfinite(v)
                    or abs(v) > 1e6
                    for v in value
                )
            ):
                raise ValueError(f"{key}: three finite coordinates required")
        if kind.startswith("array<string>") and (
            not isinstance(value, list)
            or len(value) != 1
            or any(not isinstance(v, str) for v in value)
        ):
            raise ValueError("Exactly one target component is required")
        if kind == "string" and not isinstance(value, str):
            raise ValueError(f"{key}: string required")
    if action == "component.create":
        if params["kind"] not in ["cube", "sphere", "cylinder", "torus", "cone"]:
            raise ValueError("Only solid primitives are supported by this AI adapter")
        if len(job.manifest) >= 128:
            raise ValueError("Preview component limit reached")
        if any(
            params.get(k, 1) <= 0
            for k in [
                "width",
                "height",
                "depth",
                "radius",
                "major_radius",
                "minor_radius",
                "sides",
            ]
        ):
            raise ValueError("Primitive dimensions must be positive")
    if action == "component.scale" and params["factor"] <= 0:
        raise ValueError("Scale must be positive")
    if _referenced_components(action, params) - {c.hash_sat for c in job.manifest}:
        raise ValueError("Unknown component ID; call inspect after every edit")


async def render(job, config):
    if not job.manifest:
        return None
    parts = [get_component_store().read("sat", c.hash_sat) for c in job.manifest]
    stl = await asyncio.to_thread(get_worker_gateway().export_stl, parts)
    with tempfile.TemporaryDirectory(prefix="inferracad_ai_render_") as folder:
        source, target = Path(folder) / "scene.stl", Path(folder) / "scene.png"
        source.write_bytes(stl)
        process = await asyncio.create_subprocess_exec(
            config["render_python"],
            str(Path(__file__).with_name("ai_render.py")),
            str(source),
            str(target),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), 90)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError("Preview render timed out")
        if process.returncode or not target.exists():
            raise RuntimeError("Preview renderer unavailable")
        return base64.b64encode(target.read_bytes()).decode()


async def native_command(job, action, params):
    pool = get_worker_pool()
    try:
        return await asyncio.wait_for(
            pool.execute(
                "ai-" + job.id,
                len(job.history) + 1,
                job.manifest,
                {"action": action, "params": params},
            ),
            NATIVE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as error:
        await pool.release("ai-" + job.id)
        raise RuntimeError(
            "几何操作超过60秒，候选 worker 已停止；上一步预览保持不变"
        ) from error


def tools():
    def function(name, description, properties, required=()):
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": list(required),
                    "additionalProperties": False,
                },
            },
        }

    return [
        function(
            "inspect",
            "Inspect current exact CAD components, IDs, bounds and entity counts.",
            {},
        ),
        function(
            "run_command",
            "Execute one native CAD command in the preview. Refresh IDs with inspect after edits.",
            {"action": {"type": "string"}, "params": {"type": "object"}},
            ["action", "params"],
        ),
        function(
            "render",
            "Render three views of current preview for visual verification.",
            {},
        ),
        function("undo", "Restore the previous immutable preview manifest.", {}),
        function(
            "finish",
            "Finish with a Chinese explanation of the completed changes and any limitations.",
            {
                "summary": {"type": "string"},
                "success": {
                    "type": "boolean",
                    "description": "false if the requested operation is unsupported or cannot be completed",
                },
            },
            ["summary"],
        ),
    ]


async def run(job, config):
    from .api.v1.commands import COMMAND_SCHEMAS

    schemas = {
        k: v
        for k, v in COMMAND_SCHEMAS.items()
        if k.startswith(("component.", "boolean."))
    }
    prompt = (
        "You are InferraCAD, a CAD editing assistant. Work on the existing model using native tools. "
        "All components are exact ACIS geometry. Never invent component IDs; inspect after each edit. "
        "Only primitive creation, transforms, deletion, and booleans are supported. No fillet/chamfer/extrude tools. "
        "Do not approximate unsupported edits or claim success on an unchanged model. "
        "Respect dimensions and preserve unrelated components. Inspect first, then edit and render to verify. "
        "When satisfied call finish; call finish with success=false if unsupported or not possible. Explain limitations in Chinese. Changes are previews until the human applies them. "
        "Native primitive semantics (important): primitives are CENTERED at position, default [0,0,0]. "
        "Cube width is X, height is Y, depth is Z. Cylinder/cone height is Z and radius is XY, centered at position. "
        "Only cube/sphere/cylinder/cone/torus creation is supported in this adapter. "
        "Tool schemas: " + json.dumps(schemas, ensure_ascii=False)
    )
    if not job.messages:
        job.messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": job.instruction + "\nInitial scene: "
             + json.dumps(inspect(job), ensure_ascii=False)},
        ]
    messages = job.messages
    job.segment_limit = max(1, min(int(config.get("max_calls", 20)), 40))
    job.segment_calls = 0
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            for turn in range(job.segment_limit):
                if job.cancelled:
                    job.status = "cancelled"
                    job.summary = "已停止，正式模型未修改"
                    return
                if job.cost >= float(config.get("job_soft_limit_cny", 4)):
                    raise BudgetExceeded("本任务累计费用上限已到")
                index = config["budget"].reserve(
                    config["pricing"]["model"], 2048, "native-ai/" + job.id
                )
                await asyncio.to_thread(config["budget"].wait_for_slot)
                if job.cancelled:
                    job.status = "cancelled"
                    job.summary = "已停止，正式模型未修改"
                    return
                response = await client.post(
                    config["base_url"].rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + config["api_key"]},
                    json={
                        "model": config["pricing"]["model"],
                        "messages": messages,
                        "tools": tools(),
                        "tool_choice": "auto",
                        "max_tokens": 2048,
                        "temperature": 0.3,
                        "chat_template_kwargs": {"enable_thinking": True},
                    },
                )
                response.raise_for_status()
                data = response.json()
                job.calls += 1
                job.segment_calls += 1
                config["budget"].settle(index, data.get("usage"), data.get("model"))
                if not data.get("usage"):
                    raise BudgetExceeded("本次调用缺少费用信息，已保留预算预留；请核对后处理")
                usage = data.get("usage") or {}
                job.cost += (
                    usage.get("prompt_tokens", 0)
                    * config["pricing"]["input_cny_per_million"]
                    + usage.get("completion_tokens", 0)
                    * config["pricing"]["output_cny_per_million"]
                ) / 1e6
                if job.cancelled:
                    job.status = "cancelled"
                    job.summary = "已停止，正式模型未修改"
                    return
                message = data["choices"][0]["message"]
                messages.append(
                    {
                        k: v
                        for k, v in message.items()
                        if k in ("role", "content", "tool_calls")
                    }
                )
                calls = message.get("tool_calls", [])
                if not calls:
                    if message.get("content"):
                        job.trace.append(
                            {
                                "step": len(job.trace) + 1,
                                "tool": "assistant",
                                "detail": message["content"][:1600],
                            }
                        )
                    messages.append(
                        {
                            "role": "user",
                            "content": "Use tools to inspect/edit/render, then call finish; report unsupported requests honestly.",
                        }
                    )
                    continue
                for call in calls:
                    if job.cancelled:
                        job.status = "cancelled"
                        job.summary = "已停止，正式模型未修改"
                        return
                    name = call["function"]["name"]
                    detail = ""
                    image = None
                    finished = False
                    try:
                        args = json.loads(call["function"].get("arguments", "{}"))
                        if not isinstance(args, dict):
                            raise ValueError("Arguments must be an object")
                        if name == "inspect":
                            result = inspect(job)
                        elif name == "run_command":
                            action = args["action"]
                            params = args["params"]
                            if not isinstance(params, dict):
                                raise ValueError("params must be an object")
                            validate_command(action, params, job)
                            native = await native_command(job, action, params)
                            manifest, projections, _, _ = manifest_from_result(
                                job.manifest, native
                            )
                            job.history.append(list(job.manifest))
                            job.manifest = manifest
                            job.projections.extend(projections)
                            result = inspect(job)
                            detail = (
                                action + " " + json.dumps(params, ensure_ascii=False)
                            )
                        elif name == "undo":
                            if not job.history:
                                raise ValueError("No preview step to undo")
                            job.manifest = job.history.pop()
                            result = inspect(job)
                        elif name == "render":
                            image = await render(job, config)
                            result = {
                                "rendered": bool(image),
                                "views": "isometric, front, top",
                            }
                        elif name == "finish":
                            if args.get("success", True) and [
                                c.hash_sat for c in job.manifest
                            ] == [c.hash_sat for c in job.original]:
                                raise ValueError(
                                    "No geometry change. Explain unsupported request instead of claiming success."
                                )
                            job.summary = str(args.get("summary", ""))[:4000]
                            job.status = (
                                "ready" if args.get("success", True) else "failed"
                            )
                            result = {"ready": job.status == "ready"}
                            finished = True
                        else:
                            raise ValueError("Unknown tool")
                        outcome = "ok"
                    except Exception as error:
                        result = {"error": str(error)[:600]}
                        outcome = "error"
                    job.trace.append(
                        {
                            "step": len(job.trace) + 1,
                            "tool": name,
                            "outcome": outcome,
                            "detail": detail
                            or json.dumps(result, ensure_ascii=False)[:1600],
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
                    if image:
                        messages.append(
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "Current preview, verify against the request.",
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": "data:image/png;base64," + image
                                        },
                                    },
                                ],
                            }
                        )
                    if finished:
                        return
            job.status, job.pause_reason = "paused", "round_limit"
            job.summary = (f"本段 {job.segment_limit} 次模型调用已用完，未完成预览已保留。"
                           "可查看中间模型并继续同一任务；累计费用不会重置，正式模型未修改。")
    except asyncio.CancelledError:
        job.status = "cancelled"
        job.summary = "服务停止，未应用的预览已取消"
        raise
    except BudgetExceeded as error:
        job.status, job.pause_reason = "paused", "budget_limit"
        job.summary = str(error) + "；中间预览已保留，正式模型未修改"
    except Exception as error:
        job.status = "failed"
        job.summary = "AI 服务失败（" + type(error).__name__ + "），正式模型未修改"
    finally:
        try:
            await get_worker_pool().release("ai-" + job.id)
        finally:
            persist(job)


def _atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _checkpoint_path(folder, job_id):
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise ValueError("Invalid checkpoint ID")
    return Path(folder) / "checkpoints" / (job_id + ".json")


def restore_job(project, job_id):
    if not settings.ai_config:
        return None
    try:
        folder = json.loads(Path(settings.ai_config).read_text()).get("trace_dir")
        if not folder:
            return None
        path = _checkpoint_path(folder, job_id)
        if not path.exists():
            # Legacy failed traces contain neither full messages nor geometry.
            # Keep their audit trail visible, but never fabricate a resumable job.
            legacy = json.loads((Path(folder) / (job_id + ".json")).read_text())
            if legacy.get("project_id") != project or legacy.get("status") not in {"failed", "cancelled"}:
                return None
            return Job(job_id, project, legacy.get("base_version"), legacy.get("instruction", ""), [], [], [],
                       status=legacy["status"], calls=legacy.get("calls", 0), cost=legacy.get("estimated_cny", 0),
                       trace=legacy.get("trace", []), summary=legacy.get("summary", "") +
                       "（旧版本仅保存过程记录，未保存完整对话检查点，无法原样续接。）")
        payload = json.loads(path.read_text())
        if (payload.get("schema_version") != 1 or payload.get("id") != job_id or
                payload.get("project") != project):
            return None
        fields = {key: payload[key] for key in (
            "id", "project", "base", "instruction", "selected", "created", "status", "summary",
            "trace", "cancelled", "calls", "cost", "version", "messages", "segment_calls",
            "segment_limit", "pause_reason", "model", "ledger")}
        if fields["status"] not in {"paused", "ready", "failed", "cancelled", "applied"}:
            return None
        fields["original"] = [SceneComponent(**c) for c in payload["original"]]
        fields["manifest"] = [SceneComponent(**c) for c in payload["manifest"]]
        fields["history"] = [[SceneComponent(**c) for c in stage] for stage in payload["history"]]
        fields["projections"] = payload["projections"]
        fields["trace_dir"] = folder
        return Job(**fields)
    except (OSError, ValueError, KeyError, TypeError) as error:
        logger.warning("AI checkpoint unavailable (%s)", type(error).__name__)
        return None


def persist(job):
    if job.trace_dir:
        try:
            folder = Path(job.trace_dir)
            _atomic_json(folder / (job.id + ".json"), job.public())
            if job.status != "running":
                payload = {key: getattr(job, key) for key in (
                    "id", "project", "base", "instruction", "selected", "created", "status", "summary",
                    "trace", "cancelled", "calls", "cost", "version", "messages", "segment_calls",
                    "segment_limit", "pause_reason", "model", "ledger", "projections")}
                payload.update(schema_version=1,
                    original=[asdict(c) for c in job.original], manifest=[asdict(c) for c in job.manifest],
                    history=[[asdict(c) for c in stage] for stage in job.history])
                _atomic_json(_checkpoint_path(folder, job.id), payload)
        except (OSError, ValueError) as error:
            logger.warning("AI trace could not be saved (%s)", type(error).__name__)
            if job.status in {"paused", "ready"} and not any(t.get("tool") == "checkpoint" for t in job.trace):
                job.trace.append({"step": len(job.trace) + 1, "tool": "checkpoint", "outcome": "error",
                                  "detail": "部分任务记录写盘失败；当前预览仍在内存，请勿重启服务，先继续或应用并处理存储问题。"})


def apply(job, author):
    if job.status != "ready":
        raise HTTPException(409, "AI preview is not ready to apply")
    head, _ = _head(job.project)
    if head != job.base:
        raise HTTPException(409, {"reason": "stale_base_version", "current_head": head})
    store = get_v1_store()
    version = store.commit_version(
        job.project,
        message="AI: " + job.instruction[:160],
        author=author,
        components=job.manifest,
        base_version=job.base,
    )
    store.clear_redo(job.project)
    final = {c.hash_sat for c in job.manifest}
    original = {c.hash_sat for c in job.original}
    # Preview steps have no published versions. Collapse their ancestry to the
    # captured base component so final entities retain useful generation links.
    ancestry = {}
    collapsed = {}
    for digest, graph, parent in job.projections:
        ancestor = ancestry.get(parent, parent)
        ancestry[digest] = ancestor
        if digest in final:
            collapsed[digest] = (
                digest,
                graph,
                ancestor if ancestor in original and ancestor != digest else None,
            )
    try:
        write_projections(store, list(collapsed.values()))
    except Exception as error:
        logger.warning(
            "AI version committed; derived projection failed (%s)", type(error).__name__
        )
        job.trace.append(
            {
                "step": len(job.trace) + 1,
                "tool": "entity_projection",
                "outcome": "warning",
                "detail": "SAT 版本已提交，派生实体图投影待恢复："
                + type(error).__name__,
            }
        )
    job.status = "applied"
    job.version = version.number
    persist(job)
    hub.publish(
        job.project,
        "version.committed",
        {"version": version.number, "message": version.message, "author": author},
    )
    return job.public()


async def shutdown():
    pending = [j.task for j in jobs.values() if j.task and not j.task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
