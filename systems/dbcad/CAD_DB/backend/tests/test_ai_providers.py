"""Provider store, VLM probe classification, image limits and runtime resolution."""

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import ai_provider_store as store


@pytest.fixture()
def provider_file(tmp_path, monkeypatch):
    path = tmp_path / "ai.json"
    path.write_text(json.dumps({"framework": "agenticcadedit-acis", "base_url": "https://env.example/v1"}))
    monkeypatch.setattr("app.config.settings.ai_config", str(path), raising=False)
    return tmp_path / "ai-providers.json"


def _png_data_url() -> str:
    payload = base64.b64encode(base64.b64decode(store.PROBE_PNG)).decode()
    return "data:image/png;base64," + payload


def test_provider_store_masks_keys_and_enforces_https(provider_file):
    record = store.upsert_provider({"label": "中转", "base_url": "https://one.example/v1",
                                    "api_key": "sk-abcdefghijklmnop1234"})
    assert record["id"] != store.DEFAULT_ID and record["api_key"].startswith("sk-")
    view = store.masked(record)
    assert view["key_hint"] == "…1234" and "api_key" not in view
    assert provider_file.exists() and (provider_file.stat().st_mode & 0o777) == 0o600
    with pytest.raises(ValueError, match="HTTPS"):
        store.upsert_provider({"base_url": "http://insecure.example", "api_key": "sk-x"})
    with pytest.raises(ValueError, match="API key"):
        store.upsert_provider({"base_url": "https://two.example/v1"})


def test_default_provider_keeps_env_credentials_only(provider_file):
    with pytest.raises(ValueError, match="本机配置"):
        store.upsert_provider({"id": store.DEFAULT_ID, "base_url": "https://one.example/v1",
                               "api_key": "sk-should-be-rejected"})
    record = store.update_model(store.DEFAULT_ID, "some-vlm", vlm=True,
                                pricing={"input_cny_per_million": 3, "output_cny_per_million": 18})
    assert record["base_url"] == "" and not record["api_key"]  # metadata only


def test_update_model_validates_pricing(provider_file):
    record = store.upsert_provider({"base_url": "https://one.example/v1", "api_key": "sk-keykeykey"})
    store.update_model(record["id"], "vlm-a", vlm=True,
                       pricing={"input_cny_per_million": 3, "output_cny_per_million": 18})
    entry = store.get_provider(record["id"])["models"]["vlm-a"]
    assert entry["vlm"] is True and entry["context_tokens"] == 262144
    with pytest.raises(ValueError, match="input_cny_per_million"):
        store.update_model(record["id"], "vlm-a", pricing={"input_cny_per_million": 0,
                                                           "output_cny_per_million": 1})
    assert store.ledger_path(record["id"], "vlm-a").name == f"budget-custom-{record['id']}-vlm-a.json"


def test_decode_data_urls_bounds_count_size_and_format():
    png = _png_data_url()
    assert store.decode_data_urls([png]) == [png]
    with pytest.raises(ValueError, match="3 张"):
        store.decode_data_urls([png, png, png, png])
    with pytest.raises(ValueError, match="data URL"):
        store.decode_data_urls(["https://example.com/a.png"])
    with pytest.raises(ValueError, match="格式不符"):
        store.decode_data_urls(["data:image/png;base64," + base64.b64encode(b"notapng").decode()])
    huge = "data:image/png;base64," + base64.b64encode(b"\x89PNG" + b"x" * 2_600_000).decode()
    with pytest.raises(ValueError, match="2.5MB"):
        store.decode_data_urls([huge])


def _config(folder):
    pricing = {"model": "qwen-default", "input_cny_per_million": 3.0,
               "output_cny_per_million": 18.0, "context_tokens": 262144}
    return {"framework": "agenticcadedit-acis", "base_url": "https://env.example/v1",
            "api_key": "env-key", "pricing": pricing,
            "budget": SimpleNamespace(path=folder / "research-ledger.json", limit=300.0)}


def test_resolve_runtime_config_requires_vlm_probe_and_pricing(provider_file):
    from app.ai_editor import resolve_runtime_config
    config = _config(provider_file.parent)
    assert resolve_runtime_config(config, None, None) is config
    assert resolve_runtime_config(config, store.DEFAULT_ID, "qwen-default") is config
    with pytest.raises(HTTPException) as no_probe:
        resolve_runtime_config(config, store.DEFAULT_ID, "mystery-model")
    assert no_probe.value.status_code == 422 and "VLM 探测" in no_probe.value.detail
    record = store.upsert_provider({"label": "中转", "base_url": "https://one.example/v1",
                                    "api_key": "sk-keykeykey", "budget_limit_cny": 50})
    store.update_model(record["id"], "good-vlm", vlm=True,
                       pricing={"input_cny_per_million": 2, "output_cny_per_million": 9})
    with pytest.raises(HTTPException) as no_price:
        store.update_model(record["id"], "bare-vlm", vlm=True)
        resolve_runtime_config(config, record["id"], "bare-vlm")
    assert no_price.value.status_code == 422 and "单价" in no_price.value.detail
    runtime = resolve_runtime_config(config, record["id"], "good-vlm")
    assert runtime["base_url"] == "https://one.example/v1" and runtime["api_key"] == "sk-keykeykey"
    assert runtime["pricing"]["model"] == "good-vlm"
    assert runtime["budget"].path == config["budget"].path
    assert runtime["budget"].limit == 300.0
    assert runtime["budget"].scope_limit == 50.0


def test_probe_classifier_routes_provider_errors():
    """Mirror the endpoint's classification without network access."""
    def classify(status: int, detail: str) -> str:
        if status == 200:
            return "eligible"
        lowered = detail.lower()
        if status in (400, 404, 422) and any(m in lowered for m in
                ("not support", "unsupported", "multimodal", "vision", "image input", "text only", "text-only")):
            return "not_vlm"
        if status in (401, 403):
            return "auth"
        return "server"

    assert classify(200, "") == "eligible"
    assert classify(400, "This model does not support image input") == "not_vlm"
    assert classify(400, "this model is text-only") == "not_vlm"
    # Transport-style rejections must NOT masquerade as "not a VLM": a real
    # provider once returned a min-dimension complaint containing "invalid".
    assert classify(400, "InternalError.Algo.InvalidParameter: image length must be larger than 10") == "server"
    assert classify(422, "Invalid parameter: max_tokens") == "server"
    assert classify(401, "bad key") == "auth"
    assert classify(500, "upstream boom") == "server"
