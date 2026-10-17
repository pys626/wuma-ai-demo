"""Check browser updates as well as server values when replacing a form draft."""
import copy
import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest
import access_control
import accounts
import assignment_drafts
import database as db
import experiment_packages
from auth_helpers import authenticate, load_teacher_draft

ROOT = Path(__file__).resolve().parents[1]


class ImportedDraftUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_database = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "isolated.db"
        db.init_db()
        self.values = experiment_packages.parse(
            (ROOT / "test_materials/dijkstra/experiment_config.json").read_bytes())

    def tearDown(self):
        db.DB_PATH = self.old_database
        self.tmp.cleanup()

    def editor(self):
        app = AppTest.from_file(str(ROOT / "app.py"))
        authenticate(app, "教师")
        app.session_state["current_page"] = "实验管理"
        app.run(timeout=20)
        self.assertFalse(app.exception)
        return app

    def import_values(self, app, values):
        app.text_area(key="experiment_package_json").set_value(
            experiment_packages.export(values)).run(timeout=20)
        next(b for b in app.button if b.label == "检查实验包").click().run(timeout=20)
        next(b for b in app.button if b.label == "载入未发布草稿").click().run(timeout=20)
        self.assertFalse(app.exception)

    def assert_browser_fields(self, app, values):
        fields = {
            "create_assignment_title": values["title"],
            "create_assignment_description": values["description"],
            "create_assignment_input_format": values["input_format"],
            "create_assignment_output_format": values["output_format"],
            "create_assignment_constraints": values["constraints_text"],
            "create_assignment_requirements": "\n".join(values["requirements"]),
            "create_assignment_teaching_focus": "\n".join(values["teaching_focus"]),
            "create_assignment_reference_code": values["reference_code"],
            "create_assignment_starter_code": values["starter_code"],
        }
        for index in range(5):
            sample = values["public_samples"][index] if index < len(values["public_samples"]) else {}
            for field in ["name", "input", "output", "explanation"]:
                fields[f"create_assignment_public_samples_{index}_{field}"] = sample.get(field, "")
        for index in range(8):
            case = values["test_cases"][index] if index < len(values["test_cases"]) else {}
            for widget_field, field in [("name", "name"), ("group", "group"),
                                        ("input", "input"), ("output", "expected_output")]:
                fields[f"create_assignment_test_cases_{index}_{widget_field}"] = case.get(field, "")
        widgets = {item.key: item for item in [*app.text_input, *app.text_area]}
        for key, expected in fields.items():
            widget = widgets[key]
            self.assertEqual(widget.value, expected, key)
            # AppTest alone reads server state, while an existing browser needs set_value.
            self.assertTrue(widget.proto.set_value, key + ": must replace the browser's old form value")
            self.assertEqual(widget.proto.value, expected, key)
        verification = app.checkbox(key="create_assignment_verification")
        self.assertTrue(verification.proto.set_value)
        self.assertEqual(verification.proto.value, values["verification_enabled"])
        standard = app.selectbox(key="create_cpp_standard")
        self.assertTrue(standard.proto.set_value)
        self.assertEqual(standard.value, "C++14")
        self.assertTrue(app.number_input(key="create_assignment_hint_limit").proto.set_value)
        self.assertFalse(any("Session State API" in item.value for item in app.warning))

    def test_import_replaces_existing_blank_browser_fields_and_keeps_assignment_unpublished(self):
        app = self.editor()
        before = copy.deepcopy(db.list_assignments())
        self.import_values(app, self.values)
        self.assert_browser_fields(app, self.values)
        self.assertEqual(load_teacher_draft()["data"], self.values)
        self.assertEqual(db.list_assignments(), before)

    def test_saved_draft_restores_when_upgrading_an_already_open_session(self):
        app = self.editor()
        with db.get_connection() as connection:
            teacher_id = connection.execute("SELECT teacher_id FROM classes ORDER BY id LIMIT 1").fetchone()[0]
            class_id = connection.execute("SELECT id FROM classes ORDER BY id LIMIT 1").fetchone()[0]
        with access_control.scope(accounts.get_account(teacher_id), class_id):
            document = assignment_drafts.save_draft(db.DB_PATH, self.values)
            path = assignment_drafts.draft_path(db.DB_PATH)
            before = path.read_bytes()
        app.session_state["assignment_create_loaded_for"] = str(db.DB_PATH.resolve())
        app.run(timeout=20)
        self.assertFalse(app.exception)
        self.assert_browser_fields(app, self.values)
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(any(self.values["title"] in item.value for item in app.info))
        self.assertTrue(any("下方表单" in item.value for item in app.info))

    def test_normal_edit_save_and_fresh_session_keep_teacher_changes(self):
        app = self.editor()
        self.import_values(app, self.values)
        app.text_input(key="create_assignment_title").set_value("教师修改的最短路径题")
        app.text_area(key="create_assignment_description").set_value("教师补充说明\n保留换行")
        next(b for b in app.button if b.label == "保存草稿").click().run(timeout=20)
        self.assertFalse(app.exception)
        self.assertEqual(app.text_input(key="create_assignment_title").value, "教师修改的最短路径题")
        restored = self.editor()
        self.assertEqual(restored.text_input(key="create_assignment_title").value, "教师修改的最短路径题")
        self.assertEqual(restored.text_area(key="create_assignment_description").value, "教师补充说明\n保留换行")
        self.assertEqual(restored.text_area(key="create_assignment_test_cases_7_input").value,
                         self.values["test_cases"][7]["input"])

    def test_reimport_clears_cards_that_are_not_in_the_new_experiment(self):
        app = self.editor()
        self.import_values(app, self.values)
        smaller = copy.deepcopy(self.values)
        smaller["title"] = "只保留一组样例的图算法实验"
        smaller["public_samples"] = smaller["public_samples"][:1]
        smaller["test_cases"] = smaller["test_cases"][:1]
        smaller["verification_enabled"] = False
        smaller["explanation_required"] = False
        smaller["hint_limit"] = 0
        self.import_values(app, smaller)
        self.assert_browser_fields(app, smaller)
        self.assertEqual(app.selectbox(key="create_assignment_explanation_required").value, "选做")
        self.assertEqual(app.number_input(key="create_assignment_hint_limit").value, 0)
        self.assertEqual(load_teacher_draft()["data"], smaller)
        backups = list(db.DB_PATH.with_suffix(".assignment_draft_backups").glob("*.json"))
        self.assertEqual(len(backups), 1)
