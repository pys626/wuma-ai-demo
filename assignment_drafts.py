"""Persist the teacher's unfinished experiment without storing session secrets."""

from app_clock import now_beijing
import json
import hashlib
import math
from numbers import Integral, Real
import os
from pathlib import Path
import tempfile


DRAFT_FIELDS = {
    "title", "description", "requirements", "teaching_focus", "rubric",
    "input_format", "output_format", "constraints_text", "public_samples",
    "test_cases", "verification_enabled", "reference_code", "cpp_standard",
    "hint_limit", "defense_admission", "defense_score_threshold", "starter_code",
    "rubric_reviewed", "explanation_required",
}


class DraftError(ValueError):
    pass


def draft_path(database_path):
    """Each database has its own draft; the database itself is never rewritten."""
    from access_control import current
    actor = current()
    suffix = ".assignment_draft.json"
    if actor:
        if actor["role"] != "teacher":
            raise PermissionError("实验草稿仅限教师使用。")
        return personal_draft_path(database_path, actor["id"], actor.get("class_id"))
    return Path(database_path).resolve().with_suffix(suffix)


def personal_draft_path(database_path, account_id, class_id):
    namespace = hashlib.sha256(f"{account_id}:{class_id}".encode()).hexdigest()[:20]
    return Path(database_path).resolve().with_suffix(f".teacher_{namespace}.assignment_draft.json")


def _plain(value):
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    raise DraftError("草稿包含无法保存的字段值，请检查表格输入。")


def save_draft(database_path, values):
    """Save incomplete or invalid input too, so failed publishing is recoverable."""
    if not isinstance(values, dict):
        raise DraftError("草稿内容格式不正确。")
    document = {
        "version": 1,
        "saved_at": now_beijing().isoformat(timespec="seconds"),
        "data": {key: _plain(value) for key, value in values.items() if key in DRAFT_FIELDS},
    }
    encoded = json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    destination = draft_path(database_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent,
            prefix=destination.name + ".", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return document


def load_draft(database_path):
    path = draft_path(database_path)
    if not path.exists():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise DraftError("保存的草稿无法读取，原文件已保留。") from error
    if (
        not isinstance(document, dict)
        or document.get("version") != 1
        or not isinstance(document.get("data"), dict)
    ):
        raise DraftError("保存的草稿格式不正确，原文件已保留。")
    document["data"] = {
        key: value for key, value in document["data"].items() if key in DRAFT_FIELDS
    }
    return document


def clear_draft(database_path):
    """Remove the draft only after its contents have been successfully published."""
    draft_path(database_path).unlink(missing_ok=True)
