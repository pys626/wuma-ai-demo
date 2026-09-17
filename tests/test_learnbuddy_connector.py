import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import database as db


class LearnBuddyConnectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = db.DB_PATH
        db.DB_PATH = Path(self.temp.name) / "wuma_ai.db"
        db.init_db()
        self.submission = db.create_submission_with_questions(
            "20260001", "张同学", "数组最大值", "思路", "SECRET_CODE",
            "2026-09-16 10:00", [{"dimension": "逻辑", "question": "为什么？", "reference_points": ["真实元素初始化"]}],
            code_verification={"overall_status": "全部通过", "passed": 1, "total": 1, "score": 100, "max_score": 100,
                               "cases": [{"input": "SECRET_INPUT", "expected_output": "SECRET_OUTPUT"}]},
        )
        db.save_report(self.submission, {"overall": 90, "level": "理解充分", "weakest": "逻辑",
                       "summary": "总结", "suggestion": "建议", "dimensions": {"逻辑": 90},
                       "review_required": False, "review_reasons": []}, "2026-09-16 10:01")

    def tearDown(self):
        db.DB_PATH = self.original
        self.temp.cleanup()

    def call_server(self, messages):
        script = Path(__file__).resolve().parents[1] / "learnbuddy" / "mcp_server.py"
        env = dict(os.environ, WUMA_AI_DB_PATH=str(db.DB_PATH))
        process = subprocess.run([sys.executable, str(script)], input="\n".join(json.dumps(x) for x in messages) + "\n",
                                 text=True, capture_output=True, env=env, check=True, timeout=10)
        return [json.loads(line) for line in process.stdout.splitlines()]

    def test_initialize_lists_four_read_only_tools(self):
        replies = self.call_server([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ])
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "wuma-ai-learnbuddy")
        self.assertEqual(len(replies[1]["result"]["tools"]), 4)
        self.assertTrue(all(tool["name"].startswith("wuma_") for tool in replies[1]["result"]["tools"]))

        skill_text = (Path(__file__).resolve().parents[1] / "learnbuddy" / "wuma-ai-teacher" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("只生成一次最终回答", skill_text)
        self.assertIn("普通概况请求不要再重复调用学生诊断", skill_text)
        self.assertIn("列表为空就明确回答", skill_text)
        self.assertIn("不得把“答辩理解度”改写成“综合掌握度”", skill_text)
        self.assertIn("不增加“补充说明”", skill_text)

    def test_skill_and_setup_describe_platform_model_connector_boundaries(self):
        root = Path(__file__).resolve().parents[1]
        skill_text = (
            root / "learnbuddy" / "wuma-ai-teacher" / "SKILL.md"
        ).read_text(encoding="utf-8")
        setup_text = (
            root / "learnbuddy" / "LEARNBUDDY_SETUP.md"
        ).read_text(encoding="utf-8")

        self.assertIn("LearnBuddy是交互载体", skill_text)
        self.assertIn("平台配置", skill_text)
        self.assertIn("平台底部显示哪个可用模型不影响", setup_text)
        self.assertIn("赛事要求体现为最终操作与结果均在LearnBuddy中呈现", setup_text)
        self.assertNotIn("选择平台提供的腾讯官方模型", setup_text)

    def test_diagnosis_is_structured_and_does_not_leak_private_evidence(self):
        response = self.call_server([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "wuma_student_diagnosis", "arguments": {"submission_id": self.submission}}}])[0]
        text = response["result"]["content"][0]["text"]
        self.assertIn("隐私说明", text)
        for secret in ["SECRET_CODE", "SECRET_INPUT", "SECRET_OUTPUT", "真实元素初始化"]:
            self.assertNotIn(secret, text)

    def test_unknown_submission_returns_tool_error_without_crashing_server(self):
        response = self.call_server([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "wuma_student_diagnosis", "arguments": {"submission_id": 999}}}])[0]
        self.assertTrue(response["result"]["isError"])

    def test_overview_pending_count_and_latest_summary_use_same_evidence_rule(self):
        evaluation = {"score": 85, "confidence": "高", "missing_points": ["没有解释边界"],
                      "point_assessments": [{"reference_point": "真实元素初始化", "status": "部分掌握", "evidence": "说明不完整"}]}
        db.save_initial_result(self.submission, 0, "回答", "", evaluation, True)
        response = self.call_server([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "wuma_teacher_overview", "arguments": {}}}])[0]
        result = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(result["教师待办总数"], 1)
        self.assertEqual(result["待教师复核"], 1)
        self.assertEqual(result["最新提交摘要"]["提交"], self.submission)
        self.assertEqual(result["最新提交摘要"]["教师复核状态"], "待复核")

        tasks = self.call_server([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "wuma_pending_teacher_tasks", "arguments": {}}}])[0]
        task_rows = json.loads(tasks["result"]["content"][0]["text"])
        self.assertEqual(len(task_rows), result["教师待办总数"])
        self.assertEqual(task_rows[0]["submission_id"], self.submission)

    def test_mastered_submission_is_regular_spot_check_not_pending(self):
        evaluation = {"score": 100, "confidence": "高", "point_assessments": [
            {"reference_point": "真实元素初始化", "status": "掌握", "evidence": "说明完整"}]}
        db.save_initial_result(self.submission, 0, "完整回答", "", evaluation, True)
        diagnosis = self.call_server([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "wuma_student_diagnosis", "arguments": {"submission_id": self.submission}}}])[0]
        result = json.loads(diagnosis["result"]["content"][0]["text"])
        self.assertEqual(result["教师复核"]["状态"], "常规抽查")

    def test_review_state_is_consistent_before_and_after_teacher_review(self):
        evaluation = {
            "score": 82,
            "confidence": "高",
            "missing_points": ["未解释边界"],
            "point_assessments": [{
                "reference_point": "真实元素初始化",
                "status": "部分掌握",
                "evidence": "说明不完整",
            }],
        }
        db.save_initial_result(self.submission, 0, "回答", "", evaluation, True)

        def result_of(name, arguments=None):
            response = self.call_server([{
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }])[0]
            return json.loads(response["result"]["content"][0]["text"])

        overview = result_of("wuma_teacher_overview")
        diagnosis = result_of("wuma_student_diagnosis", {"submission_id": self.submission})
        progress = result_of("wuma_student_progress", {"student_id": "20260001"})
        self.assertEqual(overview["教师待办总数"], 1)
        self.assertEqual(overview["最新提交摘要"]["教师复核状态"], "待复核")
        self.assertTrue(overview["最新提交摘要"]["是否当前待办"])
        self.assertEqual(diagnosis["教师复核"]["状态"], "待复核")
        self.assertEqual(progress["提交记录"][0]["教师复核状态"], "待复核")
        self.assertIn("评分点证据覆盖度", diagnosis)
        self.assertIn("评分点证据覆盖度", progress["提交记录"][0])

        db.save_teacher_review(
            self.submission, "任课教师", "认可AI诊断", 90, "", "2026-09-16 10:10"
        )
        overview = result_of("wuma_teacher_overview")
        diagnosis = result_of("wuma_student_diagnosis", {"submission_id": self.submission})
        progress = result_of("wuma_student_progress", {"student_id": "20260001"})
        pending = result_of("wuma_pending_teacher_tasks")
        completed = db.list_teacher_tasks(include_completed=True)

        self.assertEqual(overview["教师待办总数"], 0)
        self.assertEqual(overview["最新提交摘要"]["教师复核状态"], "已复核")
        self.assertFalse(overview["最新提交摘要"]["是否当前待办"])
        self.assertEqual(diagnosis["教师复核"]["状态"], "已复核")
        self.assertFalse(diagnosis["教师复核"]["是否当前待办"])
        self.assertEqual(progress["提交记录"][0]["教师复核状态"], "已复核")
        self.assertFalse(progress["提交记录"][0]["是否当前待办"])
        self.assertEqual(pending, [])
        self.assertTrue(any(
            item["submission_id"] == self.submission and item["status"] == "已完成"
            for item in completed
        ))
