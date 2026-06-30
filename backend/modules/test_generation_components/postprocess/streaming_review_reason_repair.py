from __future__ import annotations

from typing import Any, Callable, Iterable

from .streaming_case_keys import case_signature, review_case_id
from .streaming_postprocess_utils import (
    _dict_case_items,
    _json_for_prompt,
    _parsed_response_error_reason,
)
from .streaming_review_mapping import REASON_REPAIR_DROP_REASONS, case_review_brief

ReasonRepairPayloadParser = Callable[[str], Any]


def _normalized_reason_origin(reason_origin: str) -> str:
    normalized = str(reason_origin or "").strip().lower()
    return normalized if normalized in {"llm", "fallback_llm"} else "llm"


def _allowed_reason_tuple(drop_reasons: Iterable[str]) -> tuple[str, ...]:
    reasons: list[str] = []
    seen: set[str] = set()
    for reason in drop_reasons:
        normalized = str(reason or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        reasons.append(normalized)
    return tuple(reasons)


def build_reason_repair_candidates(
    missing_reason_cases: Iterable[Any],
    *,
    max_candidates: int = 80,
) -> list[dict[str, Any]]:
    repair_candidates: list[dict[str, Any]] = []
    limit = max(0, int(max_candidates or 0))
    for item in _dict_case_items(missing_reason_cases)[:limit]:
        compact = case_review_brief(
            item,
            id_key="id",
            module_key="module",
            include_expected_result=True,
            prefer_final_priority=True,
        )
        if compact:
            repair_candidates.append(compact)
    return repair_candidates


def build_compact_reason_repair_prompt(
    missing_reason_cases: Iterable[Any],
    *,
    drop_reasons: Iterable[str] = REASON_REPAIR_DROP_REASONS,
    max_candidates: int = 80,
) -> str:
    repair_candidates = build_reason_repair_candidates(
        missing_reason_cases,
        max_candidates=max_candidates,
    )
    if not repair_candidates:
        return ""

    repair_ids = [str(item.get("id") or "") for item in repair_candidates if item.get("id")]
    reasons = _allowed_reason_tuple(drop_reasons)
    return (
        "REVIEW REASON REPAIR ONLY.\n"
        "The final selected case set is already fixed. Do NOT select or rewrite cases.\n"
        "For each dropped candidate below, assign exactly one canonical drop reason.\n"
        "Return STRICT JSON only, no prose, no markdown.\n"
        "Schema:\n"
        '{"dropped":[{"case_id":"TC-001","reason":"coverage_redundant"}]}\n'
        f"Allowed reasons: {', '.join(reasons)}.\n"
        "Use coverage_redundant when another retained case covers the same rule or workflow value.\n"
        "Use duplicate only for near-identical validation targets.\n"
        "Use low_value for weak, generic, or low business-signal cases.\n"
        "Use selection_tradeoff_omitted when the case has some value but was omitted to keep the final set concise.\n"
        f"case_id must come from: {_json_for_prompt(repair_ids)}\n"
        f"Dropped candidates: {_json_for_prompt(repair_candidates, compact=True)}"
    )


def _reason_repair_dropped_payload(payload: Any) -> list[Any]:
    dropped_payload = payload.get("dropped") if isinstance(payload, dict) else None
    return dropped_payload if isinstance(dropped_payload, list) else []


def analyze_reason_repair_payload(
    response_text: str,
    *,
    missing_reason_cases: Iterable[Any],
    parse_json_fn: ReasonRepairPayloadParser,
    allowed_reasons: Iterable[str] = REASON_REPAIR_DROP_REASONS,
    reason_origin: str = "llm",
    existing_drop_reason_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload_text = str(response_text or "")
    repair_payload = parse_json_fn(payload_text)
    parsed_type = type(repair_payload).__name__
    parsed_len = int(len(repair_payload)) if isinstance(repair_payload, (list, dict)) else 0
    parse_success = not (
        isinstance(repair_payload, dict)
        and bool(str(repair_payload.get("error") or "").strip())
    )

    repair_invalid_reason = _parsed_response_error_reason(payload_text, repair_payload)
    if not repair_invalid_reason and not isinstance(repair_payload, dict):
        repair_invalid_reason = "schema_not_dict"

    dropped_payload = _reason_repair_dropped_payload(repair_payload)
    case_by_id = {
        review_case_id(item): item
        for item in _dict_case_items(missing_reason_cases)
        if review_case_id(item)
    }
    allowed = set(_allowed_reason_tuple(allowed_reasons))
    origin = _normalized_reason_origin(reason_origin)
    existing_signatures = set((existing_drop_reason_map or {}).keys())

    dropped_reason_map: dict[str, str] = {}
    dropped_reason_origin_map: dict[str, str] = {}
    dropped_reason_payload_count = 0
    invalid_reason_payload_count = 0
    missing_field_payload_count = 0
    unknown_case_id_count = 0
    missing_signature_count = 0
    skipped_existing_count = 0

    for dropped_item in dropped_payload:
        if not isinstance(dropped_item, dict):
            missing_field_payload_count += 1
            continue

        case_id = review_case_id(dropped_item)
        reason = str(dropped_item.get("reason") or "").strip().lower()
        if not case_id or not reason:
            missing_field_payload_count += 1
            continue

        dropped_reason_payload_count += 1
        if reason not in allowed:
            invalid_reason_payload_count += 1
            continue

        original = case_by_id.get(case_id)
        if not isinstance(original, dict):
            unknown_case_id_count += 1
            continue

        signature = case_signature(original)
        if not signature:
            missing_signature_count += 1
            continue
        if signature in existing_signatures or signature in dropped_reason_map:
            skipped_existing_count += 1
            continue

        dropped_reason_map[signature] = reason
        dropped_reason_origin_map[signature] = origin

    mapped_count = int(len(dropped_reason_map))
    dropped_reason_unmapped_count = max(0, int(dropped_reason_payload_count) - mapped_count)
    if mapped_count <= 0 and not repair_invalid_reason:
        repair_invalid_reason = "no_mapped_reasons"

    return {
        "payload": repair_payload,
        "parsed_type": str(parsed_type),
        "parsed_len": int(parsed_len),
        "parse_success": bool(parse_success),
        "mapped_count": int(mapped_count),
        "dropped_reason_map": dropped_reason_map,
        "dropped_reason_origin_map": dropped_reason_origin_map,
        "dropped_reason_payload_count": int(dropped_reason_payload_count),
        "dropped_reason_unmapped_count": int(dropped_reason_unmapped_count),
        "invalid_reason_payload_count": int(invalid_reason_payload_count),
        "missing_field_payload_count": int(missing_field_payload_count),
        "unknown_case_id_count": int(unknown_case_id_count),
        "missing_signature_count": int(missing_signature_count),
        "skipped_existing_count": int(skipped_existing_count),
        "invalid_reason": str(repair_invalid_reason),
    }


def reason_repair_payload_debug_counts(result: dict[str, Any] | None) -> dict[str, int]:
    payload = result if isinstance(result, dict) else {}
    return {
        "mapped_count": int(payload.get("mapped_count") or 0),
        "dropped_reason_count": int(len(payload.get("dropped_reason_map") or {})),
        "dropped_reason_payload_count": int(payload.get("dropped_reason_payload_count") or 0),
        "dropped_reason_unmapped_count": int(payload.get("dropped_reason_unmapped_count") or 0),
        "invalid_reason_payload_count": int(payload.get("invalid_reason_payload_count") or 0),
        "missing_field_payload_count": int(payload.get("missing_field_payload_count") or 0),
        "unknown_case_id_count": int(payload.get("unknown_case_id_count") or 0),
        "skipped_existing_count": int(payload.get("skipped_existing_count") or 0),
    }
