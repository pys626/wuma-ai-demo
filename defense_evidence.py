"""按参考要点与真实作答汇总能力证据；不按题号分配能力分数。"""
import re
from text_formatting import join_clauses


DIMENSIONS = (
    "程序逻辑理解", "关键概念掌握", "边界情况意识", "分析与修改能力",
)
POINT_SCORES = {"掌握": 100, "部分掌握": 65, "理解错误": 0}
SCORE_VERSION = "point_evidence_v1"


def _compact(text):
    return re.sub(r"\s+", "", str(text or ""))


def quote_source(quote, answer, follow_up_answer=""):
    """只接受可以在某一次回答中找到的原文，不跨两次回答拼接。"""
    quoted = _compact(quote)
    if not quoted:
        return ""
    for source, text in (("首次回答", answer), ("追问回答", follow_up_answer)):
        if quoted in _compact(text):
            return source
    return ""


def build_dimension_report(qa_records):
    evidence = {name: [] for name in DIMENSIONS}
    for number, record in enumerate(qa_records, 1):
        references = record.get("reference_points") or []
        mapping = record.get("reference_dimensions") or []
        evaluation = record.get("final_evaluation") or record.get("initial_evaluation") or {}
        assessments = evaluation.get("point_assessments") or []
        if len(mapping) != len(references):
            # 旧题没有要点映射时不猜测考查内容，更不复制整题分数。
            continue
        conflict = bool(evaluation.get("misconceptions") or evaluation.get("missing_points")) and bool(assessments) and all(
            p.get("status") == "掌握" for p in assessments
        )
        for index, (reference, dimension) in enumerate(zip(references, mapping)):
            if dimension not in evidence:
                continue
            point = assessments[index] if index < len(assessments) else {}
            status = point.get("status", "未评价")
            quote = str(point.get("answer_quote") or "").strip()
            source = quote_source(quote, record.get("answer"), record.get("follow_up_answer"))
            reason = ""
            if not record.get("answer"):
                reason = "尚未作答，不能据此判断是否掌握。"
            elif point.get("reference_point") != reference:
                reason = "逐点评价与本题参考要点不对应。"
            elif evaluation.get("premise_issue"):
                reason = "题目或参考要点前提待教师核查，暂不计分：" + str(evaluation["premise_issue"])
            elif conflict:
                reason = "逐点掌握结论与错误认识记录矛盾，需核查原回答。"
            elif evaluation.get("confidence") not in {"高", "中"}:
                reason = "AI评价把握不足，需教师核查。"
            elif status not in POINT_SCORES:
                reason = "回答未提供可判断这一要点的解释，不能等同于理解错误。"
            elif not source:
                reason = "缺少能够在实际回答中核对的原文，暂不计分。"
            evidence[dimension].append({
                "question": number, "reference_point": reference,
                "status": status, "answer_quote": quote if source else "",
                "source": source, "confidence": evaluation.get("confidence", "未记录"),
                "usable": not reason, "reason": reason,
            })

    dimensions, details = {}, {}
    for name, points in evidence.items():
        complete = bool(points) and all(p["usable"] for p in points)
        score = round(sum(POINT_SCORES[p["status"]] for p in points) / len(points)) if complete else None
        status = (
            "证据不足" if not complete else
            "需要巩固" if any(p["status"] != "掌握" for p in points) else "要点已掌握"
        )
        dimensions[name] = score
        details[name] = {
            "status": status, "score": score, "points": points,
            "reason": "本次问题未记录对应考查要点。" if not points else
                      join_clauses(dict.fromkeys(p["reason"] for p in points if p["reason"]), sentence=True) or
                      "分数来自下列要点的逐项判断，原文可核对。",
        }
    complete = all(score is not None for score in dimensions.values())
    scores = {name: score for name, score in dimensions.items() if score is not None}
    overall = round(sum(scores.values()) / len(DIMENSIONS)) if complete else None
    level = "评价尚不完整" if not complete else "理解充分" if overall >= 85 else "基本理解" if overall >= 70 else "需要巩固"
    return {
        "overall": overall, "level": level, "dimensions": dimensions,
        "weakest": min(scores, key=scores.get) if scores else "",
        "assessment_complete": complete, "assessed_dimensions": len(scores),
        "score_version": SCORE_VERSION, "dimension_evidence": details,
    }
