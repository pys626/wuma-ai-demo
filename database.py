import ast
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "wuma_ai.db"
DB_PATH = Path(os.getenv("WUMA_AI_DB_PATH", str(DEFAULT_DB_PATH)))

MAX_TEST_INPUT_CHARS = 2_000_000
MAX_TEST_OUTPUT_CHARS = 2_000_000
MAX_PUBLIC_SAMPLE_CHARS = 200_000

DEFAULT_STARTER_CODE = """#include <iostream>
using namespace std;

int main() {
    int n;
    cin >> n;
    int arr[100];
    for (int i = 0; i < n; i++) {
        cin >> arr[i];
    }
    int max_value = arr[0];
    for (int i = 1; i < n; i++) {
        if (arr[i] > max_value) {
            max_value = arr[i];
        }
    }
    cout << max_value << endl;
    return 0;
}
"""

DEFAULT_TEST_CASES = [
    {
        "name": "普通正数",
        "group": "基础功能",
        "weight": 40,
        "input": "5\n3 8 2 6 1\n",
        "expected_output": "8\n",
    },
    {
        "name": "全部负数",
        "group": "边界情况",
        "weight": 35,
        "input": "4\n-7 -2 -9 -5\n",
        "expected_output": "-2\n",
    },
    {
        "name": "单个元素",
        "group": "边界情况",
        "weight": 25,
        "input": "1\n42\n",
        "expected_output": "42\n",
    },
]

DEFAULT_PUBLIC_SAMPLES = [
    {
        "name": "样例1",
        "input": "5\n3 8 2 6 1\n",
        "output": "8\n",
        "explanation": "5表示数组长度，最大元素为8。",
    },
]

DEFAULT_ASSIGNMENT = {
    "title": "求数组中的最大值",
    "description": "输入n个整数，输出其中的最大值。",
    "requirements": [
        "使用数组保存数据",
        "使用循环比较元素",
        "考虑数组中全部是负数的情况",
    ],
    "teaching_focus": [
        "最大值变量的初始化依据",
        "循环起点与不变量",
        "n为0或n超过数组容量时的风险",
        "将程序修改为求最小值的方法",
    ],
    "starter_code": DEFAULT_STARTER_CODE,
    "reference_code": DEFAULT_STARTER_CODE,
    "rubric_reviewed": True,
    "language": "C++",
    "input_format": "第一行输入整数n，第二行输入n个整数。",
    "output_format": "输出数组中的最大值。",
    "constraints_text": "1 ≤ n ≤ 100",
    "public_samples": DEFAULT_PUBLIC_SAMPLES,
    "cpp_standard": "auto",
    "verification_enabled": True,
    "test_cases": DEFAULT_TEST_CASES,
    "hint_limit": 2,
    "defense_admission": "全部通过",
    "defense_score_threshold": 100,
}

DEFENSE_ADMISSION_MODES = {
    "全部通过",
    "达到指定分数",
    "允许带错代码",
}

RUBRIC_SOURCES = {
    "代码",
    "实验报告",
    "代码＋实验报告",
    "代码＋答辩",
    "实验报告＋答辩",
    "代码＋实验报告＋答辩",
}

DEFAULT_RUBRIC = [
    {
        "name": "正确遍历数组",
        "weight": 25,
        "source": "代码",
        "description": "使用循环检查有效范围内的数组元素。",
        "must_defend": False,
    },
    {
        "name": "最大值初始化合理",
        "weight": 25,
        "source": "代码＋实验报告",
        "description": "最大值初值来自真实数组元素，并能说明原因。",
        "must_defend": True,
    },
    {
        "name": "处理边界情况",
        "weight": 25,
        "source": "实验报告＋答辩",
        "description": "分析全负数、空输入和数组容量等边界风险。",
        "must_defend": True,
    },
    {
        "name": "分析与修改能力",
        "weight": 25,
        "source": "代码＋答辩",
        "description": "能够解释关键实现，并完成相近需求的修改。",
        "must_defend": True,
    },
]

RUBRIC_TEMPLATES = {
    "基础程序设计": DEFAULT_RUBRIC,
    "数据结构实验": [
        {
            "name": "数据结构设计",
            "weight": 25,
            "source": "代码＋实验报告",
            "description": "选择并定义适合任务的数据结构，说明关键字段与关系。",
            "must_defend": True,
        },
        {
            "name": "核心操作正确性",
            "weight": 30,
            "source": "代码＋答辩",
            "description": "插入、删除、查找或遍历等核心操作逻辑正确。",
            "must_defend": True,
        },
        {
            "name": "边界与复杂度分析",
            "weight": 25,
            "source": "实验报告＋答辩",
            "description": "分析空结构、越界和特殊输入，并说明时间空间复杂度。",
            "must_defend": True,
        },
        {
            "name": "测试与修改能力",
            "weight": 20,
            "source": "代码＋实验报告＋答辩",
            "description": "使用有效测试验证实现，并能根据新要求修改关键操作。",
            "must_defend": False,
        },
    ],
    "算法实验": [
        {
            "name": "算法正确性",
            "weight": 35,
            "source": "代码＋实验报告",
            "description": "算法步骤与实现一致，能够解决题目规定的问题。",
            "must_defend": True,
        },
        {
            "name": "复杂度分析",
            "weight": 20,
            "source": "实验报告＋答辩",
            "description": "说明时间和空间复杂度及其形成原因。",
            "must_defend": True,
        },
        {
            "name": "边界与测试",
            "weight": 20,
            "source": "代码＋实验报告",
            "description": "覆盖典型、极端和异常输入，并说明预期结果。",
            "must_defend": False,
        },
        {
            "name": "解释与改进能力",
            "weight": 25,
            "source": "代码＋答辩",
            "description": "能解释关键决策，并提出或完成合理的算法改进。",
            "must_defend": True,
        },
    ],
}

TEACHER_REVIEW_DECISIONS = {
    "认可AI诊断",
    "调整理解度结论",
    "要求学生重新答辩",
}

STUDENT_FEEDBACK_CATEGORIES = {
    "对AI结果有疑问",
    "补充答辩说明",
    "希望获得学习指导",
    "其他反馈",
}

STUDENT_FEEDBACK_STATUSES = {
    "待处理",
    "已阅",
    "已回复",
}


@contextmanager
def get_connection():
    """创建连接并保证提交、回滚和关闭，兼容Windows文件锁。"""
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _column_names(connection, table_name):
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def _migrate_qa_table(connection):
    """为已有v0.2数据库补充真实AI评价需要的列。"""
    columns = _column_names(connection, "qa_records")
    migrations = {
        "reference_points_json": "TEXT NOT NULL DEFAULT '[]'",
        "initial_evaluation_json": "TEXT NOT NULL DEFAULT '{}'",
        "final_evaluation_json": "TEXT NOT NULL DEFAULT '{}'",
        "question_reason": "TEXT NOT NULL DEFAULT ''",
    }

    for column_name, definition in migrations.items():
        if column_name not in columns:
            connection.execute(
                f"ALTER TABLE qa_records ADD COLUMN {column_name} {definition}"
            )


def _migrate_report_table(connection):
    """为v0.3报告表补充教师复核信息，不影响已有记录。"""
    columns = _column_names(connection, "reports")
    migrations = {
        "review_required": "INTEGER NOT NULL DEFAULT 0",
        "review_reasons_json": "TEXT NOT NULL DEFAULT '[]'",
    }

    for column_name, definition in migrations.items():
        if column_name not in columns:
            connection.execute(
                f"ALTER TABLE reports ADD COLUMN {column_name} {definition}"
            )

    connection.execute(
        """
        UPDATE reports
        SET
            review_required = 1,
            review_reasons_json = ?
        WHERE
            overall < 70
            AND review_required = 0
            AND review_reasons_json = '[]'
        """,
        (
            json.dumps(
                ["历史报告综合理解度低于70，建议教师复核"],
                ensure_ascii=False,
            ),
        ),
    )


def _migrate_submission_table(connection):
    """为旧提交表补充实验关联、快照和重新答辩关系。"""
    columns = _column_names(connection, "submissions")
    migrations = {
        "assignment_id": "INTEGER",
        "assignment_snapshot_json": "TEXT NOT NULL DEFAULT '{}'",
        "parent_submission_id": "INTEGER",
        "attempt_number": "INTEGER NOT NULL DEFAULT 1",
        "lab_report": "TEXT NOT NULL DEFAULT ''",
        "rubric_snapshot_json": "TEXT NOT NULL DEFAULT '[]'",
        "code_verification_json": "TEXT NOT NULL DEFAULT '{}'",
    }

    for column_name, definition in migrations.items():
        if column_name not in columns:
            connection.execute(
                f"ALTER TABLE submissions ADD COLUMN {column_name} {definition}"
            )


def _migrate_assignment_table(connection):
    """为旧实验任务补充评分点，并为历史任务提供可用的默认标尺。"""
    columns = _column_names(connection, "assignments")
    test_cases_column_added = "test_cases_json" not in columns
    if "rubric_json" not in columns:
        connection.execute(
            "ALTER TABLE assignments ADD COLUMN rubric_json "
            "TEXT NOT NULL DEFAULT '[]'"
        )
    if "verification_enabled" not in columns:
        connection.execute(
            "ALTER TABLE assignments ADD COLUMN verification_enabled "
            "INTEGER NOT NULL DEFAULT 0"
        )
    if "test_cases_json" not in columns:
        connection.execute(
            "ALTER TABLE assignments ADD COLUMN test_cases_json "
            "TEXT NOT NULL DEFAULT '[]'"
        )
    if "input_format" not in columns:
        connection.execute(
            "ALTER TABLE assignments ADD COLUMN input_format "
            "TEXT NOT NULL DEFAULT ''"
        )
    if "output_format" not in columns:
        connection.execute(
            "ALTER TABLE assignments ADD COLUMN output_format "
            "TEXT NOT NULL DEFAULT ''"
        )
    if "constraints_text" not in columns:
        connection.execute(
            "ALTER TABLE assignments ADD COLUMN constraints_text "
            "TEXT NOT NULL DEFAULT ''"
        )
    public_samples_column_added = "public_samples_json" not in columns
    if public_samples_column_added:
        connection.execute(
            "ALTER TABLE assignments ADD COLUMN public_samples_json "
            "TEXT NOT NULL DEFAULT '[]'"
        )
    if "cpp_standard" not in columns:
        connection.execute(
            "ALTER TABLE assignments ADD COLUMN cpp_standard "
            "TEXT NOT NULL DEFAULT 'auto'"
        )
    if "hint_limit" not in columns:
        connection.execute(
            "ALTER TABLE assignments ADD COLUMN hint_limit "
            "INTEGER NOT NULL DEFAULT 2"
        )
    if "defense_admission" not in columns:
        connection.execute(
            "ALTER TABLE assignments ADD COLUMN defense_admission "
            "TEXT NOT NULL DEFAULT '全部通过'"
        )
    if "defense_score_threshold" not in columns:
        connection.execute(
            "ALTER TABLE assignments ADD COLUMN defense_score_threshold "
            "INTEGER NOT NULL DEFAULT 100"
        )
    reference_code_added = "reference_code" not in columns
    if reference_code_added:
        connection.execute(
            "ALTER TABLE assignments ADD COLUMN reference_code "
            "TEXT NOT NULL DEFAULT ''"
        )
    rubric_reviewed_added = "rubric_reviewed" not in columns
    if rubric_reviewed_added:
        connection.execute(
            "ALTER TABLE assignments ADD COLUMN rubric_reviewed "
            "INTEGER NOT NULL DEFAULT 0"
        )
    connection.execute(
        """
        UPDATE assignments
        SET rubric_json = ?
        WHERE rubric_json IS NULL OR rubric_json = '' OR rubric_json = '[]'
        """,
        (json.dumps(DEFAULT_RUBRIC, ensure_ascii=False),),
    )
    if test_cases_column_added:
        connection.execute(
            """
            UPDATE assignments
            SET verification_enabled = 1, test_cases_json = ?
            WHERE title = ?
              AND (test_cases_json IS NULL OR test_cases_json = '' OR test_cases_json = '[]')
            """,
            (
                json.dumps(DEFAULT_TEST_CASES, ensure_ascii=False),
                DEFAULT_ASSIGNMENT["title"],
            ),
        )
    if public_samples_column_added:
        connection.execute(
            """
            UPDATE assignments
            SET input_format = ?, output_format = ?, constraints_text = ?,
                public_samples_json = ?
            WHERE title = ?
              AND (public_samples_json IS NULL OR public_samples_json = ''
                   OR public_samples_json = '[]')
            """,
            (
                DEFAULT_ASSIGNMENT["input_format"],
                DEFAULT_ASSIGNMENT["output_format"],
                DEFAULT_ASSIGNMENT["constraints_text"],
                json.dumps(DEFAULT_PUBLIC_SAMPLES, ensure_ascii=False),
                DEFAULT_ASSIGNMENT["title"],
            ),
        )
    if reference_code_added:
        connection.execute(
            """
            UPDATE assignments
            SET reference_code = ?
            WHERE title = ? AND (reference_code IS NULL OR reference_code = '')
            """,
            (DEFAULT_STARTER_CODE, DEFAULT_ASSIGNMENT["title"]),
        )
    if rubric_reviewed_added:
        connection.execute(
            "UPDATE assignments SET rubric_reviewed = 1 WHERE title = ?",
            (DEFAULT_ASSIGNMENT["title"],),
        )


def _migrate_student_feedback_table(connection):
    """为旧反馈表补充学生查看教师回复的时间。"""
    columns = _column_names(connection, "student_feedbacks")
    if "student_viewed_at" not in columns:
        connection.execute(
            """
            ALTER TABLE student_feedbacks
            ADD COLUMN student_viewed_at TEXT NOT NULL DEFAULT ''
            """
        )


def _seed_default_assignment(connection):
    """首次运行时创建一个可直接演示的默认实验。"""
    count = connection.execute(
        "SELECT COUNT(*) AS amount FROM assignments"
    ).fetchone()["amount"]
    if count == 0:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        cursor = connection.execute(
            """
            INSERT INTO assignments (
                title, description, requirements_json,
                teaching_focus_json, rubric_json, starter_code,
                input_format, output_format, constraints_text,
                public_samples_json, cpp_standard,
                verification_enabled, test_cases_json,
                hint_limit, defense_admission, defense_score_threshold,
                reference_code, rubric_reviewed,
                language, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    '已发布', ?, ?)
            """,
            (
                DEFAULT_ASSIGNMENT["title"],
                DEFAULT_ASSIGNMENT["description"],
                json.dumps(DEFAULT_ASSIGNMENT["requirements"], ensure_ascii=False),
                json.dumps(DEFAULT_ASSIGNMENT["teaching_focus"], ensure_ascii=False),
                json.dumps(DEFAULT_RUBRIC, ensure_ascii=False),
                DEFAULT_ASSIGNMENT["starter_code"],
                DEFAULT_ASSIGNMENT["input_format"],
                DEFAULT_ASSIGNMENT["output_format"],
                DEFAULT_ASSIGNMENT["constraints_text"],
                json.dumps(DEFAULT_PUBLIC_SAMPLES, ensure_ascii=False),
                DEFAULT_ASSIGNMENT["cpp_standard"],
                1,
                json.dumps(DEFAULT_TEST_CASES, ensure_ascii=False),
                DEFAULT_ASSIGNMENT["hint_limit"],
                DEFAULT_ASSIGNMENT["defense_admission"],
                DEFAULT_ASSIGNMENT["defense_score_threshold"],
                DEFAULT_ASSIGNMENT["reference_code"],
                int(DEFAULT_ASSIGNMENT["rubric_reviewed"]),
                DEFAULT_ASSIGNMENT["language"],
                now,
                now,
            ),
        )
        default_id = cursor.lastrowid
    else:
        row = connection.execute(
            "SELECT id FROM assignments WHERE title = ? ORDER BY id LIMIT 1",
            (DEFAULT_ASSIGNMENT["title"],),
        ).fetchone()
        default_id = row["id"] if row else None

    if default_id is not None:
        connection.execute(
            """
            UPDATE submissions
            SET assignment_id = ?
            WHERE assignment_id IS NULL AND problem = ?
            """,
            (default_id, DEFAULT_ASSIGNMENT["title"]),
        )


def init_db():
    """创建数据表，并自动兼容旧版数据库。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                requirements_json TEXT NOT NULL DEFAULT '[]',
                teaching_focus_json TEXT NOT NULL DEFAULT '[]',
                rubric_json TEXT NOT NULL DEFAULT '[]',
                starter_code TEXT NOT NULL DEFAULT '',
                input_format TEXT NOT NULL DEFAULT '',
                output_format TEXT NOT NULL DEFAULT '',
                constraints_text TEXT NOT NULL DEFAULT '',
                public_samples_json TEXT NOT NULL DEFAULT '[]',
                cpp_standard TEXT NOT NULL DEFAULT 'auto',
                verification_enabled INTEGER NOT NULL DEFAULT 0,
                test_cases_json TEXT NOT NULL DEFAULT '[]',
                hint_limit INTEGER NOT NULL DEFAULT 2,
                defense_admission TEXT NOT NULL DEFAULT '全部通过',
                defense_score_threshold INTEGER NOT NULL DEFAULT 100,
                reference_code TEXT NOT NULL DEFAULT '',
                rubric_reviewed INTEGER NOT NULL DEFAULT 0,
                language TEXT NOT NULL DEFAULT 'C++',
                status TEXT NOT NULL DEFAULT '已发布',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assignment_id INTEGER,
                parent_submission_id INTEGER,
                attempt_number INTEGER NOT NULL DEFAULT 1,
                student_id TEXT NOT NULL,
                name TEXT NOT NULL,
                problem TEXT NOT NULL,
                explanation TEXT NOT NULL,
                code TEXT NOT NULL,
                submitted_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '答辩中',
                assignment_snapshot_json TEXT NOT NULL DEFAULT '{}',
                lab_report TEXT NOT NULL DEFAULT '',
                rubric_snapshot_json TEXT NOT NULL DEFAULT '[]',
                code_verification_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(assignment_id)
                    REFERENCES assignments(id)
                    ON DELETE SET NULL,
                FOREIGN KEY(parent_submission_id)
                    REFERENCES submissions(id)
                    ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS qa_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submission_id INTEGER NOT NULL,
                question_index INTEGER NOT NULL,
                dimension TEXT NOT NULL,
                question TEXT NOT NULL,
                follow_up_question TEXT NOT NULL DEFAULT '',
                answer TEXT NOT NULL DEFAULT '',
                follow_up_answer TEXT NOT NULL DEFAULT '',
                reference_score INTEGER,
                reference_points_json TEXT NOT NULL DEFAULT '[]',
                initial_evaluation_json TEXT NOT NULL DEFAULT '{}',
                final_evaluation_json TEXT NOT NULL DEFAULT '{}',
                question_reason TEXT NOT NULL DEFAULT '',
                UNIQUE(submission_id, question_index),
                FOREIGN KEY(submission_id)
                    REFERENCES submissions(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submission_id INTEGER NOT NULL UNIQUE,
                overall INTEGER NOT NULL,
                level TEXT NOT NULL,
                weakest TEXT NOT NULL,
                summary TEXT NOT NULL,
                suggestion TEXT NOT NULL,
                dimensions_json TEXT NOT NULL,
                review_required INTEGER NOT NULL DEFAULT 0,
                review_reasons_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY(submission_id)
                    REFERENCES submissions(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS preliminary_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submission_id INTEGER NOT NULL UNIQUE,
                completion_score INTEGER NOT NULL,
                summary TEXT NOT NULL,
                criteria_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY(submission_id)
                    REFERENCES submissions(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS teacher_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submission_id INTEGER NOT NULL,
                reviewer_name TEXT NOT NULL,
                decision TEXT NOT NULL,
                confirmed_overall INTEGER NOT NULL,
                comment TEXT NOT NULL,
                reviewed_at TEXT NOT NULL,
                FOREIGN KEY(submission_id)
                    REFERENCES submissions(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS student_feedbacks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submission_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                reply_requested INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT '待处理',
                created_at TEXT NOT NULL,
                teacher_reply TEXT NOT NULL DEFAULT '',
                replied_by TEXT NOT NULL DEFAULT '',
                replied_at TEXT NOT NULL DEFAULT '',
                student_viewed_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(submission_id)
                    REFERENCES submissions(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS redefense_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_submission_id INTEGER NOT NULL,
                teacher_review_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '待开始',
                requested_at TEXT NOT NULL,
                new_submission_id INTEGER,
                completed_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(original_submission_id)
                    REFERENCES submissions(id)
                    ON DELETE CASCADE,
                FOREIGN KEY(teacher_review_id)
                    REFERENCES teacher_reviews(id)
                    ON DELETE CASCADE,
                FOREIGN KEY(new_submission_id)
                    REFERENCES submissions(id)
                    ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS ai_hint_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assignment_id INTEGER NOT NULL,
                student_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                hint_number INTEGER NOT NULL,
                verdict_status TEXT NOT NULL,
                code_digest TEXT NOT NULL,
                hint_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(assignment_id, student_id, hint_number),
                FOREIGN KEY(assignment_id)
                    REFERENCES assignments(id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_submissions_student_id
            ON submissions(student_id);

            CREATE INDEX IF NOT EXISTS idx_assignments_status
            ON assignments(status);

            CREATE INDEX IF NOT EXISTS idx_qa_submission_id
            ON qa_records(submission_id);

            CREATE INDEX IF NOT EXISTS idx_preliminary_reviews_submission_id
            ON preliminary_reviews(submission_id);

            CREATE INDEX IF NOT EXISTS idx_teacher_reviews_submission_id
            ON teacher_reviews(submission_id);

            CREATE INDEX IF NOT EXISTS idx_student_feedbacks_submission_id
            ON student_feedbacks(submission_id);

            CREATE INDEX IF NOT EXISTS idx_student_feedbacks_status
            ON student_feedbacks(status);

            CREATE INDEX IF NOT EXISTS idx_redefense_original_submission
            ON redefense_requests(original_submission_id);

            CREATE INDEX IF NOT EXISTS idx_redefense_new_submission
            ON redefense_requests(new_submission_id);

            CREATE INDEX IF NOT EXISTS idx_ai_hints_assignment_student
            ON ai_hint_records(assignment_id, student_id);
            """
        )
        _migrate_assignment_table(connection)
        _migrate_qa_table(connection)
        _migrate_report_table(connection)
        _migrate_submission_table(connection)
        _migrate_student_feedback_table(connection)
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_submissions_assignment_id
            ON submissions(assignment_id)
            """
        )
        _seed_default_assignment(connection)


def _decode_assignment(row):
    if row is None:
        return None
    assignment = dict(row)
    assignment["requirements"] = _load_json(
        assignment.pop("requirements_json"),
        [],
    )
    assignment["teaching_focus"] = _load_json(
        assignment.pop("teaching_focus_json"),
        [],
    )
    assignment["rubric"] = _load_json(
        assignment.pop("rubric_json", "[]"),
        DEFAULT_RUBRIC,
    )
    if not assignment["rubric"]:
        assignment["rubric"] = [dict(item) for item in DEFAULT_RUBRIC]
    assignment["verification_enabled"] = bool(
        assignment.get("verification_enabled", 0)
    )
    assignment["rubric_reviewed"] = bool(assignment.get("rubric_reviewed", 0))
    assignment["test_cases"] = _load_json(
        assignment.pop("test_cases_json", "[]"),
        [],
    )
    if assignment["test_cases"]:
        try:
            assignment["test_cases"] = normalize_test_cases(
                assignment["test_cases"]
            )
        except ValueError:
            # 保留异常历史数据供教师修正，避免单条旧记录阻断整个应用。
            pass
    assignment["public_samples"] = _load_json(
        assignment.pop("public_samples_json", "[]"),
        [],
    )
    assignment["public_samples"] = [
        {
            "name": normalize_sample_text(item.get("name", "")),
            "input": normalize_sample_text(item.get("input", "")),
            "output": normalize_sample_text(item.get("output", "")),
            "explanation": normalize_sample_text(item.get("explanation", "")),
        }
        for item in assignment["public_samples"]
        if isinstance(item, dict)
    ]
    assignment["cpp_standard"] = normalize_cpp_standard(
        assignment.get("cpp_standard", "auto")
    )
    assignment["hint_limit"] = normalize_hint_limit(
        assignment.get("hint_limit", 2)
    )
    (
        assignment["defense_admission"],
        assignment["defense_score_threshold"],
    ) = normalize_defense_policy(
        assignment.get("defense_admission", "全部通过"),
        assignment.get("defense_score_threshold", 100),
    )
    return assignment


def normalize_hint_limit(value):
    """教师可为每名学生设置0至5次AI提示。"""
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("AI提示次数必须是0至5之间的整数。") from error
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("AI提示次数必须是0至5之间的整数。")
    if not 0 <= number <= 5:
        raise ValueError("AI提示次数必须在0至5之间。")
    return number


def normalize_defense_policy(mode, threshold=100):
    """校验答辩准入规则及百分制阈值。"""
    normalized_mode = str(mode).strip()
    if normalized_mode not in DEFENSE_ADMISSION_MODES:
        raise ValueError("答辩准入规则不正确。")
    try:
        normalized_threshold = int(threshold)
    except (TypeError, ValueError) as error:
        raise ValueError("答辩准入分数必须是0至100之间的整数。") from error
    if isinstance(threshold, float) and not threshold.is_integer():
        raise ValueError("答辩准入分数必须是0至100之间的整数。")
    if not 0 <= normalized_threshold <= 100:
        raise ValueError("答辩准入分数必须在0至100之间。")
    return normalized_mode, normalized_threshold


def normalize_rubric(rubric):
    """校验并规范教师评分点；权重必须合计100。"""
    if not isinstance(rubric, list) or not 1 <= len(rubric) <= 8:
        raise ValueError("评分点数量必须在1至8条之间。")

    normalized = []
    names = set()
    for index, raw in enumerate(rubric, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"第{index}条评分点格式不正确。")
        name = str(raw.get("name", "")).strip()[:80]
        description = str(raw.get("description", "")).strip()[:300]
        source = str(raw.get("source", "")).strip()
        if not name:
            raise ValueError(f"第{index}条评分点名称不能为空。")
        if name in names:
            raise ValueError(f"评分点名称“{name}”重复。")
        if not description:
            raise ValueError(f"评分点“{name}”的评价说明不能为空。")
        if source not in RUBRIC_SOURCES:
            raise ValueError(f"评分点“{name}”的核查来源不正确。")
        try:
            weight = round(float(raw.get("weight", 0)))
        except (TypeError, ValueError) as error:
            raise ValueError(f"评分点“{name}”的权重必须是数字。") from error
        if not 1 <= weight <= 100:
            raise ValueError(f"评分点“{name}”的权重必须在1至100之间。")
        must_defend = raw.get("must_defend", False)
        if isinstance(must_defend, str):
            must_defend = must_defend.strip().lower() in {"true", "1", "是", "必答"}
        normalized.append(
            {
                "name": name,
                "weight": weight,
                "source": source,
                "description": description,
                "must_defend": bool(must_defend),
            }
        )
        names.add(name)

    total_weight = sum(item["weight"] for item in normalized)
    if total_weight != 100:
        raise ValueError(f"评分点权重合计必须为100，当前为{total_weight}。")
    return normalized


def get_rubric_template(name):
    """返回评分点模板副本，避免编辑时改变内置模板。"""
    if name not in RUBRIC_TEMPLATES:
        raise ValueError("没有找到对应的评分点模板。")
    return [dict(item) for item in RUBRIC_TEMPLATES[name]]


def assignment_quality_warnings(title, rubric):
    """识别容易造成错误评分的高风险配置，交由教师显式确认。"""
    cleaned_title = str(title or "").strip()
    if not cleaned_title:
        return []
    try:
        normalized = normalize_rubric(rubric)
    except ValueError:
        return []
    if cleaned_title != DEFAULT_ASSIGNMENT["title"] and normalized == normalize_rubric(
        DEFAULT_RUBRIC
    ):
        return [
            "当前仍直接使用“求数组中的最大值”的默认评分点，其中包含最大值初始化、"
            "数组遍历等特定内容；若题目不是该任务，请先改成与本实验一致的评分点。"
        ]
    return []


def _balanced_weights(amount, total=100):
    """把整数总分尽量平均分配，余数从前向后补齐。"""
    base, remainder = divmod(total, amount)
    return [base + (1 if index < remainder else 0) for index in range(amount)]


def normalize_test_cases(test_cases):
    """校验隐藏测试点，并规范分组与合计100分的权重。"""
    if not isinstance(test_cases, list) or not 1 <= len(test_cases) <= 8:
        raise ValueError("启用代码验证时，测试样例数量必须在1至8组之间。")
    normalized = []
    names = set()
    missing_weight_indices = []
    explicit_weight_total = 0
    for index, raw in enumerate(test_cases, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"第{index}组测试样例格式不正确。")
        name = str(raw.get("name", "")).strip()[:80]
        group = str(raw.get("group", "")).strip()[:40] or "基础功能"
        case_input = str(raw.get("input", ""))[:MAX_TEST_INPUT_CHARS]
        expected_output = str(raw.get("expected_output", ""))[:MAX_TEST_OUTPUT_CHARS]
        if not name:
            raise ValueError(f"第{index}组测试样例名称不能为空。")
        if name in names:
            raise ValueError(f"测试样例名称“{name}”重复。")
        if not expected_output.strip():
            raise ValueError(f"测试样例“{name}”的预期输出不能为空。")
        raw_weight = raw.get("weight")
        if raw_weight is None or str(raw_weight).strip() == "":
            weight = None
            missing_weight_indices.append(len(normalized))
        else:
            try:
                numeric_weight = float(raw_weight)
            except (TypeError, ValueError) as error:
                raise ValueError(f"测试样例“{name}”的分值必须是整数。") from error
            if not numeric_weight.is_integer():
                raise ValueError(f"测试样例“{name}”的分值必须是整数。")
            weight = int(numeric_weight)
            if not 1 <= weight <= 100:
                raise ValueError(f"测试样例“{name}”的分值必须在1至100之间。")
            explicit_weight_total += weight
        normalized.append(
            {
                "name": name,
                "group": group,
                "weight": weight,
                "input": case_input,
                "expected_output": expected_output,
            }
        )
        names.add(name)

    if missing_weight_indices:
        remaining = 100 - explicit_weight_total
        if remaining < len(missing_weight_indices):
            raise ValueError("已填写分值过高，无法为未填写分值的测试点分配至少1分。")
        distributed = _balanced_weights(len(missing_weight_indices), remaining)
        for item_index, weight in zip(missing_weight_indices, distributed):
            normalized[item_index]["weight"] = weight
    total_weight = sum(item["weight"] for item in normalized)
    if total_weight != 100:
        raise ValueError(f"隐藏测试点分值合计必须为100，当前为{total_weight}。")
    return normalized


def normalize_sample_text(value):
    """修复表格曾把单元素字符串列表保存为`['内容']`的历史数据。"""
    if isinstance(value, (list, tuple)):
        if len(value) == 1:
            return str(value[0] or "")
        return "\n".join(str(item) for item in value)
    text = str(value or "")
    stripped = text.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            parsed = ast.literal_eval(stripped)
        except (SyntaxError, ValueError):
            parsed = None
        if (
            isinstance(parsed, (list, tuple))
            and len(parsed) == 1
            and isinstance(parsed[0], str)
        ):
            return parsed[0]
    return text


def normalize_cpp_standard(value):
    standard = str(value or "auto").strip().lower()
    if standard not in {"auto", "c++11", "c++14", "c++17"}:
        raise ValueError("C++编译标准只能是自动选择、C++11、C++14或C++17。")
    return standard


def normalize_public_samples(public_samples):
    """校验学生可见的OJ公开样例；允许完全不配置，最多5组。"""
    if public_samples is None:
        return []
    if not isinstance(public_samples, list) or len(public_samples) > 5:
        raise ValueError("公开样例最多配置5组。")
    normalized = []
    for index, raw in enumerate(public_samples, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"第{index}组公开样例格式不正确。")
        name = normalize_sample_text(raw.get("name", "")).strip()[:80] or f"样例{index}"
        sample_input = normalize_sample_text(raw.get("input", ""))[
            :MAX_PUBLIC_SAMPLE_CHARS
        ]
        sample_output = normalize_sample_text(raw.get("output", ""))[
            :MAX_PUBLIC_SAMPLE_CHARS
        ]
        explanation = normalize_sample_text(raw.get("explanation", "")).strip()[:1000]
        if not sample_output.strip():
            raise ValueError(f"公开样例“{name}”的样例输出不能为空。")
        normalized.append(
            {
                "name": name,
                "input": sample_input,
                "output": sample_output,
                "explanation": explanation,
            }
        )
    return normalized


def list_assignments(published_only=False):
    """读取实验任务；学生端只读取已发布任务。"""
    sql = "SELECT * FROM assignments"
    parameters = ()
    if published_only:
        sql += " WHERE status = ?"
        parameters = ("已发布",)
    sql += " ORDER BY id DESC"

    with get_connection() as connection:
        rows = connection.execute(sql, parameters).fetchall()
        return [_decode_assignment(row) for row in rows]


def get_assignment(assignment_id):
    """按编号读取一个实验任务。"""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM assignments WHERE id = ?",
            (assignment_id,),
        ).fetchone()
        return _decode_assignment(row)


def create_assignment(
    title,
    description,
    requirements,
    teaching_focus,
    starter_code,
    language="C++",
    rubric=None,
    verification_enabled=False,
    test_cases=None,
    input_format="",
    output_format="",
    constraints_text="",
    public_samples=None,
    cpp_standard="auto",
    hint_limit=2,
    defense_admission="全部通过",
    defense_score_threshold=100,
    reference_code="",
    rubric_reviewed=False,
):
    """教师创建并直接发布一个新实验任务。"""
    rubric = normalize_rubric(rubric or DEFAULT_RUBRIC)
    test_cases = (
        normalize_test_cases(test_cases or [])
        if verification_enabled
        else []
    )
    public_samples = normalize_public_samples(public_samples)
    cpp_standard = normalize_cpp_standard(cpp_standard)
    hint_limit = normalize_hint_limit(hint_limit)
    defense_admission, defense_score_threshold = normalize_defense_policy(
        defense_admission,
        defense_score_threshold,
    )
    reference_code = str(reference_code or "")[:12000]
    rubric_reviewed = bool(rubric_reviewed)
    if verification_enabled and not reference_code.strip():
        raise ValueError("启用代码验证时必须填写教师参考答案。")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO assignments (
                title, description, requirements_json,
                teaching_focus_json, rubric_json, starter_code,
                input_format, output_format, constraints_text,
                public_samples_json, cpp_standard,
                verification_enabled, test_cases_json,
                hint_limit, defense_admission, defense_score_threshold,
                reference_code, rubric_reviewed,
                language, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    '已发布', ?, ?)
            """,
            (
                title,
                description,
                json.dumps(requirements, ensure_ascii=False),
                json.dumps(teaching_focus, ensure_ascii=False),
                json.dumps(rubric, ensure_ascii=False),
                starter_code,
                str(input_format).strip()[:3000],
                str(output_format).strip()[:3000],
                str(constraints_text).strip()[:3000],
                json.dumps(public_samples, ensure_ascii=False),
                cpp_standard,
                int(bool(verification_enabled)),
                json.dumps(test_cases, ensure_ascii=False),
                hint_limit,
                defense_admission,
                defense_score_threshold,
                reference_code,
                int(rubric_reviewed),
                language,
                now,
                now,
            ),
        )
        return cursor.lastrowid


def update_assignment(
    assignment_id,
    title,
    description,
    requirements,
    teaching_focus,
    starter_code,
    language="C++",
    rubric=None,
    verification_enabled=False,
    test_cases=None,
    input_format="",
    output_format="",
    constraints_text="",
    public_samples=None,
    cpp_standard="auto",
    hint_limit=2,
    defense_admission="全部通过",
    defense_score_threshold=100,
    reference_code="",
    rubric_reviewed=False,
):
    """修改任务内容；已有提交仍保留提交时快照。"""
    rubric = normalize_rubric(rubric or DEFAULT_RUBRIC)
    test_cases = (
        normalize_test_cases(test_cases or [])
        if verification_enabled
        else []
    )
    public_samples = normalize_public_samples(public_samples)
    cpp_standard = normalize_cpp_standard(cpp_standard)
    hint_limit = normalize_hint_limit(hint_limit)
    defense_admission, defense_score_threshold = normalize_defense_policy(
        defense_admission,
        defense_score_threshold,
    )
    reference_code = str(reference_code or "")[:12000]
    rubric_reviewed = bool(rubric_reviewed)
    if verification_enabled and not reference_code.strip():
        raise ValueError("启用代码验证时必须填写教师参考答案。")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE assignments
            SET
                title = ?,
                description = ?,
                requirements_json = ?,
                teaching_focus_json = ?,
                rubric_json = ?,
                starter_code = ?,
                input_format = ?,
                output_format = ?,
                constraints_text = ?,
                public_samples_json = ?,
                cpp_standard = ?,
                verification_enabled = ?,
                test_cases_json = ?,
                hint_limit = ?,
                defense_admission = ?,
                defense_score_threshold = ?,
                reference_code = ?,
                rubric_reviewed = ?,
                language = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                title,
                description,
                json.dumps(requirements, ensure_ascii=False),
                json.dumps(teaching_focus, ensure_ascii=False),
                json.dumps(rubric, ensure_ascii=False),
                starter_code,
                str(input_format).strip()[:3000],
                str(output_format).strip()[:3000],
                str(constraints_text).strip()[:3000],
                json.dumps(public_samples, ensure_ascii=False),
                cpp_standard,
                int(bool(verification_enabled)),
                json.dumps(test_cases, ensure_ascii=False),
                hint_limit,
                defense_admission,
                defense_score_threshold,
                reference_code,
                int(rubric_reviewed),
                language,
                now,
                assignment_id,
            ),
        )


def set_assignment_status(assignment_id, status):
    """发布或停用实验；停用不会删除历史提交。"""
    if status not in {"已发布", "已停用"}:
        raise ValueError("实验状态只能是“已发布”或“已停用”。")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE assignments
            SET status = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, now, assignment_id),
        )


def _assignment_snapshot(assignment):
    if not assignment:
        return {}
    return {
        "id": assignment.get("id"),
        "title": assignment.get("title", ""),
        "description": assignment.get("description", ""),
        "requirements": assignment.get("requirements", []),
        "teaching_focus": assignment.get("teaching_focus", []),
        "rubric": assignment.get("rubric", DEFAULT_RUBRIC),
        "input_format": assignment.get("input_format", ""),
        "output_format": assignment.get("output_format", ""),
        "constraints_text": assignment.get("constraints_text", ""),
        "public_samples": assignment.get("public_samples", []),
        "cpp_standard": normalize_cpp_standard(
            assignment.get("cpp_standard", "auto")
        ),
        "verification_enabled": bool(assignment.get("verification_enabled", False)),
        "test_cases": assignment.get("test_cases", []),
        "hint_limit": normalize_hint_limit(assignment.get("hint_limit", 2)),
        "rubric_reviewed": bool(assignment.get("rubric_reviewed", False)),
        "defense_admission": normalize_defense_policy(
            assignment.get("defense_admission", "全部通过"),
            assignment.get("defense_score_threshold", 100),
        )[0],
        "defense_score_threshold": normalize_defense_policy(
            assignment.get("defense_admission", "全部通过"),
            assignment.get("defense_score_threshold", 100),
        )[1],
        "language": assignment.get("language", "C++"),
    }


def count_ai_hints(assignment_id, student_id):
    """返回某名学生在一个实验中已经使用的AI提示次数。"""
    cleaned_student_id = str(student_id).strip()
    if not cleaned_student_id:
        return 0
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT COUNT(*) AS amount
            FROM ai_hint_records
            WHERE assignment_id = ? AND student_id = ?
            """,
            (assignment_id, cleaned_student_id),
        ).fetchone()["amount"]


def list_ai_hints(assignment_id=None, student_id=None):
    """读取AI提示记录；教师可按实验筛选，学生按实验和学号恢复记录。"""
    clauses = []
    parameters = []
    if assignment_id is not None:
        clauses.append("h.assignment_id = ?")
        parameters.append(assignment_id)
    if student_id is not None:
        clauses.append("h.student_id = ?")
        parameters.append(str(student_id).strip())
    sql = """
        SELECT h.*, a.title AS assignment_title
        FROM ai_hint_records h
        JOIN assignments a ON a.id = h.assignment_id
    """
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY h.created_at DESC, h.id DESC"
    with get_connection() as connection:
        rows = connection.execute(sql, parameters).fetchall()
    records = []
    for row in rows:
        record = dict(row)
        record["hint"] = _load_json(record.pop("hint_json", "{}"), {})
        records.append(record)
    return records


def save_ai_hint(
    assignment_id,
    student_id,
    name,
    code,
    verdict_status,
    hint,
    hint_limit,
):
    """在数据库内校验限额并保存提示，页面刷新不会恢复次数。"""
    cleaned_student_id = str(student_id).strip()
    if not cleaned_student_id:
        raise ValueError("获取AI提示前请填写学号。")
    limit = normalize_hint_limit(hint_limit)
    if limit == 0:
        raise ValueError("教师没有为本实验开放AI提示。")
    with get_connection() as connection:
        used = connection.execute(
            """
            SELECT COUNT(*) AS amount
            FROM ai_hint_records
            WHERE assignment_id = ? AND student_id = ?
            """,
            (assignment_id, cleaned_student_id),
        ).fetchone()["amount"]
        if used >= limit:
            raise ValueError(f"本实验的{limit}次AI提示已经全部使用。")
        hint_number = used + 1
        cursor = connection.execute(
            """
            INSERT INTO ai_hint_records (
                assignment_id, student_id, name, hint_number,
                verdict_status, code_digest, hint_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assignment_id,
                cleaned_student_id,
                str(name).strip()[:80],
                hint_number,
                str(verdict_status).strip()[:40],
                hashlib.sha256(str(code).encode("utf-8")).hexdigest(),
                json.dumps(hint, ensure_ascii=False),
                datetime.now().strftime("%Y-%m-%d %H:%M"),
            ),
        )
        return cursor.lastrowid, hint_number


def create_submission_with_questions(
    student_id,
    name,
    problem,
    explanation,
    code,
    submitted_at,
    questions,
    assignment=None,
    lab_report="",
    preliminary_review=None,
    code_verification=None,
):
    """在同一个事务中保存提交和3道AI问题。"""
    submission_sql = """
        INSERT INTO submissions (
            assignment_id, student_id, name, problem,
            explanation, code, submitted_at, status,
            assignment_snapshot_json, lab_report, rubric_snapshot_json,
            code_verification_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, '答辩中', ?, ?, ?, ?)
    """

    question_sql = """
        INSERT INTO qa_records (
            submission_id, question_index, dimension,
            question, reference_points_json, question_reason
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """

    with get_connection() as connection:
        cursor = connection.execute(
            submission_sql,
            (
                assignment.get("id") if assignment else None,
                student_id,
                name,
                problem,
                explanation,
                code,
                submitted_at,
                json.dumps(
                    _assignment_snapshot(assignment),
                    ensure_ascii=False,
                ),
                str(lab_report).strip()[:6000],
                json.dumps(
                    (assignment or {}).get("rubric", DEFAULT_RUBRIC),
                    ensure_ascii=False,
                ),
                json.dumps(code_verification or {}, ensure_ascii=False),
            ),
        )
        submission_id = cursor.lastrowid

        rows = []
        for index, question in enumerate(questions):
            rows.append(
                (
                    submission_id,
                    index,
                    question["dimension"],
                    question["question"],
                    json.dumps(question["reference_points"], ensure_ascii=False),
                    str(question.get("reason", "")).strip()[:300],
                )
            )
        connection.executemany(question_sql, rows)
        if preliminary_review:
            _upsert_preliminary_review(
                connection,
                submission_id,
                preliminary_review,
                submitted_at,
            )
        return submission_id


def _upsert_preliminary_review(connection, submission_id, review, created_at):
    criteria_json = json.dumps(review.get("criteria", []), ensure_ascii=False)
    connection.execute(
        """
        INSERT INTO preliminary_reviews (
            submission_id, completion_score, summary,
            criteria_json, created_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(submission_id) DO UPDATE SET
            completion_score = excluded.completion_score,
            summary = excluded.summary,
            criteria_json = excluded.criteria_json,
            created_at = excluded.created_at
        """,
        (
            submission_id,
            int(review["completion_score"]),
            str(review.get("summary", ""))[:1000],
            criteria_json,
            created_at,
        ),
    )


def save_preliminary_review(submission_id, review, created_at=None):
    """单独保存或更新一次按评分点生成的实验完成度初评。"""
    created_at = created_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_connection() as connection:
        _upsert_preliminary_review(connection, submission_id, review, created_at)


def get_preliminary_review(submission_id):
    """读取实验完成度初评及逐点评价证据。"""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM preliminary_reviews WHERE submission_id = ?",
            (submission_id,),
        ).fetchone()
    if row is None:
        return None
    review = dict(row)
    review["criteria"] = _load_json(review.pop("criteria_json", "[]"), [])
    return review


def save_initial_result(
    submission_id,
    question_index,
    answer,
    follow_up_question,
    evaluation,
    is_final,
):
    """保存首次回答、AI评价，以及可能生成的追问。"""
    evaluation_json = json.dumps(evaluation, ensure_ascii=False)
    final_json = evaluation_json if is_final else "{}"

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE qa_records
            SET
                answer = ?,
                follow_up_question = ?,
                reference_score = ?,
                initial_evaluation_json = ?,
                final_evaluation_json = ?
            WHERE submission_id = ? AND question_index = ?
            """,
            (
                answer,
                follow_up_question,
                evaluation["score"],
                evaluation_json,
                final_json,
                submission_id,
                question_index,
            ),
        )


def save_follow_up_result(
    submission_id,
    question_index,
    follow_up_answer,
    evaluation,
):
    """保存追问回答和该题最终AI评价。"""
    evaluation_json = json.dumps(evaluation, ensure_ascii=False)

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE qa_records
            SET
                follow_up_answer = ?,
                reference_score = ?,
                final_evaluation_json = ?
            WHERE submission_id = ? AND question_index = ?
            """,
            (
                follow_up_answer,
                evaluation["score"],
                evaluation_json,
                submission_id,
                question_index,
            ),
        )


def save_report(submission_id, report, created_at):
    """保存最终报告，并把提交状态改为已完成。"""
    dimensions_json = json.dumps(report["dimensions"], ensure_ascii=False)
    review_reasons_json = json.dumps(
        report.get("review_reasons", []),
        ensure_ascii=False,
    )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO reports (
                submission_id, overall, level, weakest,
                summary, suggestion, dimensions_json,
                review_required, review_reasons_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(submission_id) DO UPDATE SET
                overall = excluded.overall,
                level = excluded.level,
                weakest = excluded.weakest,
                summary = excluded.summary,
                suggestion = excluded.suggestion,
                dimensions_json = excluded.dimensions_json,
                review_required = excluded.review_required,
                review_reasons_json = excluded.review_reasons_json,
                created_at = excluded.created_at
            """,
            (
                submission_id,
                report["overall"],
                report["level"],
                report["weakest"],
                report["summary"],
                report["suggestion"],
                dimensions_json,
                int(report.get("review_required", False)),
                review_reasons_json,
                created_at,
            ),
        )
        connection.execute(
            "UPDATE submissions SET status = '已完成' WHERE id = ?",
            (submission_id,),
        )
        connection.execute(
            """
            UPDATE redefense_requests
            SET status = '已完成', completed_at = ?
            WHERE new_submission_id = ? AND status = '答辩中'
            """,
            (created_at, submission_id),
        )


def save_teacher_review(
    submission_id,
    reviewer_name,
    decision,
    confirmed_overall,
    comment,
    reviewed_at=None,
):
    """追加保存一次教师复核；再次复核不会覆盖历史记录。"""
    reviewer_name = str(reviewer_name).strip()
    decision = str(decision).strip()
    comment = "" if comment is None else str(comment).strip()

    if not reviewer_name:
        raise ValueError("请填写复核教师姓名。")
    if decision not in TEACHER_REVIEW_DECISIONS:
        raise ValueError("教师复核结论不在允许范围内。")
    try:
        confirmed_overall = round(float(confirmed_overall))
    except (TypeError, ValueError) as error:
        raise ValueError("教师确认理解度必须是0至100的数字。") from error
    if not 0 <= confirmed_overall <= 100:
        raise ValueError("教师确认理解度必须在0至100之间。")
    reviewed_at = reviewed_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_connection() as connection:
        report = connection.execute(
            "SELECT id, overall FROM reports WHERE submission_id = ?",
            (submission_id,),
        ).fetchone()
        if report is None:
            raise ValueError("该提交尚未生成AI报告，不能进行教师复核。")
        if decision == "认可AI诊断":
            confirmed_overall = report["overall"]

        cursor = connection.execute(
            """
            INSERT INTO teacher_reviews (
                submission_id, reviewer_name, decision,
                confirmed_overall, comment, reviewed_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                submission_id,
                reviewer_name[:40],
                decision,
                confirmed_overall,
                comment[:1000],
                reviewed_at,
            ),
        )
        teacher_review_id = cursor.lastrowid
        connection.execute(
            """
            UPDATE redefense_requests
            SET status = '已取消'
            WHERE
                original_submission_id = ?
                AND status IN ('待开始', '答辩中')
            """,
            (submission_id,),
        )
        if decision == "要求学生重新答辩":
            connection.execute(
                """
                INSERT INTO redefense_requests (
                    original_submission_id, teacher_review_id,
                    reason, status, requested_at
                )
                VALUES (?, ?, ?, '待开始', ?)
                """,
                (
                    submission_id,
                    teacher_review_id,
                    comment[:1000] or "教师要求针对关键证据完成重新答辩。",
                    reviewed_at,
                ),
            )
        return teacher_review_id


def get_teacher_review(submission_id):
    """读取某次提交最新的一条教师复核。"""
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM teacher_reviews
            WHERE submission_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (submission_id,),
        ).fetchone()
    return dict(row) if row else None


def list_teacher_review_history(submission_id):
    """按时间倒序读取教师复核历史。"""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM teacher_reviews
            WHERE submission_id = ?
            ORDER BY id DESC
            """,
            (submission_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_latest_redefense_request(submission_id):
    """读取针对某次提交最新的重新答辩要求。"""
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM redefense_requests
            WHERE original_submission_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (submission_id,),
        ).fetchone()
    return dict(row) if row else None


def get_redefense_request_by_new_submission(submission_id):
    """读取产生某次重新答辩提交的教师要求。"""
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM redefense_requests
            WHERE new_submission_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (submission_id,),
        ).fetchone()
    return dict(row) if row else None


def start_redefense(request_id, questions, started_at=None):
    """根据教师要求创建一次关联原提交的新答辩，重复点击时复用已有记录。"""
    if not questions:
        raise ValueError("重新答辩问题不能为空。")
    started_at = started_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_connection() as connection:
        request = connection.execute(
            """
            SELECT rr.*, s.*
            FROM redefense_requests AS rr
            INNER JOIN submissions AS s ON s.id = rr.original_submission_id
            WHERE rr.id = ?
            """,
            (request_id,),
        ).fetchone()
        if request is None:
            raise ValueError("没有找到对应的重新答辩要求。")
        if request["status"] == "答辩中" and request["new_submission_id"]:
            return request["new_submission_id"]
        if request["status"] != "待开始":
            raise ValueError("该重新答辩要求已经处理或取消。")

        cursor = connection.execute(
            """
            INSERT INTO submissions (
                assignment_id, parent_submission_id, attempt_number,
                student_id, name, problem, explanation, code,
                submitted_at, status, assignment_snapshot_json,
                lab_report, rubric_snapshot_json, code_verification_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '答辩中', ?, ?, ?, ?)
            """,
            (
                request["assignment_id"],
                request["original_submission_id"],
                int(request["attempt_number"] or 1) + 1,
                request["student_id"],
                request["name"],
                request["problem"],
                request["explanation"],
                request["code"],
                started_at,
                request["assignment_snapshot_json"],
                request["lab_report"],
                request["rubric_snapshot_json"],
                request["code_verification_json"],
            ),
        )
        new_submission_id = cursor.lastrowid
        connection.executemany(
            """
            INSERT INTO qa_records (
                submission_id, question_index, dimension,
                question, reference_points_json, question_reason
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    new_submission_id,
                    index,
                    question["dimension"],
                    question["question"],
                    json.dumps(question["reference_points"], ensure_ascii=False),
                    str(question.get("reason", "")).strip()[:300],
                )
                for index, question in enumerate(questions)
            ],
        )
        original_review = connection.execute(
            """
            SELECT completion_score, summary, criteria_json, created_at
            FROM preliminary_reviews
            WHERE submission_id = ?
            """,
            (request["original_submission_id"],),
        ).fetchone()
        if original_review:
            connection.execute(
                """
                INSERT INTO preliminary_reviews (
                    submission_id, completion_score, summary,
                    criteria_json, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    new_submission_id,
                    original_review["completion_score"],
                    original_review["summary"],
                    original_review["criteria_json"],
                    original_review["created_at"],
                ),
            )
        connection.execute(
            """
            UPDATE redefense_requests
            SET status = '答辩中', new_submission_id = ?
            WHERE id = ?
            """,
            (new_submission_id, request_id),
        )
        return new_submission_id


def save_student_feedback(
    submission_id,
    category,
    content,
    reply_requested=True,
    created_at=None,
):
    """保存学生在完成答辩后提交给教师的反馈。"""
    category = str(category).strip()
    content = str(content).strip()
    if category not in STUDENT_FEEDBACK_CATEGORIES:
        raise ValueError("学生反馈类型不在允许范围内。")
    if not content:
        raise ValueError("反馈内容不能为空。")
    if len(content) > 1000:
        raise ValueError("反馈内容不能超过1000个字符。")

    created_at = created_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_connection() as connection:
        submission = connection.execute(
            "SELECT status FROM submissions WHERE id = ?",
            (submission_id,),
        ).fetchone()
        if submission is None:
            raise ValueError("没有找到对应的实验提交。")
        if submission["status"] != "已完成":
            raise ValueError("完成AI答辩并生成报告后才能提交反馈。")

        duplicate = connection.execute(
            """
            SELECT id
            FROM student_feedbacks
            WHERE
                submission_id = ?
                AND category = ?
                AND content = ?
                AND status = '待处理'
            LIMIT 1
            """,
            (submission_id, category, content),
        ).fetchone()
        if duplicate:
            raise ValueError("相同反馈已经提交，请等待教师处理。")

        cursor = connection.execute(
            """
            INSERT INTO student_feedbacks (
                submission_id, category, content,
                reply_requested, status, created_at
            )
            VALUES (?, ?, ?, ?, '待处理', ?)
            """,
            (
                submission_id,
                category,
                content,
                int(bool(reply_requested)),
                created_at,
            ),
        )
        return cursor.lastrowid


def process_student_feedback(
    feedback_id,
    teacher_name,
    status,
    teacher_reply="",
    replied_at=None,
):
    """教师将反馈标记为已阅或回复，并保留处理人和时间。"""
    teacher_name = str(teacher_name).strip()
    status = str(status).strip()
    teacher_reply = str(teacher_reply).strip()
    if not teacher_name:
        raise ValueError("请填写处理教师姓名。")
    if status not in {"已阅", "已回复"}:
        raise ValueError("反馈处理状态只能是“已阅”或“已回复”。")
    if status == "已回复" and not teacher_reply:
        raise ValueError("选择“已回复”时，教师回复不能为空。")
    if len(teacher_reply) > 1000:
        raise ValueError("教师回复不能超过1000个字符。")

    replied_at = replied_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_connection() as connection:
        exists = connection.execute(
            "SELECT id FROM student_feedbacks WHERE id = ?",
            (feedback_id,),
        ).fetchone()
        if exists is None:
            raise ValueError("没有找到对应的学生反馈。")
        connection.execute(
            """
            UPDATE student_feedbacks
            SET
                status = ?,
                teacher_reply = ?,
                replied_by = ?,
                replied_at = ?,
                student_viewed_at = ''
            WHERE id = ?
            """,
            (
                status,
                teacher_reply if status == "已回复" else "",
                teacher_name[:40],
                replied_at,
                feedback_id,
            ),
        )


def mark_student_feedback_viewed(feedback_id, student_id, viewed_at=None):
    """学生查看教师回复后记录时间，让对应提醒自动完成。"""
    student_id = str(student_id).strip()
    viewed_at = viewed_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_connection() as connection:
        feedback = connection.execute(
            """
            SELECT sf.id, sf.status, s.student_id
            FROM student_feedbacks AS sf
            INNER JOIN submissions AS s ON s.id = sf.submission_id
            WHERE sf.id = ?
            """,
            (feedback_id,),
        ).fetchone()
        if feedback is None:
            raise ValueError("没有找到对应的学生反馈。")
        if feedback["student_id"] != student_id:
            raise ValueError("该反馈不属于当前学号。")
        if feedback["status"] != "已回复":
            raise ValueError("教师回复完成后才能标记为已查看。")
        connection.execute(
            """
            UPDATE student_feedbacks
            SET student_viewed_at = ?
            WHERE id = ? AND student_viewed_at = ''
            """,
            (viewed_at, feedback_id),
        )


def list_student_feedbacks(
    submission_id=None,
    assignment_id=None,
    status=None,
    student_id=None,
):
    """读取学生反馈，可按提交、实验或处理状态筛选。"""
    sql = """
        SELECT
            sf.*,
            s.student_id,
            s.name,
            s.problem,
            s.assignment_id,
            COALESCE(a.title, s.problem) AS assignment_title
        FROM student_feedbacks AS sf
        INNER JOIN submissions AS s ON s.id = sf.submission_id
        LEFT JOIN assignments AS a ON a.id = s.assignment_id
    """
    conditions = []
    parameters = []
    if submission_id is not None:
        conditions.append("sf.submission_id = ?")
        parameters.append(submission_id)
    if assignment_id is not None:
        conditions.append("s.assignment_id = ?")
        parameters.append(assignment_id)
    if student_id is not None:
        conditions.append("s.student_id = ?")
        parameters.append(str(student_id).strip())
    if status is not None:
        if status not in STUDENT_FEEDBACK_STATUSES:
            raise ValueError("学生反馈状态不在允许范围内。")
        conditions.append("sf.status = ?")
        parameters.append(status)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += """
        ORDER BY
            CASE sf.status WHEN '待处理' THEN 0 WHEN '已阅' THEN 1 ELSE 2 END,
            sf.reply_requested DESC,
            sf.id DESC
    """

    with get_connection() as connection:
        rows = connection.execute(sql, tuple(parameters)).fetchall()
    records = []
    for row in rows:
        record = dict(row)
        record["reply_requested"] = bool(record["reply_requested"])
        records.append(record)
    return records


def list_submissions(assignment_id=None, student_id=None, submission_id=None):
    """读取提交摘要，并可按实验、学号或提交编号筛选。"""
    sql = """
        SELECT
            s.id,
            s.assignment_id,
            s.parent_submission_id,
            s.attempt_number,
            s.student_id,
            s.name,
            s.problem,
            COALESCE(a.title, s.problem) AS assignment_title,
            s.submitted_at,
            s.status,
            s.assignment_snapshot_json,
            s.code_verification_json,
            r.overall,
            r.level,
            r.weakest,
            pr.completion_score,
            COALESCE(r.review_required, 0) AS review_required,
            CASE WHEN tr.id IS NULL THEN 0 ELSE 1 END AS teacher_reviewed,
            tr.decision AS teacher_decision,
            tr.confirmed_overall AS teacher_confirmed_overall,
            tr.reviewed_at AS teacher_reviewed_at,
            rr.status AS redefense_status,
            rr.reason AS redefense_reason,
            rr.new_submission_id AS redefense_submission_id,
            rr.requested_at AS redefense_requested_at,
            (
                SELECT COUNT(*)
                FROM student_feedbacks AS sf
                WHERE sf.submission_id = s.id
            ) AS feedback_count,
            (
                SELECT COUNT(*)
                FROM student_feedbacks AS sf
                WHERE sf.submission_id = s.id AND sf.status = '待处理'
            ) AS pending_feedback_count
        FROM submissions AS s
        LEFT JOIN assignments AS a ON a.id = s.assignment_id
        LEFT JOIN reports AS r ON r.submission_id = s.id
        LEFT JOIN preliminary_reviews AS pr ON pr.submission_id = s.id
        LEFT JOIN teacher_reviews AS tr ON tr.id = (
            SELECT latest.id
            FROM teacher_reviews AS latest
            WHERE latest.submission_id = s.id
            ORDER BY latest.id DESC
            LIMIT 1
        )
        LEFT JOIN redefense_requests AS rr ON rr.id = (
            SELECT latest_request.id
            FROM redefense_requests AS latest_request
            WHERE latest_request.original_submission_id = s.id
            ORDER BY latest_request.id DESC
            LIMIT 1
        )
    """
    conditions = []
    parameters = []
    if assignment_id is not None:
        conditions.append("s.assignment_id = ?")
        parameters.append(assignment_id)
    if student_id is not None:
        conditions.append("s.student_id = ?")
        parameters.append(student_id)
    if submission_id is not None:
        conditions.append("s.id = ?")
        parameters.append(int(submission_id))
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY s.id DESC"

    with get_connection() as connection:
        rows = connection.execute(sql, tuple(parameters)).fetchall()
    records = []
    for row in rows:
        record = dict(row)
        record["review_required"] = bool(record.get("review_required", 0))
        record["teacher_reviewed"] = bool(record.get("teacher_reviewed", 0))
        record["assignment_snapshot"] = _load_json(
            record.pop("assignment_snapshot_json", "{}"),
            {},
        )
        record["code_verification"] = _load_json(
            record.pop("code_verification_json", "{}"),
            {},
        )
        records.append(record)
    return records


def list_learning_records(assignment_id=None, student_id=None):
    """读取已完成报告，用于学习趋势和教师统计。"""
    sql = """
        SELECT
            s.id,
            s.assignment_id,
            s.parent_submission_id,
            s.attempt_number,
            s.student_id,
            s.name,
            s.problem,
            COALESCE(a.title, s.problem) AS assignment_title,
            s.submitted_at,
            r.overall,
            r.level,
            r.weakest,
            r.summary,
            r.suggestion,
            pr.completion_score,
            r.dimensions_json,
            COALESCE(r.review_required, 0) AS review_required,
            r.review_reasons_json,
            r.created_at AS report_created_at,
            CASE WHEN tr.id IS NULL THEN 0 ELSE 1 END AS teacher_reviewed,
            tr.reviewer_name AS teacher_reviewer_name,
            tr.decision AS teacher_decision,
            tr.confirmed_overall AS teacher_confirmed_overall,
            tr.comment AS teacher_comment,
            tr.reviewed_at AS teacher_reviewed_at,
            rr.status AS redefense_status,
            rr.reason AS redefense_reason,
            rr.new_submission_id AS redefense_submission_id,
            rr.requested_at AS redefense_requested_at,
            (
                SELECT COUNT(*)
                FROM student_feedbacks AS sf
                WHERE sf.submission_id = s.id
            ) AS feedback_count,
            (
                SELECT COUNT(*)
                FROM student_feedbacks AS sf
                WHERE sf.submission_id = s.id AND sf.status = '待处理'
            ) AS pending_feedback_count
        FROM submissions AS s
        LEFT JOIN assignments AS a ON a.id = s.assignment_id
        INNER JOIN reports AS r ON r.submission_id = s.id
        LEFT JOIN preliminary_reviews AS pr ON pr.submission_id = s.id
        LEFT JOIN teacher_reviews AS tr ON tr.id = (
            SELECT latest.id
            FROM teacher_reviews AS latest
            WHERE latest.submission_id = s.id
            ORDER BY latest.id DESC
            LIMIT 1
        )
        LEFT JOIN redefense_requests AS rr ON rr.id = (
            SELECT latest_request.id
            FROM redefense_requests AS latest_request
            WHERE latest_request.original_submission_id = s.id
            ORDER BY latest_request.id DESC
            LIMIT 1
        )
    """
    conditions = []
    parameters = []
    if assignment_id is not None:
        conditions.append("s.assignment_id = ?")
        parameters.append(assignment_id)
    if student_id is not None:
        conditions.append("s.student_id = ?")
        parameters.append(student_id)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY s.id ASC"

    with get_connection() as connection:
        rows = connection.execute(sql, tuple(parameters)).fetchall()

    records = []
    for row in rows:
        record = dict(row)
        record["dimensions"] = _load_json(
            record.pop("dimensions_json"),
            {},
        )
        record["review_required"] = bool(record.get("review_required", 0))
        record["review_reasons"] = _load_json(
            record.pop("review_reasons_json"),
            [],
        )
        record["teacher_reviewed"] = bool(record.get("teacher_reviewed", 0))
        records.append(record)
    return records


def get_submission(submission_id):
    """按编号读取一份完整提交。"""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM submissions WHERE id = ?",
            (submission_id,),
        ).fetchone()
        if row is None:
            return None
        submission = dict(row)
        submission["assignment_snapshot"] = _load_json(
            submission.pop("assignment_snapshot_json", "{}"),
            {},
        )
        submission["rubric_snapshot"] = _load_json(
            submission.pop("rubric_snapshot_json", "[]"),
            [],
        )
        submission["code_verification"] = _load_json(
            submission.pop("code_verification_json", "{}"),
            {},
        )
        return submission


def _load_json(text, default):
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return default
    return value


def get_qa_records(submission_id):
    """读取问答记录，并把三个JSON字段还原成Python数据。"""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM qa_records
            WHERE submission_id = ?
            ORDER BY question_index
            """,
            (submission_id,),
        ).fetchall()

    records = []
    for row in rows:
        record = dict(row)
        record["reference_points"] = _load_json(
            record.pop("reference_points_json"),
            [],
        )
        record["initial_evaluation"] = _load_json(
            record.pop("initial_evaluation_json"),
            {},
        )
        record["final_evaluation"] = _load_json(
            record.pop("final_evaluation_json"),
            {},
        )
        records.append(record)
    return records


def get_report(submission_id):
    """读取报告，并把四维JSON还原成Python字典。"""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM reports WHERE submission_id = ?",
            (submission_id,),
        ).fetchone()

    if row is None:
        return None

    report = dict(row)
    report["dimensions"] = _load_json(report.pop("dimensions_json"), {})
    report["review_required"] = bool(report.get("review_required", 0))
    report["review_reasons"] = _load_json(
        report.pop("review_reasons_json", "[]"),
        [],
    )
    report["teacher_review"] = get_teacher_review(submission_id)
    report["student_feedbacks"] = list_student_feedbacks(
        submission_id=submission_id
    )
    return report


def _sort_tasks(tasks):
    """待处理优先、紧急优先；同级任务按产生时间排序。"""
    return sorted(
        tasks,
        key=lambda item: (
            item["status"] != "待处理",
            item["priority"] != "紧急",
            item.get("created_at", ""),
            item["task_key"],
        ),
    )


def list_student_tasks(student_id, include_completed=False):
    """根据现有业务状态生成学生待办，不重复保存任务副本。"""
    student_id = str(student_id).strip()
    if not student_id:
        return []

    tasks = []
    submissions = list_submissions(student_id=student_id)
    for record in submissions:
        attempt_number = int(record.get("attempt_number") or 1)
        if record["status"] == "答辩中":
            is_redefense = bool(record.get("parent_submission_id"))
            tasks.append(
                {
                    "task_key": f"student-defense-{record['id']}",
                    "task_type": "重新答辩" if is_redefense else "AI答辩",
                    "title": "继续重新答辩" if is_redefense else "继续AI答辩",
                    "description": (
                        f"{record['assignment_title']} · 第{attempt_number}次答辩尚未完成"
                    ),
                    "priority": "紧急" if is_redefense else "普通",
                    "status": "待处理",
                    "created_at": record["submitted_at"],
                    "submission_id": record["id"],
                    "assignment_id": record.get("assignment_id"),
                    "feedback_id": None,
                    "action": "continue_defense",
                    "action_label": "继续答辩",
                }
            )

        if record.get("redefense_status") == "待开始":
            tasks.append(
                {
                    "task_key": f"student-redefense-{record['id']}",
                    "task_type": "重新答辩",
                    "title": "教师要求重新答辩",
                    "description": record.get("redefense_reason") or "请根据教师要求完成新一轮答辩。",
                    "priority": "紧急",
                    "status": "待处理",
                    "created_at": record.get("redefense_requested_at") or record["submitted_at"],
                    "submission_id": record["id"],
                    "assignment_id": record.get("assignment_id"),
                    "feedback_id": None,
                    "action": "start_redefense",
                    "action_label": "开始重新答辩",
                }
            )
        elif include_completed and record.get("redefense_status") == "已完成":
            tasks.append(
                {
                    "task_key": f"student-redefense-{record['id']}",
                    "task_type": "重新答辩",
                    "title": "重新答辩已完成",
                    "description": f"{record['assignment_title']}的新一轮答辩已经生成报告。",
                    "priority": "普通",
                    "status": "已完成",
                    "created_at": record.get("redefense_requested_at") or record["submitted_at"],
                    "submission_id": record.get("redefense_submission_id") or record["id"],
                    "assignment_id": record.get("assignment_id"),
                    "feedback_id": None,
                    "action": "view_report",
                    "action_label": "查看重答报告",
                }
            )

    feedbacks = list_student_feedbacks(student_id=student_id)
    for feedback in feedbacks:
        if feedback["status"] != "已回复":
            continue
        viewed = bool(feedback.get("student_viewed_at"))
        if viewed and not include_completed:
            continue
        tasks.append(
            {
                "task_key": f"student-feedback-{feedback['id']}",
                "task_type": "教师回复",
                "title": "教师回复已查看" if viewed else "教师回复待查看",
                "description": feedback["teacher_reply"],
                "priority": "普通",
                "status": "已完成" if viewed else "待处理",
                "created_at": feedback["replied_at"] or feedback["created_at"],
                "submission_id": feedback["submission_id"],
                "assignment_id": feedback.get("assignment_id"),
                "feedback_id": feedback["id"],
                "action": "view_reply",
                "action_label": "查看教师回复",
            }
        )
    return _sort_tasks(tasks)


def list_teacher_tasks(assignment_id=None, include_completed=False):
    """生成教师复核、反馈处理和重答进度待办。"""
    import teacher_insights as insights

    tasks = []
    submissions = list_submissions(assignment_id=assignment_id)
    for record in submissions:
        attempt_number = int(record.get("attempt_number") or 1)
        report = get_report(record["id"])
        qa_records = get_qa_records(record["id"])
        review_state = insights.teacher_review_state(
            record,
            report=report,
            qa_records=qa_records,
        )
        if review_state["状态"] == "待复核":
            is_redefense = attempt_number > 1
            tasks.append(
                {
                    "task_key": f"teacher-review-{record['id']}",
                    "task_type": "人工复核",
                    "title": "复核重新答辩结果" if is_redefense else "复核AI诊断",
                    "description": (
                        f"{record['name']} · {record['student_id']} · "
                        f"{record['assignment_title']}"
                    ),
                    "priority": (
                        "紧急"
                        if is_redefense
                        or (
                            record.get("overall") is not None
                            and record["overall"] < 70
                        )
                        else "普通"
                    ),
                    "status": "待处理",
                    "created_at": record["submitted_at"],
                    "submission_id": record["id"],
                    "assignment_id": record.get("assignment_id"),
                    "feedback_id": None,
                    "action": "review_submission",
                    "action_label": "前往复核",
                }
            )
        elif include_completed and review_state["状态"] == "已复核":
            tasks.append(
                {
                    "task_key": f"teacher-review-{record['id']}",
                    "task_type": "人工复核",
                    "title": "教师复核已完成",
                    "description": (
                        f"{record['name']} · {record['student_id']} · "
                        f"{record.get('teacher_decision') or '已处理'}"
                    ),
                    "priority": "普通",
                    "status": "已完成",
                    "created_at": record.get("teacher_reviewed_at") or record["submitted_at"],
                    "submission_id": record["id"],
                    "assignment_id": record.get("assignment_id"),
                    "feedback_id": None,
                    "action": "review_submission",
                    "action_label": "查看复核记录",
                }
            )

        if record.get("redefense_status") in {"待开始", "答辩中"}:
            tasks.append(
                {
                    "task_key": f"teacher-redefense-{record['id']}",
                    "task_type": "重答跟进",
                    "title": (
                        "学生尚未开始重新答辩"
                        if record["redefense_status"] == "待开始"
                        else "学生正在重新答辩"
                    ),
                    "description": (
                        f"{record['name']} · {record['student_id']} · "
                        f"{record['assignment_title']}"
                    ),
                    "priority": "普通",
                    "status": "待处理",
                    "created_at": record.get("redefense_requested_at") or record["submitted_at"],
                    "submission_id": record.get("redefense_submission_id") or record["id"],
                    "assignment_id": record.get("assignment_id"),
                    "feedback_id": None,
                    "action": "view_submission",
                    "action_label": "查看进度",
                }
            )

    feedbacks = list_student_feedbacks(assignment_id=assignment_id)
    for feedback in feedbacks:
        pending = feedback["status"] == "待处理"
        if not pending and not include_completed:
            continue
        tasks.append(
            {
                "task_key": f"teacher-feedback-{feedback['id']}",
                "task_type": "学生反馈",
                "title": (
                    "回复学生反馈"
                    if pending and feedback["reply_requested"]
                    else ("查看学生反馈" if pending else "学生反馈已处理")
                ),
                "description": (
                    f"{feedback['name']} · {feedback['student_id']} · "
                    f"{feedback['category']}"
                ),
                "priority": "紧急" if pending and feedback["reply_requested"] else "普通",
                "status": "待处理" if pending else "已完成",
                "created_at": feedback["created_at"],
                "submission_id": feedback["submission_id"],
                "assignment_id": feedback.get("assignment_id"),
                "feedback_id": feedback["id"],
                "action": "process_feedback",
                "action_label": "前往处理" if pending else "查看处理结果",
            }
        )
    return _sort_tasks(tasks)
