"""Agentic inspect/execute/render/undo loop over isolated ACIS scene manifests.

Only apply() publishes a version. Geometry passes through the existing native
command contract. The optional official agent uses a bounded CAD facade.
"""

from __future__ import annotations
import asyncio
import base64
import copy
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

from .ai_budget import SharedBudget, BudgetExceeded
from .ai_messages import close_tool_messages
from .cad_contract import ADVANCED_COMMAND_SCHEMAS, validate_query
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
FRAMEWORKS = {"native", "agenticcadedit-acis"}


def output_limit(config):
    default = 8192 if config.get("framework") == "agenticcadedit-acis" else 2048
    limit = config.get("max_tokens", default)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 256 <= limit <= 40960:
        raise ValueError("max_tokens must be an integer between 256 and 40960")
    return limit


def configuration():
    if not settings.ai_config:
        raise HTTPException(503, "AI 编辑未配置，请设置 CAD_DB_AI_CONFIG")
    config = json.loads(Path(settings.ai_config).read_text())
    if config.get("framework", "native") not in FRAMEWORKS:
        raise HTTPException(503, "Unknown configured AI-CAD framework")
    output_limit(config)
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
    config["budget"] = SharedBudget(config["ledger"], config["limit_cny"], config["pricing"])
    return config


def availability():
    try:
        config = configuration()
        advanced = config.get("framework", "native") == "agenticcadedit-acis"
        if advanced and not (Path(config.get("agentic_upstream", "")) / "src/vlms/base_vlm.py").is_file():
            raise ValueError("Pinned AgenticCADedit checkout is missing")
        return {
            "enabled": True,
            "model": config["pricing"]["model"],
            "framework": config.get("framework", "native"),
            "framework_label": "AgenticCADedit · ACIS" if advanced else "原生基础编辑",
            "budget": config["budget"].summary(),
            "capabilities": (["精确几何查询", "面边定位与高亮", "拉伸/旋转/扫掠", "圆角/倒角", "连续对话"] if advanced else []) + [
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
    framework: str = "native"
    parent_job_id: str | None = None
    conversation_id: str = ""
    agentic_state: dict = field(default_factory=dict)
    provider_id: str | None = None
    mode: str = "edit"
    camera: dict | None = None

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
            "can_resume": self.status == "paused" and self.pause_reason in {"round_limit", "budget_limit", "no_tool_call"} and not self.cancelled,
            "cancel_requested": self.cancelled,
            "estimated_cny": self.cost,
            "version": self.version,
            "framework": self.framework,
            "parent_job_id": self.parent_job_id,
            "conversation_id": self.conversation_id or self.id,
            "provider_id": self.provider_id,
            "mode": self.mode,
            "scene": {
                "project_id": self.project,
                "version": self.base or 0,
                "components": [c.to_scene_json() for c in self.manifest],
            }
            if self.status in ("paused", "ready", "applied", "completed")
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


def resolve_runtime_config(config, provider_id, model, *, require_vlm=True):
    """Select pinned prices while preserving the authorization's total limit."""
    from . import ai_provider_store as store

    if not provider_id and not model:
        return config
    if not model:
        raise HTTPException(422, "自定义 provider 必须同时选择模型")
    if provider_id == store.DEFAULT_ID and model == config["pricing"]["model"]:
        return config
    provider_id = provider_id or store.DEFAULT_ID
    record = store.get_provider(provider_id)
    entry = ((record or {}).get("models") or {}).get(model)
    if require_vlm and (not entry or entry.get("vlm") is not True):
        raise HTTPException(422, f"模型 {model} 未通过 VLM 探测，不能选择；请先在设置里检测")
    if not entry or "input_cny_per_million" not in entry or "output_cny_per_million" not in entry:
        raise HTTPException(422, f"模型 {model} 缺少已填单价，不能开始付费任务")
    if provider_id == store.DEFAULT_ID:
        base_url, api_key, limit = config["base_url"].rstrip("/"), config["api_key"], None
    else:
        if not record.get("api_key"):
            raise HTTPException(422, "该 provider 缺少 API key")
        base_url, api_key = record["base_url"], record["api_key"]
        limit = float(record.get("budget_limit_cny", 100))
    pricing = {"model": model,
               "input_cny_per_million": float(entry["input_cny_per_million"]),
               "output_cny_per_million": float(entry["output_cny_per_million"]),
               "context_tokens": int(entry.get("context_tokens", 262144)),
               "source": f"user provider {provider_id}"}
    runtime = dict(config)
    runtime.update(base_url=base_url, api_key=api_key, pricing=pricing,
                   budget=SharedBudget(config["budget"].path, config["budget"].limit,
                                       pricing, scope=provider_id, scope_limit=limit))
    return runtime


async def start(project, base, instruction, selected, parent_job_id=None,
                images=None, provider_id=None, model=None, mode="edit"):
    config = configuration()
    config = resolve_runtime_config(config, provider_id, model)
    framework = config.get("framework", "native")
    if mode not in {"edit", "inspect"} or (mode == "inspect" and framework != "agenticcadedit-acis"):
        raise HTTPException(422, "只读检查需要官方 AgenticCADedit 框架")
    if images:
        if framework != "agenticcadedit-acis":
            raise HTTPException(422, "图片输入目前仅在官方 AgenticCADedit 框架下可用")
        from . import ai_provider_store as store
        try:
            images = store.decode_data_urls(images)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
    check_running(project)
    head, components = _head(project)
    if base != head:
        raise HTTPException(409, {"reason": "stale_base_version", "current_head": head})
    if set(selected) - {c.hash_sat for c in components}:
        raise HTTPException(422, "所选组件不属于当前版本")
    parent = None
    if parent_job_id:
        parent = find_job(project, parent_job_id)
        if parent.status not in {"applied", "completed", "failed", "cancelled"} or parent.framework != "agenticcadedit-acis" or framework != parent.framework:
            raise HTTPException(409, "只有已终结的 AgenticCADedit 阶段可作为连续对话的父阶段")
        if not parent.messages:
            raise HTTPException(409, "父阶段缺少完整对话记录")
    make_room()
    job = Job(
        uuid4().hex, project, base, instruction, selected, components, list(components)
    )
    job.trace_dir = config.get("trace_dir")
    job.model = config["pricing"]["model"]
    job.ledger = str(config["budget"].path.resolve())
    job.framework = framework
    job.provider_id = provider_id or None
    job.mode = mode
    job.conversation_id = parent.conversation_id or parent.id if parent else job.id
    if parent:
        job.camera = copy.deepcopy(parent.camera)
        job.parent_job_id = parent.id
        job.messages = close_tool_messages(parent.messages, allow_unknown=True)
        # Keep source history, but never carry native handles or undo snapshots
        # over an intervening human edit. Current SAT assets define the new stage.
        job.messages.append({"role": "user", "content":
            f"新的人工-agent编辑阶段。旧阶段状态={parent.status}，base={parent.base}，已应用版本={parent.version}；"
            f"当前正式版本={base}。请重新检查当前几何，旧组件引用不保证有效。"
            "未应用的旧预览不属于当前模型；中断工具不得自动重放。"
            f"保留仍生效的要求，按本次要求更新；本阶段要求：{instruction}"})
        job.trace.append({"step": 1, "tool": "conversation", "outcome": "ok",
                          "detail": f"继承父阶段 {parent.id} 的对话；在当前版本 {base} 上重新建立几何绑定"})
    if images:
        # The official loop seeds from job.messages; a multimodal user message puts
        # the reference images into the very context the upstream loop manages.
        content = [{"type": "text", "text": instruction +
                    "\n随指令附有参考图片（图纸/照片，均为需求参考，不是当前模型几何）。"
                    "先检查当前场景，再结合图片制定建模步骤并验证。"}]
        content += [{"type": "image_url", "image_url": {"url": image}} for image in images]
        job.messages.append({"role": "user", "content": content})
        job.trace.append({"step": len(job.trace) + 1, "tool": "images", "outcome": "ok",
                          "detail": f"附带 {len(images)} 张参考图片进入官方循环上下文"})
    if framework == "agenticcadedit-acis":
        permission = ("只读检查，禁止任何几何写操作（包括临时几何）；查询、报告并 finish。"
                      if mode == "inspect" else "编辑预览，允许按本阶段要求调用几何工具；先前阶段的只读模式已结束。")
        job.messages.append({"role": "user", "content": f"当前阶段权限：{permission}\n当前阶段要求：{instruction}"})
    jobs[job.id] = job
    job.task = asyncio.create_task(run(job, config))
    return job


async def resume(job, instruction=""):
    # A paused status may become visible while finally is still releasing its worker.
    if job.status == "paused" and job.task and not job.task.done():
        await asyncio.shield(job.task)
    if job.status != "paused" or job.pause_reason not in {"round_limit", "budget_limit", "no_tool_call"} or job.cancelled:
        raise HTTPException(409, "此任务不能继续；只有已暂停的未完成预览可以续作")
    if job.pause_reason == "no_tool_call" and not instruction.strip():
        raise HTTPException(422, "模型停止调用工具；请先回答其问题或补充要求，再继续同一任务")
    head, _ = _head(job.project)
    if head != job.base:
        raise HTTPException(409, {"reason": "stale_base_version", "current_head": head})
    check_running(job.project)
    config = resolve_runtime_config(configuration(), job.provider_id,
                                    job.model if job.provider_id else None)
    if (job.model != config["pricing"]["model"] or
            job.ledger != str(config["budget"].path.resolve()) or job.framework != config.get("framework", "native")):
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
    reservation = (pricing["context_tokens"] * pricing["input_cny_per_million"] + output_limit(config) * pricing["output_cny_per_million"]) / 1e6
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

    if getattr(job, "mode", "edit") == "inspect":
        raise ValueError("本阶段只读，禁止几何写操作；请仅查询并汇报检查结果")
    _validate(action, params)
    if action in ADVANCED_COMMAND_SCHEMAS:
        if _referenced_components(action, params) - {c.hash_sat for c in job.manifest}:
            raise ValueError("Unknown component ID; inspect current geometry")
        return
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
    if job.framework == "agenticcadedit-acis":
        await run_official(job, config)
        return
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


def _finalize_official(job, result):
    """Map one finished official segment onto the native job states."""
    summary = str(result.get("summary") or "").strip()
    job.summary = summary[:4000]
    if result.get("finished") and result.get("success"):
        if getattr(job, "mode", "edit") == "inspect":
            job.status = "completed"
            job.version = getattr(job, "base", None)
        elif [c.hash_sat for c in job.manifest] == [c.hash_sat for c in job.original]:
            job.status = "failed"
            job.summary = ("模型声明完成但预览几何没有任何变化；按未支持处理，未发布版本。"
                           + summary[:2000])
        else:
            job.status = "ready"
    elif result.get("finished"):
        job.status = "failed"
    else:
        job.status, job.pause_reason = "paused", str(result.get("exit_reason") or "round_limit")
        job.summary = ("本段未显式完成（" + job.pause_reason + "）。预览与完整对话已保留，"
                       "可继续同一任务；累计费用不重置，正式模型未修改。" + summary[:2000])


def _world_frame(job):
    """Fixed camera frame from the job's scenes: original ∪ current preview."""
    if getattr(job, "camera", None):
        return copy.deepcopy(job.camera)
    boxes = [c for c in list(job.original) + list(job.manifest)
             if c.bbox_min and c.bbox_max]
    if not boxes:
        return {"center": [0.0, 0.0, 0.0], "size": 10.0}
    lows = [min(c.bbox_min[i] for c in boxes) for i in range(3)]
    highs = [max(c.bbox_max[i] for c in boxes) for i in range(3)]
    size = max(highs[i] - lows[i] for i in range(3)) or 1.0
    job.camera = {"center": [(lows[i] + highs[i]) / 2 for i in range(3)], "size": size}
    return copy.deepcopy(job.camera)


async def _map_selection_highlights(query, components, highlight_faces, highlight_edges):
    """Resolve highlight centres to real worker selections; never draw markers."""
    requested = ([("faces", point) for point in highlight_faces] +
                 [("edges", point) for point in highlight_edges])
    if not requested:
        return []
    catalog = []
    for component in components:
        for kind in ("faces", "edges"):
            listing = await query({"kind": kind, "component": component,
                                   "limit": 1000, "offset": 0})
            for item in listing.get("items", []):
                center = item.get("center")
                if isinstance(center, dict):
                    center = [center.get(k, 0.0) for k in ("x", "y", "z")]
                if isinstance(center, list) and len(center) == 3:
                    catalog.append((kind, [float(v) for v in center], component,
                                    int(item.get("index"))))
    picks = {}
    for kind, point in requested:
        best = None
        ambiguous = False
        for entry_kind, center, component, index in catalog:
            if entry_kind != kind:
                continue
            distance = sum((a - b) ** 2 for a, b in zip(center, point))
            if best is None or distance < best[0]:
                best = (distance, component, index)
                ambiguous = False
            elif abs(distance-best[0]) <= 1e-12:
                ambiguous = True
        if best is None or best[0] > 1e-6:
            raise ValueError("高亮中心与当前拓扑不匹配；重新 list_faces/list_edges 获取当前中心后再渲染")
        if ambiguous:
            raise ValueError("多个实体具有相同高亮中心，不能猜测选区；请明确定位后再操作")
        picks.setdefault((best[1], kind), []).append(best[2])
    selections = []
    for (component, kind), indices in picks.items():
        result = await query({"kind": "render_selection", "component": component,
                              "faces" if kind == "faces" else "edges": sorted(set(indices))})
        selections.extend(result.get("selections", []))
    return selections


async def official_model(job, config, messages, tool_specs):
    """One accounted provider request; the parent owns credentials and budget."""
    if job.cancelled:
        raise asyncio.CancelledError("Native AI job cancelled")
    if job.cost >= float(config.get("job_soft_limit_cny", 4)):
        raise BudgetExceeded("本任务累计费用上限已到")
    index = config["budget"].reserve(
        config["pricing"]["model"], output_limit(config), "native-ai/" + job.id
    )
    await asyncio.to_thread(config["budget"].wait_for_slot)
    if job.cancelled:
        raise asyncio.CancelledError("Native AI job cancelled")
    async with httpx.AsyncClient(timeout=float(config.get("model_timeout", 300))) as client:
        response = await client.post(
            config["base_url"].rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + config["api_key"]},
            json={
                "model": config["pricing"]["model"],
                "messages": messages,
                "tools": tool_specs,
                "tool_choice": "auto",
                "max_tokens": output_limit(config),
                "temperature": float(config.get("temperature", 0.6)),
                "top_p": float(config.get("top_p", 0.95)),
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
    usage = data["usage"]
    job.cost += (
        usage.get("prompt_tokens", 0) * config["pricing"]["input_cny_per_million"]
        + usage.get("completion_tokens", 0) * config["pricing"]["output_cny_per_million"]
    ) / 1e6
    return data


async def run_official(job, config):
    """One official AgenticCADedit segment over native ACIS callbacks."""
    from .ai_agentic import AgenticCallbacks, run_agentic

    job.segment_limit = max(1, min(int(config.get("max_calls", 20)), 100))
    job.segment_calls = 0

    async def execute(action, params):
        validate_command(action, params, job)
        return await native_command(job, action, params)

    async def geometry_query(request):
        validate_query(request, {c.hash_sat for c in job.manifest})
        pool = get_worker_pool()
        try:
            return await asyncio.wait_for(
                pool.query("ai-" + job.id, len(job.trace) + 1, job.manifest, request),
                NATIVE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as error:
            await pool.release("ai-" + job.id)
            raise RuntimeError("几何查询超过60秒，候选 worker 已停止") from error

    async def render_views(views, highlight_faces, highlight_edges):
        if not job.manifest:
            raise ValueError("空预览没有可渲染的几何")
        assets = get_component_store()
        parts = [assets.read("sat", c.hash_sat) for c in job.manifest]
        if any(part is None for part in parts):
            raise ValueError("预览几何资产缺失，无法渲染")
        highlights = await _map_selection_highlights(
            geometry_query, [c.hash_sat for c in job.manifest],
            highlight_faces or [], highlight_edges or [])
        stl = await asyncio.to_thread(get_worker_gateway().export_stl, parts)
        with tempfile.TemporaryDirectory(prefix="rcad_render_") as folder:
            source = Path(folder) / "scene.stl"
            source.write_bytes(stl)
            spec = Path(folder) / "spec.json"
            spec.write_text(json.dumps({"views": views, "camera": _world_frame(job),
                                        "highlights": highlights}, ensure_ascii=False))
            prefix = Path(folder) / "view"
            process = await asyncio.create_subprocess_exec(
                config["render_python"],
                str(Path(__file__).with_name("ai_render_views.py")),
                str(source), str(spec), str(prefix),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), 90)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise RuntimeError("多视图渲染超时")
            images = sorted(Path(folder).glob("view*.png"))
            if process.returncode or len(images) != len(views):
                raise RuntimeError("多视图渲染失败：" + stderr.decode(errors="replace")[-300:])
            return [base64.b64encode(image.read_bytes()).decode() for image in images]

    async def model(messages, tool_specs):
        return await official_model(job, config, messages, tool_specs)

    async def export_step(output_dir):
        if not job.manifest:
            return {"ok": False, "error": "预览没有可导出的几何"}
        assets = get_component_store()
        parts = [assets.read("sat", c.hash_sat) for c in job.manifest]
        if any(part is None for part in parts):
            return {"ok": False, "error": "预览几何资产缺失，无法导出"}
        step = await asyncio.to_thread(get_worker_gateway().export_step, parts)
        destination = Path(output_dir) / "preview.step"
        destination.write_bytes(step)
        return {"ok": True, "step_path": str(destination), "bytes": len(step)}

    def checkpoint():
        persist(job)

    try:
        result = await run_agentic(job, config, AgenticCallbacks(
            execute=execute, query=geometry_query, render=render_views, model=model,
            export_step=export_step, checkpoint=checkpoint))
        _finalize_official(job, result)
        job.trace.append({
            "step": len(job.trace) + 1, "tool": "official_segment", "outcome": "ok",
            "detail": json.dumps({k: result.get(k) for k in (
                "finished", "success", "exit_reason", "framework", "adapter", "step_exported")},
                ensure_ascii=False),
        })
    except asyncio.CancelledError:
        job.status = "cancelled"
        job.summary = "已停止，未应用的预览已取消"
        raise
    except BudgetExceeded as error:
        job.status, job.pause_reason = "paused", "budget_limit"
        job.summary = str(error) + "；中间预览与完整对话已保留，可核对预算后继续，正式模型未修改"
    except Exception as error:
        job.status = "failed"
        job.summary = "官方框架段失败（" + type(error).__name__ + "），正式模型未修改"
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
        fields.update({key: payload.get(key, default) for key, default in (
            ("framework", "native"), ("parent_job_id", None), ("conversation_id", job_id),
            ("agentic_state", {}), ("provider_id", None), ("mode", "edit"), ("camera", None))})
        if fields["status"] == "running":
            fields.update(status="paused", pause_reason="interrupted",
                          summary="服务在执行中中断。已恢复最近检查点供审阅；费用或工具结果可能待核对，禁止自动重放。")
        if fields["status"] not in {"paused", "ready", "failed", "cancelled", "applied", "completed"}:
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
            # Persist running progress, but never auto-replay interrupted tools.
            payload = {key: getattr(job, key) for key in (
                "id", "project", "base", "instruction", "selected", "created", "status", "summary",
                "trace", "cancelled", "calls", "cost", "version", "messages", "segment_calls",
                "segment_limit", "pause_reason", "model", "ledger", "projections",
                "framework", "parent_job_id", "conversation_id", "agentic_state",
                "provider_id", "mode", "camera")}
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
