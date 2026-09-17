"""从已保存证据生成教师摘要；不调用模型、不改写历史评价。"""
import re


def brief(value, limit=72):
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def evaluation(item):
    return item.get("final_evaluation") or item.get("initial_evaluation") or {}


def strings(value):
    return [v.strip() for v in value if isinstance(v, str) and v.strip()] if isinstance(value, list) else []


def question_finding(item, index):
    ev = evaluation(item)
    source = f"第{index}题"
    errors = strings(ev.get("misconceptions"))
    points = ev.get("point_assessments") or []
    errors += [p.get("reference_point", "") for p in points if p.get("status") == "理解错误"]
    missing = strings(ev.get("missing_points"))
    missing += [p.get("reference_point", "") for p in points if p.get("status") == "部分掌握"]
    if not item.get("answer") or not ev:
        return (2, "证据不足", f"{source}：尚无完整回答与评价，不能据此认定未掌握。")
    if ev.get("confidence") not in {"高", "中"}:
        detail = errors[0] if errors else missing[0] if missing else "评价置信度低或未记录"
        return (2, "证据不足", f"{source}：{brief(detail, 55)}；需核查原回答。")
    if errors:
        return (0, "需要巩固", f"{source} · AI标记理解错误：{brief(errors[0], 60)}")
    if missing:
        return (3, "需要巩固", f"{source} · 回答遗漏：{brief(missing[0], 60)}")
    if not points or any(p.get("status") != "掌握" for p in points):
        return (2, "证据不足", f"{source}：部分参考点缺少直接证据，请展开核查。")
    return (8, "已有掌握证据", f"{source}：已记录参考点均有掌握证据。")


def mastery_summary(report, qa_records):
    if not report:
        return {"status": "证据不足", "text": "答辩尚未完成", "dimension": ""}
    findings = [question_finding(item, i) for i, item in enumerate(qa_records, 1)]
    if any(status == "需要巩固" for _, status, _ in findings):
        return {"status": "需要巩固", "text": "回答有具体错误或遗漏，见逐题证据", "dimension": ""}
    if not findings or any(status == "证据不足" for _, status, _ in findings):
        return {"status": "证据不足", "text": "现有回答或评价不足以确认掌握", "dimension": ""}
    dimensions = report.get("dimensions") or {}
    scores = {name: score for name, score in dimensions.items() if isinstance(score, (int, float))}
    if not scores:
        return {"status": "证据不足", "text": "未记录能力维度评分", "dimension": ""}
    lowest = min(scores, key=scores.get)
    if min(scores.values()) >= 80:
        text = "各维度均衡，已有掌握证据" if len(set(scores.values())) == 1 else f"整体良好，{lowest}相对较低（{scores[lowest]}/100）"
        return {"status": "相对较弱（整体良好）" if len(set(scores.values())) > 1 else "已有掌握证据", "text": text, "dimension": lowest if len(set(scores.values())) > 1 else ""}
    return {"status": "待核查", "text": f"{lowest}评分较低，但缺少对应错误证据，需核对评价", "dimension": lowest}


def teacher_review_state(record, report=None, qa_records=None, mastery=None):
    """返回教师复核的唯一标准状态，供网页、待办与MCP共同使用。"""
    if record.get("status") != "已完成":
        return {
            "状态": "不适用",
            "是否当前待办": False,
            "原因": "答辩尚未完成",
        }

    reasons = []
    if record.get("review_required"):
        reasons.append("诊断报告标记需要教师复核")
    if int(record.get("attempt_number") or 1) > 1:
        reasons.append("该记录属于重新答辩")
    if mastery is None and report is not None and qa_records is not None:
        mastery = mastery_summary(report, qa_records)
    if mastery and mastery.get("status") in {"需要巩固", "证据不足", "待核查"}:
        reasons.append(f"掌握证据状态为{mastery['status']}")

    if record.get("teacher_reviewed"):
        return {
            "状态": "已复核",
            "是否当前待办": False,
            "原因": "；".join(reasons) if reasons else "教师已完成常规抽查",
        }
    if reasons:
        return {
            "状态": "待复核",
            "是否当前待办": True,
            "原因": "；".join(reasons),
        }
    return {
        "状态": "常规抽查",
        "是否当前待办": False,
        "原因": "当前没有强制复核触发条件",
    }


def needs_teacher_review(record, report=None, qa_records=None, mastery=None):
    """兼容旧调用：只回答该记录现在是否属于教师待办。"""
    state = teacher_review_state(record, report, qa_records, mastery)
    return state["是否当前待办"]


def ranked_concerns(record, preliminary, report, qa_records):
    candidates = []
    for i, item in enumerate(qa_records, 1):
        priority, status, detail = question_finding(item, i)
        if status != "已有掌握证据":
            candidates.append((priority, f"{status}｜{detail}"))
    for reason in strings((report or {}).get("review_reasons")):
        candidates.append((1, "报告复核原因（待核实）：" + brief(reason, 60)))
    code_status = (record.get("code_verification") or {}).get("overall_status", "未执行")
    if code_status in {"环境不可用", "编译环境不兼容"}:
        candidates.append((4, "该次提交因当时环境异常未完成判题；当前运行环境请查看侧栏。"))
    elif code_status not in {"全部通过", "未启用", "未执行"}:
        candidates.append((4, f"代码判题：{code_status}，请核查判题证据。"))
    for item in (preliminary or {}).get("criteria", []):
        if item.get("status") != "满足":
            candidates.append((5, "静态初评待核查｜" + brief(item.get("criterion_name"), 22) + "：" + brief(item.get("missing") or item.get("defense_focus") or item.get("status"), 50)))
    summary = mastery_summary(report, qa_records)
    candidates.append((7, summary["status"] + "｜" + summary["text"]))
    ordered = []
    for _, text in sorted(candidates, key=lambda pair: pair[0]):
        if text not in ordered:
            ordered.append(text)
    return ordered[:3]


def comparable_points(old_questions, new_questions):
    """仅匹配完全相同（忽略空白）的参考知识点，不把题号/宽泛维度当作知识点。"""
    def collect(questions):
        result = {}
        for index, item in enumerate(questions, 1):
            ev = evaluation(item)
            assessments = ev.get("point_assessments") or []
            for point in assessments:
                label = str(point.get("reference_point") or "").strip()
                if not label:
                    continue
                result.setdefault(re.sub(r"\s+", "", label), []).append({
                    "label": label, "question": index, "status": point.get("status") or "未记录",
                    "evidence": brief(point.get("evidence"), 100),
                    "reliable": bool(item.get("answer")) and ev.get("confidence") in {"高", "中"}
                    and not (point.get("status") == "掌握" and strings(ev.get("misconceptions"))),
                })
            if not assessments:
                for label in strings(item.get("reference_points")):
                    result.setdefault(re.sub(r"\s+", "", label), []).append({
                        "label": label, "question": index, "status": "未记录", "evidence": "缺少逐点评价", "reliable": False,
                    })
        return result
    old, new = collect(old_questions), collect(new_questions)
    rows = []
    for key in dict.fromkeys([*old, *new]):
        before, after = old.get(key, []), new.get(key, [])
        conclusion = "本次未覆盖，不能判断原问题是否消除" if not after else "本次新增考查点" if not before else "证据不足，需人工核查"
        if before and after and all(p["reliable"] for p in before + after):
            old_states, new_states = {p["status"] for p in before}, {p["status"] for p in after}
            if "理解错误" in new_states:
                conclusion = "本次仍有理解错误证据" if "理解错误" in old_states else "本次出现理解错误证据"
            elif new_states == {"掌握"}:
                conclusion = "原误解已有纠正证据（待教师确认）" if "理解错误" in old_states else "本次已有掌握证据"
            elif "部分掌握" in new_states:
                conclusion = "本次仍需补充说明"
        def describe(items):
            return "；".join(f"第{p['question']}题 · {p['status']}：{p['evidence']}" for p in items) or "未覆盖"
        rows.append({"参考知识点": (before or after)[0]["label"], "上次证据": describe(before), "本次证据": describe(after), "变化判断": conclusion})
    return rows


def mark_superseded_records(records):
    """仅用同学生同实验后续成功判题/完成答辩解除旧技术状态；保留人工事项。"""
    def key(record):
        student = str(record.get("student_id") or "").strip()
        assignment = ("id", record["assignment_id"]) if record.get("assignment_id") is not None else ("legacy", record.get("problem") or record.get("assignment_title"))
        return (student or ("unknown", record["id"]), assignment)
    progress = {}
    for record in records:
        entry = progress.setdefault(key(record), {"code": 0, "defense": 0})
        if (record.get("code_verification") or {}).get("overall_status") == "全部通过":
            entry["code"] = max(entry["code"], int(record["id"]))
        if record.get("status") == "已完成":
            entry["defense"] = max(entry["defense"], int(record["id"]))
    return [dict(record, _code_resolved_by=progress[key(record)]["code"] if progress[key(record)]["code"] > int(record["id"]) else None,
                 _defense_resolved_by=progress[key(record)]["defense"] if progress[key(record)]["defense"] > int(record["id"]) else None)
            for record in records]
