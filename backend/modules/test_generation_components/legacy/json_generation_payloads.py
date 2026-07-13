from typing import Any, Callable, Iterable, Mapping


_REQUIREMENT_SEMANTICS_FIELDS = (
    "confirmed_facts",
    "scoped_rules",
    "pending_items",
    "reuse_declarations",
    "hard_flow_constraints",
    "reuse_risks",
)


def _clean_payload_items(items: Any) -> list[str]:
    return [str(item).strip() for item in (items or []) if str(item).strip()]


def build_requirement_semantics_payload(prompt_context: Any) -> dict[str, list[str]]:
    return {
        field_name: _clean_payload_items(prompt_context.get(field_name))
        for field_name in _REQUIREMENT_SEMANTICS_FIELDS
    }


def _build_core_flow_coverage_payload(coverage_payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "covered_count": int(coverage_payload.get("core_flow_covered_count") or 0),
        "required_count": int(coverage_payload.get("core_flow_required_count") or 0),
        "coverage_ratio": float(coverage_payload.get("core_flow_coverage_ratio") or 0.0),
    }


def _count_final_case_payloads(result: Any) -> int:
    if not isinstance(result, list):
        return 0
    return int(len([item for item in result if isinstance(item, dict)]))


def build_core_flow_backfill_apply_summary_payload(
    *,
    request_id: Any,
    normalized_generation_mode: str | None,
    multi_pass: bool,
    backfill_enabled: Any,
    backfill_apply_to_final: Any,
    backfill_applied: Any,
    primary_case_count_before_backfill: Any,
    result: Any,
    core_flow_backfill_generation_result: Mapping[str, Any],
    core_flow_coverage_before_apply: Mapping[str, Any],
    core_flow_coverage_after_apply: Mapping[str, Any],
    core_flow_still_missing_after_apply: Iterable[Any],
    final_quality_gate_passed: Any,
    apply_skip_reason: Any,
) -> dict[str, Any]:
    return {
        "kind": "core_flow_backfill_apply_summary",
        "request_id": request_id,
        "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
        "backfill_enabled": bool(backfill_enabled),
        "backfill_apply_to_final": bool(backfill_apply_to_final),
        "backfill_applied": bool(backfill_applied),
        "primary_case_count": int(primary_case_count_before_backfill),
        "final_case_count": _count_final_case_payloads(result),
        "generated_backfill_candidate_count": int(
            len(core_flow_backfill_generation_result.get("generated_backfill_candidate_cases") or [])
        ),
        "accepted_backfill_candidate_count": int(
            len(core_flow_backfill_generation_result.get("accepted_backfill_cases") or [])
        ),
        "rejected_backfill_candidate_count": int(
            len(core_flow_backfill_generation_result.get("rejected_backfill_cases") or [])
        ),
        "accepted_for_preview_count": int(core_flow_backfill_generation_result.get("accepted_for_preview_count") or 0),
        "primary_retained_count": int(core_flow_backfill_generation_result.get("primary_retained_count") or 0),
        "primary_trimmed_count": int(core_flow_backfill_generation_result.get("primary_trimmed_count") or 0),
        "backfill_retained_count": int(core_flow_backfill_generation_result.get("backfill_retained_count") or 0),
        "backfill_trimmed_count": int(core_flow_backfill_generation_result.get("backfill_trimmed_count") or 0),
        "coverage_before": _build_core_flow_coverage_payload(core_flow_coverage_before_apply),
        "coverage_after": _build_core_flow_coverage_payload(core_flow_coverage_after_apply),
        "still_missing_core_flows": list(core_flow_still_missing_after_apply),
        "final_quality_gate_passed": bool(final_quality_gate_passed),
        "apply_skip_reason": str(apply_skip_reason or ""),
    }


def _normalize_case_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _normalize_case_steps(value: Any) -> str:
    if isinstance(value, list):
        return " | ".join(_normalize_case_text(item) for item in value if str(item or "").strip())
    return _normalize_case_text(value)


def _build_case_signature(
    case_payload: dict[str, Any],
    *,
    text_field_getter: Callable[[dict[str, Any], str], Any],
    steps_getter: Callable[[dict[str, Any]], Any],
) -> str:
    if not isinstance(case_payload, dict):
        return ""
    return "||".join(
        [
            _normalize_case_text(text_field_getter(case_payload, "test_module")),
            _normalize_case_text(text_field_getter(case_payload, "description")),
            _normalize_case_steps(steps_getter(case_payload)),
            _normalize_case_text(text_field_getter(case_payload, "test_input")),
            _normalize_case_text(text_field_getter(case_payload, "expected_result")),
        ]
    )
