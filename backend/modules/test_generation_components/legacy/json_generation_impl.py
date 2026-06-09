from typing import Any
import json
import uuid

from sqlalchemy.orm import Session

from core.ai.ai_client import get_client_for_user
from core.db.models import TestGeneration, LogEntry
from modules.domain.stage25_switches import STAGE25_SWITCHES
from modules.memory_fabric.contracts.memory_context import MemoryContext
from modules.memory_fabric.runtime.diagnostics import init_memory_diag
from modules.memory_fabric.runtime.factory import get_memory_fabric
from ..prompting.generation_diagnostics import (
    build_context_compression_diagnostics,
    build_coverage_diagnostics,
    build_gate_reason_chain,
)
from ..coverage.coverage_analyzer import (
    analyze_coverage,
)
from ..prompting.prompt_orchestration import (
    build_append_closed_loop_coverage_instruction,
    build_closed_loop_base_prompt,
    build_supplement_closed_loop_instruction,
)
from ..prompting.structured_context import (
    build_structured_prompt_context,
)
from ..control.build_feedback_control_state import (
    build_feedback_control_state,
)
from ..control.generation_mode_activation import (
    merge_generation_mode_control_state,
    resolve_linked_final_case_signal,
)
from ..postprocess.result_postprocess import (
    finalize_generated_cases,
    merge_cases_for_append,
    normalize_final_case_priorities,
    prepare_append_existing_cases,
    stream_postprocess_cases,
)
from ..postprocess.persistence_gate import (
    build_persistence_gate_diagnostic,
    evaluate_persistence_gate,
    summarize_persistence_case_quality_gate,
)
from ..postprocess.case_contract import (
    merge_contract_quality_gate,
    project_persistable_cases,
    summarize_persistable_case_contract,
)
from .multi_pass_pipeline import (
    run_multi_pass_generation,
)
from ..judge.test_case_judge import (
    judge_cases,
)
from ..judge.test_case_repairer import (
    repair_cases,
)
from ..judge.training_gate import (
    training_gate,
)
from .adapters import (
    clean_and_parse_json,
    normalize_json_structure,
    deduplicate_test_cases,
    count_unique_test_cases,
    infer_case_kind,
    reorder_cases_by_closed_loop,
    convert_json_to_excel as _convert_json_to_excel_adapter,
)


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


class LegacyGenerationJsonMixin:

    def generate_test_cases_json(
        self,
        requirement: str,
        project_id: int,
        db: Session = None,
        doc_type: str = "requirement",
        compress: bool = False,
        expected_count: int = 20,
        batch_size: int = 20,
        batch_index: int = 0,
        user_id: int = None,
        current_biz_key: str = "",
        only_current_biz: bool = False,
        multi_pass: bool = True,
        generation_mode: str = "",
        enable_sample_pool_feedback: bool = True,
    ) -> dict:
        """Generate test cases in JSON format for stage-1 orchestration."""
        # Get client for user
        client = get_client_for_user(user_id, db)
        request_id = uuid.uuid4().hex

        # Retrieve context from Knowledge Base if DB is available
        original_requirement = requirement
        kb_context = ""
        gate_debug: dict[str, Any] = {}
        context_result: dict[str, Any] | None = None
        gate_result: dict[str, Any] | None = None
        feedback_control_state: dict[str, Any] = {}
        linked_final_case_signal = resolve_linked_final_case_signal(
            db=db,
            project_id=project_id,
            user_id=user_id,
            requirement_text=original_requirement,
        )
        memory_diag: dict[str, Any] = init_memory_diag()
        memory_fabric = None
        try:
            memory_fabric = get_memory_fabric()
        except Exception:
            memory_fabric = None
        memory_ctx = MemoryContext.from_runtime(
            user_id=user_id,
            project_id=project_id,
            run_id=request_id,
            request_id=request_id,
        )
        if db:
            # Non-session DB cannot run the snapshot readiness gate; keep generation unblocked.
            gate_result = {
                "proceed": True,
                "gate_debug": {
                    "snapshot_gate_enabled": False,
                    "snapshot_wait_result": "skipped_non_session_db",
                },
            }
            if self._is_active_db_session(db):
                gate_result = self._run_snapshot_readiness_gate(
                project_id=project_id,
                user_id=user_id,
                status_messages=None,
            )
            gate_debug = gate_result.get("gate_debug") or {}
            if not gate_result.get("proceed"):
                gate_reason_chain = build_gate_reason_chain(gate_debug)
                wait_result = str(gate_debug.get("snapshot_wait_result") or "").strip().lower()
                if "timeout" in wait_result:
                    gate_reason_chain.append("snapshot_gate_timeout")
                gate_reason_chain.append("hybrid_context_not_built")
                gate_reason_chain.append("generation_aborted_before_model_call")
                print(
                    "snapshot gate abort(json): "
                    f"status_before={gate_debug.get('snapshot_status_before_generation')}, "
                    f"status_after={gate_debug.get('snapshot_status_after_wait')}, "
                    f"result={gate_debug.get('snapshot_wait_result')}"
                )
                error_payload = {
                    "error": gate_result.get("error_code") or "SNAPSHOT_NOT_READY_TIMEOUT",
                    "abort_code": gate_result.get("error_code") or "SNAPSHOT_NOT_READY_TIMEOUT",
                    "message": gate_result.get("error_message")
                    or "snapshot is not ready; generation aborted by fail-fast gate.",
                    "fallback_reason": "snapshot_wait_gate_abort",
                    "context_source": "none",
                    "reason_chain": gate_reason_chain,
                    "fusion_debug": {
                        **gate_debug,
                        "final_decision": "timeout_fail_fast",
                        "final_generation_context_mode": "none",
                        "reason_chain": gate_reason_chain,
                    },
                }
                self._emit_context_source_log(
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                    context_result={
                        "context_source": "none",
                        "fusion_debug": error_payload.get("fusion_debug") or {},
                        "snapshot_result": {},
                        "rag_result": {},
                    },
                    gate_debug=gate_debug,
                    doc_type=doc_type,
                    compress=compress,
                    requirement_length=len(requirement or ""),
                )
                self._emit_final_context_trace(
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                    request_id=request_id,
                    context_result={
                        "context_source": "none",
                        "fusion_debug": error_payload.get("fusion_debug") or {},
                        "snapshot_result": {},
                        "rag_result": {},
                    },
                    gate_debug=gate_debug,
                    fallback_reason="snapshot_wait_gate_abort",
                    abort_code=error_payload.get("abort_code") or "",
                    compressed_chars=0,
                )
                return error_payload

            context_result = self._resolve_kb_context_with_hybrid(
                requirement=requirement,
                project_id=project_id,
                db=db,
                user_id=user_id,
                compress=compress,
                precision_mode=True,
                memory_fabric=memory_fabric,
                memory_ctx=memory_ctx,
                memory_diag=memory_diag,
                request_id=request_id,
            )
            fusion_debug = context_result.get("fusion_debug") or {}
            if gate_debug:
                # Merge snapshot gate diagnostics into fusion debug for unified tracing.
                fusion_debug.update(gate_debug)
                reason_chain = list(fusion_debug.get("reason_chain") or [])
                for reason in build_gate_reason_chain(gate_debug):
                    if reason and (not reason_chain or reason_chain[-1] != reason):
                        reason_chain.append(reason)
                fusion_debug["reason_chain"] = reason_chain
                if (
                    gate_debug.get("snapshot_wait_result") == "timeout_fallback_rag"
                    and fusion_debug.get("final_decision") == "proceed_with_generation"
                ):
                    fusion_debug["final_decision"] = "timeout_fallback_rag_then_proceed"
            fusion_debug["final_generation_context_mode"] = context_result.get("context_source") or "empty"
            context_result["fusion_debug"] = fusion_debug
            kb_context = context_result.get("kb_context") or ""
            feedback_control_state = merge_generation_mode_control_state(
                build_feedback_control_state(
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                    requirement_text=original_requirement,
                    enable_priority_sample_pool=bool(enable_sample_pool_feedback),
                    include_agent_learning=True,
                    memory_fabric=memory_fabric,
                    memory_ctx=memory_ctx,
                    memory_diag=memory_diag,
                ),
                requirement_text=original_requirement,
                expected_count=int(expected_count or 0),
                linked_final_case_count=int(linked_final_case_signal.get("linked_final_case_count") or 0),
            ).to_dict()
            # Stop before model generation when context assembly explicitly aborts.
            if context_result.get("abort_generation"):
                fusion_debug = context_result.get("fusion_debug") or {}
                abort_error = context_result.get("abort_error") or "context is empty; generation aborted by guard policy."
                reason_chain = list(fusion_debug.get("reason_chain") or [])
                if not reason_chain or reason_chain[-1] != "hybrid_context_not_built":
                    reason_chain.append("hybrid_context_not_built")
                if reason_chain[-1] != "generation_aborted_before_model_call":
                    reason_chain.append("generation_aborted_before_model_call")
                fusion_debug["reason_chain"] = reason_chain
                print(
                    "Hybrid guard abort(json): "
                    f"snapshot_status={fusion_debug.get('snapshot_status')}, "
                    f"snapshot_queue_status={fusion_debug.get('snapshot_queue_status')}, "
                    f"snapshot_queue_reason={fusion_debug.get('snapshot_queue_reason')}, "
                    f"lane_counts={fusion_debug.get('lane_counts')}, "
                    f"lane_reasons={fusion_debug.get('lane_reasons')}, "
                    f"final_empty_reason={fusion_debug.get('hybrid_empty_reason')}"
                )
                error_payload = {
                    "error": "HYBRID_EMPTY_CONTEXT_ABORT",
                    "abort_code": "HYBRID_EMPTY_CONTEXT_ABORT",
                    "message": abort_error,
                    "fallback_reason": context_result.get("fallback_reason") or "",
                    "context_source": context_result.get("context_source") or "empty",
                    "reason_chain": reason_chain,
                    "fusion_debug": fusion_debug,
                }
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
                self._emit_final_context_trace(
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                    request_id=request_id,
                    context_result=context_result,
                    gate_debug=gate_debug,
                    fallback_reason=context_result.get("fallback_reason") or "",
                    abort_code="HYBRID_EMPTY_CONTEXT_ABORT",
                    compressed_chars=len(kb_context or ""),
                )
                return error_payload

            if compress:
                # Compress the requirement before model generation when requested.
                try:
                    compressed_req = client.compress_context(
                        requirement,
                        prompt="请将以下需求压缩为适合测试用例生成的精炼版本，保留技术细节、字段/ID、约束、边界与异常规则，去除冗余。输出纯文本。",
                        db=db,
                    )
                    if compressed_req and not compressed_req.startswith("Error") and not compressed_req.startswith("Exception"):
                        requirement = compressed_req
                except Exception:
                    pass

        # Meta-Analysis Step (Dynamic Strategy Planning)
        analysis_result = self.analyze_requirement_context(requirement, kb_context, client, db)
        if isinstance(analysis_result, dict):
            strategy_plan = analysis_result
        else:
            # 中文注释：元分析异常时使用默认策略，避免中断主链路。
            strategy_plan = self._default_strategy_plan()
        feedback_control_state = merge_generation_mode_control_state(
            feedback_control_state,
            requirement_text=original_requirement,
            expected_count=int(expected_count or 0),
            linked_final_case_count=int(
                (linked_final_case_signal or {}).get("linked_final_case_count") or 0
            ),
        ).to_dict()
        prompt_context = build_structured_prompt_context(
            requirement=requirement or "",
            kb_context=kb_context or "",
            rag_result=(context_result or {}).get("rag_result") if isinstance(context_result, dict) else None,
            existing_cases=[],
            current_biz_key=current_biz_key,
            only_current_biz=bool(only_current_biz),
            feedback_control_state=feedback_control_state,
        )
        if isinstance(prompt_context.get("feedback_control_state"), dict):
            feedback_control_state = dict(prompt_context.get("feedback_control_state") or {})
        # 中文注释：控制层只做生成约束增强，诊断字段透传到 GEN_DIAG 便于闭环观测。
        feedback_control_diag_payload: dict[str, Any] = dict(prompt_context.get("control_summary") or {})
        resolved_current_biz = str(prompt_context.get("current_biz_key") or "unknown")
        base_prompt = build_closed_loop_base_prompt(
            strategy_plan,
            requirement_context=prompt_context.get("requirement_context") or "",
            requirement_semantics_context=prompt_context.get("requirement_semantics_context") or "",
            testcase_context=prompt_context.get("testcase_context") or "(empty)",
            supplement_context=prompt_context.get("supplement_context") or "(empty)",
            control_context=prompt_context.get("control_context") or "",
            current_biz_key=resolved_current_biz,
            doc_type=doc_type,
            pretty_json=False,
        )
        # 中文注释：在模型调用前输出 biz_key 隔离诊断，便于日志面板观察。
        if self._is_active_db_session(db):
            try:
                isolation_payload = dict(prompt_context.get("biz_key_isolation_log") or {})
                if isolation_payload:
                    isolation_payload.update(
                        {
                            "project_id": int(project_id),
                            "request_id": request_id,
                            "source": "generate_test_cases_json",
                        }
                    )
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            user_id=user_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(isolation_payload, ensure_ascii=False)}",
                        )
                    )
                    db.commit()
            except Exception as e:
                try:
                    db.rollback()
                except Exception:
                    pass
                print(f"Failed to emit biz key isolation log(json): {e}")

        # Calculate start number for IDs based on batch index
        start_id = batch_index * batch_size + 1
        stage_logs: list[dict[str, Any]] = []
        coverage_check_payload: dict[str, Any] | None = None
        raw_response_payload: Any = ""
        judge_summary_payload: dict[str, Any] = {}
        judge_decision_table_payload: list[dict[str, Any]] = []
        gen_diag_payload: dict[str, Any] = {}
        compression_event_payload: dict[str, Any] = {}
        review_decision_summary_payload: dict[str, Any] = {}
        review_decision_table_payload: list[dict[str, Any]] = []
        generation_summary_payload: dict[str, Any] = {}
        convergence_payload: dict[str, Any] = {}
        candidate_cases_before_judge: list[dict[str, Any]] = []
        final_cases_after_judge: list[dict[str, Any]] = []
        candidate_total_before_judge = 0
        final_case_count = 0
        empty_result_guard_triggered = False
        empty_result_stage = ""
        core_flow_backfill_apply_summary_payload: dict[str, Any] = {}
        core_flow_backfill_generation_result: dict[str, Any] = {}

        system_prompt = f"""
{base_prompt}

BATCH GENERATION INSTRUCTION (workflow-first):
This is batch #{batch_index + 1}.
Start the Test Case IDs from {start_id} (e.g., TC-{start_id:03d}).
Suggested batch size reference: about {batch_size} cases (not a hard target).
If additional cases add no coverage gain, stop instead of padding.
Prioritize completing the current module closed-loop first; do not cross-module jump for count balancing.
Return ONLY the JSON array.
"""
        self._emit_final_context_trace(
            db=db,
            project_id=project_id,
            user_id=user_id,
            request_id=request_id,
            context_result=context_result,
            gate_debug=gate_debug,
            fallback_reason=(context_result or {}).get("fallback_reason") if isinstance(context_result, dict) else "",
            abort_code="",
            compressed_chars=len(kb_context or ""),
        )
        normalized_generation_mode = str(generation_mode or "").strip().lower()
        use_pipeline = bool(multi_pass) or normalized_generation_mode in {
            "single_pass",
            "multi_pass",
            "biz_key_multi_pass",
        }
        if use_pipeline:
            # 中文注释：统一由 pipeline 根据 generation_mode 决定生成策略。
            multi_pass_result = run_multi_pass_generation(
                client=client,
                requirement=requirement,
                db=db,
                base_prompt=system_prompt,
                requirement_context=prompt_context.get("requirement_context") or requirement,
                current_biz_key=resolved_current_biz,
                expected_count=int(expected_count or batch_size or 1),
                start_id=start_id,
                clean_and_parse_json_fn=clean_and_parse_json,
                normalize_json_structure_fn=normalize_json_structure,
                deduplicate_test_cases_fn=deduplicate_test_cases,
                reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
                multi_pass=bool(multi_pass),
                generation_mode=generation_mode,
                prompt_context=prompt_context,
                build_base_prompt_fn=lambda req_ctx, req_sem_ctx, tc_ctx, sup_ctx, ctl_ctx, biz_key: build_closed_loop_base_prompt(
                    strategy_plan,
                    requirement_context=req_ctx,
                    requirement_semantics_context=req_sem_ctx,
                    testcase_context=tc_ctx,
                    supplement_context=sup_ctx,
                    control_context=ctl_ctx,
                    current_biz_key=biz_key,
                    doc_type=doc_type,
                    pretty_json=False,
                ),
            )
            result = multi_pass_result.get("final_cases") or []
            stage_logs = list(multi_pass_result.get("stage_logs") or [])
            coverage_check_payload = dict(multi_pass_result.get("coverage") or {})
            raw_response_payload = multi_pass_result.get("raw") or {}
        else:
            response = client.generate_response(
                requirement,
                system_prompt,
                db=db,
                task_type="generation",
            )
            raw_response_payload = response
            result = finalize_generated_cases(
                response,
                start_id=start_id,
                clean_and_parse_json_fn=clean_and_parse_json,
                normalize_json_structure_fn=normalize_json_structure,
                deduplicate_test_cases_fn=deduplicate_test_cases,
                reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            )
            stage_logs = [
                {"kind": "generation_mode", "mode": "single_pass", "biz_keys": [resolved_current_biz], "current_biz_key": resolved_current_biz},
                {"kind": "generation_stage", "stage": "primary", "case_count": len(result) if isinstance(result, list) else 0},
                {"kind": "generation_stage", "stage": "gap", "case_count": 0},
                {"kind": "generation_stage", "stage": "review", "case_count": len(result) if isinstance(result, list) else 0},
            ]
            if isinstance(result, list):
                coverage_check_payload = {
                    "kind": "coverage_check",
                    **analyze_coverage(prompt_context.get("requirement_context") or requirement, result),
                }

        if isinstance(result, list):
            candidate_cases_before_judge = [item for item in result if isinstance(item, dict)]
            candidate_total_before_judge = int(len(candidate_cases_before_judge))
            requirement_semantics_payload = {
                "confirmed_facts": [str(item).strip() for item in (prompt_context.get("confirmed_facts") or []) if str(item).strip()],
                "scoped_rules": [str(item).strip() for item in (prompt_context.get("scoped_rules") or []) if str(item).strip()],
                "pending_items": [str(item).strip() for item in (prompt_context.get("pending_items") or []) if str(item).strip()],
                "reuse_declarations": [str(item).strip() for item in (prompt_context.get("reuse_declarations") or []) if str(item).strip()],
                "hard_flow_constraints": [str(item).strip() for item in (prompt_context.get("hard_flow_constraints") or []) if str(item).strip()],
                "reuse_risks": [str(item).strip() for item in (prompt_context.get("reuse_risks") or []) if str(item).strip()],
            }
            judged = judge_cases(
                cases=candidate_cases_before_judge,
                requirement_semantics_context=requirement_semantics_payload,
                control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
            )
            repaired = repair_cases(
                judged=judged,
                requirement_semantics_context=requirement_semantics_payload,
                control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
                strategy="rule_first_llm_fallback",
            )
            confirmed_pass_cases, repaired_pass_cases, rejected_cases, pending_cases = training_gate(repaired)
            result = deduplicate_test_cases([*confirmed_pass_cases, *repaired_pass_cases])
            result = reorder_cases_by_closed_loop(
                result,
                start_id=start_id,
                renumber_ids=True,
            )
            final_cases_after_judge = [item for item in result if isinstance(item, dict)]
            final_case_count = int(len(final_cases_after_judge))
            if final_case_count <= 0:
                empty_result_guard_triggered = True
                empty_result_stage = "post_judge_training_gate"
                result = {
                    "error": "EMPTY_GENERATED_RESULT",
                    "error_code": "EMPTY_GENERATED_RESULT",
                    "error_message": "生成完成但最终测试用例为空",
                    "status": "failed",
                    "final_status": "empty_result_failed",
                    "empty_result_guard_triggered": True,
                    "empty_result_stage": empty_result_stage,
                    "candidate_total": int(candidate_total_before_judge),
                    "review_input_size": int(candidate_total_before_judge),
                    "review_output_size": 0,
                    "final_case_count": 0,
                }
            coverage_check_payload = {
                "kind": "coverage_check",
                **analyze_coverage(
                    prompt_context.get("requirement_context") or requirement,
                    result if isinstance(result, list) else [],
                ),
            }
            stage_primary = 0
            stage_gap = 0
            stage_review = 0
            for stage_log in stage_logs:
                if not isinstance(stage_log, dict):
                    continue
                if str(stage_log.get("kind") or "").strip() != "generation_stage":
                    continue
                stage = str(stage_log.get("stage") or "").strip().lower()
                count = int(stage_log.get("case_count") or 0)
                if stage == "primary":
                    stage_primary = count
                elif stage == "gap":
                    stage_gap = count
                elif stage == "review":
                    stage_review = count
            if stage_primary <= 0:
                stage_primary = int(candidate_total_before_judge)
            candidate_total = int(candidate_total_before_judge or stage_review or stage_primary)
            retained_total = int(final_case_count if not empty_result_guard_triggered else 0)
            dropped_total = max(0, int(candidate_total) - int(retained_total))
            review_decision_summary_payload = {
                "candidate_total": int(candidate_total),
                "retained_total": int(retained_total),
                "dropped_total": int(dropped_total),
                "drop_by_review_llm_count": 0,
                "drop_by_review_selector_count": 0,
                "drop_by_review_gate_count": int(dropped_total),
                "drop_by_pre_gate_dedup_count": 0,
                "drop_by_post_review_dedup_count": 0,
                "drop_no_new_signal_count": 0,
                "drop_rule_cap_count": 0,
                "review_input_size": int(candidate_total),
                "review_output_size": int(retained_total),
                "review_llm_filter_applied": False,
                "review_decision_summary_available": True,
                "review_skipped_reason": "",
                "reason_source_breakdown": {"primary": 0, "fallback": 0, "backfill": 0},
                "review_llm_drop_reason_source_breakdown": {"llm": 0, "fallback_llm": 0, "deterministic_backfill": 0},
                "priority_decision_state_breakdown": {"decided": 0, "conflict": 0, "undetermined": 0, "optional": 0, "invalid": 0},
                "priority_final_breakdown": {"P0": 0, "P1": 0, "P2": 0, "null": 0},
                "legacy_priority_breakdown": {"P0": 0, "P1": 0, "P2": 0, "UNKNOWN": 0},
                "priority_conflict_count": 0,
                "priority_undetermined_count": 0,
                "priority_optional_count": 0,
                "priority_invalid_count": 0,
                "needs_priority_review": False,
                "candidate_primary": int(stage_primary),
                "candidate_gap": int(stage_gap),
                "final_case_count": int(retained_total),
                "empty_result_guard_triggered": bool(empty_result_guard_triggered),
                "empty_result_stage": str(empty_result_stage),
                "llm_reason_coverage_ratio": 0.0,
                "deterministic_backfill_ratio": 0.0,
                "primary_reason_incomplete": True,
                "primary_dropped_reason_count": 0,
                "primary_dropped_reason_payload_count": 0,
                "primary_reason_coverage_ratio": 0.0,
                "fallback_reason_incomplete": False,
                "fallback_dropped_reason_count": 0,
                "fallback_dropped_reason_mapped_count": 0,
                "fallback_dropped_reason_unmapped_count": 0,
                "fallback_reason_coverage_ratio": 0.0,
                "review_llm_runtime_debug": {
                    "invoked": False,
                    "mapped_count": 0,
                    "dropped_reason_count": 0,
                    "payload_has_selection_signal": False,
                    "applied_reason": "judge_training_gate_path",
                    "primary_model": "",
                    "primary_invalid_reason": "review_not_invoked_generate_tests_json",
                    "retry_invoked": False,
                    "retry_reason": "",
                    "retry_model": "",
                    "retry_parse_success": False,
                    "retry_mapped_count": 0,
                    "retry_payload_has_selection_signal": False,
                    "final_source": "review_selector",
                    "final_dropped_reason_count": 0,
                    "final_dropped_reason_payload_count": 0,
                    "final_dropped_reason_unmapped_count": 0,
                    "final_reason_incomplete": False,
                },
            }
            for case_item in final_cases_after_judge:
                if not isinstance(case_item, dict):
                    continue
                priority_state = str(case_item.get("priority_decision_state") or "undetermined").strip().lower()
                if priority_state not in {"decided", "conflict", "undetermined", "optional", "invalid"}:
                    priority_state = "undetermined"
                review_decision_summary_payload["priority_decision_state_breakdown"][priority_state] += 1

                priority_final = str(case_item.get("priority_final") or "").strip().upper()
                if priority_final not in {"P0", "P1", "P2"}:
                    priority_final = "null"
                review_decision_summary_payload["priority_final_breakdown"][priority_final] += 1

                legacy_priority = str(case_item.get("legacy_priority") or case_item.get("priority") or "").strip().upper()
                if legacy_priority not in {"P0", "P1", "P2"}:
                    legacy_priority = "UNKNOWN"
                review_decision_summary_payload["legacy_priority_breakdown"][legacy_priority] += 1

            review_decision_summary_payload["priority_conflict_count"] = int(
                review_decision_summary_payload["priority_decision_state_breakdown"].get("conflict") or 0
            )
            review_decision_summary_payload["priority_undetermined_count"] = int(
                review_decision_summary_payload["priority_decision_state_breakdown"].get("undetermined") or 0
            )
            review_decision_summary_payload["priority_optional_count"] = int(
                review_decision_summary_payload["priority_decision_state_breakdown"].get("optional") or 0
            )
            review_decision_summary_payload["priority_invalid_count"] = int(
                review_decision_summary_payload["priority_decision_state_breakdown"].get("invalid") or 0
            )
            review_decision_summary_payload["needs_priority_review"] = bool(
                int(review_decision_summary_payload["priority_conflict_count"] or 0) > 0
                or int(review_decision_summary_payload["priority_undetermined_count"] or 0) > 0
            )
            generation_summary_payload = {
                "status": "failed_empty_result" if empty_result_guard_triggered else "completed",
                "final_status": "empty_result_failed" if empty_result_guard_triggered else "success",
                "final_count": int(retained_total),
                "expected_count": int(expected_count or 0),
                "candidate_total": int(candidate_total),
                "review_input_size": int(candidate_total),
                "review_output_size": int(retained_total),
                "needs_priority_review": bool(review_decision_summary_payload.get("needs_priority_review")),
                "review_decision_summary_available": True,
                "review_skipped_reason": "",
                "empty_result_guard_triggered": bool(empty_result_guard_triggered),
                "empty_result_stage": str(empty_result_stage),
            }
            if empty_result_guard_triggered:
                generation_summary_payload["error_code"] = "EMPTY_GENERATED_RESULT"
                generation_summary_payload["error_message"] = "生成完成但最终测试用例为空"
            convergence_payload = {
                "primary_count": int(stage_primary),
                "gap_count": int(stage_gap),
                "review_count": int(stage_review or candidate_total),
                "candidate_count_before_review": int(candidate_total),
                "review_selected_count": int(retained_total),
                "final_count": int(retained_total),
                "expected_count": int(expected_count or 0),
                "empty_result_guard_triggered": bool(empty_result_guard_triggered),
                "empty_result_stage": str(empty_result_stage),
            }
            judge_summary_payload = {
                "pass_count": int(repaired.pass_count or 0),
                "repairable_count": int(repaired.repairable_count or 0),
                "reject_count": int(repaired.reject_count or 0),
                "pending_count": int(repaired.pending_count or 0),
                "repaired_case_count": int(repaired.repaired_case_count or 0),
                "appended_case_count": int(repaired.appended_case_count or 0),
                "confirmed_pass_out_count": int(len(confirmed_pass_cases)),
                "repaired_pass_out_count": int(len(repaired_pass_cases)),
                "rejected_out_count": int(len(rejected_cases)),
                "pending_out_count": int(len(pending_cases)),
                "core_flow_covered": bool(repaired.core_flow_covered),
                "reuse_risk_covered": bool(repaired.reuse_risk_covered),
            }
            judge_decision_table_payload = []
            for judged_item in repaired.cases or []:
                signal_set = judged_item.signals
                before_case = judged_item.before_case if isinstance(judged_item.before_case, dict) else {}
                after_case = judged_item.after_case if isinstance(judged_item.after_case, dict) else {}
                signals_payload = {
                    "violates_confirmed_fact": bool(signal_set.violates_confirmed_fact),
                    "missing_core_flow": bool(signal_set.missing_core_flow),
                    "missing_reuse_risk": bool(signal_set.missing_reuse_risk),
                    "contains_pending_logic": bool(signal_set.contains_pending_logic),
                    "confirmed_fact_hits": [str(item) for item in (signal_set.confirmed_fact_hits or [])],
                    "confirmed_fact_violations": [
                        str(item) for item in (signal_set.confirmed_fact_violations or [])
                    ],
                    "reuse_risk_hits": [str(item) for item in (signal_set.reuse_risk_hits or [])],
                    "pending_hits": [str(item) for item in (signal_set.pending_hits or [])],
                    "vague_or_unconfirmed_hits": [
                        str(item) for item in (getattr(signal_set, "vague_or_unconfirmed_hits", []) or [])
                    ],
                }
                judge_decision_table_payload.append(
                    {
                        "case_id": str(judged_item.case_id or ""),
                        "status": str(getattr(judged_item.status, "value", judged_item.status)),
                        "reject_reason": str(judged_item.reject_reason or ""),
                        "pending_reason": str(judged_item.pending_reason or ""),
                        "signals": signals_payload,
                        "violates_confirmed_fact": bool(signals_payload.get("violates_confirmed_fact")),
                        "missing_core_flow": bool(signals_payload.get("missing_core_flow")),
                        "missing_reuse_risk": bool(signals_payload.get("missing_reuse_risk")),
                        "contains_pending_logic": bool(signals_payload.get("contains_pending_logic")),
                        "confirmed_fact_hits": list(signals_payload.get("confirmed_fact_hits") or []),
                        "confirmed_fact_violations": list(signals_payload.get("confirmed_fact_violations") or []),
                        "reuse_risk_hits": list(signals_payload.get("reuse_risk_hits") or []),
                        "pending_hits": list(signals_payload.get("pending_hits") or []),
                        "vague_or_unconfirmed_hits": list(
                            signals_payload.get("vague_or_unconfirmed_hits") or []
                        ),
                        "before_case_snapshot": dict(before_case),
                        "after_case_snapshot": dict(after_case),
                    }
                )

            def _normalize_case_text(value: Any) -> str:
                return " ".join(str(value or "").strip().lower().split())

            def _normalize_case_steps(value: Any) -> str:
                if isinstance(value, list):
                    return " | ".join(_normalize_case_text(item) for item in value if str(item or "").strip())
                return _normalize_case_text(value)

            def _build_case_signature(case_payload: dict[str, Any]) -> str:
                if not isinstance(case_payload, dict):
                    return ""
                return "||".join(
                    [
                        _normalize_case_text(case_payload.get("test_module")),
                        _normalize_case_text(case_payload.get("description")),
                        _normalize_case_steps(case_payload.get("steps")),
                        _normalize_case_text(case_payload.get("test_input")),
                        _normalize_case_text(case_payload.get("expected_result")),
                    ]
                )

            final_signature_counts: dict[str, int] = {}
            for case_payload in final_cases_after_judge:
                signature = _build_case_signature(case_payload)
                if not signature:
                    continue
                final_signature_counts[signature] = int(final_signature_counts.get(signature) or 0) + 1

            judge_by_case_id: dict[str, dict[str, Any]] = {}
            judge_reject_pending_by_signature: dict[str, list[dict[str, Any]]] = {}
            for judge_row in judge_decision_table_payload:
                if not isinstance(judge_row, dict):
                    continue
                judge_case_id = str(judge_row.get("case_id") or "").strip()
                if judge_case_id:
                    judge_by_case_id[judge_case_id] = judge_row
                judge_status = str(judge_row.get("status") or judge_row.get("judge_status") or "").strip().upper()
                if judge_status not in {"REJECT", "PENDING"}:
                    continue
                before_case_snapshot = judge_row.get("before_case_snapshot")
                if not isinstance(before_case_snapshot, dict):
                    before_case_snapshot = {}
                signature = _build_case_signature(before_case_snapshot)
                if not signature:
                    continue
                judge_reject_pending_by_signature.setdefault(signature, []).append(judge_row)

            review_decision_table_payload = []
            dropped_by_gate_count = 0
            dropped_by_post_dedup_count = 0
            for candidate_index, case_item in enumerate(candidate_cases_before_judge, start=1):
                if not isinstance(case_item, dict):
                    continue
                candidate_signature = _build_case_signature(case_item)
                retained_final = False
                if candidate_signature and int(final_signature_counts.get(candidate_signature) or 0) > 0:
                    retained_final = True
                    final_signature_counts[candidate_signature] = int(final_signature_counts.get(candidate_signature) or 0) - 1

                case_id = str(case_item.get("id") or case_item.get("case_id") or "").strip() or f"ROW-{candidate_index:03d}"
                model_priority = str(
                    case_item.get("model_priority_current")
                    or case_item.get("model_priority")
                    or case_item.get("priority")
                    or ""
                ).strip().upper()
                legacy_priority = str(case_item.get("legacy_priority") or case_item.get("priority") or "").strip().upper()
                priority_state = str(case_item.get("priority_decision_state") or "undetermined").strip().lower()
                if priority_state not in {"decided", "conflict", "undetermined", "optional", "invalid"}:
                    priority_state = "undetermined"
                priority_final = str(case_item.get("priority_final") or "").strip().upper()
                if priority_final not in {"P0", "P1", "P2"}:
                    priority_final = ""

                dropped_stage = "retained"
                dropped_reason = "retained"
                retained_reason = "retained_after_judge"
                judge_ref: dict[str, Any] = {}
                if not retained_final:
                    judge_ref = dict(judge_by_case_id.get(case_id) or {})
                    if not judge_ref and candidate_signature:
                        signature_rows = judge_reject_pending_by_signature.get(candidate_signature) or []
                        if signature_rows:
                            judge_ref = dict(signature_rows.pop(0) or {})
                    judge_status = str(judge_ref.get("status") or judge_ref.get("judge_status") or "").strip().upper()
                    if judge_status in {"REJECT", "PENDING"}:
                        dropped_stage = "review_gate"
                        dropped_reason = str(
                            judge_ref.get("reject_reason")
                            or judge_ref.get("pending_reason")
                            or judge_status.lower()
                        ).strip() or judge_status.lower()
                        dropped_by_gate_count += 1
                    else:
                        dropped_stage = "post_review_dedup"
                        dropped_reason = "post_judge_dedup_or_merge"
                        dropped_by_post_dedup_count += 1
                    retained_reason = ""

                review_decision_table_payload.append(
                    {
                        "candidate_index": int(candidate_index),
                        "case_id": case_id,
                        "description": str(case_item.get("description") or "").strip(),
                        "test_module": str(case_item.get("test_module") or "").strip(),
                        "model_priority": model_priority,
                        "model_priority_current": model_priority,
                        "legacy_priority": legacy_priority if legacy_priority in {"P0", "P1", "P2"} else "UNKNOWN",
                        "priority_final": priority_final or "",
                        "priority_decision_state": priority_state,
                        "priority_decision_source": str(case_item.get("priority_decision_source") or "").strip(),
                        "priority_confidence": str(case_item.get("priority_confidence") or "").strip(),
                        "priority_conflict_reason": str(case_item.get("priority_conflict_reason") or "").strip(),
                        "priority_score": case_item.get("priority_score"),
                        "suggested_priority": str(case_item.get("suggested_priority") or "").strip(),
                        "priority_reasons": case_item.get("priority_reasons") if isinstance(case_item.get("priority_reasons"), list) else [],
                        "selected_by_review_llm": False,
                        "selected_by_review_must_keep": False,
                        "selected_by_review_constraints": False,
                        "selected_by_review_gate": bool(not retained_final and dropped_stage == "review_gate"),
                        "retained_final": bool(retained_final),
                        "dropped_stage": dropped_stage,
                        "dropped_reason": dropped_reason,
                        "review_llm_drop_reason_raw": "",
                        "review_llm_drop_reason": "",
                        "review_llm_drop_reason_source": "",
                        "review_llm_drop_reason_evidence": {},
                        "has_positive_evidence": False,
                        "has_coverage_signal": False,
                        "has_high_signal": False,
                        "has_competition_signal": False,
                        "review_constraint_reason": "",
                        "bucket": str(case_item.get("bucket") or "").strip(),
                        "rule_keys": list(case_item.get("rule_keys") or []) if isinstance(case_item.get("rule_keys"), list) else [],
                        "adds_rule": bool(case_item.get("adds_rule")),
                        "adds_bucket": bool(case_item.get("adds_bucket")),
                        "high_signal": bool(case_item.get("high_signal")),
                        "has_coverage_value": bool(case_item.get("has_coverage_value")),
                        "retained_reason": retained_reason,
                        "rerank_rank": case_item.get("rerank_rank") or "",
                        "focus_score": case_item.get("focus_score") or "",
                        "covered_rule_ids": list(case_item.get("covered_rule_ids") or []) if isinstance(case_item.get("covered_rule_ids"), list) else [],
                        "missing_rule_hits": list(case_item.get("missing_rule_hits") or []) if isinstance(case_item.get("missing_rule_hits"), list) else [],
                        "core_rule_hits": list(case_item.get("core_rule_hits") or []) if isinstance(case_item.get("core_rule_hits"), list) else [],
                        "coverage_gain_score": case_item.get("coverage_gain_score") or "",
                        "signature": candidate_signature,
                    }
                )

            review_decision_summary_payload["drop_by_review_gate_count"] = int(dropped_by_gate_count)
            review_decision_summary_payload["drop_by_post_review_dedup_count"] = int(dropped_by_post_dedup_count)
            
        # Save to DB if session provided
        if db:
            try:
                if self._is_active_db_session(db):
                    for stage_log in stage_logs:
                        payload = dict(stage_log)
                        payload.update(
                            {
                                "request_id": request_id,
                                "multi_pass": bool(multi_pass),
                                "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                            }
                        )
                        db.add(
                            LogEntry(
                                project_id=project_id,
                                user_id=user_id,
                                log_type="system",
                                message=f"GEN_DIAG:{json.dumps(payload, ensure_ascii=False)}",
                            )
                        )
                    if coverage_check_payload:
                        coverage_payload = dict(coverage_check_payload)
                        coverage_payload.update(
                            {
                                "request_id": request_id,
                                "multi_pass": bool(multi_pass),
                                "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                            }
                        )
                        db.add(
                            LogEntry(
                                project_id=project_id,
                                user_id=user_id,
                                log_type="system",
                                message=f"GEN_DIAG:{json.dumps(coverage_payload, ensure_ascii=False)}",
                            )
                        )
                    if feedback_control_diag_payload:
                        control_diag_payload = {
                            "kind": "feedback_control_state",
                            **dict(feedback_control_diag_payload),
                            "request_id": request_id,
                            "multi_pass": bool(multi_pass),
                            "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                        }
                        db.add(
                            LogEntry(
                                project_id=project_id,
                                user_id=user_id,
                                log_type="system",
                                message=f"GEN_DIAG:{json.dumps(control_diag_payload, ensure_ascii=False)}",
                            )
                        )
                    if judge_summary_payload:
                        judge_diag_payload = {
                            "kind": "judge_summary",
                            **dict(judge_summary_payload),
                            "request_id": request_id,
                            "multi_pass": bool(multi_pass),
                            "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                        }
                        db.add(
                            LogEntry(
                                project_id=project_id,
                                user_id=user_id,
                                log_type="system",
                                message=f"GEN_DIAG:{json.dumps(judge_diag_payload, ensure_ascii=False)}",
                            )
                        )
                    if memory_diag:
                        memory_diag_payload = {
                            "kind": "memory_fabric_diag",
                            **dict(memory_diag),
                            "request_id": request_id,
                            "multi_pass": bool(multi_pass),
                            "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                        }
                        db.add(
                            LogEntry(
                                project_id=project_id,
                                user_id=user_id,
                                log_type="system",
                                message=f"GEN_DIAG:{json.dumps(memory_diag_payload, ensure_ascii=False)}",
                            )
                        )
                    full_input = (system_prompt or "") + requirement
                    actual_model = client.select_model(full_input, task_type="generation")
                    compression_diag_payload = build_context_compression_diagnostics(
                        context_result=context_result if isinstance(context_result, dict) else {},
                    )
                    gen_diag_payload = {
                        "kind": "gen_diag",
                        "mode": "json",
                        "doc_type": doc_type,
                        "compress": bool(compress),
                        "expected_count": int(expected_count or 0),
                        "generated_count": int(count_unique_test_cases(result)) if isinstance(result, list) else 0,
                        "content_length": len(requirement or ""),
                        "kb_length": len(kb_context or ""),
                        "model": actual_model,
                        "max_tokens": client.max_tokens,
                        "request_id": request_id,
                        "multi_pass": bool(multi_pass),
                        "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                        "context_compression_ratio": compression_diag_payload.get("compression_ratio"),
                        "context_retained_chunk_count": compression_diag_payload.get("retained_chunk_count"),
                        "context_relevance_distribution": compression_diag_payload.get("relevance_distribution") or {},
                    }
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            user_id=user_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(gen_diag_payload, ensure_ascii=False)}",
                        )
                    )
                    compression_event_payload = {
                        "kind": "generation_context_compression",
                        **compression_diag_payload,
                        "request_id": request_id,
                        "multi_pass": bool(multi_pass),
                        "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    }
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            user_id=user_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(compression_event_payload, ensure_ascii=False)}",
                        )
                    )
                    db.commit()

                from core.settings.config import settings
                from modules.test_generation_components.coverage.core_flow_coverage_contract import (
                    audit_core_flow_coverage,
                )
                from modules.test_generation_components.coverage.core_flow_backfill import (
                    plan_core_flow_backfill,
                )
                from modules.test_generation_components.coverage.core_flow_backfill_generation import (
                    generate_core_flow_backfill_candidates,
                    summarize_case_quality_gate,
                )

                if isinstance(result, list):
                    backfill_enabled = bool(getattr(settings, "CORE_FLOW_BACKFILL_ENABLED", False))
                    backfill_apply_to_final = bool(getattr(settings, "CORE_FLOW_BACKFILL_APPLY_TO_FINAL", False))
                    backfill_max_candidates = int(getattr(settings, "CORE_FLOW_BACKFILL_MAX_CANDIDATES", 12) or 12)
                    backfill_min_final_cases = int(getattr(settings, "CORE_FLOW_BACKFILL_MIN_FINAL_CASES", 12) or 12)
                    backfill_max_final_cases = int(getattr(settings, "CORE_FLOW_BACKFILL_MAX_FINAL_CASES", 18) or 18)
                    backfill_min_coverage_ratio = float(getattr(settings, "CORE_FLOW_BACKFILL_MIN_COVERAGE_RATIO", 0.8) or 0.8)
                    backfill_applied = False
                    apply_skip_reason = ""
                    final_quality_gate_passed = True
                    primary_case_count_before_backfill = int(len([x for x in result if isinstance(x, dict)]))
                    core_flow_coverage_before_apply = audit_core_flow_coverage([x for x in result if isinstance(x, dict)])
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
                            existing_cases=[x for x in result if isinstance(x, dict)],
                            coverage_audit=core_flow_coverage_before_apply,
                            max_backfill_cases=backfill_max_candidates,
                        )
                        backfill_plan["project_id"] = int(project_id)
                        backfill_plan["user_id"] = int(user_id or 0)
                        backfill_plan["request_id"] = request_id
                        backfill_plan["generation_mode"] = normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass")
                        core_flow_backfill_generation_result = generate_core_flow_backfill_candidates(
                            requirement_context=requirement,
                            existing_cases=[x for x in result if isinstance(x, dict)],
                            backfill_plan=backfill_plan,
                            db=db,
                            llm_client=client,
                            max_candidates=backfill_max_candidates,
                            preview_min_total=backfill_min_final_cases,
                            preview_max_total=backfill_max_final_cases,
                        )
                        merged_preview_cases = [
                            x for x in (core_flow_backfill_generation_result.get("merged_preview_cases") or []) if isinstance(x, dict)
                        ]
                        merged_preview_cases = _normalize_missing_priority_final_cases(
                            merged_preview_cases,
                            requirement_text=requirement,
                        )
                        merged_quality_gate = summarize_case_quality_gate(merged_preview_cases)
                        merged_quality_gate = merge_contract_quality_gate(
                            merged_quality_gate,
                            summarize_persistable_case_contract(merged_preview_cases),
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
                            result = merged_preview_cases
                            final_cases_after_judge = [x for x in result if isinstance(x, dict)]
                            final_case_count = int(len(final_cases_after_judge))
                            backfill_applied = True
                            apply_skip_reason = ""

                        if backfill_apply_to_final and not final_quality_gate_passed:
                            core_flow_backfill_apply_summary_payload = {
                                "kind": "core_flow_backfill_apply_summary",
                                "request_id": request_id,
                                "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                                "backfill_enabled": bool(backfill_enabled),
                                "backfill_apply_to_final": bool(backfill_apply_to_final),
                                "backfill_applied": bool(backfill_applied),
                                "primary_case_count": int(primary_case_count_before_backfill),
                                "final_case_count": int(len([x for x in result if isinstance(x, dict)])) if isinstance(result, list) else 0,
                                "generated_backfill_candidate_count": int(len(core_flow_backfill_generation_result.get("generated_backfill_candidate_cases") or [])),
                                "accepted_backfill_candidate_count": int(len(core_flow_backfill_generation_result.get("accepted_backfill_cases") or [])),
                                "rejected_backfill_candidate_count": int(len(core_flow_backfill_generation_result.get("rejected_backfill_cases") or [])),
                                "accepted_for_preview_count": int(core_flow_backfill_generation_result.get("accepted_for_preview_count") or 0),
                                "primary_retained_count": int(core_flow_backfill_generation_result.get("primary_retained_count") or 0),
                                "primary_trimmed_count": int(core_flow_backfill_generation_result.get("primary_trimmed_count") or 0),
                                "backfill_retained_count": int(core_flow_backfill_generation_result.get("backfill_retained_count") or 0),
                                "backfill_trimmed_count": int(core_flow_backfill_generation_result.get("backfill_trimmed_count") or 0),
                                "coverage_before": {
                                    "covered_count": int(core_flow_coverage_before_apply.get("core_flow_covered_count") or 0),
                                    "required_count": int(core_flow_coverage_before_apply.get("core_flow_required_count") or 0),
                                    "coverage_ratio": float(core_flow_coverage_before_apply.get("core_flow_coverage_ratio") or 0.0),
                                },
                                "coverage_after": {
                                    "covered_count": int(core_flow_coverage_after_apply.get("core_flow_covered_count") or 0),
                                    "required_count": int(core_flow_coverage_after_apply.get("core_flow_required_count") or 0),
                                    "coverage_ratio": float(core_flow_coverage_after_apply.get("core_flow_coverage_ratio") or 0.0),
                                },
                                "still_missing_core_flows": list(core_flow_still_missing_after_apply),
                                "final_quality_gate_passed": bool(final_quality_gate_passed),
                                "apply_skip_reason": str(apply_skip_reason or ""),
                            }
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
                                **{k: v for k, v in merged_quality_gate.items() if k != "passed"},
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
                            return {
                                "error": "LOW_QUALITY_GENERATED_CASES",
                                "error_code": "LOW_QUALITY_GENERATED_CASES",
                                "error_message": "merged backfill result failed quality gate or coverage threshold",
                                "final_status": "quality_gate_failed",
                                "quality_gate_failed": True,
                                **{k: v for k, v in merged_quality_gate.items() if k != "passed"},
                                "apply_skip_reason": apply_skip_reason,
                                "core_flow_coverage_ratio": float(core_flow_coverage_after_apply.get("core_flow_coverage_ratio") or 0.0),
                                "core_flow_min_required_ratio": float(backfill_min_coverage_ratio),
                            }

                    core_flow_backfill_apply_summary_payload = {
                        "kind": "core_flow_backfill_apply_summary",
                        "request_id": request_id,
                        "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                        "backfill_enabled": bool(backfill_enabled),
                        "backfill_apply_to_final": bool(backfill_apply_to_final),
                        "backfill_applied": bool(backfill_applied),
                        "primary_case_count": int(primary_case_count_before_backfill),
                        "final_case_count": int(len([x for x in result if isinstance(x, dict)])) if isinstance(result, list) else 0,
                        "generated_backfill_candidate_count": int(len(core_flow_backfill_generation_result.get("generated_backfill_candidate_cases") or [])),
                        "accepted_backfill_candidate_count": int(len(core_flow_backfill_generation_result.get("accepted_backfill_cases") or [])),
                        "rejected_backfill_candidate_count": int(len(core_flow_backfill_generation_result.get("rejected_backfill_cases") or [])),
                        "accepted_for_preview_count": int(core_flow_backfill_generation_result.get("accepted_for_preview_count") or 0),
                        "primary_retained_count": int(core_flow_backfill_generation_result.get("primary_retained_count") or 0),
                        "primary_trimmed_count": int(core_flow_backfill_generation_result.get("primary_trimmed_count") or 0),
                        "backfill_retained_count": int(core_flow_backfill_generation_result.get("backfill_retained_count") or 0),
                        "backfill_trimmed_count": int(core_flow_backfill_generation_result.get("backfill_trimmed_count") or 0),
                        "coverage_before": {
                            "covered_count": int(core_flow_coverage_before_apply.get("core_flow_covered_count") or 0),
                            "required_count": int(core_flow_coverage_before_apply.get("core_flow_required_count") or 0),
                            "coverage_ratio": float(core_flow_coverage_before_apply.get("core_flow_coverage_ratio") or 0.0),
                        },
                        "coverage_after": {
                            "covered_count": int(core_flow_coverage_after_apply.get("core_flow_covered_count") or 0),
                            "required_count": int(core_flow_coverage_after_apply.get("core_flow_required_count") or 0),
                            "coverage_ratio": float(core_flow_coverage_after_apply.get("core_flow_coverage_ratio") or 0.0),
                        },
                        "still_missing_core_flows": list(core_flow_still_missing_after_apply),
                        "final_quality_gate_passed": bool(final_quality_gate_passed),
                        "apply_skip_reason": str(apply_skip_reason or ""),
                    }
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            user_id=user_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(core_flow_backfill_apply_summary_payload, ensure_ascii=False)}",
                        )
                    )
                    db.commit()

                    generation_summary_payload["core_flow_backfill_enabled"] = bool(backfill_enabled)
                    generation_summary_payload["core_flow_backfill_applied"] = bool(backfill_applied)
                    generation_summary_payload["primary_case_count_before_backfill"] = int(primary_case_count_before_backfill)
                    generation_summary_payload["final_count"] = int(len([x for x in result if isinstance(x, dict)])) if isinstance(result, list) else 0
                    generation_summary_payload["final_case_count_after_backfill"] = int(len([x for x in result if isinstance(x, dict)])) if isinstance(result, list) else 0
                    generation_summary_payload["core_flow_coverage_before"] = float(core_flow_coverage_before_apply.get("core_flow_coverage_ratio") or 0.0)
                    generation_summary_payload["core_flow_coverage_after"] = float(core_flow_coverage_after_apply.get("core_flow_coverage_ratio") or 0.0)
                    generation_summary_payload["core_flow_still_missing_count"] = int(len(core_flow_still_missing_after_apply))

                if isinstance(result, list):
                    result = _normalize_missing_priority_final_cases(result, requirement_text=requirement)
                quality_gate_result = summarize_case_quality_gate(result if isinstance(result, list) else [])
                quality_gate_result = merge_contract_quality_gate(
                    quality_gate_result,
                    summarize_persistable_case_contract(result),
                )
                quality_gate_result = summarize_persistence_case_quality_gate(
                    quality_gate_result,
                    generation_summary=generation_summary_payload,
                    review_decision_summary=review_decision_summary_payload,
                    judge_summary=judge_summary_payload,
                    settings=settings,
                )
                priority_final_invalid_case_ids = list(quality_gate_result.get("invalid_priority_final_case_ids") or [])
                invalid_priority_final_count = int(quality_gate_result.get("invalid_priority_final_count") or len(priority_final_invalid_case_ids))
                priority_final_null_count = int(quality_gate_result.get("priority_final_null_count") or 0)
                non_assertable_case_ids = list(quality_gate_result.get("non_assertable_case_ids") or [])
                truncated_case_ids = list(quality_gate_result.get("truncated_case_ids") or [])
                non_assertable_expected_result_count = int(quality_gate_result.get("non_assertable_expected_result_count") or 0)
                truncated_text_count = int(quality_gate_result.get("truncated_text_count") or 0)
                failed_checks: list[str] = list(quality_gate_result.get("failed_checks") or [])

                workflow_blueprints = [
                    dict(item)
                    for item in (feedback_control_state.get("workflow_blueprints") or [])
                    if isinstance(item, dict)
                ] if isinstance(feedback_control_state, dict) else []
                persistence_cases = project_persistable_cases(result)
                persistence_gate_result = evaluate_persistence_gate(
                    persistence_cases,
                    workflow_blueprints=workflow_blueprints,
                    execution_plan={},
                    generation_mode=normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    quality_gate=quality_gate_result,
                    settings=settings,
                )
                persistence_gate_diag = build_persistence_gate_diagnostic(persistence_gate_result)
                persistence_gate_diag["request_id"] = request_id
                persistence_gate_diag["project_id"] = int(project_id)
                db.add(
                    LogEntry(
                        project_id=project_id,
                        user_id=user_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(persistence_gate_diag, ensure_ascii=False)}",
                    )
                )
                db.commit()

                persistence_failure_code = str(persistence_gate_result.get("failure_code") or "")
                if persistence_failure_code == "EMPTY_GENERATED_RESULT":
                    return {
                        "error": "EMPTY_GENERATED_RESULT",
                        "error_code": "EMPTY_GENERATED_RESULT",
                        "error_message": "生成完成但最终测试用例为空",
                        "status": "failed",
                        "final_status": "empty_result_failed",
                        "empty_result_guard_triggered": True,
                        "empty_result_stage": "persistence_gate",
                    }
                if persistence_failure_code == "LOW_QUALITY_GENERATED_CASES":
                    quality_gate_payload = {
                        "kind": "generation_quality_gate",
                        "request_id": request_id,
                        "multi_pass": bool(multi_pass),
                        "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                        "error_code": "LOW_QUALITY_GENERATED_CASES",
                        "final_status": "quality_gate_failed",
                        "quality_gate_failed": True,
                        "priority_final_null_count": int(priority_final_null_count),
                        "invalid_priority_final_count": int(invalid_priority_final_count),
                        "invalid_priority_final_case_ids": list(priority_final_invalid_case_ids),
                        "non_assertable_expected_result_count": int(non_assertable_expected_result_count),
                        "truncated_text_count": int(truncated_text_count),
                        "non_assertable_case_ids": list(non_assertable_case_ids),
                        "truncated_case_ids": list(truncated_case_ids),
                        "persistable_required_field_missing_case_ids": list(quality_gate_result.get("persistable_required_field_missing_case_ids") or []),
                        "persistable_priority_final_invalid_case_ids": list(quality_gate_result.get("persistable_priority_final_invalid_case_ids") or []),
                        "persistable_reasoning_leakage_case_ids": list(quality_gate_result.get("persistable_reasoning_leakage_case_ids") or []),
                        "failed_checks": list(failed_checks),
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
                    return {
                        "error": "LOW_QUALITY_GENERATED_CASES",
                        "error_code": "LOW_QUALITY_GENERATED_CASES",
                        "error_message": "生成结果未通过质量门禁：存在不可执行或截断的预期结果",
                        "final_status": "quality_gate_failed",
                        "quality_gate_failed": True,
                        "failed_checks": list(failed_checks),
                        "priority_final_null_count": int(priority_final_null_count),
                        "invalid_priority_final_count": int(invalid_priority_final_count),
                        "invalid_priority_final_case_ids": list(priority_final_invalid_case_ids),
                        "non_assertable_expected_result_count": int(non_assertable_expected_result_count),
                        "truncated_text_count": int(truncated_text_count),
                        "non_assertable_case_ids": list(non_assertable_case_ids),
                        "truncated_case_ids": list(truncated_case_ids),
                        "persistable_required_field_missing_case_ids": list(quality_gate_result.get("persistable_required_field_missing_case_ids") or []),
                        "persistable_priority_final_invalid_case_ids": list(quality_gate_result.get("persistable_priority_final_invalid_case_ids") or []),
                        "persistable_reasoning_leakage_case_ids": list(quality_gate_result.get("persistable_reasoning_leakage_case_ids") or []),
                    }

                if not bool(persistence_gate_result.get("passed")):
                    execution_validation = dict(persistence_gate_result.get("execution_plan_validation") or {})
                    return {
                        "error": "execution_plan_failed",
                        "error_code": "execution_plan_failed",
                        "error_message": "生成结果未通过执行计划门禁",
                        "final_status": "execution_plan_failed",
                        "persistence_gate_failed": True,
                        "failure_reasons": list(execution_validation.get("failure_reasons") or []),
                        "metrics": dict(execution_validation.get("metrics") or {}),
                        "state_conflicts": list(execution_validation.get("state_conflicts") or []),
                    }
                result = project_persistable_cases(
                    persistence_gate_result.get("cases") if isinstance(persistence_gate_result.get("cases"), list) else []
                )

                db_entry = TestGeneration(
                    requirement_text=original_requirement,
                    generated_result=json.dumps(result, ensure_ascii=False)
                    if not (isinstance(result, dict) and ("error" in result))
                    else json.dumps({"error": result, "raw": raw_response_payload}, ensure_ascii=False),
                    project_id=project_id,
                    user_id=user_id
                )
                db.add(db_entry)
                db.commit()
                db.refresh(db_entry)
                persisted_generation_id = int(db_entry.id or 0)

                persisted_payload = {
                    "kind": "generation_persisted",
                    "generation_id": int(persisted_generation_id),
                    "project_id": int(project_id),
                    "request_id": request_id,
                }
                db.add(
                    LogEntry(
                        project_id=project_id,
                        user_id=user_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(persisted_payload, ensure_ascii=False)}",
                    )
                )

                generation_mode_payload = {
                    "kind": "generation_mode",
                    "mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    "biz_keys": [resolved_current_biz],
                    "current_biz_key": resolved_current_biz,
                    "multi_pass": bool(multi_pass),
                    "request_id": request_id,
                    "generation_id": int(persisted_generation_id),
                }
                db.add(
                    LogEntry(
                        project_id=project_id,
                        user_id=user_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(generation_mode_payload, ensure_ascii=False)}",
                    )
                )

                post_persist_gen_diag_payload = dict(gen_diag_payload or {})
                if not post_persist_gen_diag_payload:
                    post_persist_gen_diag_payload = {
                        "kind": "gen_diag",
                        "mode": "json",
                        "doc_type": doc_type,
                        "compress": bool(compress),
                        "expected_count": int(expected_count or 0),
                        "generated_count": int(count_unique_test_cases(result)) if isinstance(result, list) else 0,
                        "request_id": request_id,
                        "multi_pass": bool(multi_pass),
                        "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    }
                post_persist_gen_diag_payload["generation_id"] = int(persisted_generation_id)
                db.add(
                    LogEntry(
                        project_id=project_id,
                        user_id=user_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(post_persist_gen_diag_payload, ensure_ascii=False)}",
                    )
                )

                post_persist_compression_payload = dict(compression_event_payload or {})
                if not post_persist_compression_payload:
                    post_persist_compression_payload = {
                        "kind": "generation_context_compression",
                        "request_id": request_id,
                        "multi_pass": bool(multi_pass),
                        "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                        "snapshot_id": "",
                        "corpus_hash": "",
                        "retrieval_hash": "",
                    }
                post_persist_compression_payload["generation_id"] = int(persisted_generation_id)
                db.add(
                    LogEntry(
                        project_id=project_id,
                        user_id=user_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(post_persist_compression_payload, ensure_ascii=False)}",
                    )
                )

                if not review_decision_summary_payload:
                    review_decision_summary_payload = {
                        "candidate_total": int(candidate_total_before_judge or 0),
                        "retained_total": int(final_case_count if isinstance(result, list) else 0),
                        "dropped_total": max(
                            0,
                            int(candidate_total_before_judge or 0)
                            - int(final_case_count if isinstance(result, list) else 0),
                        ),
                        "review_input_size": int(candidate_total_before_judge or 0),
                        "review_output_size": int(final_case_count if isinstance(result, list) else 0),
                        "review_decision_summary_available": False,
                        "review_skipped_reason": "review_postprocess_not_executed_in_generate_tests_json",
                        "reason_source_breakdown": {"primary": 0, "fallback": 0, "backfill": 0},
                        "priority_decision_state_breakdown": {
                            "decided": 0,
                            "conflict": 0,
                            "undetermined": 0,
                            "optional": 0,
                            "invalid": 0,
                        },
                        "priority_final_breakdown": {"P0": 0, "P1": 0, "P2": 0, "null": 0},
                        "legacy_priority_breakdown": {"P0": 0, "P1": 0, "P2": 0, "UNKNOWN": 0},
                        "priority_conflict_count": 0,
                        "priority_undetermined_count": 0,
                        "priority_optional_count": 0,
                        "priority_invalid_count": 0,
                        "needs_priority_review": False,
                        "candidate_primary": int(candidate_total_before_judge or 0),
                        "candidate_gap": 0,
                        "final_case_count": int(final_case_count if isinstance(result, list) else 0),
                        "empty_result_guard_triggered": bool(empty_result_guard_triggered),
                        "empty_result_stage": str(empty_result_stage or ""),
                    }

                review_summary_diag_payload = {
                    "kind": "review_decision_summary",
                    **dict(review_decision_summary_payload or {}),
                    "request_id": request_id,
                    "generation_id": int(persisted_generation_id),
                    "multi_pass": bool(multi_pass),
                    "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                }
                db.add(
                    LogEntry(
                        project_id=project_id,
                        user_id=user_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(review_summary_diag_payload, ensure_ascii=False)}",
                    )
                )

                if review_decision_table_payload:
                    review_table_diag_payload = {
                        "kind": "review_decision_table",
                        "generation_id": int(persisted_generation_id),
                        "request_id": request_id,
                        "rows": [item for item in review_decision_table_payload if isinstance(item, dict)],
                        "row_count": int(len([item for item in review_decision_table_payload if isinstance(item, dict)])),
                        "row_count_total": int(len([item for item in review_decision_table_payload if isinstance(item, dict)])),
                        "rows_scope": "all",
                        "multi_pass": bool(multi_pass),
                        "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    }
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            user_id=user_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(review_table_diag_payload, ensure_ascii=False)}",
                        )
                    )

                if not convergence_payload:
                    convergence_payload = {
                        "primary_count": int(candidate_total_before_judge or 0),
                        "gap_count": 0,
                        "review_count": int(candidate_total_before_judge or 0),
                        "candidate_count_before_review": int(candidate_total_before_judge or 0),
                        "review_selected_count": int(final_case_count if isinstance(result, list) else 0),
                        "final_count": int(final_case_count if isinstance(result, list) else 0),
                        "expected_count": int(expected_count or 0),
                        "empty_result_guard_triggered": bool(empty_result_guard_triggered),
                        "empty_result_stage": str(empty_result_stage or ""),
                    }

                convergence_diag_payload = {
                    "kind": "generation_convergence",
                    **dict(convergence_payload or {}),
                    "request_id": request_id,
                    "generation_id": int(persisted_generation_id),
                    "multi_pass": bool(multi_pass),
                    "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                }
                db.add(
                    LogEntry(
                        project_id=project_id,
                        user_id=user_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(convergence_diag_payload, ensure_ascii=False)}",
                    )
                )

                if not generation_summary_payload:
                    generation_summary_payload = {
                        "status": "failed_empty_result" if bool(empty_result_guard_triggered) else "completed",
                        "final_status": "empty_result_failed" if bool(empty_result_guard_triggered) else "success",
                        "final_count": int(final_case_count if isinstance(result, list) else 0),
                        "expected_count": int(expected_count or 0),
                        "candidate_total": int(candidate_total_before_judge or 0),
                        "review_input_size": int(candidate_total_before_judge or 0),
                        "review_output_size": int(final_case_count if isinstance(result, list) else 0),
                        "review_decision_summary_available": False,
                        "review_skipped_reason": "review_postprocess_not_executed_in_generate_tests_json",
                        "empty_result_guard_triggered": bool(empty_result_guard_triggered),
                        "empty_result_stage": str(empty_result_stage or ""),
                    }
                    if bool(empty_result_guard_triggered):
                        generation_summary_payload["error_code"] = "EMPTY_GENERATED_RESULT"
                        generation_summary_payload["error_message"] = "生成完成但最终测试用例为空"

                generation_summary_diag_payload = {
                    "kind": "generation_summary",
                    **dict(generation_summary_payload or {}),
                    "request_id": request_id,
                    "generation_id": int(persisted_generation_id),
                    "multi_pass": bool(multi_pass),
                    "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                }
                db.add(
                    LogEntry(
                        project_id=project_id,
                        user_id=user_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(generation_summary_diag_payload, ensure_ascii=False)}",
                    )
                )
                db.commit()

                normalized_judge_rows: list[dict[str, Any]] = []
                for row in (judge_decision_table_payload or []):
                    if not isinstance(row, dict):
                        continue
                    status = str(row.get("judge_status") or row.get("status") or "").strip().upper()
                    signals_raw = row.get("signals") if isinstance(row.get("signals"), dict) else {}
                    before_case = row.get("before_case_snapshot")
                    if not isinstance(before_case, dict):
                        before_case = {}
                    after_case = row.get("after_case_snapshot")
                    if not isinstance(after_case, dict):
                        after_case = {}
                    normalized_judge_rows.append(
                        {
                            "generation_id": int(db_entry.id or 0),
                            "request_id": request_id,
                            "case_id": str(row.get("case_id") or "").strip(),
                            "judge_status": status,
                            "reject_reason": str(row.get("reject_reason") or "").strip(),
                            "pending_reason": str(row.get("pending_reason") or "").strip(),
                            "signals": {
                                "violates_confirmed_fact": bool(
                                    signals_raw.get("violates_confirmed_fact", row.get("violates_confirmed_fact"))
                                ),
                                "missing_core_flow": bool(
                                    signals_raw.get("missing_core_flow", row.get("missing_core_flow"))
                                ),
                                "missing_reuse_risk": bool(
                                    signals_raw.get("missing_reuse_risk", row.get("missing_reuse_risk"))
                                ),
                                "contains_pending_logic": bool(
                                    signals_raw.get("contains_pending_logic", row.get("contains_pending_logic"))
                                ),
                                "confirmed_fact_hits": list(signals_raw.get("confirmed_fact_hits") or row.get("confirmed_fact_hits") or []),
                                "confirmed_fact_violations": list(signals_raw.get("confirmed_fact_violations") or row.get("confirmed_fact_violations") or []),
                                "reuse_risk_hits": list(signals_raw.get("reuse_risk_hits") or row.get("reuse_risk_hits") or []),
                                "pending_hits": list(signals_raw.get("pending_hits") or row.get("pending_hits") or []),
                                "vague_or_unconfirmed_hits": list(
                                    signals_raw.get("vague_or_unconfirmed_hits")
                                    or row.get("vague_or_unconfirmed_hits")
                                    or []
                                ),
                            },
                            "before_case_snapshot": dict(before_case),
                            "after_case_snapshot": dict(after_case),
                        }
                    )
                reject_pending_rows = [
                    row
                    for row in normalized_judge_rows
                    if str(row.get("judge_status") or "").upper() in {"REJECT", "PENDING"}
                ]
                if judge_summary_payload or normalized_judge_rows:
                    judge_table_diag_payload = {
                        "kind": "judge_decision_table",
                        "generation_id": int(db_entry.id or 0),
                        "request_id": request_id,
                        "rows": reject_pending_rows or normalized_judge_rows,
                        "row_count": int(len(reject_pending_rows or normalized_judge_rows)),
                        "row_count_total": int(len(normalized_judge_rows)),
                        "row_count_reject_pending": int(len(reject_pending_rows)),
                        "rows_scope": "reject_pending_only" if reject_pending_rows else "all_when_no_reject_pending",
                        "row_evidence_incomplete": bool(
                            int(judge_summary_payload.get("rejected_out_count") or 0)
                            + int(judge_summary_payload.get("pending_out_count") or 0) > 0
                            and len(reject_pending_rows) == 0
                        ),
                        "multi_pass": bool(multi_pass),
                        "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    }
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            user_id=user_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(judge_table_diag_payload, ensure_ascii=False)}",
                        )
                    )
                    db.commit()
                # Add db id to result for reference
                if isinstance(result, list):
                     pass # Can't add to list easily, maybe wrap? keeping as is.
                elif isinstance(result, dict):
                    result['db_id'] = db_entry.id

                if isinstance(result, list) and STAGE25_SWITCHES.coverage_diagnostics_enabled:
                    coverage_diag = build_coverage_diagnostics(
                        requirement=requirement,
                        generated_cases=[x for x in result if isinstance(x, dict)],
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

                if isinstance(result, list):
                    from modules.test_generation_components.coverage.core_flow_coverage_contract import (
                        audit_core_flow_coverage,
                    )
                    from core.settings.config import settings
                    core_flow_audit = audit_core_flow_coverage(
                        [x for x in result if isinstance(x, dict)]
                    )
                    core_flow_audit_payload = {
                        "kind": "core_flow_coverage",
                        "request_id": request_id,
                        "generation_id": int(persisted_generation_id),
                        "multi_pass": bool(multi_pass),
                        "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                        "core_flow_covered_count": int(core_flow_audit["core_flow_covered_count"]),
                        "core_flow_required_count": int(core_flow_audit["core_flow_required_count"]),
                        "core_flow_coverage_ratio": float(core_flow_audit["core_flow_coverage_ratio"]),
                        "core_flow_coverage_passed": bool(core_flow_audit["core_flow_coverage_passed"]),
                        "missing_core_flows": list(core_flow_audit["missing_core_flows"]),
                        "false_positive_guard_notes": list(core_flow_audit["false_positive_guard_notes"]),
                    }
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            user_id=user_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(core_flow_audit_payload, ensure_ascii=False)}",
                        )
                    )
                    db.commit()

                    if bool(getattr(settings, "CORE_FLOW_BACKFILL_ENABLED", False)):
                        from modules.test_generation_components.coverage.core_flow_backfill import (
                            plan_core_flow_backfill,
                        )
                        backfill_plan = plan_core_flow_backfill(
                            requirement_context=requirement,
                            existing_cases=[x for x in result if isinstance(x, dict)],
                            coverage_audit=core_flow_audit,
                            max_backfill_cases=int(getattr(settings, "CORE_FLOW_BACKFILL_MAX_CANDIDATES", 12) or 12),
                        )
                        backfill_diag_payload = {
                            "kind": "core_flow_backfill_dry_run",
                            "request_id": request_id,
                            "generation_id": int(persisted_generation_id),
                            "multi_pass": bool(multi_pass),
                            "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                            **{k: v for k, v in backfill_plan.items() if k != "backfill_plan"},
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
                        db.add(
                            LogEntry(
                                project_id=project_id,
                                user_id=user_id,
                                log_type="system",
                                message=f"GEN_DIAG:{json.dumps(backfill_diag_payload, ensure_ascii=False)}",
                            )
                        )
                        db.commit()
            except Exception as e:
                print(f"Failed to save to DB: {e}")

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
                 
        return result

    def generate_test_cases_excel(
        self,
        requirement: str,
        project_id: int,
        db: Session = None,
        doc_type: str = "requirement",
        compress: bool = False,
        user_id: int = None,
        current_biz_key: str = "",
        only_current_biz: bool = False,
        multi_pass: bool = True,
        generation_mode: str = "",
    ) -> bytes:
        # Generate test cases in JSON format
        json_result = self.generate_test_cases_json(
            requirement,
            project_id,
            db,
            doc_type,
            compress,
            user_id=user_id,
            current_biz_key=current_biz_key,
            only_current_biz=only_current_biz,
            multi_pass=multi_pass,
            generation_mode=generation_mode,
        )
        
        return self.convert_json_to_excel(json_result)

    def convert_json_to_excel(self, json_data: list | dict) -> bytes:
        """Convert generated JSON cases into an Excel workbook payload.

        The adapter preserves the structured case contract and delegates the
        workbook layout to the export implementation.
        """
        return _convert_json_to_excel_adapter(json_data)
