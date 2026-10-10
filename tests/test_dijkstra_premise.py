"""Synthetic Dijkstra acceptance cases; offline mocks do not establish model accuracy."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from auth_helpers import authenticate
import code_verifier
import database as db
import evaluation_benchmark as benchmark
import llm_service as llm

ROOT = Path(__file__).resolve().parents[1]
SCOPE = "dijkstra_premise"
INITIAL_EVALUATE = llm.evaluate_initial_answer
ISSUE = "题目假设不可达节点以INF入队，但代码仅在有限距离严格改善后入队；该前提不成立。"


def raw_response(item):
    invalid = item["acceptance_mode"] == "invalid_premise"
    return {"score": 100, "confidence": "高", "feedback": "已核对入队路径与合法数据范围。",
            "premise_issue": ISSUE if invalid else "", "missing_points": [], "misconceptions": [],
            "follow_up_question": "", "point_assessments": [
                {"status": "掌握", "evidence": "回答解释了实际执行路径和范围。", "answer_quote": quote}
                for quote in item["answer"].split("\n")]}


def premise_response(item):
    invalid = item["acceptance_mode"] == "invalid_premise"
    return {"status": "待教师核查" if invalid else "可评价",
            "premise_issue": ISSUE if invalid else "", "basis": "已核对队列入队与实际运算路径。"}


def evaluated(item):
    with patch.object(llm, "_chat_json", side_effect=[premise_response(item), raw_response(item)]):
        return INITIAL_EVALUATE(item["problem"], item["code"], item["question"], item["answer"])


class DijkstraPremiseTests(unittest.TestCase):
    def setUp(self):
        self.plan = benchmark.build_plan(2, SCOPE)
        self.bad, self.normal = self.plan[:2]
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "isolated.db"

    def tearDown(self):
        self.temp.cleanup()

    def test_two_rounds_pair_same_code_answer_and_context_without_changing_old_scopes(self):
        self.assertEqual(len(self.plan), 4)
        self.assertEqual(len({x["id"] for x in self.plan}), 4)
        self.assertEqual([x["round"] for x in self.plan], [1, 1, 2, 2])
        self.assertEqual(self.bad["code"], self.normal["code"])
        self.assertEqual(self.bad["answer"], self.normal["answer"])
        self.assertIn("const ll INF = numeric_limits<ll>::max();", self.bad["code"])
        self.assertIn("max() / 4", (ROOT / "test_materials/dijkstra/reference.cpp").read_text(encoding="utf-8"))
        for scope, expected in [("all", 26), ("focused", 6), ("complex", 36)]:
            self.assertEqual(len(benchmark.build_plan(2, scope)), expected)

    def test_evaluation_uses_real_service_contract_without_sending_acceptance_answers(self):
        for item in self.plan:
            with patch.object(llm, "_chat_json", side_effect=[premise_response(item), raw_response(item)]) as chat:
                result = benchmark.evaluate(item, llm)
            prompt = chat.call_args.args[0][1]["content"]
            self.assertIn(item["question"]["question"], prompt)
            if item["acceptance_mode"] == "invalid_premise":
                self.assertNotIn(item["answer"], prompt)
                self.assertEqual(chat.call_count, 1)
            else:
                self.assertIn(item["answer"], prompt)
                self.assertEqual(chat.call_count, 2)
            sent_context = json.loads(prompt.split("<experiment_context>", 1)[1].split("</experiment_context>", 1)[0])
            self.assertEqual(sent_context, item["question"]["experiment_context"])
            self.assertNotIn(item["manual_check"], prompt)
            self.assertNotIn(item["case"], prompt)
            context = item["question"]["experiment_context"]
            self.assertEqual(set(context), {"description", "requirements", "input_format", "output_format", "constraints_text"})
            self.assertTrue(all(row["是否一致"] == "是" for row in benchmark.comparison_rows(item, result)))

    def test_invalid_premise_requires_all_four_guards_without_grading_invalid_reference_points(self):
        result = evaluated(self.bad)
        rows = benchmark.comparison_rows(self.bad, result)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(not row["需核查"] for row in rows))
        self.assertEqual(result["confidence"], "低")
        for change in [{"premise_issue": ""}, {"premise_issue": "   "}, {"confidence": "高"},
                       {"should_follow_up": True}, {"follow_up_question": "继续证明一定溢出"},
                       {"is_sufficient": True}]:
            with self.subTest(change=change):
                changed = {**result, **change}
                rows = benchmark.comparison_rows(self.bad, changed)
                self.assertTrue(any(row["需核查"] and row["是否一致"] == "否" for row in rows))
        changed = {**result, "point_assessments": []}
        self.assertEqual(benchmark.comparison_rows(self.bad, changed), benchmark.comparison_rows(self.bad, result))

    def test_missing_control_fields_cannot_be_counted_as_acceptance_success(self):
        for item in [self.bad, self.normal]:
            result = evaluated(item)
            for field in ["confidence", "should_follow_up", "follow_up_question", "is_sufficient"]:
                with self.subTest(case=item["case"], field=field):
                    changed = {k: v for k, v in result.items() if k != field}
                    self.assertTrue(any(row["需核查"] for row in benchmark.comparison_rows(item, changed)))

    def test_valid_control_requires_correct_judgment_and_actual_answer_quotes(self):
        result = evaluated(self.normal)
        self.assertEqual(len(benchmark.comparison_rows(self.normal, result)), 6)
        self.assertTrue(all(not row["需核查"] for row in benchmark.comparison_rows(self.normal, result)))
        for change in [{"premise_issue": ISSUE}, {"confidence": "低"}, {"is_sufficient": False},
                       {"should_follow_up": True, "follow_up_question": "无必要追问"}]:
            self.assertTrue(any(row["需核查"] for row in benchmark.comparison_rows(self.normal, {**result, **change})))
        for field, value in [("answer_quote", "学生没有说过的原文"), ("status", "理解错误")]:
            changed = copy.deepcopy(result)
            changed["point_assessments"][0][field] = value
            rows = benchmark.comparison_rows(self.normal, changed)
            self.assertEqual(rows[4]["是否一致"], "否")
            self.assertTrue(rows[4]["需核查"])

    def test_four_results_have_twenty_conditions_and_preserve_previous_runs_outside_database(self):
        old = benchmark.create_run(self.path, {"model": "offline"}, 1, "focused")
        run = benchmark.create_run(self.path, {"model": "offline"}, 2, SCOPE)
        for item in run["plan"]:
            run["results"][item["id"]] = {"evaluation": evaluated(item)}
        benchmark.save_run(self.path, run)
        restored = benchmark.load_latest(self.path)
        self.assertEqual(restored["fixture_version"], "dijkstra_premise_comparison_v1")
        self.assertEqual(len(benchmark.rows_for(restored)), 20)
        self.assertEqual(len(benchmark.stability_rows(restored)), 10)
        self.assertTrue((benchmark.folder(self.path) / (old["id"] + ".json")).exists())
        self.assertFalse(self.path.exists())
        self.assertTrue(all(row["是否一致"] == "是" for row in benchmark.rows_for(restored)))

    def test_max_inf_reference_executes_unreachable_zero_cycle_and_long_distance_cases(self):
        samples = [
            {"input": "4 2 1 4\n1 2 5\n3 4 1\n", "expected_output": "-1\n", "count_only": True},
            {"input": "4 4 1 4\n1 2 0\n2 1 0\n2 3 7\n3 4 0\n", "expected_output": "7\n", "count_only": True},
        ]
        n = 2000
        samples.append({"input": f"{n} {n-1} 1 {n}\n" + "\n".join(f"{i} {i+1} 1000000000" for i in range(1, n)) + "\n",
                        "expected_output": "1999000000000\n", "count_only": True})
        with patch.dict(os.environ, {"CODE_EXECUTION_ENABLED": "true", "CODE_EXECUTION_BACKEND": "local",
                                    "CXX_COMPILER": r"D:\dev-c++\Dev-Cpp\MinGW64\bin\g++.exe"}):
            code_verifier.reset_execution_backend()
            try:
                result = code_verifier.verify_cpp_code(self.bad["code"], samples, cpp_standard="c++14")
                self.assertEqual(result["overall_status"], "全部通过", json.dumps(result, ensure_ascii=False))
                self.assertEqual(result["passed"], 3)
            finally:
                code_verifier.reset_execution_backend()


class DijkstraPremisePageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        self.path = Path(self.temp.name) / "isolated.db"
        db.DB_PATH = self.path
        db.init_db()
        self.plan = benchmark.build_plan(2, SCOPE)
        self.app = AppTest.from_file(str(ROOT / "app.py"))
        authenticate(self.app, "管理员")
        self.app.session_state["current_page"] = "账号与系统管理"
        self.app.run(timeout=20)
        next(c for c in self.app.checkbox if c.label == "显示高级诊断").check().run(timeout=20)
        self.app.selectbox(key="benchmark_scope").set_value(SCOPE).run(timeout=20)
        self.app.selectbox(key="benchmark_repeats").set_value(2).run(timeout=20)
        self.app.button(key="benchmark_new").click().run(timeout=20)

    def tearDown(self):
        db.DB_PATH = self.old_path
        self.temp.cleanup()

    def service(self, problem, code, question, answer):
        item = next(x for x in self.plan if x["question"] == question)
        return evaluated(item)

    def run_button(self, callback):
        with patch.object(llm, "is_configured", return_value=True), patch.object(llm, "evaluate_initial_answer", side_effect=callback):
            self.app.button(key="benchmark_run").click().run(timeout=20)
        self.assertFalse(self.app.exception)

    def test_page_runs_four_evaluations_and_shows_controls_without_creating_student_tasks(self):
        before = db.list_teacher_tasks()
        self.run_button(self.service)
        run = benchmark.load_latest(self.path)
        self.assertEqual(len(run["results"]), 4)
        texts = [x.value for x in self.app.markdown] + [x.value for x in self.app.caption]
        self.assertTrue(any("自动验收条件一致：20/20" in x for x in texts))
        self.assertTrue(any("已保存 4/4 条真实评价" in x for x in texts))
        self.assertTrue(any("人工核对要点" in x for x in texts))
        self.assertTrue(any("前提说明与反馈" in x for x in texts))
        self.assertEqual(db.list_teacher_tasks(), before)
        self.assertEqual(db.list_submissions(), [])

    def test_interrupted_run_continues_remaining_items_without_repeating_saved_result(self):
        calls = []
        def interrupted(*args):
            calls.append(args[2]["question"])
            if len(calls) == 2:
                raise llm.LLMServiceError("合成中断")
            return self.service(*args)
        self.run_button(interrupted)
        run = benchmark.load_latest(self.path)
        self.assertEqual(len(run["results"]), 1)
        self.assertEqual(run["failed_item"], run["plan"][1]["id"])
        resumed = []
        def remaining(*args):
            resumed.append(args[2]["question"])
            return self.service(*args)
        self.run_button(remaining)
        run = benchmark.load_latest(self.path)
        self.assertEqual(len(resumed), 3)
        self.assertEqual(len(run["results"]), 4)
        self.assertEqual(run["failed_item"], "")
        self.assertEqual(len(benchmark.rows_for(run)), 20)
