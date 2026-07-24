from __future__ import annotations

from dataclasses import dataclass
import time
import traceback
from typing import Any, Callable

from .streaming_case_keys import case_signature as _signature
from .streaming_case_keys import review_case_id as _review_case_id
from .streaming_postprocess_utils import (
    RETRYABLE_RESPONSE_ERROR_REASONS as _RETRYABLE_RESPONSE_ERROR_REASONS,
    _client_response_metadata,
    _clip_text,
    _dict_case_items,
    _select_review_model,
)
from .streaming_review_mapping import (
    REVIEW_DROP_REASONS as _REVIEW_DROP_REASONS,
    map_review_selection_with_reasons as _map_review_selection_with_reasons,
)
from .streaming_review_retry import (
    analyze_review_retry_payload as _analyze_review_retry_payload,
    build_review_protocol_repair_prompt as _build_review_protocol_repair_prompt,
    resolve_review_fallback_models as _resolve_review_fallback_models,
    review_retry_payload_debug_counts as _review_payload_debug_counts,
)


@dataclass(frozen=True)
class ReviewLlmSelectionResult:
    selected_from_llm_pool: list[dict[str, Any]]
    selected_signatures: set[str]
    drop_reason_raw_map: dict[str, str]
    drop_reason_raw_origin_map: dict[str, str]
    runtime_debug: dict[str, Any]
    applied: bool


def run_review_llm_selection(
    *,
    client: Any,
    db: Any,
    requirement: str,
    llm_pool_cases: list[dict[str, Any]],
    selected_from_llm_pool: list[dict[str, Any]],
    skip_review_llm_by_noop: bool,
    review_llm_runtime_debug: dict[str, Any],
    reference_count_effective: int,
    review_target_min_count: int,
    review_target_max_count: int,
    review_constraints: dict[str, Any],
    review_contract_context: dict[str, Any],
    current_biz_key: str,
    build_review_select_prompt_fn: Callable[..., str],
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
) -> ReviewLlmSelectionResult:
    review_llm_applied = False
    review_llm_selected_signatures: set[str] = set()
    review_llm_drop_reason_raw_map: dict[str, str] = {}
    review_llm_drop_reason_raw_origin_map: dict[str, str] = {}
    review_prompt = ""

    try:
        if llm_pool_cases and skip_review_llm_by_noop:
            review_llm_runtime_debug["final_source"] = "review_selector"
            review_llm_runtime_debug["applied"] = False
            review_llm_runtime_debug["applied_reason"] = "skipped_deterministic_noop"
            review_llm_runtime_debug["skip_reason"] = "llm_pool_within_constraint_window"
        elif llm_pool_cases:
            review_prompt = build_review_select_prompt_fn(
                requirement_context=requirement,
                candidate_cases=llm_pool_cases,
                target_count=max(1, int(reference_count_effective or len(llm_pool_cases) or 1)),
                target_min_count=review_target_min_count,
                target_max_count=review_target_max_count,
                coverage_constraints=review_constraints,
                review_contract_context=review_contract_context,
                current_biz_key=current_biz_key,
                pretty_json=False,
            )
            review_llm_runtime_debug["prompt_chars"] = int(len(review_prompt or ""))
            review_llm_runtime_debug["prompt_est_tokens"] = int(round(len(review_prompt or "") / 4))
            review_llm_runtime_debug["invoked"] = True

            review_llm_runtime_debug["primary_model"] = _select_review_model(client, review_prompt)
            review_llm_started = time.perf_counter()
            review_response = client.generate_response(
                review_prompt,
                "You are a QA Auditor.",
                db=db,
                task_type="review",
            )
            review_llm_runtime_debug["primary_duration_ms"] = max(
                0,
                int(round((time.perf_counter() - review_llm_started) * 1000)),
            )
            review_response_text = str(review_response or "")
            review_llm_runtime_debug["primary_response_metadata"] = _client_response_metadata(client)
            primary_result = _analyze_review_retry_payload(
                review_response_text,
                candidate_cases=llm_pool_cases,
                parse_json_fn=clean_and_parse_json_fn,
                normalize_json_structure_fn=normalize_json_structure_fn,
                reason_origin="llm",
                map_selection_fn=_map_review_selection_with_reasons,
            )
            review_llm_runtime_debug["response_len"] = int(len(review_response_text))
            review_llm_runtime_debug["response_preview"] = _clip_text(review_response_text, 500)
            review_llm_runtime_debug["parsed_type"] = str(primary_result.get("parsed_type") or "")
            review_llm_runtime_debug["parsed_len"] = int(primary_result.get("parsed_len") or 0)
            primary_debug_counts = _review_payload_debug_counts(primary_result)
            review_llm_runtime_debug.update(primary_debug_counts)
            primary_mapped_count = primary_debug_counts["mapped_count"]
            primary_dropped_reason_count = primary_debug_counts["dropped_reason_count"]
            primary_dropped_reason_payload_count = primary_debug_counts["dropped_reason_payload_count"]
            review_llm_runtime_debug["primary_dropped_reason_count"] = int(primary_dropped_reason_count)
            review_llm_runtime_debug["primary_dropped_reason_payload_count"] = int(primary_dropped_reason_payload_count)
            review_llm_runtime_debug["primary_reason_incomplete"] = bool(
                primary_mapped_count > 0 and primary_dropped_reason_count <= 0
            )
            review_llm_runtime_debug["primary_reason_coverage_ratio"] = (
                round(float(primary_dropped_reason_count) / float(primary_dropped_reason_payload_count), 4)
                if primary_dropped_reason_payload_count > 0
                else 0.0
            )
            review_llm_runtime_debug["payload_has_selection_signal"] = bool(
                primary_result.get("payload_has_selection_signal")
            )
            review_llm_runtime_debug["primary_invalid_reason"] = str(primary_result.get("invalid_reason") or "")

            final_result = dict(primary_result)
            final_source = "primary_llm"
            retry_reason = str(primary_result.get("invalid_reason") or "")
            if retry_reason:
                retry_reason, final_source, final_result, review_response_text = _run_contract_retry_if_available(
                    client=client,
                    db=db,
                    retry_reason=retry_reason,
                    review_response_text=review_response_text,
                    review_prompt=review_prompt,
                    llm_pool_cases=llm_pool_cases,
                    clean_and_parse_json_fn=clean_and_parse_json_fn,
                    normalize_json_structure_fn=normalize_json_structure_fn,
                    review_llm_runtime_debug=review_llm_runtime_debug,
                    final_result=final_result,
                    final_source=final_source,
                )

            if retry_reason:
                fallback_skip_reason = _review_fallback_skip_reason(
                    retry_reason=retry_reason,
                    review_llm_runtime_debug=review_llm_runtime_debug,
                )
            else:
                fallback_skip_reason = ""

            if fallback_skip_reason:
                review_llm_runtime_debug["fallback_skipped_reason"] = fallback_skip_reason
                review_llm_runtime_debug["retry_response_len"] = int(len(review_response_text))
            elif retry_reason:
                final_source, final_result, review_response_text = _run_fallback_retries(
                    client=client,
                    db=db,
                    retry_reason=retry_reason,
                    review_prompt=review_prompt,
                    review_response_text=review_response_text,
                    llm_pool_cases=llm_pool_cases,
                    clean_and_parse_json_fn=clean_and_parse_json_fn,
                    normalize_json_structure_fn=normalize_json_structure_fn,
                    review_llm_runtime_debug=review_llm_runtime_debug,
                    final_result=final_result,
                    final_source=final_source,
                )

            _finalize_retry_debug(
                final_result=final_result,
                review_llm_runtime_debug=review_llm_runtime_debug,
            )
            final_invalid_reason = str(final_result.get("invalid_reason") or "")
            review_llm_runtime_debug["final_source"] = (
                str(final_source) if not final_invalid_reason else "review_selector"
            )
            if not final_invalid_reason:
                selected_from_llm_pool = _dict_case_items(final_result.get("mapped") or [])
                review_llm_selected_signatures = set(final_result.get("mapped_signatures") or set())
                review_llm_drop_reason_raw_map = dict(final_result.get("dropped_reason_map") or {})
                review_llm_drop_reason_raw_origin_map = dict(final_result.get("dropped_reason_origin_map") or {})
                final_dropped_reason_count = int(len(review_llm_drop_reason_raw_map))
                _record_final_overlap_debug(
                    llm_pool_cases=llm_pool_cases,
                    final_result=final_result,
                    review_llm_drop_reason_raw_map=review_llm_drop_reason_raw_map,
                    review_llm_runtime_debug=review_llm_runtime_debug,
                )
                review_llm_runtime_debug["final_dropped_reason_count"] = int(final_dropped_reason_count)
                final_debug_counts = _review_payload_debug_counts(final_result)
                review_llm_runtime_debug["final_dropped_reason_payload_count"] = final_debug_counts[
                    "dropped_reason_payload_count"
                ]
                review_llm_runtime_debug["final_dropped_reason_unmapped_count"] = final_debug_counts[
                    "dropped_reason_unmapped_count"
                ]
                review_llm_applied = True
                review_llm_runtime_debug["applied"] = True
                review_llm_runtime_debug["applied_reason"] = (
                    "mapped_valid_payload" if final_source == "primary_llm" else "retry_payload_valid"
                )
                review_llm_runtime_debug["fallback_reason_incomplete"] = bool(
                    final_source == "fallback_llm" and int(final_dropped_reason_count or 0) <= 0
                )
            else:
                review_llm_runtime_debug["applied"] = False
                review_llm_runtime_debug["applied_reason"] = final_invalid_reason
        else:
            review_llm_runtime_debug["applied_reason"] = "empty_llm_pool"
    except Exception:
        review_llm_runtime_debug["exception"] = str(traceback.format_exc()[-1500:])

    return ReviewLlmSelectionResult(
        selected_from_llm_pool=_dict_case_items(selected_from_llm_pool),
        selected_signatures=set(review_llm_selected_signatures),
        drop_reason_raw_map=dict(review_llm_drop_reason_raw_map),
        drop_reason_raw_origin_map=dict(review_llm_drop_reason_raw_origin_map),
        runtime_debug=review_llm_runtime_debug,
        applied=bool(review_llm_applied),
    )


def _run_contract_retry_if_available(
    *,
    client: Any,
    db: Any,
    retry_reason: str,
    review_response_text: str,
    review_prompt: str,
    llm_pool_cases: list[dict[str, Any]],
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    review_llm_runtime_debug: dict[str, Any],
    final_result: dict[str, Any],
    final_source: str,
) -> tuple[str, str, dict[str, Any], str]:
    review_llm_runtime_debug["retry_invoked"] = True
    review_llm_runtime_debug["retry_reason"] = retry_reason
    primary_model_for_retry = str(review_llm_runtime_debug.get("primary_model") or "").strip()
    if not (
        retry_reason in _RETRYABLE_RESPONSE_ERROR_REASONS
        and primary_model_for_retry
        and str(review_response_text or "").startswith("Error: Empty response")
    ):
        return retry_reason, final_source, final_result, review_response_text

    review_llm_runtime_debug["primary_contract_retry_invoked"] = True
    review_llm_runtime_debug["primary_contract_retry_model"] = primary_model_for_retry
    contract_retry_started = time.perf_counter()
    contract_retry_text = str(
        client.generate_response(
            _build_review_protocol_repair_prompt(
                review_prompt=review_prompt,
                candidate_cases=llm_pool_cases,
                drop_reasons=_REVIEW_DROP_REASONS,
            ),
            "You are a QA Auditor. Return strict JSON only.",
            db=db,
            task_type="review",
            model=primary_model_for_retry,
            max_tokens=4096,
        )
        or ""
    )
    review_llm_runtime_debug["primary_contract_retry_duration_ms"] = max(
        0,
        int(round((time.perf_counter() - contract_retry_started) * 1000)),
    )
    review_llm_runtime_debug["primary_contract_retry_response_len"] = int(len(contract_retry_text))
    review_llm_runtime_debug["primary_contract_retry_response_metadata"] = _client_response_metadata(client)
    contract_retry_result = _analyze_review_retry_payload(
        contract_retry_text,
        candidate_cases=llm_pool_cases,
        parse_json_fn=clean_and_parse_json_fn,
        normalize_json_structure_fn=normalize_json_structure_fn,
        reason_origin="primary_contract_retry",
        map_selection_fn=_map_review_selection_with_reasons,
    )
    contract_retry_invalid_reason = str(contract_retry_result.get("invalid_reason") or "")
    review_llm_runtime_debug["primary_contract_retry_invalid_reason"] = contract_retry_invalid_reason
    if contract_retry_invalid_reason:
        return retry_reason, final_source, final_result, review_response_text
    return "", "primary_contract_retry", contract_retry_result, contract_retry_text


def _review_fallback_skip_reason(
    *,
    retry_reason: str,
    review_llm_runtime_debug: dict[str, Any],
) -> str:
    if retry_reason not in _RETRYABLE_RESPONSE_ERROR_REASONS:
        return ""
    if not bool(review_llm_runtime_debug.get("primary_contract_retry_invoked")):
        return ""
    contract_invalid_reason = str(review_llm_runtime_debug.get("primary_contract_retry_invalid_reason") or "")
    if contract_invalid_reason not in _RETRYABLE_RESPONSE_ERROR_REASONS:
        return ""
    return "empty_response_after_contract_retry"


def _run_fallback_retries(
    *,
    client: Any,
    db: Any,
    retry_reason: str,
    review_prompt: str,
    review_response_text: str,
    llm_pool_cases: list[dict[str, Any]],
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    review_llm_runtime_debug: dict[str, Any],
    final_result: dict[str, Any],
    final_source: str,
) -> tuple[str, dict[str, Any], str]:
    fallback_models = _resolve_review_fallback_models(
        client=client,
        primary_model_name=str(review_llm_runtime_debug.get("primary_model") or ""),
    )
    repair_prompt = _build_review_protocol_repair_prompt(
        review_prompt=review_prompt,
        candidate_cases=llm_pool_cases,
        drop_reasons=_REVIEW_DROP_REASONS,
    )

    for fallback_model in fallback_models:
        model_key = str(fallback_model or "").strip()
        if not model_key:
            continue
        fallback_started = time.perf_counter()
        review_response_retry = client.generate_response(
            repair_prompt,
            "You are a QA Auditor.",
            db=db,
            task_type="review",
            model=model_key,
        )
        fallback_duration_ms = max(
            0,
            int(round((time.perf_counter() - fallback_started) * 1000)),
        )
        retry_text = str(review_response_retry or "")
        retry_result = _analyze_review_retry_payload(
            retry_text,
            candidate_cases=llm_pool_cases,
            parse_json_fn=clean_and_parse_json_fn,
            normalize_json_structure_fn=normalize_json_structure_fn,
            reason_origin="fallback_llm",
            map_selection_fn=_map_review_selection_with_reasons,
        )
        retry_invalid_reason = str(retry_result.get("invalid_reason") or "")
        retry_debug_counts = _review_payload_debug_counts(retry_result)
        review_llm_runtime_debug["retry_attempts"].append(
            {
                "model": model_key,
                "duration_ms": int(fallback_duration_ms),
                "response_len": int(len(retry_text)),
                "is_error": bool(
                    bool(retry_invalid_reason)
                    and retry_invalid_reason in _RETRYABLE_RESPONSE_ERROR_REASONS
                ),
                "invalid_reason": retry_invalid_reason,
                "parsed_type": str(retry_result.get("parsed_type") or ""),
                "mapped_count": retry_debug_counts["mapped_count"],
                "dropped_reason_count": retry_debug_counts["dropped_reason_count"],
                "dropped_reason_payload_count": retry_debug_counts[
                    "dropped_reason_payload_count"
                ],
                "dropped_reason_unmapped_count": retry_debug_counts[
                    "dropped_reason_unmapped_count"
                ],
                "payload_has_selection_signal": bool(retry_result.get("payload_has_selection_signal")),
            }
        )
        if retry_invalid_reason:
            continue
        review_response_text = retry_text
        final_result = retry_result
        final_source = "fallback_llm"
        review_llm_runtime_debug["retry_model"] = model_key
        break

    review_llm_runtime_debug["retry_response_len"] = int(len(review_response_text))
    return final_source, final_result, review_response_text


def _finalize_retry_debug(
    *,
    final_result: dict[str, Any],
    review_llm_runtime_debug: dict[str, Any],
) -> None:
    review_llm_runtime_debug["retry_parse_success"] = bool(
        review_llm_runtime_debug.get("retry_invoked")
        and bool(final_result.get("parse_success"))
        and not bool(final_result.get("invalid_reason"))
    )
    review_llm_runtime_debug["retry_mapped_count"] = int(
        len(final_result.get("mapped") or []) if bool(review_llm_runtime_debug.get("retry_invoked")) else 0
    )
    review_llm_runtime_debug["retry_payload_has_selection_signal"] = bool(
        final_result.get("payload_has_selection_signal") if bool(review_llm_runtime_debug.get("retry_invoked")) else False
    )
    if bool(review_llm_runtime_debug.get("retry_invoked")):
        final_debug_counts = _review_payload_debug_counts(final_result)
        review_llm_runtime_debug["retry_dropped_reason_count"] = final_debug_counts["dropped_reason_count"]
        review_llm_runtime_debug["retry_dropped_reason_payload_count"] = final_debug_counts[
            "dropped_reason_payload_count"
        ]
        review_llm_runtime_debug["retry_dropped_reason_unmapped_count"] = final_debug_counts[
            "dropped_reason_unmapped_count"
        ]


def _record_final_overlap_debug(
    *,
    llm_pool_cases: list[dict[str, Any]],
    final_result: dict[str, Any],
    review_llm_drop_reason_raw_map: dict[str, str],
    review_llm_runtime_debug: dict[str, Any],
) -> None:
    final_mapped_signatures = {
        str(signature or "").strip()
        for signature in set(final_result.get("mapped_signatures") or set())
        if str(signature or "").strip()
    }
    final_dropped_signatures = {
        str(signature or "").strip()
        for signature in review_llm_drop_reason_raw_map.keys()
        if str(signature or "").strip()
    }
    selected_and_dropped_overlap = final_mapped_signatures & final_dropped_signatures
    signature_to_case_id = {
        _signature(item): _review_case_id(item)
        for item in llm_pool_cases
        if isinstance(item, dict) and _signature(item)
    }
    overlap_case_ids = [
        str(signature_to_case_id.get(signature) or "")
        for signature in selected_and_dropped_overlap
    ]
    overlap_case_ids = [case_id for case_id in overlap_case_ids if case_id]
    review_llm_runtime_debug["final_selected_and_dropped_overlap_count"] = int(
        len(selected_and_dropped_overlap)
    )
    review_llm_runtime_debug["final_selected_and_dropped_overlap_case_ids"] = overlap_case_ids[:20]
    review_llm_runtime_debug["final_payload_consistent"] = bool(
        len(selected_and_dropped_overlap) == 0
    )
