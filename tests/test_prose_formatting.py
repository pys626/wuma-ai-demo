import ast
import copy
import html
from pathlib import Path
import unittest
from unittest.mock import Mock

import defense_evidence
import teacher_insights as insights
from text_formatting import clean_prose, join_clauses


class ProseFormattingTests(unittest.TestCase):
    def app_helpers(self, names, **namespace):
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        nodes = [node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        namespace.update(insights=insights, html=html)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), namespace)
        return namespace

    def test_composed_summary_keeps_scores_and_explanation_with_one_separator(self):
        result = join_clauses([
            "代码AC · 8/8（100/100）。", None, "",
            "答辩理解度：96/100；",
            "第1题 · 仍需补充：minIndex记录下标。；分数反映整体表现。",
            "教师确认理解度：96/100。",
        ], sentence=True)
        self.assertEqual(result, "代码AC · 8/8（100/100）；答辩理解度：96/100；"
                         "第1题 · 仍需补充：minIndex记录下标；分数反映整体表现；教师确认理解度：96/100。")
        self.assertEqual(join_clauses([None, "。", "；"], sentence=True), "")

    def test_old_prose_with_spaces_is_fixed_without_changing_code_terms_or_sentences(self):
        result = clean_prose("minIndex = i。 \n；j = i + 1； 。数值3.5。。下一句？")
        self.assertEqual(result, "minIndex = i；j = i + 1。数值3.5。下一句？")
        self.assertEqual(clean_prose("第一句。\n第二句。"), "第一句。\n第二句。")
        self.assertEqual(join_clauses(["是否需要复核？"], sentence=True), "是否需要复核？")

    def test_multiple_missing_points_clean_summary_but_preserve_student_and_model_records(self):
        records = [{
            "answer": "我记录下标。；这是原始输入。", "code": "int x = 0;",
            "final_evaluation": {"confidence": "高", "missing_points": ["仍需解释下标。"],
                                 "point_assessments": [{"status": "部分掌握"}]},
        }, {
            "answer": "使用临时变量。", "final_evaluation": {
                "confidence": "高", "missing_points": ["仍需解释交换。"],
                "point_assessments": [{"status": "部分掌握"}]},
        }]
        before = copy.deepcopy(records)
        result = insights.mastery_summary({"overall": 96}, records)
        self.assertEqual(result["status"], "需要巩固")
        self.assertIn("第1题", result["text"])
        self.assertIn("第2题", result["text"])
        self.assertNotRegex(result["text"], r"[。；;]\s*[。；;]")
        self.assertEqual(records, before)

    def test_dimension_reasons_are_readable_and_missing_evidence_remains_unscored(self):
        records = []
        for confidence in ["高", "低"]:
            records.append({
                "answer": "选择最小值。", "reference_points": ["解释下标"],
                "reference_dimensions": ["程序逻辑理解"],
                "final_evaluation": {"confidence": confidence, "point_assessments": [{
                    "reference_point": "解释下标", "status": "掌握", "answer_quote": "不存在的回答。",
                }]},
            })
        before = copy.deepcopy(records)
        report = defense_evidence.build_dimension_report(records)
        reason = report["dimension_evidence"]["程序逻辑理解"]["reason"]
        self.assertIn("原文", reason)
        self.assertIn("把握不足", reason)
        self.assertNotRegex(reason, r"[。；;]\s*[。；;]")
        self.assertIsNone(report["overall"])
        self.assertEqual(records, before)

    def test_teacher_digest_handles_existing_decision_punctuation(self):
        helpers = self.app_helpers({"build_teacher_digest", "compact_code_result", "understanding_text",
                                    "confirmed_score_text"})
        digest = helpers["build_teacher_digest"]({
            "teacher_reviewed": True, "teacher_decision": "已复核。", "teacher_confirmed_overall": 96,
        }, None, {"overall": 96}, [], [])
        self.assertIn("96/100", digest["conclusion"])
        self.assertNotRegex(digest["conclusion"], r"[。；;]\s*[。；;]")
        self.assertEqual(digest["action"].count("。"), 1)

    def test_legacy_reading_note_is_cleaned_and_html_is_still_escaped(self):
        st = Mock()
        helpers = self.app_helpers({"render_reading_note"}, st=st)
        helpers["render_reading_note"]("本次报告概览", "需要补充。；教师确认：96/100。<script>")
        rendered = st.markdown.call_args.args[0]
        self.assertIn("需要补充；教师确认：96/100。&lt;script&gt;", rendered)
        self.assertNotIn("<script>", rendered)


if __name__ == "__main__":
    unittest.main()
