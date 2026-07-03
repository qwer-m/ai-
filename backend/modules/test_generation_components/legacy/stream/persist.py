from typing import Any, Iterator
import json
import traceback

from .persistence_diagnostics import (
    _build_pre_persistence_failure_diagnostics,
)
from .persistence_post_persist_diagnostics import (
    add_diagnostic_log,
    build_stream_post_persist_diagnostic_payloads,
)
from .persistence_postprocess_result import unpack_stream_postprocess_result
from .persistence_quality_ledger import (
    _build_quality_ledger_payload as _build_quality_ledger_payload_impl,
)
from .persistence_status_text import render_stop_reason_text as _render_stop_reason_text
from .persistence_timing_events import sanitize_timing_events as _sanitize_timing_events
from .persistence_timing_ledger import build_stream_timing_ledger
from .runtime import LazyAttrProxy, call_component


LogEntry = LazyAttrProxy("core.db.models", "LogEntry")
TestGeneration = LazyAttrProxy("core.db.models", "TestGeneration")
settings = LazyAttrProxy("core.settings.config", "settings")
STAGE25_SWITCHES = LazyAttrProxy("modules.domain.stage25_switches", "STAGE25_SWITCHES")


def summarize_case_quality_gate(*args: Any, **kwargs: Any) -> Any:
    return call_component("...coverage.case_quality_gate", "summarize_case_quality_gate", *args, **kwargs)


def build_case_quality_failures(*args: Any, **kwargs: Any) -> Any:
    return call_component("...postprocess.persistence_gate", "build_case_quality_failures", *args, **kwargs)


def build_case_quality_metrics(*args: Any, **kwargs: Any) -> Any:
    return call_component("...postprocess.persistence_gate", "build_case_quality_metrics", *args, **kwargs)


def build_persistence_gate_diagnostic(*args: Any, **kwargs: Any) -> Any:
    return call_component("...postprocess.persistence_gate", "build_persistence_gate_diagnostic", *args, **kwargs)


def evaluate_persistence_gate(*args: Any, **kwargs: Any) -> Any:
    return call_component("...postprocess.persistence_gate", "evaluate_persistence_gate", *args, **kwargs)


def is_candidate_insufficient_underfill(*args: Any, **kwargs: Any) -> Any:
    return call_component("...postprocess.persistence_gate", "is_candidate_insufficient_underfill", *args, **kwargs)


def summarize_persistence_case_quality_gate(*args: Any, **kwargs: Any) -> Any:
    return call_component("...postprocess.persistence_gate", "summarize_persistence_case_quality_gate", *args, **kwargs)


def merge_contract_quality_gate(*args: Any, **kwargs: Any) -> Any:
    return call_component("...postprocess.case_contract", "merge_contract_quality_gate", *args, **kwargs)


def project_persistable_cases(*args: Any, **kwargs: Any) -> Any:
    return call_component("...postprocess.case_contract", "project_persistable_cases", *args, **kwargs)


def summarize_persistable_case_contract(*args: Any, **kwargs: Any) -> Any:
    return call_component("...postprocess.case_contract", "summarize_persistable_case_contract", *args, **kwargs)


def build_context_compression_diagnostics(*args: Any, **kwargs: Any) -> Any:
    return call_component("...prompting.generation_diagnostics", "build_context_compression_diagnostics", *args, **kwargs)


def build_coverage_diagnostics(*args: Any, **kwargs: Any) -> Any:
    return call_component("...prompting.generation_diagnostics", "build_coverage_diagnostics", *args, **kwargs)


def build_supplement_closed_loop_instruction(*args: Any, **kwargs: Any) -> Any:
    return call_component(
        "...prompting.prompt_orchestration",
        "build_supplement_closed_loop_instruction",
        *args,
        **kwargs,
    )


def merge_cases_for_append(*args: Any, **kwargs: Any) -> Any:
    return call_component("...postprocess.result_postprocess", "merge_cases_for_append", *args, **kwargs)


def normalize_final_case_priorities(*args: Any, **kwargs: Any) -> Any:
    return call_component("...postprocess.result_postprocess", "normalize_final_case_priorities", *args, **kwargs)


def stream_postprocess_cases(*args: Any, **kwargs: Any) -> Any:
    return call_component("...postprocess.result_postprocess", "stream_postprocess_cases", *args, **kwargs)


def count_unique_test_cases(*args: Any, **kwargs: Any) -> Any:
    return call_component("..adapters", "count_unique_test_cases", *args, **kwargs)


def deduplicate_test_cases(*args: Any, **kwargs: Any) -> Any:
    return call_component("..adapters", "deduplicate_test_cases", *args, **kwargs)


def infer_case_kind(*args: Any, **kwargs: Any) -> Any:
    return call_component("..adapters", "infer_case_kind", *args, **kwargs)


def normalize_json_structure(*args: Any, **kwargs: Any) -> Any:
    return call_component("..adapters", "normalize_json_structure", *args, **kwargs)


def reorder_cases_by_closed_loop(*args: Any, **kwargs: Any) -> Any:
    return call_component("..adapters", "reorder_cases_by_closed_loop", *args, **kwargs)


def clean_and_parse_json(*args: Any, **kwargs: Any) -> Any:
    return call_component("..adapters", "clean_and_parse_json", *args, **kwargs)


def _select_generation_model(client: Any, full_input: str) -> str:
    selector = getattr(client, "select_model", None)
    if callable(selector):
        try:
            selected = selector(full_input, task_type="generation")
            if selected:
                return str(selected)
        except Exception:
            pass
    return str(getattr(client, "model_name", None) or getattr(client, "model", None) or "unknown")


def _normalize_missing_priority_final_cases(
    cases: Any,
    *,
    requirement_text: str,
) -> Any:
    """Fill stripped priority_final fields without masking explicit invalid values."""
    if not isinstance(cases, list):
        return cases
    if not any(isinstance(item, dict) and "priority_final" not in item for item in cases):
        return cases

    normalized = normalize_final_case_priorities(cases, requirement_text=requirement_text)
    normalized_by_index = {
        index: item
        for index, item in enumerate(normalized if isinstance(normalized, list) else [])
        if isinstance(item, dict)
    }
    resolved: list[Any] = []
    for index, item in enumerate(cases):
        if isinstance(item, dict) and "priority_final" not in item:
            resolved.append(normalized_by_index.get(index, item))
        else:
            resolved.append(item)
    return resolved


def _build_quality_ledger_payload(
    *,
    generation_id: int | None,
    request_id: str,
    mode: str,
    stage_counts: dict[str, Any],
    coverage_payload: dict[str, Any],
    convergence_payload: dict[str, Any],
    generation_summary_payload: dict[str, Any],
    review_decision_summary_payload: dict[str, Any],
    judge_summary_payload: dict[str, Any],
    feedback_control_debug_payload: dict[str, Any],
    compression_diag_payload: dict[str, Any],
    context_result: dict[str, Any],
    judge_decision_table_payload: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _build_quality_ledger_payload_impl(
        generation_id=generation_id,
        request_id=request_id,
        mode=mode,
        stage_counts=stage_counts,
        coverage_payload=coverage_payload,
        convergence_payload=convergence_payload,
        generation_summary_payload=generation_summary_payload,
        review_decision_summary_payload=review_decision_summary_payload,
        judge_summary_payload=judge_summary_payload,
        feedback_control_debug_payload=feedback_control_debug_payload,
        compression_diag_payload=compression_diag_payload,
        context_result=context_result,
        judge_decision_table_payload=judge_decision_table_payload,
        build_case_quality_failures_fn=build_case_quality_failures,
        build_case_quality_metrics_fn=build_case_quality_metrics,
        is_candidate_insufficient_underfill_fn=is_candidate_insufficient_underfill,
    )


class LegacyGenerationStreamPersistMixin:

    def _stream_persist_phase(
        self,
        *,
        state: dict[str, Any],
    ) -> Iterator[Any]:
        client = state["client"]
        requirement = state["requirement"]
        project_id = state["project_id"]
        db = state["db"]
        doc_type = state["doc_type"]
        compress = state["compress"]
        expected_count = state["expected_count"]
        overwrite = state["overwrite"]
        append = state["append"]
        user_id = state["user_id"]
        original_requirement = state["original_requirement"]
        kb_context = state.get("kb_context") or ""
        start_id = int(state.get("start_id") or 1)
        existing_cases = state.get("existing_cases") or []
        existing_entry = state.get("existing_entry")
        context_result = state.get("context_result") or {}
        gate_debug = state.get("gate_debug") or {}
        base_prompt = state.get("base_prompt") or ""
        full_content = state.get("full_content") or ""
        existing_unique_count = int(state.get("existing_unique_count") or 0)
        system_prompt = state.get("system_prompt") or ""
        current_biz_key = str(state.get("current_biz_key") or "")
        multi_pass = bool(state.get("multi_pass", True))
        generation_mode = str(state.get("generation_mode") or "").strip().lower()
        request_id = str(state.get("request_id") or "").strip()
        feedback_control_state = state.get("feedback_control_state") or {}
        requirement_semantics_context = state.get("requirement_semantics_context") or {}
        memory_diag = state.get("memory_diag") if isinstance(state.get("memory_diag"), dict) else {}
        generation_timing_events = _sanitize_timing_events(state.get("generation_timing_events") or [])
        persisted_generation_id: int | None = None

        try:
            postprocess_result = yield from stream_postprocess_cases(
                client=client,
                requirement=requirement,
                base_prompt=base_prompt,
                kb_context=kb_context,
                full_content=full_content,
                expected_count=expected_count,
                append=append,
                existing_cases=existing_cases,
                existing_unique_count=existing_unique_count,
                start_id=start_id,
                db=db,
                clean_and_parse_json_fn=clean_and_parse_json,
                normalize_json_structure_fn=normalize_json_structure,
                deduplicate_test_cases_fn=deduplicate_test_cases,
                reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
                count_unique_test_cases_fn=count_unique_test_cases,
                infer_case_kind_fn=infer_case_kind,
                build_supplement_closed_loop_instruction_fn=build_supplement_closed_loop_instruction,
                current_biz_key=current_biz_key,
                multi_pass=multi_pass,
                generation_mode=generation_mode,
                feedback_control_state=feedback_control_state,
                requirement_semantics_context=requirement_semantics_context,
            )

            postprocess_payload = unpack_stream_postprocess_result(
                postprocess_result,
                generation_timing_events=generation_timing_events,
                sanitize_timing_events_fn=_sanitize_timing_events,
            )
            parsed_result = postprocess_payload.parsed_result
            stage_counts = postprocess_payload.stage_counts
            coverage_payload = postprocess_payload.coverage_payload
            convergence_payload = postprocess_payload.convergence_payload
            generation_summary_payload = postprocess_payload.generation_summary_payload
            review_decision_summary_payload = postprocess_payload.review_decision_summary_payload
            review_decision_table_payload = postprocess_payload.review_decision_table_payload
            judge_decision_table_payload = postprocess_payload.judge_decision_table_payload
            feedback_control_debug_payload = postprocess_payload.feedback_control_debug_payload
            judge_summary_payload = postprocess_payload.judge_summary_payload
            generation_timing_events = postprocess_payload.generation_timing_events

            parsed_result = _normalize_missing_priority_final_cases(parsed_result, requirement_text=requirement)
            stream_quality_gate_result = summarize_case_quality_gate(parsed_result if isinstance(parsed_result, list) else [])
            stream_quality_gate_result = merge_contract_quality_gate(
                stream_quality_gate_result,
                summarize_persistable_case_contract(parsed_result),
            )
            parsed_result = project_persistable_cases(parsed_result)
            stream_quality_gate_result = summarize_persistence_case_quality_gate(
                stream_quality_gate_result,
                generation_summary=generation_summary_payload,
                review_decision_summary=review_decision_summary_payload,
                judge_summary=judge_summary_payload,
                settings=settings,
            )
            persistence_preview = parsed_result
            if append and existing_entry:
                persistence_preview = merge_cases_for_append(
                    existing_cases,
                    parsed_result,
                    deduplicate_test_cases_fn=deduplicate_test_cases,
                    reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
                )
            persistence_preview = project_persistable_cases(persistence_preview)
            stream_quality_gate_result = merge_contract_quality_gate(
                stream_quality_gate_result,
                summarize_persistable_case_contract(persistence_preview),
            )
            workflow_blueprints = [
                dict(item)
                for item in (feedback_control_state.get("workflow_blueprints") or [])
                if isinstance(item, dict)
            ] if isinstance(feedback_control_state, dict) else []
            execution_plan = dict(review_decision_summary_payload.get("execution_plan") or {})
            persistence_gate_result = evaluate_persistence_gate(
                persistence_preview,
                workflow_blueprints=workflow_blueprints,
                execution_plan=execution_plan,
                generation_mode=generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                quality_gate=stream_quality_gate_result,
                settings=settings,
            )
            persistence_gate_diag = build_persistence_gate_diagnostic(persistence_gate_result)
            persistence_gate_diag["request_id"] = request_id
            persistence_gate_diag["project_id"] = int(project_id)
            yield add_diagnostic_log(
                db=db,
                log_entry_type=LogEntry,
                project_id=project_id,
                user_id=user_id,
                payload=persistence_gate_diag,
            )
            if db:
                db.commit()
            if not bool(persistence_gate_result.get("passed")):
                compression_diag_payload = build_context_compression_diagnostics(
                    context_result=context_result if isinstance(context_result, dict) else {},
                )
                pre_failure_diagnostics = _build_pre_persistence_failure_diagnostics(
                    build_quality_ledger_payload=_build_quality_ledger_payload,
                    generation_id=None,
                    request_id=request_id,
                    project_id=int(project_id),
                    mode=generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    multi_pass=bool(multi_pass),
                    expected_count=int(expected_count or 0),
                    stage_counts=stage_counts,
                    coverage_payload=coverage_payload,
                    convergence_payload=convergence_payload,
                    generation_summary_payload=generation_summary_payload,
                    review_decision_summary_payload=review_decision_summary_payload,
                    review_decision_table_payload=review_decision_table_payload,
                    judge_summary_payload=judge_summary_payload,
                    judge_decision_table_payload=judge_decision_table_payload,
                    feedback_control_debug_payload=feedback_control_debug_payload,
                    compression_diag_payload=compression_diag_payload,
                    context_result=context_result if isinstance(context_result, dict) else {},
                )
                for diag_payload in pre_failure_diagnostics:
                    yield add_diagnostic_log(
                        db=db,
                        log_entry_type=LogEntry,
                        project_id=project_id,
                        user_id=user_id,
                        payload=diag_payload,
                    )
                if db and pre_failure_diagnostics:
                    db.commit()
                failure_code = str(persistence_gate_result.get("failure_code") or "execution_plan_failed")
                execution_plan_validation = persistence_gate_result.get("execution_plan_validation")
                quality_gate = persistence_gate_result.get("quality_gate")
                quality_failed_checks = (
                    quality_gate.get("failed_checks")
                    if isinstance(quality_gate, dict)
                    else []
                )
                execution_failure_reasons = (
                    execution_plan_validation.get("failure_reasons")
                    if isinstance(execution_plan_validation, dict)
                    else []
                )
                failure_reasons = (
                    quality_failed_checks
                    if failure_code == "LOW_QUALITY_GENERATED_CASES"
                    else execution_failure_reasons
                )
                failure_reason_text = ",".join(
                    str(item).strip()
                    for item in (failure_reasons or [])
                    if str(item).strip()
                )
                failure_detail = f": {failure_reason_text}" if failure_reason_text else ""
                yield f"\n@@STATUS@@:生成结果未通过落库门禁{failure_detail}\n"
                yield f"Error: {failure_code}{failure_detail}\n"
                return
            parsed_result = persistence_gate_result.get("cases") if isinstance(persistence_gate_result.get("cases"), list) else []
            cleaned_response = json.dumps(parsed_result, ensure_ascii=False)
            if db:
                if overwrite:
                    from sqlalchemy import desc

                    query = db.query(TestGeneration).filter(
                        TestGeneration.project_id == project_id,
                        TestGeneration.requirement_text == original_requirement,
                    )
                    if user_id:
                        query = query.filter(TestGeneration.user_id == user_id)
                    existing_entry_overwrite = query.order_by(desc(TestGeneration.created_at)).first()
                    if existing_entry_overwrite:
                        existing_entry_overwrite.generated_result = cleaned_response
                        db.commit()
                        persisted_generation_id = int(existing_entry_overwrite.id or 0) or None
                    else:
                        new_entry = TestGeneration(
                            requirement_text=original_requirement,
                            generated_result=cleaned_response,
                            project_id=project_id,
                            user_id=user_id,
                        )
                        db.add(
                            new_entry
                        )
                        db.commit()
                        persisted_generation_id = int(new_entry.id or 0) or None
                elif append and existing_entry:
                    existing_entry.generated_result = json.dumps(parsed_result, ensure_ascii=False)
                    db.commit()
                    persisted_generation_id = int(existing_entry.id or 0) or None
                else:
                    new_entry = TestGeneration(
                        requirement_text=original_requirement,
                        generated_result=cleaned_response,
                        project_id=project_id,
                        user_id=user_id,
                    )
                    db.add(
                        new_entry
                    )
                    db.commit()
                    persisted_generation_id = int(new_entry.id or 0) or None

                # 中文注释：把本次最终落库 generation_id 回传给前端，便于流式完成后回拉最终结果。
                timing_ledger = build_stream_timing_ledger(
                    generation_timing_events=generation_timing_events,
                    generation_id=persisted_generation_id,
                    project_id=project_id,
                    request_id=request_id,
                    generation_mode=generation_mode,
                    multi_pass=multi_pass,
                )
                duration_by_stage_ms = timing_ledger.duration_by_stage_ms

                full_input = (system_prompt or "") + requirement
                actual_model = _select_generation_model(client, full_input)
                compression_diag_payload = build_context_compression_diagnostics(
                    context_result=context_result if isinstance(context_result, dict) else {},
                )
                quality_ledger_payload = _build_quality_ledger_payload(
                    generation_id=persisted_generation_id,
                    request_id=request_id,
                    mode=generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    stage_counts=stage_counts,
                    coverage_payload=coverage_payload,
                    convergence_payload=convergence_payload,
                    generation_summary_payload=generation_summary_payload,
                    review_decision_summary_payload=review_decision_summary_payload,
                    judge_summary_payload=judge_summary_payload,
                    feedback_control_debug_payload=feedback_control_debug_payload,
                    compression_diag_payload=compression_diag_payload,
                    context_result=context_result if isinstance(context_result, dict) else {},
                    judge_decision_table_payload=judge_decision_table_payload,
                )
                post_persist_payloads = build_stream_post_persist_diagnostic_payloads(
                    generation_id=persisted_generation_id,
                    project_id=project_id,
                    request_id=request_id,
                    generation_mode=generation_mode,
                    multi_pass=multi_pass,
                    current_biz_key=current_biz_key,
                    timing_payload=timing_ledger.payload,
                    stage_counts=stage_counts,
                    duration_by_stage_ms=duration_by_stage_ms,
                    doc_type=doc_type,
                    compress=compress,
                    expected_count=expected_count,
                    generated_count=count_unique_test_cases(parsed_result),
                    requirement_length=len(requirement),
                    kb_length=len(kb_context or ""),
                    model=actual_model,
                    max_tokens=getattr(client, "max_tokens", None),
                    compression_diag_payload=compression_diag_payload,
                    convergence_payload=convergence_payload,
                    review_decision_summary_payload=review_decision_summary_payload,
                    feedback_control_debug_payload=feedback_control_debug_payload,
                    judge_summary_payload=judge_summary_payload,
                    judge_decision_table_payload=judge_decision_table_payload,
                    memory_diag=memory_diag,
                    review_decision_table_payload=review_decision_table_payload,
                    generation_summary_payload=generation_summary_payload,
                    quality_ledger_payload=quality_ledger_payload,
                    coverage_payload=coverage_payload,
                )
                for payload in post_persist_payloads.before_generation_summary:
                    yield add_diagnostic_log(
                        db=db,
                        log_entry_type=LogEntry,
                        project_id=project_id,
                        user_id=user_id,
                        payload=payload,
                    )

                if post_persist_payloads.generation_summary:
                    yield add_diagnostic_log(
                        db=db,
                        log_entry_type=LogEntry,
                        project_id=project_id,
                        user_id=user_id,
                        payload=post_persist_payloads.generation_summary,
                    )
                    status = str(generation_summary_payload.get("status") or "")
                    stop_reason_text = _render_stop_reason_text(
                        list(generation_summary_payload.get("stop_reason") or [])
                    )
                    if status in {"completed_with_optimal_set", "completed_with_quality_stop"}:
                        yield "@@STATUS@@:正常完成\n"
                        if stop_reason_text:
                            yield f"@@STATUS@@:停止原因：{stop_reason_text}\n"
                    if status == "completed_with_optimal_set":
                        yield "@@STATUS@@:已达到质量停止条件\n"
                        yield "@@STATUS@@:当前为最优测试用例集合\n"
                        yield "@@STATUS@@:继续生成将降低质量或增加冗余\n"

                for payload in post_persist_payloads.after_generation_summary:
                    yield add_diagnostic_log(
                        db=db,
                        log_entry_type=LogEntry,
                        project_id=project_id,
                        user_id=user_id,
                        payload=payload,
                    )

                # 保留既有覆盖诊断
                if STAGE25_SWITCHES.coverage_diagnostics_enabled:
                    coverage_diag = build_coverage_diagnostics(
                        requirement=requirement,
                        generated_cases=[x for x in parsed_result if isinstance(x, dict)],
                        kb_context=kb_context,
                        fusion_debug=(context_result or {}).get("fusion_debug") or {},
                        expected_count=int(expected_count or 0),
                    )
                    yield add_diagnostic_log(
                        db=db,
                        log_entry_type=LogEntry,
                        project_id=project_id,
                        user_id=user_id,
                        payload=coverage_diag,
                        prefix="GEN_COVERAGE_DIAG",
                    )

                db.commit()

                self._emit_context_source_log(
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                    context_result=context_result,
                    gate_debug=gate_debug,
                    doc_type=doc_type,
                    compress=compress,
                    requirement_length=len(requirement or ""),
                )
        except Exception as e:
            error_type = type(e).__name__
            error_message = str(e)
            diagnostic_payload = {
                "kind": "stream_persist_exception",
                "request_id": request_id,
                "project_id": int(project_id or 0),
                "user_id": int(user_id or 0) if user_id is not None else None,
                "generation_id": int(persisted_generation_id or 0),
                "error_type": error_type,
                "error_message": error_message[:1000],
                "traceback_tail": traceback.format_exc()[-2000:],
            }
            print(f"Failed to save streamed result to DB: {error_type}: {error_message}")
            if db:
                try:
                    db.rollback()
                except Exception:
                    pass
                try:
                    yield add_diagnostic_log(
                        db=db,
                        log_entry_type=LogEntry,
                        project_id=project_id,
                        user_id=user_id,
                        payload=diagnostic_payload,
                    )
                    db.commit()
                except Exception:
                    try:
                        db.rollback()
                    except Exception:
                        pass
            else:
                yield f"GEN_DIAG:{json.dumps(diagnostic_payload, ensure_ascii=False)}\n"
            yield "@@STATUS@@:生成结果落库失败，已保留流式预览结果，请查看 stream_persist_exception 诊断\n"
            yield f"Error: STREAM_PERSISTENCE_FAILED: {error_type}: {error_message[:300]}\n"
