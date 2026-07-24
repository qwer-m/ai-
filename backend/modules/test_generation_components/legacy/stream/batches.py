import json
import time
from typing import Any, Iterator

from .batch_candidate_acceptance import accept_stream_batch_candidates
from .batch_diagnostics import (
    build_case_semantic_retry_instruction,
    build_required_stage_coverage_instruction,
    build_case_signature as _build_case_signature,
    build_stream_batch_token_usage,
    build_stream_coverage_plan_lite as _build_stream_coverage_plan_lite,
    extract_requirement_semantics_payload as _extract_requirement_semantics_payload,
    is_non_assertable_expected_result as _is_non_assertable_expected_result,
    is_retryable_provider_error as _is_retryable_provider_error,
)
from .batch_diagnostic_emitters import (
    emit_biz_key_diag as _emit_biz_key_diag_payload,
    emit_prompt_context_intake_diag as _emit_prompt_context_intake_diag_payload,
    emit_stream_gen_diag as _emit_stream_gen_diag_payload,
    emit_stream_batch_quality_diag as _emit_stream_batch_quality_diag_payload,
    emit_stream_batch_token_usage_diag as _emit_stream_batch_token_usage_diag_payload,
)
from .batch_flow_control import (
    build_existing_case_history,
    build_stream_batch_quality_metric,
    resolve_stream_batch_plan,
    select_complete_generated_cases,
)
from .batch_parallel_shards import (
    build_coverage_shard_plan,
    build_parallel_shard_instruction,
    execute_parallel_shard_requests,
    merge_parallel_shard_cases,
    parallel_shard_config_from_settings,
    should_use_parallel_shards,
)
from .batch_prompt_runtime import (
    append_history_to_testcase_context,
    build_functional_architecture_instruction,
    build_recent_history_context,
    build_stream_batch_system_prompt,
)
from .runtime import LazyAttrProxy, call_component
from ...control.semantic_contract import resolve_case_semantic_gate
from ...postprocess.streaming_case_normalization import is_placeholder_expected_result


LogEntry = LazyAttrProxy("core.db.models", "LogEntry")
settings = LazyAttrProxy("core.settings.config", "settings")


def analyze_coverage(*args: Any, **kwargs: Any) -> Any:
    return call_component("...coverage.coverage_analyzer", "analyze_coverage", *args, **kwargs)


def evaluate_required_stage_candidate_coverage(*args: Any, **kwargs: Any) -> Any:
    """运行到批次阶段时再加载执行计划，保持流式入口的轻量导入契约。"""
    return call_component(
        "...postprocess.streaming_execution_plan_metadata",
        "evaluate_required_stage_candidate_coverage",
        *args,
        **kwargs,
    )


def enforce_functional_module_contract(*args: Any, **kwargs: Any) -> Any:
    """延迟加载模块契约，避免流式入口导入后处理依赖树。"""
    return call_component(
        "...postprocess.module_contract",
        "enforce_functional_module_contract",
        *args,
        **kwargs,
    )


def build_append_closed_loop_coverage_instruction(*args: Any, **kwargs: Any) -> Any:
    return call_component(
        "...prompting.prompt_orchestration",
        "build_append_closed_loop_coverage_instruction",
        *args,
        **kwargs,
    )


def build_closed_loop_base_prompt(*args: Any, **kwargs: Any) -> Any:
    return call_component("...prompting.prompt_orchestration", "build_closed_loop_base_prompt", *args, **kwargs)


def build_structured_prompt_context(*args: Any, **kwargs: Any) -> Any:
    return call_component("...prompting.structured_context", "build_structured_prompt_context", *args, **kwargs)


def build_prompt_context_intake_diagnostics(*args: Any, **kwargs: Any) -> Any:
    return call_component(
        "...prompting.generation_diagnostics",
        "build_prompt_context_intake_diagnostics",
        *args,
        **kwargs,
    )


def execution_side_suite_order_text(*args: Any, **kwargs: Any) -> Any:
    return call_component(
        "...postprocess.streaming_execution_plan_ordering",
        "execution_side_suite_order_text",
        *args,
        **kwargs,
    )


def clean_and_parse_json(*args: Any, **kwargs: Any) -> Any:
    return call_component("..adapters", "clean_and_parse_json", *args, **kwargs)


def get_client_for_user(*args: Any, **kwargs: Any) -> Any:
    return call_component("core.ai.ai_client", "get_client_for_user", *args, **kwargs)


def count_unique_test_cases(*args: Any, **kwargs: Any) -> Any:
    return call_component("..adapters", "count_unique_test_cases", *args, **kwargs)


def infer_case_kind(*args: Any, **kwargs: Any) -> Any:
    return call_component("..adapters", "infer_case_kind", *args, **kwargs)


def normalize_json_structure(*args: Any, **kwargs: Any) -> Any:
    return call_component("..adapters", "normalize_json_structure", *args, **kwargs)


def project_persistable_cases(*args: Any, **kwargs: Any) -> Any:
    return call_component("...postprocess.case_contract", "project_persistable_cases", *args, **kwargs)


def _public_case_batch_json(cases: list[dict[str, Any]]) -> str:
    """仅序列化公开用例字段，内部语义继续留在后处理数据流中。"""
    public_cases = project_persistable_cases(cases)
    return json.dumps(public_cases, ensure_ascii=False, indent=2)


class LegacyGenerationStreamBatchesMixin:

    def _stream_run_batches_phase(
        self,
        *,
        state: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        client = state["client"]
        requirement = state["requirement"]
        architecture_requirement = str(state.get("original_requirement") or requirement or "")
        project_id = state["project_id"]
        db = state["db"]
        doc_type = state["doc_type"]
        expected_count = state["expected_count"]
        batch_size = state["batch_size"]
        append = state["append"]
        user_id = state["user_id"]
        request_id = state["request_id"]
        kb_context = state.get("kb_context") or ""
        start_id = int(state.get("start_id") or 1)
        existing_cases = state.get("existing_cases") or []
        context_result = state.get("context_result") or {}
        gate_debug = state.get("gate_debug") or {}
        feedback_control_state = state.get("feedback_control_state") or {}
        require_case_semantic_contract, requirement_semantic_contract = resolve_case_semantic_gate(
            feedback_control_state
        )
        case_semantic_rejections = state.setdefault("case_semantic_rejections", [])
        requirement_workflow_blueprints = [
            dict(item)
            for item in (
                (requirement_semantic_contract or {}).get("workflow_blueprints") or []
            )
            if isinstance(item, dict) and isinstance(item.get("steps"), list)
        ]
        accepted_semantic_cases: list[dict[str, Any]] = []
        required_stage_coverage = evaluate_required_stage_candidate_coverage(
            accepted_semantic_cases,
            workflow_blueprints=requirement_workflow_blueprints,
        )
        required_stage_coverage_instruction = build_required_stage_coverage_instruction(
            required_stage_coverage
        )

        def _normalize_generated_json_structure(data: Any) -> Any:
            if not require_case_semantic_contract:
                return normalize_json_structure(data)
            return normalize_json_structure(
                data,
                require_case_semantic_contract=True,
                requirement_semantic_contract=requirement_semantic_contract,
                semantic_rejections=case_semantic_rejections,
                semantic_source_stage="stream_primary_generation",
            )
        only_current_biz = bool(state.get("only_current_biz") or False)
        current_biz_key = str(state.get("current_biz_key") or "").strip()
        compress = bool(state.get("compress") or False)
        multi_pass = bool(state.get("multi_pass", True))
        generation_mode = str(state.get("generation_mode") or "").strip().lower()
        final_trace_emitted = False
        biz_diag_emitted = False
        system_prompt = ""
        timing_events = state.setdefault("generation_timing_events", [])
        phase_started = time.perf_counter()

        def _record_timing_event(stage: str, started_at: float, **fields: Any) -> dict[str, Any]:
            event = {
                "stage": str(stage or "unknown"),
                "duration_ms": max(0, int(round((time.perf_counter() - started_at) * 1000))),
            }
            for key, value in fields.items():
                if value is not None:
                    event[key] = value
            timing_events.append(event)
            return event

        def _emit_biz_key_diag(prompt_context: dict[str, Any]) -> None:
            nonlocal biz_diag_emitted
            biz_diag_emitted = _emit_biz_key_diag_payload(
                prompt_context=prompt_context,
                already_emitted=bool(biz_diag_emitted),
                db=db,
                is_active_db_session=self._is_active_db_session(db),
                log_entry_model=LogEntry,
                project_id=project_id,
                user_id=user_id,
                request_id=request_id,
            )

        def _emit_stream_batch_quality_diag(batch_metric: dict[str, Any]) -> None:
            _emit_stream_batch_quality_diag_payload(
                batch_metric=batch_metric,
                db=db,
                is_active_db_session=self._is_active_db_session(db),
                log_entry_model=LogEntry,
                project_id=project_id,
                user_id=user_id,
                request_id=request_id,
                current_biz_key=current_biz_key,
                multi_pass=multi_pass,
                generation_mode=generation_mode,
                stream_batch_diags=stream_batch_diags,
            )

        def _emit_prompt_context_intake_diag(
            *,
            prompt_context: dict[str, Any],
            base_prompt_text: str,
            system_prompt_text: str,
            batch_index_value: int,
            total_batches_value: int,
            attempt_value: int,
            requested_count: int,
        ) -> None:
            _emit_prompt_context_intake_diag_payload(
                prompt_context=prompt_context,
                base_prompt_text=base_prompt_text,
                system_prompt_text=system_prompt_text,
                batch_index_value=batch_index_value,
                total_batches_value=total_batches_value,
                attempt_value=attempt_value,
                requested_count=requested_count,
                client=client,
                requirement=requirement,
                context_result=context_result if isinstance(context_result, dict) else {},
                kb_context=kb_context,
                doc_type=doc_type,
                compress=compress,
                project_id=project_id,
                user_id=user_id,
                request_id=request_id,
                multi_pass=multi_pass,
                generation_mode=generation_mode,
                db=db,
                is_active_db_session=self._is_active_db_session(db),
                log_entry_model=LogEntry,
                build_prompt_context_intake_diagnostics_fn=build_prompt_context_intake_diagnostics,
                stream_batch_diags=stream_batch_diags,
            )

        def _emit_stream_batch_token_usage_diag(payload: dict[str, Any]) -> None:
            _emit_stream_batch_token_usage_diag_payload(
                payload=payload,
                db=db,
                is_active_db_session=self._is_active_db_session(db),
                log_entry_model=LogEntry,
                project_id=project_id,
                user_id=user_id,
                stream_batch_diags=stream_batch_diags,
            )

        def _emit_stream_gen_diag(payload: dict[str, Any]) -> None:
            _emit_stream_gen_diag_payload(
                db=db,
                is_active_db_session=self._is_active_db_session(db),
                log_entry_model=LogEntry,
                project_id=project_id,
                user_id=user_id,
                payload=payload,
                stream_batch_diags=stream_batch_diags,
            )

        # --- STEP 1: META-ANALYSIS ---
        if multi_pass:
            yield "@@STATUS@@:[multi-pass] 阶段1/3 主生成开始...\n"
        yield "@@STATUS@@:正在进行需求元分析 (Meta-Analysis)，识别系统类型与测试策略...\n"
        meta_analysis_started = time.perf_counter()
        strategy_plan = self.analyze_requirement_context(requirement, kb_context, client, db)
        if not isinstance(strategy_plan, dict):
            # 中文注释：元分析异常时使用默认策略，避免主链路中断。
            strategy_plan = self._default_strategy_plan()
        _record_timing_event(
            "meta_analysis",
            meta_analysis_started,
            system_type=str(strategy_plan.get("system_type") or ""),
            complexity=str(strategy_plan.get("complexity") or ""),
        )
        yield (
            "@@STATUS@@:分析完成 - 系统类型: "
            f"{strategy_plan.get('system_type')}, 复杂度: {strategy_plan.get('complexity')}, "
            f"策略: {json.dumps(strategy_plan.get('suggested_ratios'))}...\n"
        )

        prompt_context = build_structured_prompt_context(
            requirement=requirement or "",
            architecture_requirement=architecture_requirement,
            kb_context=kb_context or "",
            rag_result=(context_result or {}).get("rag_result") if isinstance(context_result, dict) else None,
            existing_cases=[c for c in existing_cases if isinstance(c, dict)] if isinstance(existing_cases, list) else [],
            current_biz_key=current_biz_key,
            only_current_biz=only_current_biz,
            feedback_control_state=feedback_control_state,
        )
        current_biz_key = str(prompt_context.get("current_biz_key") or current_biz_key or "unknown")
        project_profile = dict(prompt_context.get("project_profile") or {})
        if isinstance(prompt_context.get("feedback_control_state"), dict):
            feedback_control_state = dict(prompt_context.get("feedback_control_state") or {})
        requirement_semantics_context = _extract_requirement_semantics_payload(prompt_context)
        coverage_plan_lite, coverage_plan_rules = _build_stream_coverage_plan_lite(requirement, analyze_coverage_fn=analyze_coverage)
        _emit_biz_key_diag(prompt_context)

        base_prompt = build_closed_loop_base_prompt(
            strategy_plan,
            requirement_context=prompt_context.get("requirement_context") or "",
            requirement_semantics_context=prompt_context.get("requirement_semantics_context") or "",
            testcase_context=prompt_context.get("testcase_context") or "(empty)",
            supplement_context=prompt_context.get("supplement_context") or "(empty)",
            control_context=prompt_context.get("control_context") or "",
            current_biz_key=current_biz_key,
            doc_type=doc_type,
            pretty_json=True,
        )

        full_content = ""

        existing_unique_count = count_unique_test_cases(existing_cases) if isinstance(existing_cases, list) else 0
        batch_plan = resolve_stream_batch_plan(
            expected_count=int(expected_count or 0),
            batch_size=int(batch_size or 0),
            append=bool(append),
            start_id=int(start_id or 1),
            existing_unique_count=int(existing_unique_count or 0),
        )
        batch_size = int(batch_plan["batch_size"])
        if bool(batch_plan.get("auto_extended")):
            yield (
                f"@@STATUS@@:当前用例数({existing_unique_count})已达预期({expected_count})，"
                f"自动增加 {batch_size} 条用例..\n"
            )
        expected_count = int(batch_plan["expected_count"])
        generation_target_count = int(batch_plan["generation_target_count"])
        total_batches = int(batch_plan["total_batches"])
        stream_batch_diags: list[str] = []
        architecture_instruction = build_functional_architecture_instruction(
            project_profile=project_profile,
        )
        architecture = dict(project_profile.get("functional_architecture") or {})
        verified_modules = [
            item
            for item in (architecture.get("functional_modules") or [])
            if isinstance(item, dict) and item.get("evidence_verified") is True
        ]
        verified_interactions = [
            item
            for item in (architecture.get("module_interactions") or [])
            if isinstance(item, dict) and item.get("evidence_verified") is True
        ]
        if architecture_instruction:
            _emit_stream_gen_diag(
                {
                    "kind": "functional_architecture_global_batch_context",
                    "project_id": int(project_id),
                    "request_id": str(request_id or ""),
                    "expected_count": int(expected_count),
                    "generation_target_count": int(generation_target_count),
                    "batch_size": int(batch_size),
                    "total_batches": int(total_batches),
                    "verified_module_count": int(len(verified_modules)),
                    "verified_interaction_count": int(len(verified_interactions)),
                }
            )
            if stream_batch_diags:
                yield stream_batch_diags.pop()
        planned_total_batches = int(total_batches)
        current_id = start_id

        history_summaries, seen_case_signatures = build_existing_case_history(
            existing_cases,
            append=bool(append),
            build_case_signature_fn=_build_case_signature,
        )
        batch_quality_metrics: list[dict[str, Any]] = []
        batch_acceptance_summaries: list[dict[str, Any]] = []
        generated_case_count = 0
        low_gain_streak = 0
        early_stop_triggered = False
        early_stop_reason = ""
        parallel_result_summary: dict[str, Any] = {}

        primary_batches_started = time.perf_counter()
        completed_batches = 0
        parallel_completed = False
        parallel_config = parallel_shard_config_from_settings(settings)
        parallel_allowed, parallel_gate_reason = should_use_parallel_shards(
            expected_count=int(expected_count or 0),
            append=bool(append),
            multi_pass=bool(multi_pass),
            total_batches=int(total_batches),
            coverage_rule_count=int(len(coverage_plan_rules)),
            config=parallel_config,
        )
        if bool(parallel_config.enabled):
            _emit_stream_gen_diag(
                {
                    "kind": "parallel_coverage_shard_gate",
                    "project_id": int(project_id),
                    "request_id": str(request_id or ""),
                    "enabled": bool(parallel_config.enabled),
                    "allowed": bool(parallel_allowed),
                    "gate_reason": str(parallel_gate_reason or ""),
                    "expected_count": int(expected_count or 0),
                    "total_batches": int(total_batches),
                    "coverage_rule_count": int(len(coverage_plan_rules)),
                    "max_workers": int(parallel_config.max_workers),
                    "min_expected_count": int(parallel_config.min_expected_count),
                    "min_coverage_rules": int(parallel_config.min_coverage_rules),
                }
            )
            if stream_batch_diags:
                yield stream_batch_diags.pop()

        if parallel_allowed:
            parallel_started = time.perf_counter()
            parallel_fallback_reason = ""
            shard_plan = build_coverage_shard_plan(
                coverage_plan_rules,
                expected_count=int(expected_count or 0),
                max_workers=int(parallel_config.max_workers),
                max_cases_per_worker=int(batch_size or 25),
            )
            if len(shard_plan) < 2:
                parallel_fallback_reason = "shard_plan_too_small"
            else:
                yield (
                    "@@STATUS@@:并发覆盖分片生成已启用"
                    f"（{len(shard_plan)} 个分片，并发度 {parallel_config.max_workers}）...\n"
                )
                _emit_stream_gen_diag(
                    {
                        "kind": "parallel_coverage_shard_plan",
                        "project_id": int(project_id),
                        "request_id": str(request_id or ""),
                        "shard_count": int(len(shard_plan)),
                        "expected_count": int(expected_count or 0),
                        "max_workers": int(parallel_config.max_workers),
                        "shards": [
                            {
                                "shard_id": str(shard.get("shard_id") or ""),
                                "target_count": int(shard.get("target_count") or 0),
                                "rule_ids": list(shard.get("rule_ids") or [])[:20],
                            }
                            for shard in shard_plan
                            if isinstance(shard, dict)
                        ],
                    }
                )
                if stream_batch_diags:
                    yield stream_batch_diags.pop()

                history_context_str = build_recent_history_context(history_summaries)
                side_suite_order = execution_side_suite_order_text()
                shard_requests: list[dict[str, Any]] = []
                client_factory = state.get("parallel_shard_client_factory")
                previous_target_count = 0
                for shard in shard_plan:
                    shard_client = None
                    if callable(client_factory):
                        shard_client = client_factory(dict(shard))
                    elif self._is_active_db_session(db):
                        shard_client = get_client_for_user(user_id, db)
                    if shard_client is None:
                        parallel_fallback_reason = "parallel_client_unavailable"
                        break
                    shard_instruction = build_parallel_shard_instruction(shard)
                    if required_stage_coverage_instruction:
                        shard_instruction = (
                            f"{shard_instruction}\n\n{required_stage_coverage_instruction}"
                        )
                    shard_need = max(1, int(shard.get("target_count") or 1))
                    shard_system_prompt = build_stream_batch_system_prompt(
                        base_prompt=base_prompt,
                        coverage_instruction="",
                        history_context=history_context_str,
                        coverage_plan_lite=coverage_plan_lite,
                        side_suite_order=side_suite_order,
                        batch_index=max(0, int(shard.get("shard_index") or 1) - 1),
                        total_batches=int(len(shard_plan)),
                        current_id=int(start_id + previous_target_count),
                        generated_in_batch=0,
                        need=int(shard_need),
                        shard_instruction=shard_instruction,
                        architecture_instruction=architecture_instruction,
                    )
                    _emit_prompt_context_intake_diag(
                        prompt_context=prompt_context,
                        base_prompt_text=base_prompt,
                        system_prompt_text=shard_system_prompt,
                        batch_index_value=int(shard.get("shard_index") or 1),
                        total_batches_value=int(len(shard_plan)),
                        attempt_value=1,
                        requested_count=int(shard_need),
                    )
                    if stream_batch_diags:
                        yield stream_batch_diags.pop()
                    shard_requests.append(
                        {
                            "request_id": str(request_id or ""),
                            "shard": dict(shard),
                            "client": shard_client,
                            "system_prompt": shard_system_prompt,
                        }
                    )
                    system_prompt = shard_system_prompt
                    previous_target_count += shard_need

                if not parallel_fallback_reason and not final_trace_emitted:
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
                    final_trace_emitted = True

                shard_results: list[dict[str, Any]] = []
                if not parallel_fallback_reason:
                    try:
                        shard_results = execute_parallel_shard_requests(
                            requests=shard_requests,
                            requirement=requirement,
                            clean_and_parse_json_fn=clean_and_parse_json,
                            normalize_json_structure_fn=_normalize_generated_json_structure,
                            max_workers=int(parallel_config.max_workers),
                        )
                    except Exception as exc:
                        parallel_fallback_reason = f"parallel_executor_exception:{str(exc)[:120]}"

                if not parallel_fallback_reason:
                    failed_results = [
                        result
                        for result in shard_results
                        if str(result.get("status") or "") not in {"parsed"}
                    ]
                    for result in shard_results:
                        shard = result.get("shard") if isinstance(result.get("shard"), dict) else {}
                        _record_timing_event(
                            "parallel_shard_attempt",
                            parallel_started,
                            shard_id=str(shard.get("shard_id") or ""),
                            shard_index=int(shard.get("shard_index") or 0),
                            requested_count=int(shard.get("target_count") or 0),
                            response_case_count=int(result.get("response_case_count") or 0),
                            duration_ms=int(result.get("duration_ms") or 0),
                            attempt_status=str(result.get("status") or ""),
                            provider_error=str(result.get("error") or "")[:200],
                        )
                    if failed_results:
                        parallel_fallback_reason = "shard_failed"

                merge_result: dict[str, Any] = {}
                parallel_incomplete_rows: list[dict[str, Any]] = []
                parallel_module_contract_summary: dict[str, Any] = {}
                if not parallel_fallback_reason:
                    merge_result = merge_parallel_shard_cases(
                        shard_results,
                        build_case_signature_fn=_build_case_signature,
                        start_id=int(start_id or 1),
                        expected_count=int(expected_count or 0),
                    )
                    parallel_acceptance = accept_stream_batch_candidates(
                        [case for case in (merge_result.get("cases") or []) if isinstance(case, dict)],
                        limit=int(expected_count or 0),
                        start_id=int(start_id or 1),
                        project_profile=project_profile,
                        select_complete_generated_cases_fn=select_complete_generated_cases,
                        is_placeholder_expected_result_fn=is_placeholder_expected_result,
                        enforce_functional_module_contract_fn=enforce_functional_module_contract,
                    )
                    merge_result["cases"] = list(parallel_acceptance.cases)
                    parallel_incomplete_rows = list(parallel_acceptance.incomplete_rows)
                    parallel_module_contract_summary = dict(
                        parallel_acceptance.module_contract_summary or {}
                    )
                    batch_acceptance_summaries.append(
                        {
                            "source": "parallel_shards",
                            **parallel_module_contract_summary,
                        }
                    )
                    unique_case_count = int(merge_result.get("unique_case_count") or 0)
                    input_case_count = int(merge_result.get("input_case_count") or 0)
                    duplicate_rate = float(merge_result.get("duplicate_rate") or 0.0)
                    unique_ratio = float(unique_case_count) / float(expected_count or 1)
                    if input_case_count <= 0:
                        parallel_fallback_reason = "empty_parallel_result"
                    elif parallel_incomplete_rows:
                        parallel_fallback_reason = "incomplete_case_schema"
                    elif int(
                        parallel_module_contract_summary.get(
                            "module_rejected_case_count"
                        )
                        or 0
                    ) > 0:
                        parallel_fallback_reason = "functional_module_contract_rejected"
                    elif duplicate_rate > float(parallel_config.duplicate_rate_abort):
                        parallel_fallback_reason = "duplicate_rate_above_threshold"
                    elif unique_ratio < float(parallel_config.min_unique_ratio):
                        parallel_fallback_reason = "unique_ratio_below_threshold"

                parallel_result_summary = {
                    "kind": "parallel_coverage_shard_result",
                    "project_id": int(project_id),
                    "request_id": str(request_id or ""),
                    "status": "fallback" if parallel_fallback_reason else "accepted",
                    "fallback_reason": str(parallel_fallback_reason or ""),
                    "shard_count": int(len(shard_plan)),
                    "input_case_count": int(merge_result.get("input_case_count") or 0),
                    "unique_case_count": int(merge_result.get("unique_case_count") or 0),
                    "accepted_case_count": int(len(merge_result.get("cases") or [])),
                    "duplicate_count": int(merge_result.get("duplicate_count") or 0),
                    "duplicate_rate": float(merge_result.get("duplicate_rate") or 0.0),
                    "min_unique_ratio": float(parallel_config.min_unique_ratio),
                    "duplicate_rate_abort": float(parallel_config.duplicate_rate_abort),
                    "incomplete_case_count": int(len(parallel_incomplete_rows)),
                    "incomplete_case_samples": parallel_incomplete_rows[:10],
                    "functional_module_contract": dict(
                        parallel_module_contract_summary or {}
                    ),
                    "per_shard_counts": list(merge_result.get("per_shard_counts") or [])[:10],
                    "shard_results": [
                        {
                            "shard_id": str((result.get("shard") or {}).get("shard_id") or ""),
                            "status": str(result.get("status") or ""),
                            "duration_ms": int(result.get("duration_ms") or 0),
                            "response_case_count": int(result.get("response_case_count") or 0),
                            "model": str((result.get("metadata") or {}).get("model") or ""),
                            "input_tokens": (result.get("metadata") or {}).get("input_tokens"),
                            "output_tokens": (result.get("metadata") or {}).get("output_tokens"),
                        }
                        for result in shard_results
                        if isinstance(result, dict)
                    ][:10],
                }
                _emit_stream_gen_diag(parallel_result_summary)
                if stream_batch_diags:
                    yield stream_batch_diags.pop()

                _record_timing_event(
                    "parallel_shard_generation",
                    parallel_started,
                    shard_count=int(len(shard_plan)),
                    max_workers=int(parallel_config.max_workers),
                    accepted=not bool(parallel_fallback_reason),
                    fallback_reason=str(parallel_fallback_reason or ""),
                    input_case_count=int(merge_result.get("input_case_count") or 0),
                    unique_case_count=int(merge_result.get("unique_case_count") or 0),
                    accepted_case_count=int(len(merge_result.get("cases") or [])),
                    duplicate_rate=float(merge_result.get("duplicate_rate") or 0.0),
                )

                if parallel_fallback_reason:
                    yield (
                        "@@STATUS@@:并发覆盖分片未通过质量门禁"
                        f"（{parallel_fallback_reason}），回退串行批次生成...\n"
                    )
                else:
                    parsed_batch_cases = [
                        case for case in (merge_result.get("cases") or []) if isinstance(case, dict)
                    ]
                    if parsed_batch_cases:
                        accepted_semantic_cases.extend(parsed_batch_cases)
                        required_stage_coverage = evaluate_required_stage_candidate_coverage(
                            accepted_semantic_cases,
                            workflow_blueprints=requirement_workflow_blueprints,
                        )
                        required_stage_coverage_instruction = (
                            build_required_stage_coverage_instruction(
                                required_stage_coverage
                            )
                        )
                        generated_case_count += int(len(parsed_batch_cases))
                        full_content += json.dumps(parsed_batch_cases, ensure_ascii=False, indent=2)
                        full_content += "\n"
                        yield _public_case_batch_json(parsed_batch_cases)
                        yield "\n"
                        for case in parsed_batch_cases:
                            history_summaries.append(f"{case.get('id', '')}: {case.get('description', '')}")
                        batch_metric, low_gain_streak = build_stream_batch_quality_metric(
                            parsed_batch_cases=parsed_batch_cases,
                            seen_case_signatures=seen_case_signatures,
                            batch_index=1,
                            build_case_signature_fn=_build_case_signature,
                            is_non_assertable_expected_result_fn=_is_non_assertable_expected_result,
                            previous_low_gain_streak=0,
                        )
                        batch_metric.update(
                            {
                                "parallel_shards_used": True,
                                "parallel_shard_count": int(len(shard_plan)),
                                "parallel_input_case_count": int(merge_result.get("input_case_count") or 0),
                                "parallel_duplicate_count": int(merge_result.get("duplicate_count") or 0),
                            }
                        )
                        batch_quality_metrics.append(batch_metric)
                        _emit_stream_batch_quality_diag(batch_quality_metrics[-1])
                        if stream_batch_diags:
                            yield stream_batch_diags.pop()
                    else:
                        full_content += "[]\n"
                    completed_batches = int(len(shard_plan))
                    current_id = int(start_id or 1) + int(len(parsed_batch_cases))
                    parallel_completed = True

        if parallel_completed:
            total_batches = 0

        for batch_index in range(total_batches):
            remaining = generation_target_count - (current_id - start_id)
            current_batch_count = min(batch_size, remaining)
            if current_batch_count <= 0:
                break

            generated_in_batch = 0
            attempt = 0
            parsed_batch_cases: list[dict[str, Any]] = []
            incomplete_case_count = 0
            semantic_retry_instruction = ""

            while generated_in_batch < current_batch_count and attempt < 3:
                need = current_batch_count - generated_in_batch
                attempt += 1
                attempt_started = time.perf_counter()
                yield (
                    f"@@STATUS@@:正在生成第 {batch_index + 1}/{total_batches} 批次"
                    f" ({current_batch_count} 条) - 第 {attempt} 次尝试...\n"
                )

                history_context_str = build_recent_history_context(history_summaries)

                prompt_context = build_structured_prompt_context(
                    requirement=requirement or "",
                    architecture_requirement=architecture_requirement,
                    kb_context=kb_context or "",
                    rag_result=(context_result or {}).get("rag_result") if isinstance(context_result, dict) else None,
                    existing_cases=[c for c in existing_cases if isinstance(c, dict)] if isinstance(existing_cases, list) else [],
                    current_biz_key=current_biz_key,
                    only_current_biz=only_current_biz,
                    feedback_control_state=feedback_control_state,
                )
                current_biz_key = str(prompt_context.get("current_biz_key") or current_biz_key or "unknown")
                if isinstance(prompt_context.get("feedback_control_state"), dict):
                    feedback_control_state = dict(prompt_context.get("feedback_control_state") or {})
                requirement_semantics_context = _extract_requirement_semantics_payload(prompt_context)
                _emit_biz_key_diag(prompt_context)

                testcase_context = append_history_to_testcase_context(
                    prompt_context.get("testcase_context") or "(empty)",
                    history_summaries,
                )

                base_prompt = build_closed_loop_base_prompt(
                    strategy_plan,
                    requirement_context=prompt_context.get("requirement_context") or "",
                    requirement_semantics_context=prompt_context.get("requirement_semantics_context") or "",
                    testcase_context=testcase_context,
                    supplement_context=prompt_context.get("supplement_context") or "(empty)",
                    control_context=prompt_context.get("control_context") or "",
                    current_biz_key=current_biz_key,
                    doc_type=doc_type,
                    pretty_json=True,
                )

                coverage_instruction = ""
                if append and existing_cases:
                    coverage_instruction = build_append_closed_loop_coverage_instruction(
                        existing_cases=[c for c in existing_cases if isinstance(c, dict)],
                        requirement=requirement,
                        expected_count=expected_count,
                        infer_case_kind_fn=infer_case_kind,
                    )
                side_suite_order = execution_side_suite_order_text()

                system_prompt = build_stream_batch_system_prompt(
                    base_prompt=base_prompt,
                    coverage_instruction=coverage_instruction,
                    history_context=history_context_str,
                    coverage_plan_lite=coverage_plan_lite,
                    side_suite_order=side_suite_order,
                    batch_index=batch_index,
                    total_batches=total_batches,
                    current_id=current_id,
                    generated_in_batch=generated_in_batch,
                    need=need,
                    architecture_instruction=architecture_instruction,
                )
                if semantic_retry_instruction:
                    system_prompt = f"{system_prompt}\n\n{semantic_retry_instruction}"
                if required_stage_coverage_instruction:
                    system_prompt = (
                        f"{system_prompt}\n\n{required_stage_coverage_instruction}"
                    )

                if not final_trace_emitted:
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
                    final_trace_emitted = True

                if attempt == 1:
                    _emit_prompt_context_intake_diag(
                        prompt_context=prompt_context,
                        base_prompt_text=base_prompt,
                        system_prompt_text=system_prompt,
                        batch_index_value=batch_index + 1,
                        total_batches_value=total_batches,
                        attempt_value=attempt,
                        requested_count=need,
                    )
                    if stream_batch_diags:
                        yield stream_batch_diags.pop()

                stream = client.generate_response_stream(
                    requirement,
                    system_prompt,
                    task_type="generation",
                )
                chunk_acc = ""
                attempt_content = ""
                provider_error = None
                for chunk in stream:
                    chunk_acc += chunk
                    attempt_content += chunk
                    if chunk.startswith("Error:") or chunk.startswith("[额度耗尽]") or chunk.startswith("Exception occurred:"):
                        provider_error = chunk
                        break

                attempt_status = "received"
                if provider_error:
                    attempt_status = "provider_error"
                elif not chunk_acc.strip():
                    attempt_status = "empty_response"
                attempt_timing_event = _record_timing_event(
                    "primary_batch_attempt",
                    attempt_started,
                    batch_index=int(batch_index + 1),
                    total_batches=int(total_batches),
                    attempt=int(attempt),
                    requested_count=int(need),
                    response_chars=int(len(attempt_content or "")),
                    attempt_status=attempt_status,
                    provider_error=str(provider_error or "")[:200],
                )

                token_usage_diag = build_stream_batch_token_usage(
                    client=client,
                    project_id=project_id,
                    request_id=request_id,
                    current_biz_key=current_biz_key,
                    multi_pass=multi_pass,
                    generation_mode=generation_mode,
                    batch_index=batch_index + 1,
                    total_batches=total_batches,
                    attempt=attempt,
                    need=need,
                    system_prompt_text=system_prompt,
                    requirement_text=requirement,
                    output_text=attempt_content,
                    duration_ms=int(attempt_timing_event.get("duration_ms") or 0),
                    response_chars=int(len(attempt_content or "")),
                    attempt_status=attempt_status,
                    provider_error=str(provider_error or ""),
                )
                _emit_stream_batch_token_usage_diag(token_usage_diag)
                if stream_batch_diags:
                    yield stream_batch_diags.pop()

                if not provider_error and not chunk_acc.strip():
                    attempt_timing_event["retry_scheduled"] = bool(attempt < 3)
                    if attempt < 3:
                        yield "\n@@STATUS@@:模型未返回内容，正在重试...\n"
                        continue
                    yield "\n@@STATUS@@:生成失败\n"
                    yield "Error: 模型未返回内容（可能是模型配置/额度/网络/内容安全导致），请检查后重试\n"
                    attempt = 3
                    break

                if provider_error:
                    attempt_timing_event["retry_scheduled"] = bool(
                        attempt < 3 and _is_retryable_provider_error(provider_error)
                    )
                    if attempt < 3 and _is_retryable_provider_error(provider_error):
                        yield "\n@@STATUS@@:模型连接中断，正在重试当前批次...\n"
                        continue
                    yield "\n@@STATUS@@:生成失败\n"
                    yield f"{provider_error}\n"
                    attempt = 3
                    break

                yield "\n"

                try:
                    raw_parsed_batch = clean_and_parse_json(attempt_content)
                    raw_model_cases = (
                        [case for case in raw_parsed_batch if isinstance(case, dict)]
                        if isinstance(raw_parsed_batch, list)
                        else []
                    )
                    rejection_start = int(len(case_semantic_rejections))
                    parsed_batch = _normalize_generated_json_structure(raw_parsed_batch)
                    new_semantic_rejections = list(
                        case_semantic_rejections[rejection_start:]
                    )
                    if isinstance(parsed_batch, list):
                        raw_attempt_cases = [case for case in parsed_batch if isinstance(case, dict)]
                        batch_acceptance = accept_stream_batch_candidates(
                            raw_attempt_cases,
                            limit=int(need),
                            start_id=int(current_id + generated_in_batch),
                            project_profile=project_profile,
                            select_complete_generated_cases_fn=select_complete_generated_cases,
                            is_placeholder_expected_result_fn=is_placeholder_expected_result,
                            enforce_functional_module_contract_fn=enforce_functional_module_contract,
                        )
                        accepted_attempt = list(batch_acceptance.cases)
                        incomplete_rows = list(batch_acceptance.incomplete_rows)
                        module_contract_summary = dict(
                            batch_acceptance.module_contract_summary or {}
                        )
                        module_rejected_case_count = int(
                            module_contract_summary.get("module_rejected_case_count")
                            or 0
                        )
                        batch_acceptance_summaries.append(
                            {
                                "source": "serial_batch",
                                "batch_index": int(batch_index + 1),
                                "attempt": int(attempt),
                                **module_contract_summary,
                            }
                        )
                        parsed_batch_cases.extend(accepted_attempt)
                        accepted_semantic_cases.extend(accepted_attempt)
                        required_stage_coverage = evaluate_required_stage_candidate_coverage(
                            accepted_semantic_cases,
                            workflow_blueprints=requirement_workflow_blueprints,
                        )
                        required_stage_coverage_instruction = (
                            build_required_stage_coverage_instruction(
                                required_stage_coverage
                            )
                        )
                        generated_in_batch += int(len(accepted_attempt))
                        incomplete_case_count += int(
                            len(incomplete_rows)
                            + len(new_semantic_rejections)
                            + module_rejected_case_count
                        )
                        attempt_timing_event["parsed_case_count"] = int(
                            len(raw_model_cases) or len(raw_attempt_cases)
                        )
                        attempt_timing_event["accepted_case_count"] = int(len(accepted_attempt))
                        attempt_timing_event["incomplete_case_count"] = int(len(incomplete_rows))
                        attempt_timing_event["semantic_rejected_case_count"] = int(
                            len(new_semantic_rejections)
                        )
                        attempt_timing_event["module_contract_rejected_case_count"] = int(
                            module_rejected_case_count
                        )
                        attempt_timing_event["module_contract_normalized_count"] = int(
                            module_contract_summary.get("normalized_count") or 0
                        )
                        if new_semantic_rejections or module_rejected_case_count:
                            attempt_timing_event["attempt_status"] = (
                                "candidate_contract_partial"
                                if accepted_attempt
                                else "candidate_contract_rejected"
                            )
                            if new_semantic_rejections:
                                semantic_retry_instruction = build_case_semantic_retry_instruction(
                                    new_semantic_rejections
                                )
                        else:
                            attempt_timing_event["attempt_status"] = (
                                "parsed_partial" if incomplete_rows and accepted_attempt
                                else "schema_incomplete" if incomplete_rows
                                else "parsed"
                            )
                        for case in accepted_attempt:
                            history_summaries.append(f"{case.get('id', '')}: {case.get('description', '')}")
                        if incomplete_rows:
                            _emit_stream_batch_quality_diag(
                                {
                                    "batch_index": int(batch_index + 1),
                                    "attempt": int(attempt),
                                    "requested_count": int(need),
                                    "accepted_case_count": int(len(accepted_attempt)),
                                    "incomplete_case_count": int(len(incomplete_rows)),
                                    "incomplete_case_samples": incomplete_rows[:10],
                                    "source_regeneration_scheduled": bool(
                                        generated_in_batch < current_batch_count and attempt < 3
                                    ),
                                }
                            )
                            if stream_batch_diags:
                                yield stream_batch_diags.pop()
                        if new_semantic_rejections:
                            _emit_stream_batch_quality_diag(
                                {
                                    "batch_index": int(batch_index + 1),
                                    "attempt": int(attempt),
                                    "requested_count": int(need),
                                    "accepted_case_count": int(len(accepted_attempt)),
                                    "semantic_rejected_case_count": int(
                                        len(new_semantic_rejections)
                                    ),
                                    "source_regeneration_scheduled": bool(
                                        generated_in_batch < current_batch_count and attempt < 3
                                    ),
                                }
                            )
                            if stream_batch_diags:
                                yield stream_batch_diags.pop()
                        if module_rejected_case_count:
                            _emit_stream_batch_quality_diag(
                                {
                                    "batch_index": int(batch_index + 1),
                                    "attempt": int(attempt),
                                    "requested_count": int(need),
                                    "accepted_case_count": int(len(accepted_attempt)),
                                    "module_contract_rejected_case_count": int(
                                        module_rejected_case_count
                                    ),
                                    "functional_module_contract": dict(
                                        module_contract_summary or {}
                                    ),
                                    "source_regeneration_scheduled": bool(
                                        generated_in_batch < current_batch_count
                                        and attempt < 3
                                    ),
                                }
                            )
                            if stream_batch_diags:
                                yield stream_batch_diags.pop()
                        if generated_in_batch >= current_batch_count:
                            break
                        if (
                            module_rejected_case_count
                            and not incomplete_rows
                            and not new_semantic_rejections
                            and attempt < 3
                        ):
                            yield "@@STATUS@@:检测到用例模块或交互契约冲突，正在按全局功能架构重新生成...\n"
                            continue
                        if (
                            incomplete_rows
                            or new_semantic_rejections
                            or module_rejected_case_count
                        ) and attempt < 3:
                            if new_semantic_rejections:
                                yield "@@STATUS@@:检测到模型用例语义契约不完整，正在按字段级反馈重新生成...\n"
                            else:
                                yield "@@STATUS@@:检测到模型缺失必填字段，正在基于当前需求补充生成...\n"
                            continue
                        break
                except Exception:
                    attempt_timing_event["attempt_status"] = "parse_failed"
                    pass

            if parsed_batch_cases:
                generated_case_count += int(len(parsed_batch_cases))
                full_content += json.dumps(parsed_batch_cases, ensure_ascii=False, indent=2)
                full_content += "\n"
                yield _public_case_batch_json(parsed_batch_cases)
                yield "\n"
            else:
                full_content += "[]\n"

            if parsed_batch_cases:
                batch_metric, low_gain_streak = build_stream_batch_quality_metric(
                    parsed_batch_cases=parsed_batch_cases,
                    seen_case_signatures=seen_case_signatures,
                    batch_index=int(batch_index + 1),
                    build_case_signature_fn=_build_case_signature,
                    is_non_assertable_expected_result_fn=_is_non_assertable_expected_result,
                    previous_low_gain_streak=int(low_gain_streak),
                )
                batch_metric["source_incomplete_case_count"] = int(incomplete_case_count)
                batch_quality_metrics.append(batch_metric)
                _emit_stream_batch_quality_diag(batch_quality_metrics[-1])
                if stream_batch_diags:
                    yield stream_batch_diags.pop()

                if low_gain_streak >= 2:
                    _emit_stream_batch_quality_diag(
                        {
                            "batch_index": int(batch_index + 1),
                            "early_stop_triggered": False,
                            "global_batches_continue": True,
                            "diagnostic_reason": "local_low_incremental_gain",
                            "low_gain_streak": int(low_gain_streak),
                        }
                    )
                    if stream_batch_diags:
                        yield stream_batch_diags.pop()
            else:
                low_gain_streak = 0

            current_id += int(len(parsed_batch_cases))
            completed_batches += 1
        _record_timing_event(
            "primary_batches",
            primary_batches_started,
            total_batches=int(planned_total_batches),
            completed_batches=int(completed_batches),
            batch_size=int(batch_size),
            expected_count=int(expected_count or 0),
            early_stop_triggered=bool(early_stop_triggered),
            early_stop_reason=str(early_stop_reason or ""),
        )
        _record_timing_event(
            "stream_generation_phase",
            phase_started,
            total_batches=int(planned_total_batches),
            completed_batches=int(completed_batches),
        )
        if required_stage_coverage.get("active") is True:
            _emit_stream_gen_diag(
                {
                    "kind": "required_stage_candidate_coverage",
                    "request_id": request_id,
                    "workflow_id": str(
                        required_stage_coverage.get("workflow_id") or ""
                    ),
                    "required_stage_ids": list(
                        required_stage_coverage.get("required_stage_ids") or []
                    ),
                    "covered_required_stage_ids": list(
                        required_stage_coverage.get(
                            "covered_required_stage_ids"
                        )
                        or []
                    ),
                    "missing_required_stage_ids": list(
                        required_stage_coverage.get(
                            "missing_required_stage_ids"
                        )
                        or []
                    ),
                    "candidate_edge_count": int(
                        required_stage_coverage.get("candidate_edge_count") or 0
                    ),
                }
            )
        if case_semantic_rejections:
            _emit_stream_gen_diag(
                {
                    "kind": "case_semantic_contract_rejections",
                    "request_id": request_id,
                    "rejected_count": int(len(case_semantic_rejections)),
                    "rejections": list(case_semantic_rejections)[:20],
                }
            )

        state.update(
            {
                "requirement": requirement,
                "kb_context": kb_context,
                "expected_count": expected_count,
                "batch_size": batch_size,
                "start_id": start_id,
                "existing_cases": existing_cases,
                "existing_unique_count": existing_unique_count,
                "base_prompt": base_prompt,
                "full_content": full_content,
                "context_result": context_result if isinstance(context_result, dict) else {},
                "gate_debug": gate_debug if isinstance(gate_debug, dict) else {},
                "system_prompt": system_prompt if isinstance(system_prompt, str) else "",
                "current_biz_key": current_biz_key,
                "only_current_biz": only_current_biz,
                "multi_pass": multi_pass,
                "generation_mode": generation_mode,
                "feedback_control_state": feedback_control_state if isinstance(feedback_control_state, dict) else {},
                "requirement_semantics_context": requirement_semantics_context,
                "stream_coverage_plan_lite": coverage_plan_lite,
                "stream_coverage_plan_rule_count": int(len(coverage_plan_rules)),
                "stream_batch_quality_metrics": batch_quality_metrics,
                "stream_batch_acceptance_summaries": list(
                    batch_acceptance_summaries
                ),
                "stream_early_stop_triggered": bool(early_stop_triggered),
                "stream_early_stop_reason": str(early_stop_reason or ""),
                "stream_parallel_shards_enabled": bool(parallel_config.enabled),
                "stream_parallel_shards_used": bool(parallel_completed),
                "stream_parallel_shard_gate_reason": str(parallel_gate_reason or ""),
                "stream_parallel_shard_result": parallel_result_summary if isinstance(parallel_result_summary, dict) else {},
                "case_semantic_rejections": list(case_semantic_rejections),
                "case_semantic_rejection_count": int(len(case_semantic_rejections)),
                "case_semantic_accepted_count": int(generated_case_count),
                "case_semantic_contract_failed": bool(
                    require_case_semantic_contract
                    and case_semantic_rejections
                    and generated_case_count <= 0
                ),
                "required_stage_candidate_coverage": dict(
                    required_stage_coverage or {}
                ),
                "generation_timing_events": timing_events,
            }
        )
        return state
