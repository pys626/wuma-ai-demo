from auth_helpers import authenticate, load_teacher_draft
import json
import re
import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest
from unittest.mock import patch

import database as db
import code_verifier as verifier
import llm_service as llm
import assignment_drafts
import student_drafts


QUESTIONS = [
    {
        "dimension": "程序逻辑理解",
        "question": "为什么使用数组首元素初始化最大值？",
        "reference_points": ["初值来自真实元素"],
    },
    {
        "dimension": "边界情况意识",
        "question": "输入规模超过容量时会怎样？",
        "reference_points": ["数组越界"],
    },
    {
        "dimension": "分析与修改能力",
        "question": "如何改为寻找最小值？",
        "reference_points": ["改变比较方向"],
    },
]

VERIFICATION = {
    "enabled": True,
    "overall_status": "全部通过",
    "message": "共执行3组测试，通过3组。",
    "compiler": "g++",
    "compile": {"status": "成功", "message": "编译成功。", "duration_ms": 10},
    "passed": 3,
    "total": 3,
    "score": 100,
    "max_score": 100,
    "group_scores": [
        {
            "group": "基础功能",
            "score": 100,
            "max_score": 100,
            "passed": 1,
            "total": 1,
        }
    ],
    "cases": [
        {
            "name": "普通正数",
            "group": "基础功能",
            "weight": 100,
            "score": 100,
            "verdict_code": "AC",
            "status": "通过",
            "input": "5\n3 8 2 6 1\n",
            "expected_output": "8\n",
            "actual_output": "8\n",
            "stderr": "",
            "duration_ms": 2,
            "message": "实际输出与预期输出一致。",
        }
    ],
}

CUSTOM_RUN = {
    "enabled": True,
    "overall_status": "运行成功",
    "message": "程序已使用自定义输入正常运行。",
    "compiler": "g++",
    "compile": {"status": "成功", "message": "编译成功。", "duration_ms": 10},
    "passed": 1,
    "total": 1,
    "cases": [
        {
            "name": "自定义运行",
            "status": "运行成功",
            "actual_output": "8\n",
            "stderr": "",
            "duration_ms": 2,
            "message": "程序正常结束。",
        }
    ],
}

CUSTOM_RUN_EMPTY = {
    **CUSTOM_RUN,
    "cases": [
        {
            **CUSTOM_RUN["cases"][0],
            "actual_output": "",
        }
    ],
}

PUBLIC_SAMPLE_WRONG = {
    **VERIFICATION,
    "overall_status": "全部未通过",
    "message": "共执行1组测试，通过0组。",
    "passed": 0,
    "total": 1,
    "score": 0,
    "max_score": 100,
    "cases": [
        {
            **VERIFICATION["cases"][0],
            "status": "未通过",
            "score": 0,
            "verdict_code": "WA",
            "expected_output": "8\n",
            "actual_output": "7\n",
            "message": "实际输出与预期输出不一致。",
        }
    ],
}

PUBLIC_SAMPLES_MIXED = {
    **VERIFICATION,
    "overall_status": "部分通过",
    "message": "共执行2组测试，通过1组。",
    "passed": 1,
    "total": 2,
    "score": 50,
    "max_score": 100,
    "cases": [
        {**VERIFICATION["cases"][0], "weight": 50, "score": 50},
        {
            **VERIFICATION["cases"][0],
            "name": "连续空格",
            "status": "未通过",
            "weight": 50,
            "score": 0,
            "verdict_code": "WA",
            "input": "3\n1 2 3\n",
            "expected_output": "1   3\n",
            "actual_output": "1 3\n",
            "message": "实际输出与预期输出不一致。",
        },
    ],
}

PARTIAL_VERIFICATION = {
    **VERIFICATION,
    "overall_status": "部分通过",
    "message": "共执行2组测试，通过1组。",
    "passed": 1,
    "total": 2,
    "score": 50,
    "max_score": 100,
    "cases": [
        {**VERIFICATION["cases"][0], "weight": 50, "score": 50},
        {
            **VERIFICATION["cases"][0],
            "name": "教师隐藏边界",
            "status": "未通过",
            "weight": 50,
            "score": 0,
            "verdict_code": "WA",
            "input": "SECRET_HIDDEN_INPUT",
            "expected_output": "SECRET_EXPECTED_OUTPUT",
            "actual_output": "SECRET_ACTUAL_OUTPUT",
        },
    ],
}

ENVIRONMENT_UNAVAILABLE = {
    "enabled": True,
    "overall_status": "环境不可用",
    "message": "Docker服务暂时不可用。",
    "compiler": "gcc:13-bookworm / g++",
    "compile": {"status": "未执行", "message": "无法连接Docker服务。"},
    "passed": 0,
    "total": 0,
    "score": 0,
    "max_score": 0,
    "cases": [],
}


class AppFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_folder = tempfile.TemporaryDirectory()
        self.original_path = db.DB_PATH
        db.DB_PATH = Path(self.temp_folder.name) / "wuma_ai.db"
        db.init_db()
        self.submission_id = db.create_submission_with_questions(
            "20260006",
            "界面测试同学",
            "求数组中的最大值",
            "使用首元素初始化，再遍历比较。",
            "int main() { return 0; }",
            "2026-09-09 22:30",
            QUESTIONS,
        )
        db.save_report(
            self.submission_id,
            {
                "overall": 68,
                "level": "需要巩固",
                "weakest": "边界情况意识",
                "summary": "基本逻辑正确，边界分析不足。",
                "suggestion": "补充数组越界练习。",
                "dimensions": {
                    "程序逻辑理解": 80,
                    "关键概念掌握": 70,
                    "边界情况意识": 55,
                    "分析与修改能力": 65,
                },
                "review_required": True,
                "review_reasons": ["综合理解度低于70"],
            },
            "2026-09-09 22:35",
        )
        self.feedback_id = db.save_student_feedback(
            self.submission_id,
            "希望获得学习指导",
            "希望老师推荐一些数组边界练习。",
            True,
            "2026-09-09 22:40",
        )

    def tearDown(self):
        db.DB_PATH = self.original_path
        self.temp_folder.cleanup()

    def _start_app(
        self,
        app_path=None,
        role="学生",
        student_id="20260001",
        name="张同学",
    ):
        app_path = app_path or Path(__file__).resolve().parents[1] / "app.py"
        app = AppTest.from_file(str(app_path))
        app.session_state["auth_role"] = role
        app.session_state["auth_name"] = name if role == "学生" else "任课教师"
        app.session_state["auth_student_id"] = student_id if role == "学生" else ""
        app.session_state["form_name"] = name
        app.session_state["form_student_id"] = student_id
        app.session_state["current_page"] = "首页"
        authenticate(app, role, student_id, name)
        return app.run(timeout=20)

    def _open_teacher_dashboard(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        app = self._start_app(app_path, role="教师")
        teacher_button = next(
            button for button in app.button if button.label.startswith("教师工作台")
        )
        teacher_button.click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        return app

    def _open_task_center(self, role="学生", student_id="20260006"):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        app = self._start_app(
            app_path,
            role=role,
            student_id=student_id,
            name="界面测试同学",
        )
        task_button = next(
            button
            for button in app.button
            if button.label.startswith("任务中心")
        )
        task_button.click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        return app

    def test_teacher_workspace_starts_with_compact_review_flow(self):
        app = self._open_teacher_dashboard()
        self.assertTrue(any(item.value == "教师工作台" for item in app.title))
        workspace_view = next(
            item for item in app.radio if item.label == "工作区域"
        )
        self.assertEqual(workspace_view.value, "待办处理")
        self.assertTrue(
            any(item.value == "学生状态总览" for item in app.subheader)
        )
        self.assertTrue(
            any(item.value == "教师诊断摘要" for item in app.subheader)
        )
        self.assertTrue(
            any(item.label == "选择要处理的提交" for item in app.selectbox)
        )
        self.assertTrue(
            any(
                item.label == "查看学生代码、解题思路与实验报告"
                for item in app.expander
            )
        )
        self.assertEqual(len(app.exception), 0)

    def test_teacher_dashboard_shows_hidden_case_pass_rate(self):
        verification = {
            **VERIFICATION,
            "score": 40,
            "max_score": 100,
            "passed": 1,
            "total": 2,
            "cases": [
                {
                    **VERIFICATION["cases"][0],
                    "weight": 40,
                    "score": 40,
                },
                {
                    **VERIFICATION["cases"][0],
                    "name": "全部负数",
                    "group": "边界情况",
                    "weight": 60,
                    "score": 0,
                    "status": "未通过",
                    "verdict_code": "WA",
                },
            ],
        }
        with db.get_connection() as connection:
            connection.execute(
                "UPDATE submissions SET code_verification_json = ? WHERE id = ?",
                (json.dumps(verification, ensure_ascii=False), self.submission_id),
            )
        app = self._open_teacher_dashboard()
        next(
            item for item in app.radio if item.label == "工作区域"
        ).set_value("班级分析").run(timeout=20)
        self.assertTrue(
            any(item.value == "代码测试点通过率" for item in app.subheader)
        )
        self.assertTrue(
            any("全部负数" in item.value for item in app.markdown)
        )
        self.assertEqual(len(app.exception), 0)

    def test_both_teacher_views_fold_originals_and_give_evidence_full_width(self):
        long_answer = "这是一段需要按需展开的学生回答。" * 100
        long_evaluation = "完整AI评价不能直接堆在页面上。" * 100
        with db.get_connection() as connection:
            connection.execute(
                "UPDATE qa_records SET answer = ?, initial_evaluation_json = ? WHERE submission_id = ?",
                (long_answer, json.dumps({"score": 65, "confidence": "中", "feedback": long_evaluation}), self.submission_id),
            )
        app = self._open_teacher_dashboard()
        for view in ["待办处理", "班级分析"]:
            with self.subTest(view=view):
                next(item for item in app.radio if item.label == "工作区域").set_value(view).run(timeout=20)
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(sum(item.label == "保存教师复核" for item in app.button), 1)
                self.assertTrue(any(item.value == "AI对本次答辩的判断与作答证据" for item in app.subheader))
                self.assertFalse(any(any(item.value == "AI对本次答辩的判断与作答证据" for item in col.subheader)
                                     for col in app.get("column")))
                self.assertTrue(any(item.label == "查看代码测试结果与运行证据" for item in app.expander))
                self.assertTrue(any(item.label == "查看评分点初评依据" for item in app.expander))
                self.assertFalse(any(any(item.value in {"完成教师复核", "教师诊断摘要"}
                                         for item in col.subheader)
                                     for col in app.get("column")))
                heads = [item.value for item in app.subheader]
                self.assertLess(heads.index("完成教师复核"), heads.index("其他原始材料（按需展开）"))
                for expander in app.expander:
                    self.assertFalse(expander.proto.expanded, expander.label)
                paths = []
                def walk(node, ancestors):
                    if hasattr(node, "children"):
                        for child in node.children.values():
                            walk(child, ancestors + [node.type])
                    elif node.type in {"markdown", "code"}:
                        value = str(node.value)
                        if long_answer in value or long_evaluation in value or node.type == "code":
                            paths.append(ancestors)
                walk(app.main, [])
                self.assertGreaterEqual(len(paths), 3)
                self.assertTrue(all("expander" in path for path in paths))

    def test_class_analysis_review_saves_and_is_shared_with_workbench(self):
        app = self._open_teacher_dashboard()
        next(item for item in app.radio if item.label == "工作区域").set_value("班级分析").run(timeout=20)
        next(item for item in app.selectbox if item.label == "处理结论").set_value("调整理解度结论").run(timeout=20)
        next(item for item in app.number_input if item.label.startswith("教师确认理解度")).set_value(76)
        next(item for item in app.text_area if item.label == "教师复核意见（选填）").set_value("已核查逐题证据，调整为76分。")
        next(item for item in app.button if item.label == "保存教师复核").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(db.get_teacher_review(self.submission_id)["confirmed_overall"], 76)
        next(item for item in app.radio if item.label == "工作区域").set_value("待办处理").run(timeout=20)
        self.assertTrue(any("教师确认理解度：76/100" in item.value for item in app.success))
        self.assertEqual(len(db.list_teacher_review_history(self.submission_id)), 1)

    def test_teacher_review_can_be_saved_without_comment(self):
        app = self._open_teacher_dashboard()
        comment = next(
            item for item in app.text_area
            if item.label == "教师复核意见（选填）"
        )
        self.assertEqual(comment.value, "")
        next(
            item for item in app.button
            if item.label == "保存教师复核"
        ).click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(db.get_teacher_review(self.submission_id)["comment"], "")

    def test_class_feedback_jump_can_locate_a_student_outside_current_filter(self):
        second_id = db.create_submission_with_questions(
            "20260007", "另一位同学", "求数组中的最大值", "遍历比较", "int main(){}",
            "2026-09-15 10:00", QUESTIONS,
        )
        db.save_report(second_id, db.get_report(self.submission_id), "2026-09-15 10:01")
        db.save_teacher_review(second_id, "任课教师", "认可AI诊断", 68, "已复核。", "2026-09-15 10:02")
        second_feedback = db.save_student_feedback(second_id, "希望获得学习指导", "请帮忙看思路。", True, "2026-09-15 10:01")
        app = self._open_teacher_dashboard()
        next(item for item in app.radio if item.label == "工作区域").set_value("班级分析").run(timeout=20)
        next(item for item in app.selectbox if item.label == "筛选学生状态").set_value("待教师复核").run(timeout=20)
        jump = next(item for item in app.selectbox if item.label == "从反馈队列快速定位")
        jump.set_value(next(label for label in jump.options if label.startswith(f"反馈#{second_feedback} ·"))).run(timeout=20)
        next(item for item in app.button if item.label == "定位并处理反馈").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("当前学生 · 另一位同学" in item.value for item in app.subheader))
        focused = next(item for item in app.expander if item.label.startswith(f"反馈#{second_feedback} ·"))
        self.assertTrue(focused.proto.expanded)
        self.assertIsNone(app.session_state["teacher_task_submission_id"])
        next(item for item in app.text_area if item.label.startswith("教师回复")).set_value("请先说明循环不变量。")
        next(item for item in app.button if item.label == "保存反馈处理结果").click().run(timeout=20)
        self.assertEqual(db.list_student_feedbacks(submission_id=second_id)[0]["teacher_reply"], "请先说明循环不变量。")
        self.assertEqual(len(app.exception), 0)

    def test_student_selector_precedes_table_and_controls_highlight_in_both_views(self):
        second_id = db.create_submission_with_questions(
            "20260008", "新同学", "求数组中的最大值", "比较数组元素", "int main(){}",
            "2026-09-15 12:00", QUESTIONS,
        )
        app = self._open_teacher_dashboard()
        selector = next(item for item in app.selectbox if item.label == "选择要处理的提交")
        selector.set_value(next(label for label in selector.options if label.startswith(f"提交编号：{second_id} ·"))).run(timeout=20)
        for view, expected_rows in [("待办处理", 1), ("班级分析", 2)]:
            next(item for item in app.radio if item.label == "工作区域").set_value(view).run(timeout=20)
            elements = list(app.main.children.values())
            select_index = next(i for i, item in enumerate(elements)
                                if item.type == "selectbox" and item.label == "选择要处理的提交")
            table_index = next(i for i, item in enumerate(elements)
                               if item.type == "markdown" and "<th>提交信息</th>" in item.value)
            self.assertLess(select_index, table_index)
            table = elements[table_index].value
            body = re.search(r"<tbody>(.*?)</tbody>", table, re.S).group(1)
            self.assertEqual(len(re.findall(r"<tr\b", body)), expected_rows)
            highlighted = re.findall(r'<tr class="wuma-selected-row"[^>]*>(.*?)</tr>', body, re.S)
            self.assertEqual(len(highlighted), 1)
            self.assertIn(f"提交编号：{second_id}", highlighted[0])
            self.assertTrue(any("当前学生 · 新同学" in item.value for item in app.subheader))
            self.assertEqual(len(app.exception), 0)
        next(item for item in app.selectbox if item.label == "筛选学生状态").set_value("有学生反馈").run(timeout=20)
        self.assertEqual(app.session_state["teacher_selected_submission_id"], self.submission_id)
        self.assertTrue(any("原选中记录已不在" in item.value for item in app.info))
        next(item for item in app.selectbox if item.label == "筛选学生状态").set_value("评测需关注").run(timeout=20)
        self.assertFalse(any(item.label == "选择要处理的提交" for item in app.selectbox))
        self.assertTrue(any("没有学生记录" in item.value for item in app.info))
        self.assertEqual(len(app.exception), 0)

    def test_class_averages_use_latest_completed_attempt_but_keep_pending_history(self):
        report = db.get_report(self.submission_id)
        for student, score in [("20260006", 90), ("20260007", 30)]:
            submission_id = db.create_submission_with_questions(
                student, "同名学生", "求数组中的最大值", "遍历比较", "int main(){}",
                "2026-09-15 12:00", QUESTIONS,
            )
            db.save_report(submission_id, {**report, "overall": score,
                           "dimensions": {name: score for name in report["dimensions"]}}, "2026-09-15 12:01")
        db.create_submission_with_questions(
            "20260006", "同名学生", "求数组中的最大值", "仍在答辩", "int main(){}",
            "2026-09-15 12:02", QUESTIONS,
        )
        app = self._open_teacher_dashboard()
        next(item for item in app.radio if item.label == "工作区域").set_value("班级分析").run(timeout=20)
        metrics = {item.label: item.value for item in app.metric}
        self.assertEqual(metrics["历史提交总数"], "4")
        self.assertEqual(metrics["纳入统计的已完成答辩"], "2")
        self.assertEqual(metrics["平均AI答辩理解度"], "60")
        self.assertEqual(metrics["待教师复核提交数"], "3")
        self.assertTrue(any("重答未完成时沿用上一次" in item.value for item in app.caption))
        self.assertEqual(len(db.list_submissions()), 4)
        self.assertEqual(len(db.list_learning_records()), 3)
        self.assertEqual(len(app.exception), 0)

    def test_class_without_completed_defense_displays_no_average_instead_of_zero(self):
        with db.get_connection() as connection:
            connection.execute("DELETE FROM reports")
            connection.execute("UPDATE submissions SET status = '答辩中'")
        app = self._open_teacher_dashboard()
        next(item for item in app.radio if item.label == "工作区域").set_value("班级分析").run(timeout=20)
        metrics = {item.label: item.value for item in app.metric}
        self.assertEqual(metrics["纳入统计的已完成答辩"], "0")
        self.assertEqual(metrics["平均AI答辩理解度"], "暂无")
        self.assertEqual(len(app.exception), 0)

    def test_teacher_summary_and_class_charts_show_values_without_hover(self):
        db.save_teacher_review(self.submission_id, "任课教师", "认可AI诊断", 68, "已核对证据。", "2026-09-16 10:00")
        with db.get_connection() as connection:
            connection.execute("UPDATE submissions SET code_verification_json = ? WHERE id = ?",
                               (json.dumps(VERIFICATION, ensure_ascii=False), self.submission_id))
        app = self._open_teacher_dashboard()
        card = next(item.value for item in app.markdown if 'class="wuma-summary-grid"' in item.value)
        self.assertIn("认可AI诊断</div>", card)
        self.assertIn("确认理解度 68/100</div>", card)
        next(item for item in app.radio if item.label == "工作区域").set_value("班级分析").run(timeout=20)
        panels = [item.value for item in app.markdown if 'class="wuma-bar-panel"' in item.value]
        self.assertEqual(len(panels), 3)
        self.assertIn("80 / 100", panels[0])
        self.assertIn("1人次 · 100.0%", panels[1])
        self.assertIn("通过 1 / 1 次 · 未通过 0 次", panels[2])
        self.assertTrue(any("仅有1—2次判定" in item.value for item in app.caption))
        self.assertEqual(len(app.exception), 0)

    def test_accepting_ai_diagnosis_updates_score_widget(self):
        app = self._open_teacher_dashboard()
        decision = next(
            item for item in app.selectbox if item.label == "处理结论"
        )
        score = next(
            item
            for item in app.number_input
            if item.label.startswith("教师确认理解度")
        )
        decision.set_value("调整理解度结论").run(timeout=20)
        score.set_value(75).run(timeout=20)
        decision.set_value("认可AI诊断").run(timeout=20)

        refreshed_score = next(
            item
            for item in app.number_input
            if item.label.startswith("教师确认理解度")
        )
        self.assertEqual(refreshed_score.value, 68)
        self.assertTrue(refreshed_score.disabled)
        self.assertEqual(len(app.exception), 0)

    def test_student_submission_saves_lab_report_and_preliminary_review(self):
        preliminary = {
            "completion_score": 78,
            "summary": "主体功能已完成，边界情况需要答辩核验。",
            "criteria": [],
        }
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        with (
            patch.object(llm, "is_configured", return_value=True),
            patch.object(
                verifier,
                "verify_cpp_code",
                return_value=VERIFICATION,
            ),
            patch.object(llm, "preliminary_review", return_value=preliminary),
            patch.object(llm, "generate_questions", return_value=QUESTIONS),
        ):
            app = self._start_app(app_path)
            submission_page = next(
                button for button in app.button if button.label == "实验提交"
            )
            submission_page.click().run(timeout=20)
            explanation = next(
                item for item in app.text_area if item.label.startswith("解题思路")
            )
            lab_report = next(
                item for item in app.text_area if item.label == "实验报告（选做）"
            )
            explanation.set_value("使用首元素初始化最大值，再循环比较更新。")
            lab_report.set_value(
                "实验目的为求最大值；测试了正数、负数和单元素输入，"
                "并分析了空数组与容量边界。"
            )
            judge = next(
                button
                for button in app.button
                if button.label == "正式评测代码"
            )
            judge.click().run(timeout=20)
            submit = next(
                button for button in app.button if button.label == "进入AI答辩"
            )
            submit.click().run(timeout=20)

        newest = db.list_submissions()[0]
        saved = db.get_submission(newest["id"])
        saved_review = db.get_preliminary_review(newest["id"])
        self.assertIn("容量边界", saved["lab_report"])
        self.assertEqual(saved_review["completion_score"], 78)
        self.assertEqual(
            saved["code_verification"]["overall_status"],
            "全部通过",
        )
        self.assertIsNone(student_drafts.load_draft(db.DB_PATH, "20260001", saved["assignment_id"]))
        self.assertTrue(any("问题1" in item.value for item in app.subheader))
        self.assertEqual(len(app.exception), 0)

    def test_partial_result_is_shown_and_does_not_enter_defense(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        with (
            patch.object(llm, "is_configured", return_value=True),
            patch.object(
                verifier,
                "verify_cpp_code",
                return_value=PARTIAL_VERIFICATION,
            ),
        ):
            app = self._start_app(app_path)
            next(
                button for button in app.button if button.label == "实验提交"
            ).click().run(timeout=20)
            next(
                item for item in app.text_area if item.label.startswith("解题思路")
            ).set_value("使用循环处理数组。")
            next(
                button for button in app.button if button.label == "正式评测代码"
            ).click().run(timeout=20)

        self.assertTrue(any("部分正确" in item.value for item in app.warning))
        self.assertFalse(any(button.label == "进入AI答辩" for button in app.button))
        self.assertTrue(any("获取AI提示" in button.label for button in app.button))
        self.assertEqual(len(db.list_submissions()), 1)
        visible_text = "\n".join(
            str(item.value)
            for collection in (app.markdown, app.info, app.warning, app.error, app.caption)
            for item in collection
        )
        self.assertNotIn("SECRET_HIDDEN_INPUT", visible_text)
        self.assertNotIn("SECRET_EXPECTED_OUTPUT", visible_text)
        self.assertNotIn("SECRET_ACTUAL_OUTPUT", visible_text)
        self.assertEqual(len(app.exception), 0)

    def test_environment_failure_is_not_a_student_error_or_defense_entry(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        with (
            patch.object(llm, "is_configured", return_value=True),
            patch.object(
                verifier,
                "verify_cpp_code",
                return_value=ENVIRONMENT_UNAVAILABLE,
            ),
        ):
            app = self._start_app(app_path)
            next(
                button for button in app.button if button.label == "实验提交"
            ).click().run(timeout=20)
            next(
                item for item in app.text_area if item.label.startswith("解题思路")
            ).set_value("使用循环处理数组。")
            next(
                button for button in app.button if button.label == "正式评测代码"
            ).click().run(timeout=20)

        self.assertTrue(any("判题未完成" in item.value for item in app.warning))
        self.assertTrue(any("不会记为学生代码错误" in item.value for item in app.caption))
        self.assertFalse(any(button.label == "进入AI答辩" for button in app.button))
        self.assertEqual(len(db.list_submissions()), 1)
        self.assertEqual(len(app.exception), 0)

    def test_ai_hint_is_limited_and_saved_by_student_and_assignment(self):
        hint = {
            "diagnosis": "先检查循环边界。",
            "guidance": "比较循环实际覆盖的下标范围。",
            "self_check": ["第一个下标是什么？", "最后一个下标是什么？"],
        }
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        with db.get_connection() as connection:
            connection.execute("UPDATE assignments SET hint_limit = 1")
        with (
            patch.object(llm, "is_configured", return_value=True),
            patch.object(
                verifier,
                "verify_cpp_code",
                return_value=PARTIAL_VERIFICATION,
            ),
            patch.object(llm, "generate_learning_hint", return_value=hint),
        ):
            app = self._start_app(app_path)
            next(
                button for button in app.button if button.label == "实验提交"
            ).click().run(timeout=20)
            next(
                item for item in app.text_area if item.label.startswith("解题思路")
            ).set_value("使用循环处理数组。")
            next(
                button for button in app.button if button.label == "正式评测代码"
            ).click().run(timeout=20)
            next(
                button for button in app.button if "获取AI提示" in button.label
            ).click().run(timeout=20)

        assignment = db.list_assignments()[0]
        records = db.list_ai_hints(assignment["id"], "20260001")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["hint"]["diagnosis"], "先检查循环边界。")
        self.assertTrue(any("当前代码" in item.label for item in app.expander))
        next(
            item for item in app.text_area if item.label == "C++代码"
        ).set_value("int main(){return 0;}").run(timeout=20)
        self.assertTrue(
            any("历史代码·已失效" in item.label for item in app.expander)
        )
        self.assertFalse(any("获取AI提示" in button.label for button in app.button))
        self.assertTrue(any("已经全部使用" in item.value for item in app.warning))
        self.assertEqual(len(app.exception), 0)
        teacher_app = self._open_teacher_dashboard()
        next(
            item for item in teacher_app.radio if item.label == "工作区域"
        ).set_value("班级分析").run(timeout=20)
        self.assertTrue(
            any(item.value == "AI提示使用记录" for item in teacher_app.subheader)
        )
        self.assertTrue(
            any("先检查循环边界" in item.value for item in teacher_app.markdown)
        )
        tables = [item.value for item in teacher_app.markdown if "wuma-data-table" in item.value]
        self.assertFalse(any(hint["guidance"] in table for table in tables))
        hint_detail = next(item for item in teacher_app.expander if item.label.startswith("提示详情"))
        self.assertFalse(hint_detail.proto.expanded)
        self.assertTrue(any(hint["guidance"] in item.value for item in hint_detail.markdown))

    def test_student_sees_oj_statement_and_can_run_custom_input(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        available = {
            "available": True,
            "label": "本机代码验证可用",
            "detail": "测试编译器",
            "compiler": "g++",
        }
        with (
            patch.object(verifier, "runtime_status", return_value=available),
            patch.object(verifier, "run_cpp_code", return_value=CUSTOM_RUN),
        ):
            app = self._start_app(app_path)
            next(
                button for button in app.button if button.label == "实验提交"
            ).click().run(timeout=20)
            self.assertTrue(
                any("输入格式" in item.value for item in app.markdown)
            )
            custom_input = next(
                item for item in app.text_area if item.label == "自定义测试输入"
            )
            custom_input.set_value("5\n3 8 2 6 1\n")
            next(
                button
                for button in app.button
                if button.label == "运行代码并查看输出"
            ).click().run(timeout=20)
        self.assertTrue(any("程序运行完成" in item.value for item in app.info))
        self.assertEqual(len(app.exception), 0)

    def test_empty_output_is_shown_as_wrong_answer_when_output_is_required(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        available = {
            "available": True,
            "label": "本机代码验证可用",
            "detail": "测试编译器",
            "compiler": "g++",
        }
        with (
            patch.object(verifier, "runtime_status", return_value=available),
            patch.object(verifier, "run_cpp_code", return_value=CUSTOM_RUN_EMPTY),
        ):
            app = self._start_app(app_path)
            next(
                button for button in app.button if button.label == "实验提交"
            ).click().run(timeout=20)
            next(
                button
                for button in app.button
                if button.label == "运行代码并查看输出"
            ).click().run(timeout=20)

        self.assertTrue(any("答案错误" in item.value for item in app.error))
        self.assertFalse(any("程序运行成功" in item.value for item in app.success))
        self.assertEqual(len(app.exception), 0)

    def test_public_sample_can_be_judged_with_one_click(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        available = {
            "available": True,
            "label": "本机代码验证可用",
            "detail": "测试编译器",
            "compiler": "g++",
        }
        public_pass = {
            **VERIFICATION,
            "passed": 1,
            "total": 1,
        }
        with (
            patch.object(verifier, "runtime_status", return_value=available),
            patch.object(
                verifier,
                "verify_cpp_code",
                return_value=public_pass,
            ) as verify,
        ):
            app = self._start_app(app_path)
            next(
                button for button in app.button if button.label == "实验提交"
            ).click().run(timeout=20)
            next(
                button for button in app.button if button.label == "运行样例1"
            ).click().run(timeout=20)

        self.assertTrue(any("答案正确" in item.value for item in app.success))
        sample_case = verify.call_args.args[1][0]
        self.assertEqual(sample_case["input"], "5\n3 8 2 6 1\n")
        self.assertEqual(sample_case["expected_output"], "8\n")
        self.assertEqual(len(app.exception), 0)

    def test_wrong_public_sample_shows_expected_and_actual_outputs(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        available = {
            "available": True,
            "label": "本机代码验证可用",
            "detail": "测试编译器",
            "compiler": "g++",
        }
        with (
            patch.object(verifier, "runtime_status", return_value=available),
            patch.object(
                verifier,
                "verify_cpp_code",
                return_value=PUBLIC_SAMPLE_WRONG,
            ),
        ):
            app = self._start_app(app_path)
            next(
                button for button in app.button if button.label == "实验提交"
            ).click().run(timeout=20)
            next(
                button for button in app.button if button.label == "运行样例1"
            ).click().run(timeout=20)

        self.assertTrue(any("答案错误" in item.value for item in app.error))
        captions = [item.value for item in app.caption]
        self.assertIn("预期输出", captions)
        self.assertIn("实际输出", captions)
        self.assertEqual(len(app.exception), 0)

    def test_all_public_samples_show_summary_and_first_difference(self):
        assignment = db.list_assignments()[0]
        public_samples = [
            assignment["public_samples"][0],
            {
                "name": "连续空格",
                "input": "3\n1 2 3\n",
                "output": "1   3\n",
                "explanation": "用于检查连续空格。",
            },
        ]
        with db.get_connection() as connection:
            connection.execute(
                "UPDATE assignments SET public_samples_json = ? WHERE id = ?",
                (json.dumps(public_samples, ensure_ascii=False), assignment["id"]),
            )
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        available = {
            "available": True,
            "label": "本机代码验证可用",
            "detail": "测试编译器",
            "compiler": "g++",
        }
        with (
            patch.object(verifier, "runtime_status", return_value=available),
            patch.object(
                verifier,
                "verify_cpp_code",
                return_value=PUBLIC_SAMPLES_MIXED,
            ) as verify,
        ):
            app = self._start_app(app_path)
            next(
                button for button in app.button if button.label == "实验提交"
            ).click().run(timeout=20)
            next(
                button for button in app.button if button.label == "运行全部公开样例"
            ).click().run(timeout=20)

        self.assertEqual(len(verify.call_args.args[1]), 2)
        self.assertTrue(any("1/2" in item.value for item in app.error))
        self.assertTrue(any("缺少空格" in item.value for item in app.warning))
        self.assertTrue(any("2个空格" in item.value for item in app.warning))
        self.assertEqual(len(app.exception), 0)

    def test_public_sample_result_is_marked_stale_after_code_changes(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        available = {
            "available": True,
            "label": "本机代码验证可用",
            "detail": "测试编译器",
            "compiler": "g++",
        }
        with (
            patch.object(verifier, "runtime_status", return_value=available),
            patch.object(verifier, "verify_cpp_code", return_value=VERIFICATION),
        ):
            app = self._start_app(app_path)
            next(
                button for button in app.button if button.label == "实验提交"
            ).click().run(timeout=20)
            next(
                button for button in app.button if button.label == "运行样例1"
            ).click().run(timeout=20)
            next(
                item for item in app.text_area if item.label == "C++代码"
            ).set_value("int main(){return 0;}").run(timeout=20)

        self.assertTrue(
            any("判题结果已失效" in item.value for item in app.info)
        )
        self.assertEqual(len(app.exception), 0)

    def test_short_input_survives_ai_failure_and_can_retry(self):
        preliminary = {
            "completion_score": 60,
            "summary": "输入较短，后续通过答辩继续核验。",
            "criteria": [],
        }
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        with (
            patch.object(llm, "is_configured", return_value=True),
            patch.object(verifier, "verify_cpp_code", return_value=VERIFICATION),
            patch.object(
                llm,
                "preliminary_review",
                side_effect=[llm.LLMServiceError("临时连接失败"), preliminary],
            ),
            patch.object(llm, "generate_questions", return_value=QUESTIONS),
        ):
            app = self._start_app(app_path)
            next(
                button for button in app.button if button.label == "实验提交"
            ).click().run(timeout=20)
            next(
                item for item in app.text_area if item.label.startswith("解题思路")
            ).set_value("想")
            judge = next(
                button
                for button in app.button
                if button.label == "正式评测代码"
            )
            judge.click().run(timeout=20)
            submit = next(
                button for button in app.button if button.label == "进入AI答辩"
            )
            submit.click().run(timeout=20)
            self.assertTrue(any("临时连接失败" in item.value for item in app.error))
            assignment = db.list_assignments()[0]
            self.assertEqual(student_drafts.load_draft(db.DB_PATH, "20260001", assignment["id"])["data"]["explanation"], "想")

            retry = next(
                button
                for button in app.button
                if button.label == "进入AI答辩"
            )
            retry.click().run(timeout=20)

        newest = db.list_submissions()[0]
        saved = db.get_submission(newest["id"])
        self.assertEqual(saved["explanation"], "想")
        self.assertEqual(saved["lab_report"], "")
        self.assertTrue(any("问题1" in item.value for item in app.subheader))
        self.assertEqual(len(app.exception), 0)

    def test_teacher_reply_reaches_student_report(self):
        app = self._open_teacher_dashboard()
        reply_box = next(
            item
            for item in app.text_area
            if item.label.startswith("教师回复")
        )
        reply_box.set_value("请完成课程平台中的三道数组边界练习。")
        save_button = next(
            button
            for button in app.button
            if button.label == "保存反馈处理结果"
        )
        save_button.click().run(timeout=20)

        saved = db.list_student_feedbacks(submission_id=self.submission_id)[0]
        self.assertEqual(saved["status"], "已回复")
        self.assertIn("三道数组", saved["teacher_reply"])

        app = self._start_app(
            student_id="20260006",
            name="界面测试同学",
        )
        next(
            button for button in app.button if button.label == "学生报告"
        ).click().run(timeout=20)
        self.assertTrue(
            any("三道数组边界练习" in item.value for item in app.success)
        )
        self.assertEqual(len(app.exception), 0)

    def test_student_can_submit_feedback_after_defense(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        app = self._start_app(
            app_path,
            student_id="20260006",
            name="界面测试同学",
        )
        report_button = next(
            button for button in app.button if button.label == "学生报告"
        )
        report_button.click().run(timeout=20)
        feedback_box = next(
            item for item in app.text_area if item.label == "反馈内容"
        )
        feedback_box.set_value("我想补充说明第二题中考虑过数组越界。")
        submit_button = next(
            button for button in app.button if button.label == "提交反馈给教师"
        )
        submit_button.click().run(timeout=20)

        feedbacks = db.list_student_feedbacks(submission_id=self.submission_id)
        self.assertEqual(len(feedbacks), 2)
        self.assertTrue(
            any("补充说明第二题" in item["content"] for item in feedbacks)
        )
        self.assertEqual(len(app.exception), 0)

    def test_archive_loads_report_only_when_expanded_and_can_collapse(self):
        app = self._start_app(student_id="20260006", name="界面测试同学")
        with (
            patch.object(db, "get_report", wraps=db.get_report) as report_reader,
            patch.object(db, "get_qa_records", wraps=db.get_qa_records) as question_reader,
        ):
            next(button for button in app.button if button.label == "学习档案").click().run(timeout=20)
            report_reader.assert_not_called()
            question_reader.assert_not_called()
            self.assertFalse(any(item.value == "诊断证据" for item in app.subheader))
            next(item for item in app.toggle if item.key == "archive_report_expanded").set_value(True).run(timeout=20)
            report_reader.assert_called_once_with(self.submission_id)
            question_reader.assert_called_once_with(self.submission_id)
            self.assertTrue(any(item.value == "诊断证据" for item in app.subheader))
            next(item for item in app.toggle if item.key == "archive_report_expanded").set_value(False).run(timeout=20)
            self.assertFalse(any(item.value == "诊断证据" for item in app.subheader))
            self.assertEqual(report_reader.call_count, 1)
        self.assertEqual(len(app.exception), 0)

    def test_archive_distinguishes_record_sequence_and_resets_details_on_selection(self):
        another_id = db.create_submission_with_questions(
            "20260006", "界面测试同学", "另一次独立提交",
            "使用数组首元素初始化。", "int main() { return 0; }",
            "2026-09-10 09:00", QUESTIONS,
        )
        db.save_report(
            another_id, {**db.get_report(self.submission_id), "overall": 81},
            "2026-09-10 09:05",
        )
        app = self._start_app(student_id="20260006", name="界面测试同学")
        next(button for button in app.button if button.label == "学习档案").click().run(timeout=20)
        self.assertEqual(next(item for item in app.metric if item.label == "累计答辩记录").value, "2")
        selector = next(item for item in app.selectbox if item.key == "archive_report_select")
        options = selector.options
        self.assertTrue(options[0].startswith(f"记录2 · 提交#{another_id}"))
        self.assertTrue(options[1].startswith(f"记录1 · 提交#{self.submission_id}"))
        self.assertTrue(all("第1轮" in label for label in options))
        table = next(item.value for item in app.markdown if "<th>记录序号</th>" in item.value)
        self.assertIn("<th>答辩轮次</th>", table)
        self.assertEqual(table.count("第1轮"), 2)
        next(item for item in app.toggle if item.key == "archive_report_expanded").set_value(True).run(timeout=20)
        next(item for item in app.selectbox if item.key == "archive_report_select").set_value(options[1]).run(timeout=20)
        self.assertFalse(app.session_state["archive_report_expanded"])
        self.assertFalse(any(item.value == "诊断证据" for item in app.subheader))
        next(item for item in app.toggle if item.key == "archive_report_expanded").set_value(True).run(timeout=20)
        self.assertTrue(any(item.value == "诊断证据" for item in app.subheader))
        self.assertEqual(len(app.exception), 0)

    def test_student_can_start_teacher_requested_redefense(self):
        db.save_teacher_review(
            self.submission_id,
            "王老师",
            "要求学生重新答辩",
            68,
            "请重点重新说明数组越界及空输入情况。",
            "2026-09-09 22:45",
        )
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        with (
            patch.object(llm, "is_configured", return_value=True),
            patch.object(llm, "generate_questions", return_value=QUESTIONS),
        ):
            app = self._start_app(
                app_path,
                student_id="20260006",
                name="界面测试同学",
            )
            report_button = next(
                button for button in app.button if button.label == "学生报告"
            )
            report_button.click().run(timeout=20)
            start_button = next(
                button
                for button in app.button
                if button.label == "开始教师要求的重新答辩"
            )
            start_button.click().run(timeout=20)

        request = db.get_latest_redefense_request(self.submission_id)
        new_submission = db.get_submission(request["new_submission_id"])
        self.assertEqual(request["status"], "答辩中")
        self.assertEqual(new_submission["parent_submission_id"], self.submission_id)
        self.assertEqual(new_submission["attempt_number"], 2)
        self.assertTrue(any("问题1" in item.value for item in app.subheader))
        self.assertEqual(len(app.exception), 0)

    def test_teacher_task_jumps_to_matching_submission(self):
        app = self._open_teacher_dashboard()
        review_button = next(
            button for button in app.button if button.label == "开始复核"
        )
        review_button.click().run(timeout=20)
        self.assertTrue(
            any(item.value == "完成教师复核" for item in app.subheader)
        )
        self.assertEqual(app.session_state["teacher_active_task"]["submission_id"], self.submission_id)
        self.assertFalse(any(item.label == "筛选学生状态" for item in app.selectbox))
        heads = [item.value for item in app.subheader]
        self.assertLess(heads.index("完成教师复核"), heads.index("教师诊断摘要"))
        self.assertIsNone(app.session_state["teacher_task_submission_id"])
        self.assertIsNone(app.session_state["teacher_task_feedback_id"])
        self.assertIsNone(app.session_state["teacher_task_assignment_id"])
        self.assertEqual(len(app.exception), 0)

    def test_student_reply_task_is_completed_after_opening(self):
        db.process_student_feedback(
            self.feedback_id,
            "任课教师",
            "已回复",
            "请完成三道数组边界练习。",
            "2026-09-09 22:50",
        )
        app = self._open_task_center()
        reply_button = next(
            button
            for button in app.button
            if button.label == "查看教师回复"
        )
        reply_button.click().run(timeout=20)

        feedback = db.list_student_feedbacks(
            submission_id=self.submission_id
        )[0]
        self.assertTrue(feedback["student_viewed_at"])
        self.assertTrue(any("学生理解度报告" in item.value for item in app.title))
        self.assertEqual(len(app.exception), 0)

    def test_teacher_feedback_task_opens_exact_form_before_diagnosis(self):
        another_feedback = db.save_student_feedback(
            self.submission_id, "补充答辩说明", "补充：我考虑了空数组。", False, "2026-09-10 10:00",
        )
        app = self._open_teacher_dashboard()
        next(button for button in app.button if button.key ==
             f"task_action_教师待办_teacher-feedback-{self.feedback_id}").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        heads = [item.value for item in app.subheader]
        self.assertLess(heads.index("处理学生反馈"), heads.index("教师诊断摘要"))
        self.assertFalse(any(item.label == "筛选学生状态" for item in app.selectbox))
        feedback_panels = [item for item in app.expander if item.label.startswith("反馈#")]
        self.assertEqual(len(feedback_panels), 1)
        self.assertTrue(feedback_panels[0].label.startswith(f"反馈#{self.feedback_id} ·"))
        self.assertTrue(feedback_panels[0].proto.expanded)
        self.assertFalse(next(item for item in app.expander
                              if item.label == "查看本次诊断与答辩依据").proto.expanded)
        next(item for item in app.text_area if item.label.startswith("教师回复")).set_value("请练习全负数输入。")
        next(button for button in app.button if button.label == "保存反馈处理结果").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        feedbacks = {item["id"]: item for item in db.list_student_feedbacks(submission_id=self.submission_id)}
        self.assertEqual(feedbacks[self.feedback_id]["teacher_reply"], "请练习全负数输入。")
        self.assertEqual(feedbacks[another_feedback]["status"], "待处理")
        self.assertEqual(app.session_state["teacher_active_task"]["feedback_id"], self.feedback_id)
        self.assertTrue(any("本项待办已处理" in item.value for item in app.success))

    def test_return_from_teacher_task_restores_filter_and_selection_without_saving(self):
        app = self._open_teacher_dashboard()
        next(item for item in app.selectbox if item.label == "筛选学生状态").set_value("有学生反馈").run(timeout=20)
        selected = next(item for item in app.selectbox if item.label == "选择要处理的提交").value
        next(button for button in app.button if button.label == "回复反馈").click().run(timeout=20)
        next(item for item in app.text_area if item.label.startswith("教师回复")).set_value("尚未保存的回复")
        next(button for button in app.button if button.label == "返回待办").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertIsNone(app.session_state["teacher_active_task"])
        self.assertEqual(next(item for item in app.selectbox if item.label == "筛选学生状态").value, "有学生反馈")
        self.assertEqual(next(item for item in app.selectbox if item.label == "选择要处理的提交").value, selected)
        feedback = db.list_student_feedbacks(submission_id=self.submission_id)[0]
        self.assertEqual(feedback["status"], "待处理")
        self.assertEqual(feedback["teacher_reply"], "")

    def test_next_task_preserves_pending_work_and_updates_after_save(self):
        app = self._open_teacher_dashboard()
        next(button for button in app.button if button.label == "回复反馈").click().run(timeout=20)
        next(button for button in app.button if button.label == "处理下一项").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state["teacher_active_task"]["action"], "review_submission")
        self.assertEqual(db.list_student_feedbacks(submission_id=self.submission_id)[0]["status"], "待处理")
        self.assertIsNone(db.get_teacher_review(self.submission_id))
        next(button for button in app.button if button.label == "保存教师复核").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        next(button for button in app.button if button.label == "处理下一项").click().run(timeout=20)
        self.assertEqual(app.session_state["teacher_active_task"]["feedback_id"], self.feedback_id)
        next(item for item in app.text_area if item.label.startswith("教师回复")).set_value("请练习空数组和全负数。")
        next(button for button in app.button if button.label == "保存反馈处理结果").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(db.list_teacher_tasks(), [])
        self.assertTrue(next(button for button in app.button if button.label == "处理下一项").disabled)

    def test_student_reply_task_shows_reply_first_and_can_return_or_open_report(self):
        db.process_student_feedback(self.feedback_id, "任课教师", "已回复", "请练习全负数输入。", "2026-09-10 10:00")
        app = self._open_task_center()
        next(button for button in app.button if button.label == "查看教师回复").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any(item.value == "教师回复" for item in app.subheader))
        self.assertTrue(any("请练习全负数输入。" in item.value for item in app.markdown))
        self.assertFalse(any(item.value == "诊断证据" for item in app.subheader))
        next(button for button in app.button if button.label == "查看完整报告").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any(item.value == "诊断证据" for item in app.subheader))
        self.assertIsNone(app.session_state["student_task_feedback_id"])

    def test_leaving_teacher_task_via_sidebar_clears_focus(self):
        app = self._open_teacher_dashboard()
        next(button for button in app.button if button.label == "开始复核").click().run(timeout=20)
        next(button for button in app.button if button.label.startswith("教师工作台")).click().run(timeout=20)
        self.assertIsNone(app.session_state["teacher_active_task"])
        self.assertTrue(any(item.label == "筛选学生状态" for item in app.selectbox))
        next(button for button in app.button if button.label == "开始复核").click().run(timeout=20)
        next(button for button in app.button if button.label == "首页").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertIsNone(app.session_state["teacher_active_task"])
        next(button for button in app.button if button.label.startswith("教师工作台")).click().run(timeout=20)
        self.assertTrue(any(item.label == "筛选学生状态" for item in app.selectbox))
        self.assertIsNone(db.get_teacher_review(self.submission_id))

    def test_unavailable_execution_environment_is_not_shown_as_student_error(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        unavailable = {
            "available": False,
            "label": "Docker服务未启动",
            "detail": "请启动Docker Desktop后再重试。",
            "compiler": "gcc:13-bookworm / g++",
            "backend_id": "docker_cpp",
        }
        with patch.object(verifier, "runtime_status", return_value=unavailable):
            app = self._start_app(app_path)
            next(
                button for button in app.button if button.label == "实验提交"
            ).click().run(timeout=20)

        warnings = [item.value for item in app.warning]
        self.assertTrue(any("Docker服务未启动" in item for item in warnings))
        self.assertTrue(any("不会记为学生代码错误" in item for item in warnings))
        run_button = next(
            button
            for button in app.button
            if button.label == "运行代码并查看输出"
        )
        self.assertTrue(run_button.disabled)
        self.assertEqual(len(app.exception), 0)

    def test_teacher_can_publish_eight_multiline_hidden_cases(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        app = self._start_app(app_path, role="教师")
        next(
            button for button in app.button if button.label == "实验管理"
        ).click().run(timeout=20)

        next(
            item for item in app.text_input if item.label == "实验名称"
        ).set_value("八组多行测试")
        next(
            item for item in app.text_area if item.label == "实验描述"
        ).set_value("验证八组多行隐藏测试能够稳定发布。")
        next(
            item for item in app.text_area if item.label == "实验要求（每行一条）"
        ).set_value("正确读取多行输入")
        next(
            item
            for item in app.text_area
            if item.label == "隐藏教学重点（每行一条）"
        ).set_value("能解释输入输出格式")
        next(
            item
            for item in app.checkbox
            if item.label == "启用C++编译与隐藏测试样例验证"
            and item.key == "create_assignment_verification"
        ).check()
        for index in range(8):
            next(
                item
                for item in app.text_area
                if item.key == f"create_assignment_test_cases_{index}_input"
            ).set_value(f"3\n{index} {index + 1} {index + 2}\n")
            next(
                item
                for item in app.text_area
                if item.key == f"create_assignment_test_cases_{index}_output"
            ).set_value(f"{index + 2}\n")
        next(
            item
            for item in app.text_area
            if item.key == "create_assignment_reference_code"
        ).set_value("int main() { return 0; }")
        app.run(timeout=20)
        next(
            item
            for item in app.checkbox
            if item.label == "我已逐项核对，确认这些评分点确实适用于当前实验"
            and item.key == "create_assignment_quality_confirmed"
        ).check()
        with patch.object(
            verifier,
            "verify_cpp_code",
            return_value=VERIFICATION,
        ):
            next(
                button for button in app.button if button.label == "发布实验"
            ).click().run(timeout=20)

        saved = next(
            item for item in db.list_assignments() if item["title"] == "八组多行测试"
        )
        self.assertEqual(len(saved["test_cases"]), 8)
        self.assertTrue(all(item.get("count_only") for item in saved["test_cases"]))
        self.assertFalse(any(item.label == "分值（可留空）" for item in app.number_input))
        self.assertEqual(saved["test_cases"][0]["name"], "测试1")
        self.assertEqual(saved["test_cases"][0]["input"], "3\n0 1 2\n")
        self.assertTrue(saved["rubric_reviewed"])
        self.assertTrue(saved["reference_code"])
        self.assertEqual(len(app.exception), 0)

    def _open_experiment_editor(self):
        app = self._start_app(role="教师")
        next(button for button in app.button if button.label == "实验管理").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        return app

    def _selection_draft(self):
        path = Path(__file__).resolve().parents[1] / "test_materials/selection_sort/experiment_config.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_teacher_draft_restores_all_materials_in_a_fresh_session(self):
        config = self._selection_draft()
        assignment_drafts.save_draft(db.DB_PATH, config)
        app = self._open_experiment_editor()
        self.assertEqual(app.text_input(key="create_assignment_title").value, config["title"])
        self.assertEqual(app.text_area(key="create_assignment_test_cases_7_input").value, config["test_cases"][7]["input"])
        self.assertEqual(app.session_state["create_rubric_seed"], config["rubric"])
        self.assertTrue(app.checkbox(key="create_assignment_verification").value)
        app.text_area(key="create_assignment_description").set_value("教师补充的描述\n保留下一行")
        next(button for button in app.button if button.label == "保存草稿").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        restored = self._open_experiment_editor()
        self.assertEqual(restored.text_area(key="create_assignment_description").value, "教师补充的描述\n保留下一行")
        self.assertEqual(restored.text_area(key="create_assignment_reference_code").value, config["reference_code"])
        self.assertEqual(restored.text_area(key="create_assignment_public_samples_1_input").value, config["public_samples"][1]["input"])
        self.assertEqual(len(db.list_assignments()), 1)

    def test_failed_preflight_saves_draft_before_restart_without_publishing(self):
        config = self._selection_draft()
        assignment_drafts.save_draft(db.DB_PATH, config)
        app = self._open_experiment_editor()
        app.text_input(key="create_assignment_title").set_value("教师改过的选择排序")
        with patch.object(verifier, "verify_cpp_code", return_value=ENVIRONMENT_UNAVAILABLE):
            next(button for button in app.button if button.label == "发布实验").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(db.list_assignments()), 1)
        self.assertTrue(any("草稿" in item.value for item in app.info))
        restored = self._open_experiment_editor()
        self.assertEqual(restored.text_input(key="create_assignment_title").value, "教师改过的选择排序")
        self.assertEqual(restored.text_area(key="create_assignment_test_cases_7_output").value, config["test_cases"][7]["expected_output"])

    def test_successful_publish_clears_draft_and_starts_a_blank_form(self):
        config = self._selection_draft()
        assignment_drafts.save_draft(db.DB_PATH, config)
        app = self._open_experiment_editor()
        with patch.object(verifier, "verify_cpp_code", return_value=VERIFICATION):
            next(button for button in app.button if button.label == "发布实验").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(db.list_assignments()), 2)
        self.assertIsNone(load_teacher_draft())
        self.assertEqual(app.text_input(key="create_assignment_title").value, "")
        self.assertEqual(app.text_area(key="create_assignment_reference_code").value, "")
        self.assertEqual(app.text_area(key="create_assignment_test_cases_0_input").value, "")

    def test_incomplete_form_can_be_saved_without_validation_or_model_calls(self):
        app = self._open_experiment_editor()
        existing_errors = [item.value for item in app.error]
        app.text_area(key="create_assignment_description").set_value("还未填写完成")
        with patch.object(verifier, "verify_cpp_code") as verify:
            next(button for button in app.button if button.label == "保存草稿").click().run(timeout=20)
        verify.assert_not_called()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(db.list_assignments()), 1)
        self.assertEqual(load_teacher_draft()["data"]["description"], "还未填写完成")
        self.assertEqual([item.value for item in app.error], existing_errors)


    def _open_student_submission(self, student_id="20260001"):
        app = self._start_app(student_id=student_id)
        next(button for button in app.button if button.label == "实验提交").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        return app

    def test_student_draft_restores_in_fresh_session_and_is_private(self):
        assignment = db.list_assignments()[0]
        app = self._open_student_submission()
        values = {"explanation": "选择最小元素\n保留重复值", "lab_report": "测试记录", "code": "int main(){\n  return 0;\n}", "custom_input": "3\n2  1 3\n"}
        for field, value in values.items():
            app.text_area(key=f"{field}_{assignment['id']}").set_value(value)
        app.run(timeout=20)
        restored = self._open_student_submission()
        for field, value in values.items():
            self.assertEqual(restored.text_area(key=f"{field}_{assignment['id']}").value, value)
        self.assertTrue(any("已恢复系统保存" in item.value for item in restored.info))
        self.assertFalse(any(button.label == "进入AI答辩" for button in restored.button))
        other = self._open_student_submission("20260002")
        self.assertEqual(other.text_area(key=f"explanation_{assignment['id']}").value, "")
        self.assertEqual(len(db.list_submissions()), 1)

    def test_old_formal_cache_requires_rejudging_under_new_policy(self):
        with (patch.object(llm, "is_configured", return_value=True),
              patch.object(verifier, "verify_cpp_code", return_value=VERIFICATION) as verify):
            app = self._open_student_submission()
            next(item for item in app.text_area if item.label.startswith("解题思路")).set_value("逐项比较求最大值。")
            next(button for button in app.button if button.label == "正式评测代码").click().run(timeout=20)
            result_key = str(db.list_assignments()[0]["id"])
            cached = dict(app.session_state["formal_judge_results"])
            cached[result_key].pop("policy_version")
            app.session_state["formal_judge_results"] = cached
            app.run(timeout=20)
            self.assertFalse(any(button.label == "进入AI答辩" for button in app.button))
            self.assertTrue(any("判题规则已经改变" in item.value for item in app.info))
            self.assertEqual(verify.call_count, 1)
            self.assertEqual(len(app.exception), 0)

    def test_formal_judge_receives_public_and_hidden_cases(self):
        assignment = db.list_assignments()[0]
        with patch.object(verifier, "verify_cpp_code", return_value=PARTIAL_VERIFICATION) as verify:
            app = self._open_student_submission()
            next(item for item in app.text_area if item.label.startswith("解题思路")).set_value("逐项比较求最大值。")
            next(button for button in app.button if button.label == "正式评测代码").click().run(timeout=20)
        cases = verify.call_args.args[1]
        self.assertEqual(len(cases), len(assignment["public_samples"]) + len(assignment["test_cases"]))
        self.assertTrue(all(case["count_only"] for case in cases))
        self.assertEqual(cases[0]["visibility"], "公开")
        self.assertEqual(cases[-1]["visibility"], "隐藏")
        self.assertFalse(any(button.label == "进入AI答辩" for button in app.button))

    def test_changed_assignment_warns_and_corrupt_draft_is_preserved(self):
        assignment = db.list_assignments()[0]
        student_drafts.save_draft(db.DB_PATH, "20260001", assignment["id"], {"explanation": "保留思路"}, "old-version")
        app = self._open_student_submission()
        self.assertTrue(any("教师已更新实验" in item.value for item in app.warning))
        self.assertTrue(any("重新评测" in item.value for item in app.warning))
        self.assertEqual(app.text_area(key=f"explanation_{assignment['id']}").value, "保留思路")
        path = student_drafts.draft_path(db.DB_PATH, "20260002", assignment["id"])
        path.write_text("broken-json", encoding="utf-8")
        other = self._open_student_submission("20260002")
        self.assertTrue(any("系统草稿暂时无法恢复" in item.value for item in other.warning))
        other.text_area(key=f"explanation_{assignment['id']}").set_value("新输入").run(timeout=20)
        self.assertEqual(path.read_text(encoding="utf-8"), "broken-json")


    def test_restore_changed_draft_without_verification_has_no_rejudge_requirement(self):
        assignment = db.list_assignments()[0]
        with db.get_connection() as connection:
            connection.execute("UPDATE assignments SET verification_enabled = 0 WHERE id = ?", (assignment["id"],))
        student_drafts.save_draft(db.DB_PATH, "20260001", assignment["id"], {"explanation": "保留思路"}, "old-version")
        with patch.object(llm, "is_configured", return_value=True):
            app = self._open_student_submission()
        self.assertEqual(app.text_area(key=f"explanation_{assignment['id']}").value, "保留思路")
        self.assertTrue(any("教师已更新实验" in item.value for item in app.warning))
        self.assertFalse(any("重新评测" in item.value for item in list(app.warning) + list(app.info)))
        self.assertTrue(any(button.label == "调用AI分析并开始答辩" for button in app.button))
        self.assertFalse(any(button.label == "正式评测代码" for button in app.button))
        self.assertEqual(len(app.exception), 0)

    def test_teacher_publishes_one_hidden_case_and_restores_optional_choice(self):
        config = self._selection_draft()
        config["title"] = "选择排序：一组隐藏样例"
        config["test_cases"] = config["test_cases"][:1]
        config["explanation_required"] = False
        assignment_drafts.save_draft(db.DB_PATH, config)
        app = self._open_experiment_editor()
        self.assertEqual(app.selectbox(key="create_assignment_explanation_required").value, "选做")
        with patch.object(verifier, "verify_cpp_code", return_value=VERIFICATION):
            next(button for button in app.button if button.label == "发布实验").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        saved = next(assignment for assignment in db.list_assignments() if assignment["title"] == config["title"])
        self.assertEqual(len(saved["test_cases"]), 1)
        self.assertFalse(saved["explanation_required"])

    def test_teacher_optional_choice_allows_empty_student_explanation_and_reaches_ai(self):
        assignment = db.list_assignments()[0]
        teacher = self._open_experiment_editor()
        teacher.selectbox(key=f"edit_assignment_explanation_required_{assignment['id']}").set_value("选做")
        with patch.object(verifier, "verify_cpp_code", return_value=VERIFICATION):
            next(button for button in teacher.button if button.label == "保存修改").click().run(timeout=20)
        self.assertEqual(len(teacher.exception), 0)
        self.assertFalse(db.get_assignment(assignment["id"])["explanation_required"])
        with (patch.object(llm, "is_configured", return_value=True),
              patch.object(verifier, "verify_cpp_code", return_value=VERIFICATION),
              patch.object(llm, "preliminary_review", return_value={"completion_score": 100, "summary": "实现正确", "criteria": []}) as preliminary,
              patch.object(llm, "generate_questions", return_value=QUESTIONS) as questions):
            app = self._open_student_submission()
            self.assertEqual(app.text_area(key=f"explanation_{assignment['id']}").label, "解题思路（选做）")
            self.assertEqual(app.text_area(key=f"explanation_{assignment['id']}").value, "")
            next(button for button in app.button if button.label == "正式评测代码").click().run(timeout=20)
            next(button for button in app.button if button.label == "进入AI答辩").click().run(timeout=20)
            self.assertFalse(preliminary.call_args.kwargs["explanation_required"])
            self.assertFalse(questions.call_args.kwargs["explanation_required"])
        saved = db.get_submission(db.list_submissions()[0]["id"])
        self.assertEqual(saved["explanation"], "")
        self.assertFalse(saved["assignment_snapshot"]["explanation_required"])
        self.assertEqual(len(app.exception), 0)

    def test_required_explanation_blocks_empty_submission_before_judge(self):
        with patch.object(verifier, "verify_cpp_code") as verify:
            app = self._open_student_submission()
            next(button for button in app.button if button.label == "正式评测代码").click().run(timeout=20)
        verify.assert_not_called()
        self.assertTrue(any("解题思路为必做" in item.value for item in app.error))
        self.assertEqual(len(db.list_submissions()), 1)


if __name__ == "__main__":
    unittest.main()
