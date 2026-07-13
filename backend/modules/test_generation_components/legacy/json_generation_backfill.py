from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from .json_generation_dependencies import LogEntry
from .json_generation_payloads import build_core_flow_backfill_apply_summary_payload


@dataclass(frozen=True)
class CoreFlowBackfillApplyResult:
    result: Any
    final_cases_after_judge: list[dict[str, Any]]
    final_case_count: int
    generation_summary_payload: dict[str, Any]
    core_flow_backfill_apply_summary_payload: dict[str, Any]
    core_flow_backfill_generation_result: dict[str, Any]
    error_payload: dict[str, Any] | None = None


def apply_core_flow_backfill_if_needed(
    *,
    db: Any,
    client: Any,
    settings: Any,
    requirement: str,
    result: Any,
    project_id: int,
    user_id: int | None,
    request_id: str,
    normalized_generation_mode: str,
    multi_pass: bool,
    generation_summary_payload: dict[str, Any],
    final_cases_after_judge: list[dict[str, Any]],
    final_case_count: int,
    normalize_missing_priority_final_cases_fn: Callable[..., Any],
    merge_contract_quality_gate_fn: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    summarize_persistable_case_contract_fn: Callable[[Any], dict[str, Any]],
) -> CoreFlowBackfillApplyResult:
    from ..coverage.core_flow_backfill import plan_core_flow_backfill
    from ..coverage.core_flow_backfill_generation import (
        generate_core_flow_backfill_candidates,
        summarize_case_quality_gate,
    )
    from ..coverage.core_flow_coverage_contract import audit_core_flow_coverage

    summary_payload = dict(generation_summary_payload or {})
    result_value = result
    final_cases = list(final_cases_after_judge or [])
    final_count = int(final_case_count or 0)
    core_flow_backfill_generation_result: dict[str, Any] = {}
    core_flow_backfill_apply_summary_payload: dict[str, Any] = {}

    if not isinstance(result_value, list):
        return CoreFlowBackfillApplyResult(
            result=result_value,
            final_cases_after_judge=final_cases,
            final_case_count=final_count,
            generation_summary_payload=summary_payload,
            core_flow_backfill_apply_summary_payload=core_flow_backfill_apply_summary_payload,
            core_flow_backfill_generation_result=core_flow_backfill_generation_result,
        )

    backfill_enabled = bool(getattr(settings, "CORE_FLOW_BACKFILL_ENABLED", False))
    backfill_apply_to_final = bool(getattr(settings, "CORE_FLOW_BACKFILL_APPLY_TO_FINAL", False))
    backfill_max_candidates = int(getattr(settings, "CORE_FLOW_BACKFILL_MAX_CANDIDATES", 12) or 12)
    backfill_min_final_cases = int(getattr(settings, "CORE_FLOW_BACKFILL_MIN_FINAL_CASES", 12) or 12)
    backfill_max_final_cases = int(getattr(settings, "CORE_FLOW_BACKFILL_MAX_FINAL_CASES", 18) or 18)
    backfill_min_coverage_ratio = float(getattr(settings, "CORE_FLOW_BACKFILL_MIN_COVERAGE_RATIO", 0.8) or 0.8)
    backfill_applied = False
    apply_skip_reason = ""
    final_quality_gate_passed = True
    primary_cases = [item for item in result_value if isinstance(item, dict)]
    primary_case_count_before_backfill = int(len(primary_cases))
    core_flow_coverage_before_apply = audit_core_flow_coverage(primary_cases)
    core_flow_coverage_after_apply = dict(core_flow_coverage_before_apply)
    core_flow_still_missing_after_apply = list(core_flow_coverage_before_apply.get("missing_core_flows") or [])
    merged_quality_gate = {
        "passed": True,
        "failed_checks": [],
        "priority_final_null_count": 0,
        "invalid_priority_final_count": 0,
        "invalid_priority_final_case_ids": [],
        "non_assertable_expected_result_count": 0,
        "truncated_text_count": 0,
        "non_assertable_case_ids": [],
        "truncated_case_ids": [],
    }
    merged_coverage_ratio = float(core_flow_coverage_after_apply.get("core_flow_coverage_ratio") or 0.0)

    if not backfill_enabled:
        apply_skip_reason = "backfill_feature_disabled"
    else:
        backfill_plan = plan_core_flow_backfill(
            requirement_context=requirement,
            existing_cases=primary_cases,
            coverage_audit=core_flow_coverage_before_apply,
            max_backfill_cases=backfill_max_candidates,
        )
        backfill_plan["project_id"] = int(project_id)
        backfill_plan["user_id"] = int(user_id or 0)
        backfill_plan["request_id"] = request_id
        backfill_plan["generation_mode"] = normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass")
        core_flow_backfill_generation_result = generate_core_flow_backfill_candidates(
            requirement_context=requirement,
            existing_cases=primary_cases,
            backfill_plan=backfill_plan,
            db=db,
            llm_client=client,
            max_candidates=backfill_max_candidates,
            preview_min_total=backfill_min_final_cases,
            preview_max_total=backfill_max_final_cases,
        )
        merged_preview_cases = [
            item
            for item in (core_flow_backfill_generation_result.get("merged_preview_cases") or [])
            if isinstance(item, dict)
        ]
        merged_preview_cases = normalize_missing_priority_final_cases_fn(
            merged_preview_cases,
            requirement_text=requirement,
        )
        merged_quality_gate = summarize_case_quality_gate(merged_preview_cases)
        merged_quality_gate = merge_contract_quality_gate_fn(
            merged_quality_gate,
            summarize_persistable_case_contract_fn(merged_preview_cases),
        )
        core_flow_coverage_after_apply = audit_core_flow_coverage(merged_preview_cases)
        core_flow_still_missing_after_apply = list(core_flow_coverage_after_apply.get("missing_core_flows") or [])
        merged_coverage_ratio = float(core_flow_coverage_after_apply.get("core_flow_coverage_ratio") or 0.0)

        if not backfill_apply_to_final:
            apply_skip_reason = "apply_to_final_disabled_shadow_only"
        elif not merged_preview_cases:
            final_quality_gate_passed = False
            apply_skip_reason = "merged_preview_empty"
        elif len(merged_preview_cases) < backfill_min_final_cases or len(merged_preview_cases) > backfill_max_final_cases:
            final_quality_gate_passed = False
            apply_skip_reason = "merged_preview_case_count_out_of_range"
        elif not bool(merged_quality_gate.get("passed")):
            final_quality_gate_passed = False
            apply_skip_reason = "merged_result_quality_gate_failed"
        elif merged_coverage_ratio < backfill_min_coverage_ratio:
            final_quality_gate_passed = False
            apply_skip_reason = "merged_result_coverage_below_threshold"
        else:
            result_value = merged_preview_cases
            final_cases = [item for item in result_value if isinstance(item, dict)]
            final_count = int(len(final_cases))
            backfill_applied = True
            apply_skip_reason = ""

        if backfill_apply_to_final and not final_quality_gate_passed:
            core_flow_backfill_apply_summary_payload = build_core_flow_backfill_apply_summary_payload(
                request_id=request_id,
                normalized_generation_mode=normalized_generation_mode,
                multi_pass=multi_pass,
                backfill_enabled=backfill_enabled,
                backfill_apply_to_final=backfill_apply_to_final,
                backfill_applied=backfill_applied,
                primary_case_count_before_backfill=primary_case_count_before_backfill,
                result=result_value,
                core_flow_backfill_generation_result=core_flow_backfill_generation_result,
                core_flow_coverage_before_apply=core_flow_coverage_before_apply,
                core_flow_coverage_after_apply=core_flow_coverage_after_apply,
                core_flow_still_missing_after_apply=core_flow_still_missing_after_apply,
                final_quality_gate_passed=final_quality_gate_passed,
                apply_skip_reason=apply_skip_reason,
            )
            db.add(
                LogEntry(
                    project_id=project_id,
                    user_id=user_id,
                    log_type="system",
                    message=f"GEN_DIAG:{json.dumps(core_flow_backfill_apply_summary_payload, ensure_ascii=False)}",
                )
            )
            quality_gate_payload = {
                "kind": "generation_quality_gate",
                "request_id": request_id,
                "multi_pass": bool(multi_pass),
                "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                "error_code": "LOW_QUALITY_GENERATED_CASES",
                "final_status": "quality_gate_failed",
                "quality_gate_failed": True,
                **{key: value for key, value in merged_quality_gate.items() if key != "passed"},
                "apply_skip_reason": apply_skip_reason,
                "core_flow_coverage_ratio": float(core_flow_coverage_after_apply.get("core_flow_coverage_ratio") or 0.0),
                "core_flow_min_required_ratio": float(backfill_min_coverage_ratio),
            }
            db.add(
                LogEntry(
                    project_id=project_id,
                    user_id=user_id,
                    log_type="system",
                    message=f"GEN_DIAG:{json.dumps(quality_gate_payload, ensure_ascii=False)}",
                )
            )
            db.commit()
            return CoreFlowBackfillApplyResult(
                result=result_value,
                final_cases_after_judge=final_cases,
                final_case_count=final_count,
                generation_summary_payload=summary_payload,
                core_flow_backfill_apply_summary_payload=core_flow_backfill_apply_summary_payload,
                core_flow_backfill_generation_result=core_flow_backfill_generation_result,
                error_payload={
                    "error": "LOW_QUALITY_GENERATED_CASES",
                    "error_code": "LOW_QUALITY_GENERATED_CASES",
                    "error_message": "merged backfill result failed quality gate or coverage threshold",
                    "final_status": "quality_gate_failed",
                    "quality_gate_failed": True,
                    **{key: value for key, value in merged_quality_gate.items() if key != "passed"},
                    "apply_skip_reason": apply_skip_reason,
                    "core_flow_coverage_ratio": float(core_flow_coverage_after_apply.get("core_flow_coverage_ratio") or 0.0),
                    "core_flow_min_required_ratio": float(backfill_min_coverage_ratio),
                },
            )

    core_flow_backfill_apply_summary_payload = build_core_flow_backfill_apply_summary_payload(
        request_id=request_id,
        normalized_generation_mode=normalized_generation_mode,
        multi_pass=multi_pass,
        backfill_enabled=backfill_enabled,
        backfill_apply_to_final=backfill_apply_to_final,
        backfill_applied=backfill_applied,
        primary_case_count_before_backfill=primary_case_count_before_backfill,
        result=result_value,
        core_flow_backfill_generation_result=core_flow_backfill_generation_result,
        core_flow_coverage_before_apply=core_flow_coverage_before_apply,
        core_flow_coverage_after_apply=core_flow_coverage_after_apply,
        core_flow_still_missing_after_apply=core_flow_still_missing_after_apply,
        final_quality_gate_passed=final_quality_gate_passed,
        apply_skip_reason=apply_skip_reason,
    )
    db.add(
        LogEntry(
            project_id=project_id,
            user_id=user_id,
            log_type="system",
            message=f"GEN_DIAG:{json.dumps(core_flow_backfill_apply_summary_payload, ensure_ascii=False)}",
        )
    )
    db.commit()

    summary_payload["core_flow_backfill_enabled"] = bool(backfill_enabled)
    summary_payload["core_flow_backfill_applied"] = bool(backfill_applied)
    summary_payload["primary_case_count_before_backfill"] = int(primary_case_count_before_backfill)
    summary_payload["final_count"] = int(len([item for item in result_value if isinstance(item, dict)])) if isinstance(result_value, list) else 0
    summary_payload["final_case_count_after_backfill"] = int(len([item for item in result_value if isinstance(item, dict)])) if isinstance(result_value, list) else 0
    summary_payload["core_flow_coverage_before"] = float(core_flow_coverage_before_apply.get("core_flow_coverage_ratio") or 0.0)
    summary_payload["core_flow_coverage_after"] = float(core_flow_coverage_after_apply.get("core_flow_coverage_ratio") or 0.0)
    summary_payload["core_flow_still_missing_count"] = int(len(core_flow_still_missing_after_apply))

    return CoreFlowBackfillApplyResult(
        result=result_value,
        final_cases_after_judge=final_cases,
        final_case_count=final_count,
        generation_summary_payload=summary_payload,
        core_flow_backfill_apply_summary_payload=core_flow_backfill_apply_summary_payload,
        core_flow_backfill_generation_result=core_flow_backfill_generation_result,
    )


__all__ = [
    "CoreFlowBackfillApplyResult",
    "apply_core_flow_backfill_if_needed",
]
