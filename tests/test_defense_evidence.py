from auth_helpers import authenticate
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import database as db
import defense_evidence as evidence
import llm_service as llm
import teacher_insights as insights


FIXTURE = Path(__file__).resolve().parents[1] / "test_materials/selection_sort/04_AI评分对照案例.json"


def records_for(case_index=0):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    case = fixture["cases"][case_index]
    records = copy.deepcopy(fixture["questions"])
    for index, record in enumerate(records):
        record.update(answer=case["answers"][index], follow_up_question="", follow_up_answer="")
        record["final_evaluation"] = {
            "score": 93, "confidence": "高", "misconceptions": [], "missing_points": [],
            "point_assessments": llm._point_assessments([
                {"status": status, "answer_quote": quote, "evidence": quote}
                for status, quote in zip(case["statuses"][index], case["quotes"][index])
            ], record["reference_points"]),
        }
    return records


def report_for(records):
    return {
        **evidence.build_dimension_report(records), "summary": "检查实际作答依据。",
        "suggestion": "核查边界解释。", "review_required": True, "review_reasons": ["核查证据"],
    }


class DimensionEvidenceTests(unittest.TestCase):
    def test_three_teacher_authored_answer_categories(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        for index, case in enumerate(fixture["cases"]):
            with self.subTest(case=case["name"]):
                report = evidence.build_dimension_report(records_for(index))
                self.assertEqual(report["dimensions"], case["expected_dimensions"])
                self.assertEqual(report["overall"], case["expected_overall"])
                self.assertTrue(report["assessment_complete"])

    def test_same_question_has_independent_concept_and_boundary_scores(self):
        records = records_for()
        record = records[1]
        record["answer"] += "n=1时仍然会读取a[1]。"
        record["final_evaluation"]["point_assessments"][1].update(
            status="理解错误", answer_quote="n=1时仍然会读取a[1]。",
        )
        report = evidence.build_dimension_report(records)
        self.assertEqual(report["dimensions"]["关键概念掌握"], 100)
        self.assertEqual(report["dimensions"]["边界情况意识"], 0)
        self.assertEqual(report["overall"], 75)

    def test_question_order_and_model_numeric_variation_do_not_change_dimensions(self):
        records = records_for(1)
        expected = evidence.build_dimension_report(records)["dimensions"]
        for scores in ([0, 0, 0], [99, 100, 97], [45, 65, 89]):
            for record, score in zip(records, scores):
                record["final_evaluation"]["score"] = score
            self.assertEqual(evidence.build_dimension_report(list(reversed(records)))["dimensions"], expected)

    def test_missing_dimension_never_inherits_another_point_score(self):
        records = records_for()
        records[1]["reference_dimensions"][1] = "关键概念掌握"
        report = evidence.build_dimension_report(records)
        self.assertIsNone(report["dimensions"]["边界情况意识"])
        self.assertIsNone(report["overall"])
        self.assertEqual(report["assessed_dimensions"], 3)
        self.assertEqual(report["level"], "评价尚不完整")

    def test_unanswered_or_unmentioned_point_is_not_an_error_or_zero(self):
        for unanswered in (False, True):
            records = records_for()
            if unanswered:
                records[1]["answer"] = ""
            else:
                records[1]["final_evaluation"]["point_assessments"][1].update(status="未体现", answer_quote="")
            report = evidence.build_dimension_report(records)
            self.assertIsNone(report["dimensions"]["边界情况意识"])
            self.assertEqual(report["dimension_evidence"]["边界情况意识"]["status"], "证据不足")

    def test_invented_or_code_only_quotes_do_not_become_evidence(self):
        for quote in ("", "学生从未说过的正确答案", "int main(){return 0;}"):
            records = records_for()
            records[1]["final_evaluation"]["point_assessments"][1]["answer_quote"] = quote
            report = evidence.build_dimension_report(records)
            self.assertIsNone(report["overall"])
            self.assertEqual(report["dimension_evidence"]["边界情况意识"]["points"][0]["answer_quote"], "")

    def test_low_confidence_or_mismatched_reference_requires_review(self):
        for kind in ("confidence", "reference"):
            records = records_for()
            if kind == "confidence":
                records[1]["final_evaluation"]["confidence"] = "低"
            else:
                records[1]["final_evaluation"]["point_assessments"][1]["reference_point"] = "另一个参考点"
            self.assertIsNone(evidence.build_dimension_report(records)["dimensions"]["边界情况意识"])

    def test_contradictory_fields_do_not_produce_precise_scores(self):
        records = records_for()
        records[1]["final_evaluation"]["misconceptions"] = ["交换过程有明确误解"]
        self.assertIsNone(evidence.build_dimension_report(records)["dimensions"]["关键概念掌握"])

    def test_teacher_digest_does_not_repeat_an_ungrounded_error_as_a_fact(self):
        record = records_for()[1]
        record["final_evaluation"]["point_assessments"][1].update(
            status="理解错误", answer_quote="回答中不存在的错误断言",
        )
        self.assertEqual(insights.question_finding(record, 2)[1], "证据不足")

    def test_follow_up_can_supply_evidence_but_cannot_automatically_raise_score(self):
        records = records_for()
        records[1]["answer"] = "我需要再想一下单元素情况。"
        records[1]["follow_up_answer"] = "temp先保存原值。n=1时排序循环不执行。"
        points = records[1]["final_evaluation"]["point_assessments"]
        points[0].update(status="部分掌握", answer_quote="temp先保存原值")
        points[1].update(status="掌握", answer_quote="n=1时排序循环不执行")
        report = evidence.build_dimension_report(records)
        self.assertEqual(report["dimensions"]["关键概念掌握"], 65)
        self.assertEqual(report["dimensions"]["边界情况意识"], 100)
        self.assertEqual(report["dimension_evidence"]["边界情况意识"]["points"][0]["source"], "追问回答")

    def test_quote_matches_whitespace_but_never_crosses_answer_boundary(self):
        self.assertEqual(evidence.quote_source("n = 1", "n=1"), "首次回答")
        self.assertEqual(evidence.quote_source("最小值", "最小", "值"), "")

    def test_old_questions_without_mapping_have_no_new_fabricated_scores(self):
        records = records_for()
        for record in records:
            record.pop("reference_dimensions")
        report = evidence.build_dimension_report(records)
        self.assertTrue(all(score is None for score in report["dimensions"].values()))

    def test_report_ignores_model_scores_and_passes_optional_policy_and_evidence(self):
        records = records_for()
        with patch.object(llm, "_chat_json", return_value={
            "dimensions": {name: 2 for name in evidence.DIMENSIONS},
            "summary": "回答充分。", "suggestion": "继续验证边界。", "review_reasons": [],
        }) as chat:
            result = llm.generate_report({"problem": "选择排序", "explanation": "", "code": "代码",
                                          "assignment_snapshot": {"explanation_required": False}}, records)
        self.assertEqual(result["overall"], 100)
        prompt = chat.call_args.args[0][1]["content"]
        self.assertIn("dimension_evidence", prompt)
        self.assertIn("answer_quote", prompt)
        self.assertIn("未提交选做思路", prompt)

    def test_incomplete_report_summary_cannot_claim_full_understanding(self):
        records = records_for()
        records[1]["reference_dimensions"][1] = "关键概念掌握"
        with patch.object(llm, "_chat_json", return_value={
            "summary": "整体理解充分，满分。", "suggestion": "核查边界。", "review_reasons": [],
        }):
            report = llm.generate_report({"problem": "选择排序", "explanation": "", "code": "代码"}, records)
        self.assertNotIn("满分", report["summary"])
        self.assertTrue(report["review_required"])
        self.assertIn("评价尚不完整", report["summary"])
        self.assertEqual(insights.mastery_summary(report, records)["status"], "证据不足")

    def test_partial_point_requires_follow_up_even_with_high_model_score(self):
        question = records_for(1)[0]
        response = {**question["final_evaluation"], "score": 100, "feedback": "部分解释仍不充分。", "follow_up_question": "为什么不再检查已排序前缀？"}
        with patch.object(llm, "_chat_json", side_effect=[{"status": "可评价", "premise_issue": "", "basis": "模拟核对题目前提。"}, response]):
            result = llm.evaluate_initial_answer("选择排序", "代码", question, question["answer"])
        self.assertGreaterEqual(result["score"], 80)
        self.assertTrue(result["should_follow_up"])
        self.assertIn(question["reference_points"][1], result["missing_points"])

    def test_question_generator_keeps_point_dimension_mapping(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        with patch.object(llm, "_chat_json", return_value={"questions": fixture["questions"]}):
            questions = llm.generate_questions("选择排序", [], "代码", "")
        self.assertEqual(questions[1]["reference_dimensions"], ["关键概念掌握", "边界情况意识"])
        fixture["questions"][1]["reference_dimensions"] = ["不存在的维度"]
        with patch.object(llm, "_chat_json", return_value={"questions": fixture["questions"]}):
            with self.assertRaises(llm.LLMServiceError):
                llm.generate_questions("选择排序", [], "代码", "")


class EvidencePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.original_path, self.original_read_only = db.DB_PATH, db.READ_ONLY
        db.DB_PATH, db.READ_ONLY = Path(self.folder.name) / "evidence.db", False
        db.init_db()
        self.records = records_for()
        self.submission = db.create_submission_with_questions(
            "20260099", "证据测试同学", "选择排序", "", "int main(){}", "2026-10-07 10:00", self.records,
        )
        for index, record in enumerate(self.records):
            db.save_initial_result(self.submission, index, record["answer"], "", record["final_evaluation"], True)

    def tearDown(self):
        db.DB_PATH, db.READ_ONLY = self.original_path, self.original_read_only
        self.folder.cleanup()

    def save_incomplete(self):
        records = copy.deepcopy(self.records)
        records[1]["final_evaluation"]["point_assessments"][1]["answer_quote"] = "未出现的文字"
        report = report_for(records)
        db.save_report(self.submission, report, "2026-10-07 10:05")
        return report

    def test_mapping_and_quotes_survive_database_roundtrip(self):
        saved = db.get_qa_records(self.submission)
        self.assertEqual(saved[1]["reference_dimensions"], ["关键概念掌握", "边界情况意识"])
        self.assertEqual(evidence.build_dimension_report(saved)["overall"], 100)

    def test_incomplete_report_is_completed_without_exposing_internal_score_marker(self):
        expected = self.save_incomplete()
        report = db.get_report(self.submission)
        self.assertIsNone(report["overall"])
        self.assertEqual(report["dimension_evidence"], expected["dimension_evidence"])
        self.assertEqual(db.get_submission(self.submission)["status"], "已完成")
        self.assertIsNone(db.list_submissions()[0]["overall"])
        self.assertIsNone(db.list_learning_records()[0]["overall"])
        self.assertTrue(any(t["action"] == "review_submission" for t in db.list_teacher_tasks()))

    def test_incomplete_report_cannot_be_auto_approved_but_teacher_can_confirm(self):
        self.save_incomplete()
        with self.assertRaisesRegex(ValueError, "评价尚不完整"):
            db.save_teacher_review(self.submission, "教师", "认可AI诊断", 80, "")
        db.save_teacher_review(self.submission, "教师", "调整理解度结论", 80, "核查原文后确认。")
        self.assertEqual(db.get_teacher_review(self.submission)["confirmed_overall"], 80)
        self.assertIsNone(db.get_report(self.submission)["overall"])

    def test_redefense_does_not_require_inventing_a_confirmation_score(self):
        self.save_incomplete()
        db.save_teacher_review(self.submission, "教师", "要求学生重新答辩", None, "补充边界解释。")
        self.assertIsNone(db.get_teacher_review(self.submission)["confirmed_overall"])
        self.assertIsNone(db.list_teacher_review_history(self.submission)[0]["confirmed_overall"])
        self.assertIsNone(db.list_submissions()[0]["teacher_confirmed_overall"])
        request = db.get_latest_redefense_request(self.submission)
        child = db.start_redefense(request["id"], self.records, "2026-10-07 10:10")
        self.assertEqual(db.get_qa_records(child)[1]["reference_dimensions"], self.records[1]["reference_dimensions"])

    def test_schema_migration_and_old_read_only_reports_preserve_history(self):
        legacy = {"overall": 91, "dimensions": {name: 91 for name in evidence.DIMENSIONS},
                  "level": "理解充分", "weakest": "边界情况意识", "summary": "历史总结",
                  "suggestion": "历史建议", "review_required": False, "review_reasons": []}
        db.save_report(self.submission, legacy, "2026-10-07 10:05")
        with db.get_connection() as conn:
            conn.execute("ALTER TABLE reports DROP COLUMN evidence_json")
            conn.execute("ALTER TABLE qa_records DROP COLUMN reference_dimensions_json")
        db.READ_ONLY = True
        self.assertEqual(db.list_submissions()[0]["overall"], 91)
        self.assertEqual(db.list_learning_records()[0]["overall"], 91)
        self.assertEqual(db.get_qa_records(self.submission)[0]["reference_dimensions"], [])
        db.READ_ONLY = False
        db.init_db()
        saved = db.get_report(self.submission)
        self.assertEqual(saved["overall"], 91)
        self.assertEqual(saved["dimensions"], legacy["dimensions"])
        self.assertEqual(saved["summary"], "历史总结")

    def test_student_teacher_archive_and_class_analysis_render_incomplete_report(self):
        from streamlit.testing.v1 import AppTest
        self.save_incomplete()
        app_path = str(Path(__file__).resolve().parents[1] / "app.py")
        student = AppTest.from_file(app_path)
        for key, value in {"auth_role": "学生", "auth_name": "证据测试同学", "auth_student_id": "20260099",
                           "current_page": "学生报告", "submission_id": self.submission}.items():
            student.session_state[key] = value
        authenticate(student, "学生", "20260099", "证据测试同学")
        student.run(timeout=20)
        self.assertEqual(len(student.exception), 0)
        self.assertTrue(any(m.value == "评价尚不完整" for m in student.metric))
        student.session_state["current_page"] = "学习档案"
        student.run(timeout=20)
        self.assertEqual(len(student.exception), 0)
        self.assertTrue(any(m.value == "暂无完整评分" for m in student.metric))
        teacher = AppTest.from_file(app_path)
        teacher.session_state["auth_role"] = "教师"
        teacher.session_state["auth_name"] = "任课教师"
        teacher.session_state["current_page"] = "教师工作台"
        authenticate(teacher, "教师")
        teacher.run(timeout=20)
        self.assertEqual(len(teacher.exception), 0)
        decision = next(s for s in teacher.selectbox if s.label == "处理结论")
        self.assertNotIn("认可AI诊断", decision.options)
        decision.set_value("要求学生重新答辩").run(timeout=20)
        next(b for b in teacher.button if b.label == "保存教师复核").click().run(timeout=20)
        self.assertEqual(len(teacher.exception), 0)
        self.assertIsNone(db.get_teacher_review(self.submission)["confirmed_overall"])
        next(r for r in teacher.radio if r.label == "工作区域").set_value("班级分析").run(timeout=20)
        self.assertEqual(len(teacher.exception), 0)


if __name__ == "__main__":
    unittest.main()
