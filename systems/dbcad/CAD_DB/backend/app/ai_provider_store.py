"""User-defined AI providers: endpoint, key, per-model VLM eligibility and pricing.

Keys live only in a 0600 local JSON file next to the private AI config. They are
never returned to the browser (masked view only) and never logged. VLM probing
sends one minimal image+tool request; its tiny usage is recorded and disclosed
instead of silently charging the research ledger.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from .config import settings

DEFAULT_ID = "default"
PROBE_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAgUlEQVR4nNXOMREAIBDAsFL/wphxgBNE/MA1CrL2uZRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMRJnMT5OzD1APGeAvvutJUiAAAAAElFTkSuQmCC"
)
_SLUG = re.compile(r"[^a-zA-Z0-9._-]+")


def _providers_path() -> Path | None:
    if not settings.ai_config:
        return None
    return Path(settings.ai_config).resolve().parent / "ai-providers.json"


def _load(path: Path) -> dict:
    if not path.exists():
        return {"providers": []}
    data = json.loads(path.read_text())
    if not isinstance(data.get("providers"), list):
        raise ValueError("Corrupt provider store")
    return data


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=1)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    path.chmod(0o600)


def list_providers() -> list[dict]:
    """Stored records only; the env-configured default is merged by the API layer."""
    path = _providers_path()
    return list(_load(path)["providers"]) if path else []


def get_provider(provider_id: str) -> dict | None:
    return next((p for p in list_providers() if p["id"] == provider_id), None)


def upsert_provider(payload: dict) -> dict:
    path = _providers_path()
    if path is None:
        raise ValueError("AI 未配置，无法保存自定义 provider")
    provider_id = payload.get("id")
    base_url = str(payload.get("base_url", "")).rstrip("/")
    if not base_url.startswith("https://"):
        raise ValueError("自定义 provider 必须使用 HTTPS")
    data = _load(path)
    record = next((p for p in data["providers"] if provider_id and p["id"] == provider_id), None)
    api_key = str(payload.get("api_key") or "")
    if record is None:
        if provider_id and provider_id != DEFAULT_ID:
            raise ValueError("Provider 不存在")
        provider_id = provider_id or uuid4().hex[:8]
        if any(p["id"] == provider_id for p in data["providers"]):
            raise ValueError("Provider 标识冲突")
        record = {"id": provider_id, "label": "", "base_url": base_url, "api_key": "",
                  "created": time.time(), "budget_limit_cny": 100.0, "models": {}}
        data["providers"].append(record)
    else:
        if provider_id == DEFAULT_ID:
            # The default record stores per-model metadata only; its key stays in env.
            base_url = record["base_url"]
            api_key = ""
        record["base_url"] = base_url
    if api_key:
        if provider_id == DEFAULT_ID:
            raise ValueError("默认 provider 的凭据只来自本机配置，不接受网页填写")
        record["api_key"] = api_key
    if provider_id != DEFAULT_ID:
        if not record["api_key"]:
            raise ValueError("自定义 provider 需要 API key")
        if payload.get("label"):
            record["label"] = str(payload["label"])[:80]
        limit = payload.get("budget_limit_cny")
        if limit is not None:
            limit = float(limit)
            if not 0 < limit <= 10000:
                raise ValueError("预算上限需在 (0, 10000] 元")
            record["budget_limit_cny"] = limit
    _save(path, data)
    return record


def delete_provider(provider_id: str) -> None:
    if provider_id == DEFAULT_ID:
        raise ValueError("默认 provider 不能删除")
    path = _providers_path()
    if path is None:
        return
    data = _load(path)
    remaining = [p for p in data["providers"] if p["id"] != provider_id]
    if len(remaining) == len(data["providers"]):
        raise ValueError("Provider 不存在")
    data["providers"] = remaining
    _save(path, data)


def update_model(provider_id: str, model: str, *, vlm: bool | None = None,
                 probe_error: str | None = None, probe_usage: dict | None = None,
                 pricing: dict | None = None) -> dict:
    path = _providers_path()
    if path is None:
        raise ValueError("AI 未配置")
    data = _load(path)
    record = next((p for p in data["providers"] if p["id"] == provider_id), None)
    if record is None:
        if provider_id != DEFAULT_ID:
            raise ValueError("Provider 不存在")
        record = {"id": DEFAULT_ID, "label": "本机默认", "base_url": "", "api_key": "",
                  "created": time.time(), "budget_limit_cny": 100.0, "models": {}}
        data["providers"].append(record)
    entry = record["models"].setdefault(str(model)[:120], {})
    if vlm is not None:
        entry["vlm"] = vlm
        entry["probed_at"] = time.time()
        entry["probe_error"] = probe_error
        if probe_usage:
            entry["probe_usage"] = probe_usage
    if pricing:
        for key in ("input_cny_per_million", "output_cny_per_million"):
            value = float(pricing.get(key, 0))
            if not 0 < value <= 10000:
                raise ValueError(f"{key} 需在 (0, 10000]")
            entry[key] = value
        context = int(pricing.get("context_tokens", 262144))
        if not 1 <= context <= 10_000_000:
            raise ValueError("context_tokens 需在 [1, 10000000]")
        entry["context_tokens"] = context
    _save(path, data)
    return record


def masked(record: dict) -> dict:
    key = record.get("api_key") or ""
    return {
        "id": record["id"], "label": record.get("label") or ("本机默认" if record["id"] == DEFAULT_ID else ""),
        "base_url": record["base_url"], "created": record.get("created"),
        "budget_limit_cny": record.get("budget_limit_cny", 100.0),
        "key_hint": ("…" + key[-4:]) if len(key) >= 8 else ("" if not key else "…"),
        "editable": record["id"] != DEFAULT_ID,
        "models": {
            model: {k: v for k, v in entry.items()} for model, entry in record.get("models", {}).items()
        },
    }


def ledger_path(provider_id: str, model: str) -> Path | None:
    root = _providers_path()
    if root is None:
        return None
    slug = _SLUG.sub("-", model)[:60] or "model"
    return root.parent / f"budget-custom-{provider_id}-{slug}.json"


def decode_data_urls(images: list) -> list[str]:
    """Validate user image attachments: PNG/JPEG data URLs, bounded size/count."""
    if not isinstance(images, list) or len(images) > 3:
        raise ValueError("最多附带 3 张图片")
    cleaned = []
    for image in images:
        if not isinstance(image, str):
            raise ValueError("图片需为 data URL 字符串")
        match = re.match(r"^data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)$", image)
        if not match:
            raise ValueError("仅支持 PNG/JPEG 的 data URL 图片")
        payload = base64.b64decode(match.group(2), validate=True)
        if not payload.startswith(b"\x89PNG") and not payload.startswith(b"\xff\xd8"):
            raise ValueError("图片内容与声明格式不符")
        if len(payload) > 2_500_000:
            raise ValueError("单张图片需小于 2.5MB")
        cleaned.append(image)
    return cleaned
