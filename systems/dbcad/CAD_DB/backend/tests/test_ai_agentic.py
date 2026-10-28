"""Meaningful native-language, rollback, recovery and official-loop contracts."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai_agentic import AgenticBridge, AgenticCallbacks, run_agentic
from app.ai_cad_facade import CadRef, CadSession


# A valid 1x1 PNG exercises real MCP image transport without a CAD dependency.
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aT9sAAAAASUVORK5CYII="


class Fixture:
    def __init__(self):
        self.job = SimpleNamespace(manifest=[], original=[], projections=[], selected=[],
                                   messages=[], trace=[], agentic_state={}, instruction="Create and verify a plate",
                                   cancelled=False)
        self.commands = []
        self.requests = []
        self.models = []
        self.callbacks = AgenticCallbacks(self.execute, self.query, self.render, self.model)

    async def execute(self, action, params):
        self.commands.append((action, params))
        digest = hashlib.sha256(json.dumps([action, params, len(self.commands)], sort_keys=True).encode()).hexdigest()
        entry = {"sat_bytes": digest, "glb_bytes": digest, "bbox": {"min": [-1, -1, -1], "max": [1, 1, 1]}}
        if action in {"component.create", "component.extrude", "component.revolve", "component.sweep"}:
            return {"added": [entry]}
        if action == "component.delete":
            return {"removed": [params["component"]]}
        if action.startswith("boolean."):
            entry["sid"] = params["targets"][0]
            return {"modified": [entry], "removed": [params["tool"]]}
        entry["sid"] = params["component"]
        return {"modified": [entry]}

    async def query(self, request):
        self.requests.append(request)
        if request["kind"] in {"faces", "edges"}:
            return {"items": [{"index": 2, "type": "LINE", "center": [1, 0, 0]},
                              {"index": 5, "type": "CIRCLE", "center": [4, 0, 0]}], "truncated": False}
        return {"components": [c.to_scene_json() for c in self.job.manifest], "valid": True, "distance": 3.0}

    async def render(self, views, faces, edges):
        return [PNG for _ in views]

    async def model(self, messages, tools):
        self.models.append((messages, tools))
        raise AssertionError("A test must explicitly supply deterministic model responses")


def test_atomic_step_restores_geometry_bindings_aliases_and_projection_history():
    fixture = Fixture()

    async def check():
        session = CadSession(fixture.job, fixture.callbacks)
        assert (await session.run_step("a=cad.box(20,10,4); alias=a; dims=[1,2]"))["ok"]
        first = fixture.job.manifest[0].hash_sat
        assert (await session.run_step("a=cad.translate(a,[1,0,0]); alias=a; dims=[9]"))["ok"]
        second = fixture.job.manifest[0].hash_sat
        failed = await session.run_step("dims=[0]; alias=cad.sphere(2); value=__import__('os')")
        assert failed["ok"] is False and failed["rolled_back"]
        assert [c.hash_sat for c in fixture.job.manifest] == [second]
        assert session.bindings["alias"] == CadRef(second)
        assert session.bindings["dims"] == [9]
        assert len(session.history) == 2
        assert (await session.undo())["ok"]
        assert [c.hash_sat for c in fixture.job.manifest] == [first]
        assert session.bindings["a"] == session.bindings["alias"] == CadRef(first)
        assert session.bindings["dims"] == [1, 2]
        assert len(fixture.job.projections) == 1

    asyncio.run(check())


def test_queries_do_not_consume_undo_and_checkpoint_restores_native_state():
    fixture = Fixture()

    async def check():
        session = CadSession(fixture.job, fixture.callbacks)
        await session.run_step("result=cad.box(20,10,4); alias=result")
        query = await session.run_step("print(cad.edges(result)); print(cad.validate(result))")
        assert query["ok"] and not query["changed_geometry"]
        assert len(session.history) == 1
        fixture.job.agentic_state = json.loads(json.dumps(fixture.job.agentic_state))
        session = CadSession(fixture.job, fixture.callbacks)
        assert session.bindings["result"] == session.bindings["alias"]
        await session.run_step("result=cad.translate(result,[0,0,5])")
        assert (await session.undo())["ok"]
        assert session.bindings["result"] == session.bindings["alias"]
        assert len(fixture.job.manifest) == 1

    asyncio.run(check())


@pytest.mark.parametrize("code", [
    "import os", "open('/etc/passwd')", "().__class__.__bases__", "cad.__class__",
    "f=cad.box", "exec('1')", "while True: pass", "for i in range(100000): pass",
    "x=[1]*1000000", "x=2**1000000", "cad.box(1,2,float('nan'))",
    "cad.box(True,2,3)", "x={1:'bad'}", "cad.call('box',[1,2,3],{})",
])
def test_generated_program_cannot_escape_or_exceed_bounds(code):
    fixture = Fixture()

    async def check():
        session = CadSession(fixture.job, fixture.callbacks)
        result = await session.run_step(code)
        assert result["ok"] is False
        assert fixture.job.manifest == []
        assert session.bindings == {}
        assert session.history == []

    asyncio.run(check())


def test_native_modeling_queries_and_edge_selection_map_to_worker_contract():
    fixture = Fixture()

    async def check():
        session = CadSession(fixture.job, fixture.callbacks)
        result = await session.run_step(
            "part=cad.extrude({'kind':'rectangle','width':30,'height':20},5); "
            "hole=cad.cylinder(2,12); part=cad.subtract(part,hole); "
            "edges=[e['index'] for e in cad.edges(part) if e['type']=='LINE']; "
            "part=cad.fillet(part,edges,0.5); "
            "part=cad.chamfer(part,[cad.nearest_edge(part,[4,0,0])],0.2); "
            "print(cad.validate(part))"
        )
        assert result["ok"], result
        assert len(fixture.job.manifest) == 1
        assert [c[0] for c in fixture.commands] == ["component.extrude", "component.create", "boolean.subtract",
                                                    "component.fillet", "component.chamfer"]
        assert fixture.commands[-2][1]["edges"] == [2]
        assert fixture.commands[-1][1]["edges"] == [5]
        assert "e" not in session.bindings
        assert fixture.requests[-1]["kind"] == "inspect"

    asyncio.run(check())


def test_loop_program_and_explicit_stale_reference_error():
    fixture = Fixture()

    async def check():
        session = CadSession(fixture.job, fixture.callbacks)
        result = await session.run_step("r=2\nfor i in range(3):\n p=cad.sphere(r,center=[i*5,0,0])\n")
        assert result["ok"] and len(fixture.job.manifest) == 3
        result = await session.run_step("alias=p; p=cad.translate(p,[0,0,5]); print(cad.validate(alias))")
        assert result["ok"] is False and "stale" in result["error"]
        assert len(fixture.job.manifest) == 3
        assert "alias" not in session.bindings

    asyncio.run(check())


def test_nearest_entity_ties_and_truncation_do_not_silently_select():
    fixture = Fixture()

    async def query(request):
        return {"items": [{"index": 1, "center": [0, 0, 0]}, {"index": 3, "center": [0, 0, 0]}]}

    fixture.callbacks.query = query

    async def check():
        session = CadSession(fixture.job, fixture.callbacks)
        await session.run_step("p=cad.box(2,2,2)")
        assert not (await session.run_step("edge=cad.nearest_edge(p,[0,0,0])"))["ok"]
        assert len(session.history) == 1

    asyncio.run(check())


def test_geometry_rollback_also_applies_if_final_inspection_fails():
    fixture = Fixture()

    async def query(request):
        raise RuntimeError("query failed")

    fixture.callbacks.query = query

    async def check():
        session = CadSession(fixture.job, fixture.callbacks)
        response = await session.run_step("p=cad.box(1,2,3)")
        assert response["ok"] is False
        assert fixture.job.manifest == [] and session.history == [] and session.bindings == {}

    asyncio.run(check())


def test_cancelled_native_step_rolls_back_complete_preview_and_bindings():
    fixture = Fixture()
    ordinary = fixture.execute

    async def execute(action, params):
        if action == "component.translate":
            raise asyncio.CancelledError()
        return await ordinary(action, params)

    fixture.callbacks.execute = execute

    async def check():
        session = CadSession(fixture.job, fixture.callbacks)
        with pytest.raises(asyncio.CancelledError):
            await session.run_step("a=cad.box(1,2,3); alias=a; a=cad.translate(a,[1,0,0])")
        assert fixture.job.manifest == [] and fixture.job.projections == []
        assert session.bindings == {} and session.history == []

    asyncio.run(check())


def test_bridge_preserves_model_errors_and_restricts_export_directory(tmp_path):
    fixture = Fixture()
    error = RuntimeError("provider unavailable")

    async def model(messages, tools):
        raise error

    fixture.callbacks.model = model

    async def check():
        bridge = AgenticBridge(fixture.job, fixture.callbacks, tmp_path)
        with pytest.raises(RuntimeError):
            await bridge.dispatch("model", {"messages": [{"role": "user", "content": "build"}], "tools": []})
        assert bridge.fatal is error
        with pytest.raises(ValueError):
            await bridge.dispatch("tool", {"name": "export_step", "arguments": {"output_dir": "/etc"}})
        result = await bridge.dispatch("tool", {"name": "export_step", "arguments": {"output_dir": str(tmp_path)}})
        assert result["ok"] is False and "SAT" in result["error"]

    asyncio.run(check())


@pytest.mark.integration
def test_pinned_official_loop_executes_native_mcp_and_returns_real_image_blocks(tmp_path):
    upstream, python = os.environ.get("CAD_AGENT_UPSTREAM"), os.environ.get("CAD_AGENT_PYTHON")
    if not upstream or not python:
        pytest.skip("Set CAD_AGENT_UPSTREAM and CAD_AGENT_PYTHON for the fixed official environment")
    fixture = Fixture()
    turns = [
        [("run_step", {"code": "result=cad.box(20,10,4)"})],
        [("list_edges", {}), ("render_views", {"views": ["front"]})],
        [("finish", {"summary": "已生成并验证预览", "success": True})],
    ]

    async def model(messages, tools):
        index = len(fixture.models)
        fixture.models.append((messages, tools))
        assert {t["function"]["name"] for t in tools} == {
            "run_step", "inspect", "list_faces", "list_edges", "render_views", "undo", "reset", "export_step", "finish"}
        message = {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"call-{index}-{n}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            for n, (name, args) in enumerate(turns[index])
        ]}
        return {"choices": [{"message": message}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}}

    async def export(output_dir):
        path = Path(output_dir) / "tmp.step"
        path.write_text("ISO-10303-21;\nEND-ISO-10303-21;")
        return {"ok": True, "step_path": str(path)}

    fixture.callbacks.model, fixture.callbacks.export_step = model, export
    result = asyncio.run(run_agentic(fixture.job, {
        "agentic_upstream": upstream, "render_python": python, "max_calls": 5,
    }, fixture.callbacks))
    assert result["finished"] and result["success"] and result["step_exported"]
    assert result["framework"] == "AgenticCADedit"
    assert len(fixture.job.manifest) == 1 and len(fixture.models) == 3
    last_messages = fixture.models[-1][0]
    assert any(isinstance(m.get("content"), list) and any(c.get("type") == "image_url" for c in m["content"])
               for m in last_messages)
    assert fixture.job.agentic_state["bindings"]["result"]["$cad"] == fixture.job.manifest[0].hash_sat


@pytest.mark.integration
def test_official_loop_retains_public_clarification_and_accepts_next_human_turn(tmp_path):
    upstream, python = os.environ.get("CAD_AGENT_UPSTREAM"), os.environ.get("CAD_AGENT_PYTHON")
    if not upstream or not python:
        pytest.skip("Set CAD_AGENT_UPSTREAM and CAD_AGENT_PYTHON for the fixed official environment")
    fixture = Fixture()
    question = "出水口直径需要多少毫米？"
    async def model(messages, tools):
        fixture.models.append((messages, tools))
        if len(fixture.models) == 1:
            message = {"role": "assistant", "content": question}
        else:
            assert any(m.get("content") == question for m in messages)
            assert messages[-1]["content"] == "直径 12 mm"
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "clarification-finish", "type": "function", "function": {
                    "name": "finish", "arguments": json.dumps({"summary": "已记录直径", "success": True})}}]}
        return {"choices": [{"message": message}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}}
    fixture.callbacks.model = model
    config = {"agentic_upstream": upstream, "render_python": python, "max_calls": 2}
    async def check():
        result = await run_agentic(fixture.job, config, fixture.callbacks)
        assert result["exit_reason"] == "no_tool_call" and result["summary"] == question
        fixture.job.messages.append({"role": "user", "content": "直径 12 mm"})
        result = await run_agentic(fixture.job, config, fixture.callbacks)
        assert result["finished"] and result["summary"] == "已记录直径"
    asyncio.run(check())


def test_explicit_overview_view_keeps_existing_render_arguments():
    fixture = Fixture()
    session = CadSession(fixture.job, fixture.callbacks)
    result = asyncio.run(session.render(["front", "overview", "front"]))
    assert result["ok"] and result["views"] == ["front", "overview", "front"]
    assert len(result["images"]) == 3


@pytest.mark.integration
def test_official_loop_resumes_same_native_state_across_subprocess_segments(tmp_path):
    upstream, python = os.environ.get("CAD_AGENT_UPSTREAM"), os.environ.get("CAD_AGENT_PYTHON")
    if not upstream or not python:
        pytest.skip("Set CAD_AGENT_UPSTREAM and CAD_AGENT_PYTHON for the fixed official environment")
    fixture = Fixture()
    programs = ["result=cad.box(20,10,4); alias=result", "result=cad.translate(result,[0,0,5])"]

    async def model(messages, tools):
        index = len(fixture.models)
        fixture.models.append((messages, tools))
        if index == 1:
            assert any(message.get("role") == "tool" for message in messages)
            assert any("alias=result" in json.dumps(message) for message in messages)
        name = "run_step" if index < 2 else "finish"
        args = {"code": programs[index]} if index < 2 else {"summary": "完成", "success": True}
        return {"choices": [{"message": {"role": "assistant", "content": None,
            "tool_calls": [{"id": f"resume-{index}", "type": "function", "function": {
                "name": name, "arguments": json.dumps(args)}}]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10}}

    fixture.callbacks.model = model
    config = {"agentic_upstream": upstream, "render_python": python, "max_calls": 1}

    async def check():
        first = await run_agentic(fixture.job, config, fixture.callbacks)
        assert not first["finished"] and first["exit_reason"] == "round_limit"
        original = fixture.job.manifest[0].hash_sat
        # Simulate JSON persistence and a new runner/MCP process, preserving assets.
        fixture.job.agentic_state = json.loads(json.dumps(fixture.job.agentic_state))
        fixture.job.messages = json.loads(json.dumps(fixture.job.messages))
        fixture.job.messages.append({"role": "user", "content": "继续保留预览"})
        second = await run_agentic(fixture.job, {**config, "max_calls": 3}, fixture.callbacks)
        assert second["finished"] and second["success"]
        assert len(fixture.job.manifest) == 1
        assert fixture.job.manifest[0].hash_sat != original
        assert fixture.job.agentic_state["bindings"]["alias"]["$cad"] == original
        session = CadSession(fixture.job, fixture.callbacks)
        await session.undo()
        assert fixture.job.manifest[0].hash_sat == original
        assert session.bindings["result"] == session.bindings["alias"]

    asyncio.run(check())

