"""Save a single answer draft without changing official answer/evaluation records."""
from app_clock import now_beijing

from local_store import fingerprint, read_json, scoped_path, write_json


def identity(student_id, submission_id, question_index, stage, question):
    if not str(student_id).strip() or not isinstance(submission_id, int) or submission_id < 1:
        raise ValueError("答辩草稿缺少学生或提交身份。")
    if stage not in {"initial", "follow_up"} or not isinstance(question_index, int) or question_index < 0:
        raise ValueError("答辩草稿的题号或回答阶段无效。")
    return [str(student_id).strip(), submission_id, question_index, stage, fingerprint(question)]


def path(database, student_id, submission_id, question_index, stage, question):
    return scoped_path(database, "defense_drafts", identity(student_id, submission_id, question_index, stage, question))


def load(database, student_id, submission_id, question_index, stage, question):
    expected = identity(student_id, submission_id, question_index, stage, question)
    document = read_json(path(database, student_id, submission_id, question_index, stage, question))
    if document is None:
        return None
    if (not isinstance(document, dict) or document.get("version") != 1
            or document.get("identity") != expected or not isinstance(document.get("answer"), str)):
        raise ValueError("答辩草稿身份或格式不正确，原文件已保留。")
    return document


def save(database, student_id, submission_id, question_index, stage, question, answer):
    if not isinstance(answer, str) or len(answer) > 100000:
        raise ValueError("答辩回答必须为不超过10万字的文本。")
    document = {"version": 1, "identity": identity(student_id, submission_id, question_index, stage, question),
                "answer": answer, "saved_at": now_beijing().isoformat(timespec="seconds")}
    write_json(path(database, student_id, submission_id, question_index, stage, question), document)
    return document


def clear(database, student_id, submission_id, question_index, stage, question):
    path(database, student_id, submission_id, question_index, stage, question).unlink(missing_ok=True)
