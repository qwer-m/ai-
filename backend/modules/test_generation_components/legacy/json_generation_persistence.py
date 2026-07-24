from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class JsonPersistenceResult:
    result: Any
    error_payload: dict[str, Any] | None = None


def _project_public_result(
    result: Any,
    *,
    project_persistable_cases_fn: Callable[..., Any],
) -> Any:
    """统一 JSON 返回边界，避免失败路径泄漏内部用例字段。"""
    if not isinstance(result, list):
        return result
    projected = project_persistable_cases_fn(result)
    return projected if isinstance(projected, list) else []


def _workflow_blueprint_source_from_declaration(
    workflow_blueprints: list[dict[str, Any]],
) -> str:
    blueprint = next(
        (
            item
            for item in workflow_blueprints
            if isinstance(item, dict) and isinstance(item.get("steps"), list)
        ),
        {},
    )
    if not blueprint or blueprint.get("closure_declaration_complete") is not True:
        return ""
    repository_source = str(blueprint.get("repository_source") or blueprint.get("source") or "").strip()
    source_type = str(blueprint.get("source_type") or "").strip()
    if repository_source == "current_requirement_blueprint" or source_type == "current_requirement_extracted":
        return "current_requirement_blueprint"
    if blueprint.get("trusted") is True and repository_source == "workflow_blueprint_repository":
        return "feedback_control_state"
    return ""


def _resolved_execution_plan_payload(
    review_decision_summary_payload: dict[str, Any],
    workflow_blueprints: list[dict[str, Any]],
) -> dict[str, Any]:
    execution_plan = dict(review_decision_summary_payload.get("execution_plan") or {})
    if execution_plan:
        return execution_plan
    source = _workflow_blueprint_source_from_declaration(workflow_blueprints)
    return {"workflow_blueprint_source": source} if source else {}


def run_json_persistence_flow(
    *,
    db: Any,
    active_db_session: bool,
    client: Any,
    result: Any,
    raw_response_payload: Any,
    original_requirement: str,
    requirement: str,
    project_id: int,
    user_id: int | None,
    request_id: str,
    normalized_generation_mode: str,
    multi_pass: bool,
    resolved_current_biz: str,
    doc_type: str,
    compress: bool,
    expected_count: int,
    kb_context: str,
    context_result: dict[str, Any],
    stage_logs: list[dict[str, Any]],
    coverage_check_payload: dict[str, Any] | None,
    feedback_control_diag_payload: dict[str, Any],
    judge_summary_payload: dict[str, Any],
    judge_decision_table_payload: list[dict[str, Any]],
    memory_diag: dict[str, Any],
    system_prompt: str,
    generation_summary_payload: dict[str, Any],
    feedback_control_state: dict[str, Any],
    final_cases_after_judge: list[dict[str, Any]],
    final_case_count: int,
    review_decision_summary_payload: dict[str, Any],
    review_decision_table_payload: list[dict[str, Any]],
    convergence_payload: dict[str, Any],
    candidate_total_before_judge: int,
    empty_result_guard_triggered: bool,
    empty_result_stage: str,
    gen_diag_payload: dict[str, Any],
    compression_event_payload: dict[str, Any],
    log_entry_cls: Callable[..., Any],
    test_generation_cls: Callable[..., Any],
    count_unique_test_cases_fn: Callable[[Any], Any],
    build_context_compression_diagnostics_fn: Callable[..., dict[str, Any]],
    emit_pre_persist_generation_diagnostics_fn: Callable[..., Any],
    emit_post_persist_generation_diagnostics_fn: Callable[..., Any],
    emit_post_persist_coverage_audit_diagnostics_fn: Callable[..., Any],
    normalize_missing_priority_final_cases_fn: Callable[..., Any],
    merge_contract_quality_gate_fn: Callable[..., dict[str, Any]],
    summarize_persistable_case_contract_fn: Callable[..., dict[str, Any]],
    summarize_persistence_case_quality_gate_fn: Callable[..., dict[str, Any]],
    project_persistable_cases_fn: Callable[..., Any],
    evaluate_persistence_gate_fn: Callable[..., dict[str, Any]],
    build_persistence_gate_diagnostic_fn: Callable[..., dict[str, Any]],
    build_coverage_diagnostics_fn: Callable[..., dict[str, Any]],
    coverage_diagnostics_enabled: bool,
) -> JsonPersistenceResult:
    if not db:
        return JsonPersistenceResult(
            result=_project_public_result(
                result,
                project_persistable_cases_fn=project_persistable_cases_fn,
            )
        )

    result_value = result
    gen_diag_payload_value = dict(gen_diag_payload or {})
    compression_event_payload_value = dict(compression_event_payload or {})
    final_cases = list(final_cases_after_judge or [])
    final_count = int(final_case_count or 0)
    generation_summary = dict(generation_summary_payload or {})

    try:
        if active_db_session:
            pre_persist_diag_result = emit_pre_persist_generation_diagnostics_fn(
                db=db,
                client=client,
                project_id=project_id,
                user_id=user_id,
                request_id=request_id,
                normalized_generation_mode=normalized_generation_mode,
                multi_pass=multi_pass,
                stage_logs=stage_logs,
                coverage_check_payload=coverage_check_payload,
                feedback_control_diag_payload=feedback_control_diag_payload,
                judge_summary_payload=judge_summary_payload,
                memory_diag=memory_diag,
                system_prompt=system_prompt,
                requirement=requirement,
                result=result_value,
                context_result=context_result if isinstance(context_result, dict) else {},
                doc_type=doc_type,
                compress=compress,
                expected_count=expected_count,
                kb_context=kb_context,
                count_unique_test_cases_fn=count_unique_test_cases_fn,
                build_context_compression_diagnostics_fn=build_context_compression_diagnostics_fn,
            )
            gen_diag_payload_value = pre_persist_diag_result.gen_diag_payload
            compression_event_payload_value = pre_persist_diag_result.compression_event_payload

        from core.settings.config import settings
        from ..coverage.case_quality_gate import summarize_case_quality_gate

        if isinstance(result_value, list):
            result_value = normalize_missing_priority_final_cases_fn(result_value, requirement_text=requirement)
        quality_gate_result = summarize_case_quality_gate(result_value if isinstance(result_value, list) else [])
        quality_gate_result = merge_contract_quality_gate_fn(
            quality_gate_result,
            summarize_persistable_case_contract_fn(result_value),
        )
        quality_gate_result = summarize_persistence_case_quality_gate_fn(
            quality_gate_result,
            generation_summary=generation_summary,
            review_decision_summary=review_decision_summary_payload,
            judge_summary=judge_summary_payload,
            settings=settings,
        )

        workflow_blueprints = [
            dict(item)
            for item in (feedback_control_state.get("workflow_blueprints") or [])
            if isinstance(item, dict)
        ] if isinstance(feedback_control_state, dict) else []

        persistence_result = _evaluate_and_persist(
            db=db,
            client=client,
            result=result_value,
            raw_response_payload=raw_response_payload,
            original_requirement=original_requirement,
            requirement=requirement,
            project_id=project_id,
            user_id=user_id,
            request_id=request_id,
            normalized_generation_mode=normalized_generation_mode,
            multi_pass=multi_pass,
            resolved_current_biz=resolved_current_biz,
            doc_type=doc_type,
            compress=compress,
            expected_count=expected_count,
            kb_context=kb_context,
            context_result=context_result,
            generation_summary_payload=generation_summary,
            final_case_count=final_count,
            review_decision_summary_payload=review_decision_summary_payload,
            review_decision_table_payload=review_decision_table_payload,
            convergence_payload=convergence_payload,
            candidate_total_before_judge=candidate_total_before_judge,
            empty_result_guard_triggered=empty_result_guard_triggered,
            empty_result_stage=empty_result_stage,
            gen_diag_payload=gen_diag_payload_value,
            compression_event_payload=compression_event_payload_value,
            judge_summary_payload=judge_summary_payload,
            judge_decision_table_payload=judge_decision_table_payload,
            quality_gate_result=quality_gate_result,
            workflow_blueprints=workflow_blueprints,
            log_entry_cls=log_entry_cls,
            test_generation_cls=test_generation_cls,
            count_unique_test_cases_fn=count_unique_test_cases_fn,
            project_persistable_cases_fn=project_persistable_cases_fn,
            evaluate_persistence_gate_fn=evaluate_persistence_gate_fn,
            build_persistence_gate_diagnostic_fn=build_persistence_gate_diagnostic_fn,
            emit_post_persist_generation_diagnostics_fn=emit_post_persist_generation_diagnostics_fn,
            emit_post_persist_coverage_audit_diagnostics_fn=emit_post_persist_coverage_audit_diagnostics_fn,
            build_coverage_diagnostics_fn=build_coverage_diagnostics_fn,
            coverage_diagnostics_enabled=coverage_diagnostics_enabled,
        )
        return persistence_result
    except Exception as exc:
        print(f"Failed to save to DB: {exc}")
        return JsonPersistenceResult(
            result=_project_public_result(
                result_value,
                project_persistable_cases_fn=project_persistable_cases_fn,
            )
        )


def _evaluate_and_persist(
    *,
    db: Any,
    client: Any,
    result: Any,
    raw_response_payload: Any,
    original_requirement: str,
    requirement: str,
    project_id: int,
    user_id: int | None,
    request_id: str,
    normalized_generation_mode: str,
    multi_pass: bool,
    resolved_current_biz: str,
    doc_type: str,
    compress: bool,
    expected_count: int,
    kb_context: str,
    context_result: dict[str, Any],
    generation_summary_payload: dict[str, Any],
    final_case_count: int,
    review_decision_summary_payload: dict[str, Any],
    review_decision_table_payload: list[dict[str, Any]],
    convergence_payload: dict[str, Any],
    candidate_total_before_judge: int,
    empty_result_guard_triggered: bool,
    empty_result_stage: str,
    gen_diag_payload: dict[str, Any],
    compression_event_payload: dict[str, Any],
    judge_summary_payload: dict[str, Any],
    judge_decision_table_payload: list[dict[str, Any]],
    quality_gate_result: dict[str, Any],
    workflow_blueprints: list[dict[str, Any]],
    log_entry_cls: Callable[..., Any],
    test_generation_cls: Callable[..., Any],
    count_unique_test_cases_fn: Callable[[Any], Any],
    project_persistable_cases_fn: Callable[..., Any],
    evaluate_persistence_gate_fn: Callable[..., dict[str, Any]],
    build_persistence_gate_diagnostic_fn: Callable[..., dict[str, Any]],
    emit_post_persist_generation_diagnostics_fn: Callable[..., Any],
    emit_post_persist_coverage_audit_diagnostics_fn: Callable[..., Any],
    build_coverage_diagnostics_fn: Callable[..., dict[str, Any]],
    coverage_diagnostics_enabled: bool,
) -> JsonPersistenceResult:
    execution_plan_payload = _resolved_execution_plan_payload(
        review_decision_summary_payload,
        workflow_blueprints,
    )

    persistence_cases = project_persistable_cases_fn(result)
    mode = normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass")
    from core.settings.config import settings

    persistence_gate_result = evaluate_persistence_gate_fn(
        persistence_cases,
        workflow_blueprints=workflow_blueprints,
        execution_plan=execution_plan_payload,
        generation_mode=mode,
        quality_gate=quality_gate_result,
        settings=settings,
    )
    persistence_gate_diag = build_persistence_gate_diagnostic_fn(persistence_gate_result)
    persistence_gate_diag["request_id"] = request_id
    persistence_gate_diag["project_id"] = int(project_id)
    db.add(
        log_entry_cls(
            project_id=project_id,
            user_id=user_id,
            log_type="system",
            message=f"GEN_DIAG:{json.dumps(persistence_gate_diag, ensure_ascii=False)}",
        )
    )
    db.commit()

    persistence_failure_code = str(persistence_gate_result.get("failure_code") or "")
    if persistence_failure_code == "EMPTY_GENERATED_RESULT":
        return JsonPersistenceResult(result=result, error_payload=_empty_generated_result_payload())
    if persistence_failure_code == "LOW_QUALITY_GENERATED_CASES":
        quality_payload = _quality_gate_payload(
            quality_gate_result=quality_gate_result,
            request_id=request_id,
            multi_pass=multi_pass,
            generation_mode=mode,
        )
        db.add(
            log_entry_cls(
                project_id=project_id,
                user_id=user_id,
                log_type="system",
                message=f"GEN_DIAG:{json.dumps(quality_payload, ensure_ascii=False)}",
            )
        )
        db.commit()
        return JsonPersistenceResult(
            result=result,
            error_payload=_quality_gate_error_payload(quality_gate_result),
        )

    if not bool(persistence_gate_result.get("passed")):
        execution_validation = dict(persistence_gate_result.get("execution_plan_validation") or {})
        return JsonPersistenceResult(
            result=result,
            error_payload={
                "error": "execution_plan_failed",
                "error_code": "execution_plan_failed",
                "error_message": "生成结果未通过执行计划门禁",
                "final_status": "execution_plan_failed",
                "persistence_gate_failed": True,
                "failure_reasons": list(execution_validation.get("failure_reasons") or []),
                "metrics": dict(execution_validation.get("metrics") or {}),
                "state_conflicts": list(execution_validation.get("state_conflicts") or []),
                "semantic_conflicts": list(execution_validation.get("semantic_conflicts") or []),
                "execution_group_order_conflicts": list(
                    execution_validation.get("execution_group_order_conflicts") or []
                ),
            },
        )

    persisted_result = project_persistable_cases_fn(
        persistence_gate_result.get("cases") if isinstance(persistence_gate_result.get("cases"), list) else []
    )
    db_entry = test_generation_cls(
        requirement_text=original_requirement,
        generated_result=json.dumps(persisted_result, ensure_ascii=False)
        if not (isinstance(persisted_result, dict) and ("error" in persisted_result))
        else json.dumps({"error": persisted_result, "raw": raw_response_payload}, ensure_ascii=False),
        project_id=project_id,
        user_id=user_id,
    )
    db.add(db_entry)
    db.commit()
    db.refresh(db_entry)
    persisted_generation_id = int(db_entry.id or 0)

    emit_post_persist_generation_diagnostics_fn(
        db=db,
        project_id=project_id,
        user_id=user_id,
        request_id=request_id,
        generation_id=persisted_generation_id,
        normalized_generation_mode=normalized_generation_mode,
        multi_pass=multi_pass,
        resolved_current_biz=resolved_current_biz,
        doc_type=doc_type,
        compress=compress,
        expected_count=expected_count,
        result=persisted_result,
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
        count_unique_test_cases_fn=count_unique_test_cases_fn,
    )
    if isinstance(persisted_result, dict):
        persisted_result["db_id"] = db_entry.id

    emit_post_persist_coverage_audit_diagnostics_fn(
        db=db,
        project_id=project_id,
        user_id=user_id,
        result=persisted_result,
        requirement=requirement,
        kb_context=kb_context,
        context_result=context_result if isinstance(context_result, dict) else {},
        expected_count=expected_count,
        coverage_diagnostics_enabled=bool(coverage_diagnostics_enabled),
        build_coverage_diagnostics_fn=build_coverage_diagnostics_fn,
    )
    return JsonPersistenceResult(result=persisted_result)


def _empty_generated_result_payload() -> dict[str, Any]:
    return {
        "error": "EMPTY_GENERATED_RESULT",
        "error_code": "EMPTY_GENERATED_RESULT",
        "error_message": "生成完成但最终测试用例为空",
        "status": "failed",
        "final_status": "empty_result_failed",
        "empty_result_guard_triggered": True,
        "empty_result_stage": "persistence_gate",
    }


def _quality_gate_payload(
    *,
    quality_gate_result: dict[str, Any],
    request_id: str,
    multi_pass: bool,
    generation_mode: str,
) -> dict[str, Any]:
    priority_final_invalid_case_ids = list(quality_gate_result.get("invalid_priority_final_case_ids") or [])
    return {
        "kind": "generation_quality_gate",
        "request_id": request_id,
        "multi_pass": bool(multi_pass),
        "generation_mode": generation_mode,
        "error_code": "LOW_QUALITY_GENERATED_CASES",
        "final_status": "quality_gate_failed",
        "quality_gate_failed": True,
        "priority_final_null_count": int(quality_gate_result.get("priority_final_null_count") or 0),
        "invalid_priority_final_count": int(
            quality_gate_result.get("invalid_priority_final_count") or len(priority_final_invalid_case_ids)
        ),
        "invalid_priority_final_case_ids": priority_final_invalid_case_ids,
        "non_assertable_expected_result_count": int(quality_gate_result.get("non_assertable_expected_result_count") or 0),
        "truncated_text_count": int(quality_gate_result.get("truncated_text_count") or 0),
        "non_assertable_case_ids": list(quality_gate_result.get("non_assertable_case_ids") or []),
        "truncated_case_ids": list(quality_gate_result.get("truncated_case_ids") or []),
        "persistable_required_field_missing_case_ids": list(
            quality_gate_result.get("persistable_required_field_missing_case_ids") or []
        ),
        "persistable_priority_final_invalid_case_ids": list(
            quality_gate_result.get("persistable_priority_final_invalid_case_ids") or []
        ),
        "persistable_reasoning_leakage_case_ids": list(
            quality_gate_result.get("persistable_reasoning_leakage_case_ids") or []
        ),
        "failed_checks": list(quality_gate_result.get("failed_checks") or []),
    }


def _quality_gate_error_payload(quality_gate_result: dict[str, Any]) -> dict[str, Any]:
    payload = _quality_gate_payload(
        quality_gate_result=quality_gate_result,
        request_id="",
        multi_pass=False,
        generation_mode="",
    )
    payload.pop("kind", None)
    payload.pop("request_id", None)
    payload.pop("multi_pass", None)
    payload.pop("generation_mode", None)
    payload["error"] = "LOW_QUALITY_GENERATED_CASES"
    payload["error_message"] = "生成结果未通过质量门禁：存在不可执行或截断的预期结果"
    return payload


__all__ = [
    "JsonPersistenceResult",
    "run_json_persistence_flow",
]
