from datetime import datetime
import hashlib
import hmac
import html
import os
import sqlite3

import altair as alt
import pandas as pd
import streamlit as st

import code_verifier as verifier
import database as db
import demo_mode
import llm_service as llm
import teacher_insights as insights


st.set_page_config(
    page_title="悟码AI",
    page_icon=":material/code:",
    layout="wide",
)


STUDENT_PAGES = [
    "首页",
    "任务中心",
    "实验提交",
    "AI答辩",
    "学生报告",
    "学习档案",
]

TEACHER_PAGES = [
    "首页",
    "教师工作台",
    "实验管理",
]

DEFAULT_EXPLANATION = (
    "请说明算法的主要步骤、关键变量的作用，以及你考虑过的边界情况。"
)
DEFAULT_LAB_REPORT = (
    "选做：可按以下结构补充：实验目的、算法设计、关键代码、测试情况、"
    "边界分析、遇到的问题与解决过程。"
)

CPP_STANDARD_OPTIONS = {
    "自动选择（推荐）": "auto",
    "C++11": "c++11",
    "C++14": "c++14",
    "C++17": "c++17",
}

DEFENSE_ADMISSION_OPTIONS = [
    "全部通过",
    "达到指定分数",
    "允许带错代码",
]

BRAND_ICON_SVG = """
<svg viewBox="0 0 64 64" role="img" aria-label="悟码AI代码图标">
  <defs>
    <linearGradient id="wumaGradient" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#2563EB"/>
      <stop offset="100%" stop-color="#7C3AED"/>
    </linearGradient>
  </defs>
  <rect x="3" y="3" width="58" height="58" rx="16" fill="url(#wumaGradient)"/>
  <path d="M25 19 L13 32 L25 45" fill="none" stroke="white" stroke-width="5"
        stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M39 19 L51 32 L39 45" fill="none" stroke="white" stroke-width="5"
        stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M35 16 L29 48" fill="none" stroke="#DDD6FE" stroke-width="4"
        stroke-linecap="round"/>
  <circle cx="10" cy="13" r="3" fill="#67E8F9"/>
  <circle cx="54" cy="51" r="3" fill="#C4B5FD"/>
</svg>
""".strip()

# 每个能力维度固定一种颜色，各页面和筛选结果使用同一套对应关系。
DIMENSION_COLORS = {
    "逻辑理解": "#2563EB",
    "概念掌握": "#8B5CF6",
    "边界意识": "#0D9488",
    "修改能力": "#D97706",
}


def init_state():
    """初始化本次浏览器会话中的答辩进度。"""
    defaults = {
        "auth_role": None,
        "auth_name": "",
        "auth_student_id": "",
        "current_page": "首页",
        "submission_id": None,
        "submission": None,
        "questions": [],
        "answers": {},
        "current_question": 0,
        "awaiting_follow_up": False,
        "current_follow_up_question": "",
        "defense_completed": False,
        "report": None,
        "report_error": "",
        "flash_message": None,
        "form_name": "张同学",
        "form_student_id": "20260001",
        "teacher_task_submission_id": None,
        "teacher_task_feedback_id": None,
        "teacher_task_assignment_id": None,
        "selected_assignment_id": None,
        "submission_drafts": {},
        "submission_ai_error": "",
        "custom_run_results": {},
        "public_sample_run_results": {},
        "formal_judge_results": {},
        "hint_generation_error": "",
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def navigate(page):
    allowed_pages = pages_for_role(st.session_state.get("auth_role"))
    st.session_state.current_page = page if page in allowed_pages else "首页"
    st.rerun()


def pages_for_role(role):
    """返回当前身份可访问的页面，作为路由层权限白名单。"""
    if role == "学生":
        if demo_mode.demo_read_only():
            return ["首页", "任务中心", "学生报告", "学习档案"]
        return STUDENT_PAGES
    if role == "教师":
        if demo_mode.demo_read_only():
            return ["首页", "教师工作台"]
        return TEACHER_PAGES
    return []


def logged_in_student_id():
    """统一取得当前学生身份，避免各页面自行接受可修改学号。"""
    if st.session_state.get("auth_role") != "学生":
        return ""
    return str(st.session_state.get("auth_student_id", "")).strip()


def teacher_password():
    """教师密码只从环境变量读取，不写入页面状态或数据库。"""
    return os.getenv("TEACHER_PASSWORD", "").strip()


def logout():
    """清空浏览器会话，防止下一位使用者看到上一位学生的数据。"""
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()


def show_login():
    """提供最小角色入口：学生声明身份，教师校验本地配置密码。"""
    render_brand_lockup()
    st.title("登录悟码AI")
    st.caption("选择身份后进入对应工作区；登录后需退出才能切换身份。")
    if demo_mode.demo_mode_enabled():
        st.info(
            "当前为赛事在线Demo，展示内容均为合成教学数据。"
            "可直接选择学生端或教师端体验，无需密码。"
        )
        student_col, teacher_col = st.columns(2)
        with student_col:
            if st.button(
                "体验学生端（张同学）",
                type="primary",
                width="stretch",
            ):
                st.session_state.auth_role = "学生"
                st.session_state.auth_name = demo_mode.DEMO_STUDENT_NAME
                st.session_state.auth_student_id = demo_mode.DEMO_STUDENT_ID
                st.session_state.form_name = demo_mode.DEMO_STUDENT_NAME
                st.session_state.form_student_id = demo_mode.DEMO_STUDENT_ID
                st.session_state.current_page = "首页"
                st.rerun()
        with teacher_col:
            if st.button("体验教师端", width="stretch"):
                st.session_state.auth_role = "教师"
                st.session_state.auth_name = demo_mode.DEMO_TEACHER_NAME
                st.session_state.current_page = "首页"
                st.rerun()
        st.caption("在线Demo为只读体验；完整本地版仍支持提交、判题、答辩和教师复核。")
        st.divider()
    role = st.radio(
        "选择身份",
        ["学生", "教师"],
        horizontal=True,
        key="login_role",
    )
    if role == "学生":
        with st.form("student_login_form"):
            name = st.text_input("姓名", placeholder="例如：张同学").strip()
            student_id = st.text_input("学号", placeholder="例如：20260001").strip()
            submitted = st.form_submit_button(
                "进入学生端",
                type="primary",
                width="stretch",
            )
        if submitted:
            if not name or not student_id:
                st.error("姓名和学号不能为空。")
            elif len(name) > 50 or len(student_id) > 50:
                st.error("姓名和学号不能超过50个字符。")
            else:
                st.session_state.auth_role = "学生"
                st.session_state.auth_name = name
                st.session_state.auth_student_id = student_id
                st.session_state.form_name = name
                st.session_state.form_student_id = student_id
                st.session_state.current_page = "首页"
                st.rerun()
        st.caption("学生身份用于隔离本人任务和报告；当前简化版不设置学生密码。")
        return

    configured_password = teacher_password()
    with st.form("teacher_login_form"):
        password = st.text_input(
            "教师密码",
            type="password",
            key="teacher_login_password",
        )
        submitted = st.form_submit_button(
            "进入教师端",
            type="primary",
            width="stretch",
        )
    if not configured_password:
        st.warning("尚未配置教师密码，请先在.env中填写TEACHER_PASSWORD并重启应用。")
        return
    if submitted:
        if hmac.compare_digest(password, configured_password):
            st.session_state.auth_role = "教师"
            st.session_state.auth_name = "任课教师"
            st.session_state.current_page = "首页"
            st.session_state.pop("teacher_login_password", None)
            st.rerun()
        else:
            st.error("教师密码错误。")


def set_flash(message, level="success"):
    """保存一条刷新后显示一次的操作反馈。"""
    st.session_state.flash_message = {
        "message": message,
        "level": level,
    }


def show_flash():
    """显示并清除操作反馈，避免刷新后消息消失或重复出现。"""
    flash = st.session_state.get("flash_message")
    if not flash:
        return
    st.session_state.flash_message = None
    show_method = getattr(st, flash.get("level", "success"), st.success)
    show_method(flash["message"])


def page_header(title, description):
    st.title(title)
    st.caption(description)
    st.divider()


def render_brand_lockup(compact=False):
    """渲染统一的代码品牌图标，替代通用脑图标。"""
    compact_class = " wuma-brand-compact" if compact else ""
    st.markdown(
        f"""
        <div class="wuma-brand-lockup{compact_class}">
            <div class="wuma-brand-icon">{BRAND_ICON_SVG}</div>
            <div class="wuma-brand-name">悟码<span>AI</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_category_bar_chart(
    data,
    category,
    value,
    height=330,
    maximum=None,
    use_dimension_colors=True,
):
    """按能力维度固定配色，并让横坐标文字水平显示。"""
    value_scale = (
        alt.Scale(domain=[0, maximum])
        if maximum is not None
        else alt.Scale(zero=True)
    )
    color_scale = (
        alt.Scale(
            domain=list(DIMENSION_COLORS.keys()),
            range=list(DIMENSION_COLORS.values()),
        )
        if use_dimension_colors
        else alt.Scale(scheme="tableau20")
    )
    chart = (
        alt.Chart(data)
        .mark_bar(
            cornerRadiusTopLeft=4,
            cornerRadiusTopRight=4,
        )
        .encode(
            x=alt.X(
                f"{category}:N",
                sort=None,
                axis=alt.Axis(
                    title=category,
                    labelAngle=0,
                    labelLimit=160,
                    labelPadding=8,
                    labelOverlap=False,
                ),
            ),
            y=alt.Y(
                f"{value}:Q",
                scale=value_scale,
                axis=alt.Axis(title=value),
            ),
            color=alt.Color(
                f"{category}:N",
                scale=color_scale,
                legend=None,
            ),
            tooltip=[
                alt.Tooltip(f"{category}:N", title=category),
                alt.Tooltip(f"{value}:Q", title=value),
            ],
        )
        .properties(height=height)
    )
    st.altair_chart(chart, width="stretch")


def teacher_summary_cards_html(record, report):
    """长结论自动换行，结论与确认分数分开显示。"""
    code_result, code_score = compact_code_result(record)
    reviewed = bool(record.get("teacher_reviewed"))
    if reviewed:
        decision = record.get("teacher_decision") or "已复核"
    elif int(record.get("attempt_number") or 1) > 1:
        decision = "待复核（重答）"
    elif record.get("_needs_teacher_review") or record.get("review_required"):
        decision = "待复核"
    else:
        decision = "常规抽查" if record.get("overall") is not None else "待复核"
    confirmed = record.get("teacher_confirmed_overall")
    teacher_note = (
        f"确认理解度 {confirmed}/100" if reviewed and confirmed is not None
        else "确认分数未记录" if reviewed
        else "等待教师确认" if decision.startswith("待复核")
        else "教师可按需抽查"
    )
    cards = [
        ("代码判定", code_result, "该次提交保存的结果"),
        ("代码客观得分", code_score, "来自隐藏测试"),
        ("答辩理解度", f"{report['overall']}/100" if report else "待完成", "来自答辩问答"),
        ("教师结论", decision, teacher_note),
    ]
    content = "".join(
        '<div class="wuma-summary-card">'
        f'<div class="wuma-summary-label">{html.escape(str(label))}</div>'
        f'<div class="wuma-summary-value">{html.escape(str(value))}</div>'
        f'<div class="wuma-summary-note">{html.escape(str(note))}</div></div>'
        for label, value, note in cards
    )
    return f'<div class="wuma-summary-grid">{content}</div>'


def teacher_bar_rows_html(rows, scale_note):
    """固定比例横条，标签、精确数值和分母始终可见，不依赖悬停。"""
    content = []
    for row in rows:
        maximum = float(row.get("maximum", 100))
        percent = max(0, min(100, float(row["value"]) * 100 / maximum)) if maximum > 0 else 0
        label = html.escape(str(row["label"]))
        value_text = html.escape(str(row["value_text"]))
        note = html.escape(str(row.get("note", "")))
        color = row.get("color", "#2563EB")
        # 颜色仅使用程序内预设值，标签与备注均按纯文本显示。
        allowed_colors = set(DIMENSION_COLORS.values()) | {"#2563EB", "#0D9488", "#D97706", "#DC2626"}
        if color not in allowed_colors:
            color = "#2563EB"
        content.append(
            '<div class="wuma-bar-row">'
            f'<div class="wuma-bar-heading"><span class="wuma-bar-label">{label}</span>'
            f'<strong class="wuma-bar-value">{value_text}</strong></div>'
            f'<div class="wuma-bar-track" role="img" aria-label="{label}：{value_text}；{note}">'
            f'<div class="wuma-bar-fill" style="width:{percent:.4f}%;background:{color}"></div></div>'
            f'<div class="wuma-bar-note">{note}</div></div>'
        )
    return (
        '<div class="wuma-bar-panel">'
        f'<div class="wuma-bar-scale">{html.escape(scale_note)}</div>'
        + "".join(content) + '</div>'
    )


def build_teacher_dimension_rows(dimension_averages):
    names = {
        "程序逻辑理解": "逻辑理解", "关键概念掌握": "概念掌握",
        "边界情况意识": "边界意识", "分析与修改能力": "修改能力",
    }
    lowest = min(dimension_averages.values()) if dimension_averages else None
    return [
        {"label": names.get(name, name), "value": score, "maximum": 100,
         "value_text": f"{score:g} / 100",
         "note": "当前相对较低的维度" if score == lowest else "",
         "color": DIMENSION_COLORS.get(names.get(name, name), "#2563EB")}
        for name, score in dimension_averages.items()
    ]


def build_teacher_weakness_rows(completed, single_assignment=False):
    counts = {}
    for record in completed:
        label = (record.get("_mastery") or {}).get("status", "证据不足")
        counts[label] = counts.get(label, 0) + 1
    total = len(completed)
    unit = "人" if single_assignment else "人次"
    colors = {"需要巩固": "#D97706", "证据不足": "#64748B", "待核查": "#8B5CF6"}
    return [
        {"label": label, "value": count, "maximum": total,
         "value_text": f"{count}{unit} · {count / total * 100:.1f}%",
         "note": f"{count} / {total}{unit} · 按已有问答证据分类",
         "color": colors.get(label, "#0D9488")}
        for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def build_teacher_hidden_case_rows(statistics, include_assignment=False):
    rows = []
    for _, case in statistics.head(12).iterrows():
        passed, total = int(case["通过次数"]), int(case["判定次数"])
        rate = passed * 100 / total if total else 0
        title = f"{case['实验任务']} · " if include_assignment else ""
        title += f"{case['测试分组']} · {case['测试点']}"
        rows.append({
            "label": title, "value": rate, "maximum": 100,
            "value_text": f"{rate:.1f}%" if rate % 1 else f"{rate:.0f}%",
            "note": f"通过 {passed} / {total} 次 · 未通过 {total - passed} 次",
            "color": "#0D9488" if passed == total else "#DC2626" if passed == 0 else "#D97706",
        })
    return rows


def render_centered_dataframe(data, highlighted_rows=()):
    """显示只读HTML表格，强制表头、文字和数字全部居中。"""
    table_html = data.to_html(
        index=False,
        escape=True,
        border=0,
        classes="wuma-data-table",
        justify="center",
    )
    if highlighted_rows:
        before, separator, body = table_html.partition("<tbody>")
        rows = body.split("<tr>")
        body = rows[0] + "".join(
            ('<tr class="wuma-selected-row" aria-selected="true">'
             if index in highlighted_rows else "<tr>") + row
            for index, row in enumerate(rows[1:])
        )
        table_html = before + separator + body
    st.markdown(
        f'<div class="wuma-table-wrapper">{table_html}</div>',
        unsafe_allow_html=True,
    )


def finalize_report():
    """调用AI生成报告并保存；失败时允许用户重试。"""
    submission = db.get_submission(st.session_state.submission_id)
    qa_records = db.get_qa_records(st.session_state.submission_id)
    report = llm.generate_report(submission, qa_records)
    db.save_report(
        st.session_state.submission_id,
        report,
        datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    st.session_state.report = report
    st.session_state.report_error = ""
    st.session_state.defense_completed = True


def load_submission_session(submission_id):
    """从数据库恢复答辩进度，用于继续重新答辩或刷新后恢复。"""
    submission = db.get_submission(submission_id)
    if (
        submission
        and st.session_state.get("auth_role") == "学生"
        and submission.get("student_id") != logged_in_student_id()
    ):
        raise ValueError("不能打开其他学生的答辩记录。")
    qa_records = db.get_qa_records(submission_id)
    report = db.get_report(submission_id)
    if submission is None or not qa_records:
        raise ValueError("答辩记录不完整，无法恢复。")

    questions = [
        {
            "dimension": item["dimension"],
            "question": item["question"],
            "reference_points": item.get("reference_points", []),
            "reason": item.get("question_reason", ""),
        }
        for item in qa_records
    ]
    answers = {}
    current_question = 0
    awaiting_follow_up = False
    current_follow_up_question = ""

    for index, item in enumerate(qa_records):
        initial_evaluation = item.get("initial_evaluation") or {}
        final_evaluation = item.get("final_evaluation") or {}
        if item.get("answer") or initial_evaluation:
            answers[index] = {
                "initial": item.get("answer", ""),
                "initial_evaluation": initial_evaluation,
                "follow_up_question": item.get("follow_up_question", ""),
                "follow_up": item.get("follow_up_answer", ""),
                "final_evaluation": final_evaluation,
            }
        if final_evaluation:
            current_question = index + 1
            continue
        current_question = index
        if initial_evaluation and item.get("follow_up_question"):
            awaiting_follow_up = True
            current_follow_up_question = item["follow_up_question"]
        break

    st.session_state.submission_id = submission_id
    st.session_state.submission = submission
    st.session_state.questions = questions
    st.session_state.answers = answers
    st.session_state.current_question = current_question
    st.session_state.awaiting_follow_up = awaiting_follow_up
    st.session_state.current_follow_up_question = current_follow_up_question
    st.session_state.defense_completed = report is not None
    st.session_state.report = report
    st.session_state.report_error = ""


def start_or_resume_redefense(submission, request):
    """按照教师复核意见开始新答辩，或恢复已经开始的重答。"""
    if request["status"] == "答辩中" and request.get("new_submission_id"):
        load_submission_session(request["new_submission_id"])
        set_flash("已恢复上次未完成的重新答辩。")
        navigate("AI答辩")

    assignment = submission.get("assignment_snapshot", {})
    teaching_focus = list(assignment.get("teaching_focus", []))
    teaching_focus.append(f"教师要求学生重新答辩：{request['reason']}")
    preliminary_review = db.get_preliminary_review(submission["id"])
    with st.spinner("AI正在根据教师复核意见生成重新答辩问题，请稍候……"):
        questions = llm.generate_questions(
            submission["problem"],
            assignment.get("requirements", []),
            submission["code"],
            submission["explanation"],
            teaching_focus,
            rubric=submission.get("rubric_snapshot") or assignment.get("rubric", []),
            preliminary_review=preliminary_review,
            lab_report=submission.get("lab_report", ""),
            code_verification=submission.get("code_verification", {}),
            constraints_text=assignment.get("constraints_text", ""),
        )
        new_submission_id = db.start_redefense(
            request["id"],
            questions,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
    load_submission_session(new_submission_id)
    set_flash(
        f"重新答辩已创建为提交#{new_submission_id}，原提交和原报告均已保留。"
    )
    navigate("AI答辩")


def move_to_next_question():
    """结束当前题；三题完成后自动请求最终报告。"""
    st.session_state.current_question += 1
    st.session_state.awaiting_follow_up = False
    st.session_state.current_follow_up_question = ""

    if st.session_state.current_question >= len(st.session_state.questions):
        try:
            finalize_report()
        except (llm.LLMServiceError, sqlite3.Error) as error:
            st.session_state.report_error = str(error)


def show_home():
    st.markdown("<div class='hero'>", unsafe_allow_html=True)
    render_brand_lockup()
    st.subheader("面向编程实验的自适应答辩与理解度评测平台")
    st.markdown("### 不止评代码，更要评理解。")
    st.write(
        "学生提交C++代码后，系统先形成编译与测试证据，AI再围绕本人代码和"
        "真实验证结果生成个性化问题，并通过答辩与教师复核形成学习证据。"
    )
    st.markdown("</div>", unsafe_allow_html=True)

    st.write("")
    published_count = len(db.list_assignments(published_only=True))
    col1, col2, col3 = st.columns(3)
    col1.metric("代码证据", "编译＋样例", "可选受限运行")
    col2.metric("个性化问题", "3道", "结合验证结果生成")
    col3.metric("已发布实验", published_count, "评分点与样例可配置")

    st.subheader("使用流程")
    steps = [
        "① 提交代码与思路",
        "② 编译与样例验证",
        "③ AI答辩与按需追问",
        "④ 报告、反馈与教师复核",
    ]
    columns = st.columns(4)
    for column, step in zip(columns, steps):
        column.info(step)

    role = st.session_state.auth_role
    if role == "学生":
        if st.button("开始实验", type="primary", width="stretch"):
            navigate("实验提交")
        student_id = logged_in_student_id()
        pending_tasks = db.list_student_tasks(student_id)
        task_label = f"我的待办 · {student_id}"
    else:
        action_left, action_right = st.columns(2)
        with action_left:
            if st.button("管理实验", width="stretch"):
                navigate("实验管理")
        with action_right:
            if st.button("进入教师工作台", type="primary", width="stretch"):
                navigate("教师工作台")
        pending_tasks = db.list_teacher_tasks()
        task_label = "教师待办"

    st.subheader("待办提醒")
    task_count, task_action = st.columns([4, 1])
    task_count.metric(task_label, len(pending_tasks))
    with task_action:
        st.write("")
        task_button_label = "打开任务中心" if role == "学生" else "进入工作台"
        if st.button(task_button_label, type="primary", width="stretch"):
            navigate("任务中心" if role == "学生" else "教师工作台")
    if pending_tasks:
        urgent_count = sum(
            item["priority"] == "紧急"
            for item in pending_tasks
        )
        if urgent_count:
            st.warning(f"当前共有{urgent_count}项紧急任务，请优先处理。")
        else:
            target_name = "任务中心" if role == "学生" else "教师工作台"
            st.info(f"当前存在尚未完成的流程任务，可进入{target_name}直接处理。")
    else:
        st.success("当前没有待处理任务。")


def format_waiting_duration(created_at):
    """把任务创建时间转换为易读的等待时长。"""
    try:
        started = datetime.strptime(created_at, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return "等待时长未知"
    minutes = max(0, int((datetime.now() - started).total_seconds() // 60))
    if minutes < 60:
        return f"已等待{minutes}分钟"
    hours = minutes // 60
    if hours < 24:
        return f"已等待{hours}小时"
    return f"已等待{hours // 24}天"


def open_student_task(task):
    """从学生待办恢复答辩、开始重答或查看教师回复。"""
    submission = db.get_submission(task["submission_id"])
    if submission is None:
        raise ValueError("待办关联的提交记录不存在。")
    if submission["student_id"] != logged_in_student_id():
        raise ValueError("不能打开其他学生的任务。")
    st.session_state.form_student_id = submission["student_id"]

    if task["action"] == "continue_defense":
        load_submission_session(submission["id"])
        set_flash("已恢复尚未完成的答辩。")
        navigate("AI答辩")
    if task["action"] == "start_redefense":
        request = db.get_latest_redefense_request(submission["id"])
        if request is None:
            raise ValueError("没有找到教师的重新答辩要求。")
        start_or_resume_redefense(submission, request)

    load_submission_session(submission["id"])
    if task.get("feedback_id"):
        db.mark_student_feedback_viewed(
            task["feedback_id"],
            submission["student_id"],
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        set_flash("已打开教师回复，对应提醒已自动完成。")
    navigate("学生报告")


def open_teacher_task(task):
    """记录教师待办定位目标，并在工作台打开对应学生。"""
    st.session_state.teacher_task_submission_id = task["submission_id"]
    st.session_state.teacher_task_feedback_id = task.get("feedback_id")
    st.session_state.teacher_task_assignment_id = task.get("assignment_id")
    set_flash(f"已定位待办：{task['title']}。")
    navigate("教师工作台")


def filter_tasks(tasks, selected_filter):
    if selected_filter == "待处理":
        return [item for item in tasks if item["status"] == "待处理"]
    if selected_filter in {"紧急", "普通"}:
        return [
            item
            for item in tasks
            if item["status"] == "待处理"
            and item["priority"] == selected_filter
        ]
    return [item for item in tasks if item["status"] == "已完成"]


def render_task_list(tasks, role):
    """显示任务卡片，并提供直接进入业务页面的操作按钮。"""
    if not tasks:
        st.info("当前筛选条件下没有任务。")
        return
    for task in tasks[:30]:
        with st.container(border=True):
            content, action = st.columns([5, 1])
            icon = "🔴" if task["priority"] == "紧急" else (
                "✅" if task["status"] == "已完成" else "🟡"
            )
            with content:
                st.markdown(f"**{icon} {task['title']}**")
                st.write(task["description"])
                timing = (
                    format_waiting_duration(task.get("created_at"))
                    if task["status"] == "待处理"
                    else f"完成记录 · {task.get('created_at', '时间未知')}"
                )
                st.caption(
                    f"{task['task_type']} · {task['priority']} · {timing}"
                )
            with action:
                st.write("")
                clicked = st.button(
                    task["action_label"],
                    key=f"task_action_{role}_{task['task_key']}",
                    type="primary" if task["status"] == "待处理" else "secondary",
                    width="stretch",
                )
            if clicked:
                try:
                    if role == "学生待办":
                        open_student_task(task)
                    else:
                        open_teacher_task(task)
                except (ValueError, sqlite3.Error, llm.LLMServiceError) as error:
                    st.error(f"打开任务失败：{error}")


def show_task_center():
    page_header(
        "智能待办中心",
        "集中展示当前身份需要处理的任务，完成业务操作后提醒自动消失。",
    )
    show_flash()
    if st.session_state.auth_role == "学生":
        role = "学生待办"
        student_id = logged_in_student_id()
        st.caption(f"当前学生：{st.session_state.auth_name} · {student_id}")
        tasks = db.list_student_tasks(student_id, include_completed=True)
        if not db.list_submissions(student_id=student_id):
            st.warning("没有找到该学号的提交记录。")
    else:
        role = "教师待办"
        assignments = db.list_assignments()
        assignment_map = {"全部实验": None}
        assignment_map.update(
            {
                f"实验#{item['id']} · {item['title']}": item["id"]
                for item in assignments
            }
        )
        assignment_label = st.selectbox(
            "筛选实验任务",
            list(assignment_map),
            key="task_assignment_filter",
        )
        tasks = db.list_teacher_tasks(
            assignment_id=assignment_map[assignment_label],
            include_completed=True,
        )

    pending_count = sum(item["status"] == "待处理" for item in tasks)
    urgent_count = sum(
        item["status"] == "待处理" and item["priority"] == "紧急"
        for item in tasks
    )
    completed_count = sum(item["status"] == "已完成" for item in tasks)
    col1, col2, col3 = st.columns(3)
    col1.metric("待处理", pending_count)
    col2.metric("紧急任务", urgent_count)
    col3.metric("已完成记录", completed_count)

    selected_filter = st.selectbox(
        "筛选任务",
        ["待处理", "紧急", "普通", "已完成"],
        key=f"task_status_filter_{role}",
    )
    visible_tasks = filter_tasks(tasks, selected_filter)
    st.caption("任务来自当前业务状态；完成答辩、复核或反馈处理后会自动更新。")
    render_task_list(visible_tasks, role)


def visible_whitespace_html(value, empty_label):
    """把不可见字符转换成带颜色、字号差异的OJ格式标记。"""
    text = db.normalize_sample_text(value).replace("\r\n", "\n").replace("\r", "\n")
    if not text:
        return f'<span class="wuma-empty-sample">{html.escape(empty_label)}</span>'
    parts = []
    index = 0
    while index < len(text):
        character = text[index]
        if character == " ":
            run_end = index + 1
            while run_end < len(text) and text[run_end] == " ":
                run_end += 1
            run_length = run_end - index
            count_text = f"×{run_length}" if run_length > 1 else ""
            parts.append(
                '<span class="wuma-space-run" '
                f'title="连续{run_length}个空格" aria-label="{run_length}个空格">'
                f'<span class="wuma-space-word">空格</span>{count_text}</span>'
            )
            index = run_end
            continue
        elif character == "\n":
            parts.append(
                '<span class="wuma-newline-marker" title="换行">↵</span><br>'
            )
        elif character == "\t":
            parts.append('<span class="wuma-tab-marker" title="制表符">⇥</span>')
        else:
            parts.append(html.escape(character))
        index += 1
    return "".join(parts)


def render_sample_value(value, empty_label, show_format):
    """普通模式保留原文本，格式模式突出显示空格、换行和制表符。"""
    normalized = db.normalize_sample_text(value)
    if not show_format:
        st.code(normalized or empty_label, language="text")
        return
    st.markdown(
        f'<pre class="wuma-format-code">{visible_whitespace_html(normalized, empty_label)}</pre>',
        unsafe_allow_html=True,
    )


def render_oj_statement(assignment, key_prefix="oj_statement"):
    """按OJ题面结构展示学生可见的输入、输出和公开样例。"""
    input_format = assignment.get("input_format", "").strip()
    output_format = assignment.get("output_format", "").strip()
    constraints_text = assignment.get("constraints_text", "").strip()
    public_samples = assignment.get("public_samples", [])

    if input_format:
        st.markdown("**输入格式**")
        st.write(input_format)
    if output_format:
        st.markdown("**输出格式**")
        st.write(output_format)
    if constraints_text:
        st.markdown("**数据范围与说明**")
        st.write(constraints_text)
    if public_samples:
        st.markdown("**公开样例**")
        show_format = st.toggle(
            "查看格式",
            value=False,
            key=f"{key_prefix}_show_format",
            help="显示空格、换行和制表符，便于核对OJ输入输出格式。",
        )
        if show_format:
            st.markdown(
                '<div class="wuma-format-legend">'
                '格式提示：<span class="wuma-space-run">空格×N</span> 连续空格数量　'
                '<span class="wuma-newline-marker">↵</span> 换行　'
                '<span class="wuma-tab-marker">⇥</span> 制表符'
                '</div>',
                unsafe_allow_html=True,
            )
        for index, sample in enumerate(public_samples, start=1):
            st.markdown(f"**{sample.get('name') or f'样例{index}'}**")
            input_col, output_col = st.columns(2)
            with input_col:
                st.caption("样例输入")
                render_sample_value(sample.get("input", ""), "（无输入）", show_format)
            with output_col:
                st.caption("样例输出")
                render_sample_value(sample.get("output", ""), "（无输出）", show_format)
            if sample.get("explanation"):
                st.caption(f"样例说明：{sample['explanation']}")


def render_custom_run_result(result, output_required=False):
    """展示学生自定义运行结果，不把它当作正式判题结论。"""
    if not result:
        return
    st.markdown("**最近一次自定义运行结果**")
    status = result.get("overall_status", "未执行")
    compile_result = result.get("compile", {})
    if status in {"未启用", "环境不可用", "编译环境不兼容", "未执行"}:
        st.warning(result.get("message", status))
        if compile_result.get("message"):
            st.code(compile_result["message"], language="text")
        return
    if compile_result.get("status") != "成功":
        st.error(f"{status}：{result.get('message', '')}")
        if compile_result.get("message"):
            st.code(compile_result["message"], language="text")
        return

    effective_standard = result.get("effective_cpp_standard")
    if effective_standard:
        st.caption(
            "本次实际编译标准："
            + verifier.cpp_standard_label(effective_standard)
            + f"（{compile_result.get('standard_flag', '')}）"
        )

    cases = result.get("cases", [])
    if not cases:
        st.warning(result.get("message", status))
        return
    run_result = cases[0]
    if run_result.get("status") == "运行成功":
        actual_output = run_result.get("actual_output", "")
        if output_required and not verifier.normalize_output(actual_output):
            st.error("答案错误：题目要求输出结果，但程序没有输出。")
        else:
            st.info("程序运行完成。以下内容只是实际输出，不代表通过隐藏测试。")
    else:
        st.error(f"程序{run_result.get('status', '运行失败')}：{run_result.get('message', '')}")
    st.caption("实际输出")
    st.code(run_result.get("actual_output", "") or "（程序没有输出）", language="text")
    if run_result.get("stderr"):
        st.caption("错误输出")
        st.code(run_result["stderr"], language="text")
    st.caption(f"运行耗时：{run_result.get('duration_ms', 0)}毫秒")


def render_public_sample_case(case, sample, index, total):
    """显示单个公开样例的判题状态、首个差异和输出对照。"""
    case_name = case.get("name") or sample.get("name") or f"样例{index}"
    if total > 1:
        st.markdown(f"**{case_name}**")
    case_status = case.get("status", "未通过")
    if case_status == "通过":
        st.success("答案正确：实际输出与该公开样例的预期输出一致。")
    elif case_status == "未通过":
        st.error("答案错误：实际输出与该公开样例的预期输出不一致。")
        difference = verifier.analyze_output_difference(
            case.get("expected_output", sample.get("output", "")),
            case.get("actual_output", ""),
        )
        if difference.get("different"):
            st.warning(
                f"第一个差异：第{difference['line']}行、第{difference['column']}列附近，"
                f"{difference['kind']}。{difference['detail']}"
            )
    elif case_status == "超时":
        st.error("运行超时：程序没有在时间限制内结束。")
    elif case_status == "运行错误":
        st.error("运行错误：" + case.get("message", "程序异常结束。"))
    elif case_status == "输出过长":
        st.error("输出超限：程序产生的内容超过限制。")
    else:
        st.error(f"判题未通过：{case.get('message', case_status)}")

    if case_status != "通过":
        st.caption("输出对照（蓝色标签显示连续空格数量，换行和制表符保留格式标记）")
        expected_col, actual_col = st.columns(2)
        with expected_col:
            st.caption("预期输出")
            render_sample_value(
                case.get("expected_output", sample.get("output", "")),
                "（无输出）",
                True,
            )
        with actual_col:
            st.caption("实际输出")
            render_sample_value(
                case.get("actual_output", ""),
                "（程序没有输出）",
                True,
            )
    st.caption(f"运行耗时：{case.get('duration_ms', 0)}毫秒")


def render_public_sample_run_result(record):
    """显示单个或全部公开样例的一键判题结果。"""
    if not record:
        return
    samples = record.get("samples") or [record.get("sample", {})]
    result = record.get("result", {})
    st.markdown("**公开样例判题结果**")
    compile_result = result.get("compile", {})
    status = result.get("overall_status", "未执行")
    if compile_result.get("status") != "成功":
        if status in {"未启用", "环境不可用", "编译环境不兼容", "未执行"}:
            st.warning(result.get("message", status))
        else:
            st.error("编译错误：代码没有通过编译检查。")
        if compile_result.get("message"):
            st.code(compile_result["message"], language="text")
        return

    cases = result.get("cases", [])
    if not cases:
        st.warning(result.get("message", "公开样例没有执行。"))
        return
    passed = sum(case.get("status") == "通过" for case in cases)
    if len(cases) > 1:
        if passed == len(cases):
            st.success(f"公开样例全部通过：{passed}/{len(cases)}。")
        else:
            st.error(f"公开样例通过汇总：{passed}/{len(cases)}，请检查未通过样例。")
    for index, case in enumerate(cases, start=1):
        sample = samples[index - 1] if index <= len(samples) else {}
        render_public_sample_case(case, sample, index, len(cases))
    st.caption("以上结果仅代表当前公开样例，不替代教师隐藏测试或正式诊断。")


def student_submission_is_valid(name, student_id, explanation, lab_report, code):
    """在正式评测和进入答辩前使用同一组输入校验。"""
    if not name.strip() or not student_id.strip():
        st.error("姓名和学号不能为空。")
        return False
    if not explanation.strip():
        st.error("解题思路不能为空。")
        return False
    if len(explanation) > 1500:
        st.error("解题思路过长，请控制在1500字以内。")
        return False
    if len(lab_report) > 6000:
        st.error("实验报告过长，请控制在6000个字符以内。")
        return False
    if not code.strip():
        st.error("代码不能为空。")
        return False
    if "int main" not in code:
        st.error("代码中没有找到 int main，请检查是否粘贴了完整的C++程序。")
        return False
    if len(code) > 12000:
        st.error("代码过长，请将代码控制在12000个字符以内。")
        return False
    return True


def formal_verification_is_conclusive(result):
    """环境故障和未执行不是学生代码结论。"""
    if not result:
        return False
    return result.get("overall_status") not in {
        "未启用",
        "环境不可用",
        "编译环境不兼容",
        "未执行",
    }


def defense_admission_result(assignment, verification):
    """根据教师规则判断当前代码能否进入正式答辩。"""
    if not assignment.get("verification_enabled"):
        return True, "本实验未启用代码判题，可直接进入AI答辩。"
    if not formal_verification_is_conclusive(verification):
        return False, "代码尚未完成有效判题，暂时不能进入正式答辩。"

    mode = assignment.get("defense_admission", "全部通过")
    if mode == "允许带错代码":
        return True, "教师允许带错代码进入诊断答辩，客观代码分仍按判题结果计算。"
    if verification.get("overall_status") == "全部通过":
        return True, "代码已通过全部隐藏测试，可以进入AI答辩。"
    if mode == "达到指定分数":
        maximum = int(verification.get("max_score", 0) or 0)
        score = int(verification.get("score", 0) or 0)
        percentage = round(score * 100 / maximum) if maximum else 0
        threshold = int(assignment.get("defense_score_threshold", 100) or 100)
        if percentage >= threshold:
            return True, f"代码得分为{percentage}分，已达到教师设置的{threshold}分准入线。"
        return False, f"代码得分为{percentage}分，尚未达到{threshold}分答辩准入线。"
    return False, "教师要求全部测试通过后才能进入AI答辩。"


def render_formal_verification_result(result):
    """持久显示正式判题结论，同时隐藏测试输入和标准输出。"""
    st.markdown("### 正式评测结果")
    if not result:
        st.info("尚未进行正式评测。")
        return
    status = result.get("overall_status", "未执行")
    compile_result = result.get("compile") or {}
    if not formal_verification_is_conclusive(result):
        st.warning(f"判题未完成：{status}。{result.get('message', '')}")
        if compile_result.get("message"):
            st.code(compile_result["message"], language="text")
        st.caption("这是执行环境问题，不会记为学生代码错误，也不能据此进入正式答辩。")
        return
    if compile_result.get("status") != "成功":
        st.error("编译错误（CE）：代码没有通过编译检查。")
        if compile_result.get("message"):
            st.code(compile_result["message"], language="text")
        return

    cases = result.get("cases") or []
    passed = int(result.get("passed", 0) or 0)
    total = int(result.get("total", len(cases)) or len(cases))
    score = int(result.get("score", 0) or 0)
    maximum = int(result.get("max_score", 0) or 0)
    statuses = [str(item.get("status", "未通过")) for item in cases]
    if status == "全部通过" or (total and passed == total):
        st.success(f"答案正确（AC）：全部{total}组测试通过。")
    elif passed:
        st.warning(f"部分正确：通过{passed}/{total}组测试，请继续修改代码。")
    elif statuses and all(item == "超时" for item in statuses):
        st.error("运行超时（TLE）：程序未在时间限制内完成。")
    elif statuses and all(item == "运行错误" for item in statuses):
        st.error("运行错误（RE）：程序在测试过程中异常结束。")
    elif statuses and all(item == "输出过长" for item in statuses):
        st.error("输出超限（OLE）：程序产生的输出超过限制。")
    else:
        st.error(f"答案错误（WA）：通过{passed}/{total}组测试。")

    metric1, metric2, metric3 = st.columns(3)
    metric1.metric("通过测试", f"{passed}/{total}")
    metric2.metric("客观得分", f"{score}/{maximum}" if maximum else "未计分")
    metric3.metric("编译状态", "成功")
    if cases:
        rows = []
        for index, item in enumerate(cases, start=1):
            rows.append(
                {
                    "测试点": f"测试点{index}",
                    "结果": item.get("verdict_code") or item.get("status", "未通过"),
                    "得分": f"{item.get('score', 0)}/{item.get('weight', 0)}",
                    "耗时": f"{item.get('duration_ms', 0)}毫秒",
                }
            )
        render_centered_dataframe(pd.DataFrame(rows))
        st.caption("为保护题目，学生端不展示隐藏测试的名称、输入、标准输出和实际输出。")


def render_student_hint_history(records, hint_limit, current_code=""):
    """显示已使用的渐进提示及剩余次数。"""
    used = len(records)
    current_digest = hashlib.sha256(str(current_code).encode("utf-8")).hexdigest()
    st.markdown("### AI学习提示")
    st.caption(f"已使用 {used}/{hint_limit} 次；次数按当前学号和实验累计，刷新不会重置。")
    for record in reversed(records):
        hint = record.get("hint") or {}
        record_digest = str(record.get("code_digest") or "")
        version_label = (
            "当前代码"
            if record_digest and record_digest == current_digest
            else "历史代码·已失效"
        )
        with st.expander(
            f"第{record.get('hint_number')}次提示 · {version_label} · "
            f"{record.get('created_at', '')}",
            expanded=record is records[0],
        ):
            if version_label != "当前代码":
                st.caption("这条提示对应较早的代码，只作历史参考；请以当前评测结果为准。")
            st.markdown(f"**排查方向：** {hint.get('diagnosis', '—')}")
            st.write(hint.get("guidance", "—"))
            checks = hint.get("self_check") or []
            if checks:
                st.markdown("**请先自查：**")
                for item in checks:
                    st.write(f"- {item}")


def show_submission():
    page_header(
        "实验提交",
        "选择教师已发布的实验，先完成正式判题，达到准入规则后再进入AI答辩。",
    )
    show_flash()

    if not llm.is_configured():
        st.error(
            "尚未配置AI模型，请根据.env.example在项目根目录创建.env文件，"
            "填写API密钥和模型名称后重启应用。"
        )

    assignments = db.list_assignments(published_only=True)
    if not assignments:
        st.warning("当前没有已发布的实验任务，请先到“实验管理”创建或发布任务。")
        return

    assignment_map = {
        f"实验#{item['id']} · {item['title']}": item
        for item in assignments
    }
    labels = list(assignment_map)
    default_index = 0
    for index, item in enumerate(assignments):
        if item["id"] == st.session_state.selected_assignment_id:
            default_index = index
            break

    selected_label = st.selectbox(
        "选择实验任务",
        labels,
        index=default_index,
    )
    assignment = assignment_map[selected_label]
    st.session_state.selected_assignment_id = assignment["id"]
    configuration_blockers = assignment_publish_blockers(assignment)
    if configuration_blockers:
        st.error(
            "该实验仍可练习和运行代码，但配置尚未通过可靠性检查，暂不能进入AI答辩："
            + "；".join(configuration_blockers)
        )

    with st.container(border=True):
        st.subheader(assignment["title"])
        st.caption(f"编程语言：{assignment['language']}")
        st.write(assignment["description"])
        render_oj_statement(assignment, f"submission_{assignment['id']}")
        st.caption(
            "编译标准："
            + verifier.cpp_standard_label(assignment.get("cpp_standard", "auto"))
        )
        st.markdown("**实验要求**")
        for requirement in assignment["requirements"]:
            st.write(f"- {requirement}")
        st.markdown("**本实验评分点**")
        for criterion in assignment.get("rubric", []):
            st.write(
                f"- {criterion['name']}（{criterion['weight']}分，"
                f"核查来源：{criterion['source']}）"
            )
        if assignment.get("verification_enabled"):
            st.success(
                f"本实验已配置{len(assignment.get('test_cases', []))}组隐藏测试样例；"
                "正式评测后将先显示判题结果，达到教师准入规则后才能进入AI答辩。"
            )
            admission = assignment.get("defense_admission", "全部通过")
            admission_text = (
                f"达到{assignment.get('defense_score_threshold', 100)}分"
                if admission == "达到指定分数"
                else admission
            )
            st.caption(
                f"答辩准入：{admission_text}；"
                f"每名学生可使用AI提示{assignment.get('hint_limit', 2)}次。"
            )
            runtime = verifier.runtime_status()
            if runtime["available"]:
                st.caption(f"{runtime['label']}：{runtime['detail']}")
            else:
                st.warning(
                    f"{runtime['label']}：{runtime['detail']}"
                    " 环境恢复前不能完成正式判题，也不会记为学生代码错误。"
                )

    draft_key = str(assignment["id"])
    draft = st.session_state.submission_drafts.get(draft_key, {})
    if st.session_state.submission_ai_error:
        st.warning(
            "上一次AI处理没有完成，但姓名、学号、代码、解题思路和选做实验报告均已保留，"
            "可检查配置后直接重试。"
        )

    public_sample_selection = None
    runtime = verifier.runtime_status()
    with st.container(border=True):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input(
                "姓名",
                value=st.session_state.auth_name,
                key=f"submission_name_{assignment['id']}",
                disabled=True,
            )
        with col2:
            student_id = st.text_input(
                "学号",
                value=logged_in_student_id(),
                key=f"submission_student_id_{assignment['id']}",
                disabled=True,
            )

        explanation = st.text_area(
            "解题思路",
            value=draft.get("explanation", ""),
            placeholder=DEFAULT_EXPLANATION,
            height=110,
            key=f"explanation_{assignment['id']}",
        )
        lab_report = st.text_area(
            "实验报告（选做）",
            value=draft.get("lab_report", ""),
            placeholder=DEFAULT_LAB_REPORT,
            height=220,
            key=f"lab_report_{assignment['id']}",
        )
        code = st.text_area(
            f"{assignment['language']}代码",
            value=draft.get("code", assignment["starter_code"]),
            height=360,
            key=f"code_{assignment['id']}",
        )
        public_samples = [
            sample
            for sample in assignment.get("public_samples", [])
            if isinstance(sample, dict) and str(sample.get("output", "")).strip()
        ]
        if public_samples:
            st.markdown("**公开样例一键判题**")
            st.caption(
                "无需复制样例输入；可以运行单个或全部公开样例，不替代教师隐藏测试。"
            )
            if len(public_samples) > 1 and st.button(
                "运行全部公开样例",
                width="stretch",
                disabled=(
                    assignment["language"] != "C++" or not runtime["available"]
                ),
                key=f"public_sample_run_all_{assignment['id']}",
            ):
                public_sample_selection = list(enumerate(public_samples, start=1))
            sample_columns = st.columns(len(public_samples))
            for index, (sample_column, sample) in enumerate(
                zip(sample_columns, public_samples),
                start=1,
            ):
                sample_name = str(sample.get("name") or f"样例{index}")
                if sample_column.button(
                    f"运行{sample_name}",
                    width="stretch",
                    disabled=(
                        assignment["language"] != "C++" or not runtime["available"]
                    ),
                    key=f"public_sample_run_{assignment['id']}_{index}",
                ):
                    public_sample_selection = [(index, sample)]
        st.markdown("**提交前自定义运行（可选）**")
        custom_input = st.text_area(
            "自定义测试输入",
            value=draft.get("custom_input", ""),
            placeholder="按照题目的输入格式填写；没有输入时可以留空。",
            height=110,
            max_chars=verifier.MAX_CUSTOM_INPUT_CHARS,
            key=f"custom_input_{assignment['id']}",
        )
        custom_run_clicked = st.button(
            "运行代码并查看输出",
            width="stretch",
            disabled=assignment["language"] != "C++" or not runtime["available"],
            key=f"custom_run_{assignment['id']}",
        )
        if not runtime["available"]:
            st.caption(
                f"{runtime['label']}：{runtime['detail']} "
                "暂时无法进行自定义运行。"
            )
        else:
            st.caption("自定义输入和运行结果不会替代教师隐藏测试，也不计入正式诊断。")
        formal_judge_clicked = False
        if assignment.get("verification_enabled"):
            formal_judge_clicked = st.button(
                "正式评测代码",
                type="primary",
                width="stretch",
                disabled=assignment["language"] != "C++",
                key=f"formal_judge_{assignment['id']}",
            )
            st.caption("正式评测使用教师隐藏测试；评测后不会自动进入AI答辩。")

    st.session_state.submission_drafts[draft_key] = {
        "name": name,
        "student_id": student_id,
        "explanation": explanation,
        "lab_report": lab_report,
        "code": code,
        "custom_input": custom_input,
    }

    result_key = str(assignment["id"])
    if public_sample_selection is not None:
        if not code.strip():
            st.error("请先填写需要运行的C++代码。")
        elif len(code) > 12000:
            st.error("代码过长，请将代码控制在12000个字符以内。")
        else:
            run_all = len(public_sample_selection) > 1
            spinner_text = "正在运行全部公开样例……" if run_all else (
                "正在运行"
                + str(
                    public_sample_selection[0][1].get("name")
                    or f"样例{public_sample_selection[0][0]}"
                )
                + "……"
            )
            with st.spinner(spinner_text):
                selected_samples = [
                    dict(sample) for _, sample in public_sample_selection
                ]
                sample_cases = [
                    {
                        "name": str(sample.get("name") or f"样例{sample_index}"),
                        "input": str(sample.get("input", "")),
                        "expected_output": str(sample.get("output", "")),
                    }
                    for sample_index, sample in public_sample_selection
                ]
                st.session_state.public_sample_run_results[result_key] = {
                    "code": code,
                    "samples": selected_samples,
                    "result": verifier.verify_cpp_code(
                        code,
                        sample_cases,
                        assignment.get("cpp_standard", "auto"),
                    ),
                }
    saved_sample_run = st.session_state.public_sample_run_results.get(result_key, {})
    if saved_sample_run.get("code") == code:
        render_public_sample_run_result(saved_sample_run)
    elif saved_sample_run:
        st.info("代码已修改，上次公开样例判题结果已失效，请重新运行。")

    if custom_run_clicked:
        if not code.strip():
            st.error("请先填写需要运行的C++代码。")
        elif len(code) > 12000:
            st.error("代码过长，请将代码控制在12000个字符以内。")
        else:
            with st.spinner("正在编译并运行自定义测试……"):
                st.session_state.custom_run_results[result_key] = {
                    "code": code,
                    "input": custom_input,
                    "result": verifier.run_cpp_code(
                        code,
                        custom_input,
                        assignment.get("cpp_standard", "auto"),
                    ),
                }
    saved_custom_run = st.session_state.custom_run_results.get(result_key, {})
    if (
        saved_custom_run.get("code") == code
        and saved_custom_run.get("input") == custom_input
    ):
        output_required = bool(assignment.get("output_format", "").strip()) or any(
            str(sample.get("output", "")).strip()
            for sample in assignment.get("public_samples", [])
            if isinstance(sample, dict)
        )
        render_custom_run_result(
            saved_custom_run.get("result"),
            output_required=output_required,
        )

    if formal_judge_clicked and student_submission_is_valid(
        name,
        student_id,
        explanation,
        lab_report,
        code,
    ):
        with st.spinner("正在编译代码并运行教师隐藏测试……"):
            st.session_state.formal_judge_results[result_key] = {
                "code": code,
                "student_id": student_id.strip(),
                "assignment_updated_at": assignment.get("updated_at", ""),
                "result": verifier.verify_cpp_code(
                    code,
                    assignment.get("test_cases", []),
                    assignment.get("cpp_standard", "auto"),
                ),
            }

    saved_formal = st.session_state.formal_judge_results.get(result_key, {})
    formal_matches = (
        saved_formal.get("code") == code
        and saved_formal.get("student_id") == student_id.strip()
        and saved_formal.get("assignment_updated_at")
        == assignment.get("updated_at", "")
    )
    code_verification = saved_formal.get("result", {}) if formal_matches else {}
    if assignment.get("verification_enabled"):
        if formal_matches:
            render_formal_verification_result(code_verification)
        elif saved_formal:
            st.info("代码、学号或实验设置已经改变，上次正式评测结果已失效，请重新评测。")
        else:
            st.info("请先点击“正式评测代码”，系统不会在判题前自动进入AI答辩。")

    hint_limit = int(assignment.get("hint_limit", 2) or 0)
    hint_records = (
        db.list_ai_hints(assignment["id"], student_id.strip())
        if student_id.strip()
        else []
    )
    if assignment.get("verification_enabled"):
        if hint_limit:
            render_student_hint_history(hint_records, hint_limit, code)
        else:
            st.caption("教师未为本实验开放AI提示。")

    current_code_digest = hashlib.sha256(str(code).encode("utf-8")).hexdigest()
    current_code_hints = [
        record
        for record in hint_records
        if record.get("code_digest") == current_code_digest
    ]

    admitted, admission_message = defense_admission_result(
        assignment,
        code_verification,
    )
    if configuration_blockers:
        admitted = False
        admission_message = "请联系教师先修正实验配置，再进入AI答辩。"
    can_request_hint = (
        assignment.get("verification_enabled")
        and formal_matches
        and formal_verification_is_conclusive(code_verification)
        and code_verification.get("overall_status") != "全部通过"
        and len(hint_records) < hint_limit
    )
    if can_request_hint:
        hint_clicked = st.button(
            f"获取AI提示（剩余{hint_limit - len(hint_records)}次）",
            width="stretch",
            disabled=not llm.is_configured(),
            key=f"request_hint_{assignment['id']}_{len(hint_records) + 1}",
        )
        if hint_clicked:
            try:
                with st.spinner("AI正在结合当前代码和汇总判题证据生成渐进提示……"):
                    hint = llm.generate_learning_hint(
                        assignment["title"],
                        assignment["requirements"],
                        code,
                        explanation.strip(),
                        code_verification,
                        len(hint_records) + 1,
                        hint_limit,
                        previous_hints=current_code_hints,
                        constraints_text=assignment.get("constraints_text", ""),
                    )
                    db.save_ai_hint(
                        assignment["id"],
                        student_id,
                        name,
                        code,
                        code_verification.get("overall_status", "未执行"),
                        hint,
                        hint_limit,
                    )
            except (llm.LLMServiceError, ValueError, sqlite3.Error) as error:
                st.error(f"AI提示生成失败：{error}")
            else:
                set_flash("AI学习提示已生成，提示次数已经记录。")
                st.rerun()
    elif hint_limit and len(hint_records) >= hint_limit:
        st.warning(f"本实验的{hint_limit}次AI提示已经全部使用。")

    if admitted:
        st.success(admission_message)
        submitted = st.button(
            (
                "进入AI答辩"
                if assignment.get("verification_enabled")
                else "调用AI分析并开始答辩"
            ),
            type="primary",
            width="stretch",
            disabled=not llm.is_configured(),
            key=f"submit_assignment_{assignment['id']}",
        )
    else:
        st.warning(admission_message)
        submitted = False

    if not submitted:
        return
    if not student_submission_is_valid(
        name,
        student_id,
        explanation,
        lab_report,
        code,
    ):
        return
    if assignment.get("verification_enabled"):
        admitted, admission_message = defense_admission_result(
            assignment,
            code_verification,
        )
        if not formal_matches or not admitted:
            st.error("当前代码没有有效且满足准入规则的正式评测结果，请重新评测。")
            return

    st.session_state.submission_ai_error = ""
    try:
        with st.status(
            "正在处理实验提交……",
            expanded=True,
        ) as ai_status:
            st.write("步骤1/3：读取已经完成的正式判题证据")
            if assignment.get("verification_enabled"):
                st.write(
                    "正式判题："
                    f"{code_verification.get('overall_status', '未执行')}，"
                    f"{code_verification.get('passed', 0)}/"
                    f"{code_verification.get('total', 0)}组通过，"
                    f"客观得分{code_verification.get('score', 0)}/"
                    f"{code_verification.get('max_score', 0)}分。"
                )
            else:
                code_verification = {}
                st.write("本实验未启用代码判题，按照教师设置继续进行AI静态分析。")

            st.write("步骤2/3：按教师评分点分析代码、解题思路与选做报告")
            preliminary_review = llm.preliminary_review(
                assignment["title"],
                assignment["requirements"],
                code,
                explanation.strip(),
                lab_report.strip(),
                assignment["rubric"],
                code_verification=code_verification,
                constraints_text=assignment.get("constraints_text", ""),
            )
            st.write("步骤3/3：根据证据缺口生成并保存个性化答辩问题")
            questions = llm.generate_questions(
                assignment["title"],
                assignment["requirements"],
                code,
                explanation.strip(),
                assignment["teaching_focus"],
                rubric=assignment["rubric"],
                preliminary_review=preliminary_review,
                lab_report=lab_report.strip(),
                code_verification=code_verification,
                constraints_text=assignment.get("constraints_text", ""),
            )
            submitted_at = datetime.now().strftime("%Y-%m-%d %H:%M")
            submission_id = db.create_submission_with_questions(
                student_id.strip(),
                name.strip(),
                assignment["title"],
                explanation.strip(),
                code,
                submitted_at,
                questions,
                assignment,
                lab_report.strip(),
                preliminary_review,
                code_verification,
            )
            ai_status.update(
                label="AI初评与答辩问题已生成",
                state="complete",
                expanded=False,
            )
    except (llm.LLMServiceError, sqlite3.Error) as error:
        st.session_state.submission_ai_error = str(error)
        st.error(f"AI处理未完成：{error}")
        st.info("当前输入没有被清空，请直接点击“调用AI分析并开始答辩”重新尝试。")
        return

    st.session_state.submission_id = submission_id
    st.session_state.form_name = name.strip()
    st.session_state.form_student_id = student_id.strip()
    st.session_state.submission_drafts.pop(draft_key, None)
    st.session_state.formal_judge_results.pop(result_key, None)
    st.session_state.submission_ai_error = ""
    st.session_state.submission = {
        "id": submission_id,
        "name": name.strip(),
        "student_id": student_id.strip(),
        "problem": assignment["title"],
        "explanation": explanation.strip(),
        "code": code,
        "lab_report": lab_report.strip(),
        "submitted_at": submitted_at,
        "assignment_id": assignment["id"],
        "code_verification": code_verification,
        "assignment_snapshot": {
            "title": assignment["title"],
            "description": assignment["description"],
            "requirements": assignment["requirements"],
            "teaching_focus": assignment["teaching_focus"],
            "rubric": assignment["rubric"],
            "input_format": assignment.get("input_format", ""),
            "output_format": assignment.get("output_format", ""),
            "constraints_text": assignment.get("constraints_text", ""),
            "public_samples": assignment.get("public_samples", []),
            "verification_enabled": bool(assignment.get("verification_enabled")),
            "test_cases": assignment.get("test_cases", []),
            "language": assignment["language"],
        },
    }
    st.session_state.questions = questions
    st.session_state.answers = {}
    st.session_state.current_question = 0
    st.session_state.awaiting_follow_up = False
    st.session_state.current_follow_up_question = ""
    st.session_state.defense_completed = False
    st.session_state.report = None
    st.session_state.report_error = ""
    navigate("AI答辩")


def show_answer_history():
    completed_count = min(
        st.session_state.current_question,
        len(st.session_state.questions),
    )
    if completed_count == 0:
        return

    with st.expander("查看已完成的答辩记录"):
        for index in range(completed_count):
            question = st.session_state.questions[index]
            answer_record = st.session_state.answers[index]
            final_evaluation = (
                answer_record.get("final_evaluation")
                or answer_record.get("initial_evaluation")
                or {}
            )
            st.markdown(f"**问题{index + 1}：{question['question']}**")
            st.write("首次回答：", answer_record.get("initial", ""))
            if answer_record.get("follow_up"):
                st.write("AI追问：", answer_record["follow_up_question"])
                st.write("追问回答：", answer_record["follow_up"])
            if final_evaluation:
                st.write("AI反馈：", final_evaluation.get("feedback", ""))
                st.write("AI理解度评分：", final_evaluation.get("score", ""))
            st.divider()


def show_report_retry():
    st.warning("三道问题已完成，但最终报告尚未生成。")
    if st.session_state.report_error:
        st.error(st.session_state.report_error)

    if st.button("重新生成报告", type="primary"):
        with st.spinner("AI正在重新汇总答辩记录……"):
            try:
                finalize_report()
            except (llm.LLMServiceError, sqlite3.Error) as error:
                st.session_state.report_error = str(error)
                st.rerun()
        st.rerun()


def show_defense():
    page_header("AI自适应答辩", "每次提交回答都会调用AI评价；回答不充分时最多追问一次。")
    show_flash()

    if st.session_state.submission_id is None:
        st.warning("当前会话还没有实验提交。历史记录可以在教师工作台查看。")
        if st.button("前往实验提交"):
            navigate("实验提交")
        return

    if st.session_state.defense_completed:
        st.success("答辩已经完成，AI报告和评价证据已保存到数据库。")
        if st.button("查看学生报告", type="primary"):
            navigate("学生报告")
        render_student_feedback_form(st.session_state.submission_id)
        show_answer_history()
        return

    if st.session_state.current_question >= len(st.session_state.questions):
        show_report_retry()
        show_answer_history()
        return

    verification = (st.session_state.submission or {}).get("code_verification", {})
    if verification:
        status = verification.get("overall_status", "未执行")
        total = verification.get("total", 0)
        passed = verification.get("passed", 0)
        score = verification.get("score")
        max_score = verification.get("max_score")
        st.info(
            f"本次代码验证：{status}"
            + (f"，{passed}/{total}组样例通过。" if total else "。")
            + (f"代码客观得分：{score}/{max_score}分。" if max_score else "")
            + " AI问题会结合该证据，但不会向学生泄露隐藏样例。"
        )

    questions = st.session_state.questions
    index = st.session_state.current_question
    question = questions[index]

    st.progress(index / len(questions), text=f"当前进度：第{index + 1}题 / 共{len(questions)}题")

    with st.container(border=True):
        st.caption(f"AI考查维度：{question['dimension']}")
        st.subheader(f"问题{index + 1}")
        st.write(question["question"])

    if not st.session_state.awaiting_follow_up:
        with st.form(f"answer_form_{index}"):
            answer = st.text_area("请输入你的回答", height=150)
            answer_submitted = st.form_submit_button("提交给AI评价", type="primary")

        if answer_submitted:
            if not answer.strip():
                st.error("回答不能为空。")
                return

            with st.spinner("AI正在评价回答并判断是否需要追问……"):
                try:
                    evaluation = llm.evaluate_initial_answer(
                        st.session_state.submission["problem"],
                        st.session_state.submission["code"],
                        question,
                        answer.strip(),
                    )
                    follow_up_question = evaluation["follow_up_question"]
                    db.save_initial_result(
                        st.session_state.submission_id,
                        index,
                        answer.strip(),
                        follow_up_question,
                        evaluation,
                        is_final=not evaluation["should_follow_up"],
                    )
                except (llm.LLMServiceError, sqlite3.Error) as error:
                    st.error(str(error))
                    return

            st.session_state.answers[index] = {
                "initial": answer.strip(),
                "follow_up_question": follow_up_question,
                "follow_up": "",
                "initial_evaluation": evaluation,
                "final_evaluation": evaluation if not evaluation["should_follow_up"] else {},
            }

            if evaluation["should_follow_up"]:
                st.session_state.awaiting_follow_up = True
                st.session_state.current_follow_up_question = follow_up_question
            else:
                move_to_next_question()
            st.rerun()
    else:
        st.warning("AI发现回答中还有需要澄清的内容，请完成下面这次追问。")
        follow_up_question = st.session_state.current_follow_up_question
        st.markdown(f"**AI追问：{follow_up_question}**")

        with st.form(f"follow_up_form_{index}"):
            follow_up_answer = st.text_area("请输入追问回答", height=140)
            follow_up_submitted = st.form_submit_button("提交追问回答", type="primary")

        if follow_up_submitted:
            if not follow_up_answer.strip():
                st.error("追问回答不能为空。")
                return

            answer_record = st.session_state.answers[index]
            with st.spinner("AI正在结合两次回答生成最终评价……"):
                try:
                    evaluation = llm.evaluate_follow_up(
                        st.session_state.submission["problem"],
                        st.session_state.submission["code"],
                        question,
                        answer_record["initial"],
                        follow_up_question,
                        follow_up_answer.strip(),
                        answer_record.get("initial_evaluation"),
                    )
                    db.save_follow_up_result(
                        st.session_state.submission_id,
                        index,
                        follow_up_answer.strip(),
                        evaluation,
                    )
                except (llm.LLMServiceError, sqlite3.Error) as error:
                    st.error(str(error))
                    return

            st.session_state.answers[index]["follow_up"] = follow_up_answer.strip()
            st.session_state.answers[index]["final_evaluation"] = evaluation
            move_to_next_question()
            st.rerun()

    show_answer_history()


def render_preliminary_review(preliminary_review):
    """展示按教师评分点生成的证据初评和可追溯依据。"""
    if not preliminary_review:
        st.info("该记录创建于评分点初评功能上线前，暂无评分点证据初评。")
        return

    st.subheader("评分点初评证据")
    st.caption(
        "评分点证据初评来自代码、解题思路与可选报告的静态证据；"
        "未填写选做报告不会单独扣分，答辩理解度将在后续问答中形成。"
    )
    criteria = preliminary_review.get("criteria", [])
    evidence_df = pd.DataFrame(
        [
            {
                "评分点": item["criterion_name"],
                "核查来源": item.get("source", "—"),
                "初评状态": item["status"],
                "得分": f"{item['score']} / {item['weight']}",
                "需答辩核验": "是" if item.get("needs_defense") else "否",
                "证据置信度": item.get("confidence", "—"),
            }
            for item in criteria
        ]
    )
    if not evidence_df.empty:
        render_centered_dataframe(evidence_df)
    st.info(f"AI初评说明：{preliminary_review['summary']}")

    for index, item in enumerate(criteria, start=1):
        with st.expander(
            f"评分点{index} · {item['criterion_name']} · "
            f"{item['score']}/{item['weight']}分"
        ):
            st.write("代码证据：", item.get("code_evidence", "未记录"))
            st.write("报告证据：", item.get("report_evidence", "未记录"))
            st.write("尚缺内容：", item.get("missing", "无"))
            if item.get("needs_defense"):
                st.warning("答辩核验重点：" + item.get("defense_focus", "需要进一步核验。"))


def render_code_verification(code_verification, teacher_view=False):
    """展示编译与测试证据；学生端不泄露隐藏输入和预期输出。"""
    st.subheader("代码客观验证")
    if not code_verification:
        st.info("该实验未启用代码验证，或该记录创建于代码验证功能上线前。")
        return

    status = code_verification.get("overall_status", "未执行")
    compile_result = code_verification.get("compile", {})
    passed = int(code_verification.get("passed", 0) or 0)
    total = int(code_verification.get("total", 0) or 0)
    score = int(code_verification.get("score", 0) or 0)
    max_score = int(code_verification.get("max_score", 0) or 0)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("验证结论", status)
    col2.metric("编译状态", compile_result.get("status", "未执行"))
    col3.metric("样例通过", f"{passed} / {total}" if total else "未执行")
    col4.metric("代码客观得分", f"{score} / {max_score}" if max_score else "历史未计分")
    effective_standard = code_verification.get("effective_cpp_standard")
    requested_standard = code_verification.get("requested_cpp_standard", "auto")
    if effective_standard:
        st.caption(
            f"编译标准：教师设置为{verifier.cpp_standard_label(requested_standard)}；"
            f"本次实际使用{verifier.cpp_standard_label(effective_standard)}"
            f"（{compile_result.get('standard_flag', '')}）。"
        )
    st.caption(
        "代码客观得分只由冻结的隐藏测试点计算；它与AI答辩理解度、"
        "教师确认结果相互独立，也不是课程最终成绩。"
    )

    compile_message = str(compile_result.get("message", "")).strip()
    if compile_result.get("status") == "失败":
        st.error("代码编译失败；编译器已接受标准选项，请检查下方代码诊断信息。")
        if compile_message:
            st.code(compile_message, language="text")
    elif status in {
        "未启用",
        "环境不可用",
        "编译环境不兼容",
        "未执行",
        "编译超时",
    }:
        st.warning(code_verification.get("message", "本次未执行代码验证。"))
        if compile_result.get("status") == "环境不兼容" and compile_message:
            st.code(compile_message, language="text")

    cases = code_verification.get("cases", [])
    if not cases:
        return
    case_df = pd.DataFrame(
        [
            {
                "测试样例": item.get("name", f"样例{index}"),
                "测试分组": item.get("group", "未分组"),
                "判定": (
                    f"{item.get('status', '未执行')}"
                    + (
                        f"（{item.get('verdict_code')}）"
                        if item.get("verdict_code")
                        else ""
                    )
                ),
                "得分": (
                    f"{item.get('score', 0)}/{item.get('weight', 0)}"
                    if item.get("weight") is not None
                    else "历史未计分"
                ),
                "耗时(ms)": item.get("duration_ms", 0),
                "说明": item.get("message", ""),
            }
            for index, item in enumerate(cases, start=1)
        ]
    )
    render_centered_dataframe(case_df)

    group_scores = code_verification.get("group_scores", [])
    if group_scores:
        st.markdown("**测试分组得分**")
        render_centered_dataframe(
            pd.DataFrame(
                [
                    {
                        "测试分组": item.get("group", "未分组"),
                        "通过测试点": f"{item.get('passed', 0)}/{item.get('total', 0)}",
                        "分组得分": f"{item.get('score', 0)}/{item.get('max_score', 0)}",
                    }
                    for item in group_scores
                ]
            )
        )

    if teacher_view:
        with st.expander("教师查看隐藏样例输入与输出证据"):
            for index, item in enumerate(cases, start=1):
                st.markdown(
                    f"**样例{index} · {item.get('name', '')} · "
                    f"{item.get('group', '未分组')} · "
                    f"{item.get('score', 0)}/{item.get('weight', 0)}分 · "
                    f"{item.get('status', '未执行')}**"
                )
                left, middle, right = st.columns(3)
                with left:
                    st.caption("标准输入")
                    st.code(item.get("input", "（空输入）"), language="text")
                with middle:
                    st.caption("预期输出")
                    st.code(item.get("expected_output", ""), language="text")
                with right:
                    st.caption("实际输出")
                    st.code(item.get("actual_output", ""), language="text")
                if item.get("stderr"):
                    st.caption("运行错误输出")
                    st.code(item["stderr"], language="text")
                if item.get("evidence_truncated"):
                    st.caption(
                        "该测试点包含超长输入或输出；为控制报告体积，"
                        f"这里只展示各字段前{verifier.EVIDENCE_PREVIEW_CHARS}个字符。"
                        "判定仍使用完整内容。"
                    )


def render_report(submission, report, qa_records):
    attempt_number = int(submission.get("attempt_number") or 1)
    st.write(f"学生：**{submission['name']}**　学号：**{submission['student_id']}**")
    st.write(
        f"实验：**{submission['problem']}**　"
        f"第**{attempt_number}**次答辩　提交时间：{submission['submitted_at']}"
    )
    if submission.get("parent_submission_id"):
        st.caption(f"本次为重新答辩，关联上一次提交#{submission['parent_submission_id']}。")

    preliminary_review = db.get_preliminary_review(submission["id"])
    teacher_review = report.get("teacher_review")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric(
        "评分点证据初评",
        (
            f"{preliminary_review['completion_score']} / 100"
            if preliminary_review
            else "历史记录"
        ),
    )
    col2.metric("答辩理解度", f"{report['overall']} / 100")
    col3.metric("诊断等级", report["level"])
    col4.metric(
        "教师确认理解度",
        (
            f"{teacher_review['confirmed_overall']} / 100"
            if teacher_review
            else "待复核"
        ),
    )
    code_result, code_score = compact_code_result(submission)
    teacher_result = (
        f"教师已确认为{teacher_review['confirmed_overall']}/100"
        if teacher_review
        else "等待教师复核"
    )
    st.success(
        f"当前结论：代码{code_result}（{code_score}）；"
        f"答辩理解度{report['overall']}/100；"
        f"{insights.mastery_summary(report, qa_records)['text']}；{teacher_result}。"
    )
    st.info(
        "评分口径：代码客观得分来自隐藏测试；评分点证据初评来自代码、思路与"
        "选做报告；答辩理解度来自问答；教师确认理解度来自人工复核。"
        "四项结果互不相加，也不是课程最终成绩。"
    )
    st.caption("掌握证据状态：" + insights.mastery_summary(report, qa_records)["status"])

    render_code_verification(submission.get("code_verification"), teacher_view=False)
    render_preliminary_review(preliminary_review)

    short_dimension_names = {
        "程序逻辑理解": "逻辑理解",
        "关键概念掌握": "概念掌握",
        "边界情况意识": "边界意识",
        "分析与修改能力": "修改能力",
    }
    dimension_df = pd.DataFrame(
        {
            "维度": [
                short_dimension_names.get(name, name)
                for name in report["dimensions"]
            ],
            "AI理解度评分": list(report["dimensions"].values()),
        }
    )
    st.subheader("四维表现")
    render_category_bar_chart(
        dimension_df,
        category="维度",
        value="AI理解度评分",
        height=360,
        maximum=100,
    )
    st.caption("四维分数由AI结构化评价与三道题的逐点掌握情况共同校准。")

    left, right = st.columns(2)
    with left:
        st.info(f"AI诊断总结：{report['summary']}")
    with right:
        st.warning(f"学习建议：{report['suggestion']}")

    if report.get("review_required"):
        reasons = report.get("review_reasons", [])
        st.warning("建议教师复核：" + "；".join(reasons))
    else:
        st.success("当前未发现必须人工复核的异常，教师可进行常规抽查。")

    if teacher_review:
        st.subheader("教师人工复核结果")
        review_col1, review_col2, review_col3 = st.columns(3)
        review_col1.metric("复核状态", "已复核")
        review_col2.metric("教师结论", teacher_review["decision"])
        review_col3.metric(
            "教师确认理解度",
            f"{teacher_review['confirmed_overall']} / 100",
        )
        if teacher_review.get("comment"):
            st.info(f"教师复核意见：{teacher_review['comment']}")
        else:
            st.caption("教师未补充文字意见。")
        st.caption(
            f"复核教师：{teacher_review['reviewer_name']}　"
            f"复核时间：{teacher_review['reviewed_at']}"
        )
    elif report.get("review_required"):
        st.info("人工复核状态：等待教师处理。AI建议仅供参考。")

    student_feedbacks = report.get("student_feedbacks", [])
    if student_feedbacks:
        st.subheader("学生反馈与教师处理")
        for item in student_feedbacks:
            status_icon = {
                "待处理": "🟠",
                "已阅": "🔵",
                "已回复": "🟢",
            }.get(item["status"], "⚪")
            with st.container(border=True):
                st.markdown(
                    f"**{status_icon} {item['category']} · {item['status']}**"
                )
                st.write(item["content"])
                reply_note = "希望教师回复" if item["reply_requested"] else "无需专门回复"
                st.caption(f"提交时间：{item['created_at']}　{reply_note}")
                if item["status"] == "已回复" and item["teacher_reply"]:
                    st.success(f"教师回复：{item['teacher_reply']}")
                    st.caption(
                        f"处理教师：{item['replied_by']}　"
                        f"处理时间：{item['replied_at']}"
                    )
                elif item["status"] == "已阅":
                    st.info(
                        f"教师已阅。处理教师：{item['replied_by']}　"
                        f"处理时间：{item['replied_at']}"
                    )

    st.subheader("诊断证据")
    for index, item in enumerate(qa_records, start=1):
        evaluation = item.get("final_evaluation") or item.get("initial_evaluation") or {}
        score = evaluation.get("score", item.get("reference_score"))
        title = f"问题{index}"
        if score is not None:
            title += f" · AI理解度评分 {score}"

        with st.expander(title):
            st.write("问题：", item["question"])
            if item.get("question_reason"):
                st.write("问题生成依据：", item["question_reason"])
            st.write("首次回答：", item.get("answer") or "未作答")
            if item.get("follow_up_answer"):
                st.write("AI追问：", item["follow_up_question"])
                st.write("追问回答：", item["follow_up_answer"])
            if evaluation.get("feedback"):
                st.write("AI评价依据：", evaluation["feedback"])
            if evaluation.get("confidence"):
                st.write("证据置信度：", evaluation["confidence"])
            if evaluation.get("missing_points"):
                st.write("仍需巩固：", "；".join(evaluation["missing_points"]))

    st.caption("AI诊断仅供教学参考，教师应结合课程要求和学生实际情况进行复核。")


def render_student_feedback_form(submission_id):
    """让完成答辩的学生向教师反馈，并阻止空内容或重复提交。"""
    feedbacks = db.list_student_feedbacks(submission_id=submission_id)
    pending_count = sum(item["status"] == "待处理" for item in feedbacks)

    st.subheader("给教师留言")
    st.caption(
        "可反馈对AI结果的疑问、补充答辩说明或学习困难；内容会进入教师待处理队列。"
    )
    if pending_count:
        st.info(f"当前有{pending_count}条反馈等待教师处理，你仍可补充新的不同内容。")

    category_options = [
        "对AI结果有疑问",
        "补充答辩说明",
        "希望获得学习指导",
        "其他反馈",
    ]
    with st.form(f"student_feedback_form_{submission_id}"):
        category = st.selectbox("反馈类型", category_options)
        feedback_content = st.text_area(
            "反馈内容",
            placeholder="例如：我对第三道题的评分依据还有疑问，希望老师帮我看一下。",
            height=110,
        )
        reply_requested = st.checkbox("希望教师回复", value=True)
        feedback_submitted = st.form_submit_button(
            "提交反馈给教师",
            type="primary",
            width="stretch",
            disabled=demo_mode.demo_read_only(),
        )

    if demo_mode.demo_read_only():
        st.caption("在线Demo不保存反馈；完整本地版可提交给教师。")

    if feedback_submitted:
        try:
            db.save_student_feedback(
                submission_id,
                category,
                feedback_content,
                reply_requested,
                datetime.now().strftime("%Y-%m-%d %H:%M"),
            )
        except (ValueError, sqlite3.Error) as error:
            st.error(f"提交反馈失败：{error}")
        else:
            set_flash("反馈已提交给教师，可在学生报告中查看处理状态。")
            st.rerun()


def render_redefense_action(submission):
    """在学生报告中展示教师重新答辩要求及开始、继续或查看入口。"""
    request = db.get_latest_redefense_request(submission["id"])
    if not request or request["status"] == "已取消":
        return

    st.subheader("教师重新答辩要求")
    if request["status"] == "待开始":
        st.warning(f"教师要求你重新答辩：{request['reason']}")
        st.caption(f"要求时间：{request['requested_at']}。新答辩会保留原提交和原报告。")
        if not llm.is_configured():
            st.error("当前AI模型未配置，暂时无法生成重新答辩问题。")
            return
        if st.button(
            "开始教师要求的重新答辩",
            type="primary",
            width="stretch",
            key=f"start_redefense_{request['id']}",
        ):
            try:
                start_or_resume_redefense(submission, request)
            except (ValueError, llm.LLMServiceError, sqlite3.Error) as error:
                st.error(f"开始重新答辩失败：{error}")
    elif request["status"] == "答辩中":
        st.info(f"重新答辩正在进行：{request['reason']}")
        if st.button(
            "继续重新答辩",
            type="primary",
            width="stretch",
            key=f"resume_redefense_{request['id']}",
        ):
            try:
                start_or_resume_redefense(submission, request)
            except (ValueError, sqlite3.Error) as error:
                st.error(f"恢复重新答辩失败：{error}")
    elif request["status"] == "已完成":
        st.success(
            f"重新答辩已经完成，新记录为提交#{request['new_submission_id']}，"
            "请等待教师复核。"
        )
        if st.button(
            "查看重新答辩报告",
            width="stretch",
            key=f"view_redefense_{request['id']}",
        ):
            try:
                load_submission_session(request["new_submission_id"])
            except (ValueError, sqlite3.Error) as error:
                st.error(f"打开重新答辩报告失败：{error}")
            else:
                navigate("学生报告")


def show_report():
    page_header("学生理解度报告", "报告由真实大模型根据完整问答生成，并保存到SQLite。")
    show_flash()

    student_id = logged_in_student_id()
    submission_id = None
    if st.session_state.report is not None:
        submission_id = st.session_state.submission_id
    else:
        completed_records = [
            record
            for record in db.list_submissions(student_id=student_id)
            if record["status"] == "已完成"
        ]
        if not completed_records:
            st.warning("你目前还没有已完成的答辩报告。")
            return

        option_map = {
            (
                f"{record['name']} · {record['student_id']} · "
                f"提交#{record['id']} · 第{record.get('attempt_number', 1)}次答辩"
            ): record["id"]
            for record in completed_records
        }
        selected_label = st.selectbox("选择一份历史报告", list(option_map.keys()))
        submission_id = option_map[selected_label]

    submission = db.get_submission(submission_id)
    report = db.get_report(submission_id)
    qa_records = db.get_qa_records(submission_id)

    if submission and submission.get("student_id") != student_id:
        st.error("不能查看其他学生的报告。")
        return
    if submission is None or report is None:
        st.error("报告数据不完整，请到教师工作台检查记录。")
        return
    for feedback in report.get("student_feedbacks", []):
        if feedback["status"] == "已回复" and not feedback.get("student_viewed_at"):
            db.mark_student_feedback_viewed(
                feedback["id"],
                submission["student_id"],
                datetime.now().strftime("%Y-%m-%d %H:%M"),
            )
    render_report(submission, report, qa_records)
    render_redefense_action(submission)
    render_student_feedback_form(submission_id)


def calculate_dimension_averages(records):
    """计算多份报告的四维平均分。"""
    if not records:
        return {}

    totals = {}
    counts = {}
    for record in records:
        for dimension, score in record.get("dimensions", {}).items():
            totals[dimension] = totals.get(dimension, 0) + score
            counts[dimension] = counts.get(dimension, 0) + 1

    return {
        dimension: round(totals[dimension] / counts[dimension])
        for dimension in totals
        if counts[dimension] > 0
    }


def show_learning_archive():
    page_header(
        "学生学习档案",
        "汇总同一学号的多次答辩记录，观察理解度变化和长期薄弱点。",
    )

    student_id = logged_in_student_id()
    st.caption(f"当前学生：{st.session_state.auth_name} · {student_id}")

    all_submissions = db.list_submissions(student_id=student_id)
    learning_records = db.list_learning_records(student_id=student_id)
    if not all_submissions:
        st.warning("没有找到该学号的实验提交记录。")
        return

    latest_name = all_submissions[0]["name"]
    st.subheader(f"{latest_name} · {student_id}")

    completed_count = len(learning_records)
    average_score = (
        round(
            sum(record["overall"] for record in learning_records)
            / completed_count
        )
        if completed_count
        else 0
    )
    latest_score = learning_records[-1]["overall"] if learning_records else 0
    latest_weakest = (
        learning_records[-1]["weakest"]
        if learning_records
        else "尚无报告"
    )
    score_delta = None
    if completed_count >= 2:
        score_delta = latest_score - learning_records[-2]["overall"]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("提交次数", len(all_submissions))
    col2.metric("已完成答辩", completed_count)
    col3.metric("平均理解度", average_score)
    col4.metric("最近理解度", latest_score, delta=score_delta)
    st.caption(f"最近一次相对较低维度：{latest_weakest}")

    if not learning_records:
        st.info("已有实验提交，但暂时没有完成的答辩报告。")
        return

    left, right = st.columns(2)
    with left:
        st.subheader("答辩理解度趋势")
        trend_df = pd.DataFrame(
            {
                "答辩次数": range(1, completed_count + 1),
                "答辩理解度": [
                    record["overall"]
                    for record in learning_records
                ],
            }
        )
        st.line_chart(
            trend_df,
            x="答辩次数",
            y="答辩理解度",
            height=330,
        )

    with right:
        st.subheader("四维平均表现")
        dimension_averages = calculate_dimension_averages(learning_records)
        short_names = {
            "程序逻辑理解": "逻辑理解",
            "关键概念掌握": "概念掌握",
            "边界情况意识": "边界意识",
            "分析与修改能力": "修改能力",
        }
        dimension_df = pd.DataFrame(
            {
                "维度": [
                    short_names.get(name, name)
                    for name in dimension_averages
                ],
                "平均分": list(dimension_averages.values()),
            }
        )
        render_category_bar_chart(
            dimension_df,
            category="维度",
            value="平均分",
            height=330,
            maximum=100,
        )

    st.subheader("历史记录")
    history_df = pd.DataFrame(
        [
            {
                "提交编号": record["id"],
                "答辩次数": f"第{record.get('attempt_number', 1)}次",
                "实验任务": record["assignment_title"],
                "答辩理解度": record["overall"],
                "诊断等级": record["level"],
                "相对较低维度": record["weakest"],
                "是否建议复核": "是" if record["review_required"] else "否",
                "人工复核状态": teacher_review_status(record),
                "教师确认理解度": (
                    record["teacher_confirmed_overall"]
                    if record["teacher_reviewed"]
                    else "—"
                ),
                "学生反馈": (
                    f"待处理{record['pending_feedback_count']}条"
                    if record.get("pending_feedback_count")
                    else (
                        f"共{record['feedback_count']}条，已处理"
                        if record.get("feedback_count")
                        else "无"
                    )
                ),
                "提交时间": record["submitted_at"],
            }
            for record in reversed(learning_records)
        ]
    )
    render_centered_dataframe(history_df)

    st.subheader("查看历史详细报告")
    option_map = {
        (
            f"提交#{record['id']} · {record['assignment_title']} · "
            f"{record['overall']}分"
        ): record["id"]
        for record in reversed(learning_records)
    }
    selected_label = st.selectbox(
        "选择报告",
        list(option_map),
        key="archive_report_select",
    )
    selected_id = option_map[selected_label]
    submission = db.get_submission(selected_id)
    report = db.get_report(selected_id)
    qa_records = db.get_qa_records(selected_id)
    if submission and report:
        render_report(submission, report, qa_records)

    st.caption(
        "当前简化版按登录时填写的学号隔离档案，但尚未验证学生真实身份；"
        "正式教学部署仍应接入学校账号认证。"
    )


def lines_to_list(text):
    """把教师输入的多行文字转换成字符串列表。"""
    items = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("- "):
            line = line[2:].strip()
        if line and line not in items:
            items.append(line)
    return items


def normalize_editor_scalar(value):
    """修复Streamlit空白单元格偶尔返回空列表的问题。"""
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        if len(value) == 1:
            return value[0]
    return value


def editor_value_is_blank(value):
    """安全判断表格单元格是否为空，避免列表参与集合或布尔判断。"""
    value = normalize_editor_scalar(value)
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def editor_text(value):
    """把表格文本单元格转换成稳定的多行字符串。"""
    value = normalize_editor_scalar(value)
    if editor_value_is_blank(value):
        return ""
    return db.normalize_sample_text(value)


def rubric_to_dataframe(rubric):
    """把评分点转换成教师可编辑的表格。"""
    return pd.DataFrame(
        [
            {
                "评分点": item.get("name", ""),
                "权重": item.get("weight", 0),
                "核查来源": item.get("source", "代码＋实验报告"),
                "评价说明": item.get("description", ""),
                "必须答辩": bool(item.get("must_defend", False)),
            }
            for item in rubric
        ]
    )


def test_cases_to_dataframe(test_cases):
    """把隐藏测试样例转换成教师可编辑表格。"""
    return pd.DataFrame(
        [
            {
                "样例名称": item.get("name", ""),
                "测试分组": item.get("group", "基础功能"),
                "分值": item.get("weight"),
                "标准输入": item.get("input", ""),
                "预期输出": item.get("expected_output", ""),
            }
            for item in test_cases
        ],
        columns=["样例名称", "测试分组", "分值", "标准输入", "预期输出"],
    )


def dataframe_to_test_cases(data):
    """移除测试样例编辑器中的空行。"""
    test_cases = []
    for row in data.to_dict("records"):
        values = {}
        for column in ["样例名称", "测试分组", "标准输入", "预期输出"]:
            values[column] = editor_text(row.get(column, ""))
        raw_weight = normalize_editor_scalar(row.get("分值"))
        weight = None if editor_value_is_blank(raw_weight) else raw_weight
        if not any(value.strip() for value in values.values()) and weight is None:
            continue
        test_cases.append(
            {
                "name": values["样例名称"].strip(),
                "group": values["测试分组"].strip() or "基础功能",
                "weight": weight,
                "input": values["标准输入"],
                "expected_output": values["预期输出"],
            }
        )
    return test_cases


def render_test_case_editor(key, test_cases):
    """使用卡片输入1至8组隐藏测试，完整保留换行与空格。"""
    st.caption(
        "依次展开并填写测试点；未填写的卡片会自动忽略。"
        "标准输入和预期输出均可直接粘贴多行文本。"
    )
    cases = []
    for index in range(8):
        seed = test_cases[index] if index < len(test_cases) else {}
        seed_name = editor_text(seed.get("name", ""))
        card_title = f"测试点{index + 1}"
        if seed_name:
            card_title += f" · {seed_name}"
        with st.expander(card_title, expanded=index < max(1, len(test_cases))):
            name_col, group_col, weight_col = st.columns([2, 2, 1])
            with name_col:
                name = st.text_input(
                    "样例名称（可留空）",
                    value=seed_name,
                    key=f"{key}_{index}_name",
                    placeholder=f"留空时自动命名为测试{index + 1}",
                )
            with group_col:
                group = st.text_input(
                    "测试分组（可留空）",
                    value=editor_text(seed.get("group", "")),
                    key=f"{key}_{index}_group",
                    placeholder="基础功能 / 边界情况 / 性能要求",
                )
            with weight_col:
                seed_weight = normalize_editor_scalar(seed.get("weight"))
                weight = st.number_input(
                    "分值（可留空）",
                    min_value=1,
                    max_value=100,
                    value=(
                        None
                        if editor_value_is_blank(seed_weight)
                        else int(float(seed_weight))
                    ),
                    step=1,
                    key=f"{key}_{index}_weight",
                    placeholder="自动",
                )
            input_col, output_col = st.columns(2)
            with input_col:
                case_input = st.text_area(
                    "标准输入",
                    value=editor_text(seed.get("input", "")),
                    key=f"{key}_{index}_input",
                    height=150,
                    help="保留原始换行和空格；没有输入时可以留空。",
                )
            with output_col:
                expected_output = st.text_area(
                    "预期输出",
                    value=editor_text(seed.get("expected_output", "")),
                    key=f"{key}_{index}_output",
                    height=150,
                    help="每一行输出都按题目要求填写。",
                )
        values = [name, group, case_input, expected_output]
        if not any(value.strip() for value in values) and weight is None:
            continue
        cases.append(
            {
                "name": name.strip() or f"测试{index + 1}",
                "group": group.strip() or "基础功能",
                "weight": weight,
                "input": case_input,
                "expected_output": expected_output,
            }
        )
    filled_weights = []
    invalid_weight = False
    for item in cases:
        raw_weight = normalize_editor_scalar(item.get("weight"))
        if editor_value_is_blank(raw_weight):
            continue
        try:
            numeric_weight = float(raw_weight)
        except (TypeError, ValueError):
            invalid_weight = True
            continue
        if not numeric_weight.is_integer():
            invalid_weight = True
            continue
        filled_weights.append(int(numeric_weight))
    if invalid_weight:
        st.caption("存在格式不正确的分值；请填写1至100之间的整数，或全部留空。")
    elif cases and len(filled_weights) == len(cases):
        st.caption(
            f"当前共{len(cases)}组隐藏测试点，分值合计{sum(filled_weights)}；"
            "最多8组，保存时必须合计100。"
        )
    else:
        st.caption(
            f"当前共{len(cases)}组隐藏测试点，最多8组；"
            "未填写分值时保存后自动补齐到100分。"
        )
    return cases


def public_samples_to_dataframe(public_samples):
    """把学生可见的OJ公开样例转换成教师可编辑表格。"""
    return pd.DataFrame(
        [
            {
                "样例名称": item.get("name", ""),
                "样例输入": item.get("input", ""),
                "样例输出": item.get("output", ""),
                "样例说明": item.get("explanation", ""),
            }
            for item in public_samples
        ],
        columns=["样例名称", "样例输入", "样例输出", "样例说明"],
    )


def dataframe_to_public_samples(data):
    """移除公开样例编辑器中的全空行，保留待校验的非空行。"""
    samples = []
    for row in data.to_dict("records"):
        values = {}
        for column in ["样例名称", "样例输入", "样例输出", "样例说明"]:
            values[column] = editor_text(row.get(column, ""))
        if not any(value.strip() for value in values.values()):
            continue
        samples.append(
            {
                "name": values["样例名称"].strip(),
                "input": values["样例输入"],
                "output": values["样例输出"],
                "explanation": values["样例说明"].strip(),
            }
        )
    return samples


def render_public_sample_editor(key, public_samples):
    """使用卡片输入0至5组公开样例，完整保留换行与空格。"""
    st.caption("需要公开样例时展开填写；全空卡片会自动忽略。")
    samples = []
    for index in range(5):
        seed = public_samples[index] if index < len(public_samples) else {}
        seed_name = editor_text(seed.get("name", ""))
        card_title = f"公开样例{index + 1}"
        if seed_name:
            card_title += f" · {seed_name}"
        with st.expander(card_title, expanded=index < max(1, len(public_samples))):
            name = st.text_input(
                "样例名称（可留空）",
                value=seed_name,
                key=f"{key}_{index}_name",
                placeholder=f"留空时自动命名为样例{index + 1}",
            )
            input_col, output_col = st.columns(2)
            with input_col:
                sample_input = st.text_area(
                    "样例输入",
                    value=editor_text(seed.get("input", "")),
                    key=f"{key}_{index}_input",
                    height=130,
                )
            with output_col:
                sample_output = st.text_area(
                    "样例输出",
                    value=editor_text(seed.get("output", "")),
                    key=f"{key}_{index}_output",
                    height=130,
                )
            explanation = st.text_area(
                "样例说明（可选）",
                value=editor_text(seed.get("explanation", "")),
                key=f"{key}_{index}_explanation",
                height=80,
            )
        values = [name, sample_input, sample_output, explanation]
        if not any(value.strip() for value in values):
            continue
        samples.append(
            {
                "name": name.strip(),
                "input": sample_input,
                "output": sample_output,
                "explanation": explanation.strip(),
            }
        )
    st.caption(f"当前共{len(samples)}组公开样例，学生可以看到，最多5组。")
    return samples


def dataframe_to_rubric(data):
    """清理评分点编辑表中的空行，交由数据库统一校验。"""
    rubric = []
    for row in data.to_dict("records"):
        name = str(row.get("评分点", "")).strip()
        if not name or name.lower() == "nan":
            continue
        must_defend = row.get("必须答辩", False)
        if pd.isna(must_defend):
            must_defend = False
        rubric.append(
            {
                "name": name,
                "weight": row.get("权重", 0),
                "source": str(row.get("核查来源", "")).strip(),
                "description": str(row.get("评价说明", "")).strip(),
                "must_defend": bool(must_defend),
            }
        )
    return rubric


def render_rubric_editor(key, rubric):
    """显示动态评分点表；教师可增删1至8条评分点。"""
    edited = st.data_editor(
        rubric_to_dataframe(rubric),
        key=key,
        hide_index=True,
        num_rows="dynamic",
        width="stretch",
        column_config={
            "评分点": st.column_config.TextColumn("评分点", required=True),
            "权重": st.column_config.NumberColumn(
                "权重",
                min_value=1,
                max_value=100,
                step=1,
                required=True,
            ),
            "核查来源": st.column_config.SelectboxColumn(
                "核查来源",
                options=sorted(db.RUBRIC_SOURCES),
                required=True,
            ),
            "评价说明": st.column_config.TextColumn("评价说明", required=True),
            "必须答辩": st.column_config.CheckboxColumn("必须答辩"),
        },
    )
    rubric_rows = dataframe_to_rubric(edited)
    total = sum(
        round(float(item["weight"]))
        for item in rubric_rows
        if not pd.isna(item["weight"])
    )
    st.caption(f"当前共{len(rubric_rows)}个评分点，权重合计 {total}/100。")
    return rubric_rows


def assignment_form_is_valid(
    title,
    description,
    requirements,
    teaching_focus,
    rubric,
    verification_enabled=False,
    test_cases=None,
    public_samples=None,
    reference_code="",
    quality_warnings=None,
    quality_confirmed=True,
):
    """检查创建或修改实验时必须填写的内容。"""
    if not title.strip():
        st.error("实验名称不能为空。")
        return False
    if not description.strip():
        st.error("实验描述不能为空。")
        return False
    if not requirements:
        st.error("请至少填写1条实验要求，每行填写1条。")
        return False
    if not teaching_focus:
        st.error("请至少填写1条隐藏教学重点。")
        return False
    try:
        db.normalize_rubric(rubric)
        db.normalize_public_samples(public_samples)
        if verification_enabled:
            normalized_cases = db.normalize_test_cases(test_cases or [])
            if len(normalized_cases) < 3:
                st.error("为降低误判风险，启用代码验证时请至少配置3组隐藏测试。")
                return False
            if not str(reference_code or "").strip():
                st.error("启用代码验证时必须填写教师参考答案并通过发布前自检。")
                return False
    except ValueError as error:
        st.error(str(error))
        return False
    if quality_warnings and not quality_confirmed:
        st.error("请先修改高风险配置，或勾选确认已逐项核对评分点。")
        return False
    return True


def reference_solution_preflight(
    reference_code,
    test_cases,
    public_samples,
    cpp_standard,
):
    """发布前用教师参考答案跑完隐藏测试与公开样例。"""
    stages = [
        (
            "隐藏测试",
            db.normalize_test_cases(test_cases or []),
        )
    ]
    normalized_samples = db.normalize_public_samples(public_samples)
    if normalized_samples:
        stages.append(
            (
                "公开样例",
                [
                    {
                        "name": sample["name"],
                        "group": "公开样例",
                        "input": sample["input"],
                        "expected_output": sample["output"],
                    }
                    for sample in normalized_samples
                ],
            )
        )

    stage_results = []
    for stage_name, cases in stages:
        result = verifier.verify_cpp_code(reference_code, cases, cpp_standard)
        stage_results.append({"stage": stage_name, "result": result})
        status = result.get("overall_status", "未执行")
        if status != "全部通过":
            first_failed = next(
                (
                    case
                    for case in result.get("cases", [])
                    if case.get("status") != "通过"
                ),
                {},
            )
            case_note = (
                f"；首个未通过项：{first_failed.get('name', '未知')}"
                f"（{first_failed.get('verdict_code') or first_failed.get('status', '未知')}）"
                if first_failed
                else ""
            )
            detail = result.get("message") or result.get("compile", {}).get(
                "message",
                "",
            )
            return {
                "passed": False,
                "message": (
                    f"参考答案未通过{stage_name}自检：{status}{case_note}。"
                    f" {detail}"
                ).strip(),
                "stages": stage_results,
            }
    return {
        "passed": True,
        "message": "参考答案已通过全部隐藏测试和公开样例，可以保存实验。",
        "stages": stage_results,
    }


def assignment_publish_blockers(assignment):
    """返回会让判题或评分失去可信度的实验配置问题。"""
    quality_warnings = db.assignment_quality_warnings(
        assignment.get("title", ""),
        assignment.get("rubric", []),
    )
    blockers = [] if assignment.get("rubric_reviewed") else list(quality_warnings)
    if assignment.get("verification_enabled"):
        if len(assignment.get("test_cases") or []) < 3:
            blockers.append("启用代码验证的实验至少需要3组隐藏测试。")
        if not str(assignment.get("reference_code") or "").strip():
            blockers.append("尚未填写并自检教师参考答案。")
    return blockers


def show_assignment_management():
    page_header(
        "实验任务管理",
        "教师可以发布、修改或停用实验；隐藏教学重点只用于AI生成问题。",
    )
    show_flash()

    assignments = db.list_assignments()
    published_count = sum(
        1 for item in assignments if item["status"] == "已发布"
    )
    col1, col2, col3 = st.columns(3)
    col1.metric("全部实验", len(assignments))
    col2.metric("已发布", published_count)
    col3.metric("已停用", len(assignments) - published_count)

    create_tab, manage_tab = st.tabs(["发布新实验", "管理已有实验"])

    with create_tab:
        st.info(
            "每行填写一条实验要求或教学重点；再用评分点定义AI初评依据。"
            "隐藏教学重点不会展示给学生。"
        )
        template_col, load_col = st.columns([3, 1])
        with template_col:
            create_template_name = st.selectbox(
                "评分点模板",
                list(db.RUBRIC_TEMPLATES),
                key="create_rubric_template_name",
            )
        with load_col:
            st.write("")
            st.write("")
            load_create_template = st.button(
                "载入模板",
                key="load_create_rubric_template",
                width="stretch",
            )
        if load_create_template:
            st.session_state.create_rubric_seed = db.get_rubric_template(
                create_template_name
            )
            st.session_state.pop("create_assignment_rubric", None)
            set_flash(f"已载入“{create_template_name}”评分点模板，可继续修改。")
            st.rerun()

        create_rubric_seed = st.session_state.get(
            "create_rubric_seed",
            db.get_rubric_template("基础程序设计"),
        )
        with st.form("create_assignment_form"):
            title = st.text_input("实验名称", placeholder="例如：判断回文字符串")
            description = st.text_area(
                "实验描述",
                placeholder="说明程序需要接收什么输入、完成什么任务、输出什么结果。",
                height=100,
            )
            st.markdown("**OJ题面设置（学生可见）**")
            input_format = st.text_area(
                "输入格式（可留空）",
                placeholder="例如：第一行输入整数n，第二行输入n个整数。",
                height=90,
                max_chars=3000,
            )
            output_format = st.text_area(
                "输出格式（可留空）",
                placeholder="例如：输出数组中的最大值。",
                height=90,
                max_chars=3000,
            )
            constraints_text = st.text_area(
                "数据范围与说明（可留空）",
                placeholder="例如：1 ≤ n ≤ 100；所有输入均为整数。",
                height=90,
                max_chars=3000,
            )
            st.markdown("**公开输入输出样例（学生可见，可留空）**")
            create_public_samples = render_public_sample_editor(
                "create_assignment_public_samples",
                [],
            )
            requirements_text = st.text_area(
                "实验要求（每行一条）",
                placeholder="使用字符串保存输入\n使用循环比较字符\n考虑空字符串情况",
                height=130,
            )
            teaching_focus_text = st.text_area(
                "隐藏教学重点（每行一条）",
                placeholder="循环边界是否正确\n下标是否越界\n能否解释时间复杂度",
                height=130,
            )
            st.markdown("**评分点与证据来源**")
            create_rubric = render_rubric_editor(
                "create_assignment_rubric",
                create_rubric_seed,
            )
            st.markdown("**代码验证设置**")
            create_cpp_standard_label = st.selectbox(
                "C++编译标准",
                list(CPP_STANDARD_OPTIONS),
                key="create_cpp_standard",
                help=(
                    "自动选择会优先使用C++17；旧版GCC不支持时会尝试兼容标准，"
                    "并记录本次实际使用的标准。"
                ),
            )
            create_cpp_standard = CPP_STANDARD_OPTIONS[create_cpp_standard_label]
            create_verification_enabled = st.checkbox(
                "启用C++编译与隐藏测试样例验证",
                value=False,
                help="需要在.env中允许本机代码执行并配置g++；不可用于无隔离的公开服务。",
            )
            create_test_cases = render_test_case_editor(
                "create_assignment_test_cases",
                [],
            )
            st.caption(
                "隐藏测试默认留白；启用后至少填写3组，可按基础功能、边界情况、"
                "性能要求等分组，并设置合计100分的测试点权重。"
            )
            create_reference_code = st.text_area(
                "教师参考答案（仅教师可见）",
                value="",
                placeholder="启用代码验证时必填；发布前会实际跑完隐藏测试和公开样例。",
                height=300,
                max_chars=12000,
            )
            st.markdown("**学习支持与答辩准入**")
            create_hint_limit = st.number_input(
                "每名学生可使用的AI提示次数",
                min_value=0,
                max_value=5,
                value=2,
                step=1,
                help="按学号和实验累计；设为0表示关闭AI提示。",
            )
            create_defense_admission = st.selectbox(
                "答辩准入规则",
                DEFENSE_ADMISSION_OPTIONS,
                help="默认要求全部测试通过；允许带错代码时将进入诊断答辩，客观代码分不会改变。",
            )
            create_defense_threshold = st.number_input(
                "答辩准入分数（仅“达到指定分数”时生效）",
                min_value=0,
                max_value=100,
                value=100,
                step=5,
            )
            starter_code = st.text_area(
                "起始代码（可留空）",
                value=(
                    "#include <iostream>\n"
                    "using namespace std;\n\n"
                    "int main() {\n"
                    "    // 请在这里完成代码\n"
                    "    return 0;\n"
                    "}\n"
                ),
                height=230,
            )
            create_quality_warnings = db.assignment_quality_warnings(
                title,
                create_rubric,
            )
            if create_quality_warnings:
                for warning in create_quality_warnings:
                    st.warning(warning)
                create_quality_confirmed = st.checkbox(
                    "我已逐项核对，确认这些评分点确实适用于当前实验",
                    value=False,
                )
            else:
                create_quality_confirmed = True
            create_submitted = st.form_submit_button(
                "发布实验",
                type="primary",
                width="stretch",
            )

        if create_submitted:
            requirements = lines_to_list(requirements_text)
            teaching_focus = lines_to_list(teaching_focus_text)
            if assignment_form_is_valid(
                title,
                description,
                requirements,
                teaching_focus,
                create_rubric,
                create_verification_enabled,
                create_test_cases,
                create_public_samples,
                create_reference_code,
                create_quality_warnings,
                create_quality_confirmed,
            ):
                try:
                    if create_verification_enabled:
                        with st.spinner("正在用教师参考答案执行发布前自检……"):
                            preflight = reference_solution_preflight(
                                create_reference_code,
                                create_test_cases,
                                create_public_samples,
                                create_cpp_standard,
                            )
                        if not preflight["passed"]:
                            st.error(preflight["message"])
                            return
                    assignment_id = db.create_assignment(
                        title.strip(),
                        description.strip(),
                        requirements,
                        teaching_focus,
                        starter_code,
                        rubric=create_rubric,
                        verification_enabled=create_verification_enabled,
                        test_cases=create_test_cases,
                        input_format=input_format,
                        output_format=output_format,
                        constraints_text=constraints_text,
                        public_samples=create_public_samples,
                        cpp_standard=create_cpp_standard,
                        hint_limit=create_hint_limit,
                        defense_admission=create_defense_admission,
                        defense_score_threshold=create_defense_threshold,
                        reference_code=create_reference_code,
                        rubric_reviewed=create_quality_confirmed,
                    )
                except (ValueError, sqlite3.Error) as error:
                    st.error(f"保存实验失败：{error}")
                else:
                    set_flash(f"实验#{assignment_id}“{title.strip()}”发布成功。")
                    st.rerun()

    with manage_tab:
        if not assignments:
            st.info("目前还没有实验任务。")
            return

        option_map = {
            f"实验#{item['id']} · {item['title']} · {item['status']}": item
            for item in assignments
        }
        selected_label = st.selectbox(
            "选择要管理的实验",
            list(option_map),
            key="manage_assignment_select",
        )
        assignment = option_map[selected_label]

        status_col, time_col = st.columns(2)
        status_col.info(f"当前状态：{assignment['status']}")
        time_col.info(f"最后修改：{assignment['updated_at']}")
        existing_blockers = assignment_publish_blockers(assignment)
        if existing_blockers:
            st.warning(
                "当前配置需要修正后才能用于新的AI答辩："
                + "；".join(existing_blockers)
            )

        edit_template_col, edit_load_col = st.columns([3, 1])
        edit_template_name = edit_template_col.selectbox(
            "快速套用评分点模板",
            list(db.RUBRIC_TEMPLATES),
            key=f"edit_rubric_template_name_{assignment['id']}",
        )
        with edit_load_col:
            st.write("")
            st.write("")
            load_edit_template = st.button(
                "载入到编辑区",
                key=f"load_edit_rubric_template_{assignment['id']}",
                width="stretch",
            )
        edit_seed_key = f"edit_rubric_seed_{assignment['id']}"
        edit_widget_key = f"edit_assignment_rubric_{assignment['id']}"
        if load_edit_template:
            st.session_state[edit_seed_key] = db.get_rubric_template(
                edit_template_name
            )
            st.session_state.pop(edit_widget_key, None)
            set_flash(
                f"已把“{edit_template_name}”模板载入实验#{assignment['id']}的编辑区；"
                "点击保存修改后才会生效。"
            )
            st.rerun()
        edit_rubric_seed = st.session_state.get(
            edit_seed_key,
            assignment.get("rubric") or db.DEFAULT_RUBRIC,
        )

        with st.form(f"edit_assignment_form_{assignment['id']}"):
            edit_title = st.text_input(
                "实验名称",
                value=assignment["title"],
            )
            edit_description = st.text_area(
                "实验描述",
                value=assignment["description"],
                height=100,
            )
            st.markdown("**OJ题面设置（学生可见）**")
            edit_input_format = st.text_area(
                "输入格式（可留空）",
                value=assignment.get("input_format", ""),
                height=90,
                max_chars=3000,
            )
            edit_output_format = st.text_area(
                "输出格式（可留空）",
                value=assignment.get("output_format", ""),
                height=90,
                max_chars=3000,
            )
            edit_constraints_text = st.text_area(
                "数据范围与说明（可留空）",
                value=assignment.get("constraints_text", ""),
                height=90,
                max_chars=3000,
            )
            st.markdown("**公开输入输出样例（学生可见，可留空）**")
            edit_public_samples = render_public_sample_editor(
                f"edit_assignment_public_samples_{assignment['id']}",
                assignment.get("public_samples", []),
            )
            edit_requirements_text = st.text_area(
                "实验要求（每行一条）",
                value="\n".join(assignment["requirements"]),
                height=130,
            )
            edit_focus_text = st.text_area(
                "隐藏教学重点（每行一条）",
                value="\n".join(assignment["teaching_focus"]),
                height=130,
            )
            st.markdown("**评分点与证据来源**")
            edit_rubric = render_rubric_editor(
                edit_widget_key,
                edit_rubric_seed,
            )
            st.markdown("**代码验证设置**")
            current_cpp_standard = assignment.get("cpp_standard", "auto")
            current_cpp_standard_label = next(
                label
                for label, value in CPP_STANDARD_OPTIONS.items()
                if value == current_cpp_standard
            )
            edit_cpp_standard_label = st.selectbox(
                "C++编译标准",
                list(CPP_STANDARD_OPTIONS),
                index=list(CPP_STANDARD_OPTIONS).index(current_cpp_standard_label),
                key=f"edit_cpp_standard_{assignment['id']}",
                help="历史提交保留提交时的编译标准；这里仅影响修改后的新提交。",
            )
            edit_cpp_standard = CPP_STANDARD_OPTIONS[edit_cpp_standard_label]
            edit_verification_enabled = st.checkbox(
                "启用C++编译与隐藏测试样例验证",
                value=bool(assignment.get("verification_enabled")),
                key=f"edit_verification_enabled_{assignment['id']}",
                help="关闭后新提交只进行AI静态分析；历史运行证据不会改变。",
            )
            edit_test_cases = render_test_case_editor(
                f"edit_assignment_test_cases_{assignment['id']}",
                assignment.get("test_cases", []),
            )
            st.caption(
                "启用后至少填写3组；修改分组和分值只影响新提交，历史提交仍保留"
                "原始测试点、权重和结果。"
            )
            edit_reference_code = st.text_area(
                "教师参考答案（仅教师可见）",
                value=assignment.get("reference_code", ""),
                placeholder="启用代码验证时必填；保存前会实际跑完隐藏测试和公开样例。",
                height=300,
                max_chars=12000,
                key=f"edit_reference_code_{assignment['id']}",
            )
            st.markdown("**学习支持与答辩准入**")
            edit_hint_limit = st.number_input(
                "每名学生可使用的AI提示次数",
                min_value=0,
                max_value=5,
                value=int(assignment.get("hint_limit", 2)),
                step=1,
                key=f"edit_hint_limit_{assignment['id']}",
                help="修改后影响本实验后续提示请求；已经使用的次数不会清零。",
            )
            current_admission = assignment.get("defense_admission", "全部通过")
            edit_defense_admission = st.selectbox(
                "答辩准入规则",
                DEFENSE_ADMISSION_OPTIONS,
                index=DEFENSE_ADMISSION_OPTIONS.index(current_admission),
                key=f"edit_defense_admission_{assignment['id']}",
            )
            edit_defense_threshold = st.number_input(
                "答辩准入分数（仅“达到指定分数”时生效）",
                min_value=0,
                max_value=100,
                value=int(assignment.get("defense_score_threshold", 100)),
                step=5,
                key=f"edit_defense_threshold_{assignment['id']}",
            )
            edit_starter_code = st.text_area(
                "起始代码（可留空）",
                value=assignment["starter_code"],
                height=230,
            )
            edit_quality_warnings = db.assignment_quality_warnings(
                edit_title,
                edit_rubric,
            )
            if edit_quality_warnings:
                original_quality_warnings = db.assignment_quality_warnings(
                    assignment.get("title", ""),
                    assignment.get("rubric", []),
                )
                for warning in edit_quality_warnings:
                    st.warning(warning)
                edit_quality_confirmed = st.checkbox(
                    "我已逐项核对，确认这些评分点确实适用于当前实验",
                    value=bool(
                        original_quality_warnings
                        and assignment.get("rubric_reviewed")
                    ),
                    key=f"edit_quality_confirmed_{assignment['id']}",
                )
            else:
                edit_quality_confirmed = True
            save_submitted = st.form_submit_button(
                "保存修改",
                type="primary",
                width="stretch",
            )

        if save_submitted:
            requirements = lines_to_list(edit_requirements_text)
            teaching_focus = lines_to_list(edit_focus_text)
            if assignment_form_is_valid(
                edit_title,
                edit_description,
                requirements,
                teaching_focus,
                edit_rubric,
                edit_verification_enabled,
                edit_test_cases,
                edit_public_samples,
                edit_reference_code,
                edit_quality_warnings,
                edit_quality_confirmed,
            ):
                try:
                    if edit_verification_enabled:
                        with st.spinner("正在用教师参考答案执行保存前自检……"):
                            preflight = reference_solution_preflight(
                                edit_reference_code,
                                edit_test_cases,
                                edit_public_samples,
                                edit_cpp_standard,
                            )
                        if not preflight["passed"]:
                            st.error(preflight["message"])
                            return
                    db.update_assignment(
                        assignment["id"],
                        edit_title.strip(),
                        edit_description.strip(),
                        requirements,
                        teaching_focus,
                        edit_starter_code,
                        rubric=edit_rubric,
                        verification_enabled=edit_verification_enabled,
                        test_cases=edit_test_cases,
                        input_format=edit_input_format,
                        output_format=edit_output_format,
                        constraints_text=edit_constraints_text,
                        public_samples=edit_public_samples,
                        cpp_standard=edit_cpp_standard,
                        hint_limit=edit_hint_limit,
                        defense_admission=edit_defense_admission,
                        defense_score_threshold=edit_defense_threshold,
                        reference_code=edit_reference_code,
                        rubric_reviewed=edit_quality_confirmed,
                    )
                except (ValueError, sqlite3.Error) as error:
                    st.error(f"修改实验失败：{error}")
                else:
                    set_flash(
                        f"实验#{assignment['id']}修改成功，历史提交快照没有改变。"
                    )
                    st.rerun()

        if assignment["status"] == "已发布":
            if st.button(
                "停用该实验",
                key=f"disable_assignment_{assignment['id']}",
            ):
                db.set_assignment_status(assignment["id"], "已停用")
                set_flash(
                    f"实验#{assignment['id']}“{assignment['title']}”已停用。"
                )
                st.rerun()
        else:
            if st.button(
                "重新发布该实验",
                type="primary",
                key=f"publish_assignment_{assignment['id']}",
                disabled=bool(existing_blockers),
            ):
                if assignment.get("verification_enabled"):
                    with st.spinner("正在用教师参考答案执行重新发布自检……"):
                        preflight = reference_solution_preflight(
                            assignment.get("reference_code", ""),
                            assignment.get("test_cases", []),
                            assignment.get("public_samples", []),
                            assignment.get("cpp_standard", "auto"),
                        )
                    if not preflight["passed"]:
                        st.error(preflight["message"])
                    else:
                        db.set_assignment_status(assignment["id"], "已发布")
                        set_flash(
                            f"实验#{assignment['id']}“{assignment['title']}”重新发布成功。"
                        )
                        st.rerun()
                else:
                    db.set_assignment_status(assignment["id"], "已发布")
                    set_flash(
                        f"实验#{assignment['id']}“{assignment['title']}”重新发布成功。"
                    )
                    st.rerun()

        st.caption("停用实验不会删除任务，也不会影响已有学生提交和诊断报告。")


def teacher_review_status(record):
    """把AI建议与教师处理结果转换成易读状态。"""
    if record.get("overall") is None:
        return "待完成答辩"
    if record.get("teacher_reviewed"):
        if record.get("teacher_decision") == "要求学生重新答辩":
            status_map = {
                "待开始": "待学生重答",
                "答辩中": "学生重答中",
                "已完成": "重答已完成",
                "已取消": "已取消重答",
            }
            return status_map.get(record.get("redefense_status"), "待学生重答")
        return "已复核"
    if int(record.get("attempt_number") or 1) > 1:
        return "待复核（重答）"
    if record.get("_needs_teacher_review") or record.get("review_required"):
        return "待复核"
    return "常规抽查"


def sync_teacher_score_to_ai(decision_key, score_key, ai_score):
    """切换到认可AI诊断时，立即把教师确认理解度同步为AI分数。"""
    if st.session_state.get(decision_key) == "认可AI诊断":
        st.session_state[score_key] = int(ai_score)


def qa_final_score(item):
    evaluation = item.get("final_evaluation") or item.get("initial_evaluation") or {}
    return evaluation.get("score")


def render_redefense_comparison(submission, report, qa_records):
    """教师查看重新答辩与上一次答辩的分数和证据变化。"""
    parent_id = submission.get("parent_submission_id")
    if not parent_id:
        return
    parent_submission = db.get_submission(parent_id)
    parent_report = db.get_report(parent_id)
    parent_qa_records = db.get_qa_records(parent_id)
    request = db.get_redefense_request_by_new_submission(submission["id"])

    st.subheader("重新答辩前后对比")
    if request:
        st.info(f"教师要求：{request['reason']}")
    if parent_submission:
        st.caption(
            f"提交#{parent_id}（第{parent_submission.get('attempt_number', 1)}次）"
            f" → 提交#{submission['id']}（第{submission.get('attempt_number', 2)}次）"
        )

    if parent_report and report:
        old_score = parent_report["overall"]
        new_score = report["overall"]
        col1, col2, col3 = st.columns(3)
        col1.metric("上次AI理解度", old_score)
        col2.metric("本次AI理解度", new_score)
        col3.metric("理解度变化", new_score - old_score)

        dimension_names = list(
            dict.fromkeys(
                list(parent_report["dimensions"]) + list(report["dimensions"])
            )
        )
        dimension_rows = []
        for name in dimension_names:
            old_value = parent_report["dimensions"].get(name)
            new_value = report["dimensions"].get(name)
            change = (
                new_value - old_value
                if old_value is not None and new_value is not None
                else "—"
            )
            dimension_rows.append(
                {
                    "能力维度": name,
                    "上次分数": old_value if old_value is not None else "—",
                    "本次分数": new_value if new_value is not None else "—",
                    "变化": change,
                }
            )
        render_centered_dataframe(pd.DataFrame(dimension_rows))
    elif not report:
        st.info("学生正在重新答辩，完成后将在此显示前后分数变化。")

    st.caption("分数变化仅供参考；两次题目与难度可能不同，不能据此直接认定进步。")
    point_rows = insights.comparable_points(parent_qa_records, qa_records)
    st.markdown("**按参考知识点核查变化**")
    st.caption("仅自动对应参考点文字一致（忽略空白）的知识点；未覆盖、低置信度或缺少评价均不认定问题已解决。")
    if point_rows:
        render_centered_dataframe(pd.DataFrame(point_rows))
    else:
        st.info("历史记录缺少可对应的参考知识点，请展开两次原文人工核查。")
    with st.expander("查看前后两次问题与回答", expanded=False):
        for label, questions in [("上一次", parent_qa_records), ("重新答辩", qa_records)]:
            st.markdown(f"**{label}完整问答**")
            for index, item in enumerate(questions, 1):
                st.write(f"第{index}题：{item.get('question', '')}")
                st.write("回答：", item.get("answer") or "尚未回答")
                if item.get("follow_up_question"):
                    st.write("追问：", item["follow_up_question"])
                    st.write("追问回答：", item.get("follow_up_answer") or "尚未回答")
                st.write("参考知识点：", "；".join(item.get("reference_points") or []))
                st.write("AI评价：", insights.evaluation(item).get("feedback") or "未记录")
                st.divider()


def build_teacher_dataframe(records):
    rows = []
    for record in records:
        rows.append(
            {
                "提交编号": record["id"],
                "答辩次数": f"第{record.get('attempt_number', 1)}次",
                "学号": record["student_id"],
                "姓名": record["name"],
                "实验任务": record.get("assignment_title", record["problem"]),
                "状态": record["status"],
                "代码验证": (
                    (
                        f"{record['code_verification'].get('overall_status', '未执行')} "
                        f"{record['code_verification'].get('passed', 0)}/"
                        f"{record['code_verification'].get('total', 0)}"
                    )
                    if record.get("code_verification")
                    else "未记录"
                ),
                "代码客观得分": (
                    (
                        f"{record['code_verification'].get('score', 0)}/"
                        f"{record['code_verification'].get('max_score', 0)}"
                    )
                    if record.get("code_verification", {}).get("max_score")
                    else "历史未计分"
                ),
                "评分点证据初评": (
                    record.get("completion_score")
                    if record.get("completion_score") is not None
                    else "历史记录"
                ),
                "答辩理解度": record["overall"] if record["overall"] is not None else "待生成",
                "相对较低维度（历史报告）": record["weakest"] if record["weakest"] else "未记录",
                "AI复核建议": (
                    "建议复核"
                    if record.get("review_required")
                    else "常规抽查"
                ),
                "人工复核状态": teacher_review_status(record),
                "教师结论": record.get("teacher_decision") or "—",
                "教师确认理解度": (
                    record.get("teacher_confirmed_overall")
                    if record.get("teacher_reviewed")
                    else "—"
                ),
                "学生反馈": (
                    f"待处理{record['pending_feedback_count']}条"
                    if record.get("pending_feedback_count")
                    else (
                        f"共{record['feedback_count']}条，已处理"
                        if record.get("feedback_count")
                        else "无"
                    )
                ),
                "提交时间": record["submitted_at"],
            }
        )
    return pd.DataFrame(rows)


def build_hidden_case_statistics(records):
    """按提交聚合隐藏测试点通过率，编译失败视为所有测试点未通过。"""
    statistics = {}
    ignored_statuses = {"未启用", "环境不可用", "编译环境不兼容", "未执行"}
    for record in records:
        verification = record.get("code_verification") or {}
        overall_status = verification.get("overall_status", "未执行")
        if not verification or overall_status in ignored_statuses:
            continue
        cases = verification.get("cases", [])
        if not cases and overall_status in {"编译失败", "编译超时"}:
            snapshot = record.get("assignment_snapshot") or {}
            cases = [
                {
                    **case,
                    "status": overall_status,
                    "score": 0,
                }
                for case in snapshot.get("test_cases", [])
                if isinstance(case, dict)
            ]
        for index, case in enumerate(cases, start=1):
            name = str(case.get("name") or f"测试点{index}")
            group = str(case.get("group") or "未分组")
            assignment_title = record.get("assignment_title", record.get("problem", ""))
            key = (assignment_title, group, name)
            item = statistics.setdefault(
                key,
                {
                    "实验任务": assignment_title,
                    "测试分组": group,
                    "测试点": name,
                    "分值": int(case.get("weight", 0) or 0),
                    "通过次数": 0,
                    "判定次数": 0,
                },
            )
            item["判定次数"] += 1
            item["通过次数"] += int(case.get("status") == "通过")

    rows = []
    for item in statistics.values():
        judged = item["判定次数"]
        rows.append(
            {
                **item,
                "通过率(%)": round(item["通过次数"] * 100 / judged) if judged else 0,
            }
        )
    rows.sort(key=lambda item: (item["通过率(%)"], item["实验任务"], item["测试点"]))
    return pd.DataFrame(rows)


def render_teacher_hint_records(records):
    """总览只显示短字段，长篇提示按记录展开。"""
    st.subheader("AI提示使用记录")
    if not records:
        st.info("当前筛选条件下还没有学生使用AI提示。")
        return
    rows = [
        {"学号": item.get("student_id", ""), "姓名": item.get("name") or "—",
         "实验任务": item.get("assignment_title", ""), "提示序号": item.get("hint_number", ""),
         "当时判题": item.get("verdict_status", ""),
         "排查方向摘要": shorten_teacher_text((item.get("hint") or {}).get("diagnosis"), 40),
         "使用时间": item.get("created_at", "")}
        for item in records
    ]
    st.caption(f"共{len(rows)}次提示；完整引导和自查问题按需展开。")
    render_centered_dataframe(pd.DataFrame(rows))
    for item in records:
        hint = item.get("hint") or {}
        with st.expander(
            f"提示详情 · {item.get('name') or item.get('student_id')} · "
            f"{item.get('assignment_title', '')} · 第{item.get('hint_number')}次 · "
            f"{item.get('created_at', '')}", expanded=False,
        ):
            st.caption(f"学号：{item.get('student_id')} · 代码版本：{str(item.get('code_digest', ''))[:8] or '历史未记录'}")
            st.write("排查方向：", hint.get("diagnosis", ""))
            st.write("引导内容：", hint.get("guidance", ""))
            for check in hint.get("self_check") or []:
                st.write(f"- {check}")


def compact_code_result(record):
    """把完整判题证据压缩成教师总览所需的短标签与分数。"""
    verification = record.get("code_verification") or {}
    if not verification:
        return "未记录", "—"

    status = verification.get("overall_status", "未执行")
    passed = int(verification.get("passed", 0) or 0)
    total = int(verification.get("total", 0) or 0)
    score = int(verification.get("score", 0) or 0)
    max_score = int(verification.get("max_score", 0) or 0)
    if status == "全部通过":
        verdict = "AC"
    elif status in {"编译失败", "编译超时"}:
        verdict = "CE"
    elif status in {"环境不可用", "编译环境不兼容"}:
        verdict = "提交时评测环境异常"
    elif status in {"未启用", "未执行"}:
        verdict = status
    else:
        failed_codes = [
            item.get("verdict_code")
            for item in verification.get("cases", [])
            if item.get("verdict_code") and item.get("verdict_code") != "AC"
        ]
        verdict = failed_codes[0] if failed_codes else status

    result_text = f"{verdict} · {passed}/{total}" if total else verdict
    score_text = f"{score}/{max_score}" if max_score else "—"
    return result_text, score_text


def teacher_record_needs_attention(record):
    """判断一条提交是否应进入教师默认待办视图。"""
    verification = record.get("code_verification") or {}
    code_status = verification.get("overall_status", "未执行")
    attempt_number = int(record.get("attempt_number") or 1)
    needs_review = bool(record.get("_needs_teacher_review")) or (
        record.get("status") == "已完成"
        and (record.get("review_required") or attempt_number > 1)
        and not record.get("teacher_reviewed")
    )
    return bool(
        record.get("pending_feedback_count")
        or needs_review
        or record.get("redefense_status") in {"待开始", "答辩中"}
        or (record.get("status") != "已完成" and not record.get("_defense_resolved_by"))
        or (code_status not in {"全部通过", "未启用", "未执行"} and not record.get("_code_resolved_by"))
    )


def teacher_attention_label(record):
    """用短标签说明教师为什么需要关注该学生。"""
    labels = []
    if record.get("pending_feedback_count"):
        labels.append(f"待处理反馈{record['pending_feedback_count']}条")
    review_status = teacher_review_status(record)
    if review_status not in {"已复核", "常规抽查"} and not (review_status == "待完成答辩" and record.get("_defense_resolved_by")):
        labels.append(review_status)
    if record.get("status") != "已完成" and not record.get("_defense_resolved_by") and "待完成答辩" not in labels:
        labels.append("答辩未完成")
    code_status = (record.get("code_verification") or {}).get(
        "overall_status",
        "未执行",
    )
    if record.get("_code_resolved_by") and code_status not in {"全部通过", "未启用", "未执行"}:
        labels.append(f"旧评测问题已有后续通过记录#{record['_code_resolved_by']}")
    elif code_status in {"环境不可用", "编译环境不兼容"}:
        labels.append("历史评测环境异常")
    elif code_status not in {"全部通过", "未启用", "未执行"}:
        labels.append("代码需关注")
    if not labels:
        labels.append("已处理" if record.get("teacher_reviewed") else "常规抽查")
    return " · ".join(dict.fromkeys(labels))


def teacher_record_priority(record):
    """待反馈、待复核和低理解度记录优先，随后按提交编号倒序。"""
    attempt_number = int(record.get("attempt_number") or 1)
    needs_review = (
        record.get("status") == "已完成"
        and (record.get("review_required") or attempt_number > 1)
        and not record.get("teacher_reviewed")
    )
    understanding = record.get("overall")
    code_status = (record.get("code_verification") or {}).get(
        "overall_status",
        "未执行",
    )
    judging_attention = (
        (code_status not in {"全部通过", "未启用", "未执行"} and not record.get("_code_resolved_by"))
        or (record.get("status") != "已完成" and not record.get("_defense_resolved_by"))
    )
    return (
        0 if record.get("pending_feedback_count") else 1,
        0 if needs_review else 1,
        0 if judging_attention else 1,
        0 if understanding is not None and understanding < 70 else 1,
        -int(record.get("id") or 0),
    )


def build_teacher_status_dataframe(records):
    """生成只保留教师决策字段的学生状态总览。"""
    rows = []
    for record in sorted(records, key=teacher_record_priority):
        code_result, code_score = compact_code_result(record)
        rows.append(
            {
                "提交": f"#{record['id']}",
                "学生": f"{record['name']} · {record['student_id']}",
                "实验": record.get("assignment_title", record.get("problem", "")),
                "代码结果": code_result,
                "代码得分": code_score,
                "评分点证据初评": (
                    record.get("completion_score")
                    if record.get("completion_score") is not None
                    else "—"
                ),
                "答辩理解度": (
                    record.get("overall")
                    if record.get("overall") is not None
                    else "待完成"
                ),
                "掌握证据": (record.get("_mastery") or {}).get("status", "待核查"),
                "关注状态": teacher_attention_label(record),
            }
        )
    return pd.DataFrame(rows)


def shorten_teacher_text(value, limit=72):
    """压缩摘要中的长文本，完整内容仍保留在证据区。"""
    cleaned = " ".join(str(value or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(1, limit - 1)].rstrip() + "…"


def build_teacher_digest(record, preliminary_review, report, qa_records, feedbacks):
    """仅使用已有结构化证据生成短摘要，不增加一次大模型调用。"""
    code_result, code_score = compact_code_result(record)
    preliminary_score = (
        preliminary_review.get("completion_score")
        if preliminary_review
        else None
    )
    understanding = report.get("overall") if report else None
    if record.get("teacher_reviewed"):
        teacher_result = (
            f"{record.get('teacher_decision') or '已复核'} · "
            f"{record.get('teacher_confirmed_overall')}/100"
        )
    else:
        teacher_result = "待复核"

    conclusion_parts = [f"代码 {code_result}（{code_score}）"]
    conclusion_parts.append(
        f"评分点证据初评 {preliminary_score}/100"
        if preliminary_score is not None
        else "评分点证据初评暂无"
    )
    conclusion_parts.append(
        f"答辩理解度 {understanding}/100"
        if understanding is not None
        else "答辩尚未完成"
    )
    conclusion_parts.append(f"教师结论 {teacher_result}")

    unique_concerns = insights.ranked_concerns(record, preliminary_review, report, qa_records)
    pending_feedbacks = [item for item in feedbacks if item.get("status") == "待处理"]

    actions = []
    if pending_feedbacks:
        actions.append("先处理学生待回复反馈")
    if report is None:
        actions.append("等待学生完成答辩后再作理解度复核")
    elif not record.get("teacher_reviewed"):
        actions.append("核对关键证据并保存教师复核结论")
    if record.get("redefense_status") in {"待开始", "答辩中"}:
        actions.append("跟进重新答辩进度")
    if not actions:
        actions.append("该记录已处理，按需查看完整证据")

    evidence_sources = []
    if record.get("code_verification"):
        evidence_sources.append("代码判题")
    if preliminary_review:
        evidence_sources.append("评分点证据初评")
    if qa_records:
        answered = sum(bool(item.get("answer")) for item in qa_records)
        evidence_sources.append(f"答辩问答{answered}/{len(qa_records)}")
    if feedbacks:
        evidence_sources.append(f"学生反馈{len(feedbacks)}条")
    return {
        "conclusion": "；".join(conclusion_parts) + "。",
        "concerns": unique_concerns,
        "action": "；".join(actions) + "。",
        "evidence": "、".join(evidence_sources) or "基础提交信息",
        "teacher_result": teacher_result,
    }


def render_teacher_digest(record, preliminary_review, report, qa_records, feedbacks):
    """默认只向教师展示可以快速作出判断的精简摘要。"""
    digest = build_teacher_digest(
        record,
        preliminary_review,
        report,
        qa_records,
        feedbacks,
    )
    with st.container(border=True):
        st.subheader("教师诊断摘要")
        st.write(digest["conclusion"])
        st.markdown(teacher_summary_cards_html(record, report), unsafe_allow_html=True)
        st.markdown("**重点关注（最多3项）**")
        for item in digest["concerns"]:
            st.write(f"- {item}")
        st.info("建议处理：" + digest["action"])
        st.caption("摘要证据来源：" + digest["evidence"] + "。完整原文默认折叠。")


def render_teacher_mastery(report, qa_records):
    """掌握情况与逐题核查入口紧邻复核表单。"""
    st.subheader("知识掌握与关键证据")
    mastery = insights.mastery_summary(report, qa_records)
    st.write(f"{mastery['status']}：{mastery['text']}")
    if report and report.get("dimensions"):
        render_centered_dataframe(pd.DataFrame([
            {"能力维度": name, "AI理解度": f"{score}/100",
             "关注": "相对较低（不等于未掌握）" if name == mastery.get("dimension") else "见逐题证据"}
            for name, score in report["dimensions"].items()
        ]))
    else:
        st.info("完整理解度诊断尚未生成，可先核查已有回答。")
    st.caption("分数是AI判断；结合逐题回答与遗漏点核查，未回答不等同于未掌握。")
    if not qa_records:
        st.info("该提交暂无答辩问答。")
    for index, item in enumerate(qa_records, start=1):
        evaluation = item.get("final_evaluation") or item.get("initial_evaluation") or {}
        score = evaluation.get("score", "待评价") if item.get("answer") else "待回答"
        confidence = evaluation.get("confidence") or "未记录"
        st.markdown(f"**第{index}题 · {item.get('dimension', '综合理解')} · {score}**")
        _, status, detail = insights.question_finding(item, index)
        finding_text = (
            f"**{status}**｜证据置信度：{confidence}\n\n{detail}"
        )
        if status == "需要巩固":
            st.error(finding_text)
        elif status in {"证据不足", "待核查"}:
            st.warning(finding_text)
        else:
            st.info(finding_text)
        with st.expander(
            f"核查第{index}题 · {status} · {score}",
            expanded=False,
        ):
            st.info(f"**答辩问题**\n\n{item.get('question', '')}")
            if item.get("question_reason"):
                st.warning(
                    f"**为什么问这题**\n\n{item['question_reason']}"
                )
            with st.container(border=True):
                st.markdown("#### 学生实际回答")
                st.markdown(item.get("answer") or "尚未回答")
            if item.get("follow_up_question"):
                with st.container(border=True):
                    st.markdown("#### 追问与补充回答")
                    st.markdown(f"**追问**\n\n{item['follow_up_question']}")
                    st.markdown(
                        f"**学生补充回答**\n\n"
                        f"{item.get('follow_up_answer') or '尚未回答'}"
                    )
            for label, field in [("首次评价", "initial_evaluation"), ("最终评价", "final_evaluation")]:
                evidence = item.get(field) or {}
                if not evidence:
                    continue
                with st.container(border=True):
                    st.markdown(f"#### {label}与证据结论")
                    st.markdown(
                        f"**评价依据**\n\n{evidence.get('feedback', '')}"
                    )
                    st.caption(f"评分：{evidence.get('score', '—')} · 等级：{evidence.get('score_level', '—')} · 置信度：{evidence.get('confidence', '—')}")
                    for title, key in [("遗漏点", "missing_points"), ("仍存误解", "misconceptions")]:
                        if evidence.get(key):
                            st.write(title + "：", "；".join(evidence[key]))
                    for point in evidence.get("point_assessments", []):
                        st.write(f"- {point.get('status', '')}｜{point.get('reference_point', '')}｜证据：{point.get('evidence', '')}")
            with st.container(border=True):
                st.markdown("#### 教师参考要点")
                for point in item.get("reference_points", []):
                    st.write(f"- {point}")


def render_teacher_full_evidence(submission, preliminary_review, report, qa_records):
    """长篇材料分类折叠，逐题回答由掌握证据区提供。"""
    st.subheader("其他原始材料（按需展开）")
    with st.expander("查看学生代码、解题思路与实验报告", expanded=False):
        st.write("解题思路：", submission["explanation"])
        st.write("选做实验报告：", submission.get("lab_report") or "学生未填写（不因此扣分）")
        st.code(submission["code"], language="cpp")
    with st.expander("查看代码判题与评分点初评原文", expanded=False):
        render_code_verification(submission.get("code_verification"), teacher_view=True)
        render_preliminary_review(preliminary_review)
    if report:
        with st.expander("查看AI完整诊断原文", expanded=False):
            st.write("诊断总结：", report.get("summary", ""))
            st.write("学习建议：", report.get("suggestion", ""))
            if report.get("review_reasons"):
                st.write("建议复核原因：", "；".join(report["review_reasons"]))
    snapshot = submission.get("assignment_snapshot") or {}
    if snapshot:
        with st.expander("查看提交时的实验任务快照", expanded=False):
            st.write("实验名称：", snapshot.get("title", ""))
            st.write("实验描述：", snapshot.get("description", ""))
            render_oj_statement(snapshot, f"teacher_snapshot_{submission['id']}")
            for requirement in snapshot.get("requirements", []):
                st.write(f"- {requirement}")
            for criterion in snapshot.get("rubric", []):
                st.write(f"- {criterion['name']}｜{criterion['weight']}分｜{criterion['source']}｜{criterion['description']}")
            st.write("隐藏教学重点：", "；".join(snapshot.get("teaching_focus", [])))
            st.write("C++编译标准：", verifier.cpp_standard_label(snapshot.get("cpp_standard", "auto")))
    if submission.get("parent_submission_id"):
        with st.expander("查看重新答辩前后对比", expanded=False):
            render_redefense_comparison(submission, report, qa_records)


def render_teacher_student_case(record):
    """待办和班级分析共用同一份学生详情与处理入口。"""
    selected_id = record["id"]
    submission = db.get_submission(selected_id)
    qa_records = db.get_qa_records(selected_id)
    report = db.get_report(selected_id)
    preliminary_review = db.get_preliminary_review(selected_id)
    review_history = db.list_teacher_review_history(selected_id)
    student_feedbacks = db.list_student_feedbacks(submission_id=selected_id)
    st.subheader(f"当前学生 · {submission['name']} · {submission['student_id']}")
    st.caption(f"{record.get('assignment_title', submission['problem'])} · 提交#{selected_id} · 第{submission.get('attempt_number', 1)}次答辩 · {submission['submitted_at']}")
    render_teacher_digest(record, preliminary_review, report, qa_records, student_feedbacks)
    evidence_col, review_col = st.columns([3, 2], gap="large")
    with evidence_col:
        render_teacher_mastery(report, qa_records)
    with review_col:
        with st.container(border=True):
            render_teacher_review_actions(selected_id, report, review_history)
    render_teacher_feedback_actions(student_feedbacks)
    render_teacher_full_evidence(submission, preliminary_review, report, qa_records)
    st.caption("AI诊断用于辅助核查，教师应结合学生回答与原始证据作出结论。")


def render_teacher_review_actions(selected_id, report, review_history):
    """在当前学生下方直接完成教师复核。"""
    st.subheader("完成教师复核")
    if not report:
        st.info("学生完成答辩并生成AI报告后，才能保存教师复核结论。")
        return

    latest_review = review_history[0] if review_history else None
    if latest_review:
        st.success(
            f"最新结论：{latest_review['decision']} · "
            f"教师确认理解度{latest_review['confirmed_overall']}/100"
        )
        review_meta = (
            f"{latest_review['reviewer_name']} · {latest_review['reviewed_at']}"
        )
        if latest_review.get("comment"):
            review_meta = (
                f"{shorten_teacher_text(latest_review['comment'], 140)}　"
                + review_meta
            )
        else:
            review_meta = "未填写文字意见　" + review_meta
        st.caption(review_meta)
    elif report.get("review_required"):
        st.warning("该记录由AI建议人工复核，目前尚未处理。")
    else:
        st.info("该记录可进行常规抽查。")

    decisions = [
        "认可AI诊断",
        "调整理解度结论",
        "要求学生重新答辩",
    ]
    default_decision = (
        latest_review["decision"]
        if latest_review and latest_review["decision"] in decisions
        else decisions[0]
    )
    default_score = (
        latest_review["confirmed_overall"]
        if latest_review
        else report["overall"]
    )
    decision_key = f"teacher_review_decision_{selected_id}"
    score_key = f"teacher_review_score_{selected_id}"
    if decision_key not in st.session_state:
        st.session_state[decision_key] = default_decision
    if score_key not in st.session_state:
        st.session_state[score_key] = int(default_score)
    if st.session_state[decision_key] == "认可AI诊断":
        st.session_state[score_key] = int(report["overall"])

    decision = st.selectbox(
        "处理结论",
        decisions,
        key=decision_key,
        on_change=sync_teacher_score_to_ai,
        args=(decision_key, score_key, report["overall"]),
    )
    if decision == "认可AI诊断":
        st.caption(
            f"教师确认理解度自动同步为AI答辩理解度{report['overall']}分。"
        )
    elif decision == "要求学生重新答辩":
        st.caption("复核意见将成为下一次答辩的重点。")

    with st.form(f"teacher_workspace_review_form_{selected_id}"):
        reviewer_name = st.text_input(
            "复核教师",
            value=latest_review["reviewer_name"] if latest_review else "任课教师",
        )
        confirmed_overall = st.number_input(
            "教师确认理解度（0—100，非课程成绩）",
            min_value=0,
            max_value=100,
            step=1,
            key=score_key,
            disabled=decision == "认可AI诊断",
        )
        teacher_comment = st.text_area(
            "教师复核意见（选填）",
            value=latest_review["comment"] if latest_review else "",
            placeholder="如需，可补充认可、调整或重新答辩的依据。",
            height=100,
        )
        submitted = st.form_submit_button(
            "保存教师复核",
            type="primary",
            width="stretch",
            disabled=demo_mode.demo_read_only(),
        )

    if demo_mode.demo_read_only():
        st.caption("在线Demo不写入复核结果；完整本地版可保存教师结论。")

    if submitted:
        try:
            db.save_teacher_review(
                selected_id,
                reviewer_name,
                decision,
                confirmed_overall,
                teacher_comment,
                datetime.now().strftime("%Y-%m-%d %H:%M"),
            )
        except (ValueError, sqlite3.Error) as error:
            st.error(f"保存复核失败：{error}")
        else:
            set_flash(f"提交#{selected_id}的教师复核已保存。")
            st.rerun()

    if len(review_history) > 1:
        with st.expander(f"历史复核记录（{len(review_history)}条）"):
            for index, item in enumerate(review_history, start=1):
                st.markdown(
                    f"**第{index}条 · {item['decision']} · "
                    f"{item['confirmed_overall']}/100**"
                )
                if item.get("comment"):
                    st.write(item["comment"])
                else:
                    st.caption("本次未填写文字意见。")
                st.caption(f"{item['reviewer_name']} · {item['reviewed_at']}")


def render_teacher_feedback_actions(student_feedbacks):
    """在当前学生下方处理反馈，默认不展开长内容。"""
    st.subheader("处理学生反馈")
    if not student_feedbacks:
        st.info("该提交目前没有学生反馈。")
        return

    pending_count = sum(item["status"] == "待处理" for item in student_feedbacks)
    st.caption(
        f"共{len(student_feedbacks)}条反馈，待处理{pending_count}条；"
        "点击对应反馈后才显示完整内容。"
    )
    forced_feedback_id = st.session_state.get("teacher_task_feedback_id")
    for item in student_feedbacks:
        request_text = "希望回复" if item["reply_requested"] else "无需回复"
        with st.expander(
            f"反馈#{item['id']} · {item['category']} · {item['status']} · {request_text}",
            expanded=item["id"] == forced_feedback_id,
        ):
            st.write(item["content"])
            st.caption(f"提交时间：{item['created_at']}")
            if item["status"] == "已回复" and item["teacher_reply"]:
                st.success(f"当前回复：{item['teacher_reply']}")
            elif item["status"] == "已阅":
                st.info("该反馈已标记为教师已阅。")

            action_options = ["已回复", "已阅"]
            default_action = (
                item["status"]
                if item["status"] in action_options
                else ("已回复" if item["reply_requested"] else "已阅")
            )
            with st.form(f"teacher_workspace_feedback_form_{item['id']}"):
                feedback_teacher = st.text_input(
                    "处理教师",
                    value=item["replied_by"] or "任课教师",
                    key=f"workspace_feedback_teacher_{item['id']}",
                )
                feedback_action = st.selectbox(
                    "处理方式",
                    action_options,
                    index=action_options.index(default_action),
                    key=f"workspace_feedback_action_{item['id']}",
                )
                feedback_reply = st.text_area(
                    "教师回复（选择“已回复”时必填）",
                    value=item["teacher_reply"],
                    placeholder="请给出简洁、可执行的回复。",
                    height=90,
                    key=f"workspace_feedback_reply_{item['id']}",
                )
                processed = st.form_submit_button(
                    "保存反馈处理结果",
                    type="primary",
                    width="stretch",
                    disabled=demo_mode.demo_read_only(),
                )

            if demo_mode.demo_read_only():
                st.caption("在线Demo不修改反馈状态。")

            if processed:
                try:
                    db.process_student_feedback(
                        item["id"],
                        feedback_teacher,
                        feedback_action,
                        feedback_reply,
                        datetime.now().strftime("%Y-%m-%d %H:%M"),
                    )
                except (ValueError, sqlite3.Error) as error:
                    st.error(f"保存反馈处理失败：{error}")
                else:
                    set_flash(f"反馈#{item['id']}已更新为“{feedback_action}”。")
                    st.rerun()


def render_teacher_case_workspace(records, selected_assignment_id, show_queue=True):
    """教师端默认主线：看待办、选学生、读摘要并直接处理。"""
    records = insights.mark_superseded_records(records)
    if show_queue:
        pending_tasks = db.list_teacher_tasks(assignment_id=selected_assignment_id)
        pending_review_count = sum(
            item.get("task_type") == "人工复核"
            and item.get("status") == "待处理"
            for item in pending_tasks
        )
        pending_feedback_count = sum(
            item.get("task_type") == "学生反馈"
            and item.get("status") == "待处理"
            for item in pending_tasks
        )
        judging_attention_count = sum(
            (record.get("code_verification") or {}).get("overall_status", "未执行")
            not in {"全部通过", "未启用", "未执行"}
            and not record.get("_code_resolved_by")
            for record in records
        )

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("待处理任务", len(pending_tasks))
        col2.metric("评测需关注", judging_attention_count)
        col3.metric("待教师复核", pending_review_count)
        col4.metric("待处理反馈", pending_feedback_count)
        st.caption(
            "评分口径：代码客观得分=隐藏测试；评分点证据初评=代码、思路与选做报告；"
            "答辩理解度=学生问答；教师确认理解度=人工复核。四项结果互不相加。"
        )

        if pending_tasks:
            with st.expander(f"待办队列（{len(pending_tasks)}项）"):
                render_task_list(pending_tasks, "教师待办")

    if any(item["id"] == st.session_state.get("teacher_task_submission_id") for item in records):
        # 定位后保持目标可见，后续保存反馈时不会被旧筛选条件切走。
        st.session_state.teacher_record_filter = "全部记录"
    st.caption("后续同学生、同实验的成功判题或已完成答辩会解除旧技术待办；未处理反馈、人工复核和教师重答要求继续保留。全部记录可查历史。")
    filter_options = ["需要处理", "全部记录", "待教师复核", "有学生反馈", "评测需关注"]
    record_filter = st.selectbox(
        "筛选学生状态",
        filter_options,
        key="teacher_record_filter",
    )
    if record_filter == "需要处理":
        visible_records = [record for record in records if teacher_record_needs_attention(record)]
    elif record_filter == "待教师复核":
        visible_records = [
            record
            for record in records
            if teacher_review_status(record) in {"待复核", "待复核（重答）", "重答已完成"}
        ]
    elif record_filter == "有学生反馈":
        visible_records = [record for record in records if record.get("feedback_count")]
    elif record_filter == "评测需关注":
        visible_records = [
            record
            for record in records
            if (record.get("code_verification") or {}).get("overall_status", "未执行")
            not in {"全部通过", "未启用", "未执行"}
            and not record.get("_code_resolved_by")
        ]
    else:
        visible_records = list(records)

    if not visible_records:
        st.info("当前筛选条件下没有学生记录，请调整筛选。")
        return
    visible_records = sorted(visible_records, key=teacher_record_priority)
    option_map = {
        (
            f"提交#{record['id']} · {record['name']} · {record['student_id']} · "
            f"{teacher_attention_label(record)}"
        ): record["id"]
        for record in visible_records
    }
    forced_submission_id = st.session_state.get("teacher_task_submission_id")
    if forced_submission_id not in option_map.values():
        forced_submission_id = None
    labels = list(option_map)
    if st.session_state.get("teacher_record_selector") not in labels:
        previous_id = st.session_state.get("teacher_selected_submission_id")
        st.session_state.teacher_record_selector = next(
            (label for label, submission_id in option_map.items() if submission_id == previous_id),
            labels[0],
        )
        if previous_id is not None and previous_id not in option_map.values() and forced_submission_id is None:
            st.info("原选中记录已不在当前筛选范围，已选择首条记录。")
    if forced_submission_id is not None:
        forced_label = next(
            label for label, submission_id in option_map.items()
            if submission_id == forced_submission_id
        )
        st.session_state.teacher_record_selector = forced_label
        st.info(f"已从待办定位到提交#{forced_submission_id}。")
    selected_label = st.selectbox(
        "选择要处理的学生",
        labels,
        key="teacher_record_selector",
    )
    selected_id = option_map[selected_label]
    st.session_state.teacher_selected_submission_id = selected_id
    record = next(item for item in records if item["id"] == selected_id)
    st.subheader("学生状态总览")
    table_records = [record] if show_queue else visible_records
    status_table = build_teacher_status_dataframe(table_records)
    selected_rows = tuple(
        index for index, value in enumerate(status_table["提交"])
        if value == f"#{selected_id}"
    )
    render_centered_dataframe(status_table, highlighted_rows=selected_rows)
    st.caption(
        "仅展示当前选中提交。" if show_queue
        else f"显示当前筛选范围全部{len(table_records)}条提交，蓝色行为选中记录#{selected_id}。"
    )
    st.caption("代码结果是该次提交保存的评测结果；当前运行环境以侧栏为准。")
    render_teacher_student_case(record)
    if forced_submission_id is not None:
        # 待办只负责一次定位；处理者随后仍可自由切换其他学生。
        st.session_state.teacher_task_submission_id = None
        st.session_state.teacher_task_feedback_id = None
        st.session_state.teacher_task_assignment_id = None


def teacher_assignment_key(record):
    """按实验ID分组；旧记录无ID时使用原题名，避免同名新实验混在一起。"""
    if record.get("assignment_id") is not None:
        return ("assignment", record["assignment_id"])
    return ("legacy", record.get("problem") or record.get("assignment_title") or "未命名实验")


def latest_completed_teacher_records(records):
    """每名学生每个实验仅取提交编号最大的已完成答辩，不改动历史记录。"""
    latest = {}
    for record in records:
        if record.get("status") != "已完成" or record.get("overall") is None:
            continue
        student = str(record.get("student_id") or "").strip()
        student_key = ("student", student) if student else ("submission", record["id"])
        key = (student_key, teacher_assignment_key(record))
        if key not in latest or int(record["id"]) > int(latest[key]["id"]):
            latest[key] = record
    return sorted(latest.values(), key=lambda item: int(item["id"]))


def show_teacher_dashboard():
    page_header(
        "教师工作台",
        "按待办选择学生，先看精简诊断，再按需核查证据并完成处理。",
    )
    show_flash()

    assignments = db.list_assignments()
    filter_map = {"全部实验": None}
    for assignment in assignments:
        filter_map[
            f"实验#{assignment['id']} · {assignment['title']}"
        ] = assignment["id"]
    forced_assignment_id = st.session_state.get("teacher_task_assignment_id")
    if forced_assignment_id is not None:
        matching_label = next(
            (
                label
                for label, assignment_id in filter_map.items()
                if assignment_id == forced_assignment_id
            ),
            "全部实验",
        )
        st.session_state.teacher_assignment_filter = matching_label
    selected_filter = st.selectbox(
        "筛选实验任务",
        list(filter_map),
        key="teacher_assignment_filter",
    )
    selected_assignment_id = filter_map[selected_filter]
    records = insights.mark_superseded_records(db.list_submissions(selected_assignment_id))
    for record in records:
        record["_mastery"] = insights.mastery_summary(db.get_report(record["id"]), db.get_qa_records(record["id"]))
        record["_needs_teacher_review"] = insights.needs_teacher_review(
            record,
            mastery=record["_mastery"],
        )
    if not records:
        st.info("当前筛选条件下还没有进入AI答辩的学生提交。")
        return

    workspace_view = st.radio(
        "工作区域",
        ["待办处理", "班级分析"],
        horizontal=True,
        key="teacher_workspace_view",
    )
    if workspace_view == "待办处理":
        render_teacher_case_workspace(records, selected_assignment_id)
        return

    st.caption("先看班级趋势，再选择学生；证据核查、复核与反馈可在本页完成。")

    teacher_df = build_teacher_dataframe(records)
    completed = latest_completed_teacher_records(records)
    st.caption(
        f"掌握统计口径：每名学生在每个实验仅取最新一次已完成答辩（按提交编号），共{len(completed)}份。"
        "重答未完成时沿用上一次已完成结果；无已完成答辩者不计入均分。"
        "全部实验按学生与实验组合计数，同一学生参与不同实验分别计入。"
    )
    scores = [record["overall"] for record in completed if record["overall"] is not None]
    completion_scores = [
        record["completion_score"]
        for record in completed
        if record.get("completion_score") is not None
    ]
    code_score_percentages = [
        record["code_verification"].get("score", 0)
        * 100
        / record["code_verification"]["max_score"]
        for record in completed
        if record.get("code_verification", {}).get("max_score")
        and record["code_verification"].get("overall_status")
        not in {"环境不可用", "编译环境不兼容", "未执行", "未启用"}
    ]
    teacher_tasks = db.list_teacher_tasks(assignment_id=selected_assignment_id)
    pending_review_count = sum(
        item.get("task_type") == "人工复核"
        and item.get("status") == "待处理"
        for item in teacher_tasks
    )
    feedback_records = db.list_student_feedbacks(
        assignment_id=selected_assignment_id
    )
    pending_feedback_count = sum(
        item["status"] == "待处理" for item in feedback_records
    )
    average_score = round(sum(scores) / len(scores)) if scores else 0
    average_completion = (
        round(sum(completion_scores) / len(completion_scores))
        if completion_scores
        else 0
    )
    average_code_score = (
        round(sum(code_score_percentages) / len(code_score_percentages))
        if code_score_percentages
        else 0
    )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("历史提交总数", len(records))
    col2.metric("纳入统计的已完成答辩", len(completed))
    col3.metric(
        "平均代码得分",
        f"{average_code_score}/100" if code_score_percentages else "暂无",
    )
    col4.metric("平均评分点初评", average_completion if completion_scores else "暂无")
    col5, col6, col7 = st.columns(3)
    col5.metric("平均理解度", average_score if scores else "暂无")
    col6.metric("待人工复核", pending_review_count)
    col7.metric("待处理反馈", pending_feedback_count)

    st.caption("历史提交总数、待复核及待反馈按全部历史记录计算；三个均分使用上述去重样本。")
    csv_data = teacher_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "导出当前筛选记录（CSV）",
        data=csv_data,
        file_name="wuma_ai_teacher_records.csv",
        mime="text/csv",
    )

    completed_ids = {record["id"] for record in completed}
    learning_records = [
        record for record in db.list_learning_records(assignment_id=selected_assignment_id)
        if record["id"] in completed_ids
    ]
    left, right = st.columns(2)
    with left:
        st.subheader("班级四维平均")
        dimension_averages = calculate_dimension_averages(learning_records)
        if dimension_averages:
            st.markdown(
                teacher_bar_rows_html(build_teacher_dimension_rows(dimension_averages), "统一量尺 0—100 分 · 每项直接显示平均分"),
                unsafe_allow_html=True,
            )
        else:
            st.info("完成答辩后显示四维平均分。")
    with right:
        st.subheader("掌握证据分布")
        weakness_rows = build_teacher_weakness_rows(completed, selected_assignment_id is not None)
        if weakness_rows:
            unit_note = "人数" if selected_assignment_id is not None else "学生—实验人次"
            st.markdown(
                teacher_bar_rows_html(weakness_rows, f"按{unit_note}从多到少 · 条长为占全部统计样本的比例"),
                unsafe_allow_html=True,
            )
            st.caption("具体错误或遗漏记为需要巩固；缺少直接评价或置信度低记为证据不足；均分较高且无问题证据时，仅标相对较弱。分类供教师核查。")
        else:
            st.info("当前还没有已完成答辩的掌握证据。")

    with st.expander("测试点与实验统计", expanded=False):
        if filter_map[selected_filter] is None:
            st.subheader("各实验提交情况")
            assignment_groups = {}
            for record in records:
                assignment_groups.setdefault(teacher_assignment_key(record), []).append(record)
            assignment_summary = []
            for key, assignment_records in assignment_groups.items():
                assignment_completed = [record for record in completed if teacher_assignment_key(record) == key]
                task_scores = [record["overall"] for record in assignment_completed]
                task_completion_scores = [
                    record["completion_score"] for record in assignment_completed
                    if record.get("completion_score") is not None
                ]
                title = assignment_records[0].get("assignment_title", "未命名实验")
                display_title = f"实验#{key[1]} · {title}" if key[0] == "assignment" else f"历史实验 · {title}"
                assignment_summary.append({
                    "实验任务": display_title,
                    "历史提交数": len(assignment_records),
                    "纳入统计的学生数": len(assignment_completed),
                    "平均评分点初评": round(sum(task_completion_scores) / len(task_completion_scores)) if task_completion_scores else "暂无",
                    "平均答辩理解度": round(sum(task_scores) / len(task_scores)) if task_scores else "暂无",
                })
            render_centered_dataframe(pd.DataFrame(assignment_summary))

        st.subheader("隐藏测试点通过率")
        hidden_case_statistics = build_hidden_case_statistics(records)
        if not hidden_case_statistics.empty:
            st.caption(
                "此处按全部历史提交的判题记录统计，不使用掌握统计的去重样本。"
                "按通过率从低到高排列；编译失败计为对应测试点未通过，"
                "执行环境不可用不纳入统计。"
            )
            st.markdown(
                teacher_bar_rows_html(
                    build_teacher_hidden_case_rows(hidden_case_statistics, selected_assignment_id is None),
                    "统一量尺 0—100% · 绿色：全通过　橙色：部分通过　红色：未通过",
                ),
                unsafe_allow_html=True,
            )
            if len(hidden_case_statistics) > 12:
                st.caption(f"先展示通过率最低的12项，共{len(hidden_case_statistics)}项；其余见下方明细。")
            if any(hidden_case_statistics["判定次数"] < 3):
                st.caption("部分测试点仅有1—2次判定，100%也不代表多数学生已掌握，请结合判定次数阅读。")
            with st.expander(f"查看全部测试点明细（{len(hidden_case_statistics)}项）", expanded=False):
                render_centered_dataframe(hidden_case_statistics)
        else:
            st.info("完成启用了隐藏测试的代码判定后，这里将显示各测试点通过率。")

    with st.expander("AI提示用量与引导记录", expanded=False):
        render_teacher_hint_records(db.list_ai_hints(assignment_id=selected_assignment_id))

    with st.expander(f"学生反馈队列（待处理{pending_feedback_count}条）", expanded=False):
        feedback_status_filter = st.selectbox(
            "筛选反馈状态", ["全部", "待处理", "已阅", "已回复"],
            key="teacher_feedback_status_filter",
        )
        visible_feedbacks = [item for item in feedback_records
                             if feedback_status_filter == "全部" or item["status"] == feedback_status_filter]
        if visible_feedbacks:
            render_centered_dataframe(pd.DataFrame([
                {"反馈编号": item["id"], "姓名": item["name"], "学号": item["student_id"],
                 "提交编号": item["submission_id"], "类型": item["category"], "状态": item["status"]}
                for item in visible_feedbacks
            ]))
            jump_map = {f"反馈#{item['id']} · {item['name']} · 提交#{item['submission_id']}": item
                        for item in visible_feedbacks}
            jump_label = st.selectbox("从反馈队列快速定位", list(jump_map), key="teacher_feedback_jump")
            if st.button("定位并处理反馈"):
                item = jump_map[jump_label]
                st.session_state.teacher_task_submission_id = item["submission_id"]
                st.session_state.teacher_task_feedback_id = item["id"]
                st.rerun()
        else:
            st.info("当前反馈状态下没有记录。")

    render_teacher_case_workspace(records, selected_assignment_id, show_queue=False)


def show_sidebar():
    with st.sidebar:
        render_brand_lockup(compact=True)
        st.caption("v1.3.16 · 浏览器在线Demo版")
        st.caption("不止评代码，更要评理解")
        st.caption("已内置 LearnBuddy 技能与只读连接器适配包")
        if demo_mode.demo_mode_enabled():
            st.success("赛事在线Demo\n\n合成数据 · 安全浏览")
        if st.session_state.auth_role == "学生":
            st.info(
                f"学生：{st.session_state.auth_name}\n\n"
                f"学号：{logged_in_student_id()}"
            )
        else:
            st.info("当前身份：教师")
        if st.button("退出登录", width="stretch"):
            logout()
        st.divider()

        if st.session_state.auth_role == "学生":
            pending_task_count = len(
                db.list_student_tasks(logged_in_student_id())
            )
        else:
            pending_task_count = len(db.list_teacher_tasks())
        for page in pages_for_role(st.session_state.auth_role):
            button_type = "primary" if page == st.session_state.current_page else "secondary"
            task_page = (
                page == "任务中心"
                if st.session_state.auth_role == "学生"
                else page == "教师工作台"
            )
            label = (
                f"{page}（{pending_task_count}）"
                if task_page and pending_task_count
                else page
            )
            if st.button(label, type=button_type, width="stretch", key=f"nav_{page}"):
                navigate(page)

        st.divider()
        if st.session_state.auth_role == "教师":
            if llm.is_configured():
                if llm.is_tencent_provider():
                    st.success(
                        f"悟码AI网页智能服务：腾讯混元 / {llm.model_name()}"
                    )
                else:
                    st.warning(
                        "悟码AI网页当前仍在使用历史兼容配置。"
                        "赛事展示前请切换为腾讯混元。"
                    )
                if st.button("测试智能服务", width="stretch"):
                    with st.spinner("正在连接智能服务……"):
                        try:
                            connected = llm.test_connection()
                            if connected:
                                st.success("智能服务连接测试成功。")
                            else:
                                st.warning("API可以响应，但测试内容不符合预期。")
                        except llm.LLMServiceError as error:
                            st.error(str(error))
            else:
                st.error("悟码AI网页侧腾讯混元密钥未配置")

            st.caption(
                "LearnBuddy通过Skill和MCP读取悟码AI诊断，"
                "不调用所谓的“LearnBuddy模型API”。"
            )

            runtime = verifier.runtime_status()
            if runtime["available"]:
                st.success(runtime["label"])
                st.caption(runtime["detail"])
            else:
                st.caption(f"代码验证：{runtime['label']}；{runtime['detail']}")
            st.caption(f"数据库已有 {len(db.list_submissions())} 条提交记录")
        elif st.session_state.submission_id is None:
            st.info("当前会话：尚未提交实验")
        elif st.session_state.defense_completed:
            st.success("当前会话：答辩已完成")
        else:
            st.warning("当前会话：答辩进行中")


def add_styles():
    st.markdown(
        """
        <style>
        .stApp {
            background: linear-gradient(180deg, #f7fbff 0%, #ffffff 45%);
        }
        .hero {
            padding: 1.5rem 1.8rem;
            border-radius: 18px;
            background: linear-gradient(120deg, #e8f2ff, #f1edff);
            border: 1px solid #d9e7ff;
        }
        .wuma-brand-lockup {
            display: flex;
            align-items: center;
            gap: 1rem;
            margin: 0.2rem 0 1rem;
        }
        .wuma-brand-icon {
            width: 72px;
            height: 72px;
            flex: 0 0 auto;
            filter: drop-shadow(0 8px 18px rgba(37, 99, 235, 0.18));
        }
        .wuma-brand-icon svg {
            display: block;
            width: 100%;
            height: 100%;
        }
        .wuma-brand-name {
            color: #172033;
            font-size: 3.2rem;
            line-height: 1;
            font-weight: 800;
            letter-spacing: -0.06em;
        }
        .wuma-brand-name span {
            color: #2563EB;
        }
        .wuma-brand-compact {
            gap: 0.65rem;
            margin: 0.2rem 0 0.75rem;
        }
        .wuma-brand-compact .wuma-brand-icon {
            width: 42px;
            height: 42px;
        }
        .wuma-brand-compact .wuma-brand-name {
            font-size: 1.65rem;
        }
        div[data-testid="stMetric"] {
            background: white;
            border: 1px solid #e5e7eb;
            padding: 1rem;
            border-radius: 14px;
        }
        .wuma-summary-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(min(100%, 190px), 1fr));
            gap: 14px;
            margin: 16px 0 24px;
        }
        .wuma-summary-card {
            min-width: 0;
            padding: 20px;
            border: 1px solid #e2e8f0;
            border-radius: 16px;
            background: #ffffff;
        }
        .wuma-summary-label { color: #596579; font-size: 0.88rem; margin-bottom: 12px; }
        .wuma-summary-value {
            color: #172033;
            font-size: clamp(1.2rem, 1.8vw, 1.8rem);
            font-weight: 650;
            line-height: 1.45;
            white-space: normal;
            overflow-wrap: anywhere;
        }
        .wuma-summary-note { color: #64748b; font-size: 0.85rem; margin-top: 8px; }
        .wuma-bar-panel {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 16px;
            padding: 20px 24px;
            margin-bottom: 12px;
        }
        .wuma-bar-scale { color: #64748b; font-size: 0.82rem; line-height: 1.7; margin-bottom: 6px; }
        .wuma-bar-row { padding: 14px 0 10px; }
        .wuma-bar-row + .wuma-bar-row { border-top: 1px solid #f1f5f9; }
        .wuma-bar-heading { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 16px; margin-bottom: 10px; }
        .wuma-bar-label { flex: 1 1 160px; color: #334155; font-size: 0.95rem; line-height: 1.5; overflow-wrap: anywhere; }
        .wuma-bar-value { flex: 0 0 auto; color: #172033; font-size: 1rem; font-variant-numeric: tabular-nums; }
        .wuma-bar-track { height: 10px; background: #eef2f7; border-radius: 10px; overflow: hidden; }
        .wuma-bar-fill { height: 100%; border-radius: 10px; }
        .wuma-bar-note { color: #64748b; font-size: 0.8rem; line-height: 1.5; margin-top: 7px; overflow-wrap: anywhere; }
        @media (max-width: 640px) {
            .wuma-bar-panel { padding: 16px; }
            .wuma-summary-card { padding: 16px; }
        }
        .wuma-table-wrapper {
            width: 100%;
            overflow-x: auto;
            border: 1px solid #d9e1ec;
            border-radius: 12px;
            background: white;
        }
        table.wuma-data-table {
            width: 100%;
            border-collapse: collapse;
            color: #172033;
        }
        table.wuma-data-table th,
        table.wuma-data-table td {
            padding: 0.7rem 0.8rem;
            text-align: center !important;
            vertical-align: middle !important;
            border-right: 1px solid #d9e1ec;
            border-bottom: 1px solid #d9e1ec;
        }
        table.wuma-data-table th {
            background: #f2f6fd;
            color: #657086;
            font-weight: 600;
            white-space: nowrap;
        }
        table.wuma-data-table tr.wuma-selected-row td {
            background-color: #e5efff;
            font-weight: 600;
        }
        table.wuma-data-table tr.wuma-selected-row td:first-child {
            box-shadow: inset 4px 0 #2563eb;
        }
        table.wuma-data-table tr:last-child td {
            border-bottom: none;
        }
        table.wuma-data-table th:last-child,
        table.wuma-data-table td:last-child {
            border-right: none;
        }
        .wuma-format-code {
            margin: 0;
            padding: 0.85rem 1rem;
            min-height: 3.2rem;
            overflow-x: auto;
            white-space: pre-wrap;
            overflow-wrap: anywhere;
            border: 1px solid #dbe4f0;
            border-radius: 10px;
            background: #f6f8fb;
            color: #172033;
            font-family: "Cascadia Code", "Consolas", monospace;
            font-size: 1rem;
            line-height: 1.65;
        }
        .wuma-space-run {
            display: inline-block;
            margin: 0 0.12em;
            padding: 0.02em 0.34em;
            color: #1d4ed8;
            background: #dbeafe;
            border: 1px solid #93c5fd;
            border-radius: 4px;
            font-size: 0.76em;
            font-weight: 800;
            line-height: 1.35;
            letter-spacing: 0;
            vertical-align: 0.08em;
            white-space: nowrap;
        }
        .wuma-space-word {
            font-size: 0.94em;
        }
        .wuma-newline-marker {
            color: #db2777;
            font-size: 0.9em;
            font-weight: 900;
            background: #fce7f3;
            border-radius: 3px;
        }
        .wuma-tab-marker {
            color: #b45309;
            font-size: 0.82em;
            font-weight: 900;
            background: #fef3c7;
            border-radius: 3px;
        }
        .wuma-format-legend {
            margin: -0.2rem 0 0.8rem;
            color: #657086;
            font-size: 0.88rem;
        }
        .wuma-empty-sample {
            color: #94a3b8;
            font-style: italic;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main():
    db.init_db()
    demo_mode.ensure_demo_data()
    init_state()
    add_styles()
    if st.session_state.auth_role not in {"学生", "教师"}:
        show_login()
        return
    allowed_pages = pages_for_role(st.session_state.auth_role)
    if st.session_state.current_page not in allowed_pages:
        st.session_state.current_page = "首页"
    show_sidebar()

    page_functions = {
        "首页": show_home,
        "任务中心": show_task_center,
        "教师工作台": show_teacher_dashboard,
        "实验提交": show_submission,
        "AI答辩": show_defense,
        "学生报告": show_report,
        "学习档案": show_learning_archive,
        "实验管理": show_assignment_management,
    }
    page_functions[st.session_state.current_page]()


if __name__ == "__main__":
    main()
