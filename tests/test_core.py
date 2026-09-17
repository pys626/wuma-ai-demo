import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


try:
    import dotenv  # noqa: F401
    import openai  # noqa: F401
except ModuleNotFoundError:
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv_stub

    openai_stub = types.ModuleType("openai")

    class OpenAIError(Exception):
        pass

    class OpenAIStub:
        def __init__(self, *args, **kwargs):
            pass

    openai_stub.APIConnectionError = OpenAIError
    openai_stub.APIStatusError = OpenAIError
    openai_stub.APITimeoutError = OpenAIError
    openai_stub.AuthenticationError = OpenAIError
    openai_stub.OpenAI = OpenAIStub
    openai_stub.RateLimitError = OpenAIError
    sys.modules["openai"] = openai_stub

import database as db
import llm_service as llm


QUESTIONS = [
    {
        "dimension": "程序逻辑理解",
        "question": "为什么max_value使用arr[0]初始化？",
        "reference_points": ["初值来自真实元素", "兼容全部为负数"],
    },
    {
        "dimension": "关键概念与边界",
        "question": "当n大于100时有什么风险？",
        "reference_points": ["数组越界", "需要限制n或使用vector"],
    },
    {
        "dimension": "分析与修改能力",
        "question": "如何修改为寻找最小值？",
        "reference_points": ["修改变量含义", "修改比较方向"],
    },
]


class ScoringTests(unittest.TestCase):
    def test_learning_hint_uses_only_aggregate_verification_evidence(self):
        captured = {}
        fake_result = {
            "diagnosis": "检查循环边界。",
            "guidance": "对照有效下标范围逐步检查循环。",
            "self_check": ["循环从哪里开始？", "最后访问哪个下标？"],
        }

        def fake_chat(messages, max_tokens=0):
            captured["messages"] = messages
            return fake_result

        verification = {
            "overall_status": "部分通过",
            "passed": 1,
            "total": 2,
            "score": 50,
            "max_score": 100,
            "compile": {"status": "成功", "message": "编译成功。"},
            "cases": [
                {
                    "name": "SECRET_CASE_NAME",
                    "status": "未通过",
                    "input": "SECRET_INPUT",
                    "expected_output": "SECRET_EXPECTED",
                    "actual_output": "SECRET_ACTUAL",
                }
            ],
        }
        with patch.object(llm, "_chat_json", side_effect=fake_chat):
            result = llm.generate_learning_hint(
                "数组处理",
                ["正确处理边界"],
                "int main(){ return 0; }",
                "使用循环。",
                verification,
                1,
                2,
            )

        prompt = str(captured["messages"])
        self.assertNotIn("SECRET_CASE_NAME", prompt)
        self.assertNotIn("SECRET_INPUT", prompt)
        self.assertNotIn("SECRET_EXPECTED", prompt)
        self.assertNotIn("SECRET_ACTUAL", prompt)
        self.assertEqual(result["diagnosis"], "检查循环边界。")

    def test_good_answer_does_not_trigger_follow_up(self):
        fake_result = {
            "score": 95,
            "point_assessments": [
                {"status": "掌握", "evidence": "初值来自arr[0]"},
                {"status": "掌握", "evidence": "说明全部负数仍然正确"},
            ],
            "feedback": "结论正确，并结合代码解释了原因。",
            "missing_points": [],
            "misconceptions": [],
            "confidence": "高",
            "follow_up_question": "",
        }
        with patch.object(llm, "_chat_json", return_value=fake_result):
            result = llm.evaluate_initial_answer(
                "求最大值",
                "int max_value = arr[0];",
                QUESTIONS[0],
                "初值来自数组，全部负数时也能正确比较。",
            )

        self.assertGreaterEqual(result["score"], 90)
        self.assertFalse(result["should_follow_up"])
        self.assertEqual(result["point_assessments"][0]["status"], "掌握")

    def test_incomplete_answer_triggers_follow_up(self):
        fake_result = {
            "score": 70,
            "point_assessments": [
                {"status": "部分掌握", "evidence": "说到了记录最大值"},
                {"status": "未体现", "evidence": "未提及负数"},
            ],
            "feedback": "只说明了用途，没有解释负数场景。",
            "missing_points": ["没有分析全部为负数的情况"],
            "misconceptions": [],
            "confidence": "高",
            "follow_up_question": "如果数组全部为负数，初始化为0会怎样？",
        }
        with patch.object(llm, "_chat_json", return_value=fake_result):
            result = llm.evaluate_initial_answer(
                "求最大值",
                "int max_value = arr[0];",
                QUESTIONS[0],
                "为了记录最大值。",
            )

        self.assertLess(result["score"], 80)
        self.assertTrue(result["should_follow_up"])
        self.assertIn("全部为负数", "；".join(result["missing_points"]))

    def test_rubric_preliminary_review_is_calibrated_from_criterion_scores(self):
        fake_result = {
            "summary": "代码主体完整，但边界验证和修改说明仍需答辩核验。",
            "criteria": [
                {
                    "status": "满足",
                    "score": 25,
                    "code_evidence": "存在遍历数组的for循环",
                    "report_evidence": "报告说明了遍历过程",
                    "missing": "无",
                    "confidence": "高",
                    "needs_defense": False,
                    "defense_focus": "说明循环范围",
                },
                {
                    "status": "满足",
                    "score": 22,
                    "code_evidence": "max_value由arr[0]初始化",
                    "report_evidence": "说明初值来自首元素",
                    "missing": "需要口头解释全负数情况",
                    "confidence": "高",
                    "needs_defense": False,
                    "defense_focus": "说明初始化依据",
                },
                {
                    "status": "部分满足",
                    "score": 15,
                    "code_evidence": "未检查n的有效范围",
                    "report_evidence": "提到全负数但未分析空输入",
                    "missing": "空输入和容量检查",
                    "confidence": "中",
                    "needs_defense": True,
                    "defense_focus": "核验n为0和超过容量时的处理",
                },
                {
                    "status": "无法判断",
                    "score": 8,
                    "code_evidence": "代码未体现修改任务",
                    "report_evidence": "报告未说明如何改为最小值",
                    "missing": "修改方案",
                    "confidence": "低",
                    "needs_defense": False,
                    "defense_focus": "说明如何修改比较逻辑",
                },
            ],
        }
        with patch.object(llm, "_chat_json", return_value=fake_result):
            result = llm.preliminary_review(
                "求数组中的最大值",
                ["使用数组保存数据"],
                "int max_value = arr[0];",
                "循环比较",
                "测试了正数和负数输入。",
                db.DEFAULT_RUBRIC,
            )

        self.assertEqual(result["completion_score"], 70)
        self.assertTrue(result["criteria"][1]["needs_defense"])
        self.assertTrue(result["criteria"][3]["needs_defense"])
        self.assertEqual(result["criteria"][2]["criterion_name"], "处理边界情况")

    def test_question_generation_preserves_teacher_evidence_reason(self):
        fake_questions = {
            "questions": [
                {
                    "question": "为什么max_value使用arr[0]初始化？",
                    "reference_points": ["来自真实元素", "适用于全负数"],
                    "reason": "评分点“最大值初始化合理”要求答辩核验。",
                },
                {
                    "question": "当n为0时访问arr[0]会怎样？",
                    "reference_points": ["越界访问", "输入校验"],
                    "reason": "初评缺少空输入证据。",
                },
                {
                    "question": "如何把程序改为求最小值？",
                    "reference_points": ["修改变量含义", "修改比较方向"],
                    "reason": "修改能力无法从静态提交判断。",
                },
            ]
        }
        with patch.object(llm, "_chat_json", return_value=fake_questions) as mocked:
            questions = llm.generate_questions(
                "求数组中的最大值",
                ["使用数组"],
                "int max_value = arr[0];",
                "循环比较",
                rubric=db.DEFAULT_RUBRIC,
                preliminary_review={"completion_score": 70},
                lab_report="测试了全负数输入。",
                constraints_text="1 ≤ n ≤ 100",
            )

        self.assertEqual(len(questions), 3)
        self.assertIn("初始化合理", questions[0]["reason"])
        prompt = mocked.call_args.args[0][1]["content"]
        self.assertIn("1 ≤ n ≤ 100", prompt)
        self.assertIn("范围之外的输入不得被描述为正确性缺陷", prompt)

    def test_code_verification_evidence_is_sent_to_ai_review(self):
        fake_result = {
            "summary": "结合运行结果完成初评。",
            "criteria": [
                {
                    "status": "满足",
                    "score": item["weight"],
                    "code_evidence": "代码与样例结果一致",
                    "report_evidence": "报告有说明",
                    "missing": "无",
                    "confidence": "高",
                    "needs_defense": False,
                    "defense_focus": "核验真实理解",
                }
                for item in db.DEFAULT_RUBRIC
            ],
        }
        verification = {
            "overall_status": "部分通过",
            "passed": 2,
            "total": 3,
            "cases": [{"name": "全部负数", "status": "未通过"}],
        }
        with patch.object(llm, "_chat_json", return_value=fake_result) as mocked:
            llm.preliminary_review(
                "求数组中的最大值",
                ["使用数组"],
                "int main() { return 0; }",
                "循环比较",
                "测试了正数和负数。",
                db.DEFAULT_RUBRIC,
                code_verification=verification,
            )
        prompt = mocked.call_args.args[0][1]["content"]
        self.assertIn('"overall_status": "部分通过"', prompt)
        self.assertIn('"name": "全部负数"', prompt)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_folder = tempfile.TemporaryDirectory()
        self.original_path = db.DB_PATH
        db.DB_PATH = Path(self.temp_folder.name) / "wuma_ai.db"

    def tearDown(self):
        db.DB_PATH = self.original_path
        self.temp_folder.cleanup()

    def test_v03_report_table_is_migrated(self):
        with closing(sqlite3.connect(db.DB_PATH)) as connection:
            connection.execute(
                """
                CREATE TABLE reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    submission_id INTEGER NOT NULL UNIQUE,
                    overall INTEGER NOT NULL,
                    level TEXT NOT NULL,
                    weakest TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    suggestion TEXT NOT NULL,
                    dimensions_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO reports (
                    submission_id, overall, level, weakest,
                    summary, suggestion, dimensions_json, created_at
                )
                VALUES (
                    1, 69, '需要巩固', '分析与修改能力',
                    '历史报告', '历史建议', '{}', '2026-09-05 11:00'
                )
                """
            )
            connection.commit()

        db.init_db()
        with db.get_connection() as connection:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(reports)")
            }
            review_table = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name = 'teacher_reviews'
                """
            ).fetchone()
            feedback_table = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name = 'student_feedbacks'
                """
            ).fetchone()
            redefense_table = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name = 'redefense_requests'
                """
            ).fetchone()
            feedback_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(student_feedbacks)"
                )
            }

        self.assertIn("review_required", columns)
        self.assertIn("review_reasons_json", columns)
        self.assertIsNotNone(review_table)
        self.assertIsNotNone(feedback_table)
        self.assertIsNotNone(redefense_table)
        self.assertIn("student_viewed_at", feedback_columns)
        migrated_report = db.get_report(1)
        self.assertTrue(migrated_report["review_required"])
        self.assertIn("低于70", migrated_report["review_reasons"][0])

    def test_v04_submission_table_is_migrated(self):
        with closing(sqlite3.connect(db.DB_PATH)) as connection:
            connection.execute(
                """
                CREATE TABLE submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    problem TEXT NOT NULL,
                    explanation TEXT NOT NULL,
                    code TEXT NOT NULL,
                    submitted_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT '答辩中'
                )
                """
            )
            connection.commit()

        db.init_db()
        with db.get_connection() as connection:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(submissions)")
            }

        self.assertIn("assignment_id", columns)
        self.assertIn("assignment_snapshot_json", columns)
        self.assertIn("parent_submission_id", columns)
        self.assertIn("attempt_number", columns)
        self.assertIn("code_verification_json", columns)

    def test_v08_feedback_table_is_migrated(self):
        with closing(sqlite3.connect(db.DB_PATH)) as connection:
            connection.execute(
                """
                CREATE TABLE student_feedbacks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    submission_id INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    reply_requested INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT '待处理',
                    created_at TEXT NOT NULL,
                    teacher_reply TEXT NOT NULL DEFAULT '',
                    replied_by TEXT NOT NULL DEFAULT '',
                    replied_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.commit()

        db.init_db()
        with db.get_connection() as connection:
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(student_feedbacks)"
                )
            }
        self.assertIn("student_viewed_at", columns)

    def test_v082_assignment_is_migrated_with_default_rubric(self):
        with closing(sqlite3.connect(db.DB_PATH)) as connection:
            connection.execute(
                """
                CREATE TABLE assignments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    requirements_json TEXT NOT NULL DEFAULT '[]',
                    teaching_focus_json TEXT NOT NULL DEFAULT '[]',
                    starter_code TEXT NOT NULL DEFAULT '',
                    language TEXT NOT NULL DEFAULT 'C++',
                    status TEXT NOT NULL DEFAULT '已发布',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO assignments (
                    title, description, requirements_json,
                    teaching_focus_json, starter_code,
                    language, status, created_at, updated_at
                ) VALUES (?, ?, '[]', '[]', '', 'C++', '已发布', ?, ?)
                """,
                ("旧版实验", "用于验证评分点迁移的旧实验。", "2026-09-09", "2026-09-09"),
            )
            connection.commit()

        db.init_db()
        assignment = db.get_assignment(1)
        with db.get_connection() as connection:
            assignment_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(assignments)")
            }
            preliminary_table = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'preliminary_reviews'
                """
            ).fetchone()

        self.assertIn("rubric_json", assignment_columns)
        self.assertIn("verification_enabled", assignment_columns)
        self.assertIn("test_cases_json", assignment_columns)
        self.assertIn("input_format", assignment_columns)
        self.assertIn("output_format", assignment_columns)
        self.assertIn("constraints_text", assignment_columns)
        self.assertIn("public_samples_json", assignment_columns)
        self.assertIn("cpp_standard", assignment_columns)
        self.assertIn("hint_limit", assignment_columns)
        self.assertIn("defense_admission", assignment_columns)
        self.assertIn("defense_score_threshold", assignment_columns)
        self.assertIn("reference_code", assignment_columns)
        self.assertIn("rubric_reviewed", assignment_columns)
        self.assertEqual(assignment["cpp_standard"], "auto")
        self.assertEqual(assignment["hint_limit"], 2)
        self.assertEqual(assignment["defense_admission"], "全部通过")
        self.assertEqual(sum(item["weight"] for item in assignment["rubric"]), 100)
        self.assertIsNotNone(preliminary_table)

    def test_assignment_management_and_submission_snapshot(self):
        db.init_db()
        assignment_id = db.create_assignment(
            "判断回文字符串",
            "输入一个字符串，判断它是否为回文字符串。",
            ["使用字符串保存输入", "使用循环比较字符"],
            ["循环边界", "字符串下标安全"],
            "int main() { return 0; }",
        )
        assignment = db.get_assignment(assignment_id)
        self.assertEqual(assignment["status"], "已发布")
        self.assertEqual(len(assignment["requirements"]), 2)

        db.update_assignment(
            assignment_id,
            "判断回文",
            "输入一个字符串，输出它是否为回文字符串。",
            ["使用string", "从两端比较"],
            ["循环终止条件"],
            "int main() { return 0; }",
        )
        assignment = db.get_assignment(assignment_id)
        self.assertEqual(assignment["title"], "判断回文")

        submission_id = db.create_submission_with_questions(
            "20260002",
            "任务测试同学",
            assignment["title"],
            "使用双指针从两端比较。",
            "int main() { return 0; }",
            "2026-09-05 13:00",
            QUESTIONS,
            assignment,
        )
        db.update_assignment(
            assignment_id,
            "判断回文（新版）",
            "这是教师在学生提交后修改的新版本描述。",
            ["新版本要求"],
            ["新版本重点"],
            "int main() { return 0; }",
        )
        db.set_assignment_status(assignment_id, "已停用")

        saved_submission = db.get_submission(submission_id)
        saved_assignment = db.get_assignment(assignment_id)
        filtered_records = db.list_submissions(assignment_id)
        self.assertEqual(saved_assignment["status"], "已停用")
        self.assertEqual(saved_assignment["title"], "判断回文（新版）")
        self.assertEqual(len(filtered_records), 1)
        self.assertEqual(filtered_records[0]["id"], submission_id)
        self.assertEqual(
            saved_submission["assignment_snapshot"]["title"],
            "判断回文",
        )
        self.assertEqual(
            saved_submission["assignment_snapshot"]["requirements"],
            ["使用string", "从两端比较"],
        )

    def test_default_assignment_is_seeded(self):
        db.init_db()
        assignments = db.list_assignments(published_only=True)
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]["title"], "求数组中的最大值")
        self.assertEqual(sum(item["weight"] for item in assignments[0]["rubric"]), 100)
        self.assertTrue(assignments[0]["verification_enabled"])
        self.assertEqual(len(assignments[0]["test_cases"]), 3)
        self.assertEqual(len(assignments[0]["public_samples"]), 1)
        self.assertIn("第一行", assignments[0]["input_format"])
        self.assertEqual(assignments[0]["hint_limit"], 2)
        self.assertEqual(assignments[0]["defense_admission"], "全部通过")
        self.assertTrue(assignments[0]["rubric_reviewed"])

    def test_hint_limit_and_defense_policy_are_saved_and_enforced(self):
        db.init_db()
        assignment_id = db.create_assignment(
            "限次提示实验",
            "验证提示次数和答辩准入。",
            ["完成指定计算"],
            ["边界处理"],
            "int main() { return 0; }",
            verification_enabled=True,
            test_cases=[
                {"name": "测试1", "input": "1\n", "expected_output": "1\n"}
            ],
            hint_limit=1,
            defense_admission="达到指定分数",
            defense_score_threshold=80,
            reference_code="int main() { return 0; }",
        )
        assignment = db.get_assignment(assignment_id)
        self.assertEqual(assignment["hint_limit"], 1)
        self.assertEqual(assignment["defense_admission"], "达到指定分数")
        self.assertEqual(assignment["defense_score_threshold"], 80)
        hint = {
            "diagnosis": "检查输入。",
            "guidance": "确认读取数量。",
            "self_check": ["读了几个值？", "变量是否初始化？"],
        }
        db.save_ai_hint(
            assignment_id,
            "20260020",
            "提示测试同学",
            "int main(){}",
            "答案错误",
            hint,
            1,
        )
        self.assertEqual(db.count_ai_hints(assignment_id, "20260020"), 1)
        self.assertEqual(
            db.list_ai_hints(assignment_id, "20260020")[0]["hint"],
            hint,
        )
        with self.assertRaisesRegex(ValueError, "已经全部使用"):
            db.save_ai_hint(
                assignment_id,
                "20260020",
                "提示测试同学",
                "int main(){}",
                "答案错误",
                hint,
                1,
            )

    def test_public_oj_statement_is_saved_and_frozen_with_submission(self):
        db.init_db()
        assignment_id = db.create_assignment(
            "两数求和",
            "输入两个整数并输出它们的和。",
            ["正确读取两个整数"],
            ["输入输出关系"],
            "int main() { return 0; }",
            input_format="输入两个整数a和b。",
            output_format="输出a+b。",
            constraints_text="-1000 ≤ a,b ≤ 1000",
            public_samples=[
                {
                    "name": "基础样例",
                    "input": "2 3\n",
                    "output": "5\n",
                    "explanation": "2与3的和为5。",
                }
            ],
            cpp_standard="c++14",
        )
        assignment = db.get_assignment(assignment_id)
        submission_id = db.create_submission_with_questions(
            "20260012",
            "样例同学",
            assignment["title"],
            "读取并相加。",
            "int main() { return 0; }",
            "2026-09-10 18:00",
            QUESTIONS,
            assignment,
        )
        db.update_assignment(
            assignment_id,
            "两数求和（新版）",
            "修改后的题目。",
            ["新要求"],
            ["新重点"],
            "int main() { return 0; }",
            public_samples=[],
        )
        saved = db.get_submission(submission_id)["assignment_snapshot"]
        self.assertEqual(saved["input_format"], "输入两个整数a和b。")
        self.assertEqual(saved["public_samples"][0]["output"], "5\n")
        self.assertEqual(saved["cpp_standard"], "c++14")

    def test_public_samples_allow_empty_list_and_validate_output(self):
        self.assertEqual(db.normalize_public_samples([]), [])
        with self.assertRaisesRegex(ValueError, "样例输出不能为空"):
            db.normalize_public_samples(
                [{"name": "无输出", "input": "1", "output": ""}]
            )

    def test_public_samples_repair_single_item_list_strings(self):
        samples = db.normalize_public_samples(
            [
                {
                    "name": "['示例1']",
                    "input": "['8\\n1 2 2 3 4 1 5 6']",
                    "output": "['3']",
                    "explanation": "['最长长度为3']",
                }
            ]
        )
        self.assertEqual(samples[0]["name"], "示例1")
        self.assertEqual(samples[0]["input"], "8\n1 2 2 3 4 1 5 6")
        self.assertEqual(samples[0]["output"], "3")

    def test_cpp_standard_validation(self):
        self.assertEqual(db.normalize_cpp_standard("C++17"), "c++17")
        with self.assertRaisesRegex(ValueError, r"C\+\+编译标准"):
            db.normalize_cpp_standard("c++20")

    def test_test_cases_are_validated_and_frozen_with_submission(self):
        db.init_db()
        assignment_id = db.create_assignment(
            "验证样例实验",
            "验证测试样例保存和快照。",
            ["输出输入整数的两倍"],
            ["输入输出关系"],
            "int main() { return 0; }",
            verification_enabled=True,
            test_cases=[
                {"name": "普通输入", "input": "2\n", "expected_output": "4\n"}
            ],
            reference_code="int main() { return 0; }",
        )
        assignment = db.get_assignment(assignment_id)
        self.assertEqual(assignment["test_cases"][0]["group"], "基础功能")
        self.assertEqual(assignment["test_cases"][0]["weight"], 100)
        verification = {
            "enabled": True,
            "overall_status": "全部通过",
            "passed": 1,
            "total": 1,
            "compile": {"status": "成功", "message": "编译成功。"},
            "cases": [{"name": "普通输入", "status": "通过"}],
        }
        submission_id = db.create_submission_with_questions(
            "20260011",
            "验证同学",
            assignment["title"],
            "读取后乘二。",
            "int main() { return 0; }",
            "2026-09-10 13:00",
            QUESTIONS,
            assignment,
            code_verification=verification,
        )
        db.update_assignment(
            assignment_id,
            "验证样例实验（新版）",
            "教师已经修改实验。",
            ["新要求"],
            ["新重点"],
            "int main() { return 0; }",
            verification_enabled=False,
        )
        saved = db.get_submission(submission_id)
        self.assertTrue(saved["assignment_snapshot"]["verification_enabled"])
        self.assertEqual(
            saved["assignment_snapshot"]["test_cases"][0]["expected_output"],
            "4\n",
        )
        self.assertEqual(
            saved["assignment_snapshot"]["test_cases"][0]["weight"],
            100,
        )
        self.assertEqual(saved["code_verification"]["overall_status"], "全部通过")

    def test_invalid_test_cases_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "预期输出不能为空"):
            db.normalize_test_cases(
                [{"name": "空结果", "input": "1", "expected_output": ""}]
            )

    def test_test_case_weights_are_validated_and_auto_distributed(self):
        automatic = db.normalize_test_cases(
            [
                {"name": "测试1", "input": "1", "expected_output": "1"},
                {"name": "测试2", "input": "2", "expected_output": "2"},
            ]
        )
        self.assertEqual([item["weight"] for item in automatic], [50, 50])
        self.assertTrue(all(item["group"] == "基础功能" for item in automatic))

        mixed = db.normalize_test_cases(
            [
                {
                    "name": "基础",
                    "group": "基础功能",
                    "weight": 60,
                    "input": "1",
                    "expected_output": "1",
                },
                {
                    "name": "边界",
                    "group": "边界情况",
                    "input": "0",
                    "expected_output": "0",
                },
            ]
        )
        self.assertEqual([item["weight"] for item in mixed], [60, 40])
        self.assertEqual(mixed[1]["group"], "边界情况")

        with self.assertRaisesRegex(ValueError, "分值合计必须为100"):
            db.normalize_test_cases(
                [
                    {
                        "name": "测试1",
                        "weight": 60,
                        "input": "1",
                        "expected_output": "1",
                    },
                    {
                        "name": "测试2",
                        "weight": 30,
                        "input": "2",
                        "expected_output": "2",
                    },
                ]
            )

    def test_rubric_validation_rejects_invalid_total(self):
        invalid = [dict(item) for item in db.DEFAULT_RUBRIC]
        invalid[0]["weight"] = 20
        with self.assertRaisesRegex(ValueError, "权重合计必须为100"):
            db.normalize_rubric(invalid)

    def test_rubric_templates_are_valid_and_return_independent_copies(self):
        for name in db.RUBRIC_TEMPLATES:
            template = db.get_rubric_template(name)
            normalized = db.normalize_rubric(template)
            self.assertEqual(sum(item["weight"] for item in normalized), 100)
        first = db.get_rubric_template("算法实验")
        first[0]["name"] = "已修改"
        second = db.get_rubric_template("算法实验")
        self.assertNotEqual(first[0]["name"], second[0]["name"])

    def test_unrelated_default_rubric_requires_explicit_review(self):
        warnings = db.assignment_quality_warnings("区间第k小值", db.DEFAULT_RUBRIC)
        self.assertEqual(len(warnings), 1)
        self.assertIn("最大值", warnings[0])
        self.assertEqual(
            db.assignment_quality_warnings(
                db.DEFAULT_ASSIGNMENT["title"],
                db.DEFAULT_RUBRIC,
            ),
            [],
        )

    def test_large_hidden_case_text_is_not_truncated_at_two_thousand_chars(self):
        large_input = "1 " * 5000
        large_output = "2 " * 5000
        normalized = db.normalize_test_cases(
            [
                {
                    "name": "大数据",
                    "input": large_input,
                    "expected_output": large_output,
                }
            ]
        )
        self.assertEqual(normalized[0]["input"], large_input)
        self.assertEqual(normalized[0]["expected_output"], large_output)

    def test_reference_answer_is_required_but_not_copied_to_student_snapshot(self):
        db.init_db()
        with self.assertRaisesRegex(ValueError, "教师参考答案"):
            db.create_assignment(
                "需要参考答案",
                "验证发布门禁。",
                ["正确输出"],
                ["输出逻辑"],
                "int main(){}",
                verification_enabled=True,
                test_cases=[
                    {"name": "测试", "input": "1", "expected_output": "1"}
                ],
            )
        assignment = db.list_assignments()[0]
        snapshot = db._assignment_snapshot(assignment)
        self.assertNotIn("reference_code", snapshot)

    def test_short_nonempty_feedback_review_and_reply_are_allowed(self):
        db.init_db()
        submission_id = db.create_submission_with_questions(
            "20260010",
            "短输入同学",
            "求数组中的最大值",
            "想法",
            "int main() { return 0; }",
            "2026-09-10 10:00",
            QUESTIONS,
        )
        db.save_report(
            submission_id,
            {
                "overall": 80,
                "level": "基本理解",
                "weakest": "边界情况意识",
                "summary": "已完成。",
                "suggestion": "继续练习。",
                "dimensions": {
                    "程序逻辑理解": 80,
                    "关键概念掌握": 80,
                    "边界情况意识": 80,
                    "分析与修改能力": 80,
                },
                "review_required": False,
                "review_reasons": [],
            },
            "2026-09-10 10:05",
        )
        review_id = db.save_teacher_review(
            submission_id,
            "教师",
            "认可AI诊断",
            80,
            "好",
            "2026-09-10 10:06",
        )
        feedback_id = db.save_student_feedback(
            submission_id,
            "其他反馈",
            "好",
            True,
            "2026-09-10 10:07",
        )
        db.process_student_feedback(
            feedback_id,
            "教师",
            "已回复",
            "行",
            "2026-09-10 10:08",
        )
        self.assertIsInstance(review_id, int)
        feedback = db.list_student_feedbacks(submission_id=submission_id)[0]
        self.assertEqual(feedback["teacher_reply"], "行")

    def test_submission_saves_report_rubric_preliminary_evidence_and_reason(self):
        db.init_db()
        assignment = db.list_assignments(published_only=True)[0]
        questions = [dict(item) for item in QUESTIONS]
        questions[0]["reason"] = "评分点初始化依据需要核验。"
        preliminary = {
            "completion_score": 72,
            "summary": "主体功能完成，边界处理不足。",
            "criteria": [
                {
                    "criterion_name": "正确遍历数组",
                    "weight": 25,
                    "source": "代码",
                    "status": "满足",
                    "score": 25,
                    "code_evidence": "存在for循环",
                    "report_evidence": "说明了遍历",
                    "missing": "无",
                    "confidence": "高",
                    "needs_defense": False,
                    "defense_focus": "说明循环范围",
                }
            ],
        }
        submission_id = db.create_submission_with_questions(
            "20260009",
            "评分点测试同学",
            assignment["title"],
            "先初始化，再循环比较。",
            "int main() { return 0; }",
            "2026-09-10 09:00",
            questions,
            assignment,
            "我测试了正数、负数和数组长度边界。",
            preliminary,
        )

        submission = db.get_submission(submission_id)
        saved_review = db.get_preliminary_review(submission_id)
        saved_questions = db.get_qa_records(submission_id)
        summary = db.list_submissions()[0]
        self.assertIn("数组长度边界", submission["lab_report"])
        self.assertEqual(submission["rubric_snapshot"], assignment["rubric"])
        self.assertEqual(saved_review["completion_score"], 72)
        self.assertEqual(summary["completion_score"], 72)
        self.assertIn("初始化依据", saved_questions[0]["question_reason"])

    def test_full_records_are_saved(self):
        db.init_db()
        submission_id = db.create_submission_with_questions(
            "20260001",
            "测试同学",
            "求数组中的最大值",
            "先初始化，再循环比较。",
            "int main() { return 0; }",
            "2026-09-05 12:00",
            QUESTIONS,
        )
        evaluation = {
            "score": 58,
            "score_level": "需要巩固",
            "is_sufficient": False,
            "feedback": "存在遗漏。",
            "point_assessments": [],
            "missing_points": ["边界情况"],
            "misconceptions": [],
            "confidence": "中",
            "should_follow_up": True,
            "follow_up_question": "请说明边界情况。",
        }
        db.save_initial_result(
            submission_id,
            0,
            "首次回答",
            "请说明边界情况。",
            evaluation,
            is_final=False,
        )
        report = {
            "overall": 68,
            "level": "需要巩固",
            "weakest": "边界情况意识",
            "summary": "存在边界知识缺口。",
            "suggestion": "练习边界测试。",
            "dimensions": {
                "程序逻辑理解": 80,
                "关键概念掌握": 70,
                "边界情况意识": 52,
                "分析与修改能力": 70,
            },
            "review_required": True,
            "review_reasons": ["综合理解度低于70"],
        }
        db.save_report(submission_id, report, "2026-09-05 12:10")

        saved_report = db.get_report(submission_id)
        saved_submission = db.get_submission(submission_id)
        learning_records = db.list_learning_records(student_id="20260001")
        student_submissions = db.list_submissions(student_id="20260001")
        self.assertTrue(saved_report["review_required"])
        self.assertEqual(saved_report["review_reasons"], ["综合理解度低于70"])
        self.assertEqual(saved_submission["status"], "已完成")
        self.assertEqual(len(learning_records), 1)
        self.assertEqual(
            learning_records[0]["dimensions"]["边界情况意识"],
            52,
        )
        self.assertEqual(len(student_submissions), 1)

    def test_teacher_review_is_saved_and_history_is_preserved(self):
        db.init_db()
        submission_id = db.create_submission_with_questions(
            "20260003",
            "复核测试同学",
            "求数组中的最大值",
            "先初始化，再循环比较。",
            "int main() { return 0; }",
            "2026-09-09 20:00",
            QUESTIONS,
        )
        report = {
            "overall": 68,
            "level": "需要巩固",
            "weakest": "边界情况意识",
            "summary": "存在边界知识缺口。",
            "suggestion": "练习边界测试。",
            "dimensions": {
                "程序逻辑理解": 80,
                "关键概念掌握": 70,
                "边界情况意识": 52,
                "分析与修改能力": 70,
            },
            "review_required": True,
            "review_reasons": ["综合理解度低于70"],
        }
        db.save_report(submission_id, report, "2026-09-09 20:05")

        first_id = db.save_teacher_review(
            submission_id,
            "王老师",
            "认可AI诊断",
            68,
            "确认学生在边界处理方面仍有明显遗漏。",
            "2026-09-09 20:10",
        )
        second_id = db.save_teacher_review(
            submission_id,
            "王老师",
            "调整理解度结论",
            72,
            "结合课堂表现，将确认理解度调整为72分。",
            "2026-09-09 20:20",
        )

        latest = db.get_teacher_review(submission_id)
        history = db.list_teacher_review_history(submission_id)
        saved_report = db.get_report(submission_id)
        submission_record = db.list_submissions(student_id="20260003")[0]
        learning_record = db.list_learning_records(student_id="20260003")[0]

        self.assertGreater(second_id, first_id)
        self.assertEqual(len(history), 2)
        self.assertEqual(latest["confirmed_overall"], 72)
        self.assertEqual(saved_report["teacher_review"]["decision"], "调整理解度结论")
        self.assertTrue(submission_record["teacher_reviewed"])
        self.assertEqual(submission_record["teacher_confirmed_overall"], 72)
        self.assertTrue(learning_record["teacher_reviewed"])
        self.assertEqual(learning_record["teacher_comment"], latest["comment"])

        db.save_teacher_review(
            submission_id,
            "王老师",
            "认可AI诊断",
            75,
            "切换认可后应自动使用AI原始分数。",
        )
        synced_review = db.get_teacher_review(submission_id)
        self.assertEqual(synced_review["confirmed_overall"], 68)

    def test_teacher_review_comment_is_optional(self):
        db.init_db()
        submission_id = db.create_submission_with_questions(
            "20260009",
            "留空意见同学",
            "求数组中的最大值",
            "遍历比较。",
            "int main() { return 0; }",
            "2026-09-17 10:00",
            QUESTIONS,
        )
        report = {
            "overall": 81,
            "level": "基本掌握",
            "weakest": "边界情况意识",
            "summary": "核心思路正确。",
            "suggestion": "继续补充边界说明。",
            "dimensions": {
                "程序逻辑理解": 88,
                "关键概念掌握": 82,
                "边界情况意识": 74,
                "分析与修改能力": 80,
            },
            "review_required": False,
            "review_reasons": [],
        }
        db.save_report(submission_id, report, "2026-09-17 10:05")

        db.save_teacher_review(
            submission_id,
            "王老师",
            "认可AI诊断",
            81,
            "",
            "2026-09-17 10:10",
        )
        self.assertEqual(db.get_teacher_review(submission_id)["comment"], "")

        db.save_teacher_review(
            submission_id,
            "王老师",
            "要求学生重新答辩",
            81,
            None,
            "2026-09-17 10:15",
        )
        request = db.get_latest_redefense_request(submission_id)
        self.assertEqual(request["reason"], "教师要求针对关键证据完成重新答辩。")

    def test_student_feedback_requires_completed_submission_and_blocks_duplicate(self):
        db.init_db()
        submission_id = db.create_submission_with_questions(
            "20260004",
            "反馈测试同学",
            "求数组中的最大值",
            "先初始化，再循环比较。",
            "int main() { return 0; }",
            "2026-09-09 21:00",
            QUESTIONS,
        )

        with self.assertRaisesRegex(ValueError, "完成AI答辩"):
            db.save_student_feedback(
                submission_id,
                "对AI结果有疑问",
                "我想进一步了解第三题的评分依据。",
            )

        report = {
            "overall": 70,
            "level": "基本掌握",
            "weakest": "分析与修改能力",
            "summary": "基本理解程序逻辑。",
            "suggestion": "继续练习代码修改。",
            "dimensions": {
                "程序逻辑理解": 80,
                "关键概念掌握": 75,
                "边界情况意识": 65,
                "分析与修改能力": 60,
            },
            "review_required": False,
            "review_reasons": [],
        }
        db.save_report(submission_id, report, "2026-09-09 21:05")
        feedback_id = db.save_student_feedback(
            submission_id,
            "对AI结果有疑问",
            "我想进一步了解第三题的评分依据。",
            True,
            "2026-09-09 21:10",
        )
        self.assertGreater(feedback_id, 0)

        with self.assertRaisesRegex(ValueError, "相同反馈已经提交"):
            db.save_student_feedback(
                submission_id,
                "对AI结果有疑问",
                "我想进一步了解第三题的评分依据。",
            )

    def test_teacher_can_process_feedback_and_student_report_sees_reply(self):
        db.init_db()
        submission_id = db.create_submission_with_questions(
            "20260005",
            "回复测试同学",
            "求数组中的最大值",
            "先初始化，再循环比较。",
            "int main() { return 0; }",
            "2026-09-09 22:00",
            QUESTIONS,
        )
        report = {
            "overall": 82,
            "level": "掌握良好",
            "weakest": "边界情况意识",
            "summary": "能够解释主要逻辑。",
            "suggestion": "补充边界测试。",
            "dimensions": {
                "程序逻辑理解": 90,
                "关键概念掌握": 85,
                "边界情况意识": 70,
                "分析与修改能力": 82,
            },
            "review_required": False,
            "review_reasons": [],
        }
        db.save_report(submission_id, report, "2026-09-09 22:05")
        feedback_id = db.save_student_feedback(
            submission_id,
            "希望获得学习指导",
            "希望老师推荐一些数组边界练习。",
            True,
            "2026-09-09 22:10",
        )
        db.process_student_feedback(
            feedback_id,
            "李老师",
            "已回复",
            "请完成课程平台中的三道数组边界练习。",
            "2026-09-09 22:20",
        )

        feedbacks = db.list_student_feedbacks(
            submission_id=submission_id,
            status="已回复",
        )
        saved_report = db.get_report(submission_id)
        submission_record = db.list_submissions(student_id="20260005")[0]
        self.assertEqual(len(feedbacks), 1)
        self.assertEqual(feedbacks[0]["replied_by"], "李老师")
        self.assertIn("三道数组", feedbacks[0]["teacher_reply"])
        self.assertEqual(saved_report["student_feedbacks"][0]["status"], "已回复")
        self.assertEqual(submission_record["feedback_count"], 1)
        self.assertEqual(submission_record["pending_feedback_count"], 0)

        student_tasks = db.list_student_tasks("20260005")
        self.assertTrue(
            any(item["task_type"] == "教师回复" for item in student_tasks)
        )
        db.mark_student_feedback_viewed(
            feedback_id,
            "20260005",
            "2026-09-09 22:25",
        )
        self.assertFalse(db.list_student_tasks("20260005"))
        completed_tasks = db.list_student_tasks(
            "20260005",
            include_completed=True,
        )
        self.assertTrue(
            any(
                item["task_type"] == "教师回复"
                and item["status"] == "已完成"
                for item in completed_tasks
            )
        )

    def test_pending_tasks_follow_defense_review_and_feedback_states(self):
        db.init_db()
        submission_id = db.create_submission_with_questions(
            "20260009",
            "待办测试同学",
            "求数组中的最大值",
            "先初始化，再循环比较。",
            "int main() { return 0; }",
            "2026-09-10 09:00",
            QUESTIONS,
        )
        student_tasks = db.list_student_tasks("20260009")
        self.assertEqual(student_tasks[0]["action"], "continue_defense")

        report = {
            "overall": 66,
            "level": "需要巩固",
            "weakest": "边界情况意识",
            "summary": "边界分析不足。",
            "suggestion": "练习边界测试。",
            "dimensions": {
                "程序逻辑理解": 75,
                "关键概念掌握": 70,
                "边界情况意识": 50,
                "分析与修改能力": 68,
            },
            "review_required": True,
            "review_reasons": ["综合理解度低于70"],
        }
        db.save_report(submission_id, report, "2026-09-10 09:10")
        self.assertFalse(db.list_student_tasks("20260009"))
        teacher_tasks = db.list_teacher_tasks()
        review_task = next(
            item
            for item in teacher_tasks
            if item["action"] == "review_submission"
        )
        self.assertEqual(review_task["submission_id"], submission_id)
        self.assertEqual(review_task["priority"], "紧急")

        feedback_id = db.save_student_feedback(
            submission_id,
            "希望获得学习指导",
            "希望老师补充数组边界练习。",
            True,
            "2026-09-10 09:15",
        )
        feedback_task = next(
            item
            for item in db.list_teacher_tasks()
            if item["feedback_id"] == feedback_id
        )
        self.assertEqual(feedback_task["priority"], "紧急")

    def test_redefense_keeps_original_and_links_new_attempt(self):
        db.init_db()
        original_id = db.create_submission_with_questions(
            "20260007",
            "重答测试同学",
            "求数组中的最大值",
            "先初始化，再循环比较。",
            "int main() { return 0; }",
            "2026-09-09 23:00",
            QUESTIONS,
        )
        original_report = {
            "overall": 62,
            "level": "需要巩固",
            "weakest": "边界情况意识",
            "summary": "边界分析不足。",
            "suggestion": "重新解释数组越界问题。",
            "dimensions": {
                "程序逻辑理解": 75,
                "关键概念掌握": 65,
                "边界情况意识": 45,
                "分析与修改能力": 63,
            },
            "review_required": True,
            "review_reasons": ["综合理解度低于70"],
        }
        db.save_report(original_id, original_report, "2026-09-09 23:05")
        db.save_teacher_review(
            original_id,
            "王老师",
            "要求学生重新答辩",
            62,
            "请重点重新解释数组越界和空输入问题。",
            "2026-09-09 23:10",
        )
        request = db.get_latest_redefense_request(original_id)
        self.assertEqual(request["status"], "待开始")
        student_redefense_task = next(
            item
            for item in db.list_student_tasks("20260007")
            if item["action"] == "start_redefense"
        )
        self.assertEqual(student_redefense_task["priority"], "紧急")

        new_id = db.start_redefense(
            request["id"],
            QUESTIONS,
            "2026-09-09 23:15",
        )
        duplicate_id = db.start_redefense(
            request["id"],
            QUESTIONS,
            "2026-09-09 23:16",
        )
        new_submission = db.get_submission(new_id)
        original_submission = db.get_submission(original_id)
        self.assertEqual(duplicate_id, new_id)
        self.assertEqual(new_submission["parent_submission_id"], original_id)
        self.assertEqual(new_submission["attempt_number"], 2)
        self.assertEqual(original_submission["status"], "已完成")
        self.assertIsNotNone(db.get_report(original_id))
        continue_task = next(
            item
            for item in db.list_student_tasks("20260007")
            if item["submission_id"] == new_id
        )
        self.assertEqual(continue_task["action"], "continue_defense")

        new_report = dict(original_report)
        new_report["overall"] = 78
        new_report["level"] = "基本掌握"
        new_report["review_required"] = False
        new_report["review_reasons"] = []
        db.save_report(new_id, new_report, "2026-09-09 23:30")

        completed_request = db.get_latest_redefense_request(original_id)
        request_from_child = db.get_redefense_request_by_new_submission(new_id)
        new_record = next(
            item for item in db.list_submissions(student_id="20260007")
            if item["id"] == new_id
        )
        self.assertEqual(completed_request["status"], "已完成")
        self.assertEqual(request_from_child["original_submission_id"], original_id)
        self.assertEqual(new_record["attempt_number"], 2)
        child_review_task = next(
            item
            for item in db.list_teacher_tasks()
            if item["submission_id"] == new_id
            and item["action"] == "review_submission"
        )
        self.assertEqual(child_review_task["title"], "复核重新答辩结果")

    def test_new_teacher_decision_cancels_unstarted_redefense(self):
        db.init_db()
        submission_id = db.create_submission_with_questions(
            "20260008",
            "取消重答同学",
            "求数组中的最大值",
            "先初始化，再循环比较。",
            "int main() { return 0; }",
            "2026-09-09 23:40",
            QUESTIONS,
        )
        report = {
            "overall": 68,
            "level": "需要巩固",
            "weakest": "边界情况意识",
            "summary": "存在边界知识缺口。",
            "suggestion": "练习边界测试。",
            "dimensions": {
                "程序逻辑理解": 80,
                "关键概念掌握": 70,
                "边界情况意识": 52,
                "分析与修改能力": 70,
            },
            "review_required": True,
            "review_reasons": ["综合理解度低于70"],
        }
        db.save_report(submission_id, report, "2026-09-09 23:45")
        db.save_teacher_review(
            submission_id,
            "李老师",
            "要求学生重新答辩",
            68,
            "请重新说明边界情况。",
            "2026-09-09 23:50",
        )
        db.save_teacher_review(
            submission_id,
            "李老师",
            "认可AI诊断",
            68,
            "重新核对后取消重答要求。",
            "2026-09-09 23:55",
        )
        request = db.get_latest_redefense_request(submission_id)
        self.assertEqual(request["status"], "已取消")


class ReportTests(unittest.TestCase):
    def test_report_scores_are_calibrated_and_review_reason_is_generated(self):
        qa_records = []
        scores = [90, 70, 50]
        for index, score in enumerate(scores):
            qa_records.append(
                {
                    "dimension": QUESTIONS[index]["dimension"],
                    "question": QUESTIONS[index]["question"],
                    "answer": "测试回答",
                    "follow_up_question": "",
                    "follow_up_answer": "",
                    "initial_evaluation": {},
                    "final_evaluation": {
                        "score": score,
                        "confidence": "高",
                        "misconceptions": ["错误认识"] if index == 2 else [],
                    },
                }
            )

        fake_report = {
            "dimensions": {
                "程序逻辑理解": 88,
                "关键概念掌握": 72,
                "边界情况意识": 65,
                "分析与修改能力": 55,
            },
            "summary": "学生理解了基本循环逻辑，但修改能力仍需巩固。",
            "suggestion": "练习边界测试，并完成同类代码修改。",
            "review_reasons": ["第三题中存在明确误解"],
        }
        submission = {
            "problem": "求数组中的最大值",
            "explanation": "循环比较",
            "code": "int main() { return 0; }",
        }

        with patch.object(llm, "_chat_json", return_value=fake_report):
            report = llm.generate_report(submission, qa_records)

        self.assertEqual(
            report["dimensions"],
            {
                "程序逻辑理解": 89,
                "关键概念掌握": 71,
                "边界情况意识": 68,
                "分析与修改能力": 52,
            },
        )
        self.assertTrue(report["review_required"])
        self.assertIn("至少一道问题的理解度评分低于60", report["review_reasons"])
        self.assertIn("学生回答中仍存在明确错误认识", report["review_reasons"])


if __name__ == "__main__":
    unittest.main()
