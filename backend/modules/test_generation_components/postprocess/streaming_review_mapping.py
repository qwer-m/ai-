from __future__ import annotations

import re
from typing import Any

from .case_access import case_priority, case_text_field
from .streaming_case_keys import case_signature, review_case_id

REVIEW_DROP_REASONS = (
    "coverage_redundant",
    "duplicate",
    "low_value",
    "coverage_protected_omitted",
    "high_signal_omitted",
    "selection_tradeoff_omitted",
    "fallback_unspecified",
)
REASON_REPAIR_DROP_REASONS = (
    "coverage_redundant",
    "duplicate",
    "low_value",
    "selection_tradeoff_omitted",
)
CANONICAL_REVIEW_DROP_REASONS = set(REVIEW_DROP_REASONS)


def _clip_text(value: Any, limit: int, *, strip: bool = False) -> str:
    text = str(value or "")
    if strip:
        text = text.strip()
    return text[: max(0, int(limit))]


def case_review_brief(
    case: dict[str, Any],
    *,
    id_key: str = "case_id",
    module_key: str = "test_module",
    include_expected_result: bool = False,
    include_priority: bool = True,
    prefer_final_priority: bool = False,
    require_id: bool = True,
) -> dict[str, str]:
    case_id = review_case_id(case)
    if require_id and not case_id:
        return {}

    brief: dict[str, str] = {
        id_key: str(case_id or "").strip(),
        module_key: _clip_text(case_text_field(case, "test_module"), 80, strip=True),
        "description": _clip_text(case_text_field(case, "description"), 180, strip=True),
    }
    if include_expected_result:
        brief["expected_result"] = _clip_text(case_text_field(case, "expected_result"), 180, strip=True)
    if include_priority:
        brief["priority"] = _clip_text(case_priority(case, prefer_final=prefer_final_priority), 12, strip=True)
    return brief


def normalize_review_llm_reason(reason_text: str) -> str:
    raw = str(reason_text or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered in CANONICAL_REVIEW_DROP_REASONS:
        return lowered
    compact = re.sub(r"[\s\-]+", "_", lowered)
    if compact in CANONICAL_REVIEW_DROP_REASONS:
        return compact
    if ("duplicate" in lowered) or ("重复" in raw):
        return "duplicate"
    if ("low_value" in lowered) or ("low value" in lowered) or ("低价值" in raw):
        return "low_value"
    if ("coverage_protected_omitted" in lowered) or ("coverage protected" in lowered):
        return "coverage_protected_omitted"
    if ("coverage_redundant" in lowered) or ("coverage redundant" in lowered) or ("覆盖冗余" in raw):
        return "coverage_redundant"
    if ("selection_tradeoff_omitted" in lowered) or ("selection tradeoff" in lowered) or ("tradeoff" in lowered):
        return "selection_tradeoff_omitted"
    if ("high_signal_omitted" in lowered) or ("high signal" in lowered):
        return "high_signal_omitted"
    if ("fallback_unspecified" in lowered) or ("unspecified" in lowered):
        return "fallback_unspecified"
    return raw


def map_review_to_candidates(candidates: list[dict[str, Any]], reviewed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidate_map = {case_signature(item): item for item in candidates if isinstance(item, dict)}
    mapped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in reviewed:
        if not isinstance(item, dict):
            continue
        key = case_signature(item)
        if key in seen:
            continue
        original = candidate_map.get(key)
        if original is None:
            continue
        mapped.append(original)
        seen.add(key)
    return mapped


def map_review_selection_with_reasons(
    candidates: list[dict[str, Any]],
    reviewed_payload: Any,
    reason_origin: str = "llm",
) -> tuple[list[dict[str, Any]], set[str], dict[str, str], dict[str, str]]:
    candidate_items = [item for item in candidates if isinstance(item, dict)]
    candidate_by_id: dict[str, dict[str, Any]] = {}
    candidate_by_signature: dict[str, dict[str, Any]] = {}
    for item in candidate_items:
        candidate_by_signature[case_signature(item)] = item
        case_id = review_case_id(item)
        if case_id:
            candidate_by_id[case_id] = item

    selected_cases: list[dict[str, Any]] = []
    selected_signatures: set[str] = set()
    drop_reason_by_signature: dict[str, str] = {}
    drop_reason_origin_by_signature: dict[str, str] = {}

    if isinstance(reviewed_payload, list):
        scalar_ids: list[str] = []
        for value in reviewed_payload:
            if isinstance(value, (str, int)):
                case_id = str(value or "").strip()
                if case_id:
                    scalar_ids.append(case_id)
        if scalar_ids:
            seen_ids: set[str] = set()
            for case_id in scalar_ids:
                if case_id in seen_ids:
                    continue
                seen_ids.add(case_id)
                original = candidate_by_id.get(case_id)
                if not isinstance(original, dict):
                    continue
                signature = case_signature(original)
                if not signature or signature in selected_signatures:
                    continue
                selected_cases.append(original)
                selected_signatures.add(signature)
            if selected_cases:
                return selected_cases, selected_signatures, drop_reason_by_signature, drop_reason_origin_by_signature

        mapped = map_review_to_candidates(candidate_items, reviewed_payload)
        for item in mapped:
            if not isinstance(item, dict):
                continue
            signature = case_signature(item)
            if not signature or signature in selected_signatures:
                continue
            selected_cases.append(item)
            selected_signatures.add(signature)
        return selected_cases, selected_signatures, drop_reason_by_signature, drop_reason_origin_by_signature

    payload = reviewed_payload if isinstance(reviewed_payload, dict) else {}

    def _coerce_case_id_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        output: list[str] = []
        seen: set[str] = set()
        for item in value:
            case_id = str(item or "").strip()
            if not case_id or case_id in seen:
                continue
            seen.add(case_id)
            output.append(case_id)
        return output

    kept_case_ids = _coerce_case_id_list(payload.get("kept_case_ids"))
    if not kept_case_ids:
        kept_case_ids = _coerce_case_id_list(payload.get("selected_case_ids"))
    if not kept_case_ids:
        kept_case_ids = _coerce_case_id_list(payload.get("kept"))

    for case_id in kept_case_ids:
        original = candidate_by_id.get(case_id)
        if not isinstance(original, dict):
            continue
        signature = case_signature(original)
        if not signature or signature in selected_signatures:
            continue
        selected_cases.append(original)
        selected_signatures.add(signature)

    if not selected_cases:
        fallback_selected = payload.get("selected")
        if isinstance(fallback_selected, list):
            mapped = map_review_to_candidates(candidate_items, fallback_selected)
            for item in mapped:
                signature = case_signature(item)
                if not signature or signature in selected_signatures:
                    continue
                selected_cases.append(item)
                selected_signatures.add(signature)

    dropped_payload = payload.get("dropped")
    if isinstance(dropped_payload, list):
        for item in dropped_payload:
            if not isinstance(item, dict):
                continue
            case_id = review_case_id(item)
            reason = str(item.get("reason") or "").strip()
            if not case_id or not reason:
                continue
            original = candidate_by_id.get(case_id)
            if not isinstance(original, dict):
                continue
            signature = case_signature(original)
            if not signature:
                continue
            drop_reason_by_signature[signature] = reason
            normalized_origin = str(reason_origin or "").strip().lower()
            if normalized_origin not in {"llm", "fallback_llm"}:
                normalized_origin = "llm"
            drop_reason_origin_by_signature[signature] = normalized_origin

    return selected_cases, selected_signatures, drop_reason_by_signature, drop_reason_origin_by_signature
