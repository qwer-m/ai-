"""
评估模块 (Evaluation Module)

该模块负责对生成的测试用例、UI/API 自动化脚本以及执行结果进行质量评估。
主要功能：
1. 计算需求覆盖率 (Recall Calculation)。
2. 评估测试用例质量 (Test Quality Evaluation)。
3. 评估 UI/API 自动化脚本及执行结果 (Automation Script Evaluation)。
4. 判定测试执行结果 (Test Result Judgment)。
"""

import csv
import io
import json
import os
import re
from collections.abc import Callable
from difflib import SequenceMatcher
from html import unescape

from sqlalchemy.orm import Session

from core.ai.ai_client import get_client_for_user
from core.db.models import Evaluation, TestGenerationComparison
from modules.test_generation_components.coverage.coverage_analyzer import analyze_coverage
from modules.test_generation_components.postprocess.case_access import (
    case_field_aliases,
    case_id as case_access_id,
    case_value,
)


_MISSING_SIGNAL_PATTERNS = (
    re.compile(r"原生成.*未(包含|覆盖|涉及|提供)"),
    re.compile(r"生成.*未(包含|覆盖|涉及|提供)"),
    re.compile(r"(未包含|未覆盖|缺失|缺少|遗漏|漏测|漏掉|需补充|需要补充|补充)"),
    re.compile(r"新增.*(用例|验证|场景|步骤)"),
)

_HALLUCINATION_SIGNAL_PATTERNS = (
    re.compile(r"(多余|冗余|无关|重复|不必要|幻觉|臆造|虚构|凭空|杜撰|不存在|误报|错误断言)"),
    re.compile(r"(生成|原用例).*(多余|冗余|重复|无关)"),
)

_DEFAULT_LLM_COMPARE_MAX_CHARS = 24000
_DEFAULT_LLM_COMPARE_MAX_CASES = 80
_DEFAULT_LLM_COMPARE_CHUNK_CASES = 6
_DEFAULT_LLM_COMPARE_CHUNK_MAX_CHARS = 9000
_DEFAULT_LLM_COMPARE_CHUNK_MAX_OUTPUT_TOKENS = 3000
_DEFAULT_LLM_COMPARE_AGGREGATE_MAX_OUTPUT_TOKENS = 4000
_DEFAULT_LLM_COMPARE_SINGLE_PASS_MAX_CHARS = 60000
_DEFAULT_LLM_COMPARE_SINGLE_PASS_MAX_OUTPUT_TOKENS = 7000
_DEFAULT_LLM_COMPARE_SINGLE_PASS_BRIEF_CHARS = 90
_DEFAULT_LLM_COMPARE_SINGLE_PASS_REQUIREMENT_CHARS = 1800
_DEFAULT_LLM_COMPARE_SINGLE_PASS_DEFECT_LIMIT = 10
_DEFAULT_LLM_COMPARE_SINGLE_PASS_DEFECT_CHARS = 80
_DEFAULT_LLM_COMPARE_NEAREST_CANDIDATES = 2
_DEFAULT_LLM_COMPARE_CHUNK_RETRIES = 1
_DEFAULT_LLM_COMPARE_SUB_CHUNK_RETRIES = 0
_DEFAULT_LLM_COMPARE_AGGREGATE_RETRIES = 1
_DEFAULT_LLM_COMPARE_EMPTY_FAILURE_LIMIT = 6
_DEFAULT_LLM_COMPARE_PARTIAL_RESULT_LIMIT = 120
_LOCAL_COMPARE_MATCH_THRESHOLD = 0.62
_LOCAL_COMPARE_MODIFIED_THRESHOLD = 0.92

_CASE_ID_ALIASES = case_field_aliases(
    "id",
    "caseid",
    "tcid",
    "case no",
    "case_no",
    "用例id",
    "用例ID",
    "测试用例编号",
)
_CASE_DESC_ALIASES = case_field_aliases(
    "description",
    "case_name",
    "test_case",
    "testcase",
    "summary",
    "scenario",
    "测试场景",
    "场景",
    "功能点",
)
_CASE_MODULE_ALIASES = case_field_aliases(
    "test_module",
    "feature",
    "component",
    "test module",
    "一级模块",
    "二级模块",
)
_CASE_STEPS_ALIASES = case_field_aliases(
    "steps",
    "actions",
    "procedure",
)
_CASE_EXPECTED_ALIASES = case_field_aliases(
    "expected_result",
    "expect",
    "预期",
    "预期输出",
)

_CJK_STOP_CHARS = set("的一是在和与及或但并对为以于中后前能可应需要时将按个项条")

def _truncate_utf8_for_mysql_text(value: str, max_bytes: int = 65000) -> str:
    text = value or ""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    clipped = raw[:max_bytes]
    while clipped:
        try:
            return clipped.decode("utf-8")
        except UnicodeDecodeError:
            clipped = clipped[:-1]
    return ""


def _to_clean_text_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            cleaned.append(text)
    return cleaned


def _count_signal(items: list[str], patterns: tuple[re.Pattern[str], ...]) -> int:
    score = 0
    for item in items:
        if any(p.search(item) for p in patterns):
            score += 1
    return score


def _should_swap_defect_lists(missing_points: list[str], hallucinations: list[str]) -> bool:
    """
    当 LLM 明显把 missing_points / hallucinations 方向写反时进行纠偏。
    仅在文本信号足够明显时触发，避免误伤正常结果。
    """
    current_orientation_score = _count_signal(missing_points, _MISSING_SIGNAL_PATTERNS) + _count_signal(hallucinations, _HALLUCINATION_SIGNAL_PATTERNS)
    swapped_orientation_score = _count_signal(missing_points, _HALLUCINATION_SIGNAL_PATTERNS) + _count_signal(hallucinations, _MISSING_SIGNAL_PATTERNS)
    total_signal = current_orientation_score + swapped_orientation_score
    return total_signal >= 2 and swapped_orientation_score >= current_orientation_score + 1


def _safe_compare_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip())
    except Exception:
        return default
    return value if value > 0 else default


def _normalize_header(value: object) -> str:
    text = str(value or "").strip().lstrip("\ufeff")
    return re.sub(r"[\s_：:()（）\-]+", "", text).lower()


_KNOWN_CASE_HEADERS = {
    _normalize_header(alias)
    for aliases in (
        _CASE_ID_ALIASES,
        _CASE_DESC_ALIASES,
        _CASE_MODULE_ALIASES,
        _CASE_STEPS_ALIASES,
        _CASE_EXPECTED_ALIASES,
    )
    for alias in aliases
}


def _case_value_to_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "；".join(_case_value_to_text(item) for item in value if _case_value_to_text(item))
    if isinstance(value, dict):
        return "；".join(
            f"{key}:{_case_value_to_text(val)}"
            for key, val in value.items()
            if _case_value_to_text(val)
        )
    return str(value).strip()


def _case_field_text(case: dict[str, object], field: str) -> str:
    return _case_value_to_text(case_value(case, field, ""))


def _get_case_field(item: dict[str, object], aliases: tuple[str, ...]) -> object:
    normalized_aliases = {_normalize_header(alias) for alias in aliases}
    for key, value in item.items():
        if _normalize_header(key) in normalized_aliases and _case_value_to_text(value):
            return value
    return ""


def _extract_json_payload_text(text: str) -> str:
    value = text.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", value)
    if fenced:
        value = fenced.group(1).strip()
    return value


def _extract_nested_case_items(value: object) -> object:
    if not isinstance(value, dict):
        return value
    for key in ("cases", "generated_result", "test_cases", "data", "items", "rows", "records"):
        nested = value.get(key)
        if isinstance(nested, (list, dict)) or (isinstance(nested, str) and nested.strip()):
            return nested
    return value


def _looks_like_case_row(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    headers = {_normalize_header(key) for key in item.keys()}
    return bool(headers & _KNOWN_CASE_HEADERS)


def _parse_partial_json_case_rows(text: str) -> list[dict[str, object]]:
    decoder = json.JSONDecoder()
    rows: list[dict[str, object]] = []
    idx = 0
    while idx < len(text):
        start = text.find("{", idx)
        if start < 0:
            break
        try:
            parsed, offset = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            idx = start + 1
            continue
        if isinstance(parsed, dict) and _looks_like_case_row(parsed):
            rows.append(parsed)
        idx = start + max(offset, 1)
    return rows


def _case_rows_from_matrix(raw_rows: list[list[object]]) -> list[dict[str, object]]:
    header_idx = -1
    headers: list[str] = []
    for idx, row in enumerate(raw_rows[:30]):
        normalized_headers = [_normalize_header(cell) for cell in row]
        known_count = sum(1 for header in normalized_headers if header in _KNOWN_CASE_HEADERS)
        if known_count >= 2:
            header_idx = idx
            headers = [str(cell or "").strip() for cell in row]
            break
    if header_idx < 0 or not headers:
        return []

    rows: list[dict[str, object]] = []
    for row in raw_rows[header_idx + 1 :]:
        cleaned = {
            headers[col_idx]: _case_value_to_text(value)
            for col_idx, value in enumerate(row[: len(headers)])
            if headers[col_idx] and _case_value_to_text(value)
        }
        if cleaned:
            rows.append(cleaned)
    return rows


def _parse_csv_case_rows(text: str) -> list[dict[str, object]]:
    sample = text.strip()
    if not sample or "\n" not in sample:
        return []
    try:
        dialect = csv.Sniffer().sniff(sample[:4096], delimiters=",\t;，")
    except Exception:
        dialect = csv.excel

    try:
        raw_rows = list(csv.reader(io.StringIO(sample), dialect=dialect))
        return _case_rows_from_matrix(raw_rows)
    except Exception:
        return []


def _parse_html_table_case_rows(text: str) -> list[dict[str, object]]:
    if "<table" not in text.lower():
        return []
    try:
        import pandas as pd

        frames = pd.read_html(io.StringIO(text))
    except Exception:
        return []

    rows: list[dict[str, object]] = []
    for frame in frames:
        try:
            frame = frame.fillna("")
            matrix = [list(frame.columns)] + frame.values.tolist()
            parsed = _case_rows_from_matrix(matrix)
            if parsed:
                rows.extend(parsed)
                continue
            rows.extend(_case_rows_from_matrix(frame.values.tolist()))
        except Exception:
            continue
    return rows


def _normalize_case_record(item: object, index: int) -> dict[str, object]:
    if isinstance(item, dict):
        source = dict(item)
        case_id = _case_value_to_text(_get_case_field(source, _CASE_ID_ALIASES))
        auto_id = False
        if not case_id:
            case_id = f"CASE-{index + 1}"
            auto_id = True

        description = _case_value_to_text(_get_case_field(source, _CASE_DESC_ALIASES))
        test_module = _case_value_to_text(_get_case_field(source, _CASE_MODULE_ALIASES))
        steps = _get_case_field(source, _CASE_STEPS_ALIASES)
        expected_result = _case_value_to_text(_get_case_field(source, _CASE_EXPECTED_ALIASES))
        known_headers = {
            _normalize_header(alias)
            for aliases in (
                _CASE_ID_ALIASES,
                _CASE_DESC_ALIASES,
                _CASE_MODULE_ALIASES,
                _CASE_STEPS_ALIASES,
                _CASE_EXPECTED_ALIASES,
            )
            for alias in aliases
        }
        extra_fields = {
            str(key).strip(): _case_value_to_text(value)
            for key, value in source.items()
            if str(key).strip()
            and _normalize_header(key) not in known_headers
            and _case_value_to_text(value)
        }

        if not description:
            visible_values = [
                _case_value_to_text(value)
                for key, value in source.items()
                if _normalize_header(key) not in {_normalize_header(alias) for alias in _CASE_ID_ALIASES}
            ]
            description = "；".join(value for value in visible_values if value)[:500]

        return {
            "id": str(case_id),
            "description": str(description or ""),
            "test_module": str(test_module or ""),
            "steps": steps if isinstance(steps, list) else _case_value_to_text(steps),
            "expected_result": str(expected_result or ""),
            "extra_fields": extra_fields,
            "_auto_id": auto_id,
            "_source_index": index,
        }

    return {
        "id": f"CASE-{index + 1}",
        "description": str(item or "").strip(),
        "test_module": "",
        "steps": [],
        "expected_result": "",
        "_auto_id": True,
        "_source_index": index,
    }


def _parse_test_cases_payload(raw: object, limit: int | None = None) -> list[dict[str, object]]:
    def normalize_many(items: list[object]) -> list[dict[str, object]]:
        max_items = limit if limit is not None else len(items)
        return [_normalize_case_record(item, idx) for idx, item in enumerate(items[:max_items])]

    value = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []

        json_text = _extract_json_payload_text(text)
        try:
            value = json.loads(json_text)
            value = _extract_nested_case_items(value)
            if isinstance(value, str) and value.strip() != text:
                return _parse_test_cases_payload(value, limit=limit)
        except Exception:
            partial_json_rows = _parse_partial_json_case_rows(json_text)
            if partial_json_rows:
                return normalize_many(partial_json_rows)
            html_rows = _parse_html_table_case_rows(text)
            if html_rows:
                return normalize_many(html_rows)
            csv_rows = _parse_csv_case_rows(text)
            if csv_rows:
                return normalize_many(csv_rows)
            lines = [line.strip() for line in re.split(r"[\n\r]+", unescape(text)) if line.strip()]
            if not lines:
                return [_normalize_case_record(text[:500], 0)]
            max_items = limit if limit is not None else min(len(lines), 120)
            return [_normalize_case_record(line[:500], idx) for idx, line in enumerate(lines[:max_items])]

    value = _extract_nested_case_items(value)
    if isinstance(value, str):
        return _parse_test_cases_payload(value, limit=limit)
    if isinstance(value, dict):
        return [_normalize_case_record(value, 0)]
    if isinstance(value, list):
        return normalize_many(value)
    return []


def _case_text(case: dict[str, object]) -> str:
    return " ".join(
        part
        for part in (
            _case_field_text(case, "test_module"),
            _case_field_text(case, "description"),
            _case_field_text(case, "steps"),
            _case_field_text(case, "expected_result"),
        )
        if part
    )


def _normalize_match_text(value: object) -> str:
    text = _case_value_to_text(value).lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def _case_tokens(case: dict[str, object]) -> set[str]:
    text = _case_text(case).lower()
    tokens: set[str] = set()
    for token in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text):
        if len(token) == 1 and token in _CJK_STOP_CHARS:
            continue
        tokens.add(token)
    return tokens


def _case_similarity(left: dict[str, object], right: dict[str, object]) -> float:
    left_text = _normalize_match_text(_case_text(left))
    right_text = _normalize_match_text(_case_text(right))
    if not left_text or not right_text:
        return 0.0
    if left_text == right_text:
        return 1.0

    sequence_score = SequenceMatcher(None, left_text, right_text).ratio()
    left_tokens = _case_tokens(left)
    right_tokens = _case_tokens(right)
    if left_tokens and right_tokens:
        jaccard_score = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
        containment_score = len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))
    else:
        jaccard_score = 0.0
        containment_score = 0.0
    return round(max(sequence_score, jaccard_score, containment_score * 0.95), 4)


def _is_meaningful_case_id(case: dict[str, object]) -> bool:
    case_id = case_access_id(case)
    if not case_id or bool(case.get("_auto_id")):
        return False
    return bool(re.search(r"[A-Za-z0-9\u4e00-\u9fff]", case_id))


def _format_case_point(case: dict[str, object]) -> str:
    case_id = case_access_id(case)
    module = _case_field_text(case, "test_module")
    description = _case_field_text(case, "description") or _case_text(case)
    prefix_parts = [part for part in (case_id if _is_meaningful_case_id(case) else "", module) if part]
    prefix = " / ".join(prefix_parts)
    text = f"{prefix} - {description}" if prefix else description
    text = re.sub(r"\s+", " ", text).strip()
    return text[:180]


def _is_case_modified(left: dict[str, object], right: dict[str, object], score: float) -> bool:
    if score < _LOCAL_COMPARE_MODIFIED_THRESHOLD:
        return True
    comparable_fields = ("description", "test_module", "steps", "expected_result")
    for field in comparable_fields:
        left_text = _normalize_match_text(_case_field_text(left, field))
        right_text = _normalize_match_text(_case_field_text(right, field))
        if left_text != right_text:
            return True
    return False


def _round_metric(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)


def _is_model_error_result(result: str) -> bool:
    text = (result or "").strip()
    lowered = text.lower()
    if not text:
        return False
    if lowered.startswith("error: http "):
        return True
    return any(
        marker in lowered
        for marker in (
            "gateway time-out",
            "gateway timeout",
            "504",
            "<html",
            "upstream request timeout",
        )
    )


def _model_result_preview(raw_result: str, max_chars: int = 300) -> str:
    text = re.sub(r"\s+", " ", str(raw_result or "")).strip()
    return text[:max_chars]


def _extract_json_object_text(raw_result: str) -> str:
    text = (raw_result or "").strip()
    if not text:
        return ""
    block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    if block and block.group(1):
        text = block.group(1).strip()
    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, offset = decoder.raw_decode(text[idx:])
        except Exception:
            continue
        if isinstance(payload, dict):
            return text[idx : idx + offset]
    return text


def _parse_model_json_payload(raw_result: str, label: str) -> dict[str, object]:
    if _is_model_error_result(raw_result):
        raise RuntimeError(f"{label} 模型返回错误：{_model_result_preview(raw_result, 500)}")
    json_text = _extract_json_object_text(raw_result)
    try:
        payload = json.loads(_normalize_compare_result_json(json_text))
    except Exception as e:
        raise ValueError(
            f"{label} 模型未返回可解析 JSON：{e}; raw_preview={_model_result_preview(raw_result)}"
        ) from e
    if not isinstance(payload, dict):
        raise ValueError(f"{label} 模型未返回 JSON 对象：{_model_result_preview(raw_result)}")
    if "metrics" not in payload or "defect_analysis" not in payload:
        raise ValueError(
            f"{label} 模型返回 JSON 结构不完整，缺少 metrics 或 defect_analysis：{_model_result_preview(raw_result)}"
        )
    return payload


def _is_fast_fail_compare_error(error: object) -> bool:
    text = str(error or "").lower()
    return (
        "empty response from model" in text
        or "error: empty response" in text
        or "raw_preview=error: empty response" in text
        or "context length" in text
        or "maximum context" in text
        or "too many tokens" in text
    )


def _truncate_compare_field(value: object, max_chars: int = 700) -> str:
    text = _case_value_to_text(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...[已截断]"


def _compact_case_for_llm(case: dict[str, object]) -> dict[str, object]:
    extra_fields = case.get("extra_fields") if isinstance(case.get("extra_fields"), dict) else {}
    compact_extra: dict[str, str] = {}
    for idx, (key, value) in enumerate(extra_fields.items()):
        if idx >= 4:
            break
        compact_extra[str(key)[:60]] = _truncate_compare_field(value, 140)
    return {
        "id": case_access_id(case),
        "module": _truncate_compare_field(_case_field_text(case, "test_module"), 120),
        "description": _truncate_compare_field(_case_field_text(case, "description"), 320),
        "steps": _truncate_compare_field(_case_field_text(case, "steps"), 450),
        "expected_result": _truncate_compare_field(_case_field_text(case, "expected_result"), 320),
        "extra_fields": compact_extra,
    }


def _compact_case_brief_for_llm(case: dict[str, object], max_chars: int) -> str:
    parts = [
        case_access_id(case),
        _case_field_text(case, "test_module"),
        _case_field_text(case, "description"),
        _case_field_text(case, "steps"),
        _case_field_text(case, "expected_result"),
    ]
    brief = " | ".join(part for part in parts if part)
    brief = re.sub(r"\s+", " ", brief).strip()
    if len(brief) <= max_chars:
        return brief
    return brief[:max_chars]


def _compact_compare_unit_for_single_pass(unit: dict[str, object], case_brief_chars: int) -> dict[str, object]:
    generated_cases = unit.get("generated_cases") if isinstance(unit.get("generated_cases"), list) else []
    modified_cases = unit.get("modified_cases") if isinstance(unit.get("modified_cases"), list) else []
    return {
        "id": _case_value_to_text(unit.get("unit_id")),
        "type": _case_value_to_text(unit.get("group_type")),
        "hint": _case_value_to_text(unit.get("local_hint")),
        "sim": unit.get("local_similarity"),
        "generated": [
            _compact_case_brief_for_llm(case, case_brief_chars)
            for case in generated_cases
            if isinstance(case, dict)
        ],
        "modified": [
            _compact_case_brief_for_llm(case, case_brief_chars)
            for case in modified_cases
            if isinstance(case, dict)
        ],
    }


def _compact_requirement_baseline_for_llm(
    baseline: dict[str, object] | None,
    *,
    max_items: int = 8,
    max_chars: int = 120,
) -> dict[str, object]:
    if not isinstance(baseline, dict):
        return {}

    def compact_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            text = _case_value_to_text(item)
            if not text:
                continue
            result.append(_truncate_compare_field(text, max_chars))
            if len(result) >= max_items:
                break
        return result

    result: dict[str, object] = {}
    for key in (
        "generated_coverage_rate",
        "modified_coverage_rate",
        "generated_missing_count",
        "modified_missing_count",
    ):
        if key in baseline:
            result[key] = baseline.get(key)
    for key in (
        "requirement_points",
        "missing_in_generated",
        "missing_in_modified",
        "generated_unanchored_points",
        "modified_added_value",
        "both_missing_points",
    ):
        values = compact_list(baseline.get(key))
        if values:
            result[key] = values
    summary = _case_value_to_text(baseline.get("summary"))
    if summary:
        result["summary"] = _truncate_compare_field(summary, max_chars)
    return result


def _compare_input_stats(
    generated_test_case: str,
    modified_test_case: str,
    generated_cases: list[dict[str, object]],
    modified_cases: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "generated_chars": len(generated_test_case or ""),
        "modified_chars": len(modified_test_case or ""),
        "total_chars": len(generated_test_case or "") + len(modified_test_case or ""),
        "generated_case_count": len(generated_cases),
        "modified_case_count": len(modified_cases),
        "llm_compare_max_chars": _safe_compare_int_env(
            "EVAL_LLM_COMPARE_MAX_CHARS",
            _DEFAULT_LLM_COMPARE_MAX_CHARS,
        ),
        "llm_compare_max_cases": _safe_compare_int_env(
            "EVAL_LLM_COMPARE_MAX_CASES",
            _DEFAULT_LLM_COMPARE_MAX_CASES,
        ),
    }


def _normalize_compare_result_json(raw_result: str) -> str:
    text = (raw_result or "").strip()
    if not text:
        return raw_result

    try:
        payload = json.loads(text)
    except Exception:
        return raw_result

    if not isinstance(payload, dict):
        return raw_result

    defect_analysis = payload.get("defect_analysis")
    if not isinstance(defect_analysis, dict):
        return raw_result

    missing_points = _to_clean_text_list(defect_analysis.get("missing_points"))
    hallucinations = _to_clean_text_list(defect_analysis.get("hallucinations"))
    modifications = _to_clean_text_list(defect_analysis.get("modifications"))

    should_swap = _should_swap_defect_lists(missing_points, hallucinations)
    if should_swap:
        missing_points, hallucinations = hallucinations, missing_points

    changed = (
        should_swap
        or defect_analysis.get("missing_points") != missing_points
        or defect_analysis.get("hallucinations") != hallucinations
        or defect_analysis.get("modifications") != modifications
    )
    if not changed:
        return raw_result

    normalized_defect_analysis = dict(defect_analysis)
    normalized_defect_analysis["missing_points"] = missing_points
    normalized_defect_analysis["hallucinations"] = hallucinations
    if "modifications" in defect_analysis or modifications:
        normalized_defect_analysis["modifications"] = modifications

    payload["defect_analysis"] = normalized_defect_analysis
    return json.dumps(payload, ensure_ascii=False, indent=2)

class EvaluationModule:
    """
    评估模块类 (Evaluation Module Class)

    提供多种评估方法，支持基于 LLM 的智能评分和分析。
    """
    def __init__(self):
        pass

    def _parse_test_cases_for_requirement_baseline(self, raw: object) -> list[dict[str, object]]:
        return _parse_test_cases_payload(raw)

    def _should_use_chunked_llm_compare(
        self,
        generated_test_case: str,
        modified_test_case: str,
        generated_cases: list[dict[str, object]],
        modified_cases: list[dict[str, object]],
    ) -> bool:
        max_chars = _safe_compare_int_env("EVAL_LLM_COMPARE_MAX_CHARS", _DEFAULT_LLM_COMPARE_MAX_CHARS)
        max_cases = _safe_compare_int_env("EVAL_LLM_COMPARE_MAX_CASES", _DEFAULT_LLM_COMPARE_MAX_CASES)
        total_chars = len(generated_test_case or "") + len(modified_test_case or "")
        return (
            total_chars > max_chars
            or len(generated_cases) > max_cases
            or len(modified_cases) > max_cases
        )

    def should_run_compare_in_background(self, generated_test_case: str, modified_test_case: str) -> bool:
        generated_cases = _parse_test_cases_payload(generated_test_case)
        modified_cases = _parse_test_cases_payload(modified_test_case)
        return self._should_use_chunked_llm_compare(
            generated_test_case,
            modified_test_case,
            generated_cases,
            modified_cases,
        )

    def _build_running_compare_payload(
        self,
        *,
        comparison_id: int | None = None,
        generated_test_case: str,
        modified_test_case: str,
        generated_cases: list[dict[str, object]] | None = None,
        modified_cases: list[dict[str, object]] | None = None,
        input_stats: dict[str, object] | None = None,
        progress: dict[str, object] | None = None,
        partial_chunk_results: list[dict[str, object]] | None = None,
        summary: str | None = None,
    ) -> dict[str, object]:
        if input_stats is None:
            generated_cases = generated_cases if generated_cases is not None else _parse_test_cases_payload(generated_test_case)
            modified_cases = modified_cases if modified_cases is not None else _parse_test_cases_payload(modified_test_case)
            input_stats = _compare_input_stats(
                generated_test_case,
                modified_test_case,
                generated_cases,
                modified_cases,
            )

        progress_payload = dict(progress or {})
        if summary is None:
            phase = str(progress_payload.get("phase") or "")
            total_chunks = int(progress_payload.get("total_chunks") or 0)
            completed_chunks = int(progress_payload.get("completed_chunks") or 0)
            if phase == "single_pass_evaluating":
                summary = "模型质量评估正在进行全量平衡评估：会同时参考需求摘要、AI 生成用例与人工最终用例。"
            elif total_chunks:
                summary = f"模型质量评估后台执行中，已完成 {completed_chunks}/{total_chunks} 个分片；已完成分片会先展示，最终指标将在汇总后生成。"
            else:
                summary = "模型质量评估已转入后台执行，页面将自动刷新结果。"

        payload: dict[str, object] = {
            "analysis_status": "running",
            "analysis_mode": "llm_background",
            "is_final_evaluation": False,
            "metrics": {},
            "defect_analysis": {
                "missing_points": [],
                "hallucinations": [],
                "modifications": [],
            },
            "input_stats": input_stats,
            "summary": summary,
        }
        if comparison_id:
            payload["comparison_id"] = comparison_id
        if progress_payload:
            payload["progress"] = progress_payload
        if partial_chunk_results:
            partial_limit = _safe_compare_int_env(
                "EVAL_LLM_COMPARE_PARTIAL_RESULT_LIMIT",
                _DEFAULT_LLM_COMPARE_PARTIAL_RESULT_LIMIT,
            )
            payload["partial_chunk_results"] = partial_chunk_results[:partial_limit]
        return payload

    def build_running_compare_result(
        self,
        *,
        comparison_id: int,
        generated_test_case: str,
        modified_test_case: str,
    ) -> str:
        payload = self._build_running_compare_payload(
            comparison_id=comparison_id,
            generated_test_case=generated_test_case,
            modified_test_case=modified_test_case,
        )
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def build_background_exception_result(
        self,
        *,
        generated_test_case: str,
        modified_test_case: str,
        requirement_text: str = "",
        fallback_reason: str,
        comparison_id: int | None = None,
    ) -> str:
        baseline = self._build_requirement_baseline(
            requirement_text=requirement_text,
            generated_test_case=generated_test_case,
            modified_test_case=modified_test_case,
        )
        return self._build_model_failed_compare_result(
            generated_test_case=generated_test_case,
            modified_test_case=modified_test_case,
            requirement_text=requirement_text,
            baseline=baseline,
            fallback_reason=fallback_reason,
            comparison_id=comparison_id,
        )

    def _build_compare_units(
        self,
        generated_cases: list[dict[str, object]],
        modified_cases: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        nearest_limit = _safe_compare_int_env(
            "EVAL_LLM_COMPARE_NEAREST_CANDIDATES",
            _DEFAULT_LLM_COMPARE_NEAREST_CANDIDATES,
        )
        matched_generated: set[int] = set()
        matched_modified: set[int] = set()
        referenced_generated: set[int] = set()
        units: list[dict[str, object]] = []

        def add_modified_anchor_unit(
            *,
            modified_idx: int,
            generated_candidates: list[tuple[int, float, str]],
            local_hint: str,
            local_similarity: float,
        ) -> None:
            candidate_cases = []
            candidate_meta = []
            for generated_idx, score, role in generated_candidates[:nearest_limit]:
                referenced_generated.add(generated_idx)
                candidate_cases.append(_compact_case_for_llm(generated_cases[generated_idx]))
                candidate_meta.append(
                    {
                        "generated_index": generated_idx,
                        "candidate_role": role,
                        "similarity": _round_metric(score),
                    }
                )
            units.append(
                {
                    "unit_id": f"modified_anchor_{len(units) + 1}",
                    "group_type": "modified_anchor",
                    "local_hint": local_hint,
                    "local_similarity": _round_metric(local_similarity),
                    "generated_cases": candidate_cases,
                    "generated_candidate_meta": candidate_meta,
                    "modified_cases": [_compact_case_for_llm(modified_cases[modified_idx])],
                    "modified_index": modified_idx,
                }
            )

        def nearest_generated_candidates(
            modified_case: dict[str, object],
            *,
            include_matched: bool = True,
        ) -> list[tuple[int, float, str]]:
            scored = []
            for generated_idx, generated_case in enumerate(generated_cases):
                if not include_matched and generated_idx in matched_generated:
                    continue
                score = _case_similarity(generated_case, modified_case)
                scored.append((generated_idx, score, "nearest_candidate"))
            return [
                item
                for item in sorted(scored, key=lambda value: value[1], reverse=True)[:nearest_limit]
                if item[1] >= 0.25
            ]

        generated_by_id: dict[str, list[int]] = {}
        for idx, case in enumerate(generated_cases):
            if _is_meaningful_case_id(case):
                generated_by_id.setdefault(_normalize_header(case.get("id")), []).append(idx)

        for modified_idx, modified_case in enumerate(modified_cases):
            if not _is_meaningful_case_id(modified_case):
                continue
            candidates = [
                generated_idx
                for generated_idx in generated_by_id.get(_normalize_header(modified_case.get("id")), [])
                if generated_idx not in matched_generated
            ]
            if not candidates:
                continue
            best_idx = max(
                candidates,
                key=lambda generated_idx: _case_similarity(generated_cases[generated_idx], modified_case),
            )
            score = _case_similarity(generated_cases[best_idx], modified_case)
            matched_generated.add(best_idx)
            matched_modified.add(modified_idx)
            add_modified_anchor_unit(
                modified_idx=modified_idx,
                generated_candidates=[(best_idx, max(score, 0.75), "same_case_id_candidate")],
                local_hint="same_case_id_candidate",
                local_similarity=max(score, 0.75),
            )

        semantic_candidates: list[tuple[float, int, int]] = []
        for modified_idx, modified_case in enumerate(modified_cases):
            if modified_idx in matched_modified:
                continue
            for generated_idx, generated_case in enumerate(generated_cases):
                if generated_idx in matched_generated:
                    continue
                score = _case_similarity(generated_case, modified_case)
                if score >= _LOCAL_COMPARE_MATCH_THRESHOLD:
                    semantic_candidates.append((score, generated_idx, modified_idx))

        for score, generated_idx, modified_idx in sorted(semantic_candidates, reverse=True):
            if generated_idx in matched_generated or modified_idx in matched_modified:
                continue
            matched_generated.add(generated_idx)
            matched_modified.add(modified_idx)
            add_modified_anchor_unit(
                modified_idx=modified_idx,
                generated_candidates=[(generated_idx, score, "semantic_candidate")],
                local_hint="semantic_candidate",
                local_similarity=score,
            )

        for modified_idx, modified_case in enumerate(modified_cases):
            if modified_idx in matched_modified:
                continue
            nearest = nearest_generated_candidates(modified_case)
            add_modified_anchor_unit(
                modified_idx=modified_idx,
                generated_candidates=nearest,
                local_hint="modified_case_requires_model_judgement",
                local_similarity=nearest[0][1] if nearest else 0.0,
            )

        for generated_idx, generated_case in enumerate(generated_cases):
            if generated_idx in matched_generated or generated_idx in referenced_generated:
                continue
            nearest = sorted(
                (
                    (modified_idx, _case_similarity(generated_case, modified_case), modified_case)
                    for modified_idx, modified_case in enumerate(modified_cases)
                ),
                key=lambda item: item[1],
                reverse=True,
            )[:nearest_limit]
            units.append(
                {
                    "unit_id": f"generated_unmatched_{len(units) + 1}",
                    "group_type": "generated_unmatched",
                    "local_hint": "generated_case_requires_model_judgement",
                    "local_similarity": _round_metric(nearest[0][1]) if nearest else 0.0,
                    "generated_cases": [_compact_case_for_llm(generated_case)],
                    "generated_candidate_meta": [
                        {
                            "generated_index": generated_idx,
                            "candidate_role": "generated_unmatched",
                            "similarity": 1.0,
                        }
                    ],
                    "modified_cases": [
                        _compact_case_for_llm(case)
                        for _, score, case in nearest
                        if score >= 0.25
                    ],
                    "modified_candidate_meta": [
                        {
                            "modified_index": modified_idx,
                            "candidate_role": "nearest_modified_reference",
                            "similarity": _round_metric(score),
                        }
                        for modified_idx, score, _ in nearest
                        if score >= 0.25
                    ],
                    "generated_index": generated_idx,
                }
            )

        if not units:
            units.append(
                {
                    "unit_id": "empty_or_unparsed_input",
                    "group_type": "unparsed_input",
                    "local_hint": "unparsed_input",
                    "local_similarity": 0.0,
                    "generated_cases": [_compact_case_for_llm(case) for case in generated_cases[:5]],
                    "modified_cases": [_compact_case_for_llm(case) for case in modified_cases[:5]],
                }
            )
        return units

    def _chunk_compare_units(self, units: list[dict[str, object]]) -> list[list[dict[str, object]]]:
        max_cases = _safe_compare_int_env(
            "EVAL_LLM_COMPARE_CHUNK_GROUPS",
            _safe_compare_int_env("EVAL_LLM_COMPARE_CHUNK_CASES", _DEFAULT_LLM_COMPARE_CHUNK_CASES),
        )
        max_chars = _safe_compare_int_env("EVAL_LLM_COMPARE_CHUNK_MAX_CHARS", _DEFAULT_LLM_COMPARE_CHUNK_MAX_CHARS)
        chunks: list[list[dict[str, object]]] = []
        current: list[dict[str, object]] = []
        current_chars = 0
        for unit in units:
            unit_chars = len(json.dumps(unit, ensure_ascii=False))
            if current and (len(current) >= max_cases or current_chars + unit_chars > max_chars):
                chunks.append(current)
                current = []
                current_chars = 0
            current.append(unit)
            current_chars += unit_chars
        if current:
            chunks.append(current)
        return chunks

    def _build_deterministic_compare_result(
        self,
        *,
        generated_test_case: str,
        modified_test_case: str,
        requirement_text: str = "",
        baseline: dict[str, object] | None = None,
        analysis_mode: str = "deterministic_large_input",
        fallback_reason: str = "",
    ) -> str:
        generated_cases = _parse_test_cases_payload(generated_test_case)
        modified_cases = _parse_test_cases_payload(modified_test_case)

        matched_generated: set[int] = set()
        matched_modified: set[int] = set()
        matches: list[tuple[int, int, float, str]] = []

        generated_by_id: dict[str, list[int]] = {}
        for idx, case in enumerate(generated_cases):
            if _is_meaningful_case_id(case):
                generated_by_id.setdefault(_normalize_header(case.get("id")), []).append(idx)

        for modified_idx, modified_case in enumerate(modified_cases):
            if not _is_meaningful_case_id(modified_case):
                continue
            candidates = [
                generated_idx
                for generated_idx in generated_by_id.get(_normalize_header(modified_case.get("id")), [])
                if generated_idx not in matched_generated
            ]
            if not candidates:
                continue
            best_idx = max(
                candidates,
                key=lambda generated_idx: _case_similarity(generated_cases[generated_idx], modified_case),
            )
            best_score = max(_case_similarity(generated_cases[best_idx], modified_case), 0.75)
            matched_generated.add(best_idx)
            matched_modified.add(modified_idx)
            matches.append((best_idx, modified_idx, best_score, "id"))

        semantic_candidates: list[tuple[float, int, int]] = []
        for modified_idx, modified_case in enumerate(modified_cases):
            if modified_idx in matched_modified:
                continue
            for generated_idx, generated_case in enumerate(generated_cases):
                if generated_idx in matched_generated:
                    continue
                score = _case_similarity(generated_case, modified_case)
                if score >= _LOCAL_COMPARE_MATCH_THRESHOLD:
                    semantic_candidates.append((score, generated_idx, modified_idx))

        for score, generated_idx, modified_idx in sorted(semantic_candidates, reverse=True):
            if generated_idx in matched_generated or modified_idx in matched_modified:
                continue
            matched_generated.add(generated_idx)
            matched_modified.add(modified_idx)
            matches.append((generated_idx, modified_idx, score, "semantic"))

        generated_count = len(generated_cases)
        modified_count = len(modified_cases)
        matched_count = len(matches)
        precision = _round_metric(matched_count / generated_count) if generated_count else 0.0
        recall = _round_metric(matched_count / modified_count) if modified_count else 0.0
        f1_score = _round_metric((2 * precision * recall) / (precision + recall)) if precision + recall else 0.0
        score_sum = sum(score for _, _, score, _ in matches)
        semantic_similarity = _round_metric(score_sum / max(generated_count, modified_count, 1))

        missing_points = [
            _format_case_point(case)
            for idx, case in enumerate(modified_cases)
            if idx not in matched_modified
        ]
        hallucinations = [
            _format_case_point(case)
            for idx, case in enumerate(generated_cases)
            if idx not in matched_generated
        ]
        modifications = [
            f"{_format_case_point(modified_cases[modified_idx])}（匹配方式：{match_type}，相似度：{score:.2f}）"
            for generated_idx, modified_idx, score, match_type in matches
            if _is_case_modified(generated_cases[generated_idx], modified_cases[modified_idx], score)
        ]

        payload: dict[str, object] = {
            "analysis_mode": analysis_mode,
            "metrics": {
                "precision": precision,
                "recall": recall,
                "f1_score": f1_score,
                "semantic_similarity": semantic_similarity,
            },
            "defect_analysis": {
                "missing_points": missing_points[:20],
                "hallucinations": hallucinations[:20],
                "modifications": modifications[:20],
            },
            "input_stats": {
                "generated_chars": len(generated_test_case or ""),
                "modified_chars": len(modified_test_case or ""),
                "total_chars": len(generated_test_case or "") + len(modified_test_case or ""),
                "generated_case_count": generated_count,
                "modified_case_count": modified_count,
                "matched_case_count": matched_count,
                "llm_compare_max_chars": _safe_compare_int_env(
                    "EVAL_LLM_COMPARE_MAX_CHARS",
                    _DEFAULT_LLM_COMPARE_MAX_CHARS,
                ),
                "llm_compare_max_cases": _safe_compare_int_env(
                    "EVAL_LLM_COMPARE_MAX_CASES",
                    _DEFAULT_LLM_COMPARE_MAX_CASES,
                ),
            },
            "summary": (
                f"已使用本地结构化比较完成评估：AI 用例 {generated_count} 条，"
                f"人工版本 {modified_count} 条，匹配 {matched_count} 条；"
                f"缺失 {len(missing_points)} 条，冗余 {len(hallucinations)} 条，修改 {len(modifications)} 条。"
            ),
        }
        if fallback_reason:
            payload["fallback_reason"] = fallback_reason[:500]

        if requirement_text.strip():
            heuristic = baseline or self._build_requirement_baseline(
                requirement_text=requirement_text,
                generated_test_case=generated_test_case,
                modified_test_case=modified_test_case,
            )
            payload["requirement_baseline"] = {
                **heuristic,
                "ai_requirement_gaps": heuristic.get("missing_in_generated") or [],
                "human_requirement_gaps": heuristic.get("missing_in_modified") or [],
                "human_added_value": missing_points[:8],
                "summary": "已使用本地结构化比较附加需求锚定覆盖统计。",
            }

        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _build_model_failed_compare_result(
        self,
        *,
        generated_test_case: str,
        modified_test_case: str,
        requirement_text: str = "",
        baseline: dict[str, object] | None = None,
        fallback_reason: str,
        partial_chunk_results: list[dict[str, object]] | None = None,
        progress: dict[str, object] | None = None,
        input_stats: dict[str, object] | None = None,
        comparison_id: int | None = None,
    ) -> str:
        local_preanalysis = json.loads(
            self._build_deterministic_compare_result(
                generated_test_case=generated_test_case,
                modified_test_case=modified_test_case,
                requirement_text=requirement_text,
                baseline=baseline,
                analysis_mode="local_preanalysis",
            )
        )
        local_preanalysis.pop("analysis_status", None)
        local_preanalysis["is_final_evaluation"] = False
        payload: dict[str, object] = {
            "analysis_status": "model_failed",
            "analysis_mode": "model_required_but_failed",
            "is_final_evaluation": False,
            "summary": "模型评测未完成，当前未生成正式质量评估结果；local_preanalysis 仅供排查和重试前预览。",
            "fallback_reason": fallback_reason[:800],
            "metrics": {},
            "defect_analysis": {
                "missing_points": [],
                "hallucinations": [],
                "modifications": [],
            },
            "local_preanalysis": local_preanalysis,
        }
        if comparison_id:
            payload["comparison_id"] = comparison_id
        if input_stats:
            payload["input_stats"] = input_stats
        if progress:
            payload["progress"] = progress
        if partial_chunk_results:
            partial_limit = _safe_compare_int_env(
                "EVAL_LLM_COMPARE_PARTIAL_RESULT_LIMIT",
                _DEFAULT_LLM_COMPARE_PARTIAL_RESULT_LIMIT,
            )
            payload["partial_chunk_results"] = partial_chunk_results[:partial_limit]
        if requirement_text.strip() and baseline:
            payload["requirement_baseline"] = {
                **baseline,
                "summary": "模型评测失败，仅展示启发式需求覆盖统计，不作为正式评测结论。",
            }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _build_partial_completed_compare_result(
        self,
        *,
        generated_test_case: str,
        modified_test_case: str,
        requirement_text: str = "",
        baseline: dict[str, object] | None = None,
        fallback_reason: str,
        partial_chunk_results: list[dict[str, object]],
        progress: dict[str, object] | None = None,
        input_stats: dict[str, object] | None = None,
        comparison_id: int | None = None,
    ) -> str:
        payload = json.loads(
            self._build_model_failed_compare_result(
                generated_test_case=generated_test_case,
                modified_test_case=modified_test_case,
                requirement_text=requirement_text,
                baseline=baseline,
                fallback_reason=fallback_reason,
                partial_chunk_results=partial_chunk_results,
                progress=progress,
                input_stats=input_stats,
                comparison_id=comparison_id,
            )
        )
        payload["analysis_status"] = "partial_completed"
        payload["analysis_mode"] = "llm_chunked_partial"
        payload["is_final_evaluation"] = False
        payload["summary"] = "部分分片已完成，当前展示已完成分片预览；由于仍有分片或汇总失败，本结果不作为正式质量评估结论。"
        payload["metrics"] = {}
        payload["defect_analysis"] = {
            "missing_points": [],
            "hallucinations": [],
            "modifications": [],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _finalize_llm_compare_result(
        self,
        *,
        raw_result: str,
        baseline: dict[str, object],
        requirement_text: str,
        analysis_mode: str,
        input_stats: dict[str, object],
        chunk_summary: dict[str, object] | None = None,
        comparison_id: int | None = None,
        analysis_status: str = "completed",
        is_final_evaluation: bool = True,
        partial_chunk_results: list[dict[str, object]] | None = None,
        progress: dict[str, object] | None = None,
    ) -> str:
        payload = _parse_model_json_payload(raw_result, "最终评测")

        model_baseline = payload.get("requirement_baseline")
        if isinstance(model_baseline, dict):
            payload["requirement_baseline"] = {
                **model_baseline,
                "heuristic": baseline,
            }
        elif requirement_text.strip():
            payload["requirement_baseline"] = {
                **baseline,
                "summary": "已启用需求基准评估，但模型未返回结构化需求锚定分析，当前展示启发式覆盖统计。",
            }
        payload["analysis_status"] = analysis_status
        payload["analysis_mode"] = analysis_mode
        payload["is_final_evaluation"] = is_final_evaluation
        payload["input_stats"] = input_stats
        if analysis_status == "partial_completed":
            original_summary = str(payload.get("summary") or "").strip()
            payload["summary"] = (
                "部分分片评估完成，以下为已完成分片的汇总预览；仍有分片失败，本结果不作为完整正式质量评估结论。"
                + (f" 模型汇总：{original_summary}" if original_summary else "")
            )
        if comparison_id:
            payload["comparison_id"] = comparison_id
        if progress:
            payload["progress"] = progress
        if partial_chunk_results:
            partial_limit = _safe_compare_int_env(
                "EVAL_LLM_COMPARE_PARTIAL_RESULT_LIMIT",
                _DEFAULT_LLM_COMPARE_PARTIAL_RESULT_LIMIT,
            )
            payload["partial_chunk_results"] = partial_chunk_results[:partial_limit]
        if chunk_summary:
            payload["chunk_summary"] = chunk_summary
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _compare_test_cases_with_llm_chunks(
        self,
        *,
        client,
        generated_test_case: str,
        modified_test_case: str,
        generated_cases: list[dict[str, object]],
        modified_cases: list[dict[str, object]],
        baseline: dict[str, object],
        requirement_text: str = "",
        progress_callback: Callable[[dict[str, object]], None] | None = None,
        comparison_id: int | None = None,
    ) -> str:
        units = self._build_compare_units(generated_cases, modified_cases)
        chunks = self._chunk_compare_units(units)
        input_stats = _compare_input_stats(
            generated_test_case,
            modified_test_case,
            generated_cases,
            modified_cases,
        )
        input_stats["comparison_unit_count"] = len(units)
        input_stats["comparison_group_count"] = len(units)
        input_stats["llm_chunk_count"] = len(chunks)
        input_stats["chunk_alignment_strategy"] = "modified_anchor_case_groups"
        chunk_max_output_tokens = _safe_compare_int_env(
            "EVAL_LLM_COMPARE_CHUNK_MAX_OUTPUT_TOKENS",
            _DEFAULT_LLM_COMPARE_CHUNK_MAX_OUTPUT_TOKENS,
        )
        aggregate_max_output_tokens = _safe_compare_int_env(
            "EVAL_LLM_COMPARE_AGGREGATE_MAX_OUTPUT_TOKENS",
            _DEFAULT_LLM_COMPARE_AGGREGATE_MAX_OUTPUT_TOKENS,
        )
        single_pass_max_chars = _safe_compare_int_env(
            "EVAL_LLM_COMPARE_SINGLE_PASS_MAX_CHARS",
            _DEFAULT_LLM_COMPARE_SINGLE_PASS_MAX_CHARS,
        )
        single_pass_max_output_tokens = _safe_compare_int_env(
            "EVAL_LLM_COMPARE_SINGLE_PASS_MAX_OUTPUT_TOKENS",
            _DEFAULT_LLM_COMPARE_SINGLE_PASS_MAX_OUTPUT_TOKENS,
        )
        single_pass_brief_chars = _safe_compare_int_env(
            "EVAL_LLM_COMPARE_SINGLE_PASS_BRIEF_CHARS",
            _DEFAULT_LLM_COMPARE_SINGLE_PASS_BRIEF_CHARS,
        )
        single_pass_requirement_chars = _safe_compare_int_env(
            "EVAL_LLM_COMPARE_SINGLE_PASS_REQUIREMENT_CHARS",
            _DEFAULT_LLM_COMPARE_SINGLE_PASS_REQUIREMENT_CHARS,
        )
        single_pass_defect_limit = _safe_compare_int_env(
            "EVAL_LLM_COMPARE_SINGLE_PASS_DEFECT_LIMIT",
            _DEFAULT_LLM_COMPARE_SINGLE_PASS_DEFECT_LIMIT,
        )
        single_pass_defect_chars = _safe_compare_int_env(
            "EVAL_LLM_COMPARE_SINGLE_PASS_DEFECT_CHARS",
            _DEFAULT_LLM_COMPARE_SINGLE_PASS_DEFECT_CHARS,
        )
        chunk_retries = _safe_compare_int_env(
            "EVAL_LLM_COMPARE_CHUNK_RETRIES",
            _DEFAULT_LLM_COMPARE_CHUNK_RETRIES,
        )
        sub_chunk_retries = _safe_compare_int_env(
            "EVAL_LLM_COMPARE_SUB_CHUNK_RETRIES",
            _DEFAULT_LLM_COMPARE_SUB_CHUNK_RETRIES,
        )
        aggregate_retries = _safe_compare_int_env(
            "EVAL_LLM_COMPARE_AGGREGATE_RETRIES",
            _DEFAULT_LLM_COMPARE_AGGREGATE_RETRIES,
        )
        empty_failure_limit = _safe_compare_int_env(
            "EVAL_LLM_COMPARE_EMPTY_FAILURE_LIMIT",
            _DEFAULT_LLM_COMPARE_EMPTY_FAILURE_LIMIT,
        )
        compare_model_override = os.getenv("EVAL_LLM_COMPARE_MODEL", "").strip() or None

        chunk_system_prompt = """
        你是测试用例质量评估员。你会收到完整评估任务中的一个对齐分片。
        该分片由本地结构化解析和相似度对齐得到，按“人工最终用例 + AI 候选用例”组织。
        modified_anchor 表示以人工最终用例为锚点判断 AI 是否覆盖；generated_unmatched 表示 AI 生成用例未被任何人工锚点引用，需要判断是否多余/幻觉。
        你只对当前分片给出模型判断，不要编造当前分片之外的用例。
        请只做必要判断，不展开推理过程，不输出 Markdown。
        每个列表最多返回 5 项，每条不超过 80 个中文字符。
        case_judgements 每个 comparison group 最多 1 条。
        必须返回严格 JSON，中文输出。
        JSON 格式：
        {
          "chunk_index": 1,
          "metrics": {"precision": 0.0, "recall": 0.0, "f1_score": 0.0, "semantic_similarity": 0.0},
          "defect_analysis": {
            "missing_points": [],
            "hallucinations": [],
            "modifications": []
          },
          "case_judgements": [
            {"unit_id": "id", "verdict": "matched|missing_in_generated|hallucination|modified|uncertain", "reason": "中文理由"}
          ],
          "summary": "中文分片结论"
        }
        字段方向必须遵守：
        - missing_points 只放人工版本有、AI 生成缺失的内容。
        - hallucinations 只放 AI 生成有、人工版本没有的内容。
        - modifications 放两边都覆盖但步骤、预期、约束或可执行性被人工修正的内容。
        """
        chunk_system_prompt = """
        你是测试用例质量评估器。只评估用户输入里的 comparison_groups。
        modified_anchor：以人工最终用例为锚点，判断 AI 候选是否覆盖。
        generated_unmatched：AI 用例未被人工锚点引用，判断是否多余或幻觉。
        只输出一个严格 JSON 对象，不要 Markdown，不要解释，不要输出推理过程。
        每个 defect_analysis 列表最多 5 条，每条不超过 80 个中文字符。
        case_judgements 每个 comparison group 最多 1 条。
        JSON schema:
        {
          "chunk_index": 1,
          "metrics": {"precision": 0.0, "recall": 0.0, "f1_score": 0.0, "semantic_similarity": 0.0},
          "defect_analysis": {"missing_points": [], "hallucinations": [], "modifications": []},
          "case_judgements": [
            {"unit_id": "id", "verdict": "matched|missing_in_generated|hallucination|modified|uncertain", "reason": "中文短句"}
          ],
          "summary": "中文短句"
        }
        方向：missing_points=人工有但 AI 缺失；hallucinations=AI 有但人工没有；modifications=两边都有但人工修正了步骤、预期、约束或可执行性。
        """
        chunk_results: list[dict[str, object]] = []
        failed_chunks: list[dict[str, object]] = []
        consecutive_fast_failures = 0
        stop_after_repeated_failures = False
        stop_reason = ""

        def build_progress(
            phase: str,
            *,
            current_chunk: object | None = None,
            retrying_chunks: list[dict[str, object]] | None = None,
            last_error: object | None = None,
        ) -> dict[str, object]:
            completed_root_chunks = {
                str(item.get("chunk_index") or "").split(".", 1)[0]
                for item in chunk_results
                if str(item.get("chunk_index") or "").strip()
            }
            progress: dict[str, object] = {
                "phase": phase,
                "completed_chunks": len(completed_root_chunks),
                "total_chunks": len(chunks),
                "completed_units": sum(int(item.get("chunk_unit_count") or 0) for item in chunk_results),
                "total_units": len(units),
                "failed_chunks": len(failed_chunks),
                "retrying_chunks": retrying_chunks or [],
            }
            if current_chunk is not None:
                progress["current_chunk"] = current_chunk
            if failed_chunks:
                progress["failed_chunk_details"] = failed_chunks[-5:]
            if last_error:
                progress["last_error"] = str(last_error)[:500]
            return progress

        def emit_progress(
            phase: str,
            *,
            current_chunk: object | None = None,
            retrying_chunks: list[dict[str, object]] | None = None,
            last_error: object | None = None,
        ) -> dict[str, object]:
            progress = build_progress(
                phase,
                current_chunk=current_chunk,
                retrying_chunks=retrying_chunks,
                last_error=last_error,
            )
            if progress_callback:
                payload = self._build_running_compare_payload(
                    comparison_id=comparison_id,
                    generated_test_case=generated_test_case,
                    modified_test_case=modified_test_case,
                    generated_cases=generated_cases,
                    modified_cases=modified_cases,
                    input_stats=input_stats,
                    progress=progress,
                    partial_chunk_results=list(chunk_results),
                )
                try:
                    progress_callback(payload)
                except Exception:
                    pass
            return progress

        compact_units = [
            _compact_compare_unit_for_single_pass(unit, single_pass_brief_chars)
            for unit in units
        ]
        compact_requirement_text = _truncate_compare_field(
            requirement_text,
            single_pass_requirement_chars,
        )
        compact_requirement_baseline = _compact_requirement_baseline_for_llm(baseline)
        single_pass_prompt = {
            "task": "全量测试用例质量对比，输出平衡 JSON",
            "group_count": len(compact_units),
            "requirement_text": compact_requirement_text,
            "requirement_heuristic_baseline": compact_requirement_baseline,
            "groups": compact_units,
            "output_rules": (
                "评估全部 groups，并参考 requirement_text 与 requirement_heuristic_baseline；"
                f"defect_analysis 每类最多 {single_pass_defect_limit} 条，"
                f"每条少于 {single_pass_defect_chars} 个中文字符；summary 少于 120 字；"
                "只输出 JSON。"
            ),
            "json_schema": {
                "metrics": {
                    "precision": 0.0,
                    "recall": 0.0,
                    "f1_score": 0.0,
                    "semantic_similarity": 0.0,
                },
                "defect_analysis": {
                    "missing_points": [],
                    "hallucinations": [],
                    "modifications": [],
                },
                "requirement_baseline": {
                    "requirement_points": [],
                    "ai_requirement_gaps": [],
                    "human_requirement_gaps": [],
                    "ai_unanchored_points": [],
                    "human_added_value": [],
                    "both_missing_points": [],
                    "summary": "中文需求锚定结论",
                },
                "summary": "中文短句",
            },
        }
        single_pass_prompt_text = json.dumps(single_pass_prompt, ensure_ascii=False, separators=(",", ":"))
        input_stats["llm_single_pass_prompt_chars"] = len(single_pass_prompt_text)
        input_stats["llm_single_pass_case_brief_chars"] = single_pass_brief_chars
        input_stats["llm_single_pass_requirement_chars"] = len(compact_requirement_text)
        input_stats["llm_single_pass_defect_limit"] = single_pass_defect_limit
        input_stats["llm_single_pass_defect_chars"] = single_pass_defect_chars

        if single_pass_max_chars > 0 and len(single_pass_prompt_text) <= single_pass_max_chars:
            single_pass_system_prompt = """
            你是测试用例质量评估器。评估用户输入里的全部 groups，并把需求文档作为覆盖判断的主锚点。
            只输出严格 JSON，不要 Markdown，不要解释，不要推理过程。
            输出要兼顾速度和可诊断性：缺陷项不能只写 TC/CASE 编号，必须写清楚业务点、生成版/人工版差异或需求锚点。
            每个 defect_analysis 列表最多 __DEFECT_LIMIT__ 条；每条不超过 __DEFECT_CHARS__ 个中文字符；summary 不超过 120 个中文字符。
            JSON schema:
            {
              "metrics": {"precision": 0.0, "recall": 0.0, "f1_score": 0.0, "semantic_similarity": 0.0},
              "defect_analysis": {"missing_points": [], "hallucinations": [], "modifications": []},
              "requirement_baseline": {
                "requirement_points": [],
                "ai_requirement_gaps": [],
                "human_requirement_gaps": [],
                "ai_unanchored_points": [],
                "human_added_value": [],
                "both_missing_points": [],
                "summary": "中文需求锚定结论"
              },
              "summary": "中文短句"
            }
            方向：missing_points=需求或人工最终用例有但 AI 缺失；hallucinations=AI 有但需求和人工最终用例都不支持；
            modifications=两边都有但人工修正了步骤、预期、约束、粒度或可执行性。
            """.replace("__DEFECT_LIMIT__", str(single_pass_defect_limit)).replace(
                "__DEFECT_CHARS__",
                str(single_pass_defect_chars),
            )
            try:
                emit_progress("single_pass_evaluating", current_chunk="all")
                raw_single_pass_result = client.generate_response(
                    single_pass_prompt_text,
                    single_pass_system_prompt,
                    max_tokens=single_pass_max_output_tokens,
                    task_type="review",
                    model=compare_model_override,
                )
                _parse_model_json_payload(raw_single_pass_result, "全量平衡评测")
                return self._finalize_llm_compare_result(
                    raw_result=raw_single_pass_result,
                    baseline=baseline,
                    requirement_text=requirement_text,
                    analysis_mode="llm_single_pass_balanced",
                    input_stats=input_stats,
                    chunk_summary={
                        "chunk_count": 1,
                        "source_chunk_count": len(chunks),
                        "comparison_unit_count": len(units),
                        "comparison_group_count": len(units),
                        "successful_chunk_result_count": 1,
                        "failed_chunk_count": 0,
                        "failed_chunk_details": [],
                        "single_pass_prompt_chars": len(single_pass_prompt_text),
                        "single_pass_case_brief_chars": single_pass_brief_chars,
                        "single_pass_requirement_chars": len(compact_requirement_text),
                        "single_pass_defect_limit": single_pass_defect_limit,
                        "single_pass_defect_chars": single_pass_defect_chars,
                        "single_pass_max_output_tokens": single_pass_max_output_tokens,
                        "model_override": compare_model_override,
                        "chunk_alignment_strategy": "modified_anchor_case_groups",
                        "requirement_text_in_prompt": bool(compact_requirement_text),
                        "requirement_baseline_in_prompt": bool(compact_requirement_baseline),
                        "aggregation": "single_pass_balanced_model",
                    },
                    comparison_id=comparison_id,
                    analysis_status="completed",
                    is_final_evaluation=True,
                )
            except Exception as single_pass_error:
                emit_progress("chunking", last_error=f"全量平衡评估失败，改用分片评估：{single_pass_error}")
        else:
            emit_progress("chunking")

        def record_failed_chunk(
            *,
            chunk_index: object,
            chunk_unit_count: int,
            error: object,
        ) -> None:
            nonlocal consecutive_fast_failures, stop_after_repeated_failures, stop_reason
            failed_chunks.append(
                {
                    "chunk_index": chunk_index,
                    "chunk_unit_count": chunk_unit_count,
                    "error": str(error)[:500],
                }
            )
            if _is_fast_fail_compare_error(error):
                consecutive_fast_failures += 1
            else:
                consecutive_fast_failures = 0

            if (
                empty_failure_limit > 0
                and consecutive_fast_failures >= empty_failure_limit
                and not stop_after_repeated_failures
            ):
                stop_after_repeated_failures = True
                stop_reason = (
                    f"模型连续 {consecutive_fast_failures} 个分片返回空响应或不可恢复错误，"
                    "已停止继续原样重试；保留已完成分片供预览。"
                )

        def call_chunk_model(
            chunk: list[dict[str, object]],
            chunk_index: object,
            *,
            retries: int | None = None,
        ) -> dict[str, object]:
            prompt = {
                "task": "测试用例质量评估分片对比，只输出 JSON",
                "chunk_index": chunk_index,
                "chunk_count": len(chunks),
                "requirement_text": requirement_text[:1200],
                "comparison_groups": chunk,
                "output_rules": "metrics 四项范围 0-1；defect_analysis 三个列表最多各 5 条；case_judgements 每个 group 最多 1 条；只输出 JSON。",
                "json_schema": {
                    "chunk_index": chunk_index,
                    "metrics": {
                        "precision": 0.0,
                        "recall": 0.0,
                        "f1_score": 0.0,
                        "semantic_similarity": 0.0,
                    },
                    "defect_analysis": {
                        "missing_points": [],
                        "hallucinations": [],
                        "modifications": [],
                    },
                    "case_judgements": [
                        {
                            "unit_id": "string",
                            "verdict": "matched|missing_in_generated|hallucination|modified|uncertain",
                            "reason": "中文短句",
                        }
                    ],
                    "summary": "中文短句",
                },
            }
            prompt["comparison_groups"] = [
                _compact_compare_unit_for_single_pass(unit, single_pass_brief_chars)
                for unit in chunk
            ]
            prompt["output_rules"] = (
                "metrics 四项范围 0-1；defect_analysis 三个列表最多各 5 条且每条少于 80 字；"
                "缺陷项不能只写 TC/CASE 编号，必须写清楚业务点和生成版/人工版差异；"
                "summary 少于 100 字；只输出 JSON；不要 case_judgements。"
            )
            if isinstance(prompt.get("json_schema"), dict):
                prompt["json_schema"].pop("case_judgements", None)  # type: ignore[index]
            prompt_text = json.dumps(prompt, ensure_ascii=False, separators=(",", ":"))
            last_error: Exception | None = None
            max_attempts = max(1, (chunk_retries if retries is None else retries) + 1)
            chunk_payload: dict[str, object] | None = None
            for attempt in range(1, max_attempts + 1):
                if attempt > 1:
                    emit_progress(
                        "retrying",
                        current_chunk=chunk_index,
                        retrying_chunks=[
                            {
                                "chunk_index": chunk_index,
                                "attempt": attempt,
                                "max_attempts": max_attempts,
                                "last_error": str(last_error or "")[:300],
                            }
                        ],
                        last_error=last_error,
                    )
                retry_suffix = "" if attempt == 1 else "\n上一次返回失败或无法解析。请重新输出，且只输出一个 JSON 对象，不要解释、不要 Markdown、不要代码块。"
                try:
                    raw_chunk_result = client.generate_response(
                        prompt_text,
                        chunk_system_prompt
                        + "\n只输出 metrics、defect_analysis、summary，不要输出 case_judgements。"
                        + retry_suffix,
                        max_tokens=chunk_max_output_tokens,
                        task_type="review",
                        model=compare_model_override,
                    )
                    chunk_payload = _parse_model_json_payload(
                        raw_chunk_result,
                        f"分片 {chunk_index}/{len(chunks)}",
                    )
                    break
                except Exception as e:
                    last_error = e
                    if _is_fast_fail_compare_error(e):
                        break
            if chunk_payload is None:
                raise last_error or ValueError(f"分片 {chunk_index}/{len(chunks)} 模型未返回可解析 JSON")
            chunk_payload["chunk_index"] = chunk_index
            chunk_payload["chunk_unit_count"] = len(chunk)
            chunk_payload["retry_attempts"] = max(0, attempt - 1)
            return chunk_payload

        for idx, chunk in enumerate(chunks, start=1):
            try:
                chunk_payload = call_chunk_model(chunk, idx)
            except Exception as chunk_error:
                if len(chunk) <= 1:
                    record_failed_chunk(
                        chunk_index=idx,
                        chunk_unit_count=len(chunk),
                        error=chunk_error,
                    )
                    phase = "stopped_after_repeated_model_failures" if stop_after_repeated_failures else "chunk_failed_continuing"
                    emit_progress(phase, current_chunk=idx, last_error=stop_reason or chunk_error)
                    if stop_after_repeated_failures:
                        break
                    continue
                emit_progress("splitting", current_chunk=idx, last_error=chunk_error)
                for sub_idx, unit in enumerate(chunk, start=1):
                    sub_chunk_index = f"{idx}.{sub_idx}"
                    try:
                        chunk_results.append(call_chunk_model([unit], sub_chunk_index, retries=sub_chunk_retries))
                        consecutive_fast_failures = 0
                    except Exception as sub_error:
                        record_failed_chunk(
                            chunk_index=sub_chunk_index,
                            chunk_unit_count=1,
                            error=sub_error,
                        )
                        phase = "stopped_after_repeated_model_failures" if stop_after_repeated_failures else "chunk_failed_continuing"
                        emit_progress(phase, current_chunk=sub_chunk_index, last_error=stop_reason or sub_error)
                        if stop_after_repeated_failures:
                            break
                        continue
                    emit_progress("chunking", current_chunk=sub_chunk_index)
                if stop_after_repeated_failures:
                    break
                continue
            chunk_results.append(chunk_payload)
            consecutive_fast_failures = 0
            emit_progress("chunking", current_chunk=idx)

        if not chunk_results:
            progress = build_progress("failed", last_error=failed_chunks[-1]["error"] if failed_chunks else None)
            return self._build_model_failed_compare_result(
                generated_test_case=generated_test_case,
                modified_test_case=modified_test_case,
                requirement_text=requirement_text,
                baseline=baseline,
                fallback_reason=(
                    "所有分片模型评测均失败："
                    + "; ".join(str(item.get("error") or "")[:180] for item in failed_chunks[:3])
                ),
                partial_chunk_results=[],
                progress=progress,
                input_stats=input_stats,
                comparison_id=comparison_id,
            )

        if stop_after_repeated_failures:
            progress = build_progress("partial_completed", last_error=stop_reason)
            return self._build_partial_completed_compare_result(
                generated_test_case=generated_test_case,
                modified_test_case=modified_test_case,
                requirement_text=requirement_text,
                baseline=baseline,
                fallback_reason=stop_reason,
                partial_chunk_results=chunk_results,
                progress=progress,
                input_stats=input_stats,
                comparison_id=comparison_id,
            )

        if len(chunk_results) == 1:
            has_failed_chunks = bool(failed_chunks)
            final_progress = build_progress("partial_completed" if has_failed_chunks else "completed")
            return self._finalize_llm_compare_result(
                raw_result=json.dumps(chunk_results[0], ensure_ascii=False),
                baseline=baseline,
                requirement_text=requirement_text,
                analysis_mode="llm_chunked",
                input_stats=input_stats,
                chunk_summary={
                    "chunk_count": len(chunks),
                    "successful_chunk_result_count": len(chunk_results),
                    "failed_chunk_count": len(failed_chunks),
                    "failed_chunk_details": failed_chunks[-10:],
                    "aggregation": "single_chunk",
                },
                comparison_id=comparison_id,
                analysis_status="partial_completed" if has_failed_chunks else "completed",
                is_final_evaluation=not has_failed_chunks,
                partial_chunk_results=chunk_results if has_failed_chunks else None,
                progress=final_progress if has_failed_chunks else None,
            )

        aggregate_system_prompt = """
        你是测试用例质量评估汇总员。你会收到已完成分片的模型评测结果，以及可能存在的失败分片记录。
        请合并重复缺陷，基于已完成分片输出评测 JSON。
        如果存在 failed_chunks，只能对已完成分片下结论，并在 summary 中说明这是部分评估。
        不能把本地预匹配提示当最终结论；最终结论必须来自分片模型判断。
        请只输出最终 JSON，不输出 Markdown，不展开推理过程。
        defect_analysis 每个列表最多返回 20 项，每条不超过 100 个中文字符。
        必须返回严格 JSON，中文输出。
        JSON 格式：
        {
          "metrics": {"precision": 0.0, "recall": 0.0, "f1_score": 0.0, "semantic_similarity": 0.0},
          "defect_analysis": {
            "missing_points": [],
            "hallucinations": [],
            "modifications": []
          },
          "summary": "中文总体结论",
          "requirement_baseline": {
            "requirement_points": [],
            "ai_requirement_gaps": [],
            "human_requirement_gaps": [],
            "ai_unanchored_points": [],
            "human_added_value": [],
            "both_missing_points": [],
            "summary": "中文需求锚定结论"
          }
        }
        """
        aggregate_system_prompt = """
        你是测试用例质量评估汇总器。基于 chunk_results 合并为一个最终 JSON。
        只输出严格 JSON 对象，不要 Markdown，不要解释，不要输出推理过程。
        去重合并缺陷；defect_analysis 每个列表最多 20 条，每条不超过 100 个中文字符。
        如果 failed_chunks 非空，summary 必须说明这是部分评估。
        JSON schema:
        {
          "metrics": {"precision": 0.0, "recall": 0.0, "f1_score": 0.0, "semantic_similarity": 0.0},
          "defect_analysis": {"missing_points": [], "hallucinations": [], "modifications": []},
          "summary": "中文短句",
          "requirement_baseline": {
            "requirement_points": [],
            "ai_requirement_gaps": [],
            "human_requirement_gaps": [],
            "ai_unanchored_points": [],
            "human_added_value": [],
            "both_missing_points": [],
            "summary": "中文短句"
          }
        }
        """
        aggregate_prompt = {
            "task": "汇总所有分片模型评测，输出最终测试用例质量评估 JSON",
            "input_stats": input_stats,
            "requirement_text": requirement_text[:1200],
            "requirement_heuristic_baseline": baseline,
            "chunk_results": chunk_results,
            "failed_chunks": failed_chunks,
            "partial_evaluation": bool(failed_chunks),
            "output_rules": "只输出 JSON。metrics 四项范围 0-1；defect_analysis 去重后每类最多 20 条。",
        }
        aggregate_prompt_text = json.dumps(aggregate_prompt, ensure_ascii=False, separators=(",", ":"))
        emit_progress("aggregating")

        def call_aggregate_model() -> str:
            last_error: Exception | None = None
            max_attempts = aggregate_retries + 1
            for attempt in range(1, max_attempts + 1):
                if attempt > 1:
                    emit_progress(
                        "aggregate_retrying",
                        retrying_chunks=[
                            {
                                "chunk_index": "aggregate",
                                "attempt": attempt,
                                "max_attempts": max_attempts,
                                "last_error": str(last_error or "")[:300],
                            }
                        ],
                        last_error=last_error,
                    )
                retry_suffix = "" if attempt == 1 else "\n上一次返回失败或无法解析。请重新输出，且只输出一个 JSON 对象，不要解释、不要 Markdown、不要代码块。"
                try:
                    raw_result = client.generate_response(
                        aggregate_prompt_text,
                        aggregate_system_prompt + retry_suffix,
                        max_tokens=aggregate_max_output_tokens,
                        task_type="review",
                        model=compare_model_override,
                    )
                    _parse_model_json_payload(raw_result, "分片汇总")
                    return raw_result
                except Exception as e:
                    last_error = e
            raise last_error or ValueError("分片汇总模型未返回可解析 JSON")

        try:
            raw_final = call_aggregate_model()
        except Exception as aggregate_error:
            progress = emit_progress("aggregate_failed", last_error=aggregate_error)
            return self._build_partial_completed_compare_result(
                generated_test_case=generated_test_case,
                modified_test_case=modified_test_case,
                requirement_text=requirement_text,
                baseline=baseline,
                fallback_reason=f"分片汇总模型评测失败：{aggregate_error}",
                partial_chunk_results=chunk_results,
                progress=progress,
                input_stats=input_stats,
                comparison_id=comparison_id,
            )

        has_failed_chunks = bool(failed_chunks)
        final_progress = build_progress("partial_completed" if has_failed_chunks else "completed")
        return self._finalize_llm_compare_result(
            raw_result=raw_final,
            baseline=baseline,
            requirement_text=requirement_text,
            analysis_mode="llm_chunked",
            input_stats=input_stats,
            chunk_summary={
                "chunk_count": len(chunks),
                "comparison_unit_count": len(units),
                "comparison_group_count": len(units),
                "successful_chunk_result_count": len(chunk_results),
                "failed_chunk_count": len(failed_chunks),
                "failed_chunk_details": failed_chunks[-10:],
                "chunk_alignment_strategy": "modified_anchor_case_groups",
                "chunk_case_limit": _safe_compare_int_env(
                    "EVAL_LLM_COMPARE_CHUNK_GROUPS",
                    _safe_compare_int_env("EVAL_LLM_COMPARE_CHUNK_CASES", _DEFAULT_LLM_COMPARE_CHUNK_CASES),
                ),
                "chunk_max_chars": _safe_compare_int_env(
                    "EVAL_LLM_COMPARE_CHUNK_MAX_CHARS",
                    _DEFAULT_LLM_COMPARE_CHUNK_MAX_CHARS,
                ),
                "chunk_max_output_tokens": chunk_max_output_tokens,
                "aggregate_max_output_tokens": aggregate_max_output_tokens,
                "chunk_retries": chunk_retries,
                "sub_chunk_retries": sub_chunk_retries,
                "empty_failure_limit": empty_failure_limit,
                "aggregate_retries": aggregate_retries,
                "nearest_candidates": _safe_compare_int_env(
                    "EVAL_LLM_COMPARE_NEAREST_CANDIDATES",
                    _DEFAULT_LLM_COMPARE_NEAREST_CANDIDATES,
                ),
                "aggregation": "model",
            },
            comparison_id=comparison_id,
            analysis_status="partial_completed" if has_failed_chunks else "completed",
            is_final_evaluation=not has_failed_chunks,
            partial_chunk_results=chunk_results if has_failed_chunks else None,
            progress=final_progress if has_failed_chunks else None,
        )

    def _save_compare_result(
        self,
        *,
        generated_test_case: str,
        modified_test_case: str,
        result: str,
        db: Session = None,
        project_id: int = None,
        user_id: int = None,
    ) -> None:
        if not db:
            return
        try:
            db_entry = TestGenerationComparison(
                project_id=project_id,
                generated_test_case=generated_test_case,
                modified_test_case=modified_test_case,
                comparison_result=result,
                user_id=user_id,
            )
            db.add(db_entry)
            db.commit()
        except Exception as e:
            db.rollback()
            err = str(e)
            if "Data too long" in err:
                try:
                    db_entry = TestGenerationComparison(
                        project_id=project_id,
                        generated_test_case=_truncate_utf8_for_mysql_text(generated_test_case),
                        modified_test_case=_truncate_utf8_for_mysql_text(modified_test_case),
                        comparison_result=_truncate_utf8_for_mysql_text(result),
                        user_id=user_id,
                    )
                    db.add(db_entry)
                    db.commit()
                except Exception as e2:
                    db.rollback()
                    print(f"Failed to save comparison to DB after truncate: {e2}")
            else:
                print(f"Failed to save comparison to DB: {e}")

    def calculate_recall(self, generated_test: str, requirements: str, db: Session = None, user_id: int = None) -> float:
        """
        计算需求覆盖率 (Calculate Recall)

        使用 LLM 分析生成的测试用例是否覆盖了用户提供的所有需求点。

        Args:
            generated_test: 生成的测试用例内容。
            requirements: 用户原始需求描述。
            db: 数据库会话。
            user_id: 当前用户 ID。

        Returns:
            float: 覆盖率分数 (0.0 - 1.0)。
        """
        def to_points(value) -> set[str]:
            if value is None:
                return set()
            if isinstance(value, list):
                return {str(x).strip().lower() for x in value if str(x).strip()}
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    return set()
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        return {str(x).strip().lower() for x in parsed if str(x).strip()}
                except Exception:
                    pass
                parts = re.split(r"[\n,，;；]+", text)
                return {p.strip().lower() for p in parts if p.strip()}
            return {str(value).strip().lower()} if str(value).strip() else set()

        retrieved_points = to_points(generated_test)
        relevant_points = to_points(requirements)

        if not relevant_points:
            return 0.0

        hits = len(retrieved_points & relevant_points)
        recall = hits / len(relevant_points)
        return round(float(recall), 4)

    def evaluate_test_quality(self, test_case: str, db: Session = None, project_id: int = None, user_id: int = None) -> str:
        """
        评估测试用例质量 (Evaluate Test Quality)

        使用 LLM 作为审计员，对测试用例的清晰度、完整性和正确性进行打分。

        Args:
            test_case: 测试用例内容。
            db: 数据库会话。
            project_id: 项目 ID。
            user_id: 用户 ID。

        Returns:
            str: 评估结果文本。
        """
        client = get_client_for_user(user_id, db)
        system_prompt = """
        You are a Test Quality Auditor.
        Evaluate the quality of the following test case.
        Check for: Clarity, Completeness, correctness of steps vs expected result.
        Give a score out of 10 and a brief explanation.
        """
        result = client.generate_response(test_case, system_prompt)

        if db:
            try:
                db_entry = Evaluation(
                    project_id=project_id,
                    test_case_content=test_case,
                    evaluation_result=result,
                    user_id=user_id
                )
                db.add(db_entry)
                db.commit()
            except Exception as e:
                print(f"Failed to save to DB: {e}")

        return result

    def _build_requirement_baseline(self, *, requirement_text: str, generated_test_case: str, modified_test_case: str) -> dict[str, object]:
        generated_cases = self._parse_test_cases_for_requirement_baseline(generated_test_case)
        modified_cases = self._parse_test_cases_for_requirement_baseline(modified_test_case)
        generated_coverage = analyze_coverage(requirement_text, generated_cases)
        modified_coverage = analyze_coverage(requirement_text, modified_cases)
        generated_diag = {
            str(item.get("rule_id") or ""): item
            for item in generated_coverage.get("rule_diagnostics") or []
            if isinstance(item, dict)
        }
        modified_diag = {
            str(item.get("rule_id") or ""): item
            for item in modified_coverage.get("rule_diagnostics") or []
            if isinstance(item, dict)
        }
        rule_ids = sorted(set(generated_diag) | set(modified_diag))
        rule_rows: list[dict[str, object]] = []
        for rule_id in rule_ids:
            left = generated_diag.get(rule_id) or {}
            right = modified_diag.get(rule_id) or {}
            source = left or right
            if not bool(source.get("blocking", True)):
                continue
            rule_rows.append(
                {
                    "rule_id": rule_id,
                    "rule_text": str(source.get("rule_text") or "")[:180],
                    "biz_key": source.get("biz_key") or "unknown",
                    "ai_covered": bool(left.get("covered")),
                    "human_covered": bool(right.get("covered")),
                    "ai_missing_types": left.get("missing_types") or [],
                    "human_missing_types": right.get("missing_types") or [],
                }
            )

        requirement_points = [str(row.get("rule_text") or "") for row in rule_rows if row.get("rule_text")]
        missing_in_generated = [str(row.get("rule_text") or "") for row in rule_rows if not row.get("ai_covered")]
        missing_in_modified = [str(row.get("rule_text") or "") for row in rule_rows if not row.get("human_covered")]
        covered_by_both = [str(row.get("rule_text") or "") for row in rule_rows if row.get("ai_covered") and row.get("human_covered")]

        return {
            "requirement_points": requirement_points,
            "generated_coverage_count": len(generated_coverage.get("covered_rules") or []),
            "modified_coverage_count": len(modified_coverage.get("covered_rules") or []),
            "generated_coverage_rate": float(generated_coverage.get("coverage_rate") or 0.0),
            "modified_coverage_rate": float(modified_coverage.get("coverage_rate") or 0.0),
            "missing_in_generated": missing_in_generated[:8],
            "missing_in_modified": missing_in_modified[:8],
            "covered_by_both": covered_by_both[:8],
            "both_missing_points": [str(row.get("rule_text") or "") for row in rule_rows if not row.get("ai_covered") and not row.get("human_covered")][:8],
            "rule_rows": rule_rows[:40],
            "generated_case_count": len(generated_cases),
            "modified_case_count": len(modified_cases),
            "total_rules": int(generated_coverage.get("total_rules") or modified_coverage.get("total_rules") or len(rule_rows)),
        }

    def compare_test_cases(
        self,
        generated_test_case: str,
        modified_test_case: str,
        db: Session = None,
        project_id: int = None,
        user_id: int = None,
        requirement_text: str = "",
        persist_result: bool = True,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
        comparison_id: int | None = None,
    ) -> str:
        """
        对比测试用例 (Compare Test Cases)

        对比 AI 生成的用例与用户修改后的用例，计算 Precision, Recall, F1 Score 等指标。
        用于分析 AI 的生成质量以及用户的修改意图（缺陷归因分析）。

        Args:
            generated_test_case: AI 生成的原始用例。
            modified_test_case: 用户修改后的最终用例 (Ground Truth)。
            db: 数据库会话。
            project_id: 项目 ID。
            user_id: 用户 ID。

        Returns:
            str: JSON 格式的对比分析结果。
        """
        generated_cases = _parse_test_cases_payload(generated_test_case)
        modified_cases = _parse_test_cases_payload(modified_test_case)
        baseline = self._build_requirement_baseline(
            requirement_text=requirement_text,
            generated_test_case=generated_test_case,
            modified_test_case=modified_test_case,
        )
        try:
            client = get_client_for_user(user_id, db)
        except Exception as e:
            result = self._build_model_failed_compare_result(
                generated_test_case=generated_test_case,
                modified_test_case=modified_test_case,
                requirement_text=requirement_text,
                baseline=baseline,
                fallback_reason=f"模型客户端初始化异常：{e}",
                comparison_id=comparison_id,
            )
            if persist_result:
                self._save_compare_result(
                    generated_test_case=generated_test_case,
                    modified_test_case=modified_test_case,
                    result=result,
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                )
            return result

        if self._should_use_chunked_llm_compare(
            generated_test_case,
            modified_test_case,
            generated_cases,
            modified_cases,
        ):
            try:
                result = self._compare_test_cases_with_llm_chunks(
                    client=client,
                    generated_test_case=generated_test_case,
                    modified_test_case=modified_test_case,
                    generated_cases=generated_cases,
                    modified_cases=modified_cases,
                    baseline=baseline,
                    requirement_text=requirement_text,
                    progress_callback=progress_callback,
                    comparison_id=comparison_id,
                )
            except Exception as e:
                result = self._build_model_failed_compare_result(
                    generated_test_case=generated_test_case,
                    modified_test_case=modified_test_case,
                    requirement_text=requirement_text,
                    baseline=baseline,
                    fallback_reason=f"分片模型评测失败：{e}",
                    comparison_id=comparison_id,
                )
            if persist_result:
                self._save_compare_result(
                    generated_test_case=generated_test_case,
                    modified_test_case=modified_test_case,
                    result=result,
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                )
            return result

        system_prompt = """
        You are a Test Case Quality Auditor.
        Compare the "Generated Test Case" (AI Output) with the "Modified Test Case" (User's Final Version/Ground Truth).

        Calculate the following metrics based on the content matching:
        1. Precision: Proportion of generated test logic that was kept/used in the modified version.
        2. Recall: Proportion of necessary test logic in the modified version that was originally present in the generated version.
        3. F1 Score: Harmonic mean of Precision and Recall.
        4. Semantic Similarity: Overall semantic similarity score (0.0 to 1.0).

        Perform Defect Attribution Analysis for discrepancies:
        - Identify missing cases/steps (Recall loss): items that EXIST in Modified Test Case but are ABSENT in Generated Test Case.
        - Identify hallucinated/unnecessary cases/steps (Precision loss): items that EXIST in Generated Test Case but are ABSENT in Modified Test Case.
        - Identify modified logic (Correction).

        Field mapping rules (must follow strictly):
        - defect_analysis.missing_points: only list "modified has, generated lacks" items.
        - defect_analysis.hallucinations: only list "generated has, modified lacks" items.
        - If a line implies "新增/补充/原生成未覆盖", it belongs to missing_points, NOT hallucinations.

        Return the result strictly in the following JSON format:
        {
            "metrics": {
                "precision": 0.0,
                "recall": 0.0,
                "f1_score": 0.0,
                "semantic_similarity": 0.0
            },
            "defect_analysis": {
                "missing_points": ["point 1", "point 2"],
                "hallucinations": ["point 1", "point 2"],
                "modifications": ["point 1", "point 2"]
            },
            "summary": "Brief text summary of the comparison."
        }

        If "Requirement Baseline" is provided in the user prompt, also include:
        {
            "requirement_baseline": {
                "requirement_points": ["requirement point 1"],
                "ai_requirement_gaps": ["Requirement-backed point missing from Generated Test Case"],
                "human_requirement_gaps": ["Requirement-backed point missing from Modified Test Case"],
                "ai_unanchored_points": ["Generated point that lacks requirement evidence"],
                "human_added_value": ["Modified point that improves requirement coverage or executability"],
                "both_missing_points": ["Requirement-backed point missing from both versions"],
                "summary": "Requirement-anchored conclusion"
            }
        }

        Requirement anchoring rules:
        - Requirement Baseline is the anchor, not the Modified Test Case alone.
        - Do not assume every human modification is correct; if the modified version removes a requirement-backed point, list it in human_requirement_gaps.
        - Do not call a generated point hallucination if it is supported by the requirement, even when absent from the modified version.
        - Prefer semantic matching over literal wording.

        LANGUAGE CONSTRAINT:
        All natural language content in the output (including "summary" and lists in "defect_analysis") MUST be in Chinese (Simplified).
        """
        prompt = f"Generated Test Case:\n{generated_test_case}\n\nModified Test Case:\n{modified_test_case}"
        if requirement_text.strip():
            prompt += (
                f"\n\nRequirement Baseline:\n{requirement_text}\n"
                f"\nRequirement Coverage Rows:\n{json.dumps(baseline.get('rule_rows', []), ensure_ascii=False)}"
            )
        try:
            result = client.generate_response(
                prompt,
                system_prompt,
                max_tokens=_safe_compare_int_env(
                    "EVAL_LLM_COMPARE_SINGLE_MAX_OUTPUT_TOKENS",
                    2400,
                ),
                task_type="review",
            )
        except Exception as e:
            result = self._build_model_failed_compare_result(
                generated_test_case=generated_test_case,
                modified_test_case=modified_test_case,
                requirement_text=requirement_text,
                baseline=baseline,
                fallback_reason=f"模型比较调用异常：{e}",
            )
            if persist_result:
                self._save_compare_result(
                    generated_test_case=generated_test_case,
                    modified_test_case=modified_test_case,
                    result=result,
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                )
            return result

        if _is_model_error_result(result):
            result = self._build_model_failed_compare_result(
                generated_test_case=generated_test_case,
                modified_test_case=modified_test_case,
                requirement_text=requirement_text,
                baseline=baseline,
                fallback_reason=f"模型返回错误，未生成正式评测结果：{result}",
            )
            if persist_result:
                self._save_compare_result(
                    generated_test_case=generated_test_case,
                    modified_test_case=modified_test_case,
                    result=result,
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                )
            return result

        try:
            result = self._finalize_llm_compare_result(
                raw_result=result,
                baseline=baseline,
                requirement_text=requirement_text,
                analysis_mode="llm_single",
                input_stats=_compare_input_stats(
                    generated_test_case,
                    modified_test_case,
                    generated_cases,
                    modified_cases,
                ),
            )
        except Exception as e:
            result = self._build_model_failed_compare_result(
                generated_test_case=generated_test_case,
                modified_test_case=modified_test_case,
                requirement_text=requirement_text,
                baseline=baseline,
                fallback_reason=f"模型结果结构化解析失败：{e}",
            )

        if persist_result:
            self._save_compare_result(
                generated_test_case=generated_test_case,
                modified_test_case=modified_test_case,
                result=result,
                db=db,
                project_id=project_id,
                user_id=user_id,
            )

        return result

    def evaluate_ui_automation(self, ui_script: str, execution_result: str, db: Session = None, project_id: int = None, user_id: int = None, journey_json: dict = None) -> str:
        client = get_client_for_user(user_id, db)

        journey_recall_report = ""
        if journey_json:
            try:
                import ast

                operations = []
                try:
                    tree = ast.parse(ui_script)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                            func = node.value.func
                            if isinstance(func, ast.Attribute):
                                action_name = func.attr
                                args_str = ""
                                if node.value.args:
                                    args_str = ", ".join([ast.unparse(arg) for arg in node.value.args])
                                operations.append(f"{action_name}({args_str})")
                except Exception as e:
                    print(f"Failed to parse script with AST: {e}")
                    for line in ui_script.split('\n'):
                        if 'page.' in line:
                            operations.append(line.strip())

                journey_steps = []
                if "user_journey" in journey_json:
                    journey_steps = [step.get("action", "") for step in journey_json["user_journey"]]

                recall_prompt = f"""
                You are a UI Automation Coverage Analyst.

                Task: Calculate the coverage of the User Journey by the provided Automation Script Operations.

                User Journey Steps:
                {json.dumps(journey_steps, indent=2, ensure_ascii=False)}

                Extracted Script Operations:
                {json.dumps(operations, indent=2, ensure_ascii=False)}

                Please determine which User Journey Steps are covered by the Script Operations.
                A step is covered if there is a corresponding operation sequence in the script.

                Return a JSON object with:
                - covered_steps: list of covered step descriptions
                - missing_steps: list of missing step descriptions
                - coverage_rate: float (0.0 to 1.0)
                - explanation: brief explanation
                """

                coverage_analysis = client.generate_response(recall_prompt, "You are a JSON generator. Output only valid JSON.", db=db)
                journey_recall_report = f"\n\nJourney Coverage Analysis:\n{coverage_analysis}"

            except Exception as e:
                journey_recall_report = f"\n\nJourney Coverage Analysis Failed: {str(e)}"

        system_prompt = """
        You are a UI Automation Test Evaluator.
        Evaluate the quality and effectiveness of the following UI automation script and its execution result.

        Evaluation criteria:
        1. Script Structure: Is the script well-structured with proper setup and teardown?
        2. Error Handling: Does the script handle potential errors gracefully?
        3. Test Coverage: Does the script effectively cover the intended UI functionality?
        4. Execution Success: Did the script execute successfully?
        5. Result Reporting: Does the script provide clear test results?

        Give a comprehensive evaluation with scores out of 10 for each criterion and an overall score.
        """
        prompt = f"UI Automation Script:\n{ui_script}\n\nExecution Result:\n{execution_result}{journey_recall_report}"
        result = client.generate_response(prompt, system_prompt, db=db)

        if journey_recall_report:
             result += f"\n\n--- Detailed Coverage Report ---\n{journey_recall_report}"

        if db:
            try:
                db_entry = Evaluation(
                    project_id=project_id,
                    test_case_content=f"UI Automation: {ui_script[:100]}...",
                    evaluation_result=result,
                    user_id=user_id
                )
                db.add(db_entry)
                db.commit()
            except Exception as e:
                print(f"Failed to save to DB: {e}")

        return result

    def judge_test_result(self, input_data: dict, actual_output: dict, expected_behavior: str, db: Session = None, user_id: int = None) -> dict:
        client = get_client_for_user(user_id, db)

        system_prompt = """
        You are an AI Test Result Judge.
        Analyze the Input, Actual Output, and Expected Behavior.
        Classify the result into ONE of these categories:
        - Normal: Result matches expectation (Pass).
        - Abnormal: System error, 500, or crash (Fail).
        - False Positive: Test failed (e.g. 400 Bad Request) but it was EXPECTED due to invalid input (Business Pass).
        - False Negative: Test passed (200 OK) but data is wrong (Business Fail).

        Return JSON:
        {
            "category": "Normal" | "Abnormal" | "False Positive" | "False Negative",
            "reason": "explanation"
        }
        """

        prompt = f"""
        Input: {json.dumps(input_data)}
        Actual Output: {json.dumps(actual_output)}
        Expected Behavior: {expected_behavior}
        """

        response = client.generate_response(prompt, system_prompt, db=db)
        try:
            from core.processing.utils import extract_code_block
            return json.loads(extract_code_block(response, "json"))
        except Exception:
            return {"category": "Unknown", "reason": "Failed to parse AI response"}

    def evaluate_api_test(self, api_script: str, execution_result: str, db: Session = None, project_id: int = None, user_id: int = None, openapi_spec: str = None) -> str:
        """
        评估 API 测试脚本 (Evaluate API Test)

        评估 API 测试脚本的代码质量、断言完整性以及执行结果分析。

        Args:
            api_script: 生成的 API 测试脚本。
            execution_result: 执行结果。
            db: 数据库会话。
            project_id: 项目 ID。
            user_id: 用户 ID。

        Returns:
            str: 评估报告。
        """
        client = get_client_for_user(user_id, db)

        api_coverage_report = ""
        if openapi_spec:
            coverage_prompt = f"""
            You are an API Test Coverage Analyst.

            Task: Compare the API Test Script against the OpenAPI Specification to determine endpoint coverage.

            OpenAPI Spec (Snippet/Summary):
            {openapi_spec[:2000]}... (truncated if too long)

            API Test Script:
            {api_script}

            Return a JSON object with:
            - covered_endpoints: list of endpoints (method + path) called in the script
            - missing_endpoints: list of key endpoints from spec not covered
            - coverage_rate: float (0.0 to 1.0) estimation
            """

            coverage_analysis = client.generate_response(coverage_prompt, "You are a JSON generator. Output only valid JSON.", db=db)
            api_coverage_report = f"\n\nAPI Coverage Analysis:\n{coverage_analysis}"

        system_prompt = """
        You are an API Test Evaluator.
        Evaluate the quality and effectiveness of the following API test script and its execution result.

        Evaluation criteria:
        1. Script Structure: Is the script well-structured with proper organization?
        2. Assertions: Does the script include appropriate assertions to verify API responses?
        3. Error Handling: Does the script handle potential API errors gracefully?
        4. Test Coverage: Does the script effectively test the intended API functionality?
        5. Execution Success: Did the script execute successfully?

        Give a comprehensive evaluation with scores out of 10 for each criterion and an overall score.
        """
        prompt = f"API Test Script:\n{api_script}\n\nExecution Result:\n{execution_result}{api_coverage_report}"
        result = client.generate_response(prompt, system_prompt, db=db)

        if api_coverage_report:
            result += f"\n\n--- Detailed API Coverage Report ---\n{api_coverage_report}"

        if db:
            try:
                db_entry = Evaluation(
                    project_id=project_id,
                    test_case_content=f"API Test: {api_script[:100]}...",
                    evaluation_result=result,
                    user_id=user_id
                )
                db.add(db_entry)
                db.commit()
            except Exception as e:
                print(f"Failed to save to DB: {e}")

        return result

evaluator = EvaluationModule()
