from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .case_access import case_text_field
from .result_postprocess_priority_semantics import apply_priority_semantics_to_cases
from .streaming_case_keys import case_signature
from .streaming_case_quality import filter_low_quality_cases_with_stats
from .streaming_case_source_metadata import (
    annotate_case_source_metadata,
    apply_case_source_metadata,
)
from .streaming_flow_conflicts import filter_cases_conflicting_with_confirmed_flow_facts
from .streaming_postprocess_utils import (
    _client_response_metadata,
    _clip_text,
    _dict_case_items,
    _json_for_prompt,
    _parsed_response_error_reason,
)
from .streaming_postprocess_utils import _dict_case_count, _flow_profile_with_scenario_policy
from .streaming_priority_semantics import apply_coverage_priority_semantics
from .streaming_review_mapping import case_review_brief

FINAL_SHORTFALL_SUPPLEMENT_BATCH_SIZE = 10
FINAL_SHORTFALL_SUPPLEMENT_MAX_BATCHES = 4


def _coverage_missing_rule_evidence(coverage: dict[str, Any], *, limit: int = 20) -> list[dict[str, Any]]:
    missing_rule_ids = {
        str(item).strip()
        for item in (coverage.get("missing_rules") or [])
        if str(item).strip()
    }
    missing_types = coverage.get("missing_types") if isinstance(coverage.get("missing_types"), dict) else {}
    for values in dict(missing_types or {}).values():
        if isinstance(values, list):
            missing_rule_ids.update(str(item).strip() for item in values if str(item).strip())
    if not missing_rule_ids:
        return []

    evidence: list[dict[str, Any]] = []
    diagnostics = coverage.get("rule_diagnostics") if isinstance(coverage.get("rule_diagnostics"), list) else []
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("rule_id") or "").strip()
        if not rule_id or rule_id not in missing_rule_ids:
            continue
        evidence.append(
            {
                "rule_id": rule_id,
                "rule_text": _clip_text(item.get("rule_text"), 280, strip=True),
                "missing_types": [
                    str(value).strip()
                    for value in (item.get("missing_types") or [])
                    if str(value).strip()
                ][:8],
                "covered": bool(item.get("covered")),
                "blocking": bool(item.get("blocking", True)),
                "confidence": str(item.get("confidence") or "").strip(),
                "source_type": str(item.get("source_type") or "").strip(),
            }
        )
        if len(evidence) >= int(limit):
            break

    seen = {str(item.get("rule_id") or "") for item in evidence}
    for rule_id in sorted(missing_rule_ids):
        if rule_id in seen:
            continue
        evidence.append(
            {
                "rule_id": rule_id,
                "rule_text": "",
                "missing_types": [
                    str(kind)
                    for kind, values in dict(missing_types or {}).items()
                    if isinstance(values, list) and rule_id in {str(value) for value in values}
                ][:8],
                "covered": False,
                "blocking": True,
                "confidence": "",
                "source_type": "",
            }
        )
        if len(evidence) >= int(limit):
            break
    return evidence


@dataclass(frozen=True)
class FinalShortfallSupplementResult:
    cases: list[dict[str, Any]]
    flow_governance_summary: dict[str, Any]
    filter_stats: dict[str, Any]
    conflict_drop_count: int
    applied: bool
    supplement_count: int
    reason: str
    debug: dict[str, Any]
    floor_recovery_applied: bool
    floor_recovered_count: int
    floor_recovery_reason: str


def should_attempt_final_shortfall_supplement(
    *,
    effective_generation_coverage_mode: str,
    expected_count_value: Any,
    final_target_floor_count: Any,
    append: bool,
    current_count: Any,
) -> bool:
    if not (
        effective_generation_coverage_mode == "full_functional_regression"
        or int(expected_count_value or 0) > 0
    ):
        return False
    if int(final_target_floor_count or 0) <= 0:
        return False
    return int(current_count or 0) < int(final_target_floor_count or 0)


def resolve_final_shortfall_supplement_size(
    *,
    current_count: Any,
    target_floor_count: Any,
    max_supplement_count: int = 30,
) -> dict[str, int]:
    shortfall = max(1, int(target_floor_count or 0) - int(current_count or 0))
    if shortfall <= 5:
        buffer = 3
    elif shortfall <= 20:
        buffer = 5
    else:
        buffer = max(5, int(round(float(shortfall) * 0.25)))

    return {
        "shortfall": int(shortfall),
        "buffer": int(buffer),
        "needed": min(int(max_supplement_count), int(shortfall + buffer)),
    }


def build_final_shortfall_supplement_prompt(
    *,
    requirement: Any,
    final_cases: Iterable[Any],
    current_count: Any,
    target_floor_count: Any,
    supplement_needed: Any,
    analyze_coverage_fn: Callable[[Any, list[dict[str, Any]]], dict[str, Any]],
    batch_index: int = 1,
    batch_count: int = 1,
    previous_supplement_cases: Iterable[Any] | None = None,
) -> str:
    final_case_items = _dict_case_items(final_cases)
    existing_case_brief = [
        case_review_brief(item, id_key="id", require_id=False)
        for item in final_case_items[:90]
    ]
    previous_supplement_brief = [
        case_review_brief(item, id_key="id", require_id=False)
        for item in _dict_case_items(previous_supplement_cases or [])[:30]
    ]
    supplement_coverage = analyze_coverage_fn(requirement, final_case_items)
    supplement_missing_rules = [
        str(item)
        for item in (supplement_coverage.get("missing_rules") or [])
        if str(item).strip()
    ][:20]
    supplement_missing_types_raw = supplement_coverage.get("missing_types")
    supplement_missing_types = {
        str(key): [str(item) for item in (value or []) if str(item).strip()][:20]
        for key, value in (
            dict(supplement_missing_types_raw or {}).items()
            if isinstance(supplement_missing_types_raw, dict)
            else []
        )
        if isinstance(value, list) and value
    }
    supplement_missing_rule_evidence = _coverage_missing_rule_evidence(supplement_coverage)
    existing_module_counts: dict[str, int] = {}
    for item in final_case_items:
        module_key = case_text_field(item, "test_module") or "unknown"
        existing_module_counts[module_key] = int(existing_module_counts.get(module_key) or 0) + 1

    payload = {
        "task": "final_shortfall_supplement",
        "batch_index": int(batch_index or 1),
        "batch_count": int(batch_count or 1),
        "current_count": int(current_count or 0),
        "target_floor_count": int(target_floor_count or 0),
        "supplement_needed": int(supplement_needed or 0),
        "requirement_excerpt": _clip_text(requirement, 6000, strip=True),
        "missing_rules": supplement_missing_rules,
        "missing_types": supplement_missing_types,
        "missing_rule_evidence": supplement_missing_rule_evidence,
        "existing_module_counts": existing_module_counts,
        "existing_final_cases_to_avoid": existing_case_brief,
        "previous_supplement_cases_to_avoid": previous_supplement_brief,
    }

    return f"""
FINAL_SHORTFALL_SUPPLEMENT:
- Batch {int(batch_index or 1)}/{int(batch_count or 1)}. The current final set has {current_count} cases, below the final floor {int(target_floor_count or 0)}.
- Generate 1 to {int(supplement_needed or 0)} additional high-value, non-duplicate test cases for this batch.
- Focus only on the current requirement excerpt and the missing coverage evidence below.
- If missing_rule_evidence contains rule_text, generated cases must directly cover those rule_text facts and missing_types before adding generic count filler.
- Prefer under-covered business modules, independent functional paths, boundaries, exceptions, and cross-module state synchronization.
- Do not add display-only, copy/toast-only, sorting-only, thumbnail-only, or popup-only cases unless they close a blocking business flow.
- Do not include legacy behavior that conflicts with confirmed current requirements.
- P0 only for blocking main-path closure; otherwise use P1/P2.
- Return ONLY a strict JSON object: {{"cases":[...]}}.
- Each case must include fields: id, description, test_module, preconditions, steps, test_input, expected_result, priority.

INPUT_JSON:
{_json_for_prompt(payload, limit=18000, compact=True)}
"""


def _supplement_system_prompt() -> str:
    return (
        "You are a test-case shortfall supplement agent. "
        "Use only the provided INPUT_JSON. Return only a strict JSON object with a cases array. "
        "Do not include markdown, commentary, or analysis."
    )


def _extract_case_list_payload(parsed_payload: Any) -> tuple[list[dict[str, Any]], str]:
    if isinstance(parsed_payload, list):
        return _dict_case_items(parsed_payload), "list"
    if isinstance(parsed_payload, dict):
        for key in ("cases", "test_cases", "items", "results"):
            value = parsed_payload.get(key)
            if isinstance(value, list):
                return _dict_case_items(value), f"dict.{key}"
    return [], type(parsed_payload).__name__


def _compact_response_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "model",
        "cached",
        "wire_api",
        "http_status",
        "url_path",
        "finish_reason",
        "max_tokens",
        "json_response",
        "content_len",
        "reasoning_len",
        "input_tokens_estimated",
        "output_tokens_estimated",
        "exception_type",
        "json_compat_fallback",
        "reasoning_effort_rejected_status",
        "response_format",
        "reasoning_effort",
        "thinking",
    )
    compact = {key: metadata.get(key) for key in keys if key in metadata}
    if metadata.get("error_preview"):
        compact["error_preview"] = _clip_text(metadata.get("error_preview"), 160)
    if metadata.get("exception"):
        compact["exception"] = _clip_text(metadata.get("exception"), 160)
    return compact


def _resolve_batch_plan(*, supplement_needed: int, batch_size: int = FINAL_SHORTFALL_SUPPLEMENT_BATCH_SIZE) -> list[int]:
    needed = max(1, int(supplement_needed or 1))
    size = max(1, int(batch_size or FINAL_SHORTFALL_SUPPLEMENT_BATCH_SIZE))
    batch_count = min(FINAL_SHORTFALL_SUPPLEMENT_MAX_BATCHES, int(math.ceil(float(needed) / float(size))))
    plan: list[int] = []
    remaining = needed
    for _idx in range(batch_count):
        value = min(size, remaining)
        if value <= 0:
            break
        plan.append(int(value))
        remaining -= value
    return plan or [min(size, needed)]


def run_final_shortfall_supplement(
    *,
    client: Any,
    db: Any,
    requirement: str,
    supplement_prompt: str = "",
    current_shortfall_count: int,
    target_floor_count: int,
    supplement_needed: int,
    parsed_result: list[dict[str, Any]],
    kb_context: str,
    fact_profile: dict[str, Any],
    flow_project_profile: dict[str, Any],
    effective_generation_coverage_mode: str,
    start_id: int,
    final_floor_recovered_count: int,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    analyze_coverage_fn: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    govern_cases_by_flow_structure_fn: Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]],
) -> FinalShortfallSupplementResult:
    result_cases = _dict_case_items(parsed_result)
    result_flow_summary: dict[str, Any] = {}
    filter_stats: dict[str, Any] = {}
    conflict_drop_count = 0
    target_count = int(target_floor_count or 0)
    supplement_limit = max(1, int(supplement_needed or 0))
    batch_plan = _resolve_batch_plan(supplement_needed=supplement_limit)
    debug: dict[str, Any] = {
        "target_floor_count": int(target_count),
        "start_count": int(current_shortfall_count or 0),
        "supplement_needed": int(supplement_limit),
        "batch_size": int(FINAL_SHORTFALL_SUPPLEMENT_BATCH_SIZE),
        "batch_plan": list(batch_plan),
        "batches": [],
    }

    all_supplement_cases: list[dict[str, Any]] = []
    existing_sigs = {case_signature(item) for item in result_cases if isinstance(item, dict)}
    for batch_index, batch_goal in enumerate(batch_plan, start=1):
        if target_count > 0 and int(current_shortfall_count or 0) + len(all_supplement_cases) >= target_count:
            break
        if len(all_supplement_cases) >= supplement_limit:
            break

        prompt = supplement_prompt if batch_index == 1 and supplement_prompt else build_final_shortfall_supplement_prompt(
            requirement=requirement,
            final_cases=[*result_cases, *all_supplement_cases],
            current_count=int(current_shortfall_count or 0) + len(all_supplement_cases),
            target_floor_count=target_count,
            supplement_needed=batch_goal,
            analyze_coverage_fn=analyze_coverage_fn,
            batch_index=batch_index,
            batch_count=len(batch_plan),
            previous_supplement_cases=all_supplement_cases,
        )
        max_tokens = max(2500, min(7000, int(batch_goal or 1) * 700))
        supplement_raw = client.generate_response(
            prompt,
            _supplement_system_prompt(),
            db=db,
            max_tokens=max_tokens,
            task_type="generation",
        )
        metadata = _compact_response_metadata(_client_response_metadata(client))
        raw_text = str(supplement_raw or "")
        try:
            parsed_payload = clean_and_parse_json_fn(raw_text)
        except Exception as parse_exc:
            parsed_payload = {"error": "parse_exception", "exception": str(parse_exc)}
        normalized_payload = normalize_json_structure_fn(parsed_payload)
        parsed_cases, parsed_source = _extract_case_list_payload(normalized_payload)
        if not parsed_cases and normalized_payload is not parsed_payload:
            parsed_cases, parsed_source = _extract_case_list_payload(parsed_payload)
        if parsed_cases:
            parsed_cases = _dict_case_items(normalize_json_structure_fn(parsed_cases))
            parsed_cases = annotate_case_source_metadata(
                parsed_cases,
                source_stage="final_shortfall_supplement",
                start_index=len(all_supplement_cases) + 1,
                batch_index=batch_index,
                set_candidate_index=False,
            )
        error_reason = _parsed_response_error_reason(raw_text, normalized_payload)
        if not error_reason and not parsed_cases:
            error_reason = "empty_case_list"

        batch_debug = {
            "batch_index": int(batch_index),
            "batch_goal": int(batch_goal),
            "prompt_chars": int(len(prompt or "")),
            "max_tokens": int(max_tokens),
            "response_chars": int(len(raw_text)),
            "response_preview": _clip_text(raw_text, 240, strip=True),
            "starts_error": bool(raw_text.strip().startswith(("Error:", "Exception"))),
            "parsed_source": str(parsed_source),
            "parsed_count": int(len(parsed_cases)),
            "error_reason": str(error_reason or ""),
            "metadata": metadata,
        }
        debug["batches"].append(batch_debug)
        if not parsed_cases:
            continue

        batch_cases = deduplicate_test_cases_fn(_dict_case_items(parsed_cases))
        batch_unique: list[dict[str, Any]] = []
        for item in batch_cases:
            sig = case_signature(item)
            if not sig or sig in existing_sigs:
                continue
            existing_sigs.add(sig)
            batch_unique.append(dict(item))
        all_supplement_cases.extend(batch_unique)
        batch_debug["unique_count"] = int(len(batch_unique))

    debug["raw_generated_count"] = int(len(all_supplement_cases))

    if not all_supplement_cases:
        reasons = [
            str(item.get("error_reason") or "")
            for item in debug.get("batches", [])
            if isinstance(item, dict) and str(item.get("error_reason") or "")
        ]
        reason = "supplement_empty_response"
        if reasons and all(reason_item == "error_response" for reason_item in reasons):
            reason = "supplement_error_response"
        elif reasons and all(reason_item == "empty_response" for reason_item in reasons):
            reason = "supplement_empty_response"
        elif reasons and any(reason_item == "empty_case_list" for reason_item in reasons):
            reason = "supplement_empty_case_list"
        return FinalShortfallSupplementResult(
            cases=result_cases,
            flow_governance_summary=result_flow_summary,
            filter_stats=filter_stats,
            conflict_drop_count=conflict_drop_count,
            applied=False,
            supplement_count=0,
            reason=reason,
            debug=debug,
            floor_recovery_applied=False,
            floor_recovered_count=int(final_floor_recovered_count or 0),
            floor_recovery_reason="",
        )

    supplement_cases = deduplicate_test_cases_fn(_dict_case_items(all_supplement_cases))
    supplement_cases = apply_case_source_metadata(
        supplement_cases,
        source_cases=all_supplement_cases,
    )
    supplement_cases = apply_priority_semantics_to_cases(
        _dict_case_items(supplement_cases),
        attach_debug=False,
    )
    supplement_cases, filter_stats = filter_low_quality_cases_with_stats(
        supplement_cases,
        requirement_text=requirement,
        analyze_coverage_fn=analyze_coverage_fn,
    )
    supplement_cases, conflict_drop_count = filter_cases_conflicting_with_confirmed_flow_facts(
        _dict_case_items(supplement_cases),
        requirement=str(requirement or ""),
        kb_context=str(kb_context or ""),
        fact_profile=fact_profile,
    )
    existing_sigs = {case_signature(item) for item in result_cases if isinstance(item, dict)}
    unique_supplement: list[dict[str, Any]] = []
    for item in supplement_cases:
        sig = case_signature(item)
        if not sig or sig in existing_sigs:
            continue
        existing_sigs.add(sig)
        unique_supplement.append(dict(item))

    if not unique_supplement:
        return FinalShortfallSupplementResult(
            cases=result_cases,
            flow_governance_summary=result_flow_summary,
            filter_stats=filter_stats,
            conflict_drop_count=int(conflict_drop_count or 0),
            applied=False,
            supplement_count=0,
            reason="supplement_empty_after_filter",
            debug=debug,
            floor_recovery_applied=False,
            floor_recovered_count=int(final_floor_recovered_count or 0),
            floor_recovery_reason="",
        )

    merged_source_cases = [*result_cases, *unique_supplement]
    merged_shortfall = deduplicate_test_cases_fn(merged_source_cases)
    merged_shortfall = apply_case_source_metadata(
        merged_shortfall,
        source_cases=merged_source_cases,
    )
    merged_shortfall = apply_coverage_priority_semantics(
        requirement,
        merged_shortfall,
        analyze_coverage_fn=analyze_coverage_fn,
    )
    relaxed_flow_profile = _flow_profile_with_scenario_policy(
        flow_project_profile,
        coverage_mode=str(effective_generation_coverage_mode or ""),
        disable_scenario_pruning=True,
        intent_duplicate_cap=1,
        relaxed_for_floor_backfill=True,
    )
    supplemented_result, supplemented_summary = govern_cases_by_flow_structure_fn(
        requirement,
        _dict_case_items(merged_shortfall),
        start_id=start_id,
        renumber_ids=True,
        max_per_scenario=2,
        project_profile=relaxed_flow_profile,
    )
    supplemented_result = apply_case_source_metadata(
        supplemented_result,
        source_cases=merged_shortfall,
    )
    if _dict_case_count(supplemented_result) <= current_shortfall_count:
        return FinalShortfallSupplementResult(
            cases=result_cases,
            flow_governance_summary=result_flow_summary,
            filter_stats=filter_stats,
            conflict_drop_count=int(conflict_drop_count or 0),
            applied=False,
            supplement_count=0,
            reason="supplement_pruned_or_duplicate",
            debug=debug,
            floor_recovery_applied=False,
            floor_recovered_count=int(final_floor_recovered_count or 0),
            floor_recovery_reason="",
        )

    supplemented_summary["relaxed_for_floor_backfill"] = True
    supplement_count = max(0, _dict_case_count(supplemented_result) - current_shortfall_count)
    return FinalShortfallSupplementResult(
        cases=supplemented_result,
        flow_governance_summary=supplemented_summary,
        filter_stats=filter_stats,
        conflict_drop_count=int(conflict_drop_count or 0),
        applied=True,
        supplement_count=supplement_count,
        reason="",
        debug=debug,
        floor_recovery_applied=True,
        floor_recovered_count=max(
            int(final_floor_recovered_count or 0),
            int(supplement_count or 0),
        ),
        floor_recovery_reason="final_shortfall_supplement_generated",
    )


__all__ = [
    "FinalShortfallSupplementResult",
    "build_final_shortfall_supplement_prompt",
    "resolve_final_shortfall_supplement_size",
    "run_final_shortfall_supplement",
    "should_attempt_final_shortfall_supplement",
]
