from typing import Any

from sqlalchemy.orm import Session

from core.settings.config import settings

from .json_generation_dependencies import (
    MemoryContext,
    STAGE25_SWITCHES,
    LogEntry,
    TestGeneration,
    analyze_coverage,
    build_closed_loop_base_prompt,
    build_context_compression_diagnostics,
    build_coverage_diagnostics,
    build_feedback_control_state,
    build_gate_reason_chain,
    build_persistence_gate_diagnostic,
    build_prompt_context_intake_diagnostics,
    build_requirement_semantics_payload,
    build_structured_prompt_context,
    build_supplement_closed_loop_instruction,
    clean_and_parse_json,
    count_unique_test_cases,
    create_db_session,
    deduplicate_test_cases,
    evaluate_current_requirement_semantic_compilation,
    evaluate_persistence_gate,
    finalize_generated_cases,
    get_client_for_user,
    get_memory_fabric,
    infer_case_kind,
    init_memory_diag,
    merge_contract_quality_gate,
    merge_current_requirement_blueprint_control_state,
    merge_generation_mode_control_state,
    normalize_final_case_priorities,
    normalize_json_structure,
    project_persistable_cases,
    reorder_cases_by_closed_loop,
    requirement_compression_decision,
    resolve_linked_final_case_signal,
    stream_postprocess_cases,
    summarize_persistable_case_contract,
    summarize_persistence_case_quality_gate,
)
from .json_generation_execution import run_json_generation_execution
from .json_generation_diag_emitters import (
    emit_json_biz_key_diag,
    emit_json_prompt_context_intake_diag,
    persist_generation_diag,
)
from .json_generation_persist_diagnostics import (
    emit_pre_persist_generation_diagnostics,
    emit_post_persist_coverage_audit_diagnostics,
    emit_post_persist_generation_diagnostics,
)
from .json_generation_persistence import run_json_persistence_flow
from .json_generation_review_postprocess import run_json_review_postprocess
from ..postprocess.module_contract import enforce_functional_module_contract
from ..control.semantic_contract import (
    CASE_SEMANTIC_CONTRACT_ABORT_CODE,
    resolve_case_semantic_gate,
)
from .json_generation_runtime import resolve_json_generation_runtime


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
        batch_size: int = settings.TEST_GENERATION_BATCH_SIZE,
        batch_index: int = 0,
        user_id: int = None,
        current_biz_key: str = "",
        only_current_biz: bool = False,
        multi_pass: bool = True,
        generation_mode: str = "",
        enable_sample_pool_feedback: bool = True,
    ) -> dict:
        """Generate test cases in JSON format for stage-1 orchestration."""
        runtime_state = resolve_json_generation_runtime(
            user_id=user_id,
            db=db,
            project_id=project_id,
            requirement=requirement,
            get_client_for_user_fn=get_client_for_user,
            resolve_linked_final_case_signal_fn=resolve_linked_final_case_signal,
            init_memory_diag_fn=init_memory_diag,
            get_memory_fabric_fn=get_memory_fabric,
            memory_context_cls=MemoryContext,
        )
        client = runtime_state.client
        request_id = runtime_state.request_id
        original_requirement = runtime_state.original_requirement
        linked_final_case_signal = runtime_state.linked_final_case_signal
        memory_diag = runtime_state.memory_diag
        memory_fabric = runtime_state.memory_fabric
        memory_ctx = runtime_state.memory_ctx

        # Retrieve context from Knowledge Base if DB is available
        kb_context = ""
        gate_debug: dict[str, Any] = {}
        context_result: dict[str, Any] | None = None
        gate_result: dict[str, Any] | None = None
        feedback_control_state: dict[str, Any] = {}
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
                    current_source_doc_ids=linked_final_case_signal.get("source_doc_ids") or [],
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

            compression_decision = requirement_compression_decision(
                requirement,
                compress_requested=bool(compress),
            )
            if compress and bool(compression_decision.get("should_compress")):
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

        isolated_ai_runtime_factory = None
        if isinstance(db, Session) and bool(user_id):
            def _graph_partition_worker_runtime() -> Any:
                # 延迟导入，保持 JSON 入口的轻量模块加载契约。
                from .stream.prepare_runtime import isolated_ai_runtime

                return isolated_ai_runtime(
                    user_id=int(user_id),
                    create_db_session_fn=create_db_session,
                    get_client_for_user_fn=get_client_for_user,
                )

            isolated_ai_runtime_factory = (
                _graph_partition_worker_runtime
            )

        feedback_control_state = merge_current_requirement_blueprint_control_state(
            feedback_control_state,
            client=client,
            requirement_text=original_requirement,
            db=db,
            project_id=project_id,
            user_id=user_id,
            isolated_ai_runtime_factory=(
                isolated_ai_runtime_factory
            ),
        ).to_dict()
        current_blueprint_meta = dict((feedback_control_state or {}).get("source_meta") or {})
        semantic_compilation_gate = evaluate_current_requirement_semantic_compilation(
            current_blueprint_meta,
            requirement_text=original_requirement,
        )
        if not semantic_compilation_gate.get("passed"):
            diagnostic_payload = {
                "kind": "semantic_compilation_abort",
                "request_id": request_id,
                "project_id": project_id,
                "user_id": user_id,
                **semantic_compilation_gate,
            }
            persist_generation_diag(
                db=db,
                active_db_session=self._is_active_db_session(db),
                log_entry_model=LogEntry,
                project_id=project_id,
                user_id=user_id,
                payload=diagnostic_payload,
            )
            error_payload = {
                "error": semantic_compilation_gate.get("abort_code"),
                "abort_code": semantic_compilation_gate.get("abort_code"),
                "message": semantic_compilation_gate.get("message"),
                "reason_chain": [
                    "current_requirement_semantic_compilation_failed",
                    "generation_aborted_before_test_case_model_call",
                ],
                "semantic_compilation": semantic_compilation_gate,
                "diagnostic": diagnostic_payload,
            }
            print(
                "semantic compilation abort(json): "
                f"status={semantic_compilation_gate.get('semantic_compile_status')}, "
                f"declaration={semantic_compilation_gate.get('workflow_declaration_status')}"
            )
            return error_payload

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
            strategy_plan=strategy_plan,
        ).to_dict()
        prompt_context = build_structured_prompt_context(
            requirement=requirement or "",
            architecture_requirement=original_requirement or requirement or "",
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
        emit_json_biz_key_diag(
            prompt_context=prompt_context,
            db=db,
            active_db_session=self._is_active_db_session(db),
            log_entry_model=LogEntry,
            project_id=project_id,
            user_id=user_id,
            request_id=request_id,
        )

        # Calculate start number for IDs based on batch index
        start_id = batch_index * batch_size + 1
        generated_before_batch = max(0, int(batch_index)) * max(1, int(batch_size))
        remaining_target = max(0, int(expected_count or 0) - generated_before_batch)
        current_batch_target = min(max(1, int(batch_size)), remaining_target)
        stage_logs: list[dict[str, Any]] = []
        stage_counts: dict[str, Any] = {}
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

        system_prompt = f"""
{base_prompt}

BATCH GENERATION INSTRUCTION (workflow-first):
This is batch #{batch_index + 1}.
Start the Test Case IDs from {start_id} (e.g., TC-{start_id:03d}).
Required valid batch target: {current_batch_target} unique, complete cases. This is the acceptance target, not a soft reference.
Do not pad the result with duplicate, weakly grounded, or non-assertable cases when evidence cannot support the target.
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
        emit_json_prompt_context_intake_diag(
            client=client,
            prompt_context=prompt_context,
            context_result=context_result if isinstance(context_result, dict) else {},
            requirement=requirement,
            source_requirement=original_requirement,
            kb_context=kb_context,
            base_prompt=base_prompt,
            system_prompt=system_prompt,
            doc_type=doc_type,
            compress=bool(compress),
            project_id=project_id,
            user_id=user_id,
            request_id=request_id,
            batch_index=batch_index + 1,
            expected_count=int(expected_count or 0),
            multi_pass=bool(multi_pass),
            generation_mode=normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
            db=db,
            active_db_session=self._is_active_db_session(db),
            log_entry_model=LogEntry,
            build_prompt_context_intake_diagnostics_fn=build_prompt_context_intake_diagnostics,
        )
        require_case_semantic_contract, requirement_semantic_contract = resolve_case_semantic_gate(
            feedback_control_state
        )
        case_semantic_rejections: list[dict[str, Any]] = []

        def _normalize_generated_json_structure(data: Any) -> Any:
            if not require_case_semantic_contract:
                return normalize_json_structure(data)
            return normalize_json_structure(
                data,
                require_case_semantic_contract=True,
                requirement_semantic_contract=requirement_semantic_contract,
                semantic_rejections=case_semantic_rejections,
                semantic_source_stage="json_primary_generation",
            )

        execution_result = run_json_generation_execution(
            client=client,
            requirement=requirement,
            db=db,
            system_prompt=system_prompt,
            prompt_context=prompt_context,
            resolved_current_biz=resolved_current_biz,
            start_id=start_id,
            normalized_generation_mode=normalized_generation_mode,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=_normalize_generated_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            finalize_generated_cases_fn=finalize_generated_cases,
            analyze_coverage_fn=analyze_coverage,
        )
        result = execution_result.result
        stage_logs = execution_result.stage_logs
        coverage_check_payload = execution_result.coverage_check_payload
        raw_response_payload = execution_result.raw_response_payload
        if case_semantic_rejections:
            semantic_rejection_diagnostic = {
                "kind": "case_semantic_contract_rejections",
                "request_id": request_id,
                "project_id": project_id,
                "user_id": user_id,
                "rejected_count": int(len(case_semantic_rejections)),
                "accepted_count": int(len(result)) if isinstance(result, list) else 0,
                "rejections": list(case_semantic_rejections)[:20],
            }
            persist_generation_diag(
                db=db,
                active_db_session=self._is_active_db_session(db),
                log_entry_model=LogEntry,
                project_id=project_id,
                user_id=user_id,
                payload=semantic_rejection_diagnostic,
            )
            if require_case_semantic_contract and not result:
                return {
                    "error": CASE_SEMANTIC_CONTRACT_ABORT_CODE,
                    "abort_code": CASE_SEMANTIC_CONTRACT_ABORT_CODE,
                    "message": "模型生成用例未满足结构化语义契约，已在 Review 前中止。",
                    "reason_chain": [
                        "generated_case_semantic_contract_failed",
                        "generation_aborted_before_review",
                    ],
                    "diagnostic": {
                        **semantic_rejection_diagnostic,
                        "kind": "case_semantic_contract_abort",
                        "abort_code": CASE_SEMANTIC_CONTRACT_ABORT_CODE,
                    },
                }

        review_postprocess = run_json_review_postprocess(
            result=result,
            db=db,
            client=client,
            requirement=requirement,
            base_prompt=base_prompt,
            kb_context=kb_context,
            expected_count=expected_count,
            start_id=start_id,
            resolved_current_biz=resolved_current_biz,
            multi_pass=multi_pass,
            generation_mode=generation_mode,
            feedback_control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
            prompt_context=prompt_context,
            stage_counts=stage_counts,
            coverage_check_payload=coverage_check_payload,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=build_supplement_closed_loop_instruction,
            build_requirement_semantics_payload_fn=build_requirement_semantics_payload,
            stream_postprocess_cases_fn=stream_postprocess_cases,
            initial_case_semantic_rejections=case_semantic_rejections,
        )
        result = review_postprocess.result
        stage_counts = review_postprocess.stage_counts
        coverage_check_payload = review_postprocess.coverage_check_payload
        convergence_payload = review_postprocess.convergence_payload
        generation_summary_payload = review_postprocess.generation_summary_payload
        review_decision_summary_payload = review_postprocess.review_decision_summary_payload
        review_decision_table_payload = review_postprocess.review_decision_table_payload
        judge_summary_payload = review_postprocess.judge_summary_payload
        judge_decision_table_payload = review_postprocess.judge_decision_table_payload
        candidate_cases_before_judge = review_postprocess.candidate_cases_before_judge
        candidate_total_before_judge = review_postprocess.candidate_total_before_judge
        final_cases_after_judge = review_postprocess.final_cases_after_judge
        final_case_count = review_postprocess.final_case_count
        empty_result_guard_triggered = review_postprocess.empty_result_guard_triggered
        empty_result_stage = review_postprocess.empty_result_stage
        if not review_postprocess.stream_postprocess_applied:
            return dict(result) if isinstance(result, dict) else {
                "error": "GLOBAL_REVIEW_REQUIRED",
                "error_code": "GLOBAL_REVIEW_REQUIRED",
                "abort_code": "GLOBAL_REVIEW_REQUIRED",
                "error_message": "全局 Review 未成功执行，已终止生成，未持久化候选结果。",
                "status": "failed",
                "final_status": "global_review_failed",
            }
        if isinstance(result, list):
            result, module_contract_summary = enforce_functional_module_contract(
                [item for item in result if isinstance(item, dict)],
                project_profile=prompt_context.get("project_profile") or {},
                inherit_execution_context=True,
            )
            stage_counts["module_contract_normalized"] = int(
                module_contract_summary.get("normalized_count") or 0
            )
            stage_counts["module_contract_rejected"] = int(
                module_contract_summary.get("rejected_count") or 0
            )
            final_cases_after_judge = [dict(item) for item in result]
            final_case_count = int(len(final_cases_after_judge))

        persistence_result = run_json_persistence_flow(
            db=db,
            active_db_session=self._is_active_db_session(db),
            client=client,
            result=result,
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
            context_result=context_result if isinstance(context_result, dict) else {},
            stage_logs=stage_logs,
            coverage_check_payload=coverage_check_payload,
            feedback_control_diag_payload=feedback_control_diag_payload,
            feedback_control_state=feedback_control_state if isinstance(feedback_control_state, dict) else {},
            judge_summary_payload=judge_summary_payload,
            judge_decision_table_payload=judge_decision_table_payload,
            memory_diag=memory_diag,
            system_prompt=system_prompt,
            generation_summary_payload=generation_summary_payload,
            final_cases_after_judge=final_cases_after_judge,
            final_case_count=final_case_count,
            review_decision_summary_payload=review_decision_summary_payload,
            review_decision_table_payload=review_decision_table_payload,
            convergence_payload=convergence_payload,
            candidate_total_before_judge=candidate_total_before_judge,
            empty_result_guard_triggered=empty_result_guard_triggered,
            empty_result_stage=empty_result_stage,
            gen_diag_payload=gen_diag_payload,
            compression_event_payload=compression_event_payload,
            log_entry_cls=LogEntry,
            test_generation_cls=TestGeneration,
            count_unique_test_cases_fn=count_unique_test_cases,
            build_context_compression_diagnostics_fn=build_context_compression_diagnostics,
            emit_pre_persist_generation_diagnostics_fn=emit_pre_persist_generation_diagnostics,
            emit_post_persist_generation_diagnostics_fn=emit_post_persist_generation_diagnostics,
            emit_post_persist_coverage_audit_diagnostics_fn=emit_post_persist_coverage_audit_diagnostics,
            normalize_missing_priority_final_cases_fn=_normalize_missing_priority_final_cases,
            merge_contract_quality_gate_fn=merge_contract_quality_gate,
            summarize_persistable_case_contract_fn=summarize_persistable_case_contract,
            summarize_persistence_case_quality_gate_fn=summarize_persistence_case_quality_gate,
            project_persistable_cases_fn=project_persistable_cases,
            evaluate_persistence_gate_fn=evaluate_persistence_gate,
            build_persistence_gate_diagnostic_fn=build_persistence_gate_diagnostic,
            build_coverage_diagnostics_fn=build_coverage_diagnostics,
            coverage_diagnostics_enabled=bool(STAGE25_SWITCHES.coverage_diagnostics_enabled),
        )
        result = persistence_result.result
        if persistence_result.error_payload:
            return persistence_result.error_payload

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
