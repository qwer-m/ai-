from __future__ import annotations

from typing import Any, Callable, Iterable

from .case_access import case_step_lines, case_text_field
from .streaming_case_keys import review_case_id
from .streaming_postprocess_utils import (
    _clip_text,
    _dict_case_items,
    _json_for_prompt,
    _parsed_response_error_reason,
    _review_payload_debug_counts,
)
from .streaming_review_mapping import (
    REVIEW_DROP_REASONS,
    case_review_brief,
    map_review_selection_with_reasons,
)

ReviewPayloadParser = Callable[[str], Any]
ReviewPayloadNormalizer = Callable[[Any], Any]
ReviewSelectionMapResult = tuple[list[dict[str, Any]], set[str], dict[str, str], dict[str, str]]
ReviewSelectionMapper = Callable[..., ReviewSelectionMapResult]

REVIEW_SELECTION_SIGNAL_KEYS = ("kept_case_ids", "selected_case_ids", "kept", "selected", "dropped")
REVIEW_SELECTION_WRAPPER_KEYS = (
    "review_result",
    "reviewResult",
    "selection_result",
    "selectionResult",
    "selection",
    "result",
    "data",
    "payload",
)


def default_review_llm_runtime_debug() -> dict[str, Any]:
    return {
        "invoked": False,
        "pool_non_empty": False,
        "pool_size": 0,
        "deterministic_noop_skip_eligible": False,
        "deterministic_noop_preflight_selected_count": 0,
        "deterministic_noop_preflight_within_target_window": False,
        "deterministic_noop_preflight_signature_unchanged": False,
        "deterministic_noop_preflight_dropped_by_max": False,
        "skip_reason": "",
        "primary_model": "",
        "primary_invalid_reason": "",
        "primary_reason_incomplete": False,
        "primary_dropped_reason_count": 0,
        "primary_dropped_reason_payload_count": 0,
        "primary_reason_coverage_ratio": 0.0,
        "response_len": 0,
        "response_preview": "",
        "primary_response_metadata": {},
        "primary_compact_retry_invoked": False,
        "primary_compact_retry_model": "",
        "primary_compact_retry_invalid_reason": "",
        "primary_compact_retry_response_len": 0,
        "primary_compact_retry_response_metadata": {},
        "retry_invoked": False,
        "retry_reason": "",
        "retry_model": "",
        "retry_attempts": [],
        "retry_response_len": 0,
        "retry_parse_success": False,
        "retry_mapped_count": 0,
        "retry_payload_has_selection_signal": False,
        "parsed_type": "",
        "parsed_len": 0,
        "mapped_count": 0,
        "mapped_signature_count": 0,
        "dropped_reason_count": 0,
        "dropped_reason_payload_count": 0,
        "dropped_reason_unmapped_count": 0,
        "payload_has_selection_signal": False,
        "final_selected_and_dropped_overlap_count": 0,
        "final_selected_and_dropped_overlap_case_ids": [],
        "final_payload_consistent": True,
        "reason_repair_invoked": False,
        "reason_repair_model": "",
        "reason_repair_candidate_count": 0,
        "reason_repair_response_len": 0,
        "reason_repair_mapped_count": 0,
        "reason_repair_invalid_reason": "",
        "reason_repair_response_metadata": {},
        "final_source": "review_selector",
        "applied": False,
        "applied_reason": "",
        "exception": "",
        "forced_reset_by_fallback": False,
        "fallback_reason_incomplete": False,
        "final_reason_incomplete": False,
        "final_reason_coverage_ratio": 0.0,
    }


def build_review_llm_preflight_debug_fields(
    *,
    llm_pool_count: int,
    append_target_count: int,
    append_final_cap_count: int,
    skip_review_llm_by_noop: bool,
    noop_preflight_selected_count: int,
    noop_preflight_within_target_window: bool,
    noop_preflight_signature_unchanged: bool,
    noop_preflight_dropped_by_max: bool,
) -> dict[str, Any]:
    return {
        "pool_size": int(llm_pool_count or 0),
        "pool_non_empty": bool(llm_pool_count),
        "prompt_chars": 0,
        "prompt_est_tokens": 0,
        "candidate_count": int(llm_pool_count or 0),
        "append_target_count": int(append_target_count or 0),
        "append_final_cap_count": int(append_final_cap_count or 0),
        "deterministic_noop_skip_eligible": bool(skip_review_llm_by_noop),
        "deterministic_noop_preflight_selected_count": int(noop_preflight_selected_count or 0),
        "deterministic_noop_preflight_within_target_window": bool(
            noop_preflight_within_target_window
        ),
        "deterministic_noop_preflight_signature_unchanged": bool(
            noop_preflight_signature_unchanged
        ),
        "deterministic_noop_preflight_dropped_by_max": bool(noop_preflight_dropped_by_max),
    }


def review_payload_has_selection_signal(payload: Any) -> bool:
    return bool(isinstance(payload, dict) and any(key in payload for key in REVIEW_SELECTION_SIGNAL_KEYS))


def normalize_review_selection_payload(payload: Any) -> Any:
    if not isinstance(payload, dict) or review_payload_has_selection_signal(payload):
        return payload

    for key in REVIEW_SELECTION_WRAPPER_KEYS:
        nested = payload.get(key)
        if review_payload_has_selection_signal(nested):
            return nested

    for nested in payload.values():
        if review_payload_has_selection_signal(nested):
            return nested
    return payload


def count_review_dropped_reason_payload(payload: Any) -> int:
    dropped_payload = payload.get("dropped") if isinstance(payload, dict) else None
    if not isinstance(dropped_payload, list):
        return 0

    count = 0
    for dropped_item in dropped_payload:
        if not isinstance(dropped_item, dict):
            continue
        case_id = review_case_id(dropped_item)
        reason = str(dropped_item.get("reason") or "").strip()
        if case_id and reason:
            count += 1
    return count


def normalize_review_payload_invalid_reason(
    response_text: Any,
    parsed_payload: Any,
    *,
    parsed_type: str,
    mapped_count: int,
    payload_has_selection_signal: bool,
) -> str:
    invalid_reason = _parsed_response_error_reason(response_text, parsed_payload)
    if not invalid_reason and parsed_type not in {"dict", "list"}:
        invalid_reason = "schema_not_dict_or_list"
    elif not invalid_reason and int(mapped_count or 0) <= 0 and not payload_has_selection_signal:
        invalid_reason = "no_mapped_and_no_selection_signal"
    elif not invalid_reason and int(mapped_count or 0) <= 0:
        invalid_reason = "no_mapped_ids"
    return str(invalid_reason)


def review_retry_payload_debug_counts(result: dict[str, Any] | None) -> dict[str, int]:
    return _review_payload_debug_counts(result)


def resolve_review_fallback_models(
    *,
    client: Any,
    primary_model_name: str,
) -> list[str]:
    fallback_models: list[str] = []
    primary_model = str(primary_model_name or "").strip()
    primary_key = primary_model.lower()
    if "deepseek" in primary_key and primary_key != "deepseek-chat":
        fallback_models.append("deepseek-chat")
    for candidate in (
        str(getattr(client, "model", "") or "").strip(),
        str(getattr(client, "turbo_model", "") or "").strip(),
    ):
        if candidate:
            fallback_models.append(candidate)

    resolved: list[str] = []
    seen: set[str] = set()
    for model in fallback_models:
        model_key = str(model or "").strip()
        if not model_key or model_key in seen:
            continue
        seen.add(model_key)
        resolved.append(model_key)
    return resolved


def build_review_protocol_repair_prompt(
    *,
    review_prompt: str,
    candidate_cases: list[dict[str, Any]],
    drop_reasons: Iterable[str] = REVIEW_DROP_REASONS,
    max_candidates: int = 200,
) -> str:
    candidate_limit = max(0, int(max_candidates or 0))
    candidate_ids = [
        review_case_id(item)
        for item in _dict_case_items(candidate_cases)
        if review_case_id(item)
    ]
    candidate_ids = candidate_ids[:candidate_limit]
    allowed_reasons = [
        str(reason or "").strip()
        for reason in drop_reasons
        if str(reason or "").strip()
    ]
    return (
        f"{review_prompt}\n\n"
        "PROTOCOL FIX (MANDATORY):\n"
        "- Previous output was invalid for downstream selection mapping.\n"
        "- Return STRICT JSON only; no prose, no markdown, no code fences.\n"
        "- Schema MUST be:\n"
        "{\n"
        '  "kept_case_ids": ["<case_id>"],\n'
        '  "dropped": [{"case_id": "<case_id>", "reason": "<reason>"}]\n'
        "}\n"
        "- `kept_case_ids` and `dropped[*].case_id` must come from this candidate id list only:\n"
        f"{_json_for_prompt(candidate_ids)}\n"
        "- Do not invent or rewrite case ids.\n"
        "- `dropped[*].reason` must be ONE canonical key from:\n"
        f"  {_json_for_prompt(allowed_reasons, compact=True)}\n"
    )


def analyze_review_retry_payload(
    response_text: str,
    *,
    candidate_cases: list[dict[str, Any]],
    parse_json_fn: ReviewPayloadParser,
    normalize_json_structure_fn: ReviewPayloadNormalizer,
    reason_origin: str = "llm",
    map_selection_fn: ReviewSelectionMapper = map_review_selection_with_reasons,
) -> dict[str, Any]:
    payload_text = str(response_text or "")
    reviewed_payload = parse_json_fn(payload_text)
    parsed_type = type(reviewed_payload).__name__
    parsed_len = int(len(reviewed_payload)) if isinstance(reviewed_payload, (list, dict)) else 0
    parse_success = not (
        isinstance(reviewed_payload, dict)
        and bool(str(reviewed_payload.get("error") or "").strip())
    )

    if isinstance(reviewed_payload, list) and all(isinstance(item, dict) for item in reviewed_payload):
        reviewed_payload = normalize_json_structure_fn(reviewed_payload)
        parsed_type = type(reviewed_payload).__name__
        parsed_len = int(len(reviewed_payload)) if isinstance(reviewed_payload, list) else 0
    reviewed_payload = normalize_review_selection_payload(reviewed_payload)
    parsed_type = type(reviewed_payload).__name__
    parsed_len = int(len(reviewed_payload)) if isinstance(reviewed_payload, (list, dict)) else 0

    (
        mapped,
        mapped_signatures,
        dropped_reason_map,
        dropped_reason_origin_map,
    ) = map_selection_fn(
        candidate_cases,
        reviewed_payload,
        reason_origin=reason_origin,
    )
    dropped_reason_payload_count = count_review_dropped_reason_payload(reviewed_payload)
    dropped_reason_unmapped_count = max(
        0,
        int(dropped_reason_payload_count) - int(len(dropped_reason_map or {})),
    )
    has_selection_signal = review_payload_has_selection_signal(reviewed_payload)
    invalid_reason = normalize_review_payload_invalid_reason(
        payload_text,
        reviewed_payload,
        parsed_type=parsed_type,
        mapped_count=len(mapped or []),
        payload_has_selection_signal=has_selection_signal,
    )

    return {
        "payload": reviewed_payload,
        "parsed_type": str(parsed_type),
        "parsed_len": int(parsed_len),
        "parse_success": bool(parse_success),
        "mapped": _dict_case_items(mapped),
        "mapped_signatures": set(mapped_signatures or set()),
        "dropped_reason_map": dict(dropped_reason_map or {}),
        "dropped_reason_origin_map": dict(dropped_reason_origin_map or {}),
        "dropped_reason_payload_count": int(dropped_reason_payload_count),
        "dropped_reason_unmapped_count": int(dropped_reason_unmapped_count),
        "payload_has_selection_signal": bool(has_selection_signal),
        "invalid_reason": str(invalid_reason),
    }


def case_review_retry_brief(case: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = case_review_brief(
        case,
        id_key="id",
        module_key="module",
        include_expected_result=True,
        prefer_final_priority=True,
    )
    if not compact:
        return {}

    preconditions = _clip_text(case_text_field(case, "preconditions"), 180, strip=True)
    if preconditions:
        compact["preconditions"] = preconditions

    steps = [
        _clip_text(step, 140, strip=True)
        for step in case_step_lines(case)[:6]
        if _clip_text(step, 140, strip=True)
    ]
    if steps:
        compact["steps"] = steps

    test_input = _clip_text(case_text_field(case, "test_input"), 180, strip=True)
    if test_input:
        compact["test_input"] = test_input

    return compact


def build_compact_review_retry_prompt(
    candidate_cases: list[dict[str, Any]],
    *,
    target_min_count: int,
    target_max_count: int,
    drop_reasons: Iterable[str] = REVIEW_DROP_REASONS,
    max_candidates: int = 200,
) -> str:
    compact_cases: list[dict[str, Any]] = []
    for item in _dict_case_items(candidate_cases):
        compact = case_review_retry_brief(item)
        if compact:
            compact_cases.append(compact)

    candidate_limit = max(0, int(max_candidates or 0))
    candidate_ids = [str(item.get("id") or "") for item in compact_cases if item.get("id")]
    reasons = tuple(str(reason or "").strip() for reason in drop_reasons if str(reason or "").strip())
    min_count = max(1, int(target_min_count or 1))
    max_count = max(min_count, int(target_max_count or min_count))
    return (
        "REVIEW COMPACT RETRY.\n"
        "The previous review response had no usable final answer. Do not reason aloud.\n"
        "Return STRICT compact JSON only, no prose, no markdown, no code fences.\n"
        f"Keep between {min_count} and {max_count} cases when possible.\n"
        "Schema:\n"
        '{"kept_case_ids":["TC-001"],"dropped":[{"case_id":"TC-002","reason":"coverage_redundant"}]}\n'
        f"Allowed reasons: {', '.join(reasons)}.\n"
        "Case ids must come from this list only:\n"
        f"{_json_for_prompt(candidate_ids[:candidate_limit])}\n"
        "Compact candidate facts:\n"
        f"{_json_for_prompt(compact_cases[:candidate_limit], compact=True)}"
    )
