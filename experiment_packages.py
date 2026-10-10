"""Validate portable full experiment packages; importing never publishes an assignment."""
import json

import database as db


FORMAT = "wuma-experiment"
FIELDS = {
    "title", "description", "requirements", "teaching_focus", "rubric", "starter_code",
    "input_format", "output_format", "constraints_text", "public_samples", "test_cases",
    "cpp_standard", "verification_enabled", "hint_limit", "defense_admission",
    "defense_score_threshold", "reference_code", "rubric_reviewed", "explanation_required",
}
TEXT_LIMITS = {"title": 200, "description": 12000, "starter_code": 12000, "reference_code": 12000,
               "input_format": 3000, "output_format": 3000, "constraints_text": 3000}


def normalize(values):
    if not isinstance(values, dict):
        raise ValueError("实验内容必须为JSON对象。")
    unknown = set(values) - FIELDS
    if unknown:
        raise ValueError("实验包含未支持的字段，请使用完整实验导出格式。")
    result = {}
    for field, maximum in TEXT_LIMITS.items():
        value = values.get(field, "")
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError(f"{field}必须为不超过{maximum}字的文本。")
        result[field] = value if field.endswith("code") else value.strip()
    if not result["title"] or not result["description"]:
        raise ValueError("实验名称和描述不能为空。")
    for field in ["requirements", "teaching_focus"]:
        value = values.get(field)
        if (not isinstance(value, list) or not 1 <= len(value) <= 40
                or any(not isinstance(item, str) or not item.strip() or len(item) > 1500 for item in value)):
            raise ValueError(f"{field}需填写1至40条非空文本。")
        result[field] = [item.strip() for item in value]
    for field, default in [("verification_enabled", False), ("explanation_required", True), ("rubric_reviewed", False)]:
        if not isinstance(values.get(field, default), bool):
            raise ValueError(f"{field}必须为true或false。")
        result[field] = values.get(field, default)
    rubric = values.get("rubric")
    if not isinstance(rubric, list) or not rubric:
        raise ValueError("完整实验必须包含评分点。")
    for item in rubric:
        if not isinstance(item, dict) or any(not isinstance(item.get(k, ""), str)
                                             or len(item.get(k, "")) > limit
                                             for k, limit in [("name", 80), ("description", 300)]):
            raise ValueError("评分点名称与评价说明超过限制或类型不正确。")
    result["rubric"] = db.normalize_rubric(rubric)
    result["cpp_standard"] = db.normalize_cpp_standard(values.get("cpp_standard", "auto"))
    result["hint_limit"] = db.normalize_hint_limit(values.get("hint_limit", 2))
    result["defense_admission"], result["defense_score_threshold"] = db.normalize_defense_policy("全部通过", 100)
    samples = values.get("public_samples", [])
    if not isinstance(samples, list) or len(samples) > 5:
        raise ValueError("公开样例应为列表，最多5组。")
    for item in samples:
        if not isinstance(item, dict) or any(not isinstance(item.get(k, ""), str) for k in ["name", "input", "output", "explanation"]):
            raise ValueError("公开样例中的名称、输入、输出和说明必须为文本。")
        if len(item.get("name", "")) > 80 or len(item.get("explanation", "")) > 1000:
            raise ValueError("公开样例名称或说明超过长度限制。")
        if len(item.get("input", "")) > db.MAX_PUBLIC_SAMPLE_CHARS or len(item.get("output", "")) > db.MAX_PUBLIC_SAMPLE_CHARS:
            raise ValueError("公开样例的输入或输出超过长度限制。")
    result["public_samples"] = db.normalize_public_samples(samples)
    cases = values.get("test_cases", [])
    if not isinstance(cases, list) or len(cases) > 8:
        raise ValueError("隐藏测试需为列表，最多8组。")
    for item in cases:
        if not isinstance(item, dict) or any(not isinstance(item.get(k, ""), str) for k in ["name", "group", "input", "expected_output"]):
            raise ValueError("隐藏测试中的名称、分组、输入和输出必须为文本。")
        if len(item.get("name", "")) > 80 or len(item.get("group", "")) > 40:
            raise ValueError("隐藏测试名称或分组超过长度限制。")
        if len(item.get("input", "")) > db.MAX_TEST_INPUT_CHARS or len(item.get("expected_output", "")) > db.MAX_TEST_OUTPUT_CHARS:
            raise ValueError("隐藏测试的输入或输出超过长度限制。")
    result["test_cases"] = db.normalize_test_cases([
        {**item, "count_only": True, "weight": 0} for item in cases
    ]) if cases else []
    if result["verification_enabled"] and (not cases or not result["reference_code"].strip()):
        raise ValueError("启用验证时至少需要1组隐藏测试和教师参考答案。")
    # A different title or copied package must go through the normal publish-time rubric/preflight checks.
    result["rubric_reviewed"] = False
    return result


def parse(content):
    if isinstance(content, bytes):
        if len(content) > 1_000_000:
            raise ValueError("实验JSON不能超过1MB。")
        try:
            content = content.decode("utf-8-sig")
        except UnicodeError as error:
            raise ValueError("实验JSON需使用UTF-8编码。") from error
    if not isinstance(content, str) or len(content.encode("utf-8")) > 1_000_000:
        raise ValueError("实验JSON不能超过1MB。")
    try:
        def invalid_constant(value):
            raise ValueError("实验JSON不能包含NaN或Infinity。")
        document = json.loads(content.lstrip("\ufeff"), parse_constant=invalid_constant)
    except json.JSONDecodeError as error:
        raise ValueError("实验JSON格式不正确，请检查括号、引号和逗号。") from error
    if isinstance(document, dict) and "format" in document:
        if document.get("format") != FORMAT or document.get("version") != 1:
            raise ValueError("不支持该实验包的格式或版本。")
        document = document.get("experiment")
    return normalize(document)


def from_assignment(assignment, *, copy=False):
    values = {key: assignment[key] for key in FIELDS if key in assignment}
    if copy:
        values["title"] = values.get("title", "") + "（副本）"
    return normalize(values)


def export(assignment):
    document = {"format": FORMAT, "version": 1, "experiment": from_assignment(assignment)}
    return json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
