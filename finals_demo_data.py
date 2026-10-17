"""Load two explicitly synthetic, saved real-model cases into a NEW demo database."""
import inspect
import json
from pathlib import Path
import threading

import database as db
import demo_mode
import accounts

_LOCK = threading.Lock()
FIXTURE = Path(__file__).resolve().parent / "demo_assets/finals_cases.json"

def seed_finals_cases():
    demo_mode.validate_demo_database(db.current_database_path())
    with _LOCK:
        with db.get_connection() as connection:
            marker = connection.execute("SELECT value FROM wuma_demo_metadata WHERE key='finals_case_set'").fetchone()
        if marker:
            if marker[0] != "finals-20261010":
                raise ValueError("案例集版本不匹配，请使用新的独立演示数据库路径。")
            return False
        if accounts.enabled() or db.list_submissions():
            raise ValueError("决赛案例入口只初始化空的独立演示数据库，请指定新的文件路径；现有记录不被覆盖。")
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        if fixture.get("synthetic") is not True or fixture.get("case_set") != "finals-20261010":
            raise ValueError("演示案例缺少可验证的合成标记。")
        assignment = {key: value for key, value in fixture["assignment"].items()
                      if key in inspect.signature(db.create_assignment).parameters}
        assignment.setdefault("starter_code", "// 在这里填写选择排序程序。\n")
        assignment.setdefault("reference_code", fixture["code"])
        assignment_id = db.create_assignment(**assignment)
        assignment = db.get_assignment(assignment_id)
        for case in fixture["cases"]:
            questions = [{"dimension": qa["dimension"], "question": qa["question"],
                          "reference_points": qa["reference_points"],
                          "reference_dimensions": qa["reference_dimensions"], "reason": qa.get("question_reason", "")}
                         for qa in case["qa"]]
            sid = db.create_submission_with_questions(case["student_id"], case["name"], assignment["title"],
                   fixture["explanation"], fixture["code"], "2026-10-10 20:00", questions,
                   assignment=assignment, lab_report="合成演示作答；评价来自已保存的真实模型调用，不是实际学生效果。",
                   preliminary_review=fixture["preliminary"], code_verification=fixture["verification"])
            for index, qa in enumerate(case["qa"]):
                initial = qa["initial_evaluation"]
                db.save_initial_result(sid, index, qa["answer"], qa["follow_up_question"], initial,
                                       is_final=not bool(qa["follow_up_answer"]))
                if qa["follow_up_answer"]:
                    db.save_follow_up_result(sid, index, qa["follow_up_answer"], qa["final_evaluation"])
            db.save_report(sid, case["report"], "2026-10-10 20:20")
            db.save_teacher_review(sid, "演示教师", case["teacher_action"],
                    case["report"]["overall"] if case["teacher_action"] == "认可AI诊断" else None,
                    case["teacher_reason"], "2026-10-10 20:39")
            if case.get("redefense_questions"):
                request = db.get_latest_redefense_request(sid)
                db.start_redefense(request["id"], case["redefense_questions"], "2026-10-10 20:45")
        with db.get_connection() as connection:
            connection.execute("INSERT INTO wuma_demo_metadata VALUES ('finals_case_set', 'finals-20261010')")
        return True
