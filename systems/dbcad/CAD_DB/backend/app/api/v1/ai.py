"""Authenticated asynchronous AI previews and explicit version publication."""


import asyncio
import json
from typing import Literal
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from ... import ai_editor
from ... import ai_provider_store as store
from ...ai_budget import BudgetExceeded
from ...dependencies import verify_api_password
from .projects import _project_or_404

router = APIRouter(dependencies=[Depends(verify_api_password)])


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(min_length=2, max_length=6000)
    base_version: StrictInt | None = Field(..., ge=1)
    selected: list[str] = Field(default_factory=list, max_length=128)
    parent_job_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    images: list[StrictStr] = Field(default_factory=list, max_length=3)
    provider_id: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=120)
    mode: Literal["edit", "inspect"] = "edit"


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    author: str = Field(default="AI editor", min_length=1, max_length=120)


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(default="", max_length=6000)


class ProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(default=None, max_length=40)
    label: str | None = Field(default=None, max_length=80)
    base_url: str = Field(min_length=12, max_length=300)
    api_key: str | None = Field(default=None, max_length=400)
    budget_limit_cny: float | None = Field(default=None, gt=0, le=10000)


class ModelPricingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_cny_per_million: float = Field(gt=0, le=10000)
    output_cny_per_million: float = Field(gt=0, le=10000)
    context_tokens: int = Field(default=262144, ge=1, le=10_000_000)


def _credentials(provider_id: str) -> tuple[str, str]:
    """Resolve (base_url, api_key); only the default reads the env-configured key."""
    if provider_id == store.DEFAULT_ID:
        config = ai_editor.configuration()
        return config["base_url"].rstrip("/"), config["api_key"]
    record = store.get_provider(provider_id)
    if record is None:
        raise HTTPException(404, "Provider 不存在")
    if not record.get("api_key"):
        raise HTTPException(422, "该 provider 缺少 API key")
    return record["base_url"], record["api_key"]


async def _remote_models(base_url: str, api_key: str) -> list[str]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(base_url + "/models",
                                    headers={"Authorization": "Bearer " + api_key})
    if response.status_code == 401:
        raise HTTPException(401, "API key 认证失败")
    response.raise_for_status()
    payload = response.json().get("data") or []
    models = sorted({str(item.get("id")) for item in payload if item.get("id")})
    if not models:
        raise HTTPException(502, "Provider 未返回任何模型")
    return models


@router.get("/ai/status")
def status(provider_id: str | None = None, model: str | None = None):
    result = ai_editor.availability()
    if model:
        runtime = ai_editor.resolve_runtime_config(ai_editor.configuration(), provider_id, model)
        result.update(model=model, budget=runtime["budget"].summary())
    return result


@router.get("/ai/providers")
def providers():
    stored = {record["id"]: store.masked(record) for record in store.list_providers()}
    default_model = None
    try:
        default_model = ai_editor.configuration()["pricing"]["model"]
    except HTTPException:
        pass
    default = stored.get(store.DEFAULT_ID) or {
        "id": store.DEFAULT_ID, "label": "本机默认", "base_url": "", "created": None,
        "budget_limit_cny": None, "key_hint": "", "editable": False, "models": {},
    }
    default["default_model"] = default_model
    return {"providers": [default] + [v for k, v in stored.items() if k != store.DEFAULT_ID]}


@router.put("/ai/providers")
def put_provider(payload: ProviderRequest):
    try:
        return store.masked(store.upsert_provider(payload.model_dump()))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.delete("/ai/providers/{provider_id}")
def remove_provider(provider_id: str):
    try:
        store.delete_provider(provider_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {"removed": provider_id}


@router.get("/ai/providers/{provider_id}/models")
async def provider_models(provider_id: str):
    base_url, api_key = _credentials(provider_id)
    record = store.get_provider(provider_id) or {"models": {}}
    cache = record.get("models", {})
    models = await _remote_models(base_url, api_key)
    return {"models": [
        {"id": model,
         "vlm": cache.get(model, {}).get("vlm"),
         "probe_error": cache.get(model, {}).get("probe_error"),
         "pricing": ({k: cache.get(model, {}).get(k) for k in
                     ("input_cny_per_million", "output_cny_per_million", "context_tokens")
                     if cache.get(model, {}).get(k) is not None}
                     if all(cache.get(model, {}).get(k) is not None for k in
                            ("input_cny_per_million", "output_cny_per_million", "context_tokens")) else None)}
        for model in models
    ]}


@router.post("/ai/providers/{provider_id}/models/{model_id}/probe")
async def probe_model(provider_id: str, model_id: str):
    """A priced visual challenge returned through a tool, under the total cap.

    This is a capability smoke test, not a guarantee of CAD competence.
    """
    runtime = ai_editor.resolve_runtime_config(ai_editor.configuration(), provider_id,
                                              model_id, require_vlm=False)
    expected, image = store.visual_challenge()
    try:
        index = runtime["budget"].reserve(model_id, 512, "provider-probe/" + provider_id)
    except BudgetExceeded as error:
        raise HTTPException(402, str(error)) from error
    await asyncio.to_thread(runtime["budget"].wait_for_slot)
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(runtime["base_url"].rstrip("/") + "/chat/completions", headers={
            "Authorization": "Bearer " + runtime["api_key"],
        }, json={
            "model": model_id,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Identify the solid square's colour in the image. Call report_probe with red, green, blue or yellow. Do not guess from this text."},
                {"type": "image_url", "image_url": {
                    "url": image}},
            ]}],
            "tools": [{"type": "function", "function": {
                "name": "report_probe", "description": "Report the observed image colour",
                "parameters": {"type": "object", "properties": {"colour": {
                    "type": "string", "enum": ["red", "green", "blue", "yellow"]}}, "required": ["colour"]},
            }}],
            "tool_choice": "auto", "max_tokens": 512,
        })
    try:
        data = response.json()
    except ValueError:
        data = {}
    usage = data.get("usage")
    try:
        runtime["budget"].settle(index, usage, data.get("model"))
    except BudgetExceeded as error:
        raise HTTPException(402, str(error)) from error
    if response.status_code == 200:
        if not usage:
            raise HTTPException(502, "探针费用未知，已保留预算预留，不能自动重试")
        passed = False
        try:
            calls = data["choices"][0]["message"].get("tool_calls") or []
            for call in calls:
                arguments = call["function"].get("arguments", "{}")
                args = json.loads(arguments) if isinstance(arguments, str) else arguments
                passed |= call["function"]["name"] == "report_probe" and args.get("colour") == expected
        except (KeyError, IndexError, ValueError, TypeError, AttributeError):
            passed = False
        error = None if passed else "未同时通过图片识别与工具调用；HTTP成功不代表具备这两项能力"
        store.update_model(provider_id, model_id, vlm=passed, probe_error=error, probe_usage=usage)
        return {"model": model_id, "vlm": passed, "probe_error": error, "probe_usage": usage,
                "budget": runtime["budget"].summary()}
    detail = ""
    try:
        detail = str(response.json().get("error", {}).get("message", ""))[:300]
    except Exception:
        detail = response.text[:300]
    lowered = detail.lower()
    if response.status_code in (400, 404, 422) and any(
            marker in lowered for marker in
            ("not support", "unsupported", "multimodal", "vision", "image input", "text only", "text-only")):
        store.update_model(provider_id, model_id, vlm=False, probe_error=detail, probe_usage=usage)
        return {"model": model_id, "vlm": False, "probe_error": detail, "probe_usage": usage}
    if response.status_code in (401, 403):
        raise HTTPException(401, "API key 认证失败：" + detail)
    raise HTTPException(502, f"探测失败（HTTP {response.status_code}）：{detail}")


@router.put("/ai/providers/{provider_id}/models/{model_id}")
def put_model_pricing(provider_id: str, model_id: str, payload: ModelPricingRequest):
    try:
        record = store.update_model(provider_id, model_id, pricing=payload.model_dump())
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {"model": model_id, "pricing": record["models"][model_id]}


@router.post("/projects/{project_id}/ai/jobs", status_code=202)
async def create(project_id: str, payload: EditRequest):
    _project_or_404(project_id)
    return (
        await ai_editor.start(
            project_id, payload.base_version, payload.instruction, payload.selected,
            payload.parent_job_id, payload.images, payload.provider_id, payload.model, payload.mode,
        )
    ).public()


@router.get("/projects/{project_id}/ai/jobs/{job_id}")
def get(project_id: str, job_id: str):
    _project_or_404(project_id)
    return ai_editor.find_job(project_id, job_id).public()


@router.post("/projects/{project_id}/ai/jobs/{job_id}/cancel")
def cancel(project_id: str, job_id: str):
    _project_or_404(project_id)
    job = ai_editor.find_job(project_id, job_id)
    job.cancelled = True
    if job.status in {"ready", "paused"}:
        job.status = "cancelled"
        job.summary = "已放弃预览，正式模型未修改"
    ai_editor.persist(job)
    return job.public()


@router.post("/projects/{project_id}/ai/jobs/{job_id}/resume", status_code=202)
async def resume(project_id: str, job_id: str, payload: ResumeRequest):
    _project_or_404(project_id)
    return (await ai_editor.resume(ai_editor.find_job(project_id, job_id), payload.instruction)).public()


@router.post("/projects/{project_id}/ai/jobs/{job_id}/apply")
async def apply(project_id: str, job_id: str, payload: ApplyRequest):
    _project_or_404(project_id)
    return ai_editor.apply(ai_editor.find_job(project_id, job_id), payload.author)
