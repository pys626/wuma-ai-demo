import ast
import html
import unittest
from pathlib import Path

import pandas as pd


class TeacherVisualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        names = {"compact_code_result", "teacher_summary_cards_html", "teacher_bar_rows_html",
                 "build_teacher_dimension_rows", "build_teacher_weakness_rows", "build_teacher_hidden_case_rows"}
        module = ast.parse(source)
        nodes = [node for node in module.body if isinstance(node, ast.FunctionDef) and node.name in names]
        colors = next(node.value for node in module.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "DIMENSION_COLORS" for target in node.targets))
        cls.helpers = {"html": html, "DIMENSION_COLORS": ast.literal_eval(colors)}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), cls.helpers)

    def test_teacher_decision_and_score_are_complete_separate_text_and_escaped(self):
        record = {"teacher_reviewed": True, "teacher_decision": "要求学生重新答辩",
                  "teacher_confirmed_overall": 87}
        render = self.helpers["teacher_summary_cards_html"]
        result = render(record, {"overall": 87})
        self.assertIn('class="wuma-summary-value">要求学生重新答辩</div>', result)
        self.assertIn('class="wuma-summary-note">确认理解度 87/100</div>', result)
        record["teacher_decision"] = '<img src=x onerror="alert(1)">'
        self.assertNotIn("<img", render(record, None))
        self.assertIn("&lt;img", render(record, None))
        self.assertIn("等待教师确认", render({}, None))

    def test_dimension_rows_use_common_zero_to_one_hundred_scale_and_visible_values(self):
        rows = self.helpers["build_teacher_dimension_rows"]({"程序逻辑理解": 88, "边界情况意识": 78})
        self.assertEqual([row["maximum"] for row in rows], [100, 100])
        self.assertEqual(rows[0]["value_text"], "88 / 100")
        self.assertEqual(rows[1]["note"], "当前相对较低的维度")
        result = self.helpers["teacher_bar_rows_html"](rows, "0—100分")
        self.assertIn("width:88.0000%", result)
        self.assertIn("width:78.0000%", result)

    def test_weakness_rows_show_integer_counts_and_use_full_sample_denominator(self):
        reports = [{"_mastery": {"status": "需要巩固"}}, {"_mastery": {"status": "需要巩固"}}, {}]
        rows = self.helpers["build_teacher_weakness_rows"](reports)
        self.assertEqual(rows[0]["value_text"], "2人次 · 66.7%")
        self.assertEqual(rows[0]["maximum"], 3)
        self.assertEqual(self.helpers["build_teacher_weakness_rows"](reports, True)[0]["value_text"], "2人 · 66.7%")
        self.assertEqual(self.helpers["build_teacher_weakness_rows"]([]), [])

    def test_hidden_test_rows_show_exact_numerator_denominator_and_zero_full_bars(self):
        records = pd.DataFrame([
            {"实验任务": "区间第k小值", "测试分组": "边界", "测试点": "全负数", "通过次数": 0, "判定次数": 2},
            {"实验任务": "区间第k小值", "测试分组": "基础", "测试点": "普通样例", "通过次数": 1, "判定次数": 3},
            {"实验任务": "区间第k小值", "测试分组": "性能", "测试点": "大规模", "通过次数": 1, "判定次数": 1},
        ])
        rows = self.helpers["build_teacher_hidden_case_rows"](records, True)
        self.assertEqual([row["value_text"] for row in rows], ["0%", "33.3%", "100%"])
        self.assertEqual(rows[1]["note"], "通过 1 / 3 次 · 未通过 2 次")
        result = self.helpers["teacher_bar_rows_html"](rows, "0—100%")
        self.assertIn("width:0.0000%", result)
        self.assertIn("width:100.0000%", result)
        single = self.helpers["build_teacher_hidden_case_rows"](records.tail(1), True)
        self.assertEqual(len(single), 1)
        self.assertEqual(single[0]["note"], "通过 1 / 1 次 · 未通过 0 次")

    def test_long_chart_labels_are_preserved_as_text_and_cannot_inject_html(self):
        label = "长名称" * 50 + "<script>alert(1)</script>"
        row = {"label": label, "value": 30, "maximum": 100, "value_text": "30%",
               "note": "样本 < 5", "color": 'red;" onclick="alert(1)'}
        result = self.helpers["teacher_bar_rows_html"]([row], "0—100%")
        self.assertIn(html.escape(label), result)
        self.assertNotIn("<script>", result)
        self.assertNotIn('onclick="', result)
        self.assertIn("background:#2563EB", result)


if __name__ == "__main__":
    unittest.main()
