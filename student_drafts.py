"""Save only a student's editable submission, scoped to database/student/experiment."""

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile


FIELDS = {"explanation", "lab_report", "code", "custom_input"}


class DraftError(ValueError):
    pass


def draft_path(database, student_id, assignment_id):
    student_id = str(student_id).strip()
    if not student_id or not isinstance(assignment_id, int) or assignment_id < 1:
        raise DraftError("草稿缺少有效的学生或实验身份。")
    database = Path(database).resolve()
    identity = hashlib.sha256(f"{student_id}\0{assignment_id}".encode()).hexdigest()
    return database.with_suffix(".student_drafts") / (identity + ".json")


def load_draft(database, student_id, assignment_id):
    path = draft_path(database, student_id, assignment_id)
    if not path.exists():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise DraftError("学生草稿无法读取，原文件已保留。") from error
    if (not isinstance(document, dict) or document.get("version") != 1
            or document.get("student_id") != str(student_id).strip()
            or document.get("assignment_id") != assignment_id
            or not isinstance(document.get("data"), dict)
            or any(not isinstance(value, str) for name, value in document["data"].items() if name in FIELDS)):
        raise DraftError("学生草稿身份或格式不正确，原文件已保留。")
    document["data"] = {name: value for name, value in document["data"].items() if name in FIELDS}
    return document


def save_draft(database, student_id, assignment_id, values, assignment_updated_at=""):
    if not isinstance(values, dict):
        raise DraftError("学生草稿内容格式不正确。")
    data = {name: value for name, value in values.items() if name in FIELDS}
    if any(not isinstance(value, str) for value in data.values()):
        raise DraftError("学生草稿必须为文本。")
    if sum(len(value) for value in data.values()) > 512000:
        raise DraftError("输入内容过长，草稿尚未保存。")
    path = draft_path(database, student_id, assignment_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"version": 1, "student_id": str(student_id).strip(), "assignment_id": assignment_id,
                "assignment_updated_at": str(assignment_updated_at),
                "saved_at": datetime.now().isoformat(timespec="seconds"), "data": data}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", prefix=path.name, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(document, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return document


def clear_draft(database, student_id, assignment_id):
    draft_path(database, student_id, assignment_id).unlink(missing_ok=True)
