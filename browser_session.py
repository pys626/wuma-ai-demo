"""Restore an existing server-validated session across refreshes of the same tab."""
import hashlib
from pathlib import Path
import re
import uuid

import streamlit as st
import streamlit.components.v1 as components

_bridge = components.declare_component("wuma_browser_session", path=str(Path(__file__).with_name("ui_session_component")))
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


def storage_key(database):
    # Origin/tab storage separates browsers; the database path separates local/demo installations.
    digest = hashlib.sha256(str(Path(database).resolve()).casefold().encode()).hexdigest()[:24]
    return "wuma.session.v1:" + digest


def clear_state():
    for key in list(st.session_state):
        del st.session_state[key]
    st.session_state["_browser_clear_pending"] = True
    # Explicit logout must finish clearing, then stay on the login form.
    st.session_state["_browser_read_completed"] = True


def positive_id(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 2**63 else None


def capture_view():
    return {
        "page": st.session_state.get("current_page", "首页"),
        "class_id": positive_id(st.session_state.get("selected_class_id")),
        "assignment_id": positive_id(st.session_state.get("selected_assignment_id")),
        "submission_id": positive_id(st.session_state.get("submission_id")),
        "teacher_submission_id": positive_id(st.session_state.get("teacher_selected_submission_id")),
        "teacher_workspace": st.session_state.get("teacher_workspace_view", "待办处理"),
    }


def sanitize_view(value):
    if not isinstance(value, dict):
        return {}
    page = value.get("page")
    workspace = value.get("teacher_workspace")
    return {
        "page": page if isinstance(page, str) and len(page) <= 32 else "首页",
        **{key: positive_id(value.get(key)) for key in ("class_id", "assignment_id", "submission_id", "teacher_submission_id")},
        "teacher_workspace": workspace if isinstance(workspace, str) and workspace in {"待办处理", "班级分析"} else "待办处理",
    }


def exchange(database, operation, token="", view=None):
    request = st.session_state.setdefault("_browser_request", uuid.uuid4().hex)
    result = _bridge(
        data={"storage_key": storage_key(database), "operation": operation, "request": request,
              "token": token if operation == "write" else "", "view": view or {}},
        key="browser_session_bridge", default=None,
    )
    snapshot = result
    if not isinstance(snapshot, dict) or snapshot.get("request") != request or snapshot.get("operation") != operation:
        return None
    if snapshot.get("status") == "blocked":
        st.session_state["_browser_storage_blocked"] = True
        if operation == "read":
            st.session_state["_browser_read_completed"] = True
        st.caption("浏览器未允许保存本页会话，刷新后需重新登录。")
        return None
    if snapshot.get("status") not in {"ready", "corrupt"}:
        return None
    st.session_state.pop("_browser_storage_blocked", None)
    if operation == "clear":
        st.session_state.pop("_browser_clear_pending", None)
        st.session_state["_browser_read_completed"] = True
        return None
    if operation != "read":
        return None
    st.session_state["_browser_read_completed"] = True
    token = snapshot.get("token", "")
    if not token:
        return None
    return {"token": token if isinstance(token, str) and _TOKEN.fullmatch(token) else "invalid",
            "view": sanitize_view(snapshot.get("view"))}
