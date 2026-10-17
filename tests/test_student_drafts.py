import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import student_drafts as drafts


class StudentDraftTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.database = Path(self.folder.name) / "lesson.db"
        self.database.write_bytes(b"untouched")

    def tearDown(self):
        self.folder.cleanup()

    def test_multiline_input_is_preserved_without_database_changes(self):
        values = {"code": "int main() {\n  return 0;\n}", "explanation": "第一步\n两  个空格", "custom_input": "3\n1 2 3\n"}
        drafts.save_draft(self.database, "001", 1, values, "version1")
        self.assertEqual(drafts.load_draft(self.database, "001", 1)["data"], values)
        self.assertEqual(self.database.read_bytes(), b"untouched")

    def test_student_assignment_and_database_isolation(self):
        drafts.save_draft(self.database, "001", 1, {"code": "my code"})
        for database, student, assignment in [(self.database, "002", 1), (self.database, "001", 2), (self.database.with_name("other.db"), "001", 1)]:
            self.assertIsNone(drafts.load_draft(database, student, assignment))

    def test_auth_and_judging_evidence_are_not_persisted(self):
        drafts.save_draft(self.database, "001", 1, {"code": "code", "LLM_API_KEY": "test-secret", "auth_role": "教师", "formal_result": {"input": "hidden"}})
        self.assertEqual(drafts.load_draft(self.database, "001", 1)["data"], {"code": "code"})
        self.assertNotIn("test-secret", drafts.draft_path(self.database, "001", 1).read_text(encoding="utf-8"))

    def test_failed_save_preserves_previous_draft(self):
        drafts.save_draft(self.database, "001", 1, {"code": "old"})
        with patch.object(drafts.os, "replace", side_effect=OSError("failed")):
            with self.assertRaises(OSError):
                drafts.save_draft(self.database, "001", 1, {"code": "new"})
        self.assertEqual(drafts.load_draft(self.database, "001", 1)["data"]["code"], "old")

    def test_identity_mismatch_and_corrupt_json_are_not_loaded(self):
        saved = drafts.save_draft(self.database, "001", 1, {"code": "old"})
        saved["student_id"] = "other"
        path = drafts.draft_path(self.database, "001", 1)
        path.write_text(json.dumps(saved), encoding="utf-8")
        with self.assertRaises(drafts.DraftError):
            drafts.load_draft(self.database, "001", 1)
        path.write_text("broken", encoding="utf-8")
        with self.assertRaises(drafts.DraftError):
            drafts.load_draft(self.database, "001", 1)
        self.assertEqual(path.read_text(encoding="utf-8"), "broken")

    def test_clear_does_not_remove_other_student_or_database(self):
        for student in ["001", "002"]:
            drafts.save_draft(self.database, student, 1, {"code": student})
        drafts.clear_draft(self.database, "001", 1)
        self.assertIsNone(drafts.load_draft(self.database, "001", 1))
        self.assertEqual(drafts.load_draft(self.database, "002", 1)["data"]["code"], "002")
        self.assertEqual(self.database.read_bytes(), b"untouched")
