"""悟码AI只读 MCP 连接器，供 LearnBuddy 教师助手读取结构化诊断。"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database as db  # noqa: E402
import teacher_insights as insights  # noqa: E402


PROTOCOL_VERSION = "2025-06-18"


def data_snapshot(records):
    ids = [int(item["id"]) for item in records if item.get("id") is not None]
    return {
        "读取时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "最新提交编号": max(ids) if ids else None,
    }


def compact_code(record):
    verification = record.get("code_verification") or {}
    status = verification.get("overall_status", "未执行")
    passed, total = verification.get("passed", 0), verification.get("total", 0)
    score, maximum = verification.get("score"), verification.get("max_score")
    return {
        "status": status,
        "passed": passed,
        "total": total,
        "score": score if maximum else None,
        "max_score": maximum if maximum else None,
    }


def overview(arguments):
    assignment_id = arguments.get("assignment_id")
    records = insights.mark_superseded_records(db.list_submissions(assignment_id))
    tasks = db.list_teacher_tasks(assignment_id=assignment_id)
    latest = {}
    for record in records:
        if record.get("status") != "已完成" or record.get("overall") is None:
            continue
        student = str(record.get("student_id") or "").strip() or f"submission:{record['id']}"
        assignment = record.get("assignment_id")
        assignment = ("id", assignment) if assignment is not None else ("title", record.get("problem"))
        key = (student, assignment)
        if key not in latest or int(record["id"]) > int(latest[key]["id"]):
            latest[key] = record
    scores = [item["overall"] for item in latest.values()]
    pending_review = sum(
        item.get("task_type") == "人工复核" and item.get("status") == "待处理"
        for item in tasks
    )
    pending_feedback = sum(
        item.get("task_type") == "学生反馈" and item.get("status") == "待处理"
        for item in tasks
    )
    active_technical = sum(
        ((item.get("code_verification") or {}).get("overall_status", "未执行")
         not in {"全部通过", "未启用", "未执行"}) and not item.get("_code_resolved_by")
        for item in records
    )
    newest = max(records, key=lambda item: int(item["id"])) if records else None
    newest_summary = None
    if newest:
        newest_report = db.get_report(newest["id"])
        newest_qa = db.get_qa_records(newest["id"])
        newest_preliminary = db.get_preliminary_review(newest["id"])
        newest_mastery = insights.mastery_summary(newest_report, newest_qa)
        newest_review = insights.teacher_review_state(
            newest,
            report=newest_report,
            qa_records=newest_qa,
            mastery=newest_mastery,
        )
        newest_summary = {
            "提交": newest["id"],
            "学生": {"学号": newest.get("student_id"), "姓名": newest.get("name")},
            "实验": newest.get("assignment_title") or newest.get("problem"),
            "状态": newest.get("status"),
            "代码判题": compact_code(newest),
            "答辩理解度": newest_report.get("overall") if newest_report else None,
            "评分点证据覆盖度": (
                newest_preliminary.get("completion_score")
                if newest_preliminary else None
            ),
            "掌握证据": newest_mastery,
            "教师复核状态": newest_review["状态"],
            "是否当前待办": newest_review["是否当前待办"],
            "教师复核原因": newest_review["原因"],
            "教师结论": newest.get("teacher_decision"),
            "重点关注": insights.ranked_concerns(
                newest,
                newest_preliminary,
                newest_report,
                newest_qa,
            ),
        }
    return {
        "数据快照": data_snapshot(records),
        "统计口径": "每名学生、每个实验取最新一次已完成答辩；历史记录仍保留",
        "指标口径": "代码判题、答辩理解度与评分点证据覆盖度相互独立，均不等同课程成绩",
        "历史提交数": len(records),
        "纳入掌握统计": len(latest),
        "平均答辩理解度": round(sum(scores) / len(scores)) if scores else None,
        "教师待办总数": len(tasks),
        "待教师复核": pending_review,
        "待处理反馈": pending_feedback,
        "当前评测异常": active_technical,
        "最新提交摘要": newest_summary,
    }


def pending_tasks(arguments):
    limit = max(1, min(int(arguments.get("limit", 20)), 50))
    tasks = db.list_teacher_tasks(assignment_id=arguments.get("assignment_id"))[:limit]
    return [{key: item.get(key) for key in (
        "task_type", "title", "description", "priority", "status",
        "submission_id", "assignment_id", "action_label",
    )} for item in tasks]


def student_diagnosis(arguments):
    submission_id = int(arguments["submission_id"])
    records = db.list_submissions(submission_id=submission_id)
    if not records:
        raise ValueError(f"没有找到提交#{submission_id}")
    record = records[0]
    report = db.get_report(submission_id)
    qa = db.get_qa_records(submission_id)
    preliminary = db.get_preliminary_review(submission_id)
    feedbacks = db.list_student_feedbacks(submission_id=submission_id)
    mastery = insights.mastery_summary(report, qa)
    review_state = insights.teacher_review_state(
        record,
        report=report,
        qa_records=qa,
        mastery=mastery,
    )
    questions = []
    for index, item in enumerate(qa, 1):
        _, status, detail = insights.question_finding(item, index)
        questions.append({
            "题号": index,
            "维度": item.get("dimension") or "综合理解",
            "证据状态": status,
            "摘要": detail,
        })
    return {
        "数据快照": data_snapshot([record]),
        "提交": submission_id,
        "学生": {"学号": record.get("student_id"), "姓名": record.get("name")},
        "实验": record.get("assignment_title") or record.get("problem"),
        "代码判题": compact_code(record),
        "评分点证据覆盖度": preliminary.get("completion_score") if preliminary else None,
        "答辩理解度": report.get("overall") if report else None,
        "掌握证据": mastery,
        "重点关注": insights.ranked_concerns(record, preliminary, report, qa),
        "逐题证据摘要": questions,
        "教师复核": {
            "状态": review_state["状态"],
            "是否当前待办": review_state["是否当前待办"],
            "原因": review_state["原因"],
            "结论": record.get("teacher_decision"),
            "确认理解度": record.get("teacher_confirmed_overall"),
        },
        "指标口径": "代码全部通过不代表已掌握原理；答辩理解度和证据覆盖度不是课程成绩",
        "待处理反馈数": sum(item.get("status") == "待处理" for item in feedbacks),
        "隐私说明": "未输出学生代码、回答原文、隐藏测试输入输出或隐藏教学重点",
    }


def student_progress(arguments):
    student_id = str(arguments["student_id"]).strip()
    records = db.list_submissions(student_id=student_id)
    items = []
    for record in records:
        report = db.get_report(record["id"])
        qa = db.get_qa_records(record["id"])
        preliminary = db.get_preliminary_review(record["id"])
        mastery = insights.mastery_summary(report, qa)
        review_state = insights.teacher_review_state(
            record,
            report=report,
            qa_records=qa,
            mastery=mastery,
        )
        items.append({
            "提交": record["id"],
            "实验": record.get("assignment_title") or record.get("problem"),
            "提交时间": record.get("submitted_at"),
            "状态": record.get("status"),
            "代码判题": compact_code(record),
            "答辩理解度": report.get("overall") if report else None,
            "评分点证据覆盖度": (
                preliminary.get("completion_score") if preliminary else None
            ),
            "掌握证据": mastery,
            "教师复核状态": review_state["状态"],
            "是否当前待办": review_state["是否当前待办"],
            "教师结论": record.get("teacher_decision"),
        })
    return {
        "数据快照": data_snapshot(records),
        "学号": student_id,
        "指标口径": "代码判题、答辩理解度与评分点证据覆盖度应分别陈述",
        "提交记录": items,
    }


TOOLS = [
    {"name": "wuma_teacher_overview", "description": "查看悟码AI班级概况、教师待办数量和当前评测异常；可按实验筛选。",
     "inputSchema": {"type": "object", "properties": {"assignment_id": {"type": "integer", "description": "实验ID；省略表示全部实验"}}}},
    {"name": "wuma_pending_teacher_tasks", "description": "列出悟码AI中待教师复核、待回复反馈和重答跟进任务。",
     "inputSchema": {"type": "object", "properties": {"assignment_id": {"type": "integer"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}}},
    {"name": "wuma_student_diagnosis", "description": "按提交编号读取精简、可核查的学生诊断，不返回学生代码、回答原文、隐藏测试和隐藏教学重点。",
     "inputSchema": {"type": "object", "properties": {"submission_id": {"type": "integer", "minimum": 1}}, "required": ["submission_id"]}},
    {"name": "wuma_student_progress", "description": "按学号查看各次提交的判题、答辩理解度、证据覆盖与统一教师复核状态。",
     "inputSchema": {"type": "object", "properties": {"student_id": {"type": "string", "minLength": 1}}, "required": ["student_id"]}},
]


HANDLERS = {
    "wuma_teacher_overview": overview,
    "wuma_pending_teacher_tasks": pending_tasks,
    "wuma_student_diagnosis": student_diagnosis,
    "wuma_student_progress": student_progress,
}


def reply(message):
    method, request_id = message.get("method"), message.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        requested = (message.get("params") or {}).get("protocolVersion")
        selected_protocol = requested if requested in {"2024-11-05", "2025-03-26", PROTOCOL_VERSION} else "2024-11-05"
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": selected_protocol,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "wuma-ai-learnbuddy", "version": "1.3.15"},
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        if name not in HANDLERS:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "未知工具"}}
        try:
            result = HANDLERS[name](params.get("arguments") or {})
            content = json.dumps(result, ensure_ascii=False, indent=2)
            return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": content}], "isError": False}}
        except (ValueError, TypeError, KeyError, OSError) as error:
            return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"调用失败：{error}"}], "isError": True}}
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "不支持的方法"}}


def main():
    db_path = Path(os.getenv("WUMA_AI_DB_PATH", str(db.DB_PATH)))
    if not db_path.is_file():
        print(f"悟码AI数据库不存在：{db_path}", file=sys.stderr, flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            response = reply(json.loads(line))
        except json.JSONDecodeError as error:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(error)}}
        if response is not None:
            print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
