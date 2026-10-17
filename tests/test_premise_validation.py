"""Offline regressions for the real Dijkstra premise failure; no model calls."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import llm_service as llm
import evaluation_benchmark as benchmark
import defense_evidence as evidence
import teacher_insights as insights
import test_question_grounding as grounding

ROOT = Path(__file__).resolve().parents[1]
VALID = {"status": "可评价", "premise_issue": "", "basis": "已核对实际代码路径与公开约束。"}
ISSUE = "不可达节点不会通过有限距离松弛被发现，题目断言INF入队与实际代码冲突。"
PENDING = {"status": "待教师核查", "premise_issue": ISSUE, "basis": "起点0入队，之后只有严格改善的有限距离入队。"}


def grading(item):
    return {"score": 98, "confidence": "高", "premise_issue": "", "feedback": "已核对两段解释。",
            "missing_points": [], "misconceptions": [], "follow_up_question": "",
            "point_assessments": [{"status": "掌握", "answer_quote": quote, "evidence": "实际回答依据。"}
                                  for quote in item["answer"].split("\n")]}


class PremiseValidationTests(unittest.TestCase):
    def setUp(self):
        self.bad, self.normal = benchmark.build_plan(1, "dijkstra_premise")

    def initial(self, item):
        return llm.evaluate_initial_answer(item["problem"], item["code"], item["question"], item["answer"])

    def assert_pending(self, result):
        self.assertIsNone(result["score"])
        self.assertEqual(result["score_level"], "待教师核查")
        self.assertEqual(result["premise_issue"], ISSUE)
        self.assertEqual(result["confidence"], "低")
        self.assertFalse(result["is_sufficient"])
        self.assertFalse(result["should_follow_up"])
        self.assertEqual(result["follow_up_question"], "")
        self.assertEqual(result["missing_points"], [])
        self.assertEqual(result["misconceptions"], [])
        self.assertTrue(all(p["status"] == "未评价" for p in result["point_assessments"]))
        self.assertIn("不因未承认该前提而判为理解错误", result["feedback"])

    def test_independent_review_runs_before_grading_without_student_answer_or_expected_labels(self):
        with patch.object(llm, "_chat_json", side_effect=[VALID, grading(self.normal)]) as chat:
            result = self.initial(self.normal)
        self.assertEqual(chat.call_count, 2)
        premise_prompt = chat.call_args_list[0].args[0][1]["content"]
        grading_prompt = chat.call_args_list[1].args[0][1]["content"]
        self.assertNotIn(self.normal["answer"], premise_prompt)
        self.assertNotIn("<student_answer>", premise_prompt)
        self.assertNotIn(self.normal["case"], premise_prompt)
        self.assertNotIn(self.normal["manual_check"], premise_prompt)
        self.assertIn(self.normal["answer"], grading_prompt)
        self.assertIn(self.normal["question"]["question"], premise_prompt)
        context = json.loads(premise_prompt.split("<experiment_context>")[1].split("</experiment_context>")[0])
        self.assertEqual(context, self.normal["question"]["experiment_context"])
        self.assertTrue(result["is_sufficient"])
        self.assertEqual(result["premise_review"], VALID)

    def test_invalid_premise_stops_before_grading_and_never_returns_student_error_or_zero(self):
        with patch.object(llm, "_chat_json", return_value=PENDING) as chat:
            result = self.initial(self.bad)
        self.assertEqual(chat.call_count, 1)
        self.assert_pending(result)
        self.assertTrue(all(row["是否一致"] == "是" for row in benchmark.comparison_rows(self.bad, result)))

    def test_insufficient_facts_are_reviewed_instead_of_inventing_constraints(self):
        pending = {**PENDING, "basis": "缺少已保存约束，不能确认题目断言。"}
        with patch.object(llm, "_chat_json", return_value=pending):
            result = self.initial(self.bad)
        self.assert_pending(result)

    def test_malformed_or_conflicting_review_is_rejected_before_grading(self):
        bad_payloads = [{}, {**VALID, "status": "随意评价"}, {**VALID, "premise_issue": ISSUE},
                        {**PENDING, "premise_issue": ""}, {**VALID, "basis": ""},
                        {**VALID, "premise_issue": []}, {**VALID, "basis": "字" * 501},
                        {k: v for k, v in VALID.items() if k != "premise_issue"}]
        for payload in bad_payloads:
            with self.subTest(payload=payload), patch.object(llm, "_chat_json", return_value=payload) as chat:
                with self.assertRaises(llm.LLMServiceError):
                    self.initial(self.normal)
                self.assertEqual(chat.call_count, 1)

    def test_grading_stage_premise_issue_overrides_conflicting_score_feedback_and_misconceptions(self):
        raw = {**grading(self.bad), "score": 10, "feedback": "__RAW_BLAME__学生未承认溢出，应判错误。",
               "premise_issue": ISSUE, "missing_points": ["没有提供不存在的修复"],
               "misconceptions": ["不接受错误题目前提"], "follow_up_question": "继续承认溢出。"}
        raw["point_assessments"][0]["status"] = "理解错误"
        with patch.object(llm, "_chat_json", side_effect=[VALID, raw]):
            result = self.initial(self.bad)
        self.assert_pending(result)
        self.assertNotIn("__RAW_BLAME__", result["feedback"])
        self.assertEqual(result["raw_evaluation"], raw)

    def test_existing_initial_premise_issue_cannot_be_cleared_by_follow_up(self):
        with patch.object(llm, "_chat_json") as chat:
            result = llm.evaluate_follow_up(self.bad["problem"], self.bad["code"], self.bad["question"],
                self.bad["answer"], "旧错误追问", self.bad["answer"], {"premise_issue": ISSUE, "score": 10})
        chat.assert_not_called()
        self.assert_pending(result)

    def test_follow_up_premise_is_independently_checked_before_final_grading(self):
        follow_up = "不可达节点为什么会以INF入队？"
        with patch.object(llm, "_chat_json", return_value=PENDING) as chat:
            result = llm.evaluate_follow_up(self.normal["problem"], self.normal["code"], self.normal["question"],
                self.normal["answer"], follow_up, self.normal["answer"])
        self.assertEqual(chat.call_count, 1)
        prompt = chat.call_args.args[0][1]["content"]
        self.assertIn(follow_up, prompt)
        self.assertNotIn(self.normal["answer"], prompt)
        self.assert_pending(result)

    def test_normal_follow_up_still_grades_actual_answers(self):
        with patch.object(llm, "_chat_json", side_effect=[VALID, grading(self.normal)]) as chat:
            result = llm.evaluate_follow_up(self.normal["problem"], self.normal["code"], self.normal["question"],
                self.normal["answer"], "说明有限范围", self.normal["answer"])
        self.assertEqual(chat.call_count, 2)
        self.assertTrue(result["is_sufficient"])
        self.assertEqual(result["premise_issue"], "")

    def test_final_grading_stage_also_suppresses_conflicting_student_error(self):
        raw = {**grading(self.normal), "premise_issue": ISSUE, "feedback": "__RAW_BLAME__理解错误。"}
        with patch.object(llm, "_chat_json", side_effect=[VALID, raw]):
            result = llm.evaluate_follow_up(self.normal["problem"], self.normal["code"], self.normal["question"],
                self.normal["answer"], "有问题的追问", self.normal["answer"])
        self.assert_pending(result)
        self.assertEqual(result["raw_evaluation"], raw)

    def test_pending_score_is_excluded_from_report_and_raw_blame_is_not_forwarded_to_report_model(self):
        records = grounding.records_for()
        raw = {"score": 10, "feedback": "__RAW_BLAME__学生有错。", "premise_issue": ISSUE}
        records[2]["final_evaluation"] = llm._pending_premise_evaluation(records[2], ISSUE, PENDING, raw=raw)
        with patch.object(llm, "_chat_json", return_value={"summary": "待核查。", "suggestion": "核对题目前提。", "review_reasons": []}) as chat:
            report = llm.generate_report(grounding.SUBMISSION, records)
        self.assertIsNone(report["overall"])
        self.assertIsNone(report["dimensions"]["分析与修改能力"])
        self.assertEqual(report["dimensions"]["程序逻辑理解"], 100)
        self.assertTrue(report["review_required"])
        self.assertTrue(any(ISSUE in reason for reason in report["review_reasons"]))
        self.assertFalse(any("低于60" in reason or "学生回答中仍存在明确错误" in reason for reason in report["review_reasons"]))
        self.assertNotIn("__RAW_BLAME__", chat.call_args.args[0][1]["content"])
        self.assertEqual(insights.question_finding(records[2], 3)[1], "待核查")

    def test_non_premise_null_score_is_still_rejected_as_incomplete_evaluation(self):
        records = grounding.records_for()
        records[2]["final_evaluation"]["score"] = None
        with patch.object(llm, "_chat_json") as chat:
            with self.assertRaises(llm.LLMServiceError):
                llm.generate_report(grounding.SUBMISSION, records)
        chat.assert_not_called()
