import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import database as db
import demo_mode


class DemoModeTests(unittest.TestCase):
    def setUp(self):
        self.temp_folder = tempfile.TemporaryDirectory()
        self.original_path = db.DB_PATH
        db.DB_PATH = Path(self.temp_folder.name) / "wuma_ai_demo.db"
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


if __name__ == "__main__":
    unittest.main()
