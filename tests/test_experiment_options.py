import ast
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import assignment_drafts
import database as db
import llm_service as llm


class ExperimentOptionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.original_path = db.DB_PATH
        db.DB_PATH = Path(self.folder.name) / "options.db"
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.original_path
        self.folder.cleanup()

    def app_function(self, name):
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        node = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == name)
        ui = MagicMock()
        namespace = {"st": ui, "db": db}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), namespace)
        return namespace[name], ui

    def test_one_hidden_case_is_valid_but_zero_is_blocked(self):
        validate, ui = self.app_function("assignment_form_is_valid")
        args = ["求数组中的最大值", "数组实验", ["输出最大值"], ["初始化"], db.DEFAULT_RUBRIC, True]
        self.assertTrue(validate(*args, [{"name": "单元素", "input": "1\n7\n", "expected_output": "7", "count_only": True}], [], "int main(){}"), str(ui.error.call_args_list))
        self.assertFalse(validate(*args, [], [], "int main(){}"))
        blockers, _ = self.app_function("assignment_publish_blockers")
        assignment = {**db.get_assignment(db.list_assignments()[0]["id"]), "test_cases": [{"input": "1", "expected_output": "1"}]}
        self.assertEqual(blockers(assignment), [])
        self.assertTrue(blockers({**assignment, "test_cases": []}))
        with self.assertRaisesRegex(ValueError, "1至8组"):
            db.create_assignment("实验", "描述", ["要求"], ["重点"], "int main(){}",
                                 verification_enabled=True, test_cases=[], reference_code="int main(){}")

    def test_empty_explanation_only_blocks_required_mode(self):
        validate, ui = self.app_function("student_submission_is_valid")
        args = ["学生", "001", "", "", "int main(){return 0;}"]
        self.assertTrue(validate(*args, explanation_required=False))
        ui.error.assert_not_called()
        self.assertFalse(validate(*args, explanation_required=True))
        self.assertFalse(validate("学生", "001", "x" * 1501, "", args[-1], False))
        self.assertFalse(validate("学生", "001", "", "", "", False))

    def test_choice_survives_create_update_and_submission_snapshot(self):
        args = ["选做思路实验", "描述", ["实现代码"], ["逻辑理解"], "int main(){}"]
        id = db.create_assignment(*args, explanation_required=False)
        assignment = db.get_assignment(id)
        self.assertFalse(assignment["explanation_required"])
        questions = [{"dimension": "程序逻辑理解", "question": "为什么", "reference_points": ["逻辑"]}]
        submission_id = db.create_submission_with_questions("001", "学生", assignment["title"], "", "int main(){}", "2026-10-07 12:00", questions, assignment)
        db.update_assignment(id, *args)
        self.assertFalse(db.get_assignment(id)["explanation_required"])
        db.update_assignment(id, *args, explanation_required=True)
        self.assertTrue(db.get_assignment(id)["explanation_required"])
        self.assertFalse(db.get_submission(submission_id)["assignment_snapshot"]["explanation_required"])

    def test_legacy_migration_defaults_to_required_and_preserves_existing_submission(self):
        id = db.list_assignments()[0]["id"]
        submission_id = db.create_submission_with_questions("001", "学生", "历史实验", "历史思路", "int main(){}", "2026-10-07 12:00", [], db.get_assignment(id))
        original = copy.deepcopy(db.get_submission(submission_id))
        with db.get_connection() as connection:
            connection.execute("ALTER TABLE assignments DROP COLUMN explanation_required")
        db.init_db()
        self.assertTrue(db.get_assignment(id)["explanation_required"])
        self.assertEqual(db.get_submission(submission_id), original)

    def test_teacher_draft_keeps_optional_choice(self):
        assignment_drafts.save_draft(db.DB_PATH, {"title": "草稿", "explanation_required": False})
        self.assertFalse(assignment_drafts.load_draft(db.DB_PATH)["data"]["explanation_required"])

    def test_optional_empty_explanation_has_no_automatic_preliminary_penalty(self):
        response = {"summary": "实现满足要求", "criteria": [{"score": item["weight"], "status": "满足", "confidence": "高", "missing": "无", "needs_defense": False} for item in db.DEFAULT_RUBRIC]}
        with patch.object(llm, "_chat_json", return_value=response) as chat:
            result = llm.preliminary_review("题目", ["要求"], "int main(){}", "", "", db.DEFAULT_RUBRIC, explanation_required=False)
        self.assertEqual(result["completion_score"], 100)
        self.assertIn("未填写本身不是错误", chat.call_args.args[0][1]["content"])
        self.assertIn("不得仅因思路为空而扣分", chat.call_args.args[0][1]["content"])


if __name__ == "__main__":
    unittest.main()
