import ast
from pathlib import Path
import unittest

import pandas as pd
import teacher_insights as insights


class UIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        cls.source = app_path.read_text(encoding="utf-8")

    def test_version_marker_is_updated(self):
        self.assertIn("v1.3.16 · 浏览器在线Demo版", self.source)

    def test_public_demo_has_safe_browser_entry(self):
        self.assertIn("demo_mode.ensure_demo_data()", self.source)
        self.assertIn("体验学生端（张同学）", self.source)
        self.assertIn("体验教师端", self.source)
        self.assertIn("disabled=demo_mode.demo_read_only()", self.source)

    def test_role_pages_and_teacher_password_are_enforced(self):
        self.assertIn("STUDENT_PAGES", self.source)
        self.assertIn("TEACHER_PAGES", self.source)
        self.assertIn('os.getenv("TEACHER_PASSWORD", "")', self.source)
        self.assertIn("hmac.compare_digest", self.source)
        self.assertIn("不能查看其他学生的报告", self.source)
        self.assertIn("不能打开其他学生的任务", self.source)
        self.assertIn("退出登录", self.source)

    def test_empty_number_editor_cell_does_not_crash_test_case_form(self):
        module = ast.parse(self.source)
        helper_names = {"normalize_editor_scalar", "editor_value_is_blank"}
        helpers = [
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name in helper_names
        ]
        namespace = {"pd": pd}
        exec(compile(ast.Module(body=helpers, type_ignores=[]), "app.py", "exec"), namespace)
        self.assertIsNone(namespace["normalize_editor_scalar"]([]))
        self.assertEqual(namespace["normalize_editor_scalar"]([25]), 25)
        self.assertTrue(namespace["editor_value_is_blank"]([]))
        self.assertNotIn('item.get("weight") not in {None, ""}', self.source)

    def test_docker_environment_is_explained_without_blaming_student_code(self):
        self.assertIn("不会记为学生代码错误", self.source)
        self.assertIn("runtime['detail']", self.source)

    def test_oj_statement_and_custom_run_are_visible(self):
        self.assertIn("OJ题面设置（学生可见）", self.source)
        self.assertIn("公开输入输出样例（学生可见，可留空）", self.source)
        self.assertIn("render_oj_statement(assignment,", self.source)
        self.assertIn("运行代码并查看输出", self.source)
        self.assertIn("verifier.run_cpp_code(", self.source)
        self.assertIn('"查看格式"', self.source)
        self.assertIn("wuma-space-run", self.source)
        self.assertIn('f"×{run_length}"', self.source)
        self.assertIn("连续空格数量", self.source)
        self.assertNotIn('title="空格">·', self.source)
        self.assertIn("wuma-newline-marker", self.source)
        self.assertIn("wuma-tab-marker", self.source)

    def test_newline_marker_forces_the_next_content_to_a_new_line(self):
        self.assertIn('title="换行">↵</span><br>', self.source)

    def test_empty_required_output_is_labeled_wrong_answer(self):
        self.assertIn("答案错误：题目要求输出结果，但程序没有输出", self.source)

    def test_public_sample_one_click_judging_is_visible(self):
        self.assertIn("公开样例一键判题", self.source)
        self.assertIn("render_public_sample_run_result", self.source)
        self.assertIn("运行全部公开样例", self.source)
        self.assertIn("第一个差异", self.source)
        self.assertIn("代码已修改，上次公开样例判题结果已失效", self.source)
        self.assertIn("答案正确：实际输出与该公开样例", self.source)
        self.assertIn("答案错误：实际输出与该公开样例", self.source)

    def test_new_hidden_case_editor_starts_blank(self):
        self.assertIn('"create_assignment_test_cases",\n                [],', self.source)
        self.assertNotIn(
            '"create_assignment_test_cases",\n                db.DEFAULT_TEST_CASES,',
            self.source,
        )

    def test_public_and_hidden_cases_use_multiline_cards(self):
        self.assertIn("标准输入和预期输出均可直接粘贴多行文本", self.source)
        self.assertIn('key=f"{key}_{index}_input"', self.source)
        self.assertIn('key=f"{key}_{index}_output"', self.source)
        self.assertIn('"name": name.strip() or f"测试{index + 1}"', self.source)
        self.assertIn("for index in range(8):", self.source)
        self.assertIn("for index in range(5):", self.source)

    def test_code_brand_replaces_brain_icon(self):
        self.assertIn('page_icon=":material/code:"', self.source)
        self.assertIn("BRAND_ICON_SVG", self.source)
        self.assertIn("wuma-brand-icon", self.source)
        self.assertNotIn("🧠", self.source)

    def test_submission_draft_and_retry_feedback_are_visible(self):
        self.assertIn("submission_drafts", self.source)
        self.assertIn("submission_ai_error", self.source)
        self.assertIn("当前输入没有被清空", self.source)
        self.assertIn("步骤1/3", self.source)
        self.assertIn("步骤2/3", self.source)
        self.assertIn("步骤3/3", self.source)

    def test_rubric_templates_are_actionable(self):
        self.assertIn("评分点模板", self.source)
        self.assertIn("db.get_rubric_template(", self.source)
        self.assertIn("载入模板", self.source)

    def test_code_verification_is_visible_and_traceable(self):
        self.assertIn("import code_verifier as verifier", self.source)
        self.assertIn("启用C++编译与隐藏测试样例验证", self.source)
        self.assertIn("代码客观验证", self.source)
        self.assertIn("教师查看隐藏样例输入与输出证据", self.source)
        self.assertIn("code_verification=code_verification", self.source)

    def test_formal_judging_precedes_defense_and_reports_verdicts(self):
        self.assertIn("正式评测代码", self.source)
        self.assertIn("评测后不会自动进入AI答辩", self.source)
        self.assertIn("编译错误（CE）", self.source)
        self.assertIn("答案正确（AC）", self.source)
        self.assertIn("答案错误（WA）", self.source)
        self.assertIn("运行超时（TLE）", self.source)
        self.assertIn("运行错误（RE）", self.source)
        self.assertIn("部分正确", self.source)
        self.assertIn("formal_verification_is_conclusive", self.source)
        self.assertIn("当前代码没有有效且满足准入规则", self.source)

    def test_defense_admission_rules_are_enforced(self):
        module = ast.parse(self.source)
        helper_names = {
            "formal_verification_is_conclusive",
            "defense_admission_result",
        }
        helpers = [
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name in helper_names
        ]
        namespace = {"insights": insights}
        exec(compile(ast.Module(body=helpers, type_ignores=[]), "app.py", "exec"), namespace)
        partial = {
            "overall_status": "部分通过",
            "score": 60,
            "max_score": 100,
        }
        allowed, _ = namespace["defense_admission_result"](
            {"verification_enabled": True, "defense_admission": "全部通过"},
            partial,
        )
        self.assertFalse(allowed)
        allowed, _ = namespace["defense_admission_result"](
            {
                "verification_enabled": True,
                "defense_admission": "达到指定分数",
                "defense_score_threshold": 60,
            },
            partial,
        )
        self.assertTrue(allowed)
        allowed, _ = namespace["defense_admission_result"](
            {"verification_enabled": True, "defense_admission": "允许带错代码"},
            {"overall_status": "编译失败"},
        )
        self.assertTrue(allowed)
        allowed, _ = namespace["defense_admission_result"](
            {"verification_enabled": True, "defense_admission": "允许带错代码"},
            {"overall_status": "环境不可用"},
        )
        self.assertFalse(allowed)

    def test_limited_ai_hints_are_configurable_and_traceable(self):
        self.assertIn("每名学生可使用的AI提示次数", self.source)
        self.assertIn("答辩准入规则", self.source)
        self.assertIn("获取AI提示（剩余", self.source)
        self.assertIn("db.save_ai_hint(", self.source)
        self.assertIn("AI提示使用记录", self.source)
        self.assertIn("学生端不展示隐藏测试的名称、输入、标准输出和实际输出", self.source)
        self.assertIn("历史代码·已失效", self.source)
        self.assertIn('record.get("code_digest") == current_code_digest', self.source)

    def test_assignment_reliability_gate_is_visible(self):
        self.assertIn("教师参考答案（仅教师可见）", self.source)
        self.assertIn("reference_solution_preflight", self.source)
        self.assertIn("至少配置3组隐藏测试", self.source)
        self.assertIn("我已逐项核对，确认这些评分点确实适用于当前实验", self.source)
        self.assertIn("assignment_publish_blockers", self.source)

    def test_weighted_hidden_cases_and_class_statistics_are_visible(self):
        self.assertIn('"测试分组": item.get("group", "基础功能")', self.source)
        self.assertIn('"分值": item.get("weight")', self.source)
        self.assertIn("代码客观得分", self.source)
        self.assertIn("测试分组得分", self.source)
        self.assertIn("build_hidden_case_statistics", self.source)
        self.assertIn("隐藏测试点通过率", self.source)
        self.assertIn("编译失败计为对应测试点未通过", self.source)

    def test_minimum_character_prompts_are_removed(self):
        self.assertNotIn("请用至少10个字", self.source)
        self.assertNotIn("请填写至少30个字", self.source)
        self.assertNotIn("回答过短", self.source)

    def test_rubric_driven_review_is_visible(self):
        self.assertIn("评分点与证据来源", self.source)
        self.assertIn("render_rubric_editor", self.source)
        self.assertIn("llm.preliminary_review(", self.source)
        self.assertIn("评分点初评证据", self.source)

    def test_completion_defense_and_teacher_results_are_separated(self):
        self.assertIn("评分点证据初评", self.source)
        self.assertIn("答辩理解度", self.source)
        self.assertIn("教师确认理解度", self.source)
        self.assertIn("不是课程最终成绩", self.source)
        self.assertIn("四项结果互不相加", self.source)

    def test_lab_report_and_question_reason_are_traceable(self):
        self.assertIn('"实验报告（选做）"', self.source)
        self.assertNotIn("实验报告不能为空", self.source)
        self.assertIn("问题生成依据", self.source)
        self.assertIn("rubric_snapshot", self.source)

    def test_cpp_standard_can_be_selected_and_is_traceable(self):
        self.assertIn("CPP_STANDARD_OPTIONS", self.source)
        self.assertIn('"C++编译标准"', self.source)
        self.assertIn("effective_cpp_standard", self.source)

    def test_student_task_center_and_teacher_workspace_are_actionable(self):
        self.assertIn('"任务中心"', self.source)
        self.assertIn("智能待办中心", self.source)
        self.assertIn("db.list_student_tasks(", self.source)
        self.assertIn("db.list_teacher_tasks(", self.source)
        self.assertIn("打开任务中心", self.source)
        self.assertIn('"教师工作台"', self.source)
        self.assertIn('["待办处理", "班级分析"]', self.source)
        self.assertIn("已定位待办", self.source)

    def test_teacher_workspace_defaults_to_compact_decision_information(self):
        self.assertIn("build_teacher_status_dataframe", self.source)
        self.assertIn("build_teacher_digest", self.source)
        self.assertIn("重点关注（最多3项）", self.source)
        self.assertIn("完整原文默认折叠", self.source)
        self.assertIn("历史评测环境异常", self.source)
        self.assertIn("待办只负责一次定位", self.source)
        self.assertIn("选择要处理的学生", self.source)
        self.assertIn("查看学生代码、解题思路与实验报告", self.source)
        self.assertIn("render_teacher_review_actions", self.source)
        self.assertIn("render_teacher_feedback_actions", self.source)

    def test_teacher_digest_limits_attention_items_and_hides_feedback_body(self):
        module = ast.parse(self.source)
        helper_names = {
            "compact_code_result",
            "shorten_teacher_text",
            "build_teacher_digest",
        }
        helpers = [
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name in helper_names
        ]
        namespace = {"insights": insights}
        exec(compile(ast.Module(body=helpers, type_ignores=[]), "app.py", "exec"), namespace)
        record = {
            "code_verification": {
                "overall_status": "部分通过",
                "passed": 1,
                "total": 2,
                "score": 40,
                "max_score": 100,
                "cases": [{"verdict_code": "WA"}],
            },
            "teacher_reviewed": False,
            "teacher_decision": None,
            "teacher_confirmed_overall": None,
            "redefense_status": None,
        }
        preliminary = {
            "completion_score": 70,
            "criteria": [
                {
                    "criterion_name": "边界情况",
                    "status": "部分满足",
                    "missing": "没有解释空数组和容量上限。",
                }
            ],
        }
        report = {
            "overall": 65,
            "weakest": "边界情况意识",
            "dimensions": {"边界情况意识": 55},
            "review_reasons": ["代码结果和口头解释存在差异。"],
        }
        secret_feedback = "这是一段不应被摘要照搬的学生反馈原文"
        digest = namespace["build_teacher_digest"](
            record,
            preliminary,
            report,
            [{"answer": "回答", "question": "问题"}],
            [{"status": "待处理", "reply_requested": True, "content": secret_feedback}],
        )
        self.assertLessEqual(len(digest["concerns"]), 3)
        self.assertNotIn(secret_feedback, repr(digest))
        self.assertIn("学生反馈1条", digest["evidence"])

    def test_interface_does_not_hardcode_deepseek(self):
        self.assertNotIn("正在连接DeepSeek", self.source)
        self.assertNotIn("DeepSeek API密钥", self.source)

    def test_teacher_review_loop_is_visible_and_savable(self):
        self.assertIn("教师人工复核", self.source)
        self.assertIn("db.save_teacher_review(", self.source)
        self.assertIn("教师确认理解度", self.source)
        self.assertIn("历史复核记录（", self.source)
        self.assertIn("教师复核意见（选填）", self.source)

    def test_teacher_evidence_labels_are_visual_callouts(self):
        self.assertIn("**答辩问题**", self.source)
        self.assertIn("**为什么问这题**", self.source)
        self.assertIn("#### 学生实际回答", self.source)
        self.assertIn("st.error(finding_text)", self.source)

    def test_competition_ui_separates_web_model_from_learnbuddy(self):
        self.assertIn("悟码AI网页智能服务：腾讯混元", self.source)
        self.assertIn("赛事展示前请切换为腾讯混元", self.source)
        self.assertIn("LearnBuddy通过Skill和MCP读取悟码AI诊断", self.source)
        self.assertIn("不调用所谓的“LearnBuddy模型API”", self.source)
        self.assertNotIn("llm.provider_name()", self.source)

    def test_teacher_accepting_ai_score_is_automatic(self):
        self.assertIn("sync_teacher_score_to_ai", self.source)
        self.assertIn("教师确认理解度自动同步为AI答辩理解度", self.source)
        self.assertIn('disabled=decision == "认可AI诊断"', self.source)

    def test_student_feedback_loop_is_visible(self):
        self.assertIn("给教师留言", self.source)
        self.assertIn("db.save_student_feedback(", self.source)
        self.assertIn("学生反馈队列", self.source)
        self.assertIn("db.process_student_feedback(", self.source)

    def test_redefense_and_comparison_are_visible(self):
        self.assertIn("开始教师要求的重新答辩", self.source)
        self.assertIn("继续重新答辩", self.source)
        self.assertIn("render_redefense_comparison", self.source)
        self.assertIn("重新答辩前后对比", self.source)

    def test_feedback_filter_and_jump_are_visible(self):
        self.assertIn("筛选反馈状态", self.source)
        self.assertIn("从反馈队列快速定位", self.source)

    def test_chart_labels_are_horizontal(self):
        self.assertIn("labelAngle=0", self.source)
        self.assertNotIn("st.bar_chart(", self.source)

    def test_table_cells_are_centered(self):
        self.assertIn('classes="wuma-data-table"', self.source)
        self.assertIn("text-align: center !important", self.source)


if __name__ == "__main__":
    unittest.main()
