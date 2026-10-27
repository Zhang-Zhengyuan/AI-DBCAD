"""Official-segment integration contracts: state mapping, accounting, highlights."""

import asyncio
from types import SimpleNamespace

import pytest

from app import ai_editor
from app.ai_budget import Budget, BudgetExceeded
from app.ai_editor import (
    _finalize_official,
    _map_selection_highlights,
    _world_frame,
    official_model,
    output_limit,
)
from app.v1_store import SceneComponent


def _component(hash_sat, low, high):
    return SceneComponent(hash_sat=hash_sat, hash_glb="", name=hash_sat[:6],
                          color=[0.8, 0.8, 0.8], bbox_min=low, bbox_max=high,
                          triangles=10, vertices=10, graph="")


def _job(manifest=None, original=None):
    return SimpleNamespace(manifest=manifest or [], original=original or [],
                           trace=[], messages=[], cancelled=False, status="running",
                           pause_reason=None, summary="",
                           calls=0, segment_calls=0, cost=0.0, id="a" * 32)


def test_finalize_official_maps_finish_success_failure_and_pause():
    changed = _job(manifest=[_component("b" * 64, [0, 0, 0], [1, 1, 1])],
                   original=[])
    _finalize_official(changed, {"finished": True, "success": True, "summary": "已完成"})
    assert changed.status == "ready" and "已完成" in changed.summary

    noop = _job(manifest=[_component("b" * 64, [0, 0, 0], [1, 1, 1])],
                original=[_component("b" * 64, [0, 0, 0], [1, 1, 1])])
    _finalize_official(noop, {"finished": True, "success": True, "summary": "完成"})
    assert noop.status == "failed" and "没有任何变化" in noop.summary

    refused = _job()
    _finalize_official(refused, {"finished": True, "success": False, "exit_reason": "finish",
                                 "summary": "无法完成"})
    assert refused.status == "failed" and refused.pause_reason is None

    paused = _job()
    _finalize_official(paused, {"finished": False, "exit_reason": "no_tool_call", "summary": ""})
    assert paused.status == "paused" and paused.pause_reason == "no_tool_call"
    assert "累计费用不重置" in paused.summary


def test_world_frame_unions_original_and_current_with_stable_anchor():
    job = _job(original=[_component("a" * 64, [0, 0, 0], [10, 4, 4])],
               manifest=[_component("b" * 64, [-2, 0, 0], [0, 2, 2])])
    frame = _world_frame(job)
    assert frame["center"] == [4.0, 2.0, 2.0] and frame["size"] == 12.0
    assert _world_frame(_job()) == {"center": [0.0, 0.0, 0.0], "size": 10.0}


def test_selection_highlights_match_real_centres_and_fail_on_drift():
    listings = {
        ("faces", "comp1"): {"items": [{"index": 3, "center": [5, 0, 0]},
                                       {"index": 4, "center": [9, 9, 9]}]},
        ("edges", "comp1"): {"items": [{"index": 0, "center": [5, 0, 2]},
                                       {"index": 1, "center": [5, 0, -2]}]},
    }
    selections = []

    async def query(request):
        key = (request["kind"], request["component"])
        if request["kind"] == "render_selection":
            selections.append((request["component"], request.get("faces"), request.get("edges")))
            return {"selections": [{"kind": "face", "index": 3, "positions": [], "indices": []}]}
        return listings[key]

    result = asyncio.run(_map_selection_highlights(
        query, ["comp1"], [[5.0, 0.0, 0.0]], [[5.0, 0.0, -2.0], [5.0, 0.0, 2.0]]))
    assert result and selections[0] == ("comp1", [3], None) and selections[1][2] == [0, 1]

    with pytest.raises(ValueError, match="不匹配"):
        asyncio.run(_map_selection_highlights(query, ["comp1"], [[7.5, 0, 0]], []))


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    requests = []

    def __init__(self, timeout=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeClient.requests.append(json)
        return _Response(_FakeClient.payload)


def _config(tmp_path, framework="agenticcadedit-acis"):
    pricing = {"model": "qwen-test", "context_tokens": 262144,
               "input_cny_per_million": 3.0, "output_cny_per_million": 18.0}
    return {
        "framework": framework, "base_url": "https://example.invalid/v1",
        "api_key": "test", "pricing": pricing,
        "budget": Budget(tmp_path / "ledger.json", 300.0, pricing),
    }


def test_official_model_accounts_usage_and_requests_full_quota(monkeypatch, tmp_path):
    _FakeClient.requests.clear()
    _FakeClient.payload = {"choices": [{"message": {"role": "assistant", "content": "ok"}}],
                           "usage": {"prompt_tokens": 1000, "completion_tokens": 100},
                           "model": "qwen-test"}
    monkeypatch.setattr(ai_editor, "httpx", SimpleNamespace(AsyncClient=_FakeClient))
    job = _job()
    config = _config(tmp_path)

    data = asyncio.run(official_model(job, config, [{"role": "user", "content": "hi"}], []))
    assert data["usage"]["prompt_tokens"] == 1000
    assert job.calls == 1 and job.segment_calls == 1
    assert job.cost == pytest.approx((1000 * 3.0 + 100 * 18.0) / 1e6)
    request = _FakeClient.requests[0]
    assert request["max_tokens"] == 8192  # official default, not the 2048 native quota
    assert request["chat_template_kwargs"] == {"enable_thinking": True}
    assert config["budget"].summary()["charged_or_reserved_cny"] == pytest.approx(job.cost)


def test_official_model_retains_reservation_without_usage(monkeypatch, tmp_path):
    _FakeClient.requests.clear()
    _FakeClient.payload = {"choices": [{"message": {"role": "assistant", "content": "x"}}],
                           "model": "qwen-test"}
    monkeypatch.setattr(ai_editor, "httpx", SimpleNamespace(AsyncClient=_FakeClient))
    job = _job()
    config = _config(tmp_path)

    with pytest.raises(BudgetExceeded, match="缺少费用信息"):
        asyncio.run(official_model(job, config, [], []))
    summary = config["budget"].summary()
    assert summary["calls"] == 1 and summary["charged_or_reserved_cny"] > 0
    assert job.calls == 1  # the call happened even though usage was missing


def test_output_limit_defaults_differ_by_framework():
    assert output_limit({"framework": "agenticcadedit-acis"}) == 8192
    assert output_limit({}) == 2048
    with pytest.raises(ValueError):
        output_limit({"framework": "agenticcadedit-acis", "max_tokens": 10})
