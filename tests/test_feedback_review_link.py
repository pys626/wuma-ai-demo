import copy
import json
import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest
from auth_helpers import authenticate
import database as db
import teacher_insights as insights

ROOT = Path(__file__).resolve().parents[1]
TIME = "2026-10-08 20:51"
QUESTIONS = [{"dimension": "程序逻辑理解", "question": "为什么不可达节点不参与松弛？",
              "reference_points": ["不可达节点不入队"]}]
REPORT = {"overall": 100, "level": "理解充分", "weakest": "无", "summary": "已掌握。",
          "suggestion": "继续练习。", "review_required": False, "review_reasons": [],
          "dimensions": {"程序逻辑理解": 100, "关键概念掌握": 100,
                         "边界情况意识": 100, "分析与修改能力": 100}}


class FeedbackReviewLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_path = db.DB_PATH
        db.DB_PATH = Path(self.temp.name) / "test.db"
        db.init_db()
        self.sid = self.make_submission("20269901")

    def tearDown(self):
        db.DB_PATH = self.original_path
        self.temp.cleanup()

    def make_submission(self, student_id):
        sid = db.create_submission_with_questions(student_id, "联动测试学生", "Dijkstra测试",
            "不可达节点不会入队。", "int main(){return 0;}", TIME, QUESTIONS)
        evaluation = {"confidence": "高", "point_assessments": [
            {"reference_point": "不可达节点不入队", "status": "掌握",
             "answer_quote": "不可达节点不入队", "reason": "说明正确"}]}
        with db.get_connection() as c:
            c.execute("UPDATE qa_records SET answer=?, initial_evaluation_json=?, final_evaluation_json=? WHERE submission_id=?",
                      ("不可达节点不入队", json.dumps(evaluation), json.dumps(evaluation), sid))
        db.save_report(sid, copy.deepcopy(REPORT), TIME)
        return sid

    def record(self, sid=None):
        record = db.list_submissions(submission_id=sid or self.sid)[0]
        self.assertEqual(record["pending_ai_dispute_count"],
                         db.get_report(record["id"])["pending_ai_dispute_count"])
        return record

    def review_tasks(self, sid=None):
        return [t for t in db.list_teacher_tasks() if t["submission_id"] == (sid or self.sid)
                and t["action"] == "review_submission"]

    def dispute(self, text="AI追问假设不符合实际代码。", reply=True, time=TIME):
        return db.save_student_feedback(self.sid, "对AI结果有疑问", text, reply, time)

    def review(self, time=TIME):
        return db.save_teacher_review(self.sid, "任课教师", "认可AI诊断", 100, "已核查原文与代码。", time)

    def teacher(self):
        app = AppTest.from_file(str(ROOT / "app.py"))
        authenticate(app, "教师")
        app.run(timeout=20)
        next(b for b in app.button if b.label.startswith("教师工作台")).click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        return app

    def test_full_score_dispute_creates_separate_review_and_feedback_tasks(self):
        self.assertEqual(self.review_tasks(), [])
        for requested in (False, True):
            with self.subTest(reply_requested=requested):
                fid = self.dispute("疑问" + str(requested), reply=requested)
                review = self.review_tasks()
                self.assertEqual(len(review), 1)
                self.assertEqual(review[0]["title"], "复核学生对AI结果的疑问")
                self.assertIn("学生对AI结果提出疑问", review[0]["description"])
                self.assertEqual(review[0]["priority"], "紧急")
                self.assertTrue(any(t["feedback_id"] == fid for t in db.list_teacher_tasks()))
        self.assertEqual(self.record()["pending_ai_dispute_count"], 2)

    def test_reply_read_and_student_view_do_not_finish_review_or_change_ai_report(self):
        before = db.get_report(self.sid)
        fid = self.dispute()
        for status in ("已阅", "已回复"):
            db.process_student_feedback(fid, "任课教师", status, "你的疑问成立。", TIME)
            self.assertEqual(len(self.review_tasks()), 1)
            self.assertEqual(self.record()["pending_feedback_count"], 0)
            self.assertEqual(self.record()["pending_ai_dispute_count"], 1)
        db.mark_student_feedback_viewed(fid, "20269901", TIME)
        self.assertEqual(len(self.review_tasks()), 1)
        self.assertIsNone(db.get_teacher_review(self.sid))
        for field in REPORT:
            self.assertEqual(db.get_report(self.sid)[field], before[field])

    def test_review_finishes_dispute_task_but_preserves_unanswered_feedback(self):
        fid = self.dispute()
        rid = self.review()
        self.assertEqual(self.record()["pending_ai_dispute_count"], 0)
        self.assertEqual(self.review_tasks(), [])
        self.assertEqual(db.get_teacher_review(self.sid)["id"], rid)
        self.assertTrue(any(t["feedback_id"] == fid for t in db.list_teacher_tasks()))
        db.process_student_feedback(fid, "任课教师", "已回复", "已完成核查。", TIME)
        self.assertEqual(db.list_teacher_tasks(), [])
        self.assertEqual(len(db.list_teacher_review_history(self.sid)), 1)

    def test_new_dispute_reopens_after_review_even_in_same_minute(self):
        first = self.dispute()
        self.review()
        db.process_student_feedback(first, "任课教师", "已回复", "已核查。", TIME)
        second = self.dispute("还有一处新的疑问。")
        record = self.record()
        self.assertTrue(record["teacher_reviewed"])
        self.assertEqual(record["pending_ai_dispute_count"], 1)
        state = insights.teacher_review_state(record, db.get_report(self.sid), db.get_qa_records(self.sid))
        self.assertEqual(state["状态"], "待复核")
        self.assertEqual(len(self.review_tasks()), 1)
        self.assertEqual(db.list_learning_records()[0]["pending_ai_dispute_count"], 1)
        self.assertEqual(db.get_report(self.sid)["pending_ai_dispute_count"], 1)
        self.review()
        self.assertEqual(self.review_tasks(), [])
        self.assertEqual(len(db.list_teacher_review_history(self.sid)), 2)
        self.assertEqual(db.get_teacher_review(self.sid)["feedback_cutoff_id"], second)

    def test_other_feedback_categories_do_not_force_review(self):
        for category in ("补充答辩说明", "希望获得学习指导", "其他反馈"):
            db.save_student_feedback(self.sid, category, "请老师指导。", True, TIME)
        self.assertEqual(self.review_tasks(), [])
        self.assertEqual(self.record()["pending_ai_dispute_count"], 0)
        self.assertEqual(len(db.list_teacher_tasks()), 3)

    def test_existing_replied_dispute_is_backfilled_without_changing_feedback(self):
        fid = self.dispute()
        db.process_student_feedback(fid, "任课教师", "已回复", "你的说明正确。", TIME)
        before = db.list_student_feedbacks(submission_id=self.sid)
        for _ in range(2):
            db.init_db()
            self.assertEqual(len(self.review_tasks()), 1)
            self.assertEqual(db.list_student_feedbacks(submission_id=self.sid), before)

    def test_legacy_schema_upgrade_preserves_review_and_uses_conservative_time_order(self):
        self.dispute(time="2026-10-08 20:50")
        self.review(time=TIME)
        with db.get_connection() as c:
            c.execute("ALTER TABLE teacher_reviews DROP COLUMN feedback_cutoff_id")
        db.init_db()
        self.assertIsNone(db.get_teacher_review(self.sid)["feedback_cutoff_id"])
        self.assertEqual(self.review_tasks(), [])
        self.dispute("旧版同一分钟的先后不确定。", time=TIME)
        self.assertEqual(len(self.review_tasks()), 1)
        self.review()
        self.assertEqual(self.review_tasks(), [])
        db.init_db()
        self.assertEqual(len(db.list_teacher_review_history(self.sid)), 2)

    def test_legacy_disputes_compare_individual_times_even_with_reordered_ids(self):
        self.review(time=TIME)
        with db.get_connection() as c:
            c.execute("UPDATE teacher_reviews SET feedback_cutoff_id=NULL WHERE submission_id=?", (self.sid,))
        self.dispute("复核之后的疑问。", time="2026-10-08 20:52")
        self.dispute("较早的历史疑问。", time="2026-10-08 20:50")
        self.assertEqual(self.record()["pending_ai_dispute_count"], 1)
        self.assertEqual(len(self.review_tasks()), 1)

    def test_dispute_is_scoped_to_its_submission(self):
        other = self.make_submission("20269902")
        self.dispute()
        self.assertEqual(self.record(other)["pending_ai_dispute_count"], 0)
        self.assertEqual(self.review_tasks(other), [])
        self.assertEqual(len(self.review_tasks()), 1)

    def test_ui_reply_keeps_review_and_jumps_to_corresponding_review_form(self):
        fid = self.dispute()
        app = self.teacher()
        self.assertEqual(next(m for m in app.metric if m.label == "待教师复核").value, "1")
        next(b for b in app.button if b.key == f"task_action_教师待办_teacher-feedback-{fid}").click().run(timeout=20)
        self.assertTrue(any("复核待办会保留" in i.value for i in app.info))
        next(t for t in app.text_area if t.label.startswith("教师回复")).set_value("已收到，接下来核对答辩与代码。")
        next(b for b in app.button if b.label == "保存反馈处理结果").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(self.review_tasks()), 1)
        next(b for b in app.button if b.label == "前往对应教师复核").click().run(timeout=20)
        self.assertEqual(app.session_state["teacher_active_task"]["submission_id"], self.sid)
        self.assertEqual(app.session_state["teacher_active_task"]["action"], "review_submission")
        self.assertTrue(any(e.label == "查看学生对AI结果的疑问" and e.proto.expanded for e in app.expander))
        self.assertTrue(any("AI追问假设不符合实际代码" in t.value for t in app.markdown))
        next(b for b in app.button if b.label == "保存教师复核").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(db.list_teacher_tasks(), [])
        next(b for b in app.button if b.label == "返回待办").click().run(timeout=20)
        self.assertEqual(next(m for m in app.metric if m.label == "待教师复核").value, "0")

    def test_student_report_distinguishes_new_dispute_from_previous_review(self):
        self.review()
        fid = self.dispute()
        db.process_student_feedback(fid, "任课教师", "已回复", "还需复核。", TIME)
        app = AppTest.from_file(str(ROOT / "app.py"))
        authenticate(app, "学生", student_id="20269901", name="联动测试学生")
        app.run(timeout=20)
        next(b for b in app.button if b.label == "学生报告").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("疑问尚待教师复核" in w.value for w in app.warning))
        self.assertEqual(next(m for m in app.metric if m.label == "复核状态").value, "待复核（新疑问）")
        self.assertFalse(any("未发现必须人工复核" in v.value for v in app.success))
        self.review()
        app.run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(next(m for m in app.metric if m.label == "复核状态").value, "已复核")
        self.assertFalse(any("疑问尚待教师复核" in w.value for w in app.warning))

    def test_ui_filter_retains_new_dispute_after_existing_review(self):
        self.review()
        fid = self.dispute()
        db.process_student_feedback(fid, "任课教师", "已回复", "需进一步核查。", TIME)
        app = self.teacher()
        next(s for s in app.selectbox if s.label == "筛选学生状态").set_value("待教师复核").run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("联动测试学生" in s.value for s in app.selectbox if s.label == "选择要处理的提交"))
        self.assertTrue(any("待复核" in m.value for m in app.markdown))
        self.assertEqual(next(m for m in app.metric if m.label == "待教师复核").value, "1")
        self.assertTrue(any("保存本次复核结论" in w.value for w in app.warning))


if __name__ == "__main__":
    unittest.main()
