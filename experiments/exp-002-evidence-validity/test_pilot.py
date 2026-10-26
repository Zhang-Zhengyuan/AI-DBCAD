"""Offline real-kernel and real-MCP checks; these never call a model API."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

import cadquery as cq

from pilot import Server, host_step, loop
from src.vlms.base_vlm import AssistantTurn, BaseVLM, ToolCall
from scenes import EVENTS, compound, geometry_error, initial_code, refresh_code, score, shapes, spec, truth


class OracleTests(unittest.TestCase):
    def test_analytic_kernel_and_final_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "final.step"
            for name in ("pin", "slider"):
                s = spec(name)
                for event in EVENTS:
                    gt = truth(s, event)
                    self.assertEqual(gt["feasible"], event in {"E0", "E1"})
                    for placed in (False, True):
                        cq.exporters.export(compound(shapes(s, event, placed)), str(path))
                        result = score(path, s, event, "finished")
                        self.assertEqual(result["geometry_success"], placed == gt["feasible"])
                        self.assertEqual(result["unsafe_placement"], placed and not gt["feasible"])
                        self.assertFalse(score(path, s, event, "iteration_limit")["geometry_success"])

    def test_static_damage_is_not_a_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = spec("slider")
            p = shapes(s, "E0", True)
            del p["obstacle"]
            path = Path(tmp) / "broken.step"
            cq.exporters.export(compound(p), str(path))
            self.assertEqual(score(path, s, "E0", "finished")["final_position"], "other")


class MCPTests(unittest.TestCase):
    def test_controller_pauses_after_actual_measurement(self):
        class ScriptedAgent:
            build_tool_specs = BaseVLM.build_tool_specs
            FINISH_TOOL = BaseVLM.FINISH_TOOL
            tool_result_text = BaseVLM.tool_result_text
            create_tool_result_message = BaseVLM.create_tool_result_message
            tool_result_images = BaseVLM.tool_result_images
            def request_tool_turn(self, messages, tools):
                return AssistantTurn(messages=[{"role": "assistant", "content": "preflight"}],
                    tool_calls=[ToolCall("offline", "run_step", {"code": refresh_code(spec("pin"))})])
        async def exercise(folder):
            async with Server(folder) as server:
                await host_step(server, initial_code(spec("pin")))
                status = await loop(ScriptedAgent(), server, [], folder, 2, checkpoint_mode=True)
                self.assertEqual(status, "checkpoint_ready")
                self.assertTrue(any(t["actor"] == "state_audit" for t in server.events))
                await server.call("export_step", {"output_dir": str(folder)}, "audit")
                actual = cq.importers.importStep(str(folder / "tmp.step")).val()
                self.assertLess(geometry_error(actual, compound(shapes(spec("pin")))), 1e-5)
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)))

    def test_actual_server_refresh_bindings_and_render(self):
        async def exercise(folder):
            async with Server(folder) as server:
                for name in ("pin", "slider"):
                    s = spec(name)
                    for event in EVENTS:
                        await host_step(server, initial_code(s))
                        await host_step(server, "candidate_old = movable.translate((%s-movable.val().Center().x,0,0))" % s["target_x"])
                        await host_step(server, initial_code(s, event), "external_editor")
                        current = await host_step(server, refresh_code(s), "audit")
                        self.assertIn("distance", current["stdout"])
                        # Canonical namespace and displayed/exported compound are both current.
                        await server.call("export_step", {"output_dir": str(folder)}, "audit")
                        actual = cq.importers.importStep(str(folder / "tmp.step")).val()
                        self.assertLess(geometry_error(actual, compound(shapes(s, event))), 1e-5)
                        data = await host_step(server, "print(obstacle is parts['obstacle'], movable is parts['movable'])")
                        self.assertEqual(data["stdout"].strip(), "True True")
                render = await server.call("render_views", {"views": ["toprightiso"]}, "audit")
                self.assertTrue(any(b.type == "image" for b in render.content))
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
