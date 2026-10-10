import copy
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import MagicMock, patch

import code_verifier as verifier
import database as db
from execution_guard import ProtectionError


class ExecutionPolicyTests(unittest.TestCase):
    def assignment(self):
        return {"public_samples": [{"name": "visible", "input": "1", "output": "1"}],
                "test_cases": [{"name": "hidden", "input": "2", "expected_output": "2", "weight": 100}]}

    def test_formal_cases_include_both_visibilities_without_mutating_assignment(self):
        assignment = self.assignment()
        original = copy.deepcopy(assignment)
        cases = verifier.formal_test_cases(assignment)
        self.assertEqual([case["visibility"] for case in cases], ["公开", "隐藏"])
        self.assertTrue(all(case["count_only"] for case in cases))
        self.assertEqual([item["weight"] for item in verifier._case_scoring_metadata(cases)], [0, 0])
        self.assertEqual(assignment, original)

    def test_count_only_normalization_keeps_zero_weights_and_legacy_keeps_scores(self):
        count_only = verifier.formal_test_cases(self.assignment())
        normalized = db.normalize_test_cases(count_only)
        self.assertTrue(all(case["count_only"] and case["weight"] == 0 for case in normalized))
        self.assertEqual(sum(case["weight"] for case in db.normalize_test_cases(self.assignment()["test_cases"])), 100)

    def test_mixed_scoring_policies_are_rejected(self):
        cases = verifier.formal_test_cases(self.assignment())
        cases[1].pop("count_only")
        with self.assertRaises(ValueError):
            db.normalize_test_cases(cases)

    def test_busy_judge_does_not_start_backend_or_release_unacquired_slot(self):
        slots = MagicMock()
        slots.acquire.return_value = False
        with patch.object(verifier, "_execution_slots", slots), patch.object(verifier, "get_execution_backend") as backend:
            result = verifier.verify_cpp_code("code", [])
            self.assertEqual(result["overall_status"], "执行繁忙")
            backend.assert_not_called()
            slots.release.assert_not_called()

    def test_backend_failure_always_releases_slot(self):
        slots = MagicMock()
        slots.acquire.return_value = True
        backend = MagicMock()
        backend.verify_cpp_code.side_effect = ProtectionError("guard failed")
        with patch.object(verifier, "_execution_slots", slots), patch.object(verifier, "get_execution_backend", return_value=backend):
            with self.assertRaises(ProtectionError):
                verifier.verify_cpp_code("code", [])
            slots.release.assert_called_once()

    def test_total_budget_exhaustion_is_not_misreported_as_student_timeout(self):
        with (patch.dict(os.environ, {"CODE_EXECUTION_ENABLED": "true", "CODE_EXECUTION_TOTAL_TIMEOUT_SECONDS": "3"}),
              patch.object(verifier, "_compiler_path", return_value="compiler"),
              patch.object(verifier, "_compile_local", return_value=subprocess.CompletedProcess([], 0, "", "")),
              patch.object(verifier.time, "monotonic", side_effect=[0, 0, 2.5]),
              patch.object(verifier.execution_guard, "run_bounded") as run):
            result = verifier._verify_cpp_code_local("int main(){}", [{"input": "", "expected_output": ""}], "c++14")
        self.assertEqual(result["overall_status"], "评测中止")
        run.assert_not_called()

    def test_failed_runtime_protection_is_environment_error(self):
        with (patch.dict(os.environ, {"CODE_EXECUTION_ENABLED": "true"}),
              patch.object(verifier, "_compiler_path", return_value="compiler"),
              patch.object(verifier, "_compile_local", return_value=subprocess.CompletedProcess([], 0, "", "")),
              patch.object(verifier.execution_guard, "run_bounded", side_effect=ProtectionError("guard failed"))):
            result = verifier._verify_cpp_code_local("int main(){}", [{"input": "", "expected_output": ""}], "c++14")
        self.assertEqual(result["overall_status"], "环境不可用")
        self.assertEqual(result["cases"], [])


if __name__ == "__main__":
    unittest.main()
