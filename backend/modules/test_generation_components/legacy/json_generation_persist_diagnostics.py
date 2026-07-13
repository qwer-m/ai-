from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from .json_generation_dependencies import LogEntry
from .json_generation_post_persist_payloads import (
    EMPTY_GENERATED_RESULT_MESSAGE,
    PostPersistGenerationDiagnosticPayloads,
    _build_default_convergence,
    _build_default_generation_summary,
    _build_default_review_decision_summary,
    _dict_rows,
    _final_case_count,
    _generation_mode,
    _normalize_judge_rows,
    build_post_persist_generation_diagnostic_payloads,
)


@dataclass(frozen=True)
class PrePersistGenerationDiagnosticResult:
    gen_diag_payload: dict[str, Any]
    compression_event_payload: dict[str, Any]


def emit_pre_persist_generation_diagnostics(
    *,
    db: Any,
    client: Any,
    project_id: int,
    user_id: int | None,
    request_id: str,
    normalized_generation_mode: str | None,
    multi_pass: bool,
    stage_logs: Iterable[Any] | None,
    coverage_check_payload: Mapping[str, Any] | None,
    feedback_control_diag_payload: Mapping[str, Any] | None,
    judge_summary_payload: Mapping[str, Any] | None,
    memory_diag: Mapping[str, Any] | None,
    system_prompt: str,
    requirement: str,
    result: Any,
    context_result: Mapping[str, Any] | None,
    doc_type: str,
    compress: bool,
    expected_count: int,
    kb_context: str,
    count_unique_test_cases_fn: Callable[[Any], Any],
    build_context_compression_diagnostics_fn: Callable[..., dict[str, Any]],
) -> PrePersistGenerationDiagnosticResult:
    mode = _generation_mode(normalized_generation_mode, multi_pass)
    for stage_log in stage_logs or []:
        if not isinstance(stage_log, Mapping):
            continue
        payload = dict(stage_log)
        payload.update(
            {
                "request_id": request_id,
                "multi_pass": bool(multi_pass),
                "generation_mode": mode,
            }
        )
        _add_gen_diag_log(db=db, project_id=project_id, user_id=user_id, payload=payload)

    if coverage_check_payload:
        coverage_payload = dict(coverage_check_payload)
        coverage_payload.update(
            {
                "request_id": request_id,
                "multi_pass": bool(multi_pass),
                "generation_mode": mode,
            }
        )
        _add_gen_diag_log(db=db, project_id=project_id, user_id=user_id, payload=coverage_payload)

    if feedback_control_diag_payload:
        control_diag_payload = {
            "kind": "feedback_control_state",
            **dict(feedback_control_diag_payload),
            "request_id": request_id,
            "multi_pass": bool(multi_pass),
            "generation_mode": mode,
        }
        _add_gen_diag_log(db=db, project_id=project_id, user_id=user_id, payload=control_diag_payload)

    if judge_summary_payload:
        judge_diag_payload = {
            "kind": "judge_summary",
            **dict(judge_summary_payload),
            "request_id": request_id,
            "multi_pass": bool(multi_pass),
            "generation_mode": mode,
        }
        _add_gen_diag_log(db=db, project_id=project_id, user_id=user_id, payload=judge_diag_payload)

    if memory_diag:
        memory_diag_payload = {
            "kind": "memory_fabric_diag",
            **dict(memory_diag),
            "request_id": request_id,
            "multi_pass": bool(multi_pass),
            "generation_mode": mode,
        }
        _add_gen_diag_log(db=db, project_id=project_id, user_id=user_id, payload=memory_diag_payload)

    full_input = (system_prompt or "") + requirement
    actual_model = client.select_model(full_input, task_type="generation")
    compression_diag_payload = build_context_compression_diagnostics_fn(
        context_result=context_result if isinstance(context_result, Mapping) else {},
    )
    gen_diag_payload = {
        "kind": "gen_diag",
        "mode": "json",
        "doc_type": doc_type,
        "compress": bool(compress),
        "expected_count": int(expected_count or 0),
        "generated_count": int(count_unique_test_cases_fn(result)) if isinstance(result, list) else 0,
        "content_length": len(requirement or ""),
        "kb_length": len(kb_context or ""),
        "model": actual_model,
        "max_tokens": client.max_tokens,
        "request_id": request_id,
        "multi_pass": bool(multi_pass),
        "generation_mode": mode,
        "context_compression_ratio": compression_diag_payload.get("compression_ratio"),
        "context_retained_chunk_count": compression_diag_payload.get("retained_chunk_count"),
        "context_relevance_distribution": compression_diag_payload.get("relevance_distribution") or {},
    }
    _add_gen_diag_log(db=db, project_id=project_id, user_id=user_id, payload=gen_diag_payload)

    compression_event_payload = {
        "kind": "generation_context_compression",
        **compression_diag_payload,
        "request_id": request_id,
        "multi_pass": bool(multi_pass),
        "generation_mode": mode,
    }
    _add_gen_diag_log(db=db, project_id=project_id, user_id=user_id, payload=compression_event_payload)
    db.commit()
    return PrePersistGenerationDiagnosticResult(
        gen_diag_payload=gen_diag_payload,
        compression_event_payload=compression_event_payload,
    )


def _add_gen_diag_log(
    *,
    db: Any,
    project_id: int,
    user_id: int | None,
    payload: Mapping[str, Any],
) -> None:
    db.add(
        LogEntry(
            project_id=project_id,
            user_id=user_id,
            log_type="system",
            message=f"GEN_DIAG:{json.dumps(dict(payload), ensure_ascii=False)}",
        )
    )


def emit_post_persist_generation_diagnostics(
    *,
    db: Any,
    project_id: int,
    user_id: int | None,
    request_id: str,
    generation_id: int,
    normalized_generation_mode: str | None,
    multi_pass: bool,
    resolved_current_biz: str,
    doc_type: str,
    compress: bool,
    expected_count: int,
    result: Any,
    candidate_total_before_judge: int,
    final_case_count: int,
    empty_result_guard_triggered: bool,
    empty_result_stage: str,
    gen_diag_payload: Mapping[str, Any] | None,
    compression_event_payload: Mapping[str, Any] | None,
    review_decision_summary_payload: Mapping[str, Any] | None,
    review_decision_table_payload: Iterable[Any] | None,
    convergence_payload: Mapping[str, Any] | None,
    generation_summary_payload: Mapping[str, Any] | None,
    judge_summary_payload: Mapping[str, Any] | None,
    judge_decision_table_payload: Iterable[Any] | None,
    count_unique_test_cases_fn: Callable[[Any], Any],
) -> PostPersistGenerationDiagnosticPayloads:
    generated_count = int(count_unique_test_cases_fn(result)) if isinstance(result, list) else 0
    payloads = build_post_persist_generation_diagnostic_payloads(
        project_id=project_id,
        request_id=request_id,
        generation_id=generation_id,
        normalized_generation_mode=normalized_generation_mode,
        multi_pass=multi_pass,
        resolved_current_biz=resolved_current_biz,
        doc_type=doc_type,
        compress=compress,
        expected_count=expected_count,
        result=result,
        generated_count=generated_count,
        candidate_total_before_judge=candidate_total_before_judge,
        final_case_count=final_case_count,
        empty_result_guard_triggered=empty_result_guard_triggered,
        empty_result_stage=empty_result_stage,
        gen_diag_payload=gen_diag_payload,
        compression_event_payload=compression_event_payload,
        review_decision_summary_payload=review_decision_summary_payload,
        review_decision_table_payload=review_decision_table_payload,
        convergence_payload=convergence_payload,
        generation_summary_payload=generation_summary_payload,
        judge_summary_payload=judge_summary_payload,
        judge_decision_table_payload=judge_decision_table_payload,
    )
    for payload in payloads.pre_judge_payloads:
        _add_gen_diag_log(db=db, project_id=project_id, user_id=user_id, payload=payload)
    db.commit()
    if payloads.judge_table_payload:
        _add_gen_diag_log(db=db, project_id=project_id, user_id=user_id, payload=payloads.judge_table_payload)
        db.commit()
    return payloads


def emit_post_persist_coverage_audit_diagnostics(
    *,
    db: Any,
    project_id: int,
    user_id: int | None,
    request_id: str,
    generation_id: int,
    normalized_generation_mode: str | None,
    multi_pass: bool,
    result: Any,
    requirement: str,
    kb_context: str,
    context_result: Mapping[str, Any] | None,
    expected_count: int,
    coverage_diagnostics_enabled: bool,
    build_coverage_diagnostics_fn: Callable[..., dict[str, Any]],
) -> None:
    if not isinstance(result, list):
        return

    case_items = [item for item in result if isinstance(item, dict)]
    if coverage_diagnostics_enabled:
        coverage_diag = build_coverage_diagnostics_fn(
            requirement=requirement,
            generated_cases=case_items,
            kb_context=kb_context,
            fusion_debug=(context_result or {}).get("fusion_debug") or {},
            expected_count=int(expected_count or 0),
        )
        db.add(
            LogEntry(
                project_id=project_id,
                user_id=user_id,
                log_type="system",
                message=f"GEN_COVERAGE_DIAG:{json.dumps(coverage_diag, ensure_ascii=False)}",
            )
        )
        db.commit()

    from ..coverage.core_flow_coverage_contract import audit_core_flow_coverage
    from core.settings.config import settings

    mode = _generation_mode(normalized_generation_mode, multi_pass)
    core_flow_audit = audit_core_flow_coverage(case_items)
    core_flow_audit_payload = {
        "kind": "core_flow_coverage",
        "request_id": request_id,
        "generation_id": int(generation_id),
        "multi_pass": bool(multi_pass),
        "generation_mode": mode,
        "core_flow_covered_count": int(core_flow_audit["core_flow_covered_count"]),
        "core_flow_required_count": int(core_flow_audit["core_flow_required_count"]),
        "core_flow_coverage_ratio": float(core_flow_audit["core_flow_coverage_ratio"]),
        "core_flow_coverage_passed": bool(core_flow_audit["core_flow_coverage_passed"]),
        "missing_core_flows": list(core_flow_audit["missing_core_flows"]),
        "false_positive_guard_notes": list(core_flow_audit["false_positive_guard_notes"]),
    }
    _add_gen_diag_log(db=db, project_id=project_id, user_id=user_id, payload=core_flow_audit_payload)
    db.commit()

    if not bool(getattr(settings, "CORE_FLOW_BACKFILL_ENABLED", False)):
        return

    from ..coverage.core_flow_backfill import plan_core_flow_backfill

    backfill_plan = plan_core_flow_backfill(
        requirement_context=requirement,
        existing_cases=case_items,
        coverage_audit=core_flow_audit,
        max_backfill_cases=int(getattr(settings, "CORE_FLOW_BACKFILL_MAX_CANDIDATES", 12) or 12),
    )
    backfill_diag_payload = {
        "kind": "core_flow_backfill_dry_run",
        "request_id": request_id,
        "generation_id": int(generation_id),
        "multi_pass": bool(multi_pass),
        "generation_mode": mode,
        **{key: value for key, value in backfill_plan.items() if key != "backfill_plan"},
        "backfill_plan_summary": [
            {
                "flow_key": item["flow_key"],
                "flow_name": item["flow_name"],
                "suggested_priority": item["suggested_priority"],
                "target_case_count": item["target_case_count"],
            }
            for item in backfill_plan.get("backfill_plan") or []
        ],
    }
    _add_gen_diag_log(db=db, project_id=project_id, user_id=user_id, payload=backfill_diag_payload)
    db.commit()
