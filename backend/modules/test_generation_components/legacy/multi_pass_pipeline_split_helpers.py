from __future__ import annotations

import json
from typing import Any, Callable

from modules.testing.test_generation_components.coverage.coverage_analyzer import analyze_coverage
from modules.testing.test_generation_components.postprocess.result_postprocess import (
    apply_priority_semantics_to_case,
)


_VALID_GENERATION_MODES = {"single_pass", "multi_pass", "biz_key_multi_pass"}
_MAX_ROUNDS = 6
_MAX_EXISTING_CASES_IN_PROMPT = 80

def _resolve_generation_mode(*, multi_pass: bool, generation_mode: str) -> str:
    normalized = str(generation_mode or "").strip().lower()
    if normalized in _VALID_GENERATION_MODES:
        return normalized
    return "multi_pass" if bool(multi_pass) else "single_pass"


def _to_case_list(
    payload: Any,
    *,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    data: Any = payload
    if isinstance(payload, str):
        data = clean_and_parse_json_fn(payload)
    data = normalize_json_structure_fn(data)
    if not isinstance(data, list):
        return []
    normalized = [item for item in deduplicate_test_cases_fn(data) if isinstance(item, dict)]
    output: list[dict[str, Any]] = []
    for item in normalized:
        case = dict(item)
        output.append(apply_priority_semantics_to_case(case, attach_debug=False))
    return output


def _case_signature(case: dict[str, Any]) -> str:
    module = str(case.get("test_module") or "").strip().lower()
    desc = str(case.get("description") or "").strip().lower()
    expected = str(case.get("expected_result") or "").strip().lower()
    test_input = str(case.get("test_input") or "").strip().lower()
    return f"{module}|{desc}|{expected}|{test_input}"


def _priority_weight(priority: str) -> int:
    value = str(priority or "").strip().upper()
    if value == "P0":
        return 3
    if value == "P1":
        return 2
    if value == "P2":
        return 1
    return 0


def _focus_weight(case: dict[str, Any]) -> int:
    text = " ".join(
        [
            str(case.get("description") or ""),
            str(case.get("expected_result") or ""),
            str(case.get("test_input") or ""),
            " ".join([str(x) for x in case.get("steps", [])]) if isinstance(case.get("steps"), list) else "",
        ]
    ).lower()
    score = 0
    if any(keyword in text for keyword in ("boundary", "max", "min", "edge", "limit", "边界", "最大", "最小", "临界")):
        score += 2
    if any(keyword in text for keyword in ("exception", "error", "invalid", "fail", "异常", "错误", "失败", "拒绝")):
        score += 2
    if any(keyword in text for keyword in ("state", "transition", "状态", "流转")):
        score += 1
    return score


def _coverage_bucket(case: dict[str, Any]) -> str:
    module = str(case.get("test_module") or "").strip().lower() or "general"
    text = " ".join(
        [
            str(case.get("description") or ""),
            str(case.get("expected_result") or ""),
            str(case.get("test_input") or ""),
            " ".join([str(x) for x in case.get("steps", [])]) if isinstance(case.get("steps"), list) else "",
        ]
    ).lower()
    if any(keyword in text for keyword in ("exception", "error", "invalid", "fail", "异常", "错误", "失败", "拒绝")):
        kind = "exception"
    elif any(keyword in text for keyword in ("boundary", "max", "min", "edge", "limit", "边界", "最大", "最小", "临界")):
        kind = "boundary"
    elif any(keyword in text for keyword in ("state", "transition", "状态", "流转")):
        kind = "state"
    elif any(keyword in text for keyword in ("permission", "security", "auth", "performance", "权限", "安全", "鉴权", "性能")):
        kind = "risk"
    else:
        kind = "happy"
    return f"{module}|{kind}"


def _is_high_signal(case: dict[str, Any]) -> bool:
    return _priority_weight(case.get("priority") or "") >= 2 or _focus_weight(case) >= 2


def _attach_priority_debug(cases: list[dict[str, Any]], coverage_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in cases:
        if not isinstance(item, dict):
            continue
        case = dict(item)
        case = apply_priority_semantics_to_case(
            case,
            attach_debug=False,
            coverage_context=coverage_context,
            rule_diagnostics={"rule_diagnostics": (coverage_context or {}).get("rule_diagnostics") or []},
        )
        case.pop("meta", None)
        case.pop("displayPriority", None)
        case.pop("rawPriority", None)
        case.pop("finalPriority", None)
        output.append(case)
    return output


def _filter_new_cases(base_cases: list[dict[str, Any]], candidate_cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = {_case_signature(case) for case in base_cases if isinstance(case, dict)}
    output: list[dict[str, Any]] = []
    for case in candidate_cases:
        if not isinstance(case, dict):
            continue
        signature = _case_signature(case)
        if signature in seen:
            continue
        seen.add(signature)
        output.append(case)
    return output


def _missing_types_count(coverage: dict[str, Any]) -> int:
    diagnostics = [item for item in (coverage.get("rule_diagnostics") or []) if isinstance(item, dict)]
    return sum(len([x for x in (item.get("missing_types") or []) if str(x).strip()]) for item in diagnostics)


def _coverage_satisfied(coverage: dict[str, Any]) -> bool:
    missing_rules = list(coverage.get("missing_rules") or [])
    if missing_rules:
        return False
    return _missing_types_count(coverage) == 0


def _coverage_gap_summary(coverage: dict[str, Any]) -> str:
    missing_rules = [str(item).strip() for item in (coverage.get("missing_rules") or []) if str(item).strip()]
    diagnostics = [item for item in (coverage.get("rule_diagnostics") or []) if isinstance(item, dict)]
    lines: list[str] = []
    for item in diagnostics:
        rule_id = str(item.get("rule_id") or "").strip()
        if not rule_id:
            continue
        missing_types = [str(x).strip() for x in (item.get("missing_types") or []) if str(x).strip()]
        if not missing_types:
            continue
        rule_text = str(item.get("rule_text") or "").strip()
        lines.append(f"- {rule_id}: missing_types={','.join(missing_types)} rule={rule_text}")
        if len(lines) >= 20:
            break
    if not lines and missing_rules:
        lines = [f"- {rule}" for rule in missing_rules[:20]]
    return "\n".join(lines) if lines else "- No explicit uncovered rule detected."


def _dump_existing_cases(cases: list[dict[str, Any]]) -> str:
    payload = [item for item in cases if isinstance(item, dict)][: _MAX_EXISTING_CASES_IN_PROMPT]
    if not payload:
        return "[]"
    return json.dumps(payload, ensure_ascii=False)


def _compute_information_gain(
    *,
    coverage_before: dict[str, Any],
    coverage_after: dict[str, Any],
    before_cases: list[dict[str, Any]],
    new_cases: list[dict[str, Any]],
) -> tuple[bool, dict[str, Any]]:
    covered_before = int(coverage_before.get("covered_rules") or 0)
    covered_after = int(coverage_after.get("covered_rules") or 0)
    missing_types_before = _missing_types_count(coverage_before)
    missing_types_after = _missing_types_count(coverage_after)

    before_buckets = {_coverage_bucket(case) for case in before_cases if isinstance(case, dict)}
    new_buckets = {
        _coverage_bucket(case)
        for case in new_cases
        if isinstance(case, dict) and _coverage_bucket(case) not in before_buckets
    }

    gain = (
        covered_after > covered_before
        or missing_types_after < missing_types_before
        or len(new_buckets) > 0
    )
    detail = {
        "covered_rules_before": covered_before,
        "covered_rules_after": covered_after,
        "missing_types_before": missing_types_before,
        "missing_types_after": missing_types_after,
        "new_bucket_count": len(new_buckets),
    }
    return bool(gain), detail


def _build_primary_prompt(
    *,
    base_prompt: str,
    round_index: int,
    expected_count: int,
    current_biz_key: str,
    accumulated_cases: list[dict[str, Any]],
    coverage_before: dict[str, Any],
) -> str:
    gaps = _coverage_gap_summary(coverage_before)
    existing_cases = _dump_existing_cases(accumulated_cases)
    return f"""{base_prompt}

MULTI-PASS STAGE: PRIMARY
WORKFLOW: primary_generation -> evaluate_quality -> evaluate_coverage -> decide_continue_or_stop
ROUND: {round_index}
CURRENT_BIZ_KEY: {current_biz_key}

QUALITY RULES:
- Generate only incremental, high-value cases grounded in explicit requirements.
- Do NOT rewrite or duplicate existing validation goals.
- Do NOT generate cases solely to increase count.
- If no meaningful incremental cases remain, return [].

COVERAGE GAPS BEFORE THIS ROUND:
{gaps}

EXISTING CASES (DO NOT DUPLICATE):
{existing_cases}

QUANTITY NOTE:
- expected_count={max(1, int(expected_count or 1))} is reference only.
- There is no requirement to reach this number.

Return ONLY JSON array.
"""
