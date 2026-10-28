"""No paid API: bounded segments retain progress, provenance and budget."""
import asyncio
import copy
import json
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from app import ai_editor as ai, main
from app.ai_budget import Budget
from app.component_store import ComponentStore, get_component_store, set_component_store
from app.config import settings
from app.v1_store import SceneComponent, get_v1_store, initialize_v1_store, shutdown_v1_store


@pytest.fixture
def harness(tmp_path, monkeypatch):
    initialize_v1_store(memory=True)
    assets = ComponentStore(tmp_path / "assets")
    set_component_store(assets)
    ai.jobs.clear()
    project = get_v1_store().create_project("continuation")
    original = SceneComponent(assets.put("sat", b"original"), assets.put("glb", b"mesh"))
    get_v1_store().commit_version(project.id, message="seed", author="test", components=[original])
    pricing = dict(model="fake", context_tokens=10000, input_cny_per_million=3, output_cny_per_million=18)
    budget = Budget(tmp_path / "budget.json", 10, pricing)
    monkeypatch.setattr(budget, "wait_for_slot", lambda: None)
    config = dict(budget=budget, pricing=pricing, base_url="https://example.invalid", api_key="never-log",
                  max_calls=20, job_soft_limit_cny=4, trace_dir=str(tmp_path / "traces"))
    cfgfile = tmp_path / "config.json"
    cfgfile.write_text(json.dumps({"trace_dir": config["trace_dir"]}))
    monkeypatch.setattr(settings, "ai_config", str(cfgfile))
    monkeypatch.setattr(ai, "configuration", lambda: config)
    class Pool:
        async def release(self, *args):
            pass
    monkeypatch.setattr(ai, "get_worker_pool", lambda: Pool())
    def job(**kwargs):
        result = ai.Job(uuid4().hex, project.id, 1, "build a detailed model", [], [original], [original],
                        model="fake", ledger=str(budget.path.resolve()), trace_dir=config["trace_dir"], **kwargs)
        ai.jobs[result.id] = result
        return result
    yield project, original, config, job
    ai.jobs.clear()
    set_component_store(None)
    shutdown_v1_store()


def provider(monkeypatch, response_for):
    requests = []
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            requests.append(copy.deepcopy(kwargs["json"]))
            name, args = response_for(len(requests))
            payload = {"model": "fake", "usage": {"prompt_tokens": 10, "completion_tokens": 10},
                       "choices": [{"message": {"role": "assistant", "content": None,
                       "tool_calls": [{"id": f"call-{len(requests)}", "type": "function",
                                       "function": {"name": name, "arguments": json.dumps(args)}}]}}]}
            return httpx.Response(200, json=payload, request=httpx.Request("POST", url))
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kwargs: Client())
    return requests


def test_three_segments_exceed_40_calls_without_resetting_progress_or_cost(harness, monkeypatch):
    project, original, config, make_job = harness
    def response(n):
        return ("run_command", {"action": "component.create", "params": {"kind": "cube", "width": n}}) if n <= 45 else ("finish", {"summary": "done"})
    requests = provider(monkeypatch, response)
    async def native(job, action, params):
        digest = get_component_store().put("sat", str(params["width"]).encode())
        return {"added": [{"sat_bytes": digest, "glb_bytes": original.hash_glb, "kind": "cube"}]}
    monkeypatch.setattr(ai, "native_command", native)
    job = make_job()
    async def exercise():
        await ai.run(job, config)
        assert job.status == "paused" and job.calls == 20 and len(job.history) == 20
        first_manifest = list(job.manifest)
        first_messages = copy.deepcopy(job.messages)
        first_cost = job.cost
        assert job.public()["scene"]["components"] and job.public()["can_resume"]
        assert "messages" not in job.public() and "ledger" not in job.public()
        with pytest.raises(HTTPException):
            ai.apply(job, "not-complete")
        await ai.resume(job, "keep the existing body and finish details")
        await job.task
        assert job.status == "paused" and job.calls == 40 and len(job.history) == 40
        assert job.manifest[:len(first_manifest)] == first_manifest and job.cost > first_cost
        assert job.messages[:len(first_messages)] == first_messages
        await ai.resume(job)
        await job.task
        assert job.status == "ready" and job.calls == 46 and len(job.history) == 45
    asyncio.run(exercise())
    assert len(requests) == 46
    assert len(requests[20]["messages"]) > len(requests[0]["messages"])
    assert "keep the existing body" in requests[20]["messages"][-1]["content"]
    assert config["budget"].summary()["calls"] == 46
    assert job.cost == pytest.approx(config["budget"].summary()["estimated_cny"])
    assert get_v1_store().latest(project.id).number == 1
    monkeypatch.setattr(ai, "write_projections", lambda *args: None)
    assert ai.apply(job, "human")["version"] == 2
    assert len(get_v1_store().latest(project.id).components) == 46


def test_paused_checkpoint_restores_messages_geometry_history_and_cost(harness, monkeypatch):
    project, original, config, make_job = harness
    provider(monkeypatch, lambda n: ("inspect", {}))
    config["max_calls"] = 1
    job = make_job()
    job.history = [[original]]
    job.projections = [(original.hash_sat, {"nodes": []}, None)]
    asyncio.run(ai.run(job, config))
    checkpoint = ai._checkpoint_path(job.trace_dir, job.id)
    assert checkpoint.stat().st_mode & 0o777 == 0o600
    assert "never-log" not in checkpoint.read_text()
    ai.jobs.clear()  # Simulate a server restart, with the same assets and ledger.
    restored = ai.find_job(project.id, job.id)
    assert restored.status == "paused" and restored.messages == job.messages
    assert restored.manifest == job.manifest and restored.history == job.history
    assert restored.cost == job.cost and restored.calls == 1
    assert restored.projections[0][1] == {"nodes": []}
    async def exercise():
        await ai.resume(restored)
        await restored.task
    asyncio.run(exercise())
    assert restored.calls == 2 and restored.cost > job.cost
    ai.jobs.clear()
    with pytest.raises(HTTPException):
        ai.find_job("another-project", job.id)
    with pytest.raises(HTTPException):
        ai.find_job(project.id, "../../escape")


def test_resume_rejects_stale_head_cancelled_budget_and_uncertain_fee(harness, monkeypatch):
    project, original, config, make_job = harness
    job = make_job(status="paused", pause_reason="round_limit", messages=[{"role": "user", "content": "saved"}])
    for status in ("running", "ready", "applied", "failed", "cancelled"):
        job.status = status
        with pytest.raises(HTTPException):
            asyncio.run(ai.resume(job))
    job.status = "paused"
    job.cancelled = True
    with pytest.raises(HTTPException):
        asyncio.run(ai.resume(job))
    job.cancelled = False
    job.cost = 4
    with pytest.raises(HTTPException) as error:
        asyncio.run(ai.resume(job))
    assert error.value.status_code == 402
    job.cost = 0
    config["budget"].reserve("fake", 2048, "native-ai/" + job.id)
    with pytest.raises(HTTPException, match="尚未确认"):
        asyncio.run(ai.resume(job))
    assert config["budget"].summary()["calls"] == 1
    get_v1_store().commit_version(project.id, message="external", author="human", components=[original])
    with pytest.raises(HTTPException) as error:
        asyncio.run(ai.resume(job))
    assert error.value.status_code == 409 and error.value.detail["reason"] == "stale_base_version"


def test_resume_cannot_switch_ledgers_and_keeps_only_one_running_task(harness, monkeypatch):
    _, _, config, make_job = harness
    job = make_job(status="paused", pause_reason="round_limit", messages=[{"role": "user", "content": "saved"}])
    job.ledger += "-different"
    with pytest.raises(HTTPException, match="账本已改变"):
        asyncio.run(ai.resume(job))
    job.ledger = str(config["budget"].path.resolve())
    async def run(*args):
        await asyncio.sleep(10)
    monkeypatch.setattr(ai, "run", run)
    async def exercise():
        results = await asyncio.gather(ai.resume(job), ai.resume(job), return_exceptions=True)
        assert sum(isinstance(r, HTTPException) for r in results) == 1
        job.task.cancel()
        await asyncio.gather(job.task, return_exceptions=True)
    asyncio.run(exercise())


def test_clarification_pause_requires_human_answer_and_keeps_original_question(harness, monkeypatch):
    _, _, config, make_job = harness
    question = "出水口直径需要多少毫米？"
    job = make_job(status="paused", pause_reason="no_tool_call",
                   messages=[{"role": "assistant", "content": question}])
    before = copy.deepcopy(job.messages)
    assert job.public()["can_resume"]
    with pytest.raises(HTTPException) as error:
        asyncio.run(ai.resume(job, "  "))
    assert error.value.status_code == 422
    assert job.messages == before and config["budget"].summary()["calls"] == 0
    seen = []
    async def run(current, runtime):
        seen.extend(current.messages)
        current.status = "completed"
    monkeypatch.setattr(ai, "run", run)
    async def exercise():
        await ai.resume(job, "直径 12 mm")
        await job.task
    asyncio.run(exercise())
    assert seen[0]["content"] == question and "直径 12 mm" in seen[-1]["content"]
    assert job.id in ai.jobs and job.calls == 0


def test_budget_pause_and_paused_cancel_never_publish(harness, monkeypatch):
    project, _, config, make_job = harness
    requests = provider(monkeypatch, lambda n: ("inspect", {}))
    config["job_soft_limit_cny"] = .0001
    job = make_job()
    asyncio.run(ai.run(job, config))
    assert len(requests) == 1 and job.pause_reason == "budget_limit"
    assert job.public()["scene"] and job.public()["can_resume"]
    client = TestClient(main.app)
    url = f"/api/v1/projects/{project.id}/ai/jobs/{job.id}"
    assert client.post(url + "/apply", json={}).status_code == 409
    assert client.post(url + "/resume", json={}).status_code == 402
    # An explicit operator change to the per-job soft cap permits continuation,
    # but previous cost/calls and the original global ledger stay intact.
    config["job_soft_limit_cny"] = 4
    config["max_calls"] = 1
    async def exercise():
        await ai.resume(job)
        await job.task
    asyncio.run(exercise())
    assert job.calls == 2 and config["budget"].summary()["calls"] == 2
    assert client.post(url + "/cancel").json()["status"] == "cancelled"
    assert client.post(url + "/resume", json={}).status_code == 409
    assert get_v1_store().latest(project.id).number == 1


def test_resume_route_auth_scope_and_no_client_budget_override(harness, monkeypatch):
    project, _, _, make_job = harness
    job = make_job(status="paused", pause_reason="round_limit", messages=[{"role": "user", "content": "saved"}])
    other = get_v1_store().create_project("another")
    client = TestClient(main.app)
    monkeypatch.setattr(settings, "api_password", "test-password")
    headers = {"X-API-Password": "test-password"}
    url = f"/api/v1/projects/{project.id}/ai/jobs/{job.id}/resume"
    assert client.post(url, json={}).status_code == 401
    assert client.post(url, headers=headers, json={"max_calls": 999, "reset_budget": True}).status_code == 422
    assert client.post(url, headers=headers, json={"instruction": "x" * 6001}).status_code == 422
    assert client.post(f"/api/v1/projects/{other.id}/ai/jobs/{job.id}/resume", headers=headers, json={}).status_code == 404


def test_checkpoint_write_failure_preserves_previous_file_and_is_visible(harness, monkeypatch):
    _, _, _, make_job = harness
    job = make_job(status="paused", pause_reason="round_limit", messages=[{"role": "user", "content": "saved"}])
    ai.persist(job)
    path = ai._checkpoint_path(job.trace_dir, job.id)
    original = path.read_bytes()
    monkeypatch.setattr(ai.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    ai.persist(job)
    assert path.read_bytes() == original
    assert job.public()["trace"][-1]["tool"] == "checkpoint"
    assert job.public()["trace"][-1]["outcome"] == "error"


def test_legacy_failed_trace_is_read_only_not_a_fabricated_continuation(harness):
    project, _, config, _ = harness
    job_id = uuid4().hex
    folder = Path(config["trace_dir"])
    folder.mkdir(parents=True)
    path = folder / (job_id + ".json")
    text = json.dumps({"id": job_id, "project_id": project.id, "base_version": 1,
        "status": "failed", "summary": "old call limit", "calls": 20, "estimated_cny": 1.77,
        "trace": [{"step": 1, "tool": "inspect", "detail": "old record"}]})
    path.write_text(text)
    job = ai.find_job(project.id, job_id)
    assert job.status == "failed" and job.calls == 20 and job.cost == 1.77
    assert not job.public()["can_resume"] and job.public()["scene"] is None
    assert "无法原样续接" in job.summary
    with pytest.raises(HTTPException):
        asyncio.run(ai.resume(job))
    assert path.read_text() == text


@pytest.mark.integration
def test_real_kernel_continues_saved_cube_into_a_through_hole(harness, monkeypatch):
    if not Path(settings.worker_exe).exists():
        pytest.skip("native worker is not built")
    from app.worker_pool import GeometryPool
    _, _, config, _ = harness
    project = get_v1_store().create_project("real isolated continuation")
    job = ai.Job(uuid4().hex, project.id, None, "a cube with a through hole", [], [], [],
                 model="fake", ledger=str(config["budget"].path.resolve()), trace_dir=config["trace_dir"])
    ai.jobs[job.id] = job
    pool = GeometryPool(max_sessions=1)
    monkeypatch.setattr(ai, "get_worker_pool", lambda: pool)
    config["max_calls"] = 1
    current = [job]
    def response(n):
        if n == 1:
            return "run_command", {"action": "component.create", "params": {"kind": "cube", "width": 4, "height": 4, "depth": 4}}
        if n == 2:
            return "run_command", {"action": "component.create", "params": {"kind": "cylinder", "radius": 1, "height": 6}}
        if n == 3:
            parts = current[0].manifest
            return "run_command", {"action": "boolean.subtract", "params": {"targets": [parts[0].hash_sat], "tool": parts[1].hash_sat}}
        return "finish", {"summary": "cube and verified through hole"}
    provider(monkeypatch, response)
    async def exercise():
        await ai.run(job, config)
        assert job.status == "paused" and len(job.manifest) == 1 and not pool._sessions
        ai.jobs.clear()
        current[0] = ai.find_job(project.id, job.id)
        for _ in range(3):
            await ai.resume(current[0])
            await current[0].task
        final = current[0]
        assert final.status == "ready" and final.calls == 4 and len(final.manifest) == 1
        assert final.manifest[0].hash_sat != job.manifest[0].hash_sat
        assert len(final.history) == 3 and not pool._sessions
        assert get_v1_store().latest(project.id) is None
        component = final.manifest[0]
        assets = get_component_store()
        assert len(assets.read("sat", component.hash_sat)) > 100
        assert assets.read("glb", component.hash_glb)[:4] == b"glTF"
        assert component.bbox_min == [-2., -2., -2.] and component.bbox_max == [2., 2., 2.]
        nodes = json.loads(assets.read("graph", component.graph))["nodes"]
        # Six outer planar faces plus the inner cylindrical through-hole face.
        assert sum(any(str(label).upper() == "FACE" for label in node.get("labels", [])) for node in nodes) == 7
        assert ai.apply(final, "human")["version"] == 1
        await pool.shutdown()
    asyncio.run(exercise())
