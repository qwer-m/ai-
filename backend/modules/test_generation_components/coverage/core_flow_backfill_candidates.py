from __future__ import annotations

import ast
import json
import re
from typing import Any

from .core_flow_coverage_contract import map_case_to_core_flows
from ..postprocess.case_access import (
    case_steps,
    case_text_field,
    case_value,
)
from ..postprocess.streaming_expected_result_quality import (
    is_non_assertable_expected_result as _is_non_assertable_expected_result_shared,
)

NON_ASSERTABLE_EXPECTED_PHRASES = (
    "对应状态变化",
    "关键结果可核对",
    "对应内容",
    "匹配的结果",
    "结果内容可校验",
    "正常展示",
    "正常跳转",
    "正常更新",
    "符合预期",
)

_TRUNCATED_TEXT_ENDINGS = (
    "或显",
    "对应内",
    "可校",
    "正常展",
    "跳转至",
    "显示为",
)

_REQUIRED_FIELDS = (
    "description",
    "test_module",
    "steps",
    "expected_result",
    "source_flow_key",
)


def _normalize_steps(steps: Any) -> list[str]:
    if not isinstance(steps, list):
        return []
    output: list[str] = []
    for raw in steps:
        text = str(raw or "").strip()
        if not text:
            continue
        text = re.sub(r"^\s*(?:step\s*)?\d+\s*[\.\):、-]*\s*", "", text, flags=re.IGNORECASE)
        text = text.strip()
        if text:
            output.append(text)
    return [f"{idx}. {item}" for idx, item in enumerate(output, start=1)]


def _normalize_preconditions(value: Any, module: str) -> list[str]:
    if not isinstance(value, list):
        return [f"已登录并可访问模块：{module or '目标模块'}"]
    items = [str(item).strip() for item in value if str(item).strip()]
    if items:
        return items
    return [f"已登录并可访问模块：{module or '目标模块'}"]


def _looks_truncated_text(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    trimmed = re.sub(r"[。！？?!?]+$", "", normalized).strip()
    if not trimmed:
        return False
    return any(trimmed.endswith(suffix) for suffix in _TRUNCATED_TEXT_ENDINGS)


def _is_non_assertable_expected_result(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return True
    return _is_non_assertable_expected_result_shared(normalized)


def _case_exact_signature(case: dict[str, Any]) -> str:
    steps = "\n".join(_normalize_steps(case_steps(case)))
    payload = {
        "description": case_text_field(case, "description"),
        "test_module": case_text_field(case, "test_module"),
        "steps": steps,
        "test_input": case_text_field(case, "test_input"),
        "expected_result": case_text_field(case, "expected_result"),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def safe_json_array(raw_text: str) -> list[dict[str, Any]] | None:
    text = str(raw_text or "").strip()
    if not text:
        return []
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    candidates: list[str] = [text]
    left = text.find("[")
    right = text.rfind("]")
    if left >= 0 and right > left:
        candidates.append(text[left : right + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            try:
                parsed = ast.literal_eval(candidate)
            except Exception:
                continue
        if isinstance(parsed, list):
            if all(isinstance(item, dict) for item in parsed):
                return [item for item in parsed if isinstance(item, dict)]
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("cases"), list):
            case_rows = parsed.get("cases") or []
            if all(isinstance(item, dict) for item in case_rows):
                return [item for item in case_rows if isinstance(item, dict)]
            continue
    return None


def normalize_case_structure_light(case: dict[str, Any], index: int) -> dict[str, Any] | None:
    normalized = dict(case or {})
    description = case_text_field(normalized, "description")
    module = case_text_field(normalized, "test_module")
    steps = _normalize_steps(case_steps(normalized))
    expected_result = case_text_field(normalized, "expected_result")

    if not description or not module or not steps:
        return None

    source_flow_key = str(normalized.get("source_flow_key") or "").strip()
    source_flow_name = str(normalized.get("source_flow_name") or "").strip()

    model_priority = str(normalized.get("model_priority") or case_text_field(normalized, "priority") or "").strip().upper()
    if model_priority not in {"P0", "P1", "P2"}:
        model_priority = str(normalized.get("suggested_priority") or "").strip().upper()
    if model_priority not in {"P0", "P1", "P2"}:
        model_priority = "P2"

    normalized_case_id = f"BF-{index:03d}"
    normalized["case_id"] = normalized_case_id
    normalized["id"] = normalized_case_id
    normalized["description"] = description
    normalized["test_module"] = module
    normalized["steps"] = steps
    normalized["preconditions"] = _normalize_preconditions(case_value(normalized, "preconditions", []), module)
    normalized["test_input"] = case_text_field(normalized, "test_input") or description[:80]
    normalized["expected_result"] = expected_result
    normalized["priority"] = model_priority
    normalized["model_priority"] = model_priority
    normalized["source_flow_key"] = source_flow_key
    normalized["source_flow_name"] = source_flow_name
    normalized["backfill_generated"] = True

    if _looks_truncated_text(expected_result):
        normalized["expected_result_quality"] = "truncated"
        normalized["expected_result_quality_reason"] = "truncated_suffix_detected"
        normalized["truncated_text_detected"] = True
    elif _is_non_assertable_expected_result(expected_result):
        normalized["expected_result_quality"] = "non_assertable"
        normalized["expected_result_quality_reason"] = "template_or_weak_assertion"
        normalized["truncated_text_detected"] = False
    else:
        normalized["expected_result_quality"] = "assertable"
        normalized["expected_result_quality_reason"] = "contains_concrete_assertion"
        normalized["truncated_text_detected"] = False

    return normalized


def normalize_backfill_candidate_cases(
    parsed_candidates: list[dict[str, Any]],
    flow_name_map: dict[str, str],
) -> list[dict[str, Any]]:
    generated_cases: list[dict[str, Any]] = []
    for idx, raw_case in enumerate(parsed_candidates or [], start=1):
        if not isinstance(raw_case, dict):
            continue
        normalized = normalize_case_structure_light(raw_case, idx)
        if normalized is None:
            generated_cases.append(
                {
                    "case_id": f"BF-{idx:03d}",
                    "raw_case": dict(raw_case),
                    "source_flow_key": str(raw_case.get("source_flow_key") or ""),
                    "source_flow_name": str(raw_case.get("source_flow_name") or ""),
                    "backfill_generated": True,
                    "missing_required_fields": True,
                }
            )
            continue
        if not normalized.get("source_flow_name"):
            flow_key = str(normalized.get("source_flow_key") or "")
            if flow_key in flow_name_map:
                normalized["source_flow_name"] = flow_name_map[flow_key]
        generated_cases.append(normalized)
    return generated_cases


def accept_backfill_candidate_cases(
    priority_resolved_cases: list[dict[str, Any]],
    existing_cases: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    existing_signatures = {
        _case_exact_signature(item)
        for item in (existing_cases or [])
        if isinstance(item, dict)
    }

    accepted_cases: list[dict[str, Any]] = []
    rejected_cases: list[dict[str, Any]] = []

    for case in priority_resolved_cases or []:
        if not isinstance(case, dict):
            continue
        candidate = dict(case)
        source_flow_key = str(candidate.get("source_flow_key") or "").strip()

        missing_required = any(
            (key == "steps" and not isinstance(candidate.get("steps"), list))
            or (key != "steps" and not str(candidate.get(key) or "").strip())
            for key in _REQUIRED_FIELDS
        )
        if missing_required:
            candidate["rejection_reason"] = "missing_required_fields"
            rejected_cases.append(candidate)
            continue

        expected_result = str(candidate.get("expected_result") or "").strip()
        if not expected_result:
            candidate["rejection_reason"] = "missing_required_fields"
            rejected_cases.append(candidate)
            continue

        priority_final = str(candidate.get("priority_final") or "").strip().upper()
        if priority_final not in {"P0", "P1", "P2"}:
            candidate["rejection_reason"] = "invalid_priority_final"
            rejected_cases.append(candidate)
            continue

        if bool(candidate.get("truncated_text_detected")) or _looks_truncated_text(expected_result):
            candidate["rejection_reason"] = "truncated_expected_result"
            rejected_cases.append(candidate)
            continue

        expected_quality = str(candidate.get("expected_result_quality") or "").strip().lower()
        if expected_quality == "non_assertable" or _is_non_assertable_expected_result(expected_result):
            candidate["rejection_reason"] = "non_assertable_expected_result"
            rejected_cases.append(candidate)
            continue

        if any(phrase in expected_result for phrase in NON_ASSERTABLE_EXPECTED_PHRASES):
            candidate["rejection_reason"] = "non_assertable_expected_result"
            rejected_cases.append(candidate)
            continue

        if _case_exact_signature(candidate) in existing_signatures:
            candidate["rejection_reason"] = "duplicate_with_existing_case"
            rejected_cases.append(candidate)
            continue

        mapper_hits = map_case_to_core_flows(candidate)
        candidate["mapper_hits"] = dict(mapper_hits)
        candidate["matched_core_flows"] = sorted(list(mapper_hits.keys()))
        if not mapper_hits:
            candidate["rejection_reason"] = "source_flow_not_matched_by_mapper"
            rejected_cases.append(candidate)
            continue

        accepted_cases.append(candidate)

    return {
        "accepted_cases": accepted_cases,
        "rejected_cases": rejected_cases,
    }


__all__ = [
    "NON_ASSERTABLE_EXPECTED_PHRASES",
    "accept_backfill_candidate_cases",
    "normalize_backfill_candidate_cases",
    "normalize_case_structure_light",
    "safe_json_array",
]
