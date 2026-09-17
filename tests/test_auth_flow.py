import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import database as db


QUESTIONS = [
    {
        "dimension": "程序逻辑理解",
        "question": "请解释主要循环。",
        "reference_points": ["循环边界"],
    }
]

REPORT = {
    "overall": 80,
    "level": "理解较好",
    "weakest": "边界情况意识",
    "summary": "能够说明主要逻辑。",
    "suggestion": "继续检查边界情况。",
    "dimensions": {
        "程序逻辑理解": 85,
        "关键概念掌握": 80,
        "边界情况意识": 70,
        "分析与修改能力": 85,
    },
    "review_required": False,
    "review_reasons": [],
}


class AuthenticationFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_folder = tempfile.TemporaryDirectory()
        self.original_path = db.DB_PATH
        db.DB_PATH = Path(self.temp_folder.name) / "wuma_ai.db"
        db.init_db()
        self.app_path = Path(__file__).resolve().parents[1] / "app.py"

    def tearDown(self):
        db.DB_PATH = self.original_path
        self.temp_folder.cleanup()

    def _preauthenticated_student(self, student_id="20260001", page="首页"):
        app = AppTest.from_file(str(self.app_path))
        app.session_state["auth_role"] = "学生"
        app.session_state["auth_name"] = "张同学"
        app.session_state["auth_student_id"] = student_id
        app.session_state["form_name"] = "张同学"
        app.session_state["form_student_id"] = student_id
        app.session_state["current_page"] = page
        return app.run(timeout=20)

    def _save_completed_report(self, student_id, name):
        submission_id = db.create_submission_with_questions(
            student_id,
            name,
            "求数组中的最大值",
            "遍历数组。",
            "int main(){return 0;}",
            "2026-09-14 08:00",
            QUESTIONS,
        )
        db.save_report(submission_id, REPORT, "2026-09-14 08:05")
        return submission_id

    def test_student_login_locks_identity_and_hides_teacher_pages(self):
        app = AppTest.from_file(str(self.app_path)).run(timeout=20)
        self.assertTrue(any(item.value == "登录悟码AI" for item in app.title))
        self.assertFalse(any(button.label == "教师工作台" for button in app.button))

        next(item for item in app.text_input if item.label == "姓名").set_value("李同学")
        next(item for item in app.text_input if item.label == "学号").set_value("20261234")
        next(
            button for button in app.button if button.label == "进入学生端"
        ).click().run(timeout=20)

        labels = [button.label for button in app.button]
        self.assertIn("实验提交", labels)
        self.assertIn("学习档案", labels)
        self.assertNotIn("实验管理", labels)
        self.assertNotIn("教师工作台", labels)
        next(
            button for button in app.button if button.label == "实验提交"
        ).click().run(timeout=20)
        name = next(item for item in app.text_input if item.label == "姓名")
        student_id = next(item for item in app.text_input if item.label == "学号")
        self.assertEqual(name.value, "李同学")
        self.assertEqual(student_id.value, "20261234")
        self.assertTrue(name.disabled)
        self.assertTrue(student_id.disabled)
        self.assertEqual(len(app.exception), 0)

        next(
            button for button in app.button if button.label == "退出登录"
        ).click().run(timeout=20)
        self.assertTrue(any(item.value == "登录悟码AI" for item in app.title))
        self.assertFalse(any(button.label == "实验提交" for button in app.button))
        self.assertFalse(any(button.label == "教师工作台" for button in app.button))
        self.assertEqual(len(app.exception), 0)

    def test_teacher_password_rejects_wrong_value_and_opens_only_teacher_pages(self):
        with patch.dict(os.environ, {"TEACHER_PASSWORD": "classroom-secret"}):
            app = AppTest.from_file(str(self.app_path)).run(timeout=20)
            next(
                item for item in app.radio if item.label == "选择身份"
            ).set_value("教师").run(timeout=20)
            password = next(
                item for item in app.text_input if item.label == "教师密码"
            )
            password.set_value("wrong")
            next(
                button for button in app.button if button.label == "进入教师端"
            ).click().run(timeout=20)
            self.assertTrue(any("密码错误" in item.value for item in app.error))

            next(
                item for item in app.text_input if item.label == "教师密码"
            ).set_value("classroom-secret")
            next(
                button for button in app.button if button.label == "进入教师端"
            ).click().run(timeout=20)

        labels = [button.label for button in app.button]
        self.assertIn("实验管理", labels)
        self.assertIn("教师工作台", labels)
        self.assertNotIn("实验提交", labels)
        self.assertNotIn("学生报告", labels)
        self.assertNotIn("teacher_login_password", app.session_state)
        self.assertEqual(len(app.exception), 0)

    def test_missing_teacher_password_keeps_teacher_area_locked(self):
        with patch.dict(os.environ, {"TEACHER_PASSWORD": ""}):
            app = AppTest.from_file(str(self.app_path)).run(timeout=20)
            next(
                item for item in app.radio if item.label == "选择身份"
            ).set_value("教师").run(timeout=20)
        self.assertTrue(
            any("尚未配置教师密码" in item.value for item in app.warning)
        )
        self.assertFalse(any(button.label == "教师工作台" for button in app.button))
        self.assertEqual(len(app.exception), 0)

    def test_student_route_and_report_queries_are_limited_to_current_identity(self):
        self._save_completed_report("20260001", "张同学")
        self._save_completed_report("20260002", "王同学")

        app = self._preauthenticated_student(page="教师工作台")
        self.assertTrue(any(item.label == "首页" for item in app.button))
        self.assertFalse(any(item.label == "教师工作台" for item in app.button))
        next(
            button for button in app.button if button.label == "学生报告"
        ).click().run(timeout=20)

        report_selector = next(
            item for item in app.selectbox if item.label == "选择一份历史报告"
        )
        options = [str(item) for item in report_selector.options]
        self.assertTrue(all("20260001" in item for item in options))
        self.assertFalse(any("20260002" in item for item in options))
        self.assertEqual(len(app.exception), 0)


if __name__ == "__main__":
    unittest.main()
