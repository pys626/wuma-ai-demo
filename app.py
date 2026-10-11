from datetime import datetime
from pathlib import Path
import hashlib
import json
import html
import os
import re
import sqlite3
import time

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import code_verifier as verifier
import database as db
import demo_mode
import demo_playback
import llm_service as llm
import teacher_insights as insights
import runtime_config
import accounts
import access_control
import account_ui
import browser_session
from ui_uploads import chinese_file_uploader
import assignment_drafts
import student_drafts
import defense_drafts
import ai_workflow
import experiment_packages
import evaluation_benchmark
from local_store import fingerprint, read_json, write_json


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
    "班级与学生管理",
]

ADMIN_PAGES = ["首页", "账号与系统管理"]

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
    "逻辑理解": "#3568A0",
    "概念掌握": "#218679",
    "边界意识": "#8760AA",
    "修改能力": "#C17A2E",
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
        "teacher_active_task": None,
        "teacher_task_return_state": None,
        "student_task_feedback_id": None,
        "navigation_scroll_pending": False,
        "navigation_scroll_sequence": 0,
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


def request_navigation(page):
    """Apply navigation before rendering, without interrupting session persistence."""
    clear_teacher_task_focus()
    if page != "学生报告":
        st.session_state.student_task_feedback_id = None
    allowed_pages = pages_for_role(st.session_state.get("auth_role"))
    st.session_state.current_page = page if page in allowed_pages else "首页"
    st.session_state.navigation_scroll_pending = True


def navigate(page):
    request_navigation(page)
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
    if role == "管理员":
        return ADMIN_PAGES
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
    if st.session_state.get("auth_token"):
        accounts.logout(st.session_state.auth_token)
    browser_session.clear_state()
    st.rerun()


def show_login():
    """公开Demo采用合成身份；完整本地版使用个人账号。"""
    st.markdown(
        """
        <style>
        [data-testid="stMainBlockContainer"] {
            max-width: none;
            width: min(100%, calc(82% + 4rem));
            margin-inline: auto;
        }
        @media (max-width: 640px) {
            [data-testid="stMainBlockContainer"] { width: 100%; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    render_brand_lockup()
    if demo_mode.demo_mode_enabled():
        st.title("登录悟码AI")
        st.caption("选择身份后进入对应工作区；登录后需退出才能切换身份。")
        st.info(
            "当前为赛事在线Demo，展示内容均为合成教学数据。"
            "可直接选择学生端或教师端体验，无需密码。"
        )
        student_col, teacher_col = st.columns(2)
        with student_col:
            if st.button(
                f"体验学生端（{demo_mode.DEMO_STUDENT_NAME}）",
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
    if demo_mode.demo_mode_enabled():
        return
    account_ui.login(teacher_password())


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


def render_reading_note(title, detail, tone="info", meta=""):
    """白底深色正文，仅以细边框和标签提示状态；所有内容均按纯文本转义。"""
    if tone not in {"info", "positive", "warning", "danger", "neutral"}:
        tone = "info"
    st.markdown(
        f'<div class="wuma-reading-note wuma-note-{tone}">'
        f'<div class="wuma-note-heading"><strong>{html.escape(str(title))}</strong>'
        f'<span>{html.escape(str(meta))}</span></div>'
        f'<div class="wuma-note-detail">{html.escape(insights.clean_prose(detail))}</div></div>',
        unsafe_allow_html=True,
    )


def reset_scroll_after_navigation():
    """仅在切换页面/任务时回到顶部，输入、展开证据或保存表单时保留位置。"""
    # Keep one fixed slot even after the scroll script is consumed. Otherwise
    # the first filter rerun shifts unkeyed expanders and moves their open state.
    scroll_anchor = st.empty()
    if st.session_state.pop("navigation_scroll_pending", False):
        sequence = st.session_state.get("navigation_scroll_sequence", 0) + 1
        st.session_state.navigation_scroll_sequence = sequence
        with scroll_anchor:
            components.html(
                f"<!-- navigation {sequence} -->" + """<script>
                requestAnimationFrame(() => {
                    const main = window.parent.document.querySelector('[data-testid="stMain"]');
                    if (main) main.scrollTo({top: 0, left: 0, behavior: 'instant'});
                    window.parent.scrollTo(0, 0);
                });
                </script>""", height=0,
            )


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


def evidence_block_html(title, text, kind="question", meta=""):
    """按内容来源分区，学生原文和动态内容均完整保留并转义。"""
    labels = {"question": "考查内容", "answer": "学生原文", "judgment": "AI判断",
              "warning": "需核查", "source": "来源记录",
              "code": "代码依据", "material": "材料依据"}
    if kind not in labels:
        kind = "source"
    value = "" if text is None else str(text)
    escaped_value = html.escape(value).replace("\r", "&#13;").replace("\n", "&#10;")
    return (
        f'<section class="wuma-evidence-block wuma-evidence-block-{kind}">'
        '<div class="wuma-evidence-heading">'
        f'<span class="wuma-evidence-tag">{labels[kind]}</span>'
        f'<strong>{html.escape(str(title))}</strong>'
        f'<span class="wuma-evidence-meta">{html.escape(str(meta))}</span></div>'
        f'<div class="wuma-evidence-body">{escaped_value}</div></section>'
    )

def render_evidence_block(title, text, kind="question", meta=""):
    st.markdown(evidence_block_html(title, text, kind, meta), unsafe_allow_html=True)


def teaching_focus_html(points):
    """Group the original configured items for display without changing content."""
    groups = {}
    for number, point in enumerate(points, 1):
        text = str(point)
        if any(word in text for word in ('教师核验', '本人代码', '理解全部', '测试通过')):
            topic = '评价与教师核验'
        elif any(word in text for word in ('复杂度', 'BFS', 'Bellman-Ford', '时间上界', '空间O(')):
            topic = '复杂度与算法适用范围'
        elif any(word in text for word in ('INF', '溢出', '最大距离', '32位', '数值边界')):
            topic = '数值边界与整数范围'
        elif any(word in text for word in ('有向边', '反向边', '平行边')):
            topic = '图的建模与边的处理'
        elif any(word in text for word in ('首次', '提前', '非负')):
            topic = '返回条件与算法前提'
        elif any(word in text for word in ('dist[', '队列', '松弛', '状态', '零权环')):
            topic = '距离、队列状态与松弛'
        else:
            topic = '其他教学重点'
        pattern = r'greater<pair<long long,int>>|distance\s*!=\s*dist\[u\]|candidate\s*<\s*dist\[v\]|graph\[u\]\.push_back|INF=max/4|dist\[[uv]\]|long long|Bellman-Ford|BFS|<='
        cursor = 0
        fragments = []
        for match in re.finditer(pattern, text):
            fragments.append(html.escape(text[cursor:match.start()]))
            fragments.append('<code>' + html.escape(match.group()) + '</code>')
            cursor = match.end()
        fragments.append(html.escape(text[cursor:]))
        value = ''.join(fragments).replace('\r', '&#13;').replace('\n', '&#10;')
        groups.setdefault(topic, []).append(f'<li><span class="wuma-focus-number">{number:02d}</span><div class="wuma-focus-text">{value}</div></li>')
    cards = ''.join('<section class="wuma-focus-card"><h4>' + html.escape(topic) + '</h4><ul>' + ''.join(items) + '</ul></section>'
                    for topic, items in groups.items())
    return '<div class="wuma-focus-grid">' + cards + '</div>'


def render_teaching_focus(points):
    st.markdown('**教师专用教学重点**')
    st.caption('学生不可见，用于AI出题。以下保留提交时配置的全部原文，编号对应原条目。')
    if points:
        st.markdown(teaching_focus_html(points), unsafe_allow_html=True)
    else:
        st.caption('该提交的实验快照未记录教学重点。')

def build_defense_score_chart(data):
    """一条完整评分显示柱形，多条叠加折线；未评分处保留空缺且不跨缺口连线。"""
    frame = data.copy()
    frame["AI答辩理解度"] = pd.to_numeric(frame["AI答辩理解度"], errors="coerce")
    segments, segment = [], 0
    for score in frame["AI答辩理解度"]:
        if pd.isna(score):
            segment += 1
        segments.append(segment)
    frame["评分连续段"] = segments
    x = alt.X(
        "累计答辩记录:O", sort=None,
        scale=alt.Scale(domain=frame["累计答辩记录"].tolist()),
        axis=alt.Axis(title="答辩记录序号", labelAngle=0, labelExpr="'记录' + datum.label"),
    )
    y = alt.Y("AI答辩理解度:Q", scale=alt.Scale(domain=[0, 100]),
              axis=horizontal_score_axis("AI答辩理解度"))
    base = alt.Chart(frame).encode(x=x, y=y, tooltip=[
        alt.Tooltip("累计答辩记录:O", title="累计记录序号"),
        alt.Tooltip("实验任务:N", title="实验任务"),
        alt.Tooltip("提交编号:O", title="提交编号"),
        alt.Tooltip("答辩轮次:O", title="答辩轮次"),
        alt.Tooltip("提交时间:N", title="提交时间"),
        alt.Tooltip("AI答辩理解度:Q", title="AI答辩理解度", format=".0f"),
    ])
    bars = base.mark_bar(color="#9FBEE1", size=min(7, 220 / max(len(frame), 1)),
                         cornerRadiusTopLeft=2, cornerRadiusTopRight=2)
    layers = [bars]
    if frame["AI答辩理解度"].notna().sum() >= 2:
        layers.append(base.mark_line(
            color="#3568A0", strokeWidth=2,
            point=alt.OverlayMarkDef(filled=True, size=50),
        ).encode(detail="评分连续段:N"))
    # 透明层保留较宽的悬停区域，细柱仍容易查看提交详情。
    layers.append(base.mark_bar(color="#3568A0", size=min(24, 220 / max(len(frame), 1)), opacity=0))
    layers.append(base.mark_text(dy=-12, color="#263D57", fontSize=12).encode(
        text=alt.Text("AI答辩理解度:Q", format=".0f")))
    missing = frame.loc[frame["AI答辩理解度"].isna()]
    if not missing.empty:
        layers.append(alt.Chart(missing).mark_text(
            text="暂不评分", dy=-12, color="#64748B", fontSize=12,
        ).encode(x=x, y=alt.datum(0), tooltip=[
            alt.Tooltip("累计答辩记录:O", title="累计记录序号"),
            alt.Tooltip("实验任务:N", title="实验任务"),
            alt.Tooltip("提交编号:O", title="提交编号"),
        ]))
    return alt.layer(*layers).properties(height=330, padding={"top": 48})

def render_question_reason(item):
    """原始出题依据按需核查，内部字段不占据主要阅读区。"""
    if item.get("question_reason"):
        with st.expander("查看问题生成依据（原始记录）", expanded=False):
            render_evidence_block("为什么问这题", item["question_reason"], "source")

def render_diagnosis_evidence(qa_records):
    st.subheader("诊断证据")
    st.caption("下方按题目、学生原文和AI评价依据分区。AI判断把握是模型对本次评价的自评，不代表学生掌握程度或实际准确率。")
    for index, item in enumerate(qa_records, start=1):
        evaluation = item.get("final_evaluation") or item.get("initial_evaluation") or {}
        score = evaluation.get("score", item.get("reference_score"))
        title = f"问题{index}"
        if evaluation.get("premise_issue"):
            title += " · 题目前提待教师核查"
        elif score is not None:
            title += f" · AI理解度评分 {score}"
        with st.expander(title):
            render_evidence_block("答辩题目", item["question"], "question", meta=f"第{index}题")
            render_evidence_block("学生首次回答", item.get("answer") or "未作答", "answer", meta="首次回答原文")
            if item.get("follow_up_answer"):
                render_evidence_block("AI追问", item["follow_up_question"], "question")
                render_evidence_block("学生对追问的补充回答", item["follow_up_answer"], "answer", meta="补答原文")
            if evaluation.get("feedback"):
                meta = "AI自评判断把握：" + evaluation["confidence"] if evaluation.get("confidence") else ""
                render_evidence_block(
                    "AI评价依据（题目前提待核查）" if evaluation.get("premise_issue") else "AI评价依据",
                    insights.clean_prose(evaluation["feedback"]),
                    "warning" if evaluation.get("premise_issue") else "judgment", meta,
                )
            elif evaluation.get("confidence"):
                st.caption("AI自评判断把握：" + evaluation["confidence"])
            if not render_premise_notice(evaluation) and evaluation.get("missing_points"):
                render_evidence_block("AI认为本次回答仍需补充的要点", insights.join_clauses(evaluation["missing_points"]), "warning")
            if not evaluation:
                render_evidence_block("本题尚无AI评价", "当前记录不能用于判断该学生是否掌握本题考查要点。", "warning")
            render_question_reason(item)
    st.caption("AI诊断仅供教学参考，教师应结合课程要求和学生实际情况进行复核。")


def horizontal_score_axis(title):
    """将分数纵轴标题横排放在绘图区左上方，避免挤占图表宽度。"""
    return alt.Axis(
        title=title,
        titleAngle=0,
        titleAnchor="end",
        titleAlign="left",
        titleX=0,
        titleY=-36,
        titleColor="#365F88",
        titleFontWeight=500,
    )


def render_category_bar_chart(
    data,
    category,
    value,
    height=300,
    maximum=None,
    use_dimension_colors=True,
):
    """按能力维度固定配色，坐标标签与纵轴标题均水平显示。"""
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
            size=min(44, 220 / max(len(data), 1)),
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
                axis=horizontal_score_axis(value),
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
    )
    labels = chart.mark_text(dy=-12, color="#263D57", fontSize=12).encode(
        color=alt.value("#263D57"),
        text=alt.Text(f"{value}:Q", format=".0f"),
    )
    st.altair_chart(alt.layer(chart, labels).properties(height=height, padding={"top": 48}), width="stretch")


def confirmed_score_text(value):
    return f"{value}/100" if value is not None else "尚未评分"


def understanding_text(record, suffix="/100"):
    if not record:
        return "待完成"
    score = record.get("overall")
    return f"{score}{suffix}" if score is not None else (
        "评价尚不完整" if record.get("status") == "已完成" or record.get("assessment_complete") is False
        or record.get("level") == "评价尚不完整" else "待完成"
    )


def ai_mastery_status_text(status, scope="本次答辩考查的要点"):
    """与教师摘要和连接器共用明确的状态说明。"""
    return insights.mastery_statement(status, scope)


def render_dimension_evidence(report):
    """显示本次作答的逐点依据；不足以评分时不把原始标记当作结论。"""
    details = report.get("dimension_evidence") or {}
    if not details:
        return
    st.caption(f"本次答辩具有充分作答依据的能力维度：{report.get('assessed_dimensions', 0)}/4。缺少依据的维度暂不评分。")
    render_centered_dataframe(pd.DataFrame([
        {"本次考查的能力维度": name,
         "AI理解度评分": f"{item['score']}/100" if item.get("score") is not None else "暂不评分",
         "AI根据该学生作答作出的判断": ai_mastery_status_text(item["status"], "本维度考查的要点")}
        for name, item in details.items()
    ]), evidence_kind="dimension")
    st.caption("下方AI判断仅针对该学生本次答辩的考查要点；学生回答与AI判断分别标注，教师复核状态另行记录。")
    short_statuses = {
        "要点已掌握": "学生已掌握本维度考查要点",
        "需要巩固": "学生回答仍有错误或遗漏",
        "证据不足": "现有作答证据不足",
        "待核查": "作答或题目前提待核查",
    }
    for name, item in details.items():
        score_text = f"{item['score']}/100" if item.get("score") is not None else "暂不评分"
        short_status = short_statuses.get(item["status"], item["status"])
        with st.expander(f"{name} · AI判断：{short_status} · {score_text}", expanded=False):
            judgment = ai_mastery_status_text(item["status"], "本维度考查的要点")
            kind = "judgment" if item["status"] == "要点已掌握" else "warning"
            render_evidence_block("本维度的AI判断", judgment + "。\n" + insights.clean_prose(item["reason"]), kind)
            for point_index, point in enumerate(item.get("points", []), 1):
                parts = [evidence_block_html(
                    "考查要点", point["reference_point"], "question",
                    meta=f"第{point['question']}题 · 本维度要点{point_index}",
                )]
                if point.get("answer_quote"):
                    source = {"首次回答": "首次回答", "追问回答": "对追问的补充回答"}.get(point.get("source"), "实际回答")
                    parts.append(evidence_block_html("学生回答原文摘录", point["answer_quote"], "answer", meta=source))
                else:
                    parts.append(evidence_block_html("学生回答依据", "当前记录没有可核对的回答原文摘录。", "source"))
                point_status = {
                    "掌握": "该学生已掌握这一考查要点",
                    "部分掌握": "该学生对这一考查要点仅有部分理解",
                    "理解错误": "该学生对这一考查要点存在理解错误",
                }.get(point["status"], "现有回答不足以判断该学生是否掌握这一考查要点")
                usable = point.get("usable", not point.get("reason"))
                if not usable:
                    text = "本要点暂不评分：现有证据不足或需要教师核查。\n"
                    text += "AI原始标记：" + point["status"] + "（未计入理解度评分）"
                else:
                    text = "AI根据该学生的回答判断：" + point_status + "。"
                if point.get("reason"):
                    text += "\n" + ("暂不评分的原因：" if not usable else "核查说明：") + insights.clean_prose(point["reason"])
                parts.append(evidence_block_html("本要点的AI判断", text,
                    "judgment" if usable and point["status"] == "掌握" else "warning"))
                st.markdown('<div class="wuma-evidence-point">' + "".join(parts) + '</div>', unsafe_allow_html=True)
    st.caption("AI逐点计分：掌握100、部分掌握65、明确理解错误0，维度内等权汇总；缺少可靠依据时暂不评分。以上判断仍需结合原文核查。")


def code_verification_cards_html(result):
    """测试摘要随可用宽度换列，完整显示结论并区分实际结果与准入要求。"""
    passed = int(result.get("passed", 0) or 0)
    total = int(result.get("total", 0) or 0)
    score = int(result.get("score", 0) or 0)
    maximum = int(result.get("max_score", 0) or 0)
    cards = [
        ("本次测试结论", result.get("overall_status", "未执行"), "该次提交保存的测试结果"),
        ("本次编译结果", (result.get("compile") or {}).get("status", "未执行"), "按本次使用的编译标准执行"),
        ("测试通过数量", f"{passed} / {total}" if total else "未执行", "已通过数量 / 已配置测试总数"),
        (("进入答辩的要求", "必须全部通过", "公开与隐藏测试均须通过；代码不计分")
         if result.get("scoring_mode") == "pass_only"
         else ("历史测试得分", f"{score} / {maximum}" if maximum else "历史未计分", "保留该次提交原有的代码得分")),
    ]
    content = "".join(
        '<div class="wuma-summary-card">'
        f'<div class="wuma-summary-label">{html.escape(str(label))}</div>'
        f'<div class="wuma-summary-value">{html.escape(str(value))}</div>'
        f'<div class="wuma-summary-note">{html.escape(str(note))}</div></div>'
        for label, value, note in cards
    )
    return f'<div class="wuma-summary-grid wuma-verification-grid">{content}</div>'


def teacher_summary_cards_html(record, report):
    """长结论自动换行，结论与确认分数分开显示。"""
    code_result, code_score = compact_code_result(record)
    reviewed = bool(record.get("teacher_reviewed")) and not record.get("pending_ai_dispute_count")
    if reviewed:
        decision = record.get("teacher_decision") or "已复核"
    elif int(record.get("attempt_number") or 1) > 1:
        decision = "待复核（重答）"
    elif record.get("pending_ai_dispute_count") or record.get("_needs_teacher_review") or record.get("review_required"):
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
    teacher_tone = (
        "warning" if decision.startswith("待复核") or decision == "要求学生重新答辩"
        else "positive" if reviewed else "neutral"
    )
    cards = [
        ("代码判定", code_result, "该次提交保存的结果", "code"),
        (("进入答辩的要求", "必须全部通过", "公开与隐藏测试均须通过", "neutral")
         if (record.get("code_verification") or {}).get("scoring_mode") == "pass_only"
         else ("代码客观得分", code_score, "来自隐藏测试", "neutral")),
        ("AI答辩理解度", understanding_text(report), "AI根据该学生本次回答评分", "analysis"),
        ("教师结论", decision, teacher_note, teacher_tone),
    ]
    content = "".join(
        f'<div class="wuma-summary-card wuma-summary-card-{tone}">'
        f'<div class="wuma-summary-label">{html.escape(str(label))}</div>'
        f'<div class="wuma-summary-value">{html.escape(str(value))}</div>'
        f'<div class="wuma-summary-note">{html.escape(str(note))}</div></div>'
        for label, value, note, tone in cards
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
        {"label": insights.mastery_statement(label), "value": count, "maximum": total,
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


def render_centered_dataframe(data, highlighted_rows=(), evidence_kind=None):
    """只读表格；证据表为长文本分配宽度，其余表格保留原有布局。"""
    table_html = data.to_html(
        index=False,
        escape=True,
        border=0,
        classes=(f"wuma-data-table wuma-evidence-table wuma-evidence-{evidence_kind}"
                 if evidence_kind in {"dimension", "rubric", "code", "teacher-status"} else "wuma-data-table"),
        justify="center",
    )
    if evidence_kind == "dimension":
        # 保留 pandas 的 HTML 转义，只给判断列添加随实际列宽排版的文本块。
        table_html = re.sub(
            r"<td>([^<]*)</td>(\s*</tr>)",
            r'<td><span class="wuma-adaptive-text">\1</span></td>\2',
            table_html,
        )
    if evidence_kind == "teacher-status":
        labels = [html.escape(str(column), quote=True) for column in data.columns]
        cell_index = 0
        def label_cell(match):
            nonlocal cell_index
            column_index = cell_index % len(labels)
            label = labels[column_index]
            content = match.group(1)
            if column_index == 0:
                # pandas 会把真实换行显示成反斜杠 n；从原始单元格安全转义，保留分行。
                content = html.escape(str(data.iloc[cell_index // len(labels), 0])).replace("\r", "&#13;").replace("\n", "&#10;")
            cell_index += 1
            return f'<td data-label="{label}">{content}</td>'
        table_html = re.sub(r"<td>(.*?)</td>", label_cell, table_html, flags=re.S)
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


def ai_checkpoint(kind, inputs):
    return ai_workflow.Checkpoint(db.current_database_path(), {
        "kind": kind, "inputs": inputs, "provider": llm.provider_name(),
        "model": llm.model_name(), "evaluation_version": llm.EVALUATION_VERSION,
    })


def run_ai_stage(checkpoint, stage, operation, action):
    cached = checkpoint.get(stage)
    if cached is not None:
        st.caption(f"{stage}：使用已保存结果，无需重复调用AI。")
        return cached
    st.write(stage)
    timer = st.empty()
    try:
        result = operation.run(action, lambda elapsed, left: timer.caption(
            f"已等待 {elapsed:.0f} 秒 · 本次操作剩余预算 {left:.0f} 秒 · 单次模型请求最多45秒"
        ))
        checkpoint.save(stage, result)
    except Exception:
        checkpoint.failed(stage)
        raise
    timer.caption(f"{stage}已完成 · 本次操作已用 {time.monotonic() - operation.started:.0f} 秒")
    return result


def clear_ai_checkpoint(checkpoint):
    try:
        checkpoint.clear()
    except OSError:
        st.caption("正式记录已保存；阶段缓存暂未清理，再次进入会核对已有记录。")


def finalize_report(operation=None):
    """调用AI生成报告并保存；失败时允许用户重试。"""
    submission = db.get_submission(st.session_state.submission_id)
    qa_records = db.get_qa_records(st.session_state.submission_id)
    checkpoint = ai_checkpoint("report", {"submission_id": submission["id"], "qa": qa_records})
    report = run_ai_stage(checkpoint, "汇总答辩并生成报告", operation or ai_workflow.Operation(),
                          lambda: llm.generate_report(submission, qa_records))
    db.save_report(
        st.session_state.submission_id,
        report,
        datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    st.session_state.report = report
    st.session_state.report_error = ""
    st.session_state.defense_completed = True
    clear_ai_checkpoint(checkpoint)


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
            "reference_dimensions": item.get("reference_dimensions", []),
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
            explanation_required=assignment.get("explanation_required", True),
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


def move_to_next_question(operation=None):
    """结束当前题；三题完成后自动请求最终报告。"""
    st.session_state.current_question += 1
    st.session_state.awaiting_follow_up = False
    st.session_state.current_follow_up_question = ""

    if st.session_state.current_question >= len(st.session_state.questions):
        try:
            finalize_report(operation)
        except (llm.LLMServiceError, sqlite3.Error, ai_workflow.WorkflowError, OSError, ValueError) as error:
            st.session_state.report_error = str(error)


def show_home():
    role = st.session_state.auth_role
    if role == "管理员":
        st.title("管理员首页")
        st.caption("管理员负责账号和班级归属；发布实验、答辩证据与教学复核由教师账号处理。")
        actor = access_control.current()
        teachers = accounts.list_teachers(actor['id'])
        classes = accounts.list_classes(actor['id'])
        left, right = st.columns(2)
        left.metric("启用的教师账号", sum(bool(item['active']) for item in teachers))
        right.metric("班级数量", len(classes))
        st.info("开通教师账号 → 为班级指定任课教师 → 教师登录后开展教学。")
        st.button("进入账号与系统管理", type="primary",
                  on_click=request_navigation, args=("账号与系统管理",))
        return
    render_brand_lockup()
    st.subheader("面向编程实验的自适应答辩与理解度评测平台")
    st.markdown("### 不止评代码，更要评理解。")
    if role == "教师":
        st.write(
            "配置实验题面、评分点和测试样例，自检通过后发布实验。"
            "在教师工作台查看学生代码验证结果与答辩证据，完成教师复核并回复反馈。"
        )
    else:
        st.write(
            "学生提交C++代码后，系统先形成编译与测试证据，AI再围绕本人代码和"
            "真实验证结果生成个性化问题，并通过答辩与教师复核形成学习证据。"
        )
    st.write("")
    published_count = len(db.list_assignments(published_only=True))
    col1, col2, col3 = st.columns(3)
    col1.metric("代码证据", "编译＋样例", "可选受限运行")
    col2.metric("AI答辩问题", "每次3道", "结合验证结果生成")
    col3.metric("已发布实验", published_count, "评分点与样例可配置")

    st.subheader("使用流程")
    if role == "教师":
        steps = [
            "① 配置实验与评分点",
            "② 自检样例并发布实验",
            "③ 查看学生报告与答辩证据",
            "④ 完成复核与回复反馈",
        ]
    else:
        steps = [
            "① 提交代码与思路",
            "② 编译与样例验证",
            "③ AI答辩与按需追问",
            "④ 报告、反馈与教师复核",
        ]
    columns = st.columns(4)
    for column, step in zip(columns, steps):
        column.info(step)

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
    st.session_state.student_task_feedback_id = None

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
        st.session_state.student_task_feedback_id = task["feedback_id"]
        db.mark_student_feedback_viewed(
            task["feedback_id"],
            submission["student_id"],
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        set_flash("已打开教师回复，对应提醒已自动完成。")
    navigate("学生报告")


def open_teacher_task(task):
    """直接打开待办的处理表单，并保留进入前的筛选条件。"""
    if not st.session_state.get("teacher_active_task"):
        st.session_state.teacher_task_return_state = {
            key: st.session_state[key]
            for key in (
                "teacher_assignment_filter", "teacher_record_filter",
                "teacher_record_selector", "teacher_selected_submission_id",
                "teacher_workspace_view", "teacher_feedback_status_filter",
            )
            if key in st.session_state
        }
    st.session_state.teacher_active_task = dict(task)
    st.session_state.teacher_task_submission_id = None
    st.session_state.teacher_task_feedback_id = None
    st.session_state.teacher_task_assignment_id = None
    st.session_state.current_page = "教师工作台"
    st.session_state.navigation_scroll_pending = True


def clear_teacher_task_focus():
    """退出单项处理时恢复筛选，任务本身不会被标记完成。"""
    for key, value in (st.session_state.get("teacher_task_return_state") or {}).items():
        st.session_state[key] = value
    st.session_state.teacher_active_task = None
    st.session_state.teacher_task_return_state = None


def return_to_teacher_queue():
    clear_teacher_task_focus()
    st.session_state.teacher_workspace_view = "待办处理"
    st.session_state.navigation_scroll_pending = True


def render_teacher_task_workspace(task):
    """单项处理视图：操作入口优先出现，保存后仍停留当前任务。"""
    all_tasks = db.list_teacher_tasks(assignment_id=task.get("assignment_id"))
    other_tasks = [item for item in all_tasks if item["task_key"] != task["task_key"]]
    back_col, next_col, _ = st.columns([1, 1, 3])
    with back_col:
        st.button("返回待办", width="stretch", key="teacher_task_back",
                  on_click=return_to_teacher_queue)
    with next_col:
        st.button("处理下一项", width="stretch", key="teacher_task_next",
                  disabled=not other_tasks, on_click=open_teacher_task,
                  args=(other_tasks[0] if other_tasks else task,))
    records = insights.mark_superseded_records(db.list_submissions())
    record = next((item for item in records if item["id"] == task["submission_id"]), None)
    if record is None:
        st.warning("这项待办关联的提交已不存在，请返回待办查看最新任务。")
        return
    current = next((item for item in all_tasks if item["task_key"] == task["task_key"]), None)
    st.caption(f"当前任务：{task['title']} · 其他待办{len(other_tasks)}项")
    if current is None and task.get("status") == "待处理":
        st.success("本项待办已处理。可查看处理结果，或继续处理下一项。")
    record["_mastery"] = insights.mastery_summary(db.get_report(record["id"]), db.get_qa_records(record["id"]))
    record["_needs_teacher_review"] = insights.needs_teacher_review(record, mastery=record["_mastery"])
    render_teacher_student_case(record, focused_task=task)


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
                    ("回复反馈" if task.get("reply_requested", task["title"] == "回复学生反馈")
                     else "查看反馈") if task["action"] == "process_feedback" and task["status"] == "待处理"
                    else "开始复核" if task["action"] == "review_submission" and task["status"] == "待处理"
                    else task["action_label"],
                    key=f"task_action_{role}_{task['task_key']}",
                    type="primary" if task["status"] == "待处理" else "secondary",
                    width="stretch",
                    on_click=open_teacher_task if role != "学生待办" else None,
                    args=(task,) if role != "学生待办" else None,
                )
            if clicked and role == "学生待办":
                try:
                    open_student_task(task)
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
    col1.metric("待处理任务数", pending_count)
    col2.metric("紧急待办数", urgent_count)
    col3.metric("已完成任务数", completed_count)

    selected_filter = st.selectbox(
        "筛选任务",
        ["待处理", "紧急", "普通", "已完成"],
        key=f"task_status_filter_{role}",
    )
    visible_tasks = filter_tasks(tasks, selected_filter)
    st.caption("以上数量统计当前账号和筛选范围内的任务；“已完成”指对应任务已处理，不等于学生已掌握知识。答辩、教师复核和反馈处理各自更新状态。")
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


def student_submission_is_valid(name, student_id, explanation, lab_report, code, explanation_required=True):
    """在正式评测和进入答辩前使用同一组输入校验。"""
    if not name.strip() or not student_id.strip():
        st.error("姓名和学号不能为空。")
        return False
    if explanation_required and not explanation.strip():
        st.error("教师要求解题思路为必做，请填写后继续。")
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
        "执行繁忙",
        "评测中止",
    }


def defense_admission_result(assignment, verification):
    """根据教师规则判断当前代码能否进入正式答辩。"""
    if not assignment.get("verification_enabled"):
        return True, "本实验未启用代码判题，可直接进入AI答辩。"
    if not formal_verification_is_conclusive(verification):
        return False, "代码尚未完成有效判题，暂时不能进入正式答辩。"

    if verification.get("overall_status") == "全部通过":
        return True, "代码已通过全部公开与隐藏测试，可以进入AI答辩。"
    return False, "全部公开与隐藏测试通过后才能进入AI答辩，请继续修改代码。"


def prepare_student_draft(assignment):
    student_id = logged_in_student_id()
    key = str(student_drafts.draft_path(db.current_database_path(), student_id, assignment["id"]))
    marker = "student_draft_loaded_" + hashlib.sha256(key.encode()).hexdigest()
    if not st.session_state.get(marker):
        try:
            saved = student_drafts.load_draft(db.current_database_path(), student_id, assignment["id"])
        except (OSError, student_drafts.DraftError) as error:
            saved = None
            st.session_state[marker + "_blocked"] = True
            st.warning(f"系统草稿暂时无法恢复：{error}；请保留当前输入，并联系任课教师或系统管理员核查。")
        if saved:
            st.session_state.submission_drafts[key] = saved["data"]
            st.session_state[marker + "_saved"] = saved
            st.info("已恢复系统保存的代码、解题思路与报告草稿；" + (
                "请重新评测后提交。" if assignment.get("verification_enabled") else "核对内容后可继续编辑或提交。"
            ))
            if saved.get("assignment_updated_at") != assignment.get("updated_at", ""):
                st.warning("教师已更新实验，草稿已保留，请核对新要求后" + (
                    "重新评测。" if assignment.get("verification_enabled") else "继续编辑或提交。"
                ))
        st.session_state[marker] = True
    return key, st.session_state.submission_drafts.get(key, {})


def persist_student_draft(assignment, key, values):
    marker = "student_draft_loaded_" + hashlib.sha256(key.encode()).hexdigest()
    if st.session_state.get(marker + "_blocked"):
        return
    data = {name: value for name, value in values.items() if name in student_drafts.FIELDS}
    saved = st.session_state.get(marker + "_saved", {})
    if saved.get("data") != data or saved.get("assignment_updated_at") != assignment.get("updated_at", ""):
        try:
            saved = student_drafts.save_draft(db.current_database_path(), logged_in_student_id(), assignment["id"], data,
                                             assignment.get("updated_at", ""))
            st.session_state[marker + "_saved"] = saved
        except (OSError, student_drafts.DraftError) as error:
            st.warning(f"学生草稿尚未保存至系统：{error}；请先复制当前输入备份，持续失败时联系任课教师或系统管理员。")
            return
    st.caption("学生草稿已保存至系统 · " + saved.get("saved_at", "").replace("T", " ")
               + "；离开输入框后自动保存，刷新或服务重启后可恢复。保存草稿不等于提交本次实验。")


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

    metric1, metric2 = st.columns(2)
    metric1.metric("本次通过测试数", f"{passed}/{total}")
    metric2.metric("本次编译状态", "成功")
    if cases:
        rows = []
        for index, item in enumerate(cases, start=1):
            rows.append(
                {
                    "测试点": f"测试点{index}",
                    "结果": item.get("verdict_code") or item.get("status", "未通过"),
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
            st.markdown(f"**AI建议的排查方向：** {hint.get('diagnosis', '—')}")
            st.write(hint.get("guidance", "—"))
            checks = hint.get("self_check") or []
            if checks:
                st.markdown("**请先自查：**")
                for item in checks:
                    st.write(f"- {item}")


def show_submission():
    page_header(
        "实验提交",
        "选择教师已发布的实验，按本实验设置填写代码与材料，进行AI文字问答并查看反馈。",
    )
    show_flash()

    if not llm.is_configured():
        st.error(
            "网页AI评价服务尚未配置，暂不能开始答辩；请联系任课教师或系统管理员。"
        )

    assignments = db.list_assignments(published_only=True)
    if not assignments:
        st.warning("当前没有可用的已发布实验，请联系任课教师确认实验发布和班级安排。")
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
    if demo_playback.active():
        st.info("在线交互演示：填写和保存走真实流程；AI与判题仅回放选择排序固定案例的已保存结果。修改材料后不能套用原评价。")
        if assignment["title"] == demo_playback.fixture()["assignment"]["title"] and st.button("填入演示材料", key=f"demo_materials_{assignment['id']}"):
            materials = demo_playback.fixture()
            for field in ("code", "explanation", "lab_report"):
                st.session_state[f"{field}_{assignment['id']}"] = materials.get(field, "")
    if assignment.get("verification_enabled"):
        st.info(
            "本实验操作顺序：填写代码与教师要求的材料 → 正式评测代码 → "
            "满足开始答辩的条件后点击“进入AI答辩” → 完成文字问答 → 查看报告与教师反馈。"
        )
    else:
        st.info(
            "本实验未启用正式代码测试。操作顺序：填写代码与教师要求的材料 → "
            "点击“调用AI分析并开始答辩” → 完成文字问答 → 查看报告与教师反馈。"
        )
    configuration_blockers = assignment_publish_blockers(assignment)
    if configuration_blockers:
        st.error(
            "该实验仍可练习和运行代码，但配置尚未通过可靠性检查，暂不能进入AI答辩："
            + insights.join_clauses(configuration_blockers)
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
                f"本实验已配置{len(assignment.get('public_samples', []))}组公开样例、"
                f"{len(assignment.get('test_cases', []))}组隐藏测试；全部通过后才能进入AI答辩。"
            )
            admission_text = "公开与隐藏测试全部通过"
            st.caption(
                f"开始AI答辩的条件：{admission_text}；"
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

    draft_key, draft = prepare_student_draft(assignment)
    if st.session_state.submission_ai_error:
        st.warning(
            "上一次AI处理没有完成，但姓名、学号、代码、解题思路和选做实验报告均已保留，"
            "可稍后点击原按钮重试；持续失败时请联系任课教师确认AI服务是否可用。"
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
            "解题思路（必做）" if assignment.get("explanation_required", True) else "解题思路（选做）",
            value=draft.get("explanation", ""),
            placeholder=DEFAULT_EXPLANATION,
            height=110,
            key=f"explanation_{assignment['id']}",
        )
        st.caption("由教师设置：必做，需要填写后评测。" if assignment.get("explanation_required", True)
                   else "由教师设置：选做，可留空；未填写本身不作为扣分依据。")
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
                "可选：检查题目给出的示例是否输出正确，无需复制样例输入；可以运行单个或全部样例，不替代正式代码测试。"
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
            st.caption("可选：使用你输入的数据试运行代码并查看输出；不会自动判断输出是否正确，也不作为开始答辩的依据。")
        formal_judge_clicked = False
        if assignment.get("verification_enabled"):
            formal_judge_clicked = st.button(
                "载入本例已保存的评测证据" if demo_playback.active() else "正式评测代码",
                type="primary",
                width="stretch",
                disabled=assignment["language"] != "C++",
                key=f"formal_judge_{assignment['id']}",
            )
            st.caption("正式评测检查本实验配置的全部公开与隐藏测试；结果决定是否满足开始答辩的代码测试条件。评测后不会自动进入AI答辩。")

    st.session_state.submission_drafts[draft_key] = {
        "name": name,
        "student_id": student_id,
        "explanation": explanation,
        "lab_report": lab_report,
        "code": code,
        "custom_input": custom_input,
    }
    persist_student_draft(assignment, draft_key, st.session_state.submission_drafts[draft_key])

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
        explanation_required=assignment.get("explanation_required", True),
    ):
        with st.spinner("正在核对材料并载入已保存的评测证据……" if demo_playback.active() else "正在编译代码并运行全部公开与隐藏测试……"):
            st.session_state.formal_judge_results[result_key] = {
                "code": code,
                "student_id": student_id.strip(),
                "assignment_updated_at": assignment.get("updated_at", ""),
                "policy_version": "all-official-cases-v1",
                "result": verifier.verify_cpp_code(
                    code,
                    verifier.formal_test_cases(assignment),
                    assignment.get("cpp_standard", "auto"),
                ),
            }

    saved_formal = st.session_state.formal_judge_results.get(result_key, {})
    formal_matches = (
        saved_formal.get("code") == code
        and saved_formal.get("student_id") == student_id.strip()
        and saved_formal.get("assignment_updated_at")
        == assignment.get("updated_at", "")
        and saved_formal.get("policy_version") == "all-official-cases-v1"
    )
    code_verification = saved_formal.get("result", {}) if formal_matches else {}
    if assignment.get("verification_enabled"):
        if formal_matches:
            render_formal_verification_result(code_verification)
        elif saved_formal:
            st.info("代码、学号、实验设置或判题规则已经改变，上次正式评测结果已失效，请重新评测。")
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
        st.caption("本次载入与固定材料对应的已保存问题；提交记录会保存在你的独立演示空间。" if demo_playback.active() else "AI会根据你提交的代码与材料生成文字答辩问题，并根据你的回答决定是否追问。")
        submitted = st.button(
            (
                "进入案例答辩（结果回放）" if demo_playback.active() else "进入AI答辩"
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
        explanation_required=assignment.get("explanation_required", True),
    ):
        return
    if assignment.get("verification_enabled"):
        admitted, admission_message = defense_admission_result(
            assignment,
            code_verification,
        )
        if not formal_matches or not admitted:
            st.error("当前代码没有有效且满足开始答辩条件的正式评测结果，请重新评测。")
            return

    st.session_state.submission_ai_error = ""
    try:
        checkpoint = ai_checkpoint("submission", {
            "student_id": student_id.strip(), "name": name.strip(), "assignment": assignment,
            "code": code, "explanation": explanation.strip(), "lab_report": lab_report.strip(),
            "verification": code_verification,
        })
        operation = ai_workflow.Operation()
        if checkpoint.document.get("failed_stage"):
            st.info("继续上次未完成的阶段：" + checkpoint.document["failed_stage"])
        st.caption("本次操作等待上限120秒；已完成的AI阶段会保存，重试时可继续。")
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
                    f"{code_verification.get('total', 0)}组通过。"
                )
            else:
                code_verification = {}
                st.write("本实验未启用代码判题，按照教师设置继续进行AI静态分析。")

            st.write("步骤2/3：按教师评分点分析代码、解题思路与选做报告")
            preliminary_review = run_ai_stage(checkpoint, "评分点初评", operation, lambda: llm.preliminary_review(
                assignment["title"],
                assignment["requirements"],
                code,
                explanation.strip(),
                lab_report.strip(),
                assignment["rubric"],
                code_verification=code_verification,
                constraints_text=assignment.get("constraints_text", ""),
                explanation_required=assignment.get("explanation_required", True),
            ))
            st.write("步骤3/3：根据证据缺口生成并保存个性化答辩问题")
            questions = run_ai_stage(checkpoint, "生成答辩问题", operation, lambda: llm.generate_questions(
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
                explanation_required=assignment.get("explanation_required", True),
            ))
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
                request_token=checkpoint.document["request_token"],
            )
            ai_status.update(
                label="AI初评与答辩问题已生成",
                state="complete",
                expanded=False,
            )
    except (llm.LLMServiceError, sqlite3.Error, ai_workflow.WorkflowError, OSError, ValueError) as error:
        st.session_state.submission_ai_error = str(error)
        st.error(f"AI处理未完成：{error}")
        st.info("当前输入没有被清空，可稍后点击原按钮重试进入AI答辩；持续失败时联系任课教师。草稿保存状态见上方提示。")
        return

    st.session_state.submission_id = submission_id
    clear_ai_checkpoint(checkpoint)
    st.session_state.form_name = name.strip()
    st.session_state.form_student_id = student_id.strip()
    st.session_state.submission_drafts.pop(draft_key, None)
    try:
        student_drafts.clear_draft(db.current_database_path(), logged_in_student_id(), assignment["id"])
        marker = "student_draft_loaded_" + hashlib.sha256(draft_key.encode()).hexdigest()
        st.session_state.pop(marker + "_saved", None)
    except OSError:
        st.warning("提交已经保存，但旧草稿未能清理；下次进入时请核对已有提交。")
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
            "explanation_required": assignment.get("explanation_required", True),
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


def render_premise_notice(evaluation):
    issue = (evaluation or {}).get("premise_issue")
    if issue:
        st.warning("题目或参考要点前提待教师核查：" + str(issue))
        st.caption("本题保留回答与AI原始评价，暂不计入完整理解分；题目前提有疑问不表示学生理解错误。")
    return bool(issue)


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
            st.write("学生首次回答：", answer_record.get("initial", ""))
            if answer_record.get("follow_up"):
                st.write("AI追问：", answer_record["follow_up_question"])
                st.write("学生对追问的补充回答：", answer_record["follow_up"])
            if final_evaluation:
                st.write("AI对本题回答的评价：", insights.clean_prose(final_evaluation.get("feedback", "")))
                if not render_premise_notice(final_evaluation):
                    st.write("AI对本题回答的理解度评分：", final_evaluation.get("score", ""))
            st.divider()


def show_report_retry():
    st.warning("学生已完成三道问题的作答，但AI最终报告尚未生成。")
    if st.session_state.report_error:
        st.error(st.session_state.report_error)

    if st.button("重新生成报告", type="primary"):
        with st.spinner("正在载入本例已保存的报告……" if demo_playback.active() else "AI正在重新汇总答辩记录……"):
            try:
                finalize_report()
            except (llm.LLMServiceError, sqlite3.Error, ai_workflow.WorkflowError, OSError, ValueError) as error:
                st.session_state.report_error = str(error)
                st.rerun()
        st.rerun()


def answer_draft_context(index, stage, question, follow_up=""):
    normalized = {name: question.get(name, [] if name.startswith("reference_") else "")
                  for name in ["question", "dimension", "reference_points", "reference_dimensions"]}
    if stage == "follow_up":
        normalized["follow_up"] = follow_up
    return (db.current_database_path(), logged_in_student_id(), st.session_state.submission_id, index, stage, normalized)


def save_answer_draft(widget_key, context):
    try:
        if st.session_state.get(widget_key + "_blocked"):
            return False
        answer = st.session_state.get(widget_key, "")
        previous = st.session_state.get(widget_key + "_saved")
        if not previous or previous.get("answer") != answer:
            st.session_state[widget_key + "_saved"] = defense_drafts.save(*context, answer)
        return True
    except (OSError, ValueError) as error:
        st.session_state[widget_key + "_error"] = str(error)
        return False


def render_answer_editor(index, stage, question, follow_up=""):
    context = answer_draft_context(index, stage, question, follow_up)
    widget_key = "defense_answer_" + fingerprint([str(context[0].resolve()), *context[1:]])
    if widget_key not in st.session_state:
        try:
            saved = defense_drafts.load(*context)
        except (OSError, ValueError) as error:
            saved = None
            st.session_state[widget_key + "_blocked"] = True
            st.session_state[widget_key + "_error"] = str(error)
        st.session_state[widget_key] = saved["answer"] if saved else ""
        if saved:
            st.session_state[widget_key + "_saved"] = saved
            if saved["answer"]:
                st.info("已恢复本题的" + ("追问" if stage == "follow_up" else "首次") + "回答草稿。")
    if demo_playback.active():
        example = demo_playback.example_answer(question, stage, st.session_state.get("_interactive_case") or 0)
        if example and st.button("填入示例回答", key=f"demo_answer_{index}_{stage}"):
            st.session_state[widget_key] = example
        st.caption("在线只回放与示例原文匹配的已保存评价；任意新回答请在完整本地版实时评价。" if example else "本题尚无可回放的作答评价；可浏览问题，实时作答请使用完整本地版。")
    answer = st.text_area("请输入追问回答" if stage == "follow_up" else "请输入你的回答",
                          height=140 if stage == "follow_up" else 150, key=widget_key,
                          max_chars=100000, on_change=save_answer_draft, args=(widget_key, context))
    saved = save_answer_draft(widget_key, context)
    if saved:
        st.caption("回答草稿已保存至系统 · " + st.session_state[widget_key + "_saved"]["saved_at"].replace("T", " ")
                   + "；离开输入框后保存，刷新或重启后可从任务中心继续本题。保存草稿不等于提交AI评价。")
    else:
        st.warning("回答草稿尚未保存至系统：" + st.session_state.get(widget_key + "_error", "原草稿需核查") + "；请先复制当前回答备份，持续失败时联系任课教师或系统管理员。")
    return answer, context, saved


def clear_answer_draft(context):
    try:
        defense_drafts.clear(*context)
    except OSError:
        st.caption("回答及评价已正式保存，旧草稿暂未清理；继续答辩以数据库进度为准。")


def show_defense():
    page_header("AI自适应答辩", "在线案例回放：使用示例回答查看已保存的AI评价与追问，提交记录真实保存。" if demo_playback.active() else "每次提交回答都会调用AI评价；回答不充分时最多追问一次。")
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
    question = llm.with_experiment_context(st.session_state.submission or {}, questions[index])

    st.progress(index / len(questions), text=f"当前进度：第{index + 1}题 / 共{len(questions)}题")

    with st.container(border=True):
        st.caption(f"AI考查维度：{question['dimension']}")
        st.subheader(f"问题{index + 1}")
        st.write(question["question"])

    if not st.session_state.awaiting_follow_up:
        answer, draft_context, draft_saved = render_answer_editor(index, "initial", question)
        answer_submitted = st.button("提交并查看已保存评价" if demo_playback.active() else "提交给AI评价", type="primary", key=f"answer_submit_{index}")

        if answer_submitted:
            if not answer.strip():
                st.error("回答不能为空。")
                return
            if not draft_saved:
                st.error("当前回答尚未保存至系统。请先复制回答备份，并联系任课教师或系统管理员核查后重试。")
                return

            with st.spinner("正在核对示例回答并载入已保存评价……" if demo_playback.active() else "AI正在评价回答并判断是否需要追问……"):
                try:
                    submission = st.session_state.submission
                    checkpoint = ai_checkpoint("initial_answer", {"submission_id": st.session_state.submission_id,
                        "question": draft_context[-1], "experiment_context": question["experiment_context"],
                        "code": st.session_state.submission["code"], "answer": answer.strip()})
                    operation = ai_workflow.Operation()
                    evaluation = run_ai_stage(checkpoint, "评价首次回答", operation, lambda: llm.evaluate_initial_answer(
                        submission["problem"],
                        submission["code"],
                        question,
                        answer.strip(),
                    ))
                    follow_up_question = evaluation["follow_up_question"]
                    db.save_initial_result(
                        st.session_state.submission_id,
                        index,
                        answer.strip(),
                        follow_up_question,
                        evaluation,
                        is_final=not evaluation["should_follow_up"],
                    )
                except (llm.LLMServiceError, sqlite3.Error, ai_workflow.WorkflowError, OSError, ValueError) as error:
                    st.error(str(error))
                    st.info("当前回答草稿仍保留；点击“提交给AI评价”可继续，成功保存前不会推进题号。")
                    return
            clear_answer_draft(draft_context)
            clear_ai_checkpoint(checkpoint)

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
                move_to_next_question(operation)
            st.rerun()
    else:
        st.warning("AI发现回答中还有需要澄清的内容，请完成下面这次追问。")
        follow_up_question = st.session_state.current_follow_up_question
        st.markdown(f"**AI追问：{follow_up_question}**")

        follow_up_answer, draft_context, draft_saved = render_answer_editor(index, "follow_up", question, follow_up_question)
        follow_up_submitted = st.button("提交追问回答", type="primary", key=f"follow_up_submit_{index}")

        if follow_up_submitted:
            if not follow_up_answer.strip():
                st.error("追问回答不能为空。")
                return
            if not draft_saved:
                st.error("当前追问回答尚未保存至系统。请先复制回答备份，并联系任课教师或系统管理员核查后重试。")
                return

            answer_record = st.session_state.answers[index]
            with st.spinner("正在核对两次回答并载入已保存评价……" if demo_playback.active() else "AI正在结合两次回答生成最终评价……"):
                try:
                    submission = st.session_state.submission
                    checkpoint = ai_checkpoint("follow_up_answer", {"submission_id": st.session_state.submission_id,
                        "question": draft_context[-1], "experiment_context": question["experiment_context"],
                        "code": st.session_state.submission["code"],
                        "initial_answer": answer_record["initial"], "initial_evaluation": answer_record.get("initial_evaluation"),
                        "answer": follow_up_answer.strip()})
                    operation = ai_workflow.Operation()
                    evaluation = run_ai_stage(checkpoint, "评价追问回答", operation, lambda: llm.evaluate_follow_up(
                        submission["problem"],
                        submission["code"],
                        question,
                        answer_record["initial"],
                        follow_up_question,
                        follow_up_answer.strip(),
                        answer_record.get("initial_evaluation"),
                    ))
                    db.save_follow_up_result(
                        st.session_state.submission_id,
                        index,
                        follow_up_answer.strip(),
                        evaluation,
                    )
                except (llm.LLMServiceError, sqlite3.Error, ai_workflow.WorkflowError, OSError, ValueError) as error:
                    st.error(str(error))
                    st.info("追问回答草稿仍保留；点击“提交追问回答”可继续，成功保存前不会推进题号。")
                    return
            clear_answer_draft(draft_context)
            clear_ai_checkpoint(checkpoint)

            st.session_state.answers[index]["follow_up"] = follow_up_answer.strip()
            st.session_state.answers[index]["final_evaluation"] = evaluation
            move_to_next_question(operation)
            st.rerun()

    show_answer_history()


def render_preliminary_review(preliminary_review):
    """展示按教师评分点生成的证据初评和可追溯依据。"""
    if not preliminary_review:
        st.info("该记录创建于评分点初评功能上线前，暂无评分点证据初评。")
        return

    st.subheader("评分点初评证据")
    st.caption(
        "下方依据是AI对提交代码、解题思路与可选报告的引用或摘要，尚不等于教师确认。"
        "未填写选做报告不会单独扣分，答辩理解度将在后续问答中形成。"
    )
    criteria = preliminary_review.get("criteria", [])
    evidence_df = pd.DataFrame(
        [
            {
                "评分点": item["criterion_name"],
                "AI初评状态": item["status"],
                "初评得分": f"{item['score']} / {item['weight']}",
                "答辩进一步核查": "需要" if item.get("needs_defense") else "未标记",
            }
            for item in criteria
        ]
    )
    if not evidence_df.empty:
        render_centered_dataframe(evidence_df, evidence_kind="rubric")
    st.info(f"AI初评说明：{insights.clean_prose(preliminary_review['summary'])}")

    for index, item in enumerate(criteria, start=1):
        with st.expander(
            f"评分点{index} · {item['criterion_name']} · "
            f"{item['score']}/{item['weight']}分"
        ):
            st.caption("评分点要求的核查来源：" + item.get("source", "未记录")
                       + "；AI对本次初评的判断把握：" + item.get("confidence", "未记录"))
            render_evidence_block("AI引用的代码依据", item.get("code_evidence", "未记录"), "code")
            render_evidence_block("AI引用的材料依据", item.get("report_evidence", "未记录"), "material")
            missing = item.get("missing")
            if missing is None or not str(missing).strip():
                render_evidence_block("缺失内容记录", "本次AI初评未记录缺失内容，不能据此确认材料完整。", "source")
            elif str(missing).strip() in {"无", "无。"}:
                render_evidence_block("缺失内容记录", "AI本次未标记缺失内容。", "source")
            else:
                render_evidence_block("AI指出的尚缺内容", missing, "warning")
            if item.get("needs_defense"):
                render_evidence_block("答辩需进一步核查", item.get("defense_focus", "需要进一步核验。"), "warning")


def render_code_verification(code_verification, teacher_view=False):
    """展示编译与测试证据；学生端不泄露隐藏输入和预期输出。"""
    st.subheader("代码测试结果")
    if not code_verification:
        st.info("该实验未启用代码验证，或该记录创建于代码验证功能上线前。")
        return

    status = code_verification.get("overall_status", "未执行")
    compile_result = code_verification.get("compile", {})
    pass_only = code_verification.get("scoring_mode") == "pass_only"
    st.markdown(code_verification_cards_html(code_verification), unsafe_allow_html=True)
    st.caption("测试结论针对本次配置的测试用例；学生是否理解代码，由答辩回答和教师复核另行判断。")
    effective_standard = code_verification.get("effective_cpp_standard")
    requested_standard = code_verification.get("requested_cpp_standard", "auto")
    if effective_standard:
        st.caption(
            f"编译标准：教师设置为{verifier.cpp_standard_label(requested_standard)}；"
            f"本次实际使用{verifier.cpp_standard_label(effective_standard)}"
            f"（{compile_result.get('standard_flag', '')}）。"
        )
    st.caption(
        "公开与隐藏测试均须通过；代码验证与AI答辩理解度、教师确认结果相互独立。"
        if pass_only else "历史代码得分由该次提交保存的隐藏测试点计算，与答辩及教师确认结果相互独立。"
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
                "说明": "输出一致" if item.get("message") == "实际输出与预期输出一致。" else item.get("message", ""),
            }
            for index, item in enumerate(cases, start=1)
        ]
    )
    if pass_only:
        case_df = case_df.drop(columns=["得分"])
    if not teacher_view:
        for row_index, item in enumerate(cases):
            if item.get("visibility") != "公开":
                case_df.at[row_index, "测试样例"] = f"隐藏测试{row_index + 1}"
                case_df.at[row_index, "测试分组"] = "隐藏测试"
                case_df.at[row_index, "说明"] = "验证通过" if item.get("status") == "通过" else "详见教师端运行证据"
    render_centered_dataframe(case_df, evidence_kind="code")

    visible_messages = [(index, item) for index, item in enumerate(cases, 1)
                        if item.get("message") and (teacher_view or item.get("visibility") == "公开")]
    if visible_messages:
        with st.expander("查看测试运行说明原文", expanded=False):
            for index, item in visible_messages:
                st.write(f"测试{index} · {item.get('name', '')}：{item['message']}")

    group_scores = code_verification.get("group_scores", [])
    if group_scores:
        st.markdown("**测试分组通过情况**" if pass_only else "**测试分组得分**")
        render_centered_dataframe(
            pd.DataFrame(
                [
                    {
                        "测试分组": item.get("group", "未分组"),
                        "通过测试点": f"{item.get('passed', 0)}/{item.get('total', 0)}",
                        **({} if pass_only else {"分组得分": f"{item.get('score', 0)}/{item.get('max_score', 0)}"}),
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
                    + ("" if pass_only else f"{item.get('score', 0)}/{item.get('weight', 0)}分 · ")
                    + f"{item.get('status', '未执行')}**"
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
        f"第**{attempt_number}**轮答辩　提交时间：{submission['submitted_at']}"
    )
    if submission.get("parent_submission_id"):
        st.caption(f"本次为重新答辩，关联上一次提交#{submission['parent_submission_id']}。")

    preliminary_review = db.get_preliminary_review(submission["id"])
    teacher_review = report.get("teacher_review")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric(
        "AI评分点证据初评",
        (
            f"{preliminary_review['completion_score']} / 100"
            if preliminary_review
            else "历史记录"
        ),
    )
    col2.metric("AI答辩理解度", understanding_text(report, " / 100"))
    col3.metric("AI答辩诊断等级", report["level"])
    col4.metric(
        "教师确认理解度",
        (
            confirmed_score_text(teacher_review['confirmed_overall'])
            if teacher_review
            else "尚未记录"
        ),
    )
    code_result, code_score = compact_code_result(submission)
    teacher_result = (
        f"教师确认理解度：{confirmed_score_text(teacher_review['confirmed_overall'])}"
        if teacher_review
        else "尚无教师确认分数；复核进度见下方教师状态"
    )
    render_reading_note(
        "本次报告概览",
        insights.join_clauses([
            f"代码{code_result}（{code_score}）",
            f"AI对该学生本次答辩的理解度评分：{understanding_text(report)}",
            "AI判断依据：" + insights.mastery_summary(report, qa_records)["text"],
            teacher_result,
        ], sentence=True)
    )
    st.caption(
        "评价口径：代码验证检查通过情况（旧报告保留历史得分）；评分点证据初评来自代码、思路与"
        "选做报告；答辩理解度来自问答；教师确认理解度来自人工复核。"
        "各项结果互不相加，也不是课程最终成绩。"
    )
    st.caption("AI根据该学生本次答辩作出的判断：" + ai_mastery_status_text(insights.mastery_summary(report, qa_records)["status"]))

    render_code_verification(submission.get("code_verification"), teacher_view=False)
    render_preliminary_review(preliminary_review)

    short_dimension_names = {
        "程序逻辑理解": "逻辑理解",
        "关键概念掌握": "概念掌握",
        "边界情况意识": "边界意识",
        "分析与修改能力": "修改能力",
    }
    dimension_df = pd.DataFrame([
        {"维度": short_dimension_names.get(name, name), "AI理解度评分": score}
        for name, score in report["dimensions"].items() if score is not None
    ], columns=["维度", "AI理解度评分"])
    st.subheader("本次答辩的四维AI评价")
    if not dimension_df.empty:
        render_category_bar_chart(dimension_df, category="维度", value="AI理解度评分", height=300, maximum=100)
    render_dimension_evidence(report)
    if not report.get("score_version"):
        st.caption("本报告使用历史评分规则，分数保留原始记录。")

    left, right = st.columns(2)
    with left:
        st.info(f"AI对该学生本次答辩的诊断原文：{insights.clean_prose(report['summary'])}")
    with right:
        st.warning(f"AI给该学生的学习建议：{insights.clean_prose(report['suggestion'])}")

    if report.get("pending_ai_dispute_count"):
        st.warning("你对AI结果提出的疑问尚待教师复核。教师回复留言后，仍需完成复核并保存核查结论。")
    elif report.get("review_required"):
        reasons = report.get("review_reasons", [])
        st.warning("建议教师复核：" + insights.join_clauses(reasons))
    else:
        st.success("当前未发现必须人工复核的异常，教师可进行常规抽查。")

    if teacher_review:
        st.subheader("教师人工复核结果")
        review_col1, review_col2, review_col3 = st.columns(3)
        review_col1.metric("复核状态", "待复核（新疑问）" if report.get("pending_ai_dispute_count") else "已复核")
        if report.get("pending_ai_dispute_count"):
            st.caption("下方保留此前的教师复核结论；本次新疑问仍待教师处理。")
        review_col2.metric("教师结论", teacher_review["decision"])
        review_col3.metric(
            "教师确认理解度",
            confirmed_score_text(teacher_review['confirmed_overall']),
        )
        if teacher_review.get("comment"):
            st.info(f"教师复核意见：{teacher_review['comment']}")
        else:
            st.caption("教师未补充文字意见。")
        st.caption(
            f"复核教师：{teacher_review['reviewer_name']}　"
            f"复核时间：{teacher_review['reviewed_at']}"
        )
    elif report.get("review_required") or report.get("pending_ai_dispute_count"):
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

    render_diagnosis_evidence(qa_records)


def render_student_feedback_form(submission_id):
    """让完成答辩的学生向教师反馈，并阻止空内容或重复提交。"""
    feedbacks = db.list_student_feedbacks(submission_id=submission_id)
    pending_count = sum(item["status"] == "待处理" for item in feedbacks)

    st.subheader("给教师留言")
    st.caption(
        "可反馈对AI结果的疑问、补充答辩说明或学习困难；内容会进入教师待处理队列。选择“对AI结果有疑问”还会同时触发教师复核。"
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
            set_flash("反馈已提交，并已联动教师复核，可在学生报告中查看处理状态。"
                      if category == "对AI结果有疑问" else "反馈已提交给教师，可在学生报告中查看处理状态。")
            st.rerun()


def render_redefense_action(submission):
    """在学生报告中展示教师重新答辩要求及开始、继续或查看入口。"""
    request = db.get_latest_redefense_request(submission["id"])
    if not request or request["status"] == "已取消":
        return

    st.subheader("教师重新答辩要求")
    if request["status"] == "待开始":
        st.warning(f"教师要求你重新答辩：{request['reason']}")
        st.caption(
            "开始重答后，AI会参考上方教师意见和你的代码生成新问题；"
            "请围绕教师指出的重点准备说明。"
        )
        st.caption(f"要求时间：{request['requested_at']}。新答辩会保留原提交和原报告。")
        if not llm.is_configured():
            st.error("网页AI评价服务尚未配置，暂时无法生成重新答辩问题；请联系任课教师或系统管理员。")
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
    page_header("学生理解度报告", "查看答辩表现、教师复核及反馈处理结果。")
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
                f"提交#{record['id']} · 第{record.get('attempt_number', 1)}轮答辩"
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
    focused_feedback_id = st.session_state.get("student_task_feedback_id")
    if focused_feedback_id is not None:
        feedback = next((item for item in report.get("student_feedbacks", [])
                         if item["id"] == focused_feedback_id), None)
        if feedback is not None:
            st.subheader("教师回复")
            st.caption(f"{submission['problem']} · 提交#{submission_id} · 反馈#{feedback['id']}")
            with st.container(border=True, key="student_reply_focus"):
                st.markdown("**你提交的反馈**")
                st.write(feedback["content"])
                st.markdown("**教师处理结果**")
                if feedback.get("teacher_reply"):
                    st.write(feedback["teacher_reply"])
                else:
                    st.write(f"教师对这条反馈的处理状态：{feedback['status']}，暂未填写文字回复。")
                st.caption(f"处理教师：{feedback['replied_by']} · {feedback['replied_at']}")
            back_col, report_col, _ = st.columns([1, 1, 2])
            with back_col:
                if st.button("返回任务中心", width="stretch"):
                    navigate("任务中心")
            with report_col:
                if st.button("查看完整报告", width="stretch"):
                    st.session_state.student_task_feedback_id = None
                    st.session_state.navigation_scroll_pending = True
                    st.rerun()
            return
        st.session_state.student_task_feedback_id = None
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
            if not isinstance(score, (int, float)):
                continue
            totals[dimension] = totals.get(dimension, 0) + score
            counts[dimension] = counts.get(dimension, 0) + 1

    return {
        dimension: round(totals[dimension] / counts[dimension])
        for dimension in totals
        if counts[dimension] > 0
    }


def collapse_archive_report():
    """切换历史记录后收起详情，先核对所选提交再展开。"""
    st.session_state.archive_report_expanded = False


def show_learning_archive():
    page_header(
        "学生学习档案",
        "查看该学生各次实验答辩的AI评价及教师复核记录。",
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
    scored_records = [record for record in learning_records if record.get("overall") is not None]
    average_score = (
        round(
            sum(record["overall"] for record in scored_records)
            / len(scored_records)
        )
        if scored_records
        else "暂无完整评分"
    )
    latest_score = learning_records[-1]["overall"] if learning_records else None
    latest_weakest = (
        learning_records[-1]["weakest"]
        if learning_records and learning_records[-1].get("weakest")
        else "尚无报告"
    )
    score_delta = None
    if completed_count >= 2 and latest_score is not None and learning_records[-2]["overall"] is not None:
        score_delta = latest_score - learning_records[-2]["overall"]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("实验提交数", len(all_submissions))
    col2.metric("累计答辩记录", completed_count)
    col3.metric("平均AI答辩理解度", average_score)
    col4.metric("最近AI答辩理解度", latest_score if latest_score is not None else "暂无完整评分", delta=score_delta)
    st.caption(f"该学生最近一次答辩中AI评分相对较低的能力维度：{latest_weakest}")
    st.caption("平均分与趋势来自该学生有完整评分的AI答辩报告；证据不足不记作零分。不同实验考查内容可能不同，分数变化本身不能证明学习进步。四维平均分分别排除未评分项。")

    if not learning_records:
        st.info("已有实验提交，但暂时没有完成的答辩报告。")
        return

    indexed_records = list(enumerate(learning_records, start=1))
    st.caption(
        "累计答辩记录只统计已完成报告的记录，按提交顺序编号；答辩轮次属于各自的答辩流程，"
        "初次答辩为第1轮，教师要求重答后继续累计轮次。"
    )
    left, right = st.columns(2)
    with left:
        st.subheader("该学生各次答辩的AI理解度评分")
        trend_df = pd.DataFrame(
            {
                "累计答辩记录": range(1, completed_count + 1),
                "提交编号": [record["id"] for record in learning_records],
                "实验任务": [record.get("assignment_title", record.get("problem", "未记录")) for record in learning_records],
                "提交时间": [record.get("submitted_at", "未记录") for record in learning_records],
                "答辩轮次": [record.get("attempt_number", 1) for record in learning_records],
                "AI答辩理解度": [
                    record["overall"]
                    for record in learning_records
                ],
            }
        )
        trend_chart = build_defense_score_chart(trend_df)
        st.altair_chart(trend_chart, width="stretch")
        st.caption("柱形表示各次评分；至少两条完整评分时叠加折线。未评分记录保留空缺，不跨空缺连线。")
        if trend_df["AI答辩理解度"].notna().sum() == 1:
            st.caption("目前仅有一次完整评分，尚不能据此判断理解度的变化趋势。")

    with right:
        st.subheader("该学生答辩四维AI平均分")
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
            height=300,
            maximum=100,
        )

    st.subheader("历史记录")
    history_df = pd.DataFrame(
        [
            {
                "记录序号": sequence,
                "提交编号": record["id"],
                "答辩轮次": f"第{record.get('attempt_number', 1)}轮",
                "实验任务": record["assignment_title"],
                "AI答辩理解度": record["overall"] if record["overall"] is not None else "评价尚不完整",
                "AI答辩诊断等级": record["level"],
                "AI评分相对较低维度": record["weakest"],
                "AI是否建议教师复核": "是" if record["review_required"] else "否",
                "教师复核状态": teacher_review_status(record),
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
            for sequence, record in reversed(indexed_records)
        ]
    )
    render_centered_dataframe(history_df)

    st.subheader("查看历史详细报告")
    option_map = {
        (
            f"记录{sequence} · 提交#{record['id']} · {record['assignment_title']} · "
            f"第{record.get('attempt_number', 1)}轮 · AI答辩理解度 {understanding_text(record)}"
        ): record["id"]
        for sequence, record in reversed(indexed_records)
    }
    selected_label = st.selectbox(
        "选择报告",
        list(option_map),
        key="archive_report_select",
        on_change=collapse_archive_report,
    )
    selected_id = option_map[selected_label]
    expanded = st.toggle("展开所选报告", key="archive_report_expanded", value=False)
    if expanded:
        submission = db.get_submission(selected_id)
        report = db.get_report(selected_id)
        qa_records = db.get_qa_records(selected_id)
        if submission and report and submission["student_id"] == student_id:
            with st.container(border=True, key="archive_report_detail"):
                render_report(submission, report, qa_records)
        else:
            st.warning("所选报告暂时不可用，请重新选择。")
    else:
        st.caption("选择记录后打开开关，即可查看问答证据和教师复核结果。")

    st.caption(
        "只读Demo展示合成学生的学习档案。"
        if demo_mode.demo_mode_enabled()
        else "本页按当前登录的学生账号展示其本人档案；学生账号由教师核实名册后开通。"
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
                "权重": "" if item.get("weight") is None else str(item.get("weight", 0)),
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
    """Hidden cases only specify what must pass; historical weights are not edited."""
    st.caption("依次展开并填写测试点；未填写的卡片会自动忽略。标准输入和预期输出均可直接粘贴多行文本；每组输入最多200,000个字符，超出时会提示，不会截断。"
               "全部公开与隐藏测试均须通过，不设置测试分值。")
    cases = []
    for index in range(8):
        seed = test_cases[index] if index < len(test_cases) else {}
        title = f"测试点{index + 1}"
        if seed.get("name"):
            title += " · " + editor_text(seed["name"])
        with st.expander(title, expanded=index < max(1, len(test_cases))):
            name_col, group_col = st.columns(2)
            with name_col:
                name = st.text_input("样例名称（可留空）", value=assignment_editor_text_default(f"{key}_{index}_name", seed.get("name", "")),
                                     key=f"{key}_{index}_name", placeholder=f"留空时自动命名为测试{index + 1}")
            with group_col:
                group = st.text_input("测试分组（可留空）", value=assignment_editor_text_default(f"{key}_{index}_group", seed.get("group", "")),
                                      key=f"{key}_{index}_group", placeholder="基础功能 / 边界情况 / 性能要求")
            input_col, output_col = st.columns(2)
            with input_col:
                case_input = st.text_area("标准输入", value=assignment_editor_text_default(f"{key}_{index}_input", seed.get("input", "")),
                                          key=f"{key}_{index}_input", height=150,
                                          help="保留原始换行和空格；没有输入时可以留空。")
            with output_col:
                expected_output = st.text_area("预期输出", value=assignment_editor_text_default(f"{key}_{index}_output", seed.get("expected_output", "")),
                                               key=f"{key}_{index}_output", height=150,
                                               help="每一行输出都按题目要求填写。")
        if not any(value.strip() for value in [name, group, case_input, expected_output]):
            continue
        cases.append({"name": name.strip() or f"测试{index + 1}", "group": group.strip() or "基础功能",
                      "input": case_input, "expected_output": expected_output, "count_only": True})
    st.caption(f"当前共{len(cases)}组隐藏测试点，最多8组；启用验证至少配置1组。")
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
                value=assignment_editor_text_default(f"{key}_{index}_name", seed.get("name", "")),
                key=f"{key}_{index}_name",
                placeholder=f"留空时自动命名为样例{index + 1}",
            )
            input_col, output_col = st.columns(2)
            with input_col:
                sample_input = st.text_area(
                    "样例输入",
                    value=assignment_editor_text_default(f"{key}_{index}_input", seed.get("input", "")),
                    key=f"{key}_{index}_input",
                    height=130,
                )
            with output_col:
                sample_output = st.text_area(
                    "样例输出",
                    value=assignment_editor_text_default(f"{key}_{index}_output", seed.get("output", "")),
                    key=f"{key}_{index}_output",
                    height=130,
                )
            explanation = st.text_area(
                "样例说明（可选）",
                value=assignment_editor_text_default(f"{key}_{index}_explanation", seed.get("explanation", "")),
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
        weight = row.get("权重", 0)
        if isinstance(weight, str) and re.fullmatch(r"100|[1-9][0-9]?", weight):
            weight = int(weight)
        rubric.append(
            {
                "name": name,
                "weight": weight,
                "source": str(row.get("核查来源", "")).strip(),
                "description": str(row.get("评价说明", "")).strip(),
                "must_defend": bool(must_defend),
            }
        )
    return rubric


def render_rubric_editor(key, rubric):
    """显示动态评分点表；教师可增删1至8条评分点。"""
    # Streamlit 1.52 的数字/选项列忽略居中配置；文本列约束为整数，保存前还原数值。
    weight_column = {
        **st.column_config.TextColumn(
            "权重",
            width="small",
            validate=r"^(100|[1-9][0-9]?)$",
            max_chars=3,
            required=True,
            help="输入1至100的整数；全部评分点的权重合计应为100。",
        ),
        "alignment": "center",
    }
    edited = st.data_editor(
        rubric_to_dataframe(rubric),
        key=key,
        hide_index=True,
        num_rows="dynamic",
        width="stretch",
        column_config={
            "评分点": st.column_config.TextColumn("评分点", required=True),
            "权重": weight_column,
            "核查来源": st.column_config.SelectboxColumn(
                "核查来源",
                options=sorted(db.RUBRIC_SOURCES),
                required=True,
            ),
            "评价说明": st.column_config.TextColumn("评价说明", required=True),
            "必须答辩": st.column_config.CheckboxColumn("须通过答辩核查", help="该评分点需要通过学生问答核查；此项不表示学生必须答对才能提交。"),
        },
    )
    rubric_rows = dataframe_to_rubric(edited)
    total = 0
    for item in rubric_rows:
        if editor_value_is_blank(item["weight"]):
            continue
        try:
            total += round(float(item["weight"]))
        except (ValueError, TypeError, OverflowError):
            continue
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
            if len(normalized_cases) < 1:
                st.error("启用代码验证时请至少配置1组隐藏测试。")
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
        if len(assignment.get("test_cases") or []) < 1:
            blockers.append("启用代码验证的实验至少需要1组隐藏测试。")
        if not str(assignment.get("reference_code") or "").strip():
            blockers.append("尚未填写并自检教师参考答案。")
    return blockers


def restore_assignment_create_widgets(seed):
    """Send saved values to existing browser widgets, rather than only new defaults."""
    text_fields = {
        "title": "title", "description": "description", "input_format": "input_format",
        "output_format": "output_format", "constraints": "constraints_text",
        "reference_code": "reference_code",
    }
    for widget, field in text_fields.items():
        st.session_state[f"create_assignment_{widget}"] = editor_text(seed.get(field, ""))
    for field in ["requirements", "teaching_focus"]:
        st.session_state[f"create_assignment_{field}"] = "\n".join(seed.get(field, []))
    st.session_state.create_assignment_starter_code = editor_text(seed.get("starter_code", (
        "#include <iostream>\nusing namespace std;\n\nint main() {\n"
        "    // 请在这里完成代码\n    return 0;\n}\n"
    )))
    st.session_state.create_assignment_explanation_required = (
        "必做" if seed.get("explanation_required", True) else "选做")
    st.session_state.create_assignment_verification = bool(seed.get("verification_enabled", False))
    st.session_state.create_assignment_quality_confirmed = bool(seed.get("rubric_reviewed", False))
    st.session_state.create_assignment_hint_limit = seed.get("hint_limit", 2)
    standard = seed.get("cpp_standard", "auto")
    st.session_state.create_cpp_standard = next(
        label for label, value in CPP_STANDARD_OPTIONS.items() if value == standard)
    for field, limit, outputs in [
        ("public_samples", 5, [("name", "name"), ("input", "input"),
                               ("output", "output"), ("explanation", "explanation")]),
        ("test_cases", 8, [("name", "name"), ("group", "group"),
                           ("input", "input"), ("output", "expected_output")]),
    ]:
        items = seed.get(field, [])
        for index in range(limit):
            item = items[index] if index < len(items) else {}
            for widget, source in outputs:
                st.session_state[f"create_assignment_{field}_{index}_{widget}"] = editor_text(item.get(source, ""))


def assignment_editor_text_default(key, value):
    # Programmatic state restoration is the single source of initial create-form values.
    # Existing edit forms keep their defaults; ordinary reruns preserve teacher edits.
    if key.startswith("create_assignment_") and key in st.session_state:
        return ""
    return editor_text(value)


def prepare_assignment_create_draft():
    """Restore once per teacher/class draft, before creating any form widgets."""
    source = str(assignment_drafts.draft_path(db.current_database_path()))
    reset = st.session_state.pop("assignment_create_reset_requested", False)
    if reset or st.session_state.get("assignment_create_loaded_for") != source:
        document = None
        if not reset and not demo_mode.demo_read_only():
            try:
                document = assignment_drafts.load_draft(db.current_database_path())
            except (OSError, assignment_drafts.DraftError) as error:
                st.warning(f"草稿恢复失败：{error}")
        for key in list(st.session_state.keys()):
            if key.startswith(("create_assignment_", "create_rubric_", "create_cpp_")):
                st.session_state.pop(key, None)
        seed = document["data"] if document else {}
        restore_assignment_create_widgets(seed)
        st.session_state.assignment_create_seed = seed
        st.session_state.assignment_create_saved_at = document.get("saved_at", "") if document else ""
        st.session_state.assignment_create_loaded_for = source
        if "rubric" in seed:
            st.session_state.create_rubric_seed = seed["rubric"]
    return st.session_state.get("assignment_create_seed", {})


def save_assignment_create_draft(values):
    if demo_mode.demo_read_only():
        return False
    try:
        document = assignment_drafts.save_draft(db.current_database_path(), values)
    except (OSError, assignment_drafts.DraftError) as error:
        st.warning(f"草稿保存失败：{error}。重启前请先保留当前输入。")
        return False
    st.session_state.assignment_create_saved_at = document["saved_at"]
    st.session_state.assignment_create_seed = document["data"]
    return True


def install_assignment_draft(values):
    """Fill a new draft; keep a recoverable copy of the previous saved draft."""
    previous = assignment_drafts.load_draft(db.current_database_path())
    if previous:
        archive = db.current_database_path().resolve().with_suffix(".assignment_draft_backups")
        write_json(archive / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".json"), previous)
    assignment_drafts.save_draft(db.current_database_path(), values)
    st.session_state.assignment_create_loaded_for = None
    st.session_state.pop("assignment_create_reset_requested", None)


def render_experiment_package_import():
    with st.expander("导入完整实验", expanded=False):
        st.caption("导入内容先进入未发布草稿。请先保存正在编辑的内容；已保存的旧草稿会另存备份。")
        uploaded = chinese_file_uploader(
            "选择完整实验JSON文件", file_type="json", key="experiment_package_file",
            limit_text="仅支持JSON，每个实验包最多1MB",
            help="将完整实验JSON文件拖到上传框，或点击“选择文件”。支持UTF-8编码，每个文件最多1MB。",
        )
        oversized = uploaded is not None and uploaded.size > 1_000_000
        if oversized:
            st.error("实验包文件过大，请选择不超过1MB的JSON文件。")
        pasted = st.text_area("或粘贴完整实验JSON", key="experiment_package_json", height=120)
        source = (uploaded.getvalue() if not oversized else b"") if uploaded is not None else pasted.encode("utf-8")
        source_hash = hashlib.sha256(source).hexdigest()
        if st.button("检查实验包", key="experiment_package_check", disabled=oversized or demo_mode.demo_read_only()):
            try:
                if uploaded is not None and pasted.strip():
                    raise ValueError("请只选择文件或粘贴JSON中的一种输入方式。")
                values = experiment_packages.parse(source)
                st.session_state.experiment_package_preview = (source_hash, values)
            except ValueError as error:
                st.session_state.pop("experiment_package_preview", None)
                st.error(str(error))
        preview = st.session_state.get("experiment_package_preview")
        if preview and preview[0] == source_hash and not oversized:
            values = preview[1]
            st.write("待导入：" + values["title"])
            st.caption(f"{len(values['rubric'])}个评分点 · {len(values['public_samples'])}组公开样例 · "
                       f"{len(values['test_cases'])}组隐藏测试 · 解题思路{'必做' if values['explanation_required'] else '选做'}")
            if st.button("载入未发布草稿", key="experiment_package_load", disabled=demo_mode.demo_read_only()):
                try:
                    install_assignment_draft(values)
                except (OSError, ValueError) as error:
                    st.error("导入未完成：" + str(error))
                else:
                    set_flash(f"“{values['title']}”已保存为未发布草稿，内容已填入本页下方表单。")
                    st.rerun()
        with st.expander("示例实验包（可选）", expanded=False):
            st.caption("选择排序仅用于参考完整实验的配置。点击后载入未发布草稿，不会自动发布；正常发布实验无需载入示例。")
            if st.button("载入选择排序完整案例", key="experiment_package_selection_sort", disabled=demo_mode.demo_read_only()):
                try:
                    values = experiment_packages.parse((Path(__file__).resolve().parent /
                        "test_materials/selection_sort/experiment_config.json").read_bytes())
                    install_assignment_draft(values)
                except (OSError, ValueError) as error:
                    st.error("案例载入未完成：" + str(error))
                else:
                    set_flash("选择排序的题面、评分点、样例和参考代码已载入草稿，尚未发布。")
                    st.rerun()


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
    col1.metric("可见实验总数", len(assignments))
    col2.metric("已发布实验数", published_count)
    col3.metric("已停用实验数", len(assignments) - published_count)

    create_tab, manage_tab = st.tabs(["发布新实验", "管理已有实验"])

    with create_tab:
        draft_seed = prepare_assignment_create_draft()
        render_experiment_package_import()
        st.info(
            "每行填写一条实验要求或教学重点；再用评分点定义AI初评依据。"
            "隐藏教学重点不会展示给学生。"
        )
        saved_at = st.session_state.get("assignment_create_saved_at", "")
        if saved_at:
            st.subheader("未发布草稿")
            draft_title = str(draft_seed.get("title") or "未命名实验")
            st.info(f"正在编辑：{draft_title}。下方表单就是草稿，可继续修改并保存；点击“发布实验”后学生才能看到。")
            st.caption(f"上次保存：{saved_at.replace('T', ' ')}")
        st.caption(
            "填写后可点击“保存草稿”；点击“发布实验”也会先保存草稿。"
            "保存后可在服务重启或重新登录后继续编辑。"
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
            title = st.text_input(
                "实验名称", value="",
                key="create_assignment_title", placeholder="例如：判断回文字符串",
            )
            description = st.text_area(
                "实验描述",
                value="",
                key="create_assignment_description",
                placeholder="说明程序需要接收什么输入、完成什么任务、输出什么结果。",
                height=100,
            )
            st.markdown("**OJ题面设置（学生可见）**")
            input_format = st.text_area(
                "输入格式（可留空）",
                value="",
                key="create_assignment_input_format",
                placeholder="例如：第一行输入整数n，第二行输入n个整数。",
                height=90,
                max_chars=3000,
            )
            output_format = st.text_area(
                "输出格式（可留空）",
                value="",
                key="create_assignment_output_format",
                placeholder="例如：输出数组中的最大值。",
                height=90,
                max_chars=3000,
            )
            constraints_text = st.text_area(
                "数据范围与说明（可留空）",
                value="",
                key="create_assignment_constraints",
                placeholder="例如：1 ≤ n ≤ 100；所有输入均为整数。",
                height=90,
                max_chars=3000,
            )
            st.markdown("**公开输入输出样例（学生可见，可留空）**")
            create_public_samples = render_public_sample_editor(
                "create_assignment_public_samples",
                draft_seed.get("public_samples", []),
            )
            requirements_text = st.text_area(
                "实验要求（每行一条）",
                value="",
                key="create_assignment_requirements",
                placeholder="使用字符串保存输入\n使用循环比较字符\n考虑空字符串情况",
                height=130,
            )
            teaching_focus_text = st.text_area(
                "隐藏教学重点（每行一条）",
                value="",
                key="create_assignment_teaching_focus",
                placeholder="循环边界是否正确\n下标是否越界\n能否解释时间复杂度",
                height=130,
            )
            create_explanation_mode = st.selectbox(
                "解题思路要求", ["必做", "选做"],
                index=0,
                key="create_assignment_explanation_required",
                help="选做允许留空，AI仍通过代码、运行证据和答辩核验理解；必做要求提交思路。",
            )
            create_explanation_required = create_explanation_mode == "必做"
            st.markdown("**评分点与证据来源**")
            create_rubric = render_rubric_editor(
                "create_assignment_rubric",
                create_rubric_seed,
            )
            st.markdown("**代码验证设置**")
            create_cpp_standard_label = st.selectbox(
                "C++编译标准",
                list(CPP_STANDARD_OPTIONS),
                index=0,
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
                key="create_assignment_verification",
                help="需要在.env中允许本机代码执行并配置g++；不可用于无隔离的公开服务。",
            )
            create_test_cases = render_test_case_editor(
                "create_assignment_test_cases",
                draft_seed.get("test_cases", []),
            )
            st.caption(
                "隐藏测试默认留白；启用后至少填写1组，可按基础功能、边界情况、"
                "性能要求等分组。测试点不计分，正式评测必须全部通过。"
            )
            create_reference_code = st.text_area(
                "教师参考答案（仅教师可见）",
                value="",
                key="create_assignment_reference_code",
                placeholder="启用代码验证时必填；发布前会实际跑完隐藏测试和公开样例。",
                height=300,
                max_chars=12000,
            )
            st.markdown("**学习支持与开始AI答辩的条件**")
            create_hint_limit = st.number_input(
                "每名学生可使用的AI提示次数",
                min_value=0,
                max_value=5,
                value="min",
                key="create_assignment_hint_limit",
                step=1,
                help="按学号和实验累计；设为0表示关闭AI提示。",
            )
            create_defense_admission, create_defense_threshold = "全部通过", 100
            st.caption("开始AI答辩的条件：全部公开与隐藏测试通过；测试点只判通过，不设置分数线。")
            starter_code = st.text_area(
                "起始代码（可留空）",
                value="",
                key="create_assignment_starter_code",
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
                    key="create_assignment_quality_confirmed",
                )
            else:
                create_quality_confirmed = True
            save_draft_submitted = st.form_submit_button(
                "保存草稿", width="stretch",
            )
            create_submitted = st.form_submit_button(
                "发布实验",
                type="primary",
                width="stretch",
            )

        if save_draft_submitted or create_submitted:
            requirements = lines_to_list(requirements_text)
            teaching_focus = lines_to_list(teaching_focus_text)
            draft_saved = save_assignment_create_draft({
                "title": title, "description": description,
                "requirements": requirements, "teaching_focus": teaching_focus,
                "rubric": create_rubric,
                "input_format": input_format, "output_format": output_format,
                "constraints_text": constraints_text, "public_samples": create_public_samples,
                "verification_enabled": create_verification_enabled,
                "test_cases": create_test_cases, "reference_code": create_reference_code,
                "cpp_standard": create_cpp_standard, "hint_limit": create_hint_limit,
                "defense_admission": create_defense_admission,
                "defense_score_threshold": create_defense_threshold,
                "starter_code": starter_code, "rubric_reviewed": create_quality_confirmed,
                "explanation_required": create_explanation_required,
            })
            if save_draft_submitted and draft_saved:
                set_flash("草稿已保存；重启或重新登录后可继续填写。")
                st.rerun()

        if create_submitted:
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
                            if draft_saved:
                                st.info("当前实验已保存为草稿；修复运行环境后可继续发布，无需重新填写。")
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
                        explanation_required=create_explanation_required,
                    )
                except (ValueError, sqlite3.Error) as error:
                    st.error(f"保存实验失败：{error}")
                else:
                    try:
                        assignment_drafts.clear_draft(db.current_database_path())
                    except OSError:
                        st.warning("实验已发布，但旧草稿未能清理，请避免重复发布。")
                    st.session_state.assignment_create_reset_requested = True
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
        status_col.info(f"该实验发布状态：{assignment['status']}")
        time_col.info(f"最后修改：{assignment['updated_at']}")
        copy_col, export_col = st.columns(2)
        if copy_col.button("复制为新实验草稿", key=f"copy_assignment_{assignment['id']}",
                           disabled=demo_mode.demo_read_only(), width="stretch"):
            try:
                install_assignment_draft(experiment_packages.from_assignment(assignment, copy=True))
            except (OSError, ValueError) as error:
                st.error("复制未完成：" + str(error))
            else:
                set_flash("副本已保存到“发布新实验”草稿，请切换标签核对；原实验保持原样。")
                st.rerun()
        try:
            export_col.download_button("导出完整实验JSON", experiment_packages.export(assignment),
                file_name=f"wuma_experiment_{assignment['id']}.json", mime="application/json",
                key=f"export_assignment_{assignment['id']}", width="stretch")
        except ValueError as error:
            st.caption("当前实验需修正后才能导出：" + str(error))
        st.caption("完整实验文件包含教师参考答案和隐藏测试，仅供教师备课使用。")
        existing_blockers = assignment_publish_blockers(assignment)
        if existing_blockers:
            st.warning(
                "当前配置需要修正后才能用于新的AI答辩："
                + insights.join_clauses(existing_blockers)
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
            edit_explanation_mode = st.selectbox(
                "解题思路要求", ["必做", "选做"],
                index=0 if assignment.get("explanation_required", True) else 1,
                key=f"edit_assignment_explanation_required_{assignment['id']}",
                help="保存后影响后续提交；历史提交保留当时要求。",
            )
            edit_explanation_required = edit_explanation_mode == "必做"
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
                "启用后至少填写1组；新提交只统计通过情况，历史提交保留原始计分证据。"
            )
            edit_reference_code = st.text_area(
                "教师参考答案（仅教师可见）",
                value=assignment.get("reference_code", ""),
                placeholder="启用代码验证时必填；保存前会实际跑完隐藏测试和公开样例。",
                height=300,
                max_chars=12000,
                key=f"edit_reference_code_{assignment['id']}",
            )
            st.markdown("**学习支持与开始AI答辩的条件**")
            edit_hint_limit = st.number_input(
                "每名学生可使用的AI提示次数",
                min_value=0,
                max_value=5,
                value=int(assignment.get("hint_limit", 2)),
                step=1,
                key=f"edit_hint_limit_{assignment['id']}",
                help="修改后影响本实验后续提示请求；已经使用的次数不会清零。",
            )
            edit_defense_admission, edit_defense_threshold = "全部通过", 100
            st.caption("开始AI答辩的条件：全部公开与隐藏测试通过；旧报告的计分证据保持原样。")
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
                        explanation_required=edit_explanation_required,
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
    if record.get("overall") is None and record.get("status") != "已完成" and not record.get("report_created_at"):
        return "待完成答辩"
    if record.get("pending_ai_dispute_count"):
        return "待复核"
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
    if st.session_state.get(decision_key) == "认可AI诊断" and ai_score is not None:
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
            f"提交#{parent_id}（第{parent_submission.get('attempt_number', 1)}轮）"
            f" → 提交#{submission['id']}（第{submission.get('attempt_number', 2)}轮）"
        )

    if parent_report and report:
        old_score = parent_report["overall"]
        new_score = report["overall"]
        col1, col2, col3 = st.columns(3)
        col1.metric("上次AI答辩理解度", understanding_text(parent_report))
        col2.metric("本次AI答辩理解度", understanding_text(report))
        col3.metric("AI答辩理解度分数变化", new_score - old_score if new_score is not None and old_score is not None else "不可比较")

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
                st.write("参考知识点：", insights.join_clauses(item.get("reference_points") or []))
                st.write("AI评价：", insights.evaluation(item).get("feedback") or "未记录")
                st.divider()


def build_teacher_dataframe(records):
    rows = []
    for record in records:
        rows.append(
            {
                "提交编号": record["id"],
                "答辩轮次": f"第{record.get('attempt_number', 1)}轮",
                "学号": record["student_id"],
                "姓名": record["name"],
                "实验任务": record.get("assignment_title", record["problem"]),
                "答辩进度": record["status"],
                "本次代码测试结果": (
                    (
                        f"{record['code_verification'].get('overall_status', '未执行')} "
                        f"{record['code_verification'].get('passed', 0)}/"
                        f"{record['code_verification'].get('total', 0)}"
                    )
                    if record.get("code_verification")
                    else "未记录"
                ),
                "历史代码测试得分": (
                    (
                        f"{record['code_verification'].get('score', 0)}/"
                        f"{record['code_verification'].get('max_score', 0)}"
                    )
                    if record.get("code_verification", {}).get("max_score")
                    else "不计分" if record.get("code_verification", {}).get("scoring_mode") == "pass_only"
                    else "历史未计分"
                ),
                "AI评分点证据初评": (
                    record.get("completion_score")
                    if record.get("completion_score") is not None
                    else "历史记录"
                ),
                "AI答辩理解度": record["overall"] if record["overall"] is not None else understanding_text(record),
                "AI评分相对较低维度（历史报告）": record["weakest"] if record["weakest"] else "未记录",
                "AI复核建议": (
                    "建议复核"
                    if record.get("review_required")
                    else "常规抽查"
                ),
                "教师复核状态": teacher_review_status(record),
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
         "AI建议的排查方向摘要": shorten_teacher_text((item.get("hint") or {}).get("diagnosis"), 40),
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
            st.write("AI建议的排查方向：", hint.get("diagnosis", ""))
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
    score_text = "仅判通过" if verification.get("scoring_mode") == "pass_only" else f"{score}/{max_score}" if max_score else "—"
    return result_text, score_text


def teacher_record_needs_attention(record):
    """判断一条提交是否应进入教师默认待办视图。"""
    verification = record.get("code_verification") or {}
    code_status = verification.get("overall_status", "未执行")
    attempt_number = int(record.get("attempt_number") or 1)
    needs_review = bool(record.get("_needs_teacher_review")) or bool(record.get("pending_ai_dispute_count")) or (
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
        labels.append(f"教师待处理学生反馈{record['pending_feedback_count']}条")
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
        labels.append("本次代码测试未全部通过")
    if not labels:
        labels.append("教师复核已保存" if record.get("teacher_reviewed") else "可由教师进行常规抽查")
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
                "本次代码测试结果": code_result,
                "历史代码测试得分": code_score,
                "AI评分点证据初评": (
                    record.get("completion_score")
                    if record.get("completion_score") is not None
                    else "—"
                ),
                "AI答辩理解度": (
                    record.get("overall")
                    if record.get("overall") is not None
                    else understanding_text(record)
                ),
                "AI对该学生本次答辩要点的判断": insights.mastery_statement((record.get("_mastery") or {}).get("status", "待核查")),
                "教师处理提示": teacher_attention_label(record),
            }
        )
    return pd.DataFrame(rows)


def render_teacher_status_overview(status_table, records, highlighted_rows=(), include_experiment=False):
    """保留原始状态表，主页面只显示处理当前提交所需的五项信息。"""
    review_states = {f"#{record['id']}": teacher_review_status(record) for record in records}
    rows = []
    for _, item in status_table.iterrows():
        information = f"{item['学生']}\n提交编号：{str(item['提交']).lstrip('#')}"
        if include_experiment:
            information += "\n" + str(item['实验'])
        rows.append({
            "提交信息": information,
            "代码测试结果": item["本次代码测试结果"],
            "AI答辩理解度": item["AI答辩理解度"],
            "教师复核状态": review_states[item["提交"]],
            "当前需处理": item["教师处理提示"],
        })
    render_centered_dataframe(pd.DataFrame(rows), highlighted_rows=highlighted_rows, evidence_kind="teacher-status")


def test_case_statistics_html(statistics, records):
    """按实际通过次数呈现状态颜色；计分说明只影响显示，不修改统计数据。"""
    modes = {}
    for record in records:
        verification = record.get("code_verification") or {}
        if not verification or verification.get("overall_status") in {"未启用", "环境不可用", "编译环境不兼容", "未执行"}:
            continue
        title = record.get("assignment_title", record.get("problem", ""))
        modes.setdefault(title, set()).add("pass_only" if verification.get("scoring_mode") == "pass_only" else "weighted")
    headers = ["实验任务", "测试分组", "测试点", "测试点分值", "通过次数", "判定次数", "通过率"]
    rows = []
    for _, item in statistics.iterrows():
        passed, judged = int(item["通过次数"]), int(item["判定次数"])
        mode = modes.get(item["实验任务"], set())
        weight = "不计分" if mode == {"pass_only"} else "规则不同" if len(mode) > 1 else str(item["分值"])
        cells = [item["实验任务"], item["测试分组"], item["测试点"], weight, passed, judged]
        content = "".join(
            f'<td data-label="{headers[index]}">{html.escape(str(value))}</td>'
            for index, value in enumerate(cells)
        )
        if judged <= 0:
            tone, label, rate = "neutral", "暂无判定", "—"
        else:
            tone, label = ("pass", "全部通过") if passed == judged else ("fail", "未通过") if passed == 0 else ("partial", "部分通过")
            rate = f"{passed * 100 / judged:.1f}".rstrip("0").rstrip(".") + "%"
        sample_note = '<div class="wuma-sample-note">样本较少</div>' if 0 < judged < 3 else ""
        content += (
            f'<td data-label="通过率"><span class="wuma-rate-badge wuma-rate-{tone}">{rate}</span>'
            f'<div class="wuma-rate-label">{label}</div>{sample_note}</td>'
        )
        rows.append(f"<tr>{content}</tr>")
    heading = "".join(f"<th>{label}</th>" for label in headers)
    return (
        '<div class="wuma-table-wrapper"><table class="wuma-data-table wuma-evidence-table wuma-evidence-statistics">'
        f'<thead><tr>{heading}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


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
    if record.get("pending_ai_dispute_count"):
        teacher_result = "学生对AI结果有疑问，待复核"
    elif record.get("teacher_reviewed"):
        teacher_result = (
            f"{record.get('teacher_decision') or '已复核'} · "
            f"{confirmed_score_text(record.get('teacher_confirmed_overall'))}"
        )
    else:
        teacher_result = "尚无教师复核记录"

    conclusion_parts = [f"代码 {code_result}（{code_score}）"]
    conclusion_parts.append(
        f"AI评分点证据初评 {preliminary_score}/100"
        if preliminary_score is not None
        else "AI评分点证据初评暂无"
    )
    conclusion_parts.append(
        f"AI答辩理解度 {understanding}/100"
        if understanding is not None
        else understanding_text(report)
    )
    conclusion_parts.append(f"教师结论 {teacher_result}")

    unique_concerns = insights.ranked_concerns(record, preliminary_review, report, qa_records)
    pending_feedbacks = [item for item in feedbacks if item.get("status") == "待处理"]

    actions = []
    if pending_feedbacks:
        actions.append("先处理学生待回复反馈")
    if report is None:
        actions.append("等待学生完成答辩后再作理解度复核")
    elif not record.get("teacher_reviewed") or record.get("pending_ai_dispute_count"):
        actions.append("核对关键证据并保存教师复核结论")
    if record.get("redefense_status") in {"待开始", "答辩中"}:
        actions.append("跟进重新答辩进度")
    if not actions:
        actions.append("该提交的教师复核已保存，按需查看完整证据")

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
        "conclusion": insights.join_clauses(conclusion_parts, sentence=True),
        "concerns": unique_concerns,
        "action": insights.join_clauses(actions, sentence=True),
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
            status, separator, detail = item.partition("｜")
            if separator and status in {"要点已掌握", "需要巩固", "证据不足", "待核查", "相对较弱（整体良好）"}:
                st.write("- AI根据该学生本次答辩判断：" + ai_mastery_status_text(status) + "。" + detail)
            else:
                st.write(f"- {item}")
        st.info("建议处理：" + digest["action"])
        st.caption("摘要证据来源：" + digest["evidence"] + "。完整原文默认折叠。")


def render_teacher_mastery(report, qa_records):
    """通栏展示该学生的掌握证据，原始问答按题折叠。"""
    st.subheader("AI对本次答辩的判断与作答证据")
    mastery = insights.mastery_summary(report, qa_records)
    st.write("AI根据该学生本次答辩作出的判断：" + ai_mastery_status_text(mastery["status"]))
    st.write("判断依据：" + mastery["text"])
    if report and report.get("dimension_evidence"):
        render_dimension_evidence(report)
    elif report and report.get("dimensions"):
        render_centered_dataframe(pd.DataFrame([
            {"能力维度": name, "AI理解度": f"{score}/100",
             "关注": "相对较低（不等于未掌握）" if name == mastery.get("dimension") else "见逐题证据"}
            for name, score in report["dimensions"].items()
        ]))
    else:
        st.info("该学生本次答辩的完整AI诊断尚未生成，可先核查已有回答。")
    st.caption("分数是AI判断；结合逐题回答与遗漏点核查，未回答不等同于未掌握。")
    st.caption("AI判断把握是模型对本次评价的自评，不代表学生掌握程度或实际准确率。")
    if not qa_records:
        st.info("该提交暂无答辩问答。")
    for index, item in enumerate(qa_records, start=1):
        evaluation = item.get("final_evaluation") or item.get("initial_evaluation") or {}
        score = evaluation.get("score", "待评价") if item.get("answer") else "待回答"
        if evaluation.get("premise_issue"):
            score = "题目前提待教师核查"
        confidence = evaluation.get("confidence") or "未记录"
        score_text = f"{score}/100" if isinstance(score, (int, float)) else str(score)
        st.markdown(f"**第{index}题 · {item.get('dimension', '综合理解')} · {score_text}**")
        _, status, detail = insights.question_finding(item, index)
        tone = "warning" if status == "需要巩固" else "neutral" if status in {"证据不足", "待核查"} else "positive"
        display_status = ai_mastery_status_text(status, "本题考查的要点")
        render_reading_note(f"AI判断：{display_status}", detail, tone=tone,
                            meta=f"AI判断把握：{confidence}")
        with st.expander(
            f"核查第{index}题 · AI判断：{display_status} · AI对本题回答的理解度评分：{score_text}",
            expanded=False,
        ):
            st.info(f"**答辩问题**\n\n{item.get('question', '')}")
            render_question_reason(item)
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
            for label, field in [("AI首次评价", "initial_evaluation"), ("AI最终评价", "final_evaluation")]:
                evidence = item.get(field) or {}
                if not evidence:
                    continue
                with st.container(border=True):
                    st.markdown(f"#### {label}与证据结论")
                    st.markdown(
                        f"**评价依据**\n\n{evidence.get('feedback', '')}"
                    )
                    premise_pending = render_premise_notice(evidence)
                    label = "AI对本题回答的原始分（暂不计入答辩理解度）" if premise_pending else "AI对本题回答的理解度评分"
                    st.caption(f"{label}：{evidence.get('score', '—')} · AI自评判断把握：{evidence.get('confidence', '—')}")
                    for title, key in [("AI标记的回答遗漏点", "missing_points"), ("AI标记的学生理解错误", "misconceptions")]:
                        if evidence.get(key):
                            st.write(("AI原始标记（待核查）·" if premise_pending else "") + title + "：", insights.join_clauses(evidence[key]))
                    for point in evidence.get("point_assessments", []):
                        st.write(f"AI对该学生回答的逐点标记：{point.get('status', '')}；考查要点：{point.get('reference_point', '')}；AI引用依据：{point.get('evidence', '')}")
            with st.container(border=True):
                st.markdown("#### 教师参考要点")
                for point in item.get("reference_points", []):
                    st.write(f"- {point}")


def render_teacher_full_evidence(submission, preliminary_review, report, qa_records):
    """长篇材料分类折叠，逐题回答由掌握证据区提供。"""
    st.subheader("其他原始材料（按需展开）")
    with st.expander("查看学生代码、解题思路与实验报告", expanded=False):
        st.caption("下方保留该次提交的学生原文，供教师结合实际代码核查。")
        render_evidence_block("学生提交的解题思路", submission["explanation"], "answer", meta="提交原文")
        if submission.get("lab_report"):
            render_evidence_block("学生提交的选做实验报告", submission["lab_report"], "answer", meta="提交原文")
        else:
            render_evidence_block("选做实验报告", "学生未填写（不因此扣分）。", "source")
        st.caption("学生提交的代码（原文）")
        st.code(submission["code"], language="cpp")
    with st.expander("查看代码测试结果与运行证据", expanded=False):
        render_code_verification(submission.get("code_verification"), teacher_view=True)
    with st.expander("查看评分点初评依据", expanded=False):
        render_preliminary_review(preliminary_review)
    if report:
        with st.expander("查看AI完整诊断原文", expanded=False):
            st.write("诊断总结：", report.get("summary", ""))
            st.write("学习建议：", report.get("suggestion", ""))
            if report.get("review_reasons"):
                st.write("建议复核原因：", insights.join_clauses(report["review_reasons"]))
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
            render_teaching_focus(snapshot.get("teaching_focus", []))
            st.caption("编译配置 · C++编译标准：" + verifier.cpp_standard_label(snapshot.get("cpp_standard", "auto")))
    if submission.get("parent_submission_id"):
        with st.expander("查看重新答辩前后对比", expanded=False):
            render_redefense_comparison(submission, report, qa_records)


def render_teacher_evidence_workspace(submission, preliminary_review, report, qa_records):
    """统一详情入口，长篇问答、代码和证据表使用页面整行宽度。"""
    st.subheader("学生作答与复核证据")
    st.caption("请先核查下方回答和原始材料，再在上方保存教师复核结论。")
    with st.expander("核查逐题回答与判断依据", expanded=False):
        render_teacher_mastery(report, qa_records)
    render_teacher_full_evidence(submission, preliminary_review, report, qa_records)


def render_teacher_student_case(record, focused_task=None):
    """待办和班级分析共用同一份学生详情与处理入口。"""
    selected_id = record["id"]
    submission = db.get_submission(selected_id)
    qa_records = db.get_qa_records(selected_id)
    report = db.get_report(selected_id)
    preliminary_review = db.get_preliminary_review(selected_id)
    review_history = db.list_teacher_review_history(selected_id)
    student_feedbacks = db.list_student_feedbacks(submission_id=selected_id)
    st.subheader(f"当前学生 · {submission['name']} · {submission['student_id']}")
    st.caption(f"{record.get('assignment_title', submission['problem'])} · 提交#{selected_id} · 第{submission.get('attempt_number', 1)}轮答辩 · {submission['submitted_at']}")
    if focused_task is not None:
        if focused_task["action"] == "process_feedback":
            with st.container(border=True, key="teacher_feedback_focus"):
                render_teacher_feedback_actions(student_feedbacks,
                                                focused_feedback_id=focused_task.get("feedback_id"))
            if record.get("pending_ai_dispute_count"):
                st.info("本次AI结果仍待教师复核。回复反馈或标记已阅后，复核待办会保留，直到保存教师复核结论。")
                review_task = next((task for task in db.list_teacher_tasks(assignment_id=record.get("assignment_id"))
                                    if task["submission_id"] == selected_id and task["action"] == "review_submission"), None)
                if review_task:
                    st.button("前往对应教师复核", key=f"feedback_review_jump_{selected_id}",
                              on_click=open_teacher_task, args=(review_task,), width="stretch")
            with st.expander("查看本次诊断与答辩依据", expanded=False):
                render_teacher_digest(record, preliminary_review, report, qa_records, student_feedbacks)
            render_teacher_evidence_workspace(submission, preliminary_review, report, qa_records)
        elif focused_task["action"] == "review_submission":
            with st.container(border=True, key="teacher_review_focus"):
                render_teacher_review_actions(selected_id, report, review_history)
            render_teacher_digest(record, preliminary_review, report, qa_records, student_feedbacks)
            render_teacher_evidence_workspace(submission, preliminary_review, report, qa_records)
        else:
            st.subheader("重新答辩进度")
            st.write(focused_task["title"])
            st.caption(f"当前答辩状态：{record['status']}")
            render_teacher_digest(record, preliminary_review, report, qa_records, student_feedbacks)
            render_teacher_evidence_workspace(submission, preliminary_review, report, qa_records)
        return
    with st.container(border=True):
        render_teacher_review_actions(selected_id, report, review_history)
    render_teacher_digest(record, preliminary_review, report, qa_records, student_feedbacks)
    render_teacher_evidence_workspace(submission, preliminary_review, report, qa_records)
    render_teacher_feedback_actions(student_feedbacks)
    st.caption("AI诊断用于辅助核查，教师应结合学生回答与原始证据作出结论。")


def render_teacher_review_actions(selected_id, report, review_history):
    """在当前学生下方直接完成教师复核。"""
    st.subheader("完成教师复核")
    if not report:
        st.info("学生完成答辩并生成AI报告后，才能保存教师复核结论。")
        return

    current_record = db.list_submissions(submission_id=selected_id)[0]
    if current_record.get("pending_ai_dispute_count"):
        st.warning("学生对AI结果有疑问，请结合反馈原文、实际代码与答辩证据核查。保存本次复核结论后，才会完成对应复核待办。")
        with st.expander("查看学生对AI结果的疑问", expanded=True):
            latest_review = review_history[0] if review_history else None
            for feedback in insights.pending_ai_disputes(
                db.list_student_feedbacks(submission_id=selected_id), latest_review,
            ):
                st.markdown(f"**反馈#{feedback['id']} · {feedback['status']} · {feedback['created_at']}**")
                st.write(feedback["content"])
                if feedback.get("teacher_reply"):
                    st.caption("已回复：" + feedback["teacher_reply"])
    latest_review = review_history[0] if review_history else None
    if latest_review:
        st.success(
            f"最新教师复核结论：{latest_review['decision']} · "
            f"教师确认理解度：{confirmed_score_text(latest_review['confirmed_overall'])}"
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
    elif insights.teacher_review_state(
        current_record, report, db.get_qa_records(selected_id),
    )["是否当前待办"]:
        st.warning("该记录尚待教师复核，请核查答辩证据并保存结论。")
    else:
        st.info("该记录可进行常规抽查。")

    decisions = [
        "认可AI诊断",
        "调整理解度结论",
        "要求学生重新答辩",
    ]
    if report.get("overall") is None:
        decisions.remove("认可AI诊断")
        st.info("AI评价尚不完整，不能自动认可总分。可核查原文后填写教师结论，或要求学生重新答辩。")
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
    if st.session_state.get(decision_key) not in decisions:
        st.session_state[decision_key] = default_decision
    if score_key not in st.session_state:
        st.session_state[score_key] = int(default_score) if default_score is not None else None
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
        st.info(
            "学生启动重答后，AI会参考下方复核意见生成新问题。请写明需要核查的重点；"
            "生成题目可能改写或未完全覆盖教师要求。"
        )
        st.caption("要求重答时，教师确认理解度可留空。")

    with st.form(f"teacher_workspace_review_form_{selected_id}"):
        reviewer_name = st.text_input(
            "复核教师",
            value=st.session_state.auth_name,
            disabled=True,
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
        st.caption("复核意见保存后，学生可在自己的报告中查看。")
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
                    f"{confirmed_score_text(item['confirmed_overall'])}**"
                )
                if item.get("comment"):
                    st.write(item["comment"])
                else:
                    st.caption("本次未填写文字意见。")
                st.caption(f"{item['reviewer_name']} · {item['reviewed_at']}")


def render_teacher_feedback_actions(student_feedbacks, focused_feedback_id=None):
    """在当前学生下方处理反馈，默认不展开长内容。"""
    st.subheader("处理学生反馈")
    if not student_feedbacks:
        st.info("该提交目前没有学生反馈。")
        return

    if focused_feedback_id is not None:
        student_feedbacks = [item for item in student_feedbacks if item["id"] == focused_feedback_id]
        if not student_feedbacks:
            st.warning("未找到这条反馈，请返回待办查看最新任务。")
            return
    pending_count = sum(item["status"] == "待处理" for item in student_feedbacks)
    st.caption(
        f"正在查看反馈#{focused_feedback_id}，保存后会保留当前处理结果。"
        if focused_feedback_id is not None else
        f"共{len(student_feedbacks)}条反馈，待处理{pending_count}条；点击对应反馈后才显示完整内容。"
    )
    forced_feedback_id = focused_feedback_id or st.session_state.get("teacher_task_feedback_id")
    for item in student_feedbacks:
        request_text = "希望回复" if item["reply_requested"] else "无需回复"
        with st.expander(
            f"反馈#{item['id']} · {item['category']} · {item['status']} · {request_text}",
            expanded=item["id"] == forced_feedback_id,
        ):
            st.write(item["content"])
            st.caption(f"提交时间：{item['created_at']}")
            if item["category"] == "对AI结果有疑问":
                st.caption("此类反馈会联动教师复核。回复或标记已阅不能代替复核；请在“完成教师复核”中保存核查结论。")
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
        col2.metric("代码测试需核查提交数", judging_attention_count)
        col3.metric("待教师复核", pending_review_count)
        col4.metric("教师待处理反馈数", pending_feedback_count)
        st.caption(
            "评价口径：代码验证检查全部公开与隐藏样例的通过情况，旧报告保留历史代码得分；"
            "评分点证据初评来自代码、思路与选做报告；答辩理解度来自学生问答；"
            "教师确认理解度来自人工复核。四项结果互不相加。"
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
            f"提交编号：{record['id']} · {record['name']} · {record['student_id']} · "
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
        "选择要处理的提交",
        labels,
        key="teacher_record_selector",
    )
    st.caption("提交编号是系统生成的唯一编号，筛选后不要求连续；同一学生可有多条提交。")
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
    render_teacher_status_overview(status_table, table_records, highlighted_rows=selected_rows, include_experiment=not show_queue)
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
        if record.get("status") != "已完成":
            continue
        if record.get("overall") is None and record.get("assessment_complete") is not False:
            continue
        student = str(record.get("student_id") or "").strip()
        student_key = ("student", student) if student else ("submission", record["id"])
        key = (student_key, teacher_assignment_key(record))
        if key not in latest or int(record["id"]) > int(latest[key]["id"]):
            latest[key] = record
    return sorted(latest.values(), key=lambda item: int(item["id"]))


def benchmark_comparison_table_html(rows):
    """只转换展示字段，验收判定、引用核对与导出的原始结果保持原样。"""
    headers = ['核查项目', '预设验收要求', 'AI返回结果', '是否符合要求', '回答引用校验']
    body = []
    for row in rows:
        expected = row.get('教师预期', '未记录')
        actual = row.get('AI判断', '未返回')
        agrees = row.get('是否一致')
        tone, match = ('pass', '符合') if agrees == '是' else ('partial', '未符合') if agrees == '否' else ('neutral', '未记录')
        raw_quote = row.get('学生回答原文可核对', '未记录')
        quote = {'是': '引用可核对', '否': '引用未核对通过', '不适用': '本项不检查引用'}.get(raw_quote, raw_quote)
        quote_tone = {'是': 'pass', '否': 'partial'}.get(raw_quote, 'neutral')
        values = [html.escape(str(row.get('考查要点', '未记录'))), html.escape(str(expected)), html.escape(str(actual)),
                  f'<span class="wuma-rate-badge wuma-rate-{tone}">{match}</span>',
                  f'<span class="wuma-benchmark-quote wuma-rate-{quote_tone}">{html.escape(str(quote))}</span>']
        if row.get('需核查') and agrees == '是':
            values[3] += '<div class="wuma-benchmark-note">另需人工核查</div>'
        cells = ''.join(f'<td data-label="{label}">{value}</td>' for label, value in zip(headers, values))
        body.append(f'<tr>{cells}</tr>')
    return ('<div class="wuma-table-wrapper"><table class="wuma-data-table wuma-evidence-table wuma-benchmark-table">'
            + '<thead><tr>' + ''.join(f'<th>{label}</th>' for label in headers) + '</tr></thead>'
            + '<tbody>' + ''.join(body) + '</tbody></table></div>')


def render_benchmark_results(rows):
    groups = {}
    for row in rows:
        groups.setdefault((row['轮次'], row['案例'], row['题号']), []).append(row)
    st.caption('预设验收要求来自固定案例；完整问题、合成回答和AI评价见下方对应“对照详情”。回答引用校验只在该项要求引用时执行。')
    for (turn, case, number), items in groups.items():
        question_label = f'第{number}题' if isinstance(number, (int, float)) or str(number).isdigit() else str(number)
        with st.container(border=True):
            st.markdown(f'**第{turn}轮 · {html.escape(str(case))} · {html.escape(question_label)}**')
            st.markdown(benchmark_comparison_table_html(items), unsafe_allow_html=True)
            premise_notes = list(dict.fromkeys(str(item.get('题目前提核查') or '未记录') for item in items))
            with st.expander('本案例的题目前提核查记录', expanded=False):
                for note in premise_notes:
                    render_evidence_block('题目前提核查记录', note, 'source' if note in {'未发现', '未记录'} else 'warning')


def benchmark_stability_table_html(rows):
    headers = ['案例', '题号', '核查项目', '各轮AI返回结果', '判断是否稳定']
    body = []
    for row in rows:
        stable = row.get('判断是否稳定')
        tone, label = ('pass', '稳定') if stable == '是' else ('partial', '不稳定') if stable == '否' else ('neutral', '未记录')
        values = [html.escape(str(row.get(key, '未记录'))) for key in ['案例', '题号', '考查要点', '各轮判断']]
        values.append(f'<span class="wuma-rate-badge wuma-rate-{tone}">{label}</span>')
        body.append('<tr>' + ''.join(f'<td data-label="{label}">{value}</td>' for label, value in zip(headers, values)) + '</tr>')
    return ('<div class="wuma-table-wrapper"><table class="wuma-data-table wuma-evidence-table wuma-benchmark-stability">'
            + '<thead><tr>' + ''.join(f'<th>{label}</th>' for label in headers) + '</tr></thead>'
            + '<tbody>' + ''.join(body) + '</tbody></table></div>')


def render_ai_benchmark():
    with st.expander("AI评价对照测试", expanded=False):
        st.caption("使用固定题目和回答，对照人工预先标记的判断。测试材料为合成案例，单独保存，不写入学生成绩或待办。")
        st.caption("包含正确、部分理解、明确误解、漏答子问、要求满分，以及用库函数替代选择排序的代码；真实请求会使用当前模型额度。")
        metadata = {"provider": llm.provider_name(), "model": llm.model_name(),
                    "evaluation_version": llm.EVALUATION_VERSION}
        st.write(f"当前服务：{metadata['provider']} · {metadata['model']}")
        repeats = st.selectbox("相同案例重复评价轮数", [1, 2], key="benchmark_repeats",
                               help="两轮使用相同问题、参考要点和回答，用于检查判断稳定性。")
        scope = st.selectbox("测试范围", list(evaluation_benchmark.SCOPES), key="benchmark_scope",
                             format_func=evaluation_benchmark.SCOPES.get)
        if scope == "dijkstra_premise":
            st.info("Dijkstra每轮2条：错误前提与正常对照使用同一份代码和回答。选择2轮共4条评价。")
            st.caption("错误前提应转教师核查；正常题目应认可有依据的解释。自动验收只核对标记与逐点判断，前提说明和反馈仍需人工核对。")
            st.caption("每条先核查题目前提，正常题再评价回答；一条评价可能包含两次模型请求，发现前提问题时会跳过评分。")
        else:
            st.caption("选择排序重点复测每轮3条；复杂代码每轮18条，覆盖树遍历、图搜索、动态规划的答辩解释与代码初评。")
        try:
            run = evaluation_benchmark.load_latest(db.current_database_path())
        except (OSError, ValueError) as error:
            st.error("无法读取对照记录：" + str(error))
            return
        if st.button("新建对照记录", key="benchmark_new", disabled=demo_mode.demo_read_only()):
            try:
                evaluation_benchmark.create_run(db.current_database_path(), metadata, repeats, scope)
            except (OSError, ValueError) as error:
                st.error(str(error))
            else:
                st.rerun()
        compatible = not run or (run["model"] == metadata and run["plan_hash"] == fingerprint(
            evaluation_benchmark.build_plan(run["repeats"], run.get("scope", "all"))))
        done = bool(run) and len(run["results"]) == len(run["plan"])
        if not compatible:
            st.info("当前模型或评价规则与上次对照不同，请新建对照记录；旧结果仍可查看和导出。")
        saved_counter = st.empty()
        saved_progress = st.empty()
        if run:
            st.caption("当前记录范围：" + evaluation_benchmark.SCOPES.get(run.get("scope", "all"), "旧版案例"))
            saved_counter.caption(f"已保存 {len(run['results'])}/{len(run['plan'])} 条真实评价 · 记录 {run['created_at'].replace('T', ' ')}")
            saved_progress.progress(len(run["results"]) / len(run["plan"]))
            if run.get("failed_item"):
                st.info("上次中断的条目会继续处理；已完成条目无需重跑。")
        if st.button("运行／继续真实AI对照", key="benchmark_run",
                     disabled=demo_mode.demo_read_only() or not llm.is_configured() or not compatible or done):
            try:
                run = run or evaluation_benchmark.create_run(db.current_database_path(), metadata, repeats, scope)
                with evaluation_benchmark.claim(run["id"]):
                    operation = ai_workflow.Operation()
                    timer = st.empty()
                    for item in run["plan"]:
                        if item["id"] in run["results"]:
                            continue
                        if operation.deadline - time.monotonic() < 2:
                            st.info("本批次已到等待上限，已完成结果保存；点击同一按钮继续剩余条目。")
                            break
                        st.write(f"正在评价：第{item['round']}轮 · {item['case']} · {item.get('number', '评分点初评')}")
                        try:
                            started = time.monotonic()
                            result = operation.run(lambda current=item: evaluation_benchmark.evaluate(current, llm),
                                lambda elapsed, left: timer.caption(f"本批次已等待 {elapsed:.0f} 秒 · 剩余预算 {left:.0f} 秒"))
                            run["results"][item["id"]] = {"evaluation": result, "seconds": round(time.monotonic() - started, 2)}
                            run["failed_item"] = ""
                            evaluation_benchmark.save_run(db.current_database_path(), run)
                            saved_counter.caption(f"已保存 {len(run['results'])}/{len(run['plan'])} 条真实评价 · 记录 {run['created_at'].replace('T', ' ')}")
                            saved_progress.progress(len(run["results"]) / len(run["plan"]))
                        except (llm.LLMServiceError, ai_workflow.WorkflowError):
                            run["failed_item"] = item["id"]
                            evaluation_benchmark.save_run(db.current_database_path(), run)
                            raise
            except (llm.LLMServiceError, ai_workflow.WorkflowError, OSError, ValueError) as error:
                st.error("对照未全部完成：" + str(error))
            else:
                if len(run["results"]) == len(run["plan"]):
                    st.success("本轮真实AI对照已完成，结果已单独保存。")
        if run and run["results"]:
            rows = evaluation_benchmark.rows_for(run)
            matches = sum(row["是否一致"] == "是" for row in rows)
            issues = [row for row in rows if row["需核查"]]
            if run.get("scope") == "dijkstra_premise":
                st.write(f"自动验收条件一致：{matches}/{len(rows)} · 未满足条件：{len(issues)}项")
                st.caption("错误前提转教师核查符合预期；本表未满足条件表示偏离验收要求。请展开对照详情，人工核对前提说明与反馈是否符合代码事实。")
            else:
                st.write(f"已完成条目的判断一致：{matches}/{len(rows)} · 需核查：{len(issues)}项")
            st.caption("这是本组案例与人工预期的对照结果，不代表真实教学总体准确率。存在分歧时仍需教师核对原文。")
            render_benchmark_results(rows)
            stability = evaluation_benchmark.stability_rows(run)
            if stability:
                st.markdown("**相同回答的判断稳定性**")
                st.markdown(benchmark_stability_table_html(stability), unsafe_allow_html=True)
            for item in run["plan"]:
                if item["id"] not in run["results"]:
                    continue
                with st.expander(f"对照详情 · 第{item['round']}轮 · {item['case']} · {item.get('number', '初评')}"):
                    if item["kind"] == "answer":
                        render_evidence_block("固定测试问题", item["question"]["question"], "question")
                        render_evidence_block("合成回答原文", item["answer"], "answer", meta="固定测试案例")
                    else:
                        st.code(item["code"], language="cpp")
                    if item.get("manual_check"):
                        st.write("人工核对要点：", item["manual_check"])
                    st.json({"预设验收要求": item.get("acceptance_expected", item["expected"]),
                             "AI评价": run["results"][item["id"]]["evaluation"]})
            st.download_button("导出AI对照结果JSON", json.dumps(run, ensure_ascii=False, indent=2),
                               file_name="wuma_ai_comparison_" + run["id"] + ".json", mime="application/json",
                               key="benchmark_export")


def show_teacher_dashboard():
    page_header(
        "教师工作台",
        "按待办选择学生，先看精简诊断，再按需核查证据并完成处理。",
    )
    show_flash()

    active_task = st.session_state.get("teacher_active_task")
    if active_task:
        render_teacher_task_workspace(active_task)
        return

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
    if any((record.get("code_verification") or {}).get("scoring_mode") == "pass_only" for record in completed):
        verified = [record for record in completed if (record.get("code_verification") or {}).get("cases")]
        passed = sum(record["code_verification"].get("overall_status") == "全部通过" for record in verified)
        col3.metric("代码全通过提交数", f"{passed}/{len(verified)}" if verified else "暂无")
    else:
        col3.metric(
            "平均历史代码测试得分",
            f"{average_code_score}/100" if code_score_percentages else "暂无",
        )
    col4.metric("平均AI评分点初评", average_completion if completion_scores else "暂无")
    col5, col6, col7 = st.columns(3)
    col5.metric("平均AI答辩理解度", average_score if scores else "暂无")
    col6.metric("待教师复核提交数", pending_review_count)
    col7.metric("教师待处理反馈数", pending_feedback_count)

    st.caption("历史提交总数、待复核及待反馈按全部历史记录计算；代码通过情况和均分使用上述去重样本，新判题不计测试点分值。")
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
        st.subheader("班级答辩四维AI平均分")
        dimension_averages = calculate_dimension_averages(learning_records)
        if dimension_averages:
            st.markdown(
                teacher_bar_rows_html(build_teacher_dimension_rows(dimension_averages), "统一量尺 0—100 分 · 每项直接显示平均分"),
                unsafe_allow_html=True,
            )
        else:
            st.info("完成答辩后显示四维平均分。")
    with right:
        st.subheader("AI对学生本次答辩要点的判断分布")
        weakness_rows = build_teacher_weakness_rows(completed, selected_assignment_id is not None)
        if weakness_rows:
            unit_note = "人数" if selected_assignment_id is not None else "学生—实验人次"
            st.markdown(
                teacher_bar_rows_html(weakness_rows, f"按{unit_note}从多到少 · 条长为占全部统计样本的比例"),
                unsafe_allow_html=True,
            )
            st.caption("分类来自AI对各学生本次答辩回答的评价；回答有错误或遗漏不代表全部知识未掌握。缺少作答依据时暂不判断掌握，AI评分相对较低也不等于理解错误。教师复核进度另行统计。")
        else:
            st.info("当前还没有已完成答辩的要点掌握评价。")

    with st.expander("测试点与实验统计", expanded=False):
        if filter_map[selected_filter] is None:
            st.subheader("各实验提交情况")
            assignment_groups = {}
            for record in records:
                assignment_groups.setdefault(teacher_assignment_key(record), []).append(record)
            assignment_summary = []
            for key, assignment_records in assignment_groups.items():
                assignment_completed = [record for record in completed if teacher_assignment_key(record) == key]
                task_scores = [record["overall"] for record in assignment_completed if record["overall"] is not None]
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

        st.subheader("代码测试点通过率")
        hidden_case_statistics = build_hidden_case_statistics(records)
        if not hidden_case_statistics.empty:
            st.caption(
                "此处按全部历史提交的判题记录统计，包含判题记录中的公开与隐藏测试，不使用掌握统计的去重样本。"
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
                st.caption("通过率从低到高排列；红色表示未通过，琥珀色表示部分通过，青绿色表示全部通过。判定次数不足3次标为样本较少。")
                st.caption("仅判通过的实验不计测试点分值；历史计分实验保留分值，同名实验存在不同计分模式时标为规则不同。")
                st.markdown(test_case_statistics_html(hidden_case_statistics, records), unsafe_allow_html=True)
        else:
            st.info("完成正式代码判定并保存测试点结果后，这里将显示各测试点通过率。")

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
            item = jump_map[jump_label]
            st.button("定位并处理反馈", on_click=open_teacher_task, args=({
                    "task_key": f"teacher-feedback-{item['id']}",
                    "title": "回复学生反馈" if item["reply_requested"] else "查看学生反馈",
                    "submission_id": item["submission_id"],
                    "feedback_id": item["id"],
                    "assignment_id": item.get("assignment_id"),
                    "action": "process_feedback", "status": item["status"],
                },))
        else:
            st.info("当前反馈状态下没有记录。")

    render_teacher_case_workspace(records, selected_assignment_id, show_queue=False)


def show_sidebar():
    with st.sidebar:
        render_brand_lockup(compact=True)
        st.caption(
            "v1.4 · 交互Demo（案例回放）" if demo_playback.active() else
            ("v1.4 · 赛事只读Demo" if demo_mode.demo_mode_enabled() else "v1.4 · 完整本地版")
        )
        st.caption("不止评代码，更要评理解")
        st.caption("已内置 LearnBuddy 技能与只读连接器适配包")
        if demo_playback.active():
            st.success("独立合成数据空间\n\n可保存操作 · AI与判题回放")
        if demo_mode.demo_mode_enabled():
            st.success("赛事在线Demo\n\n合成数据 · 安全浏览")
        if st.session_state.auth_role == "学生":
            st.info(
                f"学生：{st.session_state.auth_name}\n\n"
                f"学号：{logged_in_student_id()}"
            )
        else:
            st.info(f"{st.session_state.auth_role}：{st.session_state.auth_name}")
        if st.button("退出登录", width="stretch"):
            logout()
        st.divider()
        actor = access_control.current()
        if actor and actor["role"] in {"teacher", "admin"}:
            classes = accounts.list_classes(actor["id"])
            if classes:
                mapping = {c["id"]: c["name"] for c in classes}
                def change_class():
                    for key in list(st.session_state):
                        if key.startswith(("auth_", "form_", "nav_")) or key in {"current_page", "selected_class_id", "_browser_request", "_interactive_workspace", "_interactive_case"}:
                            continue
                        del st.session_state[key]
                selected = st.selectbox("当前班级", list(mapping), format_func=mapping.get,
                                        key="selected_class_id", on_change=change_class)
                actor["class_id"] = selected
            else:
                st.caption("尚无班级，请在" + ("账号与系统管理" if actor['role']=='admin' else "班级与学生管理") + "创建班级。")
        if actor and actor["role"] == "student":
            with st.expander("修改本人密码"):
                account_ui.password_form(actor)

        if st.session_state.auth_role == "学生":
            pending_task_count = len(
                db.list_student_tasks(logged_in_student_id())
            )
        elif st.session_state.auth_role == "教师":
            pending_task_count = len(db.list_teacher_tasks())
        else:
            pending_task_count = 0
        for page in pages_for_role(st.session_state.auth_role):
            button_type = "primary" if page == st.session_state.current_page else "secondary"
            task_page = (
                page == "任务中心"
                if st.session_state.auth_role == "学生"
                else page == "教师工作台"
            )
            label = (
                f"{page}（{pending_task_count}）"
                if page == "任务中心" and task_page and pending_task_count
                else page
            )
            st.button(label, type=button_type, width="stretch", key=f"nav_{page}",
                      on_click=request_navigation, args=(page,))

        st.divider()
        if st.session_state.auth_role == "教师":
            if demo_playback.active():
                st.info("在线AI与判题为固定案例回放；完整本地版支持真实模型与代码执行。")
            elif demo_mode.demo_mode_enabled():
                st.info("赛事只读Demo无需模型密钥，不调用模型服务，也不执行学生代码。")
            elif llm.is_configured():
                st.success(f"网页AI评价服务：{llm.provider_name()} / {llm.model_name()}")
                if st.button("测试网页AI评价连接", width="stretch"):
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
                st.error("网页AI评价服务尚未配置")

            st.caption(
                "LearnBuddy通过技能和只读连接器查询悟码AI教学证据；"
                "教师在网页保存复核结论。"
            )

            runtime = verifier.runtime_status()
            if runtime["available"]:
                st.success(runtime["label"])
                st.caption(runtime["detail"])
            else:
                st.caption(f"代码验证：{runtime['label']}；{runtime['detail']}")
            st.caption(f"当前账号可见 {len(db.list_submissions())} 条提交记录")
            st.caption(f"数据源标识：{runtime_config.database_id(db.current_database_path())}")
        elif st.session_state.auth_role == "管理员":
            st.caption("账号与系统管理工作区")
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
            background: #ffffff;
        }
        [data-testid="stMainBlockContainer"] {
            padding-left: 2rem;
            padding-right: 2rem;
        }
        @media (max-width: 640px) {
            [data-testid="stMainBlockContainer"] {
                padding-left: 1rem;
                padding-right: 1rem;
            }
        }
        .wuma-focus-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; margin:12px 0 20px; }
        .wuma-focus-card { border:1px solid #d9e3ef; border-left:4px solid #4975a7; border-radius:12px; padding:16px 18px; background:#f7faff; min-width:0; }
        .wuma-focus-card:nth-child(3n+2) { border-left-color:#22796b; background:#f5faf8; }
        .wuma-focus-card:nth-child(3n) { border-left-color:#79569f; background:#faf7fc; }
        .wuma-focus-card h4 { font-size:1rem; margin:0 0 12px; color:#243f60; }
        .wuma-focus-card ul { list-style:none; padding:0; margin:0; }
        .wuma-focus-card li { display:flex; gap:10px; align-items:flex-start; margin:0 0 12px; }
        .wuma-focus-card li:last-child { margin-bottom:0; }
        .wuma-focus-number { background:#e4edf8; color:#315d8e; font-size:.8rem; line-height:1.6; padding:2px 6px; border-radius:5px; flex:none; }
        .wuma-focus-text { line-height:1.8; white-space:pre-wrap; overflow-wrap:anywhere; min-width:0; }
        .wuma-focus-text code { background:#e9eef5; color:#26394f; font-size:.9em; border-radius:3px; padding:1px 3px; white-space:normal; }
        @media (max-width:900px) { .wuma-focus-grid { grid-template-columns:1fr; } }
        [data-testid="stMain"] { overflow-anchor: none; }
        [data-testid="stMain"] h2,
        [data-testid="stMain"] h3 {
            background: #EDF3FA;
            border-left: 4px solid #7396BE;
            border-radius: 0 7px 7px 0;
            padding: 0.5rem 0.8rem;
            margin-top: 0.75rem;
            margin-bottom: 1rem;
            color: #172033;
        }
        [data-testid="stExpander"] > details > summary {
            background: #F3F6FA;
            color: #263D57;
            border-radius: 7px;
        }
        .wuma-reading-note {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-left: 3px solid #6B8FB5;
            border-radius: 8px;
            padding: 14px 16px;
            margin: 8px 0 16px;
            color: #172033;
        }
        .wuma-note-heading { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: baseline; }
        .wuma-note-heading strong { color: #365F88; font-size: 0.96rem; }
        .wuma-note-heading span { color: #64748b; font-size: 0.86rem; }
        .wuma-note-detail { margin-top: 8px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
        .wuma-note-positive { border-left-color: #4B8070; }
        .wuma-note-positive strong { color: #356B5B; }
        .wuma-note-warning { border-left-color: #B28A4D; }
        .wuma-note-warning strong { color: #8A642E; }
        .wuma-note-danger { border-left-color: #B55B5B; }
        .wuma-note-danger strong { color: #9B4343; }
        .wuma-note-neutral { border-left-color: #94A3B8; }
        .wuma-note-neutral strong { color: #475569; }
        .wuma-evidence-block {
            box-sizing: border-box;
            width: 100%; max-width: 56rem;
            border: 1px solid #dce5f0; border-left: 4px solid #3B6FB6;
            border-radius: 8px; padding: 14px 16px; margin: 10px 0 16px;
            color: #172033; background: #f4f8ff;
        }
        .wuma-evidence-heading { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: baseline; }
        .wuma-evidence-heading strong { font-size: 1rem; color: #365F88; }
        .wuma-evidence-tag { flex: 0 0 auto; padding: 2px 7px; border-radius: 5px;
            background: #e0ecfc; color: #365F88; font-size: 0.78rem; font-weight: 600; }
        .wuma-evidence-meta { color: #64748b; font-size: 0.86rem; }
        .wuma-evidence-body { margin-top: 10px; font-size: 1rem; line-height: 1.8;
            white-space: pre-wrap; overflow-wrap: anywhere; text-align: left; }
        .wuma-evidence-block-answer, .wuma-evidence-block-material {
            background: #f1faf7; border-color: #d0e8df; border-left-color: #0F8A78;
        }
        .wuma-evidence-block-answer strong, .wuma-evidence-block-material strong { color: #166557; }
        .wuma-evidence-block-answer .wuma-evidence-tag, .wuma-evidence-block-material .wuma-evidence-tag {
            background: #d8f0e6; color: #166557;
        }
        .wuma-evidence-block-judgment { background: #f8f7fc; border-color: #e7e1f0; border-left-color: #8170a6; }
        .wuma-evidence-block-judgment strong { color: #5c4880; }
        .wuma-evidence-block-judgment .wuma-evidence-tag { background: #ebe5f4; color: #5c4880; }
        .wuma-evidence-block-warning { background: #fff9ef; border-color: #f0dfc1; border-left-color: #b7791f; }
        .wuma-evidence-block-warning strong { color: #805517; }
        .wuma-evidence-block-warning .wuma-evidence-tag { background: #f6e7c9; color: #805517; }
        .wuma-evidence-block-source { background: #fafbfc; border-color: #e2e8f0; border-left-color: #94a3b8; }
        .wuma-evidence-block-source strong { color: #475569; }
        .wuma-evidence-block-source .wuma-evidence-tag { background: #e9eef4; color: #475569; }
        .wuma-evidence-point { box-sizing: border-box; width: 100%; max-width: 56rem;
            border: 1px solid #e2e8f0; border-radius: 10px; padding: 6px 14px 0; margin: 22px 0; background: #fff; }
        @media (max-width: 640px) {
            .wuma-evidence-point { padding: 4px 8px 0; }
            .wuma-evidence-block { padding: 12px; }
        }
        .st-key-teacher_feedback_focus,
        .st-key-teacher_review_focus,
        .st-key-student_reply_focus {
            background: #ffffff;
            border-top: 4px solid #7396BE;
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
            border: 1px solid #D5E0ED;
            border-top: 3px solid #8BAACD;
            padding: 1rem;
            border-radius: 14px;
        }
        [data-testid="stMetricLabel"] {
            color: #365F88;
            background: #EDF3FA;
            border-radius: 5px;
            padding: 3px 7px;
            width: fit-content;
        }
        [data-testid="stMetricLabel"] p {
            white-space: normal; overflow: visible; text-overflow: clip;
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
            border: 1px solid #D5E0ED;
            border-top: 3px solid #8BAACD;
            border-radius: 16px;
            background: #ffffff;
        }
        .wuma-summary-label {
            color: #365F88;
            background: #EDF3FA;
            width: fit-content;
            padding: 3px 7px;
            border-radius: 5px;
            font-size: 0.88rem;
            margin-bottom: 12px;
        }
        .wuma-summary-value {
            color: #172033;
            font-size: clamp(1.2rem, 1.8vw, 1.8rem);
            font-weight: 650;
            line-height: 1.45;
            white-space: normal;
            overflow-wrap: anywhere;
        }
        .wuma-summary-note { color: #64748b; font-size: 0.85rem; margin-top: 8px; }
        .wuma-summary-card-code { border-top-color: #3B6FB6; background: #f8fbff; }
        .wuma-summary-card-code .wuma-summary-label { background: #e0ecfc; color: #365F88; }
        .wuma-summary-card-neutral { border-top-color: #94a3b8; background: #fafbfc; }
        .wuma-summary-card-neutral .wuma-summary-label { background: #e9eef4; color: #475569; }
        .wuma-summary-card-analysis { border-top-color: #8170a6; background: #faf8fd; }
        .wuma-summary-card-analysis .wuma-summary-label { background: #ebe5f4; color: #5c4880; }
        .wuma-summary-card-warning { border-top-color: #b7791f; background: #fffbf3; }
        .wuma-summary-card-warning .wuma-summary-label { background: #f6e7c9; color: #805517; }
        .wuma-summary-card-positive { border-top-color: #0F8A78; background: #f4fbf8; }
        .wuma-summary-card-positive .wuma-summary-label { background: #d8f0e6; color: #166557; }
        table.wuma-evidence-table.wuma-evidence-teacher-status td { white-space: pre-line; }
        table.wuma-evidence-teacher-status th:first-child { width: 30%; }
        table.wuma-evidence-teacher-status th:nth-child(2) { width: 15%; }
        table.wuma-evidence-teacher-status th:nth-child(3) { width: 13%; }
        table.wuma-evidence-teacher-status th:nth-child(4) { width: 15%; }
        table.wuma-evidence-statistics th:first-child { width: 28%; }
        table.wuma-evidence-statistics th:nth-child(2) { width: 11%; }
        table.wuma-evidence-statistics th:nth-child(3) { width: 22%; }
        table.wuma-evidence-statistics th:nth-child(4) { width: 10%; }
        table.wuma-evidence-statistics th:nth-child(5),
        table.wuma-evidence-statistics th:nth-child(6) { width: 8%; }
        .wuma-rate-badge { display: inline-block; padding: 3px 7px; border-radius: 5px; font-weight: 650; }
        .wuma-rate-pass { color: #166557; background: #d8f0e6; }
        .wuma-rate-partial { color: #805517; background: #f6e7c9; }
        .wuma-rate-fail { color: #9b3030; background: #fbe2e2; }
        .wuma-rate-neutral { color: #475569; background: #e9eef4; }
        .wuma-rate-label, .wuma-sample-note { font-size: 0.78rem; margin-top: 4px; }
        .wuma-sample-note { color: #64748b; }
        @media (max-width: 640px) {
            table.wuma-evidence-teacher-status thead, table.wuma-evidence-statistics thead { display: none; }
            table.wuma-evidence-teacher-status, table.wuma-evidence-statistics,
            table.wuma-evidence-teacher-status tbody, table.wuma-evidence-statistics tbody { display: block; }
            table.wuma-evidence-teacher-status tr, table.wuma-evidence-statistics tr {
                display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
                border: 1px solid #d9e1ec; border-radius: 8px; margin: 10px 0; overflow: hidden;
            }
            table.wuma-evidence-table.wuma-evidence-teacher-status td,
            table.wuma-evidence-table.wuma-evidence-statistics td {
                display: block; text-align: left !important; min-width: 0; border-right: none;
            }
            table.wuma-evidence-teacher-status td:first-child,
            table.wuma-evidence-teacher-status td:last-child,
            table.wuma-evidence-statistics td:first-child,
            table.wuma-evidence-statistics td:nth-child(3) { grid-column: 1 / -1; }
            table.wuma-evidence-teacher-status td::before, table.wuma-evidence-statistics td::before {
                content: attr(data-label); display: block; color: #657086;
                font-size: 0.78rem; font-weight: 600; margin-bottom: 5px;
            }
        }
        .wuma-verification-grid {
            grid-template-columns: repeat(auto-fit, minmax(min(100%, 210px), 1fr));
        }
        .wuma-verification-grid .wuma-summary-value {
            font-size: clamp(1.12rem, 1.5vw, 1.4rem);
            line-height: 1.5;
            overflow: visible;
            text-overflow: clip;
        }
        .wuma-verification-grid .wuma-summary-label { line-height: 1.6; }
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
        table.wuma-evidence-table { table-layout: fixed; font-size: 0.94rem; }
        table.wuma-evidence-table th,
        table.wuma-evidence-table td {
            padding: 0.7rem 0.75rem;
            white-space: normal;
            overflow-wrap: anywhere;
            line-height: 1.65;
        }
        table.wuma-evidence-table td:first-child {
            text-align: left !important;
        }
        table.wuma-evidence-code td:first-child,
        table.wuma-evidence-rubric td:first-child,
        table.wuma-evidence-dimension td:first-child { text-align: center !important; }
        table.wuma-evidence-rubric th:first-child { width: 45%; }
        table.wuma-evidence-rubric td:nth-child(3) { white-space: nowrap; }
        table.wuma-evidence-dimension th:first-child { width: 24%; }
        table.wuma-evidence-dimension th:nth-child(2) { width: 18%; }
        table.wuma-evidence-dimension td:last-child { text-align: center !important; }
        table.wuma-evidence-dimension .wuma-adaptive-text {
            display: inline-block;
            max-width: 100%;
            text-align: left;
            vertical-align: middle;
        }
        table.wuma-evidence-code th:first-child { width: 25%; }
        table.wuma-evidence-code th:last-child { width: 25%; }
        table.wuma-evidence-code td:nth-child(4),
        table.wuma-evidence-code td:nth-child(5) { font-variant-numeric: tabular-nums; }
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
        .wuma-account-tag { display: inline-block; border-radius: 5px; padding: 3px 8px; font-weight: 600; }
        .wuma-account-admin { background: #ebe5f4; color: #5c4880; }
        .wuma-account-teacher { background: #e0ecfc; color: #365f88; }
        .wuma-account-active { background: #d8f0e6; color: #166557; }
        .wuma-account-inactive { background: #e9eef4; color: #475569; }
        table.wuma-account-table th:first-child { width: 24%; }
        table.wuma-account-table th:nth-child(2) { width: 24%; }
        table.wuma-account-table th:nth-child(3) { width: 34%; }
        table.wuma-account-table td:first-child { text-align: center !important; }
        table.wuma-benchmark-table th,
        table.wuma-benchmark-stability th { white-space: normal; }
        table.wuma-benchmark-table th:first-child { width: 28%; }
        table.wuma-benchmark-table th:nth-child(2),
        table.wuma-benchmark-table th:nth-child(3) { width: 24%; }
        table.wuma-benchmark-table th:nth-child(4) { width: 11%; }
        table.wuma-benchmark-table.wuma-evidence-table td,
        table.wuma-benchmark-stability.wuma-evidence-table td { vertical-align: top !important; }
        table.wuma-benchmark-table.wuma-evidence-table td:nth-child(-n+3),
        table.wuma-benchmark-stability.wuma-evidence-table td:first-child,
        table.wuma-benchmark-stability.wuma-evidence-table td:nth-child(3),
        table.wuma-benchmark-stability.wuma-evidence-table td:nth-child(4) { text-align: left !important; white-space: pre-wrap; }
        table.wuma-benchmark-stability th:first-child { width: 24%; }
        table.wuma-benchmark-stability th:nth-child(2) { width: 7%; }
        table.wuma-benchmark-stability th:nth-child(3) { width: 28%; }
        table.wuma-benchmark-stability th:nth-child(4) { width: 28%; }
        .wuma-benchmark-quote { display: inline-block; padding: 3px 6px; border-radius: 5px; font-size: 0.83rem; }
        .wuma-benchmark-note { color: #805517; font-size: 0.78rem; margin-top: 5px; }
        @media (max-width: 700px) {
            table.wuma-account-table thead, table.wuma-benchmark-table thead, table.wuma-benchmark-stability thead { display: none; }
            table.wuma-account-table, table.wuma-account-table tbody,
            table.wuma-benchmark-table, table.wuma-benchmark-table tbody,
            table.wuma-benchmark-stability, table.wuma-benchmark-stability tbody { display: block; }
            table.wuma-account-table tr, table.wuma-benchmark-table tr, table.wuma-benchmark-stability tr {
                display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
                border: 1px solid #d9e1ec; border-radius: 8px; margin: 8px 0; overflow: hidden;
            }
            table.wuma-account-table.wuma-evidence-table td,
            table.wuma-benchmark-table.wuma-evidence-table td,
            table.wuma-benchmark-stability.wuma-evidence-table td {
                display: block; min-width: 0; text-align: left !important; border-right: none;
            }
            table.wuma-benchmark-table td:first-child,
            table.wuma-benchmark-stability td:first-child,
            table.wuma-benchmark-stability td:nth-child(3),
            table.wuma-benchmark-stability td:nth-child(4),
            table.wuma-benchmark-stability td:last-child { grid-column: 1 / -1; }
            table.wuma-account-table td::before, table.wuma-benchmark-table td::before,
            table.wuma-benchmark-stability td::before {
                content: attr(data-label); display: block; color: #657086;
                font-size: 0.78rem; font-weight: 600; margin-bottom: 5px;
            }
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


def restore_browser_view(actor, view, classes):
    """Restore navigation hints only after validating the account and class permissions."""
    page = view.get("page", "首页")
    if page == "系统设置":
        page = "账号与系统管理" if actor['role']=='admin' else "班级与学生管理"
    st.session_state.current_page = page if page in pages_for_role(st.session_state.auth_role) else "首页"
    if view.get("class_id") in {item["id"] for item in classes}:
        st.session_state.selected_class_id = view["class_id"]
    try:
        with access_control.scope(actor, st.session_state.get("selected_class_id")):
            if actor["role"] == "student":
                if view.get("assignment_id") and db.get_assignment(view["assignment_id"]):
                    st.session_state.selected_assignment_id = view["assignment_id"]
                if view.get("submission_id"):
                    load_submission_session(view["submission_id"])
            elif actor["role"] == "teacher":
                st.session_state.teacher_workspace_view = view.get("teacher_workspace", "待办处理")
                if view.get("teacher_submission_id") and db.get_submission(view["teacher_submission_id"]):
                    st.session_state.teacher_selected_submission_id = view["teacher_submission_id"]
                    st.session_state.teacher_task_submission_id = view["teacher_submission_id"]
    except (PermissionError, ValueError):
        # Browser hints never grant access. Lost permissions fall back to the authorized home.
        st.session_state.current_page = "首页"
        st.session_state.submission_id = None
        st.session_state.submission = None


def main():
    # The Cloud entry imports this module once, but calls main on every rerun.
    st.set_page_config(
        page_title="悟码AI",
        page_icon=":material/code:",
        layout="wide",
    )
    if demo_mode.demo_mode_enabled():
        try:
            demo_mode.prepare_demo_database(db.current_database_path())
        except (demo_mode.DemoDatabaseError, sqlite3.Error, OSError) as error:
            st.error(f"Demo启动已停止：{error}")
            st.info("请使用 launch.py --mode demo 指定新的独立数据库，或以 local 模式启动完整本地版。")
            st.stop()
    db.init_db()
    if not demo_mode.demo_mode_enabled():
        accounts.init_schema()
    demo_mode.ensure_demo_data()
    init_state()
    add_styles()
    if not demo_mode.demo_mode_enabled():
        actor = accounts.session(st.session_state.get("auth_token", ""))
        if not actor:
            if st.session_state.get("auth_token"):
                browser_session.clear_state()
                init_state()
                st.session_state["_browser_expired_message"] = True
            if st.session_state.get("_browser_clear_pending"):
                browser_session.exchange(db.current_database_path(), "clear")
                if st.session_state.get("_browser_clear_pending") and not st.session_state.get("_browser_storage_blocked"):
                    render_brand_lockup()
                    st.caption("正在更新登录状态，请稍候。")
                    return
            elif not st.session_state.get("_role_split_completed") and not st.session_state.get("_browser_read_completed"):
                # The upgrade revoked the old token and requested its removal.
                # Do not start another restore callback while the owner signs in.
                saved = browser_session.exchange(db.current_database_path(), "read")
                if saved:
                    restored = accounts.session(saved["token"])
                    if restored:
                        st.session_state.auth_token = saved["token"]
                        st.session_state["_browser_restore_view"] = saved["view"]
                        st.rerun()
                    browser_session.clear_state()
                    st.session_state["_browser_expired_message"] = True
                    st.rerun()
                if not st.session_state.get("_browser_read_completed"):
                    render_brand_lockup()
                    st.caption("正在恢复登录状态，请稍候。")
                    return
            if st.session_state.get("_browser_expired_message"):
                st.info("会话已过期或被撤销，请重新登录。已保存草稿仍可恢复。")
            st.session_state.auth_role = ""
            show_login()
            return
        account_ui.bind(actor)
        if actor["must_change"]:
            account_ui.password_form(actor, required=True)
            if st.button("退出登录"):
                logout()
            browser_session.exchange(db.current_database_path(), "write", st.session_state.auth_token, browser_session.capture_view())
            return
        if accounts.needs_role_split(actor):
            account_ui.role_split_form(actor)
            if st.button("退出登录"):
                logout()
            browser_session.exchange(db.current_database_path(), "write", st.session_state.auth_token, {})
            return
        if st.session_state.current_page == "系统设置":
            st.session_state.current_page = "账号与系统管理" if actor['role']=='admin' else "班级与学生管理"
        classes = accounts.list_classes(actor["id"])
        if st.session_state.get("selected_class_id") not in {c["id"] for c in classes}:
            st.session_state.selected_class_id = classes[0]["id"] if classes else None
        recovered_view = st.session_state.pop("_browser_restore_view", None)
        if recovered_view is not None:
            restore_browser_view(actor, recovered_view, classes)
        try:
            with access_control.scope(actor, st.session_state.get("selected_class_id")):
                render_authenticated_pages(actor)
        except PermissionError as error:
            st.error(str(error))
            st.caption("请返回首页或重新登录；权限校验未修改其他班级的数据。")
        browser_session.exchange(db.current_database_path(), "write", st.session_state.auth_token, browser_session.capture_view())
        return
    if st.session_state.auth_role not in {"学生", "教师"}:
        show_login()
        return
    render_authenticated_pages()


def render_authenticated_pages(actor=None):
    allowed_pages = pages_for_role(st.session_state.auth_role)
    if st.session_state.current_page not in allowed_pages:
        st.session_state.current_page = "首页"
    show_sidebar()
    reset_scroll_after_navigation()
    page_functions = {
        "首页": show_home, "任务中心": show_task_center,
        "教师工作台": show_teacher_dashboard, "实验提交": show_submission,
        "AI答辩": show_defense, "学生报告": show_report,
        "学习档案": show_learning_archive, "实验管理": show_assignment_management,
        "班级与学生管理": lambda: account_ui.settings(actor, render_ai_benchmark),
        "账号与系统管理": lambda: account_ui.settings(actor, render_ai_benchmark),
    }
    page_functions[st.session_state.current_page]()



if __name__ == "__main__":
    main()
