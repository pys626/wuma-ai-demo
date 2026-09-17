"""Public demo helpers and deterministic synthetic teaching records."""

import os
import threading

import database as db


DEMO_STUDENT_NAME = "张同学"
DEMO_STUDENT_ID = "20260001"
DEMO_TEACHER_NAME = "演示教师"

_SEED_LOCK = threading.Lock()


def _env_flag(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def demo_mode_enabled():
    """Return whether the browser-safe public demo entry is enabled."""
    return _env_flag("WUMA_DEMO_MODE", False)


def demo_read_only():
    """Public demos are read-only by default unless explicitly overridden."""
    return demo_mode_enabled() and _env_flag("WUMA_DEMO_READ_ONLY", True)


def _questions():
    return [
        {
            "dimension": "程序逻辑理解",
            "question": "为什么循环从第二个数组元素开始比较？",
            "reference_points": ["首元素已作为初值", "避免重复比较"],
            "reason": "核验循环起点与初始化之间的关系。",
        },
        {
            "dimension": "边界情况意识",
            "question": "为什么最大值不能简单初始化为0？",
            "reference_points": ["全负数输入", "初值来自真实元素"],
            "reason": "核验学生是否理解全负数这一关键边界。",
        },
        {
            "dimension": "分析与修改能力",
            "question": "如果改为寻找最小值，需要修改哪些位置？",
            "reference_points": ["比较方向", "变量语义"],
            "reason": "核验学生能否迁移现有算法。",
        },
    ]


def _evaluation(score, status, evidence, missing=None, confidence="高"):
    missing = list(missing or [])
    return {
        "score": score,
        "confidence": confidence,
        "feedback": evidence,
        "misconceptions": missing if status == "理解错误" else [],
        "missing_points": missing,
        "point_assessments": [
            {
                "reference_point": evidence.split("；", 1)[0],
                "status": status,
                "evidence": evidence,
            }
        ],
    }


def _verification(passed, scores):
    names = ["普通正数", "全部负数", "单个元素"]
    weights = [40, 35, 25]
    cases = []
    for index, (name, weight, case_score) in enumerate(
        zip(names, weights, scores)
    ):
        ok = case_score == weight
        cases.append(
            {
                "name": name,
                "group": "基础功能" if index == 0 else "边界情况",
                "weight": weight,
                "score": case_score,
                "verdict_code": "AC" if ok else "WA",
                "status": "通过" if ok else "未通过",
                "input": "演示数据",
                "expected_output": "演示数据",
                "actual_output": "演示数据" if ok else "与预期不一致",
                "stderr": "",
                "duration_ms": 3 + index,
                "message": "合成演示判题记录。",
            }
        )
    score = sum(scores)
    if passed == 3:
        overall = "全部通过"
    elif passed:
        overall = "部分通过"
    else:
        overall = "全部未通过"
    return {
        "enabled": True,
        "overall_status": overall,
        "message": f"合成演示：3组测试，通过{passed}组。",
        "compiler": "演示判题器",
        "compile": {"status": "成功", "message": "合成演示记录。"},
        "passed": passed,
        "total": 3,
        "score": score,
        "max_score": 100,
        "requested_cpp_standard": "auto",
        "effective_cpp_standard": "c++17",
        "group_scores": [
            {
                "group": "全部测试",
                "score": score,
                "max_score": 100,
                "passed": passed,
                "total": 3,
            }
        ],
        "cases": cases,
    }


def _preliminary(score, statuses):
    criteria = []
    for rubric, status in zip(db.DEFAULT_RUBRIC, statuses):
        criteria.append(
            {
                "criterion_name": rubric["name"],
                "source": rubric["source"],
                "status": status,
                "score": rubric["weight"] if status == "满足" else round(rubric["weight"] * 0.6),
                "weight": rubric["weight"],
                "needs_defense": bool(rubric["must_defend"]),
                "confidence": "高" if status == "满足" else "中",
                "code_evidence": "代码结构与合成判题记录可核验。",
                "report_evidence": "实验说明已覆盖主要步骤。",
                "missing": "" if status == "满足" else "边界依据仍需通过答辩确认。",
                "defense_focus": rubric["description"],
            }
        )
    return {
        "completion_score": score,
        "summary": "根据代码、实验说明与合成判题记录生成的演示初评。",
        "criteria": criteria,
    }


def _seed_submission(
    assignment,
    student_id,
    name,
    submitted_at,
    overall,
    dimensions,
    answers,
    evaluations,
    verification,
    completion_score,
    preliminary_statuses,
    review_required,
    review_reasons,
    teacher_review=False,
):
    questions = _questions()
    submission_id = db.create_submission_with_questions(
        student_id,
        name,
        assignment["title"],
        "先以首元素初始化最大值，再遍历其余元素；同时检查输入规模与数组边界。",
        db.DEFAULT_STARTER_CODE,
        submitted_at,
        questions,
        assignment=assignment,
        lab_report="本记录为在线Demo自动生成的合成教学数据，不对应真实学生。",
        preliminary_review=_preliminary(completion_score, preliminary_statuses),
        code_verification=verification,
    )
    for index, (answer, evaluation) in enumerate(zip(answers, evaluations)):
        db.save_initial_result(
            submission_id,
            index,
            answer,
            "",
            evaluation,
            True,
        )
    weakest = min(dimensions, key=dimensions.get)
    db.save_report(
        submission_id,
        {
            "overall": overall,
            "level": "理解扎实" if overall >= 90 else ("理解较好" if overall >= 80 else "需要巩固"),
            "weakest": weakest,
            "summary": "能够解释主要程序逻辑；诊断结论来自合成演示证据。",
            "suggestion": "继续用边界输入验证初始化与循环条件。",
            "dimensions": dimensions,
            "review_required": review_required,
            "review_reasons": review_reasons,
        },
        submitted_at,
    )
    if teacher_review:
        db.save_teacher_review(
            submission_id,
            DEMO_TEACHER_NAME,
            "认可AI诊断",
            overall,
            "已核对合成演示证据。",
            submitted_at,
        )
    return submission_id


def ensure_demo_data():
    """Seed deterministic synthetic records only for an empty demo database."""
    if not demo_mode_enabled():
        return False
    with _SEED_LOCK:
        if db.list_submissions():
            return False
        assignments = db.list_assignments(published_only=True)
        if not assignments:
            return False
        assignment = assignments[-1]

        first_id = _seed_submission(
            assignment,
            DEMO_STUDENT_ID,
            DEMO_STUDENT_NAME,
            "2026-09-15 09:20",
            82,
            {
                "程序逻辑理解": 88,
                "关键概念掌握": 84,
                "边界情况意识": 72,
                "分析与修改能力": 83,
            },
            [
                "首元素已经参与初始化，所以从第二个元素开始即可。",
                "不能设为0，否则全部负数时会错误输出0。",
                "把大于号改为小于号，并把变量改为minValue。",
            ],
            [
                _evaluation(88, "掌握", "首元素已作为初值；循环起点解释正确"),
                _evaluation(78, "部分掌握", "全负数输入；已经指出0初值的问题", ["未说明空输入约束"]),
                _evaluation(82, "掌握", "比较方向；能够说明变量语义变化"),
            ],
            _verification(2, [40, 0, 25]),
            82,
            ["满足", "满足", "部分满足", "满足"],
            True,
            ["首次提交的边界情况说明仍不完整。"],
            True,
        )

        _seed_submission(
            assignment,
            DEMO_STUDENT_ID,
            DEMO_STUDENT_NAME,
            "2026-09-16 14:10",
            93,
            {
                "程序逻辑理解": 95,
                "关键概念掌握": 94,
                "边界情况意识": 91,
                "分析与修改能力": 92,
            },
            [
                "arr[0]已经成为当前最大值，循环从下标1开始能保持不变量。",
                "0不属于实际数组时不能作为候选最大值；全负数会得到错误结果。",
                "将比较条件改为小于，并用首元素初始化minValue。",
            ],
            [
                _evaluation(95, "掌握", "首元素已作为初值；并说明了循环不变量"),
                _evaluation(93, "掌握", "全负数输入；初值必须来自真实元素"),
                _evaluation(92, "掌握", "比较方向；变量语义与初始化同步修改"),
            ],
            _verification(3, [40, 35, 25]),
            95,
            ["满足", "满足", "满足", "满足"],
            False,
            [],
            True,
        )

        pending_id = _seed_submission(
            assignment,
            "20260002",
            "王同学",
            "2026-09-17 10:05",
            74,
            {
                "程序逻辑理解": 82,
                "关键概念掌握": 76,
                "边界情况意识": 60,
                "分析与修改能力": 77,
            },
            [
                "从第二个元素继续比较。",
                "设为0比较方便。",
                "把大于号改成小于号。",
            ],
            [
                _evaluation(82, "掌握", "首元素已作为初值；循环顺序基本正确"),
                _evaluation(55, "理解错误", "全负数输入；未认识到0不是数组元素", ["0初值会导致全负数输入错误"]),
                _evaluation(77, "部分掌握", "比较方向；没有同步说明变量语义", ["初始化与变量命名也需同步修改"]),
            ],
            _verification(3, [40, 35, 25]),
            79,
            ["满足", "部分满足", "部分满足", "部分满足"],
            True,
            ["第2题存在明确边界理解错误，需要教师复核。"],
            False,
        )
        db.save_student_feedback(
            pending_id,
            "希望获得学习指导",
            "我想进一步理解为什么最大值初值必须来自真实数组元素。",
            True,
            "2026-09-17 10:20",
        )
        return bool(first_id and pending_id)
