from auth_helpers import authenticate
import copy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import ai_budget
import ai_workflow
import code_verifier as verifier
import database as db
import defense_drafts
import evaluation_benchmark as benchmark
import experiment_packages as packages
import llm_provider
import llm_service as llm
from local_store import write_json


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "test_materials/selection_sort/experiment_config.json"
QUESTIONS = benchmark.build_plan()[2:5]
QUESTIONS = [copy.deepcopy(item["question"]) for item in QUESTIONS]


def evaluation(follow=False):
    return {"score": 90, "confidence": "高", "feedback": "回答依据充分。", "missing_points": [],
            "misconceptions": [], "point_assessments": [], "should_follow_up": follow,
            "follow_up_question": "请解释初始化的含义。" if follow else ""}


class LocalImprovementsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "isolated.db"

    def tearDown(self):
        self.temp.cleanup()

    def test_drafts_are_isolated_by_student_submission_question_stage_and_database(self):
        args = (self.path, "001", 1, 0, "initial", {"question": "下标如何选择？"})
        text = "完整原话。；\nminIndex = i;"
        defense_drafts.save(*args, text)
        self.assertEqual(defense_drafts.load(*args)["answer"], text)
        variations = [(self.path, "002", 1, 0, "initial", args[-1]),
                      (self.path, "001", 2, 0, "initial", args[-1]),
                      (self.path, "001", 1, 1, "initial", args[-1]),
                      (self.path, "001", 1, 0, "follow_up", args[-1]),
                      (self.path, "001", 1, 0, "initial", {"question": "修改后的题目"}),
                      (self.path.with_name("other.db"), *args[1:])]
        for context in variations:
            self.assertIsNone(defense_drafts.load(*context))
        defense_drafts.clear(*args)
        self.assertIsNone(defense_drafts.load(*args))

    def test_corrupt_draft_is_not_silently_overwritten_on_load(self):
        args = (self.path, "001", 1, 0, "initial", "问题")
        target = defense_drafts.path(*args)
        target.parent.mkdir()
        target.write_text("not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            defense_drafts.load(*args)
        self.assertEqual(target.read_text(encoding="utf-8"), "not json")

    def test_checkpoint_resumes_same_input_and_new_content_or_model_has_separate_results(self):
        original = {"student": "001", "code": "int main(){}", "model": "model-a"}
        checkpoint = ai_workflow.Checkpoint(self.path, original)
        checkpoint.save("初评", {"score": 80})
        checkpoint.failed("出题")
        resumed = ai_workflow.Checkpoint(self.path, original)
        self.assertEqual(resumed.get("初评"), {"score": 80})
        self.assertEqual(resumed.document["failed_stage"], "出题")
        for updated in [{**original, "code": "int main(){return 0;}"}, {**original, "model": "model-b"}]:
            self.assertIsNone(ai_workflow.Checkpoint(self.path, updated).get("初评"))
        token = resumed.document["request_token"]
        resumed.clear()
        self.assertNotEqual(ai_workflow.Checkpoint(self.path, original).document["request_token"], token)

    def test_wait_is_bounded_and_late_response_is_not_returned_as_success(self):
        started = time.monotonic()
        with self.assertRaises(ai_workflow.WorkflowError):
            ai_workflow.Operation(seconds=0.03).run(lambda: (time.sleep(0.12), "late")[1])
        self.assertLess(time.monotonic() - started, 0.1)
        time.sleep(0.14)
        self.assertEqual(ai_workflow.Operation(seconds=1).run(lambda: "next"), "next")

    def test_request_deadline_reaches_provider_and_hidden_retries_are_disabled(self):
        config = llm_provider.LLMConfig("test", "test", "openai-compatible", "fake-test-key", "", "test", True)
        with ai_budget.request_deadline(time.monotonic() + 3), patch.object(llm_provider, "OpenAI") as client:
            llm_provider._create_client(config)
        self.assertLessEqual(client.call_args.kwargs["timeout"], 3)
        self.assertEqual(client.call_args.kwargs["max_retries"], 0)

    def test_request_has_its_own_limit_within_the_longer_operation_budget(self):
        started = time.monotonic()
        with patch.object(ai_budget, "REQUEST_SECONDS", 0.03):
            with self.assertRaisesRegex(ai_workflow.WorkflowError, "AI请求已到等待上限"):
                ai_workflow.Operation(seconds=1).run(lambda: (time.sleep(0.12), "late")[1])
        self.assertLess(time.monotonic() - started, 0.1)
        time.sleep(0.14)

    def test_complete_package_roundtrip_preserves_all_teaching_materials_and_normalizes_pass_rule(self):
        values = packages.parse(CONFIG.read_bytes())
        values["explanation_required"] = False
        restored = packages.parse(packages.export(values))
        for field in ["title", "description", "reference_code", "starter_code", "requirements", "teaching_focus",
                      "public_samples", "rubric", "cpp_standard", "hint_limit", "explanation_required", "test_cases"]:
            self.assertEqual(restored[field], values[field], field)
        self.assertTrue(all(case["count_only"] and case["weight"] == 0 for case in restored["test_cases"]))
        self.assertEqual(restored["defense_admission"], "全部通过")

    def test_invalid_packages_fail_before_any_draft_or_assignment_write(self):
        values = json.loads(CONFIG.read_text(encoding="utf-8"))
        examples = [{**values, "verification_enabled": "false"}, {**values, "api_key": "never-store-this"},
                    {**values, "test_cases": []}, {**values, "reference_code": ""},
                    {**values, "requirements": "wrong type"}]
        for invalid in examples:
            with self.assertRaises(ValueError):
                packages.parse(json.dumps(invalid))
        with self.assertRaises(ValueError):
            packages.parse("{" + "x" * 1_000_001)
        self.assertFalse(self.path.exists())

    def test_import_export_preserves_five_public_samples_and_rejects_silent_truncation(self):
        values = packages.parse(CONFIG.read_bytes())
        sample = values["public_samples"][0]
        values["public_samples"] = [{**sample, "name": f"公开样例{i}"} for i in range(1, 6)]
        restored = packages.parse(packages.export(values))
        self.assertEqual(restored["public_samples"], values["public_samples"])
        invalid = copy.deepcopy(values)
        invalid["public_samples"][0]["input"] = "0" * (db.MAX_PUBLIC_SAMPLE_CHARS + 1)
        with self.assertRaisesRegex(ValueError, "长度限制"):
            packages.parse(json.dumps(invalid))
        with self.assertRaisesRegex(ValueError, "NaN"):
            packages.parse('{"title":NaN}')

    @unittest.skipUnless(shutil.which("g++"), "当前测试环境没有g++")
    def test_built_in_complete_case_reference_passes_all_public_and_hidden_samples(self):
        values = packages.parse(CONFIG.read_bytes())
        public = [{"name": item["name"], "input": item["input"], "expected_output": item["output"]}
                  for item in values["public_samples"]]
        with patch.dict(os.environ, {"CODE_EXECUTION_ENABLED": "true", "CXX_COMPILER": "g++"}):
            for cases in [values["test_cases"], public]:
                result = verifier.verify_cpp_code(values["reference_code"], cases, values["cpp_standard"])
                self.assertEqual(result["overall_status"], "全部通过", result)
                self.assertEqual(result["passed"], len(cases))

    def test_copy_ignores_old_identity_status_and_review_confirmation(self):
        values = packages.parse(CONFIG.read_bytes())
        values.update(id=50, status="已发布", created_at="old", rubric_reviewed=True)
        copied = packages.from_assignment(values, copy=True)
        self.assertTrue(copied["title"].endswith("（副本）"))
        self.assertFalse(copied["rubric_reviewed"])
        self.assertNotIn("id", copied)
        self.assertNotIn("status", copied)
        self.assertEqual(copied["reference_code"], values["reference_code"])

    def test_benchmark_includes_static_misuse_omission_and_instruction_injection_controls(self):
        plan = benchmark.build_plan(2)
        self.assertEqual(len(plan), 26)
        self.assertEqual(len({p["id"] for p in plan}), 26)
        omitted = next(p for p in plan if "未回答单元素" in p["case"])
        self.assertEqual(omitted["expected"], ["掌握", "未体现"])
        injected = next(p for p in plan if "要求满分" in p["case"])
        self.assertEqual(injected["expected"], ["未体现", "未体现"])
        static = next(p for p in plan if "库函数" in p["case"])
        self.assertEqual(static["expected"], ["未满足", "满足"])

    def test_benchmark_reports_false_quote_and_wrong_judgment_without_changing_expectation(self):
        item = next(p for p in benchmark.build_plan() if p["kind"] == "answer")
        before = copy.deepcopy(item)
        rows = benchmark.comparison_rows(item, {"point_assessments": [
            {"status": "掌握", "answer_quote": "学生没有说过的话"}, {"status": "理解错误", "answer_quote": item["answer"]},
        ]})
        self.assertTrue(rows[0]["需核查"])
        self.assertEqual(rows[0]["学生回答原文可核对"], "否")
        self.assertEqual(rows[1]["是否一致"], "否")
        self.assertEqual(item, before)

    def test_focused_retest_pairs_identical_swap_explanation_with_missing_boundary(self):
        plan = benchmark.build_plan(2, "focused")
        self.assertEqual(len(plan), 6)
        full = next(p for p in plan if p["case"] == "理解充分" and p["number"] == 2)
        omitted = next(p for p in plan if "未回答单元素" in p["case"])
        self.assertTrue(full["answer"].startswith(omitted["answer"]))
        self.assertEqual(full["expected"][0], omitted["expected"][0])
        run = benchmark.create_run(self.path, {"model": "offline"}, 2, "focused")
        self.assertEqual(benchmark.load_latest(self.path)["scope"], "focused")
        self.assertEqual(len(run["plan"]), 6)

    def test_rewritten_formula_quote_is_rejected_before_accepting_ai_score(self):
        item = next(p for p in benchmark.build_plan(1, "focused") if p["number"] == 3)
        response = {"score": 96, "confidence": "高", "feedback": "回答正确。", "missing_points": [],
                    "misconceptions": [], "follow_up_question": "", "point_assessments": [
                        {"status": "掌握", "answer_quote": "将a[j]<a[minIndex]改成a[j]>a[minIndex]"},
                        {"status": "掌握", "answer_quote": "双层扫描仍有n(n-1)/2次比较，时间O²，额外空间O(1)。"},
                    ]}
        with patch.object(llm, "_chat_json", side_effect=[{"status": "可评价", "premise_issue": "", "basis": "模拟核对题目前提。"}, response]):
            with self.assertRaisesRegex(llm.LLMServiceError, "原文不一致"):
                benchmark.evaluate(item, llm)
        response["point_assessments"][1]["answer_quote"] = "双层扫描仍有n(n-1)/2次比较，时间O(n²)，额外空间O(1)。"
        with patch.object(llm, "_chat_json", side_effect=[{"status": "可评价", "premise_issue": "", "basis": "模拟核对题目前提。"}, response]):
            result = benchmark.evaluate(item, llm)
        self.assertEqual(result["point_assessments"][1]["answer_quote"], response["point_assessments"][1]["answer_quote"])

    def test_follow_up_quote_must_come_from_one_actual_answer(self):
        question = {"question": "解释变量", "reference_points": ["下标含义"]}
        response = {"score": 95, "confidence": "高", "feedback": "解释正确。", "missing_points": [],
                    "misconceptions": [], "point_assessments": [
                        {"status": "掌握", "answer_quote": "minIndex保存下标，而不是元素值。"}]}
        args = ("排序", "代码", question, "minIndex保存下标。", "不是数值吗？", "不是元素值。")
        with patch.object(llm, "_chat_json", side_effect=[{"status": "可评价", "premise_issue": "", "basis": "模拟核对题目前提。"}, response]):
            with self.assertRaisesRegex(llm.LLMServiceError, "原文不一致"):
                llm.evaluate_follow_up(*args)
        response["point_assessments"][0]["answer_quote"] = "不是元素值。"
        with patch.object(llm, "_chat_json", side_effect=[{"status": "可评价", "premise_issue": "", "basis": "模拟核对题目前提。"}, response]):
            self.assertEqual(llm.evaluate_follow_up(*args)["point_assessments"][0]["answer_quote"], "不是元素值。")

    def test_benchmark_resume_and_repeated_case_stability_keep_independent_records(self):
        run = benchmark.create_run(self.path, {"model": "fake-for-offline-test"}, 2)
        first = next(p for p in run["plan"] if p["kind"] == "answer")
        repeat = next(p for p in run["plan"] if p["round"] == 2 and p["case"] == first["case"] and p.get("number") == first["number"])
        run["results"][first["id"]] = {"evaluation": {"point_assessments": [{"status": "掌握"}] * 2}}
        run["results"][repeat["id"]] = {"evaluation": {"point_assessments": [{"status": "部分掌握"}] * 2}}
        benchmark.save_run(self.path, run)
        self.assertEqual(len(benchmark.load_latest(self.path)["results"]), 2)
        self.assertTrue(all(row["判断是否稳定"] == "否" for row in benchmark.stability_rows(run)))
        self.assertFalse(self.path.exists())


class FourImprovementsFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = db.DB_PATH
        db.DB_PATH = Path(self.temp.name) / "isolated.db"
        db.init_db()
        self.student_id = "20260001"
        self.submission_id = db.create_submission_with_questions(
            self.student_id, "测试同学", "选择排序", "思路", "int main(){return 0;}",
            "2026-10-08 13:00", QUESTIONS,
        )

    def tearDown(self):
        db.DB_PATH = self.original
        self.temp.cleanup()

    def app(self, teacher=False, page="任务中心"):
        app = AppTest.from_file(str(ROOT / "app.py"))
        for key, value in {"auth_role": "教师" if teacher else "学生", "auth_name": "测试用户",
                           "auth_student_id": "" if teacher else self.student_id, "current_page": page}.items():
            app.session_state[key] = value
        authenticate(app, "教师" if teacher else "学生", self.student_id, "测试用户")
        app.run(timeout=20)
        self.assertFalse(app.exception)
        return app

    def continue_defense(self):
        app = self.app()
        next(b for b in app.button if b.label == "继续答辩").click().run(timeout=20)
        self.assertFalse(app.exception)
        return app

    def test_answer_survives_new_browser_session_and_failed_ai_call_without_advancing(self):
        app = self.continue_defense()
        answer = "minIndex保存下标，这是一段尚未评价的回答。"
        next(a for a in app.text_area if a.label == "请输入你的回答").set_value(answer).run(timeout=20)
        with patch.object(llm, "evaluate_initial_answer", side_effect=llm.LLMServiceError("模拟连接失败")):
            next(b for b in app.button if b.label == "提交给AI评价").click().run(timeout=20)
        self.assertEqual(app.session_state["current_question"], 0)
        self.assertEqual(db.get_qa_records(self.submission_id)[0]["answer"], "")
        refreshed = self.continue_defense()
        self.assertEqual(next(a for a in refreshed.text_area if a.label == "请输入你的回答").value, answer)

    def test_follow_up_draft_survives_failure_and_resumes_correct_stage(self):
        app = self.continue_defense()
        next(a for a in app.text_area if a.label == "请输入你的回答").set_value("初次解释").run(timeout=20)
        with patch.object(llm, "evaluate_initial_answer", return_value=evaluation(follow=True)):
            next(b for b in app.button if b.label == "提交给AI评价").click().run(timeout=20)
        answer = "补充解释：候选来自未排序区间。"
        next(a for a in app.text_area if a.label == "请输入追问回答").set_value(answer).run(timeout=20)
        with patch.object(llm, "evaluate_follow_up", side_effect=llm.LLMServiceError("模拟失败")):
            next(b for b in app.button if b.label == "提交追问回答").click().run(timeout=20)
        refreshed = self.continue_defense()
        self.assertTrue(refreshed.session_state["awaiting_follow_up"])
        self.assertEqual(next(a for a in refreshed.text_area if a.label == "请输入追问回答").value, answer)

    def test_db_failure_reuses_ai_result_and_saves_answer_once_on_retry(self):
        app = self.continue_defense()
        next(a for a in app.text_area if a.label == "请输入你的回答").set_value("候选来自未排序区间").run(timeout=20)
        original_save = db.save_initial_result
        attempts = []
        def fail_once(*args, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise sqlite3.OperationalError("模拟数据库短暂失败")
            return original_save(*args, **kwargs)
        with patch.object(llm, "evaluate_initial_answer", return_value=evaluation()) as ai, patch.object(db, "save_initial_result", side_effect=fail_once):
            next(b for b in app.button if b.label == "提交给AI评价").click().run(timeout=20)
            self.assertEqual(app.session_state["current_question"], 0)
            next(b for b in app.button if b.label == "提交给AI评价").click().run(timeout=20)
            self.assertEqual(ai.call_count, 1)
        self.assertEqual(app.session_state["current_question"], 1)
        self.assertTrue(db.get_qa_records(self.submission_id)[0]["final_evaluation"])

    def test_submission_token_is_idempotent_and_new_intent_can_create_new_submission(self):
        args = (self.student_id, "测试用户", "选择排序", "思路", "int main(){}", "2026-10-08", QUESTIONS)
        first = db.create_submission_with_questions(*args, request_token="test-request-one")
        second = db.create_submission_with_questions(*args, request_token="test-request-one")
        third = db.create_submission_with_questions(*args, request_token="test-request-two")
        self.assertEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertEqual(len(db.get_qa_records(first)), 3)

    def test_import_complete_experiment_fills_form_without_publishing_or_changing_original(self):
        app = self.app(teacher=True, page="实验管理")
        before = copy.deepcopy(db.list_assignments())
        next(a for a in app.text_area if a.label == "或粘贴完整实验JSON").set_value(CONFIG.read_text(encoding="utf-8")).run(timeout=20)
        next(b for b in app.button if b.label == "检查实验包").click().run(timeout=20)
        next(b for b in app.button if b.label == "载入未发布草稿").click().run(timeout=20)
        self.assertFalse(app.exception)
        self.assertIn("选择排序", app.text_input(key="create_assignment_title").value)
        self.assertEqual(app.text_area(key="create_assignment_reference_code").value, packages.parse(CONFIG.read_bytes())["reference_code"])
        self.assertEqual(db.list_assignments(), before)

    def test_copy_loads_draft_and_teacher_benchmark_panel_is_accessible(self):
        app = self.app(teacher=True, page="实验管理")
        before = copy.deepcopy(db.list_assignments())
        next(b for b in app.button if b.label == "复制为新实验草稿").click().run(timeout=20)
        self.assertFalse(app.exception)
        self.assertTrue(app.text_input(key="create_assignment_title").value.endswith("（副本）"))
        self.assertEqual(db.list_assignments(), before)
        teacher = self.app(teacher=True, page="教师工作台")
        self.assertFalse(any(e.label == "AI评价对照测试" for e in teacher.expander))
        teacher = self.app(teacher=True, page="系统设置")
        self.assertFalse(any(e.label == "AI评价对照测试" for e in teacher.expander))
        self.assertFalse(any(c.label == "显示高级诊断" for c in teacher.checkbox))
        admin = AppTest.from_file(str(ROOT / "app.py"))
        authenticate(admin, "管理员")
        admin.session_state["current_page"] = "账号与系统管理"
        admin.run(timeout=20)
        next(c for c in admin.checkbox if c.label == "显示高级诊断").check().run(timeout=20)
        self.assertFalse(admin.exception)
        self.assertTrue(any(e.label == "AI评价对照测试" for e in admin.expander))


if __name__ == "__main__":
    unittest.main()
