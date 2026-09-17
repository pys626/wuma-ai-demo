import ast
import copy
from pathlib import Path
import unittest

import teacher_insights as insights


def question(point="前缀版本相减", status="掌握", confidence="高", **changes):
    item = {"question": "问题", "answer": "回答", "reference_points": [point],
            "final_evaluation": {"confidence": confidence, "point_assessments": [
                {"reference_point": point, "status": status, "evidence": "回答中的直接说明"}]}}
    item.update(changes)
    return item


class TeacherInsightsTests(unittest.TestCase):
    def setUp(self):
        self.report = {"overall": 93, "dimensions": {"逻辑": 95, "概念": 93, "边界": 92, "修改": 94}}

    def test_high_scores_are_only_relative_and_equal_scores_have_no_weakness(self):
        result = insights.mastery_summary(self.report, [question()])
        self.assertEqual(result["status"], "相对较弱（整体良好）")
        self.assertEqual(result["dimension"], "边界")
        self.report["dimensions"] = {"逻辑": 95, "边界": 95}
        self.assertEqual(insights.mastery_summary(self.report, [question()])["dimension"], "")

    def test_high_score_cannot_override_actual_misconception(self):
        result = insights.mastery_summary(self.report, [question(status="理解错误")])
        self.assertEqual(result["status"], "需要巩固")

    def test_no_answer_missing_evaluation_and_low_confidence_are_not_failure(self):
        for q in [question(answer=""), question(final_evaluation={}), question(confidence="低", status="理解错误")]:
            self.assertEqual(insights.mastery_summary(self.report, [q])["status"], "证据不足")
        self.assertEqual(insights.mastery_summary(self.report, [])["status"], "证据不足")

    def test_low_score_without_error_evidence_is_not_invented_misconception(self):
        self.report["dimensions"]["边界"] = 50
        self.assertEqual(insights.mastery_summary(self.report, [question()])["status"], "待核查")

    def test_teacher_review_uses_answer_evidence_even_when_report_did_not_flag_it(self):
        record = {"status": "已完成", "teacher_reviewed": False, "review_required": False, "attempt_number": 1}
        self.assertTrue(insights.needs_teacher_review(record, self.report, [question(status="部分掌握")]))
        self.assertTrue(insights.needs_teacher_review(record, self.report, [question(answer="")]))
        self.assertFalse(insights.needs_teacher_review(record, self.report, [question()]))
        self.assertFalse(insights.needs_teacher_review(dict(record, teacher_reviewed=True), self.report, [question(status="理解错误")]))

    def test_review_state_distinguishes_pending_completed_and_spot_check(self):
        record = {"status": "已完成", "teacher_reviewed": False, "review_required": False, "attempt_number": 1}
        pending = insights.teacher_review_state(record, self.report, [question(status="部分掌握")])
        self.assertEqual(pending["状态"], "待复核")
        self.assertTrue(pending["是否当前待办"])

        completed = insights.teacher_review_state(
            dict(record, teacher_reviewed=True), self.report, [question(status="部分掌握")]
        )
        self.assertEqual(completed["状态"], "已复核")
        self.assertFalse(completed["是否当前待办"])

        spot_check = insights.teacher_review_state(record, self.report, [question()])
        self.assertEqual(spot_check["状态"], "常规抽查")
        self.assertFalse(spot_check["是否当前待办"])

    def test_final_evaluation_overrides_old_mistake(self):
        q = question(initial_evaluation={"misconceptions": ["旧错误"], "confidence": "高"})
        self.assertEqual(insights.question_finding(q, 2)[1], "已有掌握证据")

    def test_errors_and_review_reason_precede_static_preliminary_and_relative_minimum(self):
        self.report["review_reasons"] = ["前后回答矛盾，需要复核"]
        preliminary = {"criteria": [{"criterion_name": "普通初评", "status": "部分满足"}] * 5}
        rows = insights.ranked_concerns({}, preliminary, self.report, [question(), question(status="理解错误")])
        self.assertEqual(len(rows), 3)
        self.assertIn("第2题", rows[0])
        self.assertIn("前后回答矛盾", rows[1])
        self.assertNotIn("相对较弱", str(rows))

    def test_reordered_questions_match_reference_point_not_index(self):
        old = [question("A", "理解错误"), question("B")]
        new = [question("B"), question(" A ")]
        rows = insights.comparable_points(old, new)
        self.assertIn("第2题", rows[0]["本次证据"])
        self.assertIn("已有纠正证据", rows[0]["变化判断"])

    def test_same_question_index_with_different_points_is_not_comparable(self):
        rows = insights.comparable_points([question("A", "理解错误")], [question("B")])
        self.assertEqual(len(rows), 2)
        self.assertIn("本次未覆盖", rows[0]["变化判断"])
        self.assertEqual(rows[1]["变化判断"], "本次新增考查点")

    def test_low_confidence_or_missing_answer_never_resolves_old_error(self):
        for q in [question(confidence="低"), question(answer="")]:
            rows = insights.comparable_points([question(status="理解错误")], [q])
            self.assertIn("证据不足", rows[0]["变化判断"])

    def test_duplicate_conflicting_points_do_not_select_favorable_evidence(self):
        rows = insights.comparable_points([question(status="理解错误")], [question(), question(status="理解错误")])
        self.assertIn("仍有理解错误", rows[0]["变化判断"])

    def test_global_misconception_prevents_automatic_correction_claim(self):
        q = question()
        q["final_evaluation"]["misconceptions"] = ["有矛盾，需确认"]
        rows = insights.comparable_points([question(status="理解错误")], [q])
        self.assertIn("证据不足", rows[0]["变化判断"])

    def test_legacy_reference_without_evaluation_keeps_unknown_and_input_unmodified(self):
        old = [question(final_evaluation={})]
        snapshot = copy.deepcopy(old)
        rows = insights.comparable_points(old, [question()])
        self.assertIn("证据不足", rows[0]["变化判断"])
        self.assertEqual(old, snapshot)

    def record(self, id, student="001", assignment=1, code="环境不可用", status="答辩中", **kw):
        return dict(id=id, student_id=student, assignment_id=assignment, problem="同名实验",
                    status=status, code_verification={"overall_status": code}, **kw)

    def attention(self, record):
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text()
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "teacher_record_needs_attention")
        ns = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), ns)
        return ns[node.name](record)

    def test_success_retires_old_technical_attention_but_preserves_history(self):
        records = [self.record(1), self.record(2, code="全部通过", status="已完成")]
        before = copy.deepcopy(records)
        marked = insights.mark_superseded_records(records)
        self.assertFalse(self.attention(marked[0]))
        self.assertEqual(marked[0]["_code_resolved_by"], 2)
        self.assertEqual(records, before)

    def test_unanswered_feedback_manual_review_and_redefense_are_retained(self):
        for extra in [{"pending_feedback_count": 1}, {"review_required": True}, {"redefense_status": "待开始"}]:
            old = self.record(1, status="已完成", **extra)
            marked = insights.mark_superseded_records([old, self.record(2, code="全部通过", status="已完成")])
            self.assertTrue(self.attention(marked[0]))

    def test_other_student_assignment_or_new_failure_does_not_resolve_old_code(self):
        for new in [self.record(2, student="002", code="全部通过", status="已完成"),
                    self.record(2, assignment=2, code="全部通过", status="已完成"), self.record(2)]:
            marked = insights.mark_superseded_records([self.record(1), new])
            self.assertIsNone(marked[0]["_code_resolved_by"])
            self.assertTrue(self.attention(marked[0]))

    def test_code_success_does_not_claim_unfinished_defense_is_complete(self):
        marked = insights.mark_superseded_records([self.record(1), self.record(2, code="全部通过")])
        self.assertEqual(marked[0]["_code_resolved_by"], 2)
        self.assertIsNone(marked[0]["_defense_resolved_by"])
        self.assertTrue(self.attention(marked[0]))


if __name__ == "__main__":
    unittest.main()
