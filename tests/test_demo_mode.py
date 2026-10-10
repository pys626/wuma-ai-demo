import os
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import database as db
import demo_mode
import code_verifier as verifier
import llm_provider as provider
import llm_service as llm
import runtime_config


class DemoModeTests(unittest.TestCase):
    def setUp(self):
        self.temp_folder = tempfile.TemporaryDirectory()
        self.original_path = db.DB_PATH
        db.DB_PATH = Path(self.temp_folder.name) / "wuma_ai_demo.db"
        demo_mode.prepare_demo_database(db.DB_PATH)
        db.init_db()
        self.app_path = Path(__file__).resolve().parents[1] / "app.py"

    def tearDown(self):
        db.DB_PATH = self.original_path
        self.temp_folder.cleanup()

    def test_demo_seed_is_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(demo_mode.ensure_demo_data())
        self.assertEqual(db.list_submissions(), [])

    def test_demo_seed_creates_synthetic_progress_and_teacher_task_once(self):
        with patch.dict(
            os.environ,
            {"WUMA_DEMO_MODE": "true", "WUMA_DEMO_READ_ONLY": "true"},
            clear=False,
        ):
            self.assertTrue(demo_mode.ensure_demo_data())
            self.assertFalse(demo_mode.ensure_demo_data())

        submissions = db.list_submissions()
        self.assertEqual(len(submissions), 3)
        zhang = db.list_learning_records(student_id=demo_mode.DEMO_STUDENT_ID)
        self.assertEqual([item["overall"] for item in zhang], [82, 93])
        self.assertTrue(all(item["teacher_reviewed"] for item in zhang))
        pending = next(item for item in submissions if item["student_id"] == "20260002")
        self.assertTrue(pending["review_required"])
        self.assertFalse(pending["teacher_reviewed"])
        self.assertEqual(len(db.list_student_feedbacks(status="待处理")), 1)

    def test_demo_seed_never_adds_records_to_existing_database(self):
        db.create_submission_with_questions(
            "existing",
            "已有学生",
            "已有实验",
            "已有说明",
            "int main(){}",
            "2026-09-17 12:00",
            [
                {
                    "dimension": "程序逻辑理解",
                    "question": "已有问题",
                    "reference_points": ["已有证据"],
                }
            ],
        )
        with patch.dict(os.environ, {"WUMA_DEMO_MODE": "true"}, clear=False):
            self.assertFalse(demo_mode.ensure_demo_data())
        self.assertEqual(len(db.list_submissions()), 1)

    def test_public_demo_buttons_open_restricted_student_and_teacher_routes(self):
        demo_environment = {
            "WUMA_DEMO_MODE": "true",
            "WUMA_DEMO_READ_ONLY": "true",
        }
        with patch.dict(os.environ, demo_environment, clear=False):
            student_app = AppTest.from_file(str(self.app_path)).run(timeout=20)
            next(
                button
                for button in student_app.button
                if button.label == "体验学生端（张同学）"
            ).click().run(timeout=20)
            student_labels = [button.label for button in student_app.button]
            self.assertIn("学生报告", student_labels)
            self.assertIn("学习档案", student_labels)
            self.assertNotIn("实验提交", student_labels)
            self.assertNotIn("AI答辩", student_labels)
            self.assertEqual(len(student_app.exception), 0)

            teacher_app = AppTest.from_file(str(self.app_path)).run(timeout=20)
            next(
                button
                for button in teacher_app.button
                if button.label == "体验教师端"
            ).click().run(timeout=20)
            teacher_labels = [button.label for button in teacher_app.button]
            self.assertTrue(
                any(label.startswith("教师工作台") for label in teacher_labels)
            )
            self.assertNotIn("实验管理", teacher_labels)
            self.assertEqual(len(teacher_app.exception), 0)

    def test_public_demo_disables_teacher_review_writes(self):
        demo_environment = {
            "WUMA_DEMO_MODE": "true",
            "WUMA_DEMO_READ_ONLY": "true",
        }
        with patch.dict(os.environ, demo_environment, clear=False):
            app = AppTest.from_file(str(self.app_path)).run(timeout=20)
            next(
                button for button in app.button if button.label == "体验教师端"
            ).click().run(timeout=20)
            next(
                button
                for button in app.button
                if button.label.startswith("教师工作台")
            ).click().run(timeout=20)
            review_button = next(
                button for button in app.button if button.label == "保存教师复核"
            )
            self.assertTrue(review_button.disabled)
            self.assertEqual(len(app.exception), 0)

    def test_unmarked_database_is_rejected_before_app_can_display_or_modify_it(self):
        db.DB_PATH = Path(self.temp_folder.name) / "existing_business.db"
        db.init_db()
        db.create_submission_with_questions(
            "private-student", "PRIVATE_STUDENT_MARKER", "已有实验", "已有说明",
            "int main(){}", "2026-10-07 10:00",
            [{"dimension": "逻辑", "question": "已有问题", "reference_points": ["已有证据"]}],
        )
        before = hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest()
        with patch.dict(os.environ, {"WUMA_DEMO_MODE": "true"}):
            app = AppTest.from_file(str(self.app_path)).run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("Demo启动已停止" in item.value for item in app.error))
        self.assertFalse(any("体验教师端" == button.label for button in app.button))
        self.assertNotIn("PRIVATE_STUDENT_MARKER", str([item.value for item in app.markdown]))
        self.assertEqual(before, hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest())

    def test_demo_rejects_default_business_path_even_when_it_does_not_exist(self):
        path = Path(self.temp_folder.name) / "protected.db"
        with patch.object(runtime_config, "DEFAULT_BUSINESS_DB", path):
            with self.assertRaises(demo_mode.DemoDatabaseError):
                demo_mode.prepare_demo_database(path)
        self.assertFalse(path.exists())

    def test_demo_cannot_enable_writes_model_calls_or_code_execution_by_environment(self):
        values = {"WUMA_DEMO_MODE": "true", "WUMA_DEMO_READ_ONLY": "false", "CODE_EXECUTION_ENABLED": "true", "LLM_API_KEY": "test-placeholder"}
        with patch.dict(os.environ, values), patch.object(provider, "OpenAI") as client, patch.object(verifier, "get_execution_backend") as backend:
            self.assertTrue(demo_mode.demo_read_only())
            with self.assertRaises(provider.LLMProviderError):
                provider.chat_text([{"role": "user", "content": "测试"}])
            self.assertFalse(verifier.verify_cpp_code("int main(){}", [])['enabled'])
            self.assertFalse(verifier.run_cpp_code("int main(){}")['enabled'])
            self.assertFalse(verifier.runtime_status()['available'])
            client.assert_not_called()
            backend.assert_not_called()

    def test_demo_teacher_does_not_show_model_fault_or_test_button(self):
        with patch.dict(os.environ, {"WUMA_DEMO_MODE": "true", "LLM_API_KEY": "test-placeholder"}), patch.object(llm, "test_connection") as model_test:
            app = AppTest.from_file(str(self.app_path)).run(timeout=20)
            next(button for button in app.button if button.label == "体验教师端").click().run(timeout=20)
        self.assertNotIn("测试智能服务", [button.label for button in app.button])
        self.assertFalse(any("密钥未配置" in item.value for item in app.error))
        self.assertTrue(any("无需模型密钥" in item.value for item in app.info))
        model_test.assert_not_called()

    def test_demo_defaults_to_its_own_database_and_relative_paths_ignore_cwd(self):
        with patch.dict(os.environ, {"WUMA_DEMO_MODE": "true"}, clear=True):
            self.assertEqual(runtime_config.database_path(), runtime_config.DEFAULT_DEMO_DB.resolve())
        with patch.dict(os.environ, {"WUMA_AI_DB_PATH": "data/example.db"}, clear=True):
            self.assertEqual(runtime_config.database_path(), (runtime_config.PROJECT_ROOT / "data/example.db").resolve())


if __name__ == "__main__":
    unittest.main()
