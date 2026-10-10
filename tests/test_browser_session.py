import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from auth_helpers import authenticate, PASSWORD
import accounts
import browser_session
import database as db
import defense_drafts

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = [{"dimension": "程序逻辑理解", "question": f"请解释第{i}个步骤。",
              "reference_points": ["解释执行路径"], "reference_dimensions": ["程序逻辑理解"]} for i in range(1,4)]


class BrowserSessionContractTests(unittest.TestCase):
    def test_database_namespaces_do_not_share_credentials(self):
        self.assertNotEqual(browser_session.storage_key(ROOT/'data/a.db'), browser_session.storage_key(ROOT/'data/b.db'))
        self.assertEqual(browser_session.storage_key(ROOT/'data/a.db'), browser_session.storage_key(ROOT/'data/../data/a.db'))

    def test_navigation_hints_reject_wrong_types_and_never_include_identity_or_password(self):
        view = browser_session.sanitize_view({"page": "学生报告", "class_id": True, "submission_id": "2",
            "assignment_id": -1, "teacher_workspace": "run code", "auth_role": "教师", "password": "secret"})
        self.assertIsNone(view["class_id"])
        self.assertIsNone(view["submission_id"])
        self.assertIsNone(view["assignment_id"])
        self.assertEqual(view["teacher_workspace"], "待办处理")
        self.assertNotIn("auth_role", view)
        self.assertNotIn("password", view)

    def test_invalid_browser_view_is_empty(self):
        self.assertEqual(browser_session.sanitize_view("forged"), {})
        self.assertEqual(browser_session.sanitize_view({"page":"x"*100})["page"], "首页")


    def test_malformed_workspace_hint_does_not_break_login(self):
        for workspace in ([], {}, 3, None):
            self.assertEqual(browser_session.sanitize_view({"teacher_workspace":workspace})["teacher_workspace"],"待办处理")

    def exchange_receipt(self, receipt, operation="read", state=None):
        state = state if state is not None else {"_browser_request":"request-1"}
        with patch.object(browser_session.st,"session_state",state), patch.object(browser_session,"_bridge",return_value=receipt):
            return browser_session.exchange(ROOT/'data/a.db',operation)

    def test_stale_component_receipts_cannot_restore_credentials(self):
        for request,operation in (("request-previous","read"),("request-1","write")):
            receipt={"request":request,"operation":operation,"status":"ready","token":"A"*43}
            self.assertIsNone(self.exchange_receipt(receipt))

    def test_read_receipt_requires_valid_token_shape(self):
        receipt={"request":"request-1","operation":"read","status":"ready","token":"A"*43,"view":{"auth_role":"教师"}}
        self.assertEqual(self.exchange_receipt(receipt)["token"],"A"*43)
        receipt["token"]="forged token"
        self.assertEqual(self.exchange_receipt(receipt)["token"],"invalid")
        receipt["token"]=""
        self.assertIsNone(self.exchange_receipt(receipt))

    def test_clear_pending_waits_for_matching_successful_browser_acknowledgment(self):
        state={"_browser_request":"request-1","_browser_clear_pending":True}
        self.exchange_receipt({"request":"old","operation":"clear","status":"ready"},"clear",state)
        self.assertIn("_browser_clear_pending",state)
        self.exchange_receipt({"request":"request-1","operation":"clear","status":"ready"},"clear",state)
        self.assertNotIn("_browser_clear_pending",state)

    def test_blocked_storage_preserves_logout_and_reports_refresh_requirement(self):
        state={"_browser_request":"request-1","_browser_clear_pending":True}
        with patch.object(browser_session.st,"caption") as caption:
            self.assertIsNone(self.exchange_receipt({"request":"request-1","operation":"clear","status":"blocked"},"clear",state))
        self.assertIn("_browser_clear_pending",state)
        caption.assert_called_once()


class BrowserSessionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = Path(self.temp.name)/"isolated.db"
        db.init_db()
        self.seed = AppTest.from_file(str(ROOT/'app.py'))
        authenticate(self.seed, "学生", "20269711", "刷新测试学生")
        self.token = self.seed.session_state["auth_token"]
        assignment = db.list_assignments()[0]
        self.assignment_id = assignment["id"]
        self.sid = db.create_submission_with_questions("20269711", "刷新测试学生", "刷新进度测试",
            "解释执行路径", "int main(){return 0;}", "2026-10-08 23:00", QUESTIONS, assignment=assignment)
        evaluation = {"score":90,"confidence":"高","feedback":"解释明确","should_follow_up":False,"follow_up_question":""}
        db.save_initial_result(self.sid, 0, "首次回答已经保存", "", evaluation, True)
        self.view = {"page":"任务中心", "class_id":1, "submission_id":None, "assignment_id":self.assignment_id}
        self.operations = []

    def tearDown(self):
        db.DB_PATH = self.old_path
        self.temp.cleanup()

    def exchange(self, database, operation, token="", view=None):
        self.operations.append(operation)
        if operation == "read":
            return {"token":self.token,"view":browser_session.sanitize_view(self.view)}
        if operation == "clear":
            browser_session.st.session_state.pop("_browser_clear_pending", None)
            browser_session.st.session_state["_browser_read_completed"] = True
        return None

    def fresh(self):
        app = AppTest.from_file(str(ROOT/'app.py'))
        with patch.object(browser_session,"exchange",side_effect=self.exchange):
            app.run(timeout=20)
        self.assertEqual(len(app.exception),0)
        return app

    def test_refresh_restores_student_and_page_without_a_new_login_or_expiry_extension(self):
        with db.get_connection() as c:
            before = [tuple(r) for r in c.execute("SELECT * FROM account_sessions ORDER BY token_hash")]
        app = self.fresh()
        self.assertEqual(app.session_state["auth_student_id"], "20269711")
        self.assertEqual(app.session_state["current_page"], "任务中心")
        self.assertIn("write", self.operations)
        self.assertFalse(any(x.label=="学生学号" for x in app.text_input))
        with db.get_connection() as c:
            self.assertEqual(before,[tuple(r) for r in c.execute("SELECT * FROM account_sessions ORDER BY token_hash")])

    def test_refresh_recovers_question_progress_and_saved_answer_draft(self):
        self.view.update(page="AI答辩",submission_id=self.sid)
        normalized = {key:QUESTIONS[1].get(key,[]) for key in ("question","dimension","reference_points","reference_dimensions")}
        defense_drafts.save(db.DB_PATH,"20269711",self.sid,1,"initial",normalized,"第二题未提交的草稿")
        app = self.fresh()
        self.assertEqual(app.session_state["current_question"],1)
        self.assertEqual(app.text_area[0].value,"第二题未提交的草稿")
        self.assertTrue(any(x.value=="问题2" for x in app.subheader))

    def test_restored_initial_password_still_requires_change(self):
        actor = accounts.session(self.token)
        with db.get_connection() as c:c.execute("UPDATE accounts SET must_change=1 WHERE id=?",(actor["id"],))
        app = self.fresh()
        self.assertTrue(any(x.value=="首次登录－修改初始密码" for x in app.title))
        self.assertFalse(any(x.label=="实验提交" for x in app.button))

    def test_revoked_token_cannot_restore_an_account(self):
        accounts.logout(self.token)
        app = self.fresh()
        self.assertNotIn("auth_token", app.session_state)
        self.assertIn("clear",self.operations)
        self.assertTrue(any(x.label=="学生学号" for x in app.text_input))

    def test_expired_token_cannot_restore_an_account(self):
        with db.get_connection() as c:c.execute("UPDATE account_sessions SET expires=0")
        app = self.fresh()
        self.assertNotIn("auth_token", app.session_state)
        self.assertTrue(any("会话已过期" in x.value for x in app.info))

    def test_disabled_account_cannot_restore_an_account(self):
        actor = accounts.session(self.token)
        with db.get_connection() as c:c.execute("UPDATE accounts SET active=0 WHERE id=?",(actor["id"],))
        app = self.fresh()
        self.assertNotIn("auth_token", app.session_state)
        self.assertIn("clear",self.operations)

    def test_password_change_invalidates_stored_session(self):
        accounts.change_password(self.token,PASSWORD,"Changed-password-2026")
        app = self.fresh()
        self.assertNotIn("auth_token", app.session_state)
        self.assertIn("clear",self.operations)

    def test_forged_role_and_foreign_class_cannot_change_canonical_identity(self):
        self.view.update(page="教师工作台",class_id=999999,auth_role="教师")
        app = self.fresh()
        self.assertEqual(app.session_state["auth_role"],"学生")
        self.assertEqual(app.session_state["selected_class_id"],1)
        self.assertEqual(app.session_state["current_page"],"首页")
        self.assertFalse(any(x.label=="系统设置" for x in app.button))

    def test_forged_submission_hint_cannot_open_another_student_record(self):
        other = db.create_submission_with_questions("20269712","其他学生","私人提交","私人思路","private code",
            "2026-10-08 23:01",QUESTIONS,assignment=db.get_assignment(self.assignment_id))
        self.view.update(page="AI答辩",submission_id=other)
        app = self.fresh()
        self.assertEqual(app.session_state["current_page"],"首页")
        self.assertIsNone(app.session_state["submission"])
        self.assertIsNone(app.session_state["submission_id"])

    def test_teacher_refresh_restores_teacher_workbench(self):
        teacher=AppTest.from_file(str(ROOT/'app.py'))
        authenticate(teacher,"教师")
        self.token=teacher.session_state["auth_token"]
        self.view.update(page="教师工作台",teacher_submission_id=self.sid,teacher_workspace="班级分析")
        app=self.fresh()
        self.assertEqual(app.session_state["auth_role"],"教师")
        self.assertEqual(app.session_state["current_page"],"教师工作台")
        self.assertEqual(app.session_state["teacher_selected_submission_id"],self.sid)

    def test_teacher_analysis_view_restores_before_selecting_a_student(self):
        teacher=AppTest.from_file(str(ROOT/'app.py'))
        authenticate(teacher,"教师")
        self.token=teacher.session_state["auth_token"]
        self.view.update(page="教师工作台",teacher_submission_id=None,teacher_workspace="班级分析")
        app=self.fresh()
        self.assertEqual(app.session_state["teacher_workspace_view"],"班级分析")
        self.assertEqual(next(r for r in app.radio if r.label=="工作区域").value,"班级分析")

    def test_logout_revokes_credential_and_requests_browser_clear(self):
        app=self.fresh()
        with patch.object(browser_session,"exchange",side_effect=self.exchange):
            next(b for b in app.button if b.label=="退出登录").click().run(timeout=20)
        self.assertEqual(len(app.exception),0)
        self.assertIsNone(accounts.session(self.token))
        self.assertIn("clear",self.operations)
        self.assertNotIn("auth_token", app.session_state)
