"""Preview isolation, atomic publication, stale bases and provider failure paths."""

import asyncio
import json
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import ai_editor as ai
from app import main
from app.ai_budget import Budget
from app.component_store import ComponentStore, set_component_store
from app.config import settings
from app.v1_store import (
    SceneComponent,
    get_v1_store,
    initialize_v1_store,
    shutdown_v1_store,
)


@pytest.fixture
def scene(tmp_path, monkeypatch):
    initialize_v1_store(memory=True)
    set_component_store(ComponentStore(tmp_path / "assets"))
    ai.jobs.clear()
    p = get_v1_store().create_project("AI test")
    original = SceneComponent("a" * 64, "b" * 64, name="original")
    get_v1_store().commit_version(
        p.id, message="seed", author="test", components=[original]
    )
    yield p.id, original, tmp_path
    ai.jobs.clear()
    set_component_store(None)
    shutdown_v1_store()


def test_preview_apply_once_conflict_and_undo(scene):
    pid, original, _ = scene
    store = get_v1_store()
    changed = SceneComponent("c" * 64, "d" * 64)
    job = ai.Job("j", pid, 1, "move", [], [original], [changed], status="ready")
    ai.jobs["j"] = job
    assert store.latest(pid).number == 1
    result = ai.apply(job, "test")
    assert result["version"] == 2
    assert store.latest(pid).components == [changed]
    with pytest.raises(HTTPException) as error:
        ai.apply(job, "duplicate")
    assert error.value.status_code == 409
    from app.api.v1.commands import undo

    undo(pid)
    assert store.latest(pid).components == [original]
    stale = ai.Job("stale", pid, 1, "edit", [], [original], [changed], status="ready")
    with pytest.raises(HTTPException) as error:
        ai.apply(stale, "stale")
    assert error.value.status_code == 409


def test_empty_base_does_not_overwrite_new_head(scene):
    pid, original, _ = scene
    job = ai.Job("j", pid, None, "create", [], [], [original], status="ready")
    with pytest.raises(HTTPException):
        ai.apply(job, "stale empty project")
    assert get_v1_store().latest(pid).number == 1


def test_auth_scope_payload_and_cancel(scene, monkeypatch):
    pid, original, _ = scene
    job = ai.Job("j", pid, 1, "edit", [], [original], [original], status="ready")
    ai.jobs["j"] = job
    client = TestClient(main.app)
    monkeypatch.setattr(settings, "api_password", "test-password")
    url = f"/api/v1/projects/{pid}/ai/jobs"
    assert client.get("/api/v1/ai/status").status_code == 401
    headers = {"X-API-Password": "test-password"}
    assert (
        client.post(url, json={"instruction": "xx"}, headers=headers).status_code == 422
    )
    assert (
        client.post(
            url, json={"instruction": "xx", "base_version": True}, headers=headers
        ).status_code
        == 422
    )
    assert (
        client.get("/api/v1/projects/other/ai/jobs/j", headers=headers).status_code
        == 404
    )
    response = client.post(url + "/j/cancel", headers=headers)
    assert response.json()["status"] == "cancelled"
    assert client.post(url + "/j/apply", json={}, headers=headers).status_code == 409
    assert get_v1_store().latest(pid).number == 1


def test_command_validation_rejects_injection_and_stale_ids(scene):
    pid, original, _ = scene
    job = ai.Job("j", pid, 1, "edit", [], [original], [original])
    for action, params in [
        ("exec", {"code": "bad"}),
        ("component.translate", {"component": "unknown", "offset": [0, 0, 1]}),
        ("component.create", {"kind": "cube", "width": float("nan")}),
        ("component.create", {"kind": "cube", "file": "/etc/passwd"}),
        ("boolean.subtract", {"targets": original.hash_sat, "tool": original.hash_sat}),
    ]:
        with pytest.raises((ValueError, HTTPException)):
            ai.validate_command(action, params, job)
    ai.validate_command(
        "component.translate",
        {"component": original.hash_sat, "offset": [1, 2, 3]},
        job,
    )


def test_start_checks_base_and_selection_before_provider(scene, monkeypatch):
    pid, _, _ = scene
    monkeypatch.setattr(ai, "configuration", lambda: {})

    async def check():
        with pytest.raises(HTTPException) as error:
            await ai.start(pid, 0, "move", [])
        assert error.value.status_code == 409
        with pytest.raises(HTTPException) as error:
            await ai.start(pid, 1, "move", ["unknown"])
        assert error.value.status_code == 422

    asyncio.run(check())
    assert not ai.jobs


def test_provider_failure_keeps_reservation_and_scene(scene, monkeypatch):
    pid, original, folder = scene

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            raise RuntimeError("offline provider")

    class Pool:
        async def release(self, *args):
            pass

    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kwargs: Client())
    monkeypatch.setattr(ai, "get_worker_pool", lambda: Pool())
    budget = Budget(
        folder / "budget.json",
        10,
        {
            "model": "fake",
            "context_tokens": 10000,
            "input_cny_per_million": 3,
            "output_cny_per_million": 18,
        },
    )
    job = ai.Job("j", pid, 1, "move", [], [original], [original])
    asyncio.run(
        ai.run(
            job,
            {
                "budget": budget,
                "pricing": budget.pricing,
                "base_url": "https://example.invalid",
                "api_key": "never-log",
            },
        )
    )
    assert job.status == "failed"
    assert budget.summary()["calls"] == 1
    assert budget.summary()["charged_or_reserved_cny"] > 0
    assert get_v1_store().latest(pid).components == [original]
    assert "never-log" not in json.dumps(job.public())


def test_preview_ancestry_collapses_to_captured_base(scene, monkeypatch):
    pid, original, _ = scene
    middle = "e" * 64
    final = SceneComponent("c" * 64, "d" * 64)
    job = ai.Job("j", pid, 1, "two edits", [], [original], [final], status="ready")
    job.projections = [
        (middle, {"step": 1}, original.hash_sat),
        (final.hash_sat, {"step": 2}, middle),
    ]
    captured = []
    monkeypatch.setattr(
        ai, "write_projections", lambda store, pairs: captured.extend(pairs)
    )
    ai.apply(job, "test")
    assert captured == [(final.hash_sat, {"step": 2}, original.hash_sat)]


def test_projection_failure_reports_committed_version(scene, monkeypatch):
    pid, original, _ = scene
    changed = SceneComponent("c" * 64, "d" * 64)
    job = ai.Job("j", pid, 1, "move", [], [original], [changed], status="ready")

    def unavailable(*args):
        raise RuntimeError("projection service unavailable")

    monkeypatch.setattr(ai, "write_projections", unavailable)
    result = ai.apply(job, "test")
    assert result["status"] == "applied" and result["version"] == 2
    assert result["trace"][-1]["outcome"] == "warning"
    assert get_v1_store().latest(pid).components == [changed]


def test_native_tool_timeout_releases_preview_worker(scene, monkeypatch):
    pid, original, _ = scene
    job = ai.Job("j", pid, 1, "edit", [], [original], [original])

    class SlowPool:
        released = False

        async def execute(self, *args):
            await asyncio.sleep(2)

        async def release(self, session):
            assert session == "ai-j"
            self.released = True

    pool = SlowPool()
    monkeypatch.setattr(ai, "get_worker_pool", lambda: pool)
    monkeypatch.setattr(ai, "NATIVE_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(RuntimeError, match="候选 worker 已停止"):
        asyncio.run(
            ai.native_command(
                job,
                "component.translate",
                {"component": original.hash_sat, "offset": [1, 0, 0]},
            )
        )
    assert pool.released and job.manifest == [original]
    assert get_v1_store().latest(pid).number == 1
