"""Censored experiments must not silently become successful/safe model trials."""
import json
from pathlib import Path
import tempfile
import unittest

from audit import audit_trial


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)
        self.folder = self.run / "model/pin/E3-N"

    def write(self, relative, data):
        path = self.folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def test_orchestrator_completed_is_not_evaluated_when_budget_pauses_second_stage(self):
        self.write("record.json", {"status": "completed"})
        for stage, finished, reason in ((11, True, None), (12, False, "budget_limit")):
            self.write(f"s{stage}/result.json", {"finished": finished,
                "job": {"id": f"job{stage}", "pause_reason": reason, "summary": ""},
                "geometry": {"geometry_success": True, "task_success": finished, "unsafe": False}})
        row, _, _ = audit_trial("model", self.run, "pin", "E3", "N", [], [], {})
        self.assertEqual(row["execution_status"], "budget_censored")
        self.assertFalse(row["s12_finished"])
        self.assertIsNone(row["confirmed_expired_memory_misuse"])

    def test_missing_result_still_accounts_charges_and_visible_exact_violation(self):
        self.write("s11/input.json", {"parent": "parent", "instruction": "continue"})
        self.write("exception.json", {"type": "HTTPException", "detail": "402: budget exhausted"})
        self.write("s11/tool-events.json", [{"tool": "run_step", "oracle": {"unsafe": False},
            "exact_hidden": {"distance": {"distance": 1.5}}}])
        calls = [{"label": "native-ai/job", "status": "completed", "charged_or_reserved_cny": .5}]
        receipt = {"id": "job", "parent_job_id": "parent", "instruction": "continue"}
        row, unsafe, _ = audit_trial("model", self.run, "pin", "E3", "N", calls, [receipt], {})
        self.assertEqual(row["cost_cny"], .5)
        self.assertTrue(row["visible_clearance_violation"])
        self.assertEqual(len(unsafe), 1)
        self.assertEqual(row["execution_status"], "budget_censored")

    def test_unexecuted_trial_does_not_supply_observed_safe_evidence(self):
        row, _, _ = audit_trial("model", self.run, "pin", "E3", "N", [], [], {})
        self.assertEqual(row["execution_status"], "not_executed")
        self.assertIsNone(row["visible_clearance_violation"])


if __name__ == "__main__":
    unittest.main()
