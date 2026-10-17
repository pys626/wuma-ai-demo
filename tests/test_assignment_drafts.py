import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import assignment_drafts as drafts


class AssignmentDraftTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.database = Path(self.folder.name) / "lesson.db"
        self.database.write_bytes(b"unchanged database")

    def tearDown(self):
        self.folder.cleanup()

    def test_complete_selection_experiment_round_trips_without_changing_database(self):
        config_path = Path(__file__).resolve().parents[1] / "test_materials/selection_sort/experiment_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        drafts.save_draft(self.database, config)
        self.assertEqual(drafts.load_draft(self.database)["data"], config)
        self.assertEqual(self.database.read_bytes(), b"unchanged database")

    def test_incomplete_and_invalid_inputs_are_saved_for_later_correction(self):
        values = {"title": "", "description": "尚未写完\n第二行  连续空格", "rubric": []}
        drafts.save_draft(self.database, values)
        self.assertEqual(drafts.load_draft(self.database)["data"], values)

    def test_only_assignment_fields_are_persisted(self):
        drafts.save_draft(self.database, {"title": "实验", "TEACHER_PASSWORD": "private", "auth_role": "教师"})
        self.assertEqual(drafts.load_draft(self.database)["data"], {"title": "实验"})
        self.assertNotIn("private", drafts.draft_path(self.database).read_text(encoding="utf-8"))

    def test_blank_table_weight_is_stored_as_null(self):
        drafts.save_draft(self.database, {"rubric": [{"name": "未完成评分点", "weight": float("nan")}]})
        self.assertIsNone(drafts.load_draft(self.database)["data"]["rubric"][0]["weight"])

    def test_databases_do_not_share_drafts(self):
        other = self.database.with_name("rehearsal.db")
        drafts.save_draft(self.database, {"title": "业务草稿"})
        self.assertIsNone(drafts.load_draft(other))
        drafts.save_draft(other, {"title": "演练草稿"})
        self.assertEqual(drafts.load_draft(self.database)["data"]["title"], "业务草稿")

    def test_failed_atomic_replace_preserves_the_previous_draft(self):
        drafts.save_draft(self.database, {"title": "之前保存的内容"})
        with patch.object(drafts.os, "replace", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                drafts.save_draft(self.database, {"title": "新内容"})
        self.assertEqual(drafts.load_draft(self.database)["data"]["title"], "之前保存的内容")
        self.assertFalse(list(Path(self.folder.name).glob("*.tmp")))

    def test_corrupt_draft_is_reported_and_preserved(self):
        path = drafts.draft_path(self.database)
        path.write_text("unfinished json", encoding="utf-8")
        with self.assertRaises(drafts.DraftError):
            drafts.load_draft(self.database)
        self.assertEqual(path.read_text(encoding="utf-8"), "unfinished json")

    def test_clear_removes_only_the_draft(self):
        drafts.save_draft(self.database, {"title": "已发布"})
        drafts.clear_draft(self.database)
        self.assertIsNone(drafts.load_draft(self.database))
        self.assertEqual(self.database.read_bytes(), b"unchanged database")


if __name__ == "__main__":
    unittest.main()
