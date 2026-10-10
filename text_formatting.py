"""仅整理界面说明的标点；不用于学生原文、代码或引文存储。"""
import re


def clean_prose(value):
    """重复的句号、分号保留最后一种，兼容旧摘要及带空白的拼接。"""
    text = "" if value is None else str(value).strip()
    return re.sub(
        r"[。；;](?:\s*[。；;])+",
        lambda match: "；" if match.group()[-1] == ";" else match.group()[-1],
        text,
    )


def join_clauses(parts, *, sentence=False):
    """用一个分号连接非空说明，按需给整段添加一次句末标点。"""
    clauses = [clean_prose(part).rstrip("。；;").rstrip() for part in parts]
    text = "；".join(part for part in clauses if part)
    if sentence and text and text[-1] not in "。！？.!?…":
        text += "。"
    return text
