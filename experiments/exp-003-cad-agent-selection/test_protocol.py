"""Finite offline regression checks for the frozen-task and geometry contracts."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geometry import export_step, score, trusted_shape
from suite import cases, program, public_case, suite_manifest


class ProtocolTests(unittest.TestCase):
    def test_suite_has_all_predeclared_groups(self):
        manifest = suite_manifest()
        self.assertEqual(manifest["case_count"], 12)
        self.assertEqual(manifest["stage_count"], 24)
        groups = [c["group"] for c in manifest["cases"]]
        self.assertEqual(groups.count("source"), 8)
        self.assertEqual(groups.count("external_source"), 2)
        self.assertEqual(groups.count("external_step"), 2)

    def test_public_request_excludes_oracles_and_human_implementation(self):
        for case in cases():
            public = public_case(case)
            self.assertNotIn("reference_code", json.dumps(public))
            manual = public.get("manual_after_s1")
            if manual:
                self.assertEqual(set(manual), {"description"})

    def _score(self, actual_code, reference_code):
        with tempfile.TemporaryDirectory(prefix="exp003-oracle-") as temp:
            path = Path(temp) / "actual.step"
            export_step(trusted_shape(actual_code), path)
            return score(path, reference_code)

    def test_wrong_bore_fails_even_with_identical_bbox(self):
        expected = program("bore", height=8, hole=10)
        actual = program("bore", height=8, hole=8)
        grade = self._score(actual, expected)
        self.assertFalse(grade["geometry_success"])
        self.assertLess(grade["bbox_max_error_mm"], 1e-5)
        self.assertGreater(grade["symmetric_difference_mm3"], 100)

    def test_missing_human_added_boss_fails(self):
        case = next(c for c in cases() if c["id"] == "external-source-boss")
        grade = self._score(program("boss", hole=10, boss=False), case["stages"][1]["reference_code"])
        self.assertFalse(grade["geometry_success"])
        self.assertGreater(grade["symmetric_difference_mm3"], 100)

    def test_translated_geometry_fails(self):
        expected = program("block", width=50, hole=10)
        grade = self._score(expected + "r = r.translate((1,0,0))\n", expected)
        self.assertFalse(grade["geometry_success"])
        self.assertGreater(grade["bbox_max_error_mm"], 0.9)

    def test_coincident_swept_tube_step_roundtrip_passes(self):
        code = program("pipe", outer=6, end_x=50)
        grade = self._score(code, code)
        self.assertTrue(grade["geometry_success"], grade)
        self.assertLess(grade["symmetric_difference_mm3"], 1e-3)


if __name__ == "__main__":
    unittest.main()
