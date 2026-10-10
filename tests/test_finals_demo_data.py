import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
import database as db
import demo_mode
import finals_demo_data

class FinalsDemoTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.old = db.DB_PATH
        db.DB_PATH = Path(self.folder.name) / "finals.db"
        demo_mode.prepare_demo_database(db.DB_PATH)
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.old
        self.folder.cleanup()

    def test_saved_cases_are_idempotent_and_preserve_follow_up_and_redefense(self):
        self.assertTrue(finals_demo_data.seed_finals_cases())
        records = db.list_submissions()
        self.assertEqual(len(records), 3)
        a = next(row for row in records if row["student_id"] == "20260001")
        b = next(row for row in records if row["student_id"] == "20260002" and not row["parent_submission_id"])
        self.assertEqual(db.get_report(a["id"])["overall"], 100)
        self.assertEqual(db.get_report(b["id"])["overall"], 50)
        second = db.get_qa_records(a["id"])[1]
        self.assertTrue(second["follow_up_answer"])
        self.assertEqual(second["initial_evaluation"]["point_assessments"][2]["status"], "未体现")
        self.assertEqual(second["final_evaluation"]["point_assessments"][2]["status"], "掌握")
        self.assertEqual(db.get_latest_redefense_request(b["id"])["status"], "答辩中")
        before = hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest()
        self.assertFalse(finals_demo_data.seed_finals_cases())
        self.assertEqual(hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest(), before)

    def test_existing_records_are_not_overwritten(self):
        db.create_submission_with_questions("test", "合成学生", "原任务", "", "", "2026-10-10", [])
        before = hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "现有记录不被覆盖"):
            finals_demo_data.seed_finals_cases()
        self.assertEqual(hashlib.sha256(db.DB_PATH.read_bytes()).hexdigest(), before)

    def test_public_entry_has_password_free_roles_and_read_only_teacher_report(self):
        root = Path(__file__).resolve().parents[1]
        with patch.dict("os.environ", {"WUMA_AI_DB_PATH": str(db.DB_PATH)}):
            page = AppTest.from_file(str(root / "competition_demo.py")).run(timeout=20)
            self.assertEqual(len(page.exception), 0)
            self.assertTrue(any("已保存的真实模型评价" in item.value for item in page.info))
            self.assertTrue(any(button.label == "体验学生端（案例学生甲）" for button in page.button))
            next(button for button in page.button if button.label == "体验教师端").click().run(timeout=20)
            self.assertEqual(len(page.exception), 0)
            next(button for button in page.button if button.label.startswith("教师工作台")).click().run(timeout=20)
            self.assertEqual(len(page.exception), 0)
            self.assertFalse(any(button.label == "保存复核结论" and not button.disabled for button in page.button))
            self.assertEqual(len(db.list_submissions()), 3)
