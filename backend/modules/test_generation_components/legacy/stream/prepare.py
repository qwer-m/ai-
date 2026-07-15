from typing import Any, Iterator
import json
import time

from sqlalchemy.orm import Session

from .runtime import LazyAttrProxy, call_component
from .prepare_runtime import (
    record_prepare_timing_event,
    resolve_append_existing_state,
    resolve_stream_prepare_runtime,
)


TestGeneration = LazyAttrProxy("core.db.models", "TestGeneration")
MemoryContext = LazyAttrProxy("modules.memory_fabric.contracts.memory_context", "MemoryContext")


def get_client_for_user(*args: Any, **kwargs: Any) -> Any:
    return call_component("core.ai.ai_client", "get_client_for_user", *args, **kwargs)


def init_memory_diag(*args: Any, **kwargs: Any) -> Any:
    return call_component("modules.memory_fabric.runtime.diagnostics", "init_memory_diag", *args, **kwargs)


def get_memory_fabric(*args: Any, **kwargs: Any) -> Any:
    return call_component("modules.memory_fabric.runtime.factory", "get_memory_fabric", *args, **kwargs)


def build_gate_reason_chain(*args: Any, **kwargs: Any) -> Any:
    return call_component("...prompting.generation_diagnostics", "build_gate_reason_chain", *args, **kwargs)


def build_feedback_control_state(*args: Any, **kwargs: Any) -> Any:
    return call_component("...control.build_feedback_control_state", "build_feedback_control_state", *args, **kwargs)


def merge_current_requirement_blueprint_control_state(*args: Any, **kwargs: Any) -> Any:
    return call_component(
        "...control.current_requirement_blueprint",
        "merge_current_requirement_blueprint_control_state",
        *args,
        **kwargs,
    )


def merge_generation_mode_control_state(*args: Any, **kwargs: Any) -> Any:
    return call_component("...control.generation_mode_activation", "merge_generation_mode_control_state", *args, **kwargs)


def resolve_linked_final_case_signal(*args: Any, **kwargs: Any) -> Any:
    return call_component("...control.generation_mode_activation", "resolve_linked_final_case_signal", *args, **kwargs)


def count_unique_test_cases(*args: Any, **kwargs: Any) -> Any:
    return call_component("..adapters", "count_unique_test_cases", *args, **kwargs)


def deduplicate_test_cases(*args: Any, **kwargs: Any) -> Any:
    return call_component("..adapters", "deduplicate_test_cases", *args, **kwargs)


def normalize_json_structure(*args: Any, **kwargs: Any) -> Any:
    return call_component("..adapters", "normalize_json_structure", *args, **kwargs)


def prepare_append_existing_cases(*args: Any, **kwargs: Any) -> Any:
    return call_component("...postprocess.result_postprocess", "prepare_append_existing_cases", *args, **kwargs)


def hydrate_append_existing_cases_from_diagnostic(*args: Any, **kwargs: Any) -> Any:
    return call_component(
        "...execution.execution_suite_history",
        "hydrate_append_existing_cases_from_diagnostic",
        *args,
        **kwargs,
    )


def requirement_compression_decision(*args: Any, **kwargs: Any) -> Any:
    return call_component("..compression_policy", "requirement_compression_decision", *args, **kwargs)


class LegacyGenerationStreamPrepareMixin:

    def _stream_prepare_phase(
        self,
        *,
        client,
        request_id: str,
        requirement: str,
        project_id: int,
        db: Session | None = None,
        doc_type: str = "requirement",
        compress: bool = False,
        expected_count: int = 20,
        batch_size: int = 10,
        overwrite: bool = False,
        append: bool = False,
        previous_generation_id: int | None = None,
        user_id: int | None = None,
        current_biz_key: str = "",
        only_current_biz: bool = False,
        multi_pass: bool = True,
        generation_mode: str = "",
        enable_sample_pool_feedback: bool = True,
    ) -> Iterator[dict[str, Any]]:
        prepare_started = time.perf_counter()
        timing_events: list[dict[str, Any]] = []

        def _record_timing_event(stage: str, started_at: float, **fields: Any) -> dict[str, Any]:
            return record_prepare_timing_event(timing_events, stage, started_at, **fields)

        runtime_state = resolve_stream_prepare_runtime(
            user_id=user_id,
            db=db,
            project_id=project_id,
            requirement=requirement,
            compress=compress,
            get_client_for_user_fn=get_client_for_user,
            requirement_compression_decision_fn=requirement_compression_decision,
            resolve_linked_final_case_signal_fn=resolve_linked_final_case_signal,
            init_memory_diag_fn=init_memory_diag,
            get_memory_fabric_fn=get_memory_fabric,
            memory_context_cls=MemoryContext,
            record_timing_event_fn=_record_timing_event,
        )
        client = runtime_state.client
        request_id = runtime_state.request_id
        original_requirement = runtime_state.original_requirement
        compression_decision = runtime_state.compression_decision
        linked_final_case_signal = runtime_state.linked_final_case_signal
        memory_diag = runtime_state.memory_diag
        memory_fabric = runtime_state.memory_fabric
        memory_ctx = runtime_state.memory_ctx

        kb_context = ""
        context_result: dict[str, Any] = {}
        gate_debug: dict[str, Any] = {}
        feedback_control_state: dict[str, Any] = {}
        
        # Determine start_id if appending
        append_state = resolve_append_existing_state(
            db=db,
            append=append,
            project_id=project_id,
            user_id=user_id,
            original_requirement=original_requirement,
            previous_generation_id=previous_generation_id,
            test_generation_model=TestGeneration,
            prepare_append_existing_cases_fn=prepare_append_existing_cases,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            count_unique_test_cases_fn=count_unique_test_cases,
            record_timing_event_fn=_record_timing_event,
            hydrate_append_existing_cases_fn=hydrate_append_existing_cases_from_diagnostic,
        )
        start_id = append_state.start_id
        existing_cases = append_state.existing_cases
        existing_entry = append_state.existing_entry
        existing_unique_count = append_state.existing_unique_count
        if append and (not existing_entry or existing_unique_count <= 0):
            yield "@@STATUS@@:追加生成未找到上一轮结果，已终止本次追加。\n"
            yield (
                "Error: APPEND_BASELINE_NOT_FOUND: "
                "追加生成需要上一轮 generation_id 或可用的历史结果，请先加载最终生成结果后再追加。\n"
            )
            _record_timing_event(
                "prepare_total",
                prepare_started,
                status="aborted_append_baseline_not_found",
                db_available=bool(db),
                append=True,
                previous_generation_id=int(previous_generation_id or 0),
                append_lookup_source=str(append_state.lookup_source or ""),
            )
            return {"abort": True, "generation_timing_events": timing_events}

        if db:
            status_messages: list[str] = []
            # 中文注释：流式链路同样走 snapshot readiness gate，保证两条入口行为一致。
            gate_result = {
                "proceed": True,
                "gate_debug": {
                    "snapshot_gate_enabled": False,
                    "snapshot_wait_result": "skipped_non_session_db",
                },
            }
            snapshot_gate_started = time.perf_counter()
            active_db_session = self._is_active_db_session(db)
            if active_db_session:
                gate_result = self._run_snapshot_readiness_gate(
                project_id=project_id,
                user_id=user_id,
                status_messages=status_messages,
            )
            gate_debug = gate_result.get("gate_debug") or {}
            _record_timing_event(
                "snapshot_gate",
                snapshot_gate_started,
                active_db_session=bool(active_db_session),
                proceed=bool(gate_result.get("proceed")),
                snapshot_wait_result=str(gate_debug.get("snapshot_wait_result") or ""),
            )
            if not gate_result.get("proceed"):
                gate_reason_chain = build_gate_reason_chain(gate_debug)
                wait_result = str(gate_debug.get("snapshot_wait_result") or "").strip().lower()
                if "timeout" in wait_result:
                    gate_reason_chain.append("snapshot_gate_timeout")
                gate_reason_chain.append("hybrid_context_not_built")
                gate_reason_chain.append("generation_aborted_before_model_call")
                for status_message in status_messages:
                    yield f"@@STATUS@@:{status_message}\n"
                yield (
                    "@@STATUS@@:snapshot readiness gate 未通过，终止本次生成。"
                    f"(result={gate_debug.get('snapshot_wait_result')})\n"
                )
                gate_error_code = gate_result.get("error_code") or "SNAPSHOT_NOT_READY_TIMEOUT"
                gate_error_message = gate_result.get("error_message") or "snapshot 未就绪，终止生成。"
                yield f"Error: {gate_error_code}: {gate_error_message}\n"
                self._emit_context_source_log(
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                    context_result={
                        "context_source": "none",
                        "fusion_debug": {
                            **gate_debug,
                            "final_decision": "timeout_fail_fast",
                            "reason_chain": gate_reason_chain,
                        },
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
                        "fusion_debug": {
                            **gate_debug,
                            "reason_chain": gate_reason_chain,
                            "final_decision": "timeout_fail_fast",
                        },
                        "snapshot_result": {},
                        "rag_result": {},
                    },
                    gate_debug=gate_debug,
                    fallback_reason="snapshot_wait_gate_abort",
                    abort_code=gate_error_code,
                    compressed_chars=0,
                )
                _record_timing_event(
                    "prepare_total",
                    prepare_started,
                    status="aborted_snapshot_gate",
                    db_available=bool(db),
                    kb_chars=0,
                )
                return {"abort": True, "generation_timing_events": timing_events}

            hybrid_context_started = time.perf_counter()
            context_result = self._resolve_kb_context_with_hybrid(
                requirement=requirement,
                project_id=project_id,
                db=db,
                user_id=user_id,
                compress=compress,
                status_messages=status_messages,
                precision_mode=True,
                memory_fabric=memory_fabric,
                memory_ctx=memory_ctx,
                memory_diag=memory_diag,
                request_id=request_id,
            )
            fusion_debug = context_result.get("fusion_debug") or {}
            if gate_debug:
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
            _record_timing_event(
                "hybrid_context",
                hybrid_context_started,
                context_source=str(context_result.get("context_source") or ""),
                kb_chars=int(len(kb_context or "")),
                snapshot_used=bool(fusion_debug.get("snapshot_used")),
                realtime_rag_used=bool(fusion_debug.get("realtime_rag_used", fusion_debug.get("rag_used"))),
                abort_generation=bool(context_result.get("abort_generation")),
            )
            feedback_control_started = time.perf_counter()
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
            _record_timing_event(
                "feedback_control_state",
                feedback_control_started,
                workflow_blueprint_count=int(len(feedback_control_state.get("workflow_blueprints") or [])),
                linked_final_case_count=int(linked_final_case_signal.get("linked_final_case_count") or 0),
            )

            # 中文注释：向前端显式透出 snapshot 回退策略状态，避免将 not ready 误判为失败。
            context_debug_payload = {
                "snapshot_status": str(fusion_debug.get("snapshot_status") or ""),
                "snapshot_ready": bool(fusion_debug.get("snapshot_ready")),
                "snapshot_used": bool(fusion_debug.get("snapshot_used")),
                "snapshot_fallback_reason": str(
                    fusion_debug.get("snapshot_fallback_reason")
                    or context_result.get("fallback_reason")
                    or ""
                ),
                "realtime_rag_used": bool(
                    fusion_debug.get("realtime_rag_used", fusion_debug.get("rag_used"))
                ),
                "current_document_used": bool(fusion_debug.get("current_document_used", True)),
                "snapshot_rebuild_triggered": bool(fusion_debug.get("snapshot_rebuild_triggered")),
                "snapshot_rebuild_reason": str(
                    fusion_debug.get("snapshot_rebuild_reason")
                    or fusion_debug.get("snapshot_queue_reason")
                    or ""
                ),
            }
            context_result["context_debug"] = context_debug_payload
            yield f"@@CONTEXT_DEBUG@@:{json.dumps(context_debug_payload, ensure_ascii=False)}\n"

            for status_message in status_messages:
                yield f"@@STATUS@@:{status_message}\n"

            # 中文注释：流式链路同样在“上下文全空”时快速失败，避免继续裸跑生成。
            if context_result.get("abort_generation"):
                fusion_debug = context_result.get("fusion_debug") or {}
                abort_error = context_result.get("abort_error") or "上下文为空，已按兜底策略终止生成。"
                reason_chain = list(fusion_debug.get("reason_chain") or [])
                if not reason_chain or reason_chain[-1] != "hybrid_context_not_built":
                    reason_chain.append("hybrid_context_not_built")
                if reason_chain[-1] != "generation_aborted_before_model_call":
                    reason_chain.append("generation_aborted_before_model_call")
                fusion_debug["reason_chain"] = reason_chain
                final_decision = fusion_debug.get("final_decision") or "fail_fast"
                print(
                    "Hybrid guard abort(stream): "
                    f"snapshot_status={fusion_debug.get('snapshot_status')}, "
                    f"snapshot_queue_status={fusion_debug.get('snapshot_queue_status')}, "
                    f"snapshot_queue_reason={fusion_debug.get('snapshot_queue_reason')}, "
                    f"lane_counts={fusion_debug.get('lane_counts')}, "
                    f"lane_reasons={fusion_debug.get('lane_reasons')}, "
                    f"final_empty_reason={fusion_debug.get('hybrid_empty_reason')}"
                )
                yield (
                    "@@STATUS@@:上下文兜底触发，终止本次生成。"
                    f"(decision={final_decision},queue={fusion_debug.get('snapshot_queue_status')}/{fusion_debug.get('snapshot_queue_reason')})\n"
                )
                yield f"Error: {abort_error}\n"
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
                _record_timing_event(
                    "prepare_total",
                    prepare_started,
                    status="aborted_hybrid_empty_context",
                    db_available=bool(db),
                    kb_chars=int(len(kb_context or "")),
                )
                return {"abort": True, "generation_timing_events": timing_events}

            blueprint_started = time.perf_counter()
            feedback_control_state = merge_current_requirement_blueprint_control_state(
                feedback_control_state,
                client=client,
                requirement_text=original_requirement,
                db=db,
                project_id=project_id,
                user_id=user_id,
            ).to_dict()
            current_blueprint_meta = dict((feedback_control_state or {}).get("source_meta") or {})
            current_blueprint_status = str(
                current_blueprint_meta.get("current_requirement_blueprint_status") or ""
            )
            _record_timing_event(
                "current_requirement_blueprint",
                blueprint_started,
                status=current_blueprint_status,
                count=int(current_blueprint_meta.get("current_requirement_blueprint_count") or 0),
                step_count=int(current_blueprint_meta.get("current_requirement_blueprint_step_count") or 0),
                max_tokens=int(current_blueprint_meta.get("current_requirement_blueprint_max_tokens") or 0),
            )
            if current_blueprint_status:
                yield (
                    "@@STATUS@@:current requirement blueprint "
                    f"{current_blueprint_status} "
                    f"(count={current_blueprint_meta.get('current_requirement_blueprint_count', 0)},"
                    f"steps={current_blueprint_meta.get('current_requirement_blueprint_step_count', 0)})\n"
                )

            compression_decision = requirement_compression_decision(
                requirement,
                compress_requested=bool(compress),
            )
            if compress and not bool(compression_decision.get("should_compress")):
                requirement_compress_started = time.perf_counter()
                yield (
                    "@@STATUS@@:需求长度 "
                    f"{compression_decision.get('char_count')} 低于压缩阈值 "
                    f"{compression_decision.get('min_chars')}，跳过需求压缩...\n"
                )
                _record_timing_event(
                    "requirement_compress",
                    requirement_compress_started,
                    status="skipped_below_threshold",
                    before_chars=int(len(requirement or "")),
                    after_chars=int(len(requirement or "")),
                    min_chars=int(compression_decision.get("min_chars") or 0),
                )
            if compress and bool(compression_decision.get("should_compress")):
                requirement_compress_started = time.perf_counter()
                req_len_before = len(requirement)
                compress_status = "exception"
                compressed_req = None
                try:
                    compressed_req = client.compress_context(
                        requirement,
                        prompt="请将以下需求压缩为适合测试用例生成的精炼版本，保留技术细节、字段/ID、约束、边界与异常规则，去除冗余。输出纯文本。",
                        db=db,
                    )
                    if (
                        compressed_req
                        and not compressed_req.startswith("Error")
                        and not compressed_req.startswith("Exception")
                    ):
                        requirement = compressed_req
                        compress_status = "applied"
                        yield f"@@STATUS@@:需求压缩完成 ({req_len_before} -> {len(requirement)} 字符)...\n"
                    else:
                        yield f"@@STATUS@@:需求压缩返回异常，使用原始文本: {(compressed_req or '')[:50]}...\n"
                except Exception as e:
                    yield f"@@STATUS@@:需求压缩失败 ({str(e)})，将使用原始文本...\n"

                if compress_status == "exception" and compressed_req is not None:
                    compress_status = "invalid_response"
                _record_timing_event(
                    "requirement_compress",
                    requirement_compress_started,
                    status=compress_status,
                    before_chars=int(req_len_before),
                    after_chars=int(len(requirement or "")),
                    min_chars=int(compression_decision.get("min_chars") or 0),
                )

        if db and not compress:
            if requirement and len(requirement) > 120000:
                long_req_compress_started = time.perf_counter()
                req_len_before = len(requirement)
                compress_status = "exception"
                compressed_req = None
                yield "@@STATUS@@:输入内容较长，正在进行智能压缩以适配模型上下文...\n"
                try:
                    req_len_before = len(requirement)
                    compressed_req = client.compress_context(
                        requirement,
                        prompt="请将以下需求压缩为适合测试用例生成的精炼版本，保留技术细节、字段/ID、约束、边界与异常规则，去除冗余。输出纯文本。",
                        db=db
                    )
                    if compressed_req and not compressed_req.startswith("Error"):
                        requirement = compressed_req
                        compress_status = "applied"
                        yield f"@@STATUS@@:长文本压缩完成 ({req_len_before} -> {len(requirement)} 字符)...\n"
                    else:
                        yield f"@@STATUS@@:长文本压缩异常，使用原始文本...\n"
                except Exception:
                    pass
                if compress_status == "exception" and compressed_req is not None:
                    compress_status = "invalid_response"
                _record_timing_event(
                    "long_requirement_compress",
                    long_req_compress_started,
                    status=compress_status,
                    before_chars=int(req_len_before),
                    after_chars=int(len(requirement or "")),
                )
            if kb_context and len(kb_context) > 120000:
                kb_compress_started = time.perf_counter()
                kb_len_before = len(kb_context)
                compress_status = "exception"
                compressed_kb = None
                yield "@@STATUS@@:知识库上下文较长，正在进行智能压缩以适配模型上下文...\n"
                try:
                    kb_len_before = len(kb_context)
                    compressed_kb = client.compress_context(
                        kb_context,
                        prompt="请将以下检索到的知识库上下文压缩为适合测试用例生成的精炼摘要，保留关键实体、流程、约束、字段、边界与异常规则。输出纯文本。",
                        db=db
                    )
                    if compressed_kb and not compressed_kb.startswith("Error"):
                        kb_context = compressed_kb
                        compress_status = "applied"
                        yield f"@@STATUS@@:知识库压缩完成 ({kb_len_before} -> {len(kb_context)} 字符)...\n"
                    else:
                        yield f"@@STATUS@@:知识库压缩异常，使用原始文本...\n"
                except Exception:
                    pass
                if compress_status == "exception" and compressed_kb is not None:
                    compress_status = "invalid_response"
                _record_timing_event(
                    "kb_context_compress",
                    kb_compress_started,
                    status=compress_status,
                    before_chars=int(kb_len_before),
                    after_chars=int(len(kb_context or "")),
                )

        feedback_control_state = merge_generation_mode_control_state(
            feedback_control_state,
            requirement_text=original_requirement,
            expected_count=int(expected_count or 0),
            linked_final_case_count=int(linked_final_case_signal.get("linked_final_case_count") or 0),
        ).to_dict()

        _record_timing_event(
            "prepare_total",
            prepare_started,
            status="completed",
            db_available=bool(db),
            append=bool(append),
            compression_requested=bool(compress),
            kb_chars=int(len(kb_context or "")),
            context_source=str(context_result.get("context_source") or ""),
        )

        return {
            "abort": False,
            "client": client,
            "request_id": request_id,
            "requirement": requirement,
            "project_id": project_id,
            "db": db,
            "doc_type": doc_type,
            "compress": compress,
            "requirement_compression_decision": compression_decision,
            "expected_count": expected_count,
            "batch_size": batch_size,
            "overwrite": overwrite,
            "append": append,
            "user_id": user_id,
            "original_requirement": original_requirement,
            "kb_context": kb_context,
            "start_id": start_id,
            "existing_cases": existing_cases,
            "existing_unique_count": existing_unique_count,
            "existing_entry": existing_entry,
            "context_result": context_result,
            "gate_debug": gate_debug,
            "current_biz_key": str(current_biz_key or ""),
            "only_current_biz": bool(only_current_biz),
            "multi_pass": bool(multi_pass),
            "generation_mode": str(generation_mode or "").strip().lower(),
            "feedback_control_state": feedback_control_state,
            "memory_diag": memory_diag,
            "generation_timing_events": timing_events,
        }

