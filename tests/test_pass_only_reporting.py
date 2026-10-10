import ast
import copy
import html
from pathlib import Path
import unittest
from unittest.mock import MagicMock

import pandas as pd
import code_verifier as verifier


class PassOnlyReportingTests(unittest.TestCase):
    def render(self, result):
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        nodes = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)
                 and node.name in {"render_code_verification", "code_verification_cards_html"}]
        ui = MagicMock()
        ui.columns.return_value = [MagicMock() for _ in range(4)]
        frames = []
        namespace = {"st": ui, "pd": pd, "verifier": verifier, "html": html, "render_centered_dataframe": lambda frame, **kwargs: frames.append(frame)}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), namespace)
        namespace["render_code_verification"](result)
        return ui, frames

    def verification(self):
        return {"scoring_mode": "pass_only", "overall_status": "全部通过", "passed": 1, "total": 1,
                "score": 0, "max_score": 0, "compile": {"status": "成功"},
                "cases": [{"name": "secret case", "visibility": "隐藏", "group": "边界", "weight": 0, "score": 0,
                           "status": "通过", "message": "secret expected-output", "input": "secret-input",
                           "actual_output": "secret-actual", "expected_output": "secret-expected", "stderr": "secret-stderr"}],
                "group_scores": [{"group": "边界", "passed": 1, "total": 1, "score": 0, "max_score": 0}]}

    def test_pass_only_report_has_no_test_scores_and_does_not_change_evidence(self):
        result = self.verification()
        original = copy.deepcopy(result)
        ui, frames = self.render(result)
        cards = ui.markdown.call_args_list[0].args[0]
        self.assertIn("进入答辩的要求", cards)
        self.assertIn("必须全部通过", cards)
        self.assertIn("代码不计分", cards)
        self.assertNotIn("得分", frames[0].columns)
        self.assertNotIn("分组得分", frames[1].columns)
        self.assertEqual(result, original)

    def test_hidden_names_and_error_output_are_not_visible_to_student(self):
        result = self.verification()
        result["cases"][0]["status"] = "运行错误"
        ui, frames = self.render(result)
        text = str(ui.method_calls) + "\n".join(frame.to_string() for frame in frames)
        self.assertNotIn("secret", text)
        self.assertIn("详见教师端运行证据", text)

    def test_historical_weighted_scores_are_still_displayed(self):
        result = self.verification()
        result.pop("scoring_mode")
        result.update(score=75, max_score=100)
        result["cases"][0].update(score=75, weight=100)
        ui, frames = self.render(result)
        cards = ui.markdown.call_args_list[0].args[0]
        self.assertIn("历史测试得分", cards)
        self.assertIn("75 / 100", cards)
        self.assertEqual(frames[0].iloc[0]["得分"], "75/100")


if __name__ == "__main__":
    unittest.main()
