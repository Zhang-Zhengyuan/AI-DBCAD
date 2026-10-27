"""Cross-model spending, actual capability checks and durable continuation."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app import ai_editor as editor, ai_provider_store as store
from app.ai_budget import Budget, SharedBudget, BudgetExceeded
from app.api.v1 import ai
from app.config import settings


def price(model="one"):
    return {"model": model, "context_tokens": 1000,
            "input_cny_per_million": 3, "output_cny_per_million": 18}


def test_shared_budget_preserves_legacy_fees_and_unknown_reservations(tmp_path):
    path = tmp_path / "ledger.json"
    old = Budget(path, .01, price())
    old.reserve("one", 100, "unknown-old-request")
    newer = SharedBudget(path, .01, price("two"), scope="custom")
    newer.reserve("two", 100, "new-request")
    with pytest.raises(BudgetExceeded):
        SharedBudget(path, .01, price("three"), scope="another").reserve("three", 100, "third")
    assert newer.summary()["calls"] == 2
    data = json.loads(path.read_text())
    assert data["calls"][0]["status"] == "reserved"
    assert data["pricing"] == price()


def test_provider_limit_is_cumulative_across_models(tmp_path):
    path = tmp_path / "ledger.json"
    SharedBudget(path, 100, price(), scope="provider", scope_limit=.005).reserve("one", 100, "first")
    with pytest.raises(BudgetExceeded, match="Provider cumulative"):
        SharedBudget(path, 100, price("two"), scope="provider", scope_limit=.005).reserve("two", 100, "second")
    SharedBudget(path, 100, price("two"), scope="other", scope_limit=.005).reserve("two", 100, "independent-subcap")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ai_config", str(tmp_path / "ai.json"))
    provider = store.upsert_provider({"base_url": "https://unit.invalid/v1", "api_key": "test-key-only"})
    terms = price("vision")
    store.update_model(provider["id"], "vision", pricing=terms)
    config = {"pricing": price(), "base_url": "https://default.invalid/v1", "api_key": "fake",
              "budget": SharedBudget(tmp_path / "ledger.json", 1, price())}
    monkeypatch.setattr(editor, "configuration", lambda: config)
    return provider["id"], config


@pytest.mark.parametrize("mode,expected", [("plain", False), ("wrong", False), ("correct", True)])
def test_probe_requires_observed_image_answer_and_tool_and_accounts(runtime, monkeypatch, mode, expected):
    provider_id, config = runtime
    monkeypatch.setattr(store, "visual_challenge", lambda: ("blue", "data:image/png;base64," + store.PROBE_PNG))
    actual_client = httpx.AsyncClient

    def respond(request):
        assert request.url.host == "unit.invalid"
        content = json.loads(request.content)
        assert "blue" not in content["messages"][0]["content"][0]["text"].split("Call")[0]
        message = {"role": "assistant", "content": "pong"}
        if mode != "plain":
            message["tool_calls"] = [{"id": "c1", "type": "function", "function": {
                "name": "report_probe", "arguments": json.dumps({"colour": "blue" if mode == "correct" else "red"})}}]
        return httpx.Response(200, json={"choices": [{"message": message}], "model": "vision",
                                        "usage": {"prompt_tokens": 20, "completion_tokens": 5}})

    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kwargs: actual_client(transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(SharedBudget, "wait_for_slot", lambda *args: None)
    result = asyncio.run(ai.probe_model(provider_id, "vision"))
    assert result["vlm"] is expected
    summary = config["budget"].summary()
    assert summary["calls"] == 1
    assert summary["charged_or_reserved_cny"] == pytest.approx((20 * 3 + 5 * 18) / 1e6)


def test_probe_without_prices_fails_before_network(runtime):
    provider_id, _ = runtime
    with pytest.raises(HTTPException) as error:
        asyncio.run(ai.probe_model(provider_id, "unpriced"))
    assert error.value.status_code == 422


def test_endpoint_change_invalidates_capability_observations(runtime):
    provider_id, _ = runtime
    store.update_model(provider_id, "vision", vlm=True)
    store.upsert_provider({"id": provider_id, "base_url": "https://other.invalid/v1"})
    assert store.get_provider(provider_id)["models"] == {}


def test_readonly_stage_completes_without_version_and_forbids_writes():
    job = editor.Job("a" * 32, "project", 4, "measure", [], [], [], mode="inspect")
    with pytest.raises(ValueError, match="只读"):
        editor.validate_command("component.create", {"kind": "cube", "width": 1}, job)
    editor._finalize_official(job, {"finished": True, "success": True, "summary": "checked"})
    assert job.status == "completed" and job.version == 4
    with pytest.raises(HTTPException):
        editor.apply(job, "cannot-publish-a-check")


def test_running_checkpoint_restores_visible_progress_but_cannot_auto_replay(tmp_path, monkeypatch):
    config = tmp_path / "ai.json"
    config.write_text(json.dumps({"trace_dir": str(tmp_path / "traces")}))
    monkeypatch.setattr(settings, "ai_config", str(config))
    job = editor.Job("a" * 32, "project", 4, "edit", [], [], [], trace_dir=str(tmp_path / "traces"),
                     messages=[{"role": "assistant", "tool_calls": [{"id": "pending"}]}])
    editor.persist(job)
    restored = editor.restore_job("project", job.id)
    assert restored.status == "paused" and restored.pause_reason == "interrupted"
    assert restored.messages == job.messages
    assert restored.public()["can_resume"] is False


def test_camera_is_stable_when_preview_extents_change():
    component = lambda low, high: SimpleNamespace(bbox_min=low, bbox_max=high)
    job = SimpleNamespace(original=[component([0, 0, 0], [10, 10, 10])], manifest=[], camera=None)
    first = editor._world_frame(job)
    job.manifest = [component([100, 0, 0], [110, 10, 10])]
    assert editor._world_frame(job) == first


def test_finish_is_closed_for_strict_providers_but_unknown_writes_are_never_replayed():
    from app.ai_messages import close_tool_messages
    message = lambda name: {"role": "assistant", "tool_calls": [{"id": "one", "function": {"name": name}}]}
    assert close_tool_messages([message("finish")])[-1]["tool_call_id"] == "one"
    with pytest.raises(ValueError, match="Unresolved"):
        close_tool_messages([message("run_step")])
    closed = close_tool_messages([message("run_step")], allow_unknown=True)
    assert "UNKNOWN" in closed[-1]["content"]
    assert "automatic replay" in closed[-1]["content"]


def test_worker_stop_closes_pipes_after_process_exit():
    import io
    from app.worker_pool import _Process
    stdin,stdout=io.BytesIO(),io.BytesIO()
    worker=_Process(Path("unused"))
    worker.proc=SimpleNamespace(stdin=stdin,stdout=stdout,poll=lambda:0,wait=lambda **kw:0)
    worker._stop_sync()
    assert stdin.closed and stdout.closed and worker.proc is None


@pytest.mark.parametrize("name",["undo","reset"])
def test_readonly_mode_blocks_mcp_mutators_without_native_commands(tmp_path,name):
    from app.ai_agentic import AgenticBridge,AgenticCallbacks
    job=editor.Job("a"*32,"project",1,"inspect",[],[],[],mode="inspect")
    bridge=AgenticBridge(job,AgenticCallbacks(None,None,None,None),tmp_path)
    with pytest.raises(ValueError,match="Read-only"):
        asyncio.run(bridge.dispatch("tool",{"name":name,"arguments":{}}))


def test_highlight_ambiguous_centres_do_not_guess_between_components():
    async def query(request):
        return {"items":[{"index":1,"center":[0,0,0]}]} if request['kind']=='faces' else {"items":[]}
    with pytest.raises(ValueError,match="多个实体"):
        asyncio.run(editor._map_selection_highlights(query,['one','two'],[[0,0,0]],[]))
