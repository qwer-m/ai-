import json
import os
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
    assign_public_batch_merge_gap_repair_targets,
    build_public_batch_execution_plan,
    build_public_owned_shard_plan,
    group_shard_requests_by_public_batch,
    build_existing_case_history,
    build_stream_batch_quality_metric,
    resolve_stream_batch_plan,
    select_complete_generated_cases,
)
from .batch_parallel_shards import (
    assign_cross_shard_duplicate_repair_targets,
    build_parallel_gap_repair_requests,
    build_parallel_shard_instruction,
    merge_parallel_shard_attempts,
    merge_parallel_shard_cases,
    merge_public_batch_against_accepted_history,
    normalize_and_accept_parallel_shard_results,
    parallel_shard_config_from_settings,
    should_use_parallel_shards,
    stream_parallel_shard_requests,
)
from .batch_prompt_runtime import (
    build_functional_architecture_instruction,
    build_recent_history_context,
    build_stream_batch_system_prompt,
)
from .runtime import LazyAttrProxy, call_component
from ...control.semantic_contract import resolve_case_semantic_gate
from ...postprocess.streaming_case_normalization import is_placeholder_expected_result


LogEntry = LazyAttrProxy("core.db.models", "LogEntry")
settings = LazyAttrProxy("core.settings.config", "settings")

_STREAM_BATCH_REQUEST_TIMEOUT_ENV = "GENERATION_STREAM_BATCH_REQUEST_TIMEOUT_SECONDS"
_STREAM_BATCH_HEARTBEAT_INTERVAL_ENV = "GENERATION_STREAM_BATCH_HEARTBEAT_INTERVAL_SECONDS"


def _bounded_env_seconds(
    name: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        value = float(default)
    return max(float(minimum), min(float(maximum), value))


def stream_batch_request_timeout_seconds() -> float:
    """主生成单批硬超时，避免一次模型请求越过外层网关生命周期。"""

    return _bounded_env_seconds(
        _STREAM_BATCH_REQUEST_TIMEOUT_ENV,
        default=180.0,
        minimum=30.0,
        maximum=360.0,
    )


def stream_batch_heartbeat_interval_seconds() -> float:
    """主生成等待期间的下行心跳间隔，仅用于保持流连接活跃。"""

    return _bounded_env_seconds(
        _STREAM_BATCH_HEARTBEAT_INTERVAL_ENV,
        default=15.0,
        minimum=5.0,
        maximum=60.0,
    )


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


def build_generation_shard_control_context(*args: Any, **kwargs: Any) -> Any:
    return call_component(
        "...prompting.structured_control_context",
        "_build_control_context",
        *args,
        **kwargs,
    )


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


def _required_stage_coverage_identity(
    coverage: dict[str, Any],
) -> dict[str, Any]:
    """提取批次冻结前后必选阶段覆盖的稳定身份。"""

    return {
        "active": bool(coverage.get("active")),
        "workflow_id": str(coverage.get("workflow_id") or ""),
        "required_stage_ids": list(coverage.get("required_stage_ids") or []),
        "covered_required_stage_ids": list(
            coverage.get("covered_required_stage_ids") or []
        ),
        "missing_required_stage_ids": list(
            coverage.get("missing_required_stage_ids") or []
        ),
        "required_stage_coverage_complete": bool(
            coverage.get("required_stage_coverage_complete")
        ),
    }


def _build_frozen_public_batch_emission(
    *,
    accepted_public_batch_cases: list[dict[str, Any]],
    public_batch_execution_plan: list[dict[str, Any]],
    expected_count: int,
    workflow_blueprints: list[dict[str, Any]],
) -> tuple[list[tuple[dict[str, Any], list[dict[str, Any]]]], dict[str, Any]]:
    """从已验收冻结集切分公共批次，禁止从原始分片二次重建。"""

    frozen_cases = [
        dict(case)
        for case in accepted_public_batch_cases
        if isinstance(case, dict)
    ]
    emitted_batches: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    batch_count_mismatches: list[dict[str, int]] = []
    for public_batch in public_batch_execution_plan:
        batch = dict(public_batch)
        batch_index = int(batch.get("batch_index") or 0)
        offset = max(0, int(batch.get("start_offset") or 0))
        target_count = max(0, int(batch.get("target_count") or 0))
        owned_cases = [
            dict(case)
            for case in frozen_cases[offset : offset + target_count]
        ]
        emitted_batches.append((batch, owned_cases))
        if len(owned_cases) != target_count:
            batch_count_mismatches.append(
                {
                    "batch_index": batch_index,
                    "expected_count": target_count,
                    "actual_count": int(len(owned_cases)),
                }
            )

    emitted_cases = [
        dict(case)
        for _batch, batch_cases in emitted_batches
        for case in batch_cases
    ]
    frozen_signatures = [
        str(_build_case_signature(case) or "")
        for case in frozen_cases
    ]
    emitted_signatures = [
        str(_build_case_signature(case) or "")
        for case in emitted_cases
    ]
    frozen_coverage = evaluate_required_stage_candidate_coverage(
        frozen_cases,
        workflow_blueprints=workflow_blueprints,
    )
    emitted_coverage = evaluate_required_stage_candidate_coverage(
        emitted_cases,
        workflow_blueprints=workflow_blueprints,
    )
    frozen_coverage_identity = _required_stage_coverage_identity(
        dict(frozen_coverage or {})
    )
    emitted_coverage_identity = _required_stage_coverage_identity(
        dict(emitted_coverage or {})
    )
    expected_count = max(0, int(expected_count or 0))
    planned_count = sum(
        max(0, int(batch.get("target_count") or 0))
        for batch in public_batch_execution_plan
    )
    failure_reasons: list[str] = []
    if planned_count != expected_count:
        failure_reasons.append("public_batch_plan_count_mismatch")
    if len(frozen_cases) != expected_count:
        failure_reasons.append("frozen_case_count_mismatch")
    if len(emitted_cases) != expected_count or batch_count_mismatches:
        failure_reasons.append("emitted_case_count_mismatch")
    if frozen_signatures != emitted_signatures:
        failure_reasons.append("deterministic_signature_mismatch")
    if frozen_coverage_identity != emitted_coverage_identity:
        failure_reasons.append("required_stage_coverage_mismatch")

    diagnostic = {
        "kind": "parallel_public_batch_emission_consistency",
        "passed": not bool(failure_reasons),
        "failure_reasons": failure_reasons,
        "expected_count": expected_count,
        "planned_count": int(planned_count),
        "frozen_case_count": int(len(frozen_cases)),
        "emitted_case_count": int(len(emitted_cases)),
        "batch_count_mismatches": batch_count_mismatches,
        "deterministic_signature_match": frozen_signatures == emitted_signatures,
        "required_stage_coverage_match": (
            frozen_coverage_identity == emitted_coverage_identity
        ),
        "frozen_required_stage_coverage": frozen_coverage_identity,
        "emitted_required_stage_coverage": emitted_coverage_identity,
    }
    return emitted_batches, diagnostic


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
                source_requirement=architecture_requirement,
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
        requested_batch_size = int(batch_size or 0)
        batch_plan = resolve_stream_batch_plan(
            expected_count=int(expected_count or 0),
            batch_size=requested_batch_size,
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
        last_batch_target_count = (
            generation_target_count - (batch_size * (total_batches - 1))
            if total_batches > 0
            else 0
        )
        _emit_stream_gen_diag(
            {
                "kind": "stream_batch_plan",
                "project_id": int(project_id),
                "request_id": str(request_id or ""),
                "append": bool(append),
                "requested_batch_size": int(requested_batch_size),
                "effective_batch_size": int(batch_size),
                "expected_count": int(expected_count),
                "existing_unique_count": int(existing_unique_count),
                "generation_target_count": int(generation_target_count),
                "total_batches": int(total_batches),
                "last_batch_target_count": int(last_batch_target_count),
                "auto_extended": bool(batch_plan.get("auto_extended")),
            }
        )
        if stream_batch_diags:
            yield stream_batch_diags.pop()
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
        batch_underfill_summaries: list[dict[str, Any]] = []
        generated_case_count = 0
        low_gain_streak = 0
        early_stop_triggered = False
        early_stop_reason = ""
        parallel_result_summary: dict[str, Any] = {}
        parallel_public_batch_failure: dict[str, Any] = {}
        public_batch_emission_consistency: dict[str, Any] = {}

        primary_batches_started = time.perf_counter()
        completed_batches = 0
        parallel_completed = False
        parallel_config = parallel_shard_config_from_settings(settings)
        public_batch_execution_plan = build_public_batch_execution_plan(
            generation_target_count=int(generation_target_count),
            batch_size=int(batch_size),
            max_workers=int(parallel_config.max_workers),
        )
        control_source_meta = dict(
            (prompt_context.get("feedback_control_state") or {}).get("source_meta")
            or {}
        )
        active_requirement_contract = dict(
            control_source_meta.get("requirement_semantic_contract") or {}
        )
        active_facts_by_id = {
            str(item.get("fact_id") or "").strip(): dict(item)
            for item in (active_requirement_contract.get("evidence_facts") or [])
            if isinstance(item, dict) and str(item.get("fact_id") or "").strip()
        }
        has_module_fact_ownership = any(
            list(module.get("fact_ids") or []) for module in verified_modules
        )
        # 语义图已经给出模块事实归属时，直接按事实所有权分片。
        coverage_shard_units = (
            []
            if has_module_fact_ownership
            else [
                dict(item)
                for item in coverage_plan_rules
                if isinstance(item, dict)
                and str(item.get("rule_text") or "").strip()
            ]
        )
        existing_unit_ids = {
            str(item.get("rule_id") or "").strip()
            for item in coverage_shard_units
        }
        for module_index, module in enumerate(verified_modules, start=1):
            module_name = str(module.get("module_name") or "").strip()
            if not module_name:
                continue
            module_id = str(module.get("module_key") or f"MODULE-{module_index:03d}").strip()
            shard_unit_id = f"MODULE::{module_id}"
            if shard_unit_id in existing_unit_ids:
                continue
            features = [
                str(item).strip()
                for item in (module.get("features") or [])
                if str(item).strip()
            ]
            module_fact_ids = [
                str(item).strip()
                for item in (module.get("fact_ids") or [])
                if str(item).strip() in active_facts_by_id
            ]
            coverage_shard_units.append(
                {
                    "rule_id": shard_unit_id,
                    "rule_text": (
                        f"功能模块 {module_name}"
                        + (f"：{' | '.join(features)}" if features else "")
                    ),
                    "facts": [
                        {
                            "fact_id": fact_id,
                            "statement": str(
                                active_facts_by_id[fact_id].get("statement") or ""
                            ).strip(),
                        }
                        for fact_id in module_fact_ids
                    ],
                }
            )
            existing_unit_ids.add(shard_unit_id)
        parallel_allowed, parallel_gate_reason = should_use_parallel_shards(
            expected_count=int(expected_count or 0),
            append=bool(append),
            multi_pass=bool(multi_pass),
            total_batches=int(total_batches),
            coverage_rule_count=int(len(coverage_shard_units)),
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
                    "coverage_rule_count": int(len(coverage_shard_units)),
                    "coverage_plan_rule_count": int(len(coverage_plan_rules)),
                    "functional_module_unit_count": int(
                        len(coverage_shard_units) - len(coverage_plan_rules)
                    ),
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
            model_call_target_size = max(
                1,
                (
                    int(batch_size)
                    + int(parallel_config.max_workers)
                    - 1
                )
                // int(parallel_config.max_workers),
            )
            required_stage_ids = [
                str(item).strip()
                for item in (
                    required_stage_coverage.get("missing_required_stage_ids")
                    or required_stage_coverage.get("required_stage_ids")
                    or []
                )
                if str(item).strip()
            ]
            main_chain_target = 0
            if required_stage_coverage_instruction and required_stage_ids:
                main_chain_target = min(
                    int(expected_count or 0),
                    max(
                        len(required_stage_ids),
                        min(6, max(1, (int(expected_count or 0) + 3) // 4)),
                    ),
                )
            independent_target = max(
                0,
                int(generation_target_count) - int(main_chain_target),
            )
            primary_workflow_blueprints = [
                item
                for item in requirement_workflow_blueprints
                if item.get("primary") is True
            ]
            if not primary_workflow_blueprints and requirement_workflow_blueprints:
                primary_workflow_blueprints = [requirement_workflow_blueprints[0]]
            main_chain_fact_ids = {
                str(fact_id or "").strip()
                for blueprint in primary_workflow_blueprints
                for fact_id in [
                    *(blueprint.get("fact_ids") or []),
                    *[
                        step_fact_id
                        for step in (blueprint.get("steps") or [])
                        if isinstance(step, dict)
                        for step_fact_id in (step.get("fact_ids") or [])
                    ],
                ]
                if str(fact_id or "").strip()
            }
            shard_plan = build_public_owned_shard_plan(
                coverage_shard_units,
                public_batch_plan=public_batch_execution_plan,
                max_workers=int(parallel_config.max_workers),
                main_chain_target=int(main_chain_target),
                reserved_fact_ids=(
                    main_chain_fact_ids if main_chain_target > 0 else set()
                ),
            )
            max_shards = int(len(shard_plan))
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
                        "model_call_target_size": int(model_call_target_size),
                        "max_shards": int(max_shards),
                        "main_chain_target": int(main_chain_target),
                        "main_chain_reserved_fact_count": int(
                            len(main_chain_fact_ids)
                        ),
                        "independent_target": int(independent_target),
                        "public_batch_count": int(len(public_batch_execution_plan)),
                        "public_batches": [
                            {
                                "batch_index": int(item.get("batch_index") or 0),
                                "target_count": int(item.get("target_count") or 0),
                                "shard_target_counts": [
                                    int(value)
                                    for value in (item.get("shard_target_counts") or [])
                                ],
                            }
                            for item in public_batch_execution_plan
                        ],
                        "shards": [
                            {
                                "shard_id": str(shard.get("shard_id") or ""),
                                "shard_kind": str(shard.get("shard_kind") or "independent"),
                                "public_batch_index": int(
                                    shard.get("public_batch_index") or 0
                                ),
                                "public_batch_target_count": int(
                                    shard.get("public_batch_target_count") or 0
                                ),
                                "public_batch_shard_index": int(
                                    shard.get("public_batch_shard_index") or 0
                                ),
                                "target_count": int(shard.get("target_count") or 0),
                                "rule_ids": list(shard.get("rule_ids") or [])[:20],
                                "fact_count": int(len(shard.get("facts") or [])),
                                "candidate_fact_count": int(
                                    shard.get("candidate_fact_count") or 0
                                ),
                                "shared_fact_excluded_count": int(
                                    shard.get("shared_fact_excluded_count") or 0
                                ),
                                "reserved_fact_excluded_count": int(
                                    shard.get("reserved_fact_excluded_count") or 0
                                ),
                                "fact_catalog_chars": int(
                                    len(
                                        json.dumps(
                                            list(shard.get("facts") or []),
                                            ensure_ascii=False,
                                            separators=(",", ":"),
                                        )
                                    )
                                ),
                                "fact_id_samples": [
                                    str(item.get("fact_id") or "")
                                    for item in list(shard.get("facts") or [])[:6]
                                    if isinstance(item, dict)
                                    and str(item.get("fact_id") or "").strip()
                                ],
                            }
                            for shard in shard_plan
                            if isinstance(shard, dict)
                        ],
                    }
                )
                if stream_batch_diags:
                    yield stream_batch_diags.pop()

                side_suite_order = execution_side_suite_order_text()
                shard_requests: list[dict[str, Any]] = []
                shard_prompt_projection_cache: dict[str, tuple[str, str, dict[str, Any]]] = {}
                client_factory = state.get("parallel_shard_client_factory")
                for shard in shard_plan:
                    shard_client = None
                    if callable(client_factory):
                        shard_client = client_factory(dict(shard))
                    elif self._is_active_db_session(db):
                        shard_client = get_client_for_user(user_id, db)
                    shard_instruction = build_parallel_shard_instruction(shard)
                    if (
                        required_stage_coverage_instruction
                        and (
                            str(shard.get("shard_kind") or "") == "main_chain"
                            or main_chain_target <= 0
                        )
                    ):
                        # 将通用分片输出契约保留在提示末端，降低长提示中的结构遗忘。
                        shard_instruction = (
                            f"{required_stage_coverage_instruction}\n\n{shard_instruction}"
                        )
                    shard_need = max(1, int(shard.get("target_count") or 1))
                    shard_kind = str(shard.get("shard_kind") or "independent").strip().lower()
                    generation_scope = "main_chain" if shard_kind == "main_chain" else "independent"
                    cached_projection = shard_prompt_projection_cache.get(generation_scope)
                    if cached_projection is None:
                        shard_control_context, shard_control_summary = (
                            build_generation_shard_control_context(
                                control_state=prompt_context.get("feedback_control_state") or {},
                                generation_scope=generation_scope,
                            )
                        )
                        shard_base_prompt = build_closed_loop_base_prompt(
                            strategy_plan,
                            requirement_context=prompt_context.get("requirement_context") or "",
                            requirement_semantics_context=prompt_context.get("requirement_semantics_context") or "",
                            testcase_context=prompt_context.get("testcase_context") or "(empty)",
                            supplement_context=prompt_context.get("supplement_context") or "(empty)",
                            control_context=shard_control_context,
                            current_biz_key=current_biz_key,
                            doc_type=doc_type,
                            pretty_json=True,
                        )
                        cached_projection = (
                            shard_base_prompt,
                            shard_control_context,
                            dict(shard_control_summary or {}),
                        )
                        shard_prompt_projection_cache[generation_scope] = cached_projection
                    shard_base_prompt, shard_control_context, shard_control_summary = cached_projection
                    shard_requests.append(
                        {
                            "request_id": str(request_id or ""),
                            "shard": dict(shard),
                            "client": shard_client,
                            "system_prompt": "",
                            "shard_base_prompt": shard_base_prompt,
                            "shard_control_context": shard_control_context,
                            "shard_control_summary": dict(
                                shard_control_summary or {}
                            ),
                            "shard_instruction": shard_instruction,
                            "request_timeout_seconds": stream_batch_request_timeout_seconds(),
                            "heartbeat_interval_seconds": stream_batch_heartbeat_interval_seconds(),
                        }
                    )

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

                def _accept_parallel_candidates(
                    cases: list[dict[str, Any]],
                    *,
                    limit: int,
                    start_id: int,
                ):
                    return accept_stream_batch_candidates(
                        cases,
                        limit=limit,
                        start_id=start_id,
                        project_profile=project_profile,
                        select_complete_generated_cases_fn=select_complete_generated_cases,
                        is_placeholder_expected_result_fn=is_placeholder_expected_result,
                        enforce_functional_module_contract_fn=enforce_functional_module_contract,
                    )

                shard_results: list[dict[str, Any]] = []
                accepted_public_batch_cases: list[dict[str, Any]] = []
                accepted_public_batch_diagnostics: dict[int, dict[str, Any]] = {}
                if not parallel_fallback_reason:
                    public_batch_request_groups = (
                        group_shard_requests_by_public_batch(
                            shard_requests,
                            public_batch_plan=public_batch_execution_plan,
                        )
                    )
                    for public_batch, batch_requests in public_batch_request_groups:
                        public_batch_index = int(
                            public_batch.get("batch_index") or 0
                        )
                        public_batch_target = int(
                            public_batch.get("target_count") or 0
                        )
                        batch_history_context = build_recent_history_context(
                            history_summaries
                        )
                        batch_shard_offset = 0
                        for request in batch_requests:
                            shard = dict(request.get("shard") or {})
                            shard_need = max(
                                1,
                                int(shard.get("target_count") or 1),
                            )
                            shard_system_prompt = build_stream_batch_system_prompt(
                                base_prompt=str(
                                    request.get("shard_base_prompt") or ""
                                ),
                                coverage_instruction="",
                                history_context=batch_history_context,
                                coverage_plan_lite=coverage_plan_lite,
                                side_suite_order=side_suite_order,
                                batch_index=max(0, public_batch_index - 1),
                                total_batches=int(
                                    len(public_batch_execution_plan)
                                ),
                                current_id=(
                                    int(start_id or 1)
                                    + int(public_batch.get("start_offset") or 0)
                                    + batch_shard_offset
                                ),
                                generated_in_batch=0,
                                need=shard_need,
                                shard_instruction=str(
                                    request.get("shard_instruction") or ""
                                ),
                                architecture_instruction=architecture_instruction,
                            )
                            request["system_prompt"] = shard_system_prompt
                            _emit_prompt_context_intake_diag(
                                prompt_context={
                                    **prompt_context,
                                    "control_context": str(
                                        request.get("shard_control_context")
                                        or ""
                                    ),
                                    "control_summary": dict(
                                        request.get("shard_control_summary")
                                        or {}
                                    )
                                    | {
                                        "assigned_active_fact_count": int(
                                            len(
                                                (
                                                    request.get("shard")
                                                    or {}
                                                ).get("facts")
                                                or []
                                            )
                                        )
                                    },
                                },
                                base_prompt_text=str(
                                    request.get("shard_base_prompt") or ""
                                ),
                                system_prompt_text=shard_system_prompt,
                                batch_index_value=public_batch_index,
                                total_batches_value=int(
                                    len(public_batch_execution_plan)
                                ),
                                attempt_value=1,
                                requested_count=shard_need,
                            )
                            if stream_batch_diags:
                                yield stream_batch_diags.pop()
                            system_prompt = shard_system_prompt
                            batch_shard_offset += shard_need
                        yield (
                            f"@@STATUS@@:正在生成第 {public_batch_index}/"
                            f"{len(public_batch_execution_plan)} 批次 "
                            f"({public_batch_target} 条，{len(batch_requests)} 个子分片)...\n"
                        )
                        batch_results: list[dict[str, Any]] = []
                        try:
                            shard_stream = stream_parallel_shard_requests(
                                requests=batch_requests,
                                requirement=requirement,
                                clean_and_parse_json_fn=clean_and_parse_json,
                                max_workers=int(parallel_config.max_workers),
                                heartbeat_interval_seconds=stream_batch_heartbeat_interval_seconds(),
                            )
                            while True:
                                try:
                                    shard_progress = next(shard_stream)
                                except StopIteration as stop:
                                    batch_results = list(stop.value or [])
                                    break
                                completed = int(
                                    shard_progress.get("completed_count") or 0
                                )
                                total = int(
                                    shard_progress.get("total_count")
                                    or len(batch_requests)
                                )
                                if (
                                    str(shard_progress.get("kind") or "")
                                    == "heartbeat"
                                ):
                                    yield (
                                        f"@@STATUS@@:第 {public_batch_index}/"
                                        f"{len(public_batch_execution_plan)} 批次子分片仍在生成 "
                                        f"({completed}/{total})，连接保持活跃...\n"
                                    )
                                else:
                                    yield (
                                        f"@@STATUS@@:第 {public_batch_index}/"
                                        f"{len(public_batch_execution_plan)} 批次子分片已完成 "
                                        f"{completed}/{total}...\n"
                                    )
                        except Exception as exc:
                            batch_results = [
                                {
                                    "shard": dict(request.get("shard") or {}),
                                    "status": "exception",
                                    "error": str(exc)[:300],
                                    "error_codes": ["parallel_executor_exception"],
                                    "cases": [],
                                    "duration_ms": 0,
                                    "metadata": {},
                                    "raw_response_chars": 0,
                                    "raw_parsed_case_count": 0,
                                    "normalized_case_count": 0,
                                    "semantic_rejection_count": 0,
                                }
                                for request in batch_requests
                            ]
                        batch_start_id = int(start_id or 1) + int(
                            public_batch.get("start_offset") or 0
                        )
                        batch_results = normalize_and_accept_parallel_shard_results(
                            batch_results,
                            normalize_json_structure_fn=_normalize_generated_json_structure,
                            accept_candidates_fn=_accept_parallel_candidates,
                            semantic_rejections=case_semantic_rejections,
                            start_id=batch_start_id,
                        )
                        for result in batch_results:
                            shard = (
                                result.get("shard")
                                if isinstance(result.get("shard"), dict)
                                else {}
                            )
                            repair_instructions: list[str] = []
                            semantic_rejections = [
                                dict(item)
                                for item in (result.get("semantic_rejections") or [])
                                if isinstance(item, dict)
                            ]
                            if semantic_rejections:
                                repair_instructions.append(
                                    build_case_semantic_retry_instruction(
                                        semantic_rejections
                                    )
                                )
                            if str(shard.get("shard_kind") or "") == "main_chain":
                                main_chain_coverage = evaluate_required_stage_candidate_coverage(
                                    [
                                        dict(case)
                                        for case in (result.get("cases") or [])
                                        if isinstance(case, dict)
                                    ],
                                    workflow_blueprints=requirement_workflow_blueprints,
                                )
                                if (
                                    main_chain_coverage.get("active") is True
                                    and main_chain_coverage.get(
                                        "source_generation_allowed"
                                    )
                                    is True
                                    and main_chain_coverage.get(
                                        "required_stage_coverage_complete"
                                    )
                                    is not True
                                ):
                                    actionable_stage_ids = list(
                                        main_chain_coverage.get(
                                            "actionable_stage_ids"
                                        )
                                        or main_chain_coverage.get(
                                            "missing_required_stage_ids"
                                        )
                                        or []
                                    )
                                    result["repair_target_count"] = max(
                                        1,
                                        len(actionable_stage_ids),
                                    )
                                    stage_instruction = (
                                        build_required_stage_coverage_instruction(
                                            main_chain_coverage
                                        )
                                    )
                                    if stage_instruction:
                                        repair_instructions.append(stage_instruction)
                            result["repair_instruction"] = "\n\n".join(
                                item for item in repair_instructions if item
                            )

                        batch_repair_requests = build_parallel_gap_repair_requests(
                            requests=batch_requests,
                            accepted_results=batch_results,
                            repair_attempt=1,
                        )
                        if batch_repair_requests:
                            yield (
                                f"@@STATUS@@:第 {public_batch_index}/"
                                f"{len(public_batch_execution_plan)} 批次验收后，"
                                f"对 {len(batch_repair_requests)} 个子分片局部补生成...\n"
                            )
                            try:
                                repair_stream = stream_parallel_shard_requests(
                                    requests=batch_repair_requests,
                                    requirement=requirement,
                                    clean_and_parse_json_fn=clean_and_parse_json,
                                    max_workers=int(parallel_config.max_workers),
                                    heartbeat_interval_seconds=(
                                        stream_batch_heartbeat_interval_seconds()
                                    ),
                                )
                                while True:
                                    try:
                                        repair_progress = next(repair_stream)
                                    except StopIteration as stop:
                                        batch_repair_results = list(stop.value or [])
                                        break
                                    if (
                                        str(repair_progress.get("kind") or "")
                                        == "heartbeat"
                                    ):
                                        repair_completed = int(
                                            repair_progress.get("completed_count")
                                            or 0
                                        )
                                        repair_total = int(
                                            repair_progress.get("total_count")
                                            or len(batch_repair_requests)
                                        )
                                        yield (
                                            "@@STATUS@@:局部补生成仍在执行 "
                                            f"({repair_completed}/{repair_total})，连接保持活跃；"
                                            f"当前第 {public_batch_index}/"
                                            f"{len(public_batch_execution_plan)} 批次...\n"
                                        )
                            except Exception as exc:
                                batch_repair_results = [
                                    {
                                        "shard": dict(request.get("shard") or {}),
                                        "status": "exception",
                                        "error": str(exc)[:300],
                                        "error_codes": [
                                            "parallel_repair_executor_exception"
                                        ],
                                        "cases": [],
                                    }
                                    for request in batch_repair_requests
                                ]
                            batch_repair_results = (
                                normalize_and_accept_parallel_shard_results(
                                    batch_repair_results,
                                    normalize_json_structure_fn=_normalize_generated_json_structure,
                                    accept_candidates_fn=_accept_parallel_candidates,
                                    semantic_rejections=case_semantic_rejections,
                                    start_id=batch_start_id,
                                )
                            )
                            batch_results = merge_parallel_shard_attempts(
                                batch_results,
                                batch_repair_results,
                            )

                        batch_merge_result = merge_public_batch_against_accepted_history(
                            batch_results,
                            accepted_history_cases=accepted_public_batch_cases,
                            build_case_signature_fn=_build_case_signature,
                            start_id=int(start_id or 1),
                            expected_batch_count=public_batch_target,
                        )
                        batch_acceptance = _accept_parallel_candidates(
                            [
                                case
                                for case in (batch_merge_result.get("cases") or [])
                                if isinstance(case, dict)
                            ],
                            limit=public_batch_target,
                            start_id=batch_start_id,
                        )
                        accepted_batch_cases = list(batch_acceptance.cases)
                        batch_repair_shard_ids = {
                            str(
                                (request.get("shard") or {}).get(
                                    "repair_of_shard_id"
                                )
                                or (request.get("shard") or {}).get("shard_id")
                                or ""
                            )
                            for request in batch_repair_requests
                            if str(
                                (request.get("shard") or {}).get(
                                    "repair_of_shard_id"
                                )
                                or (request.get("shard") or {}).get("shard_id")
                                or ""
                            )
                        }
                        merge_gap_repair_request_count = 0
                        merge_gap_repair_attempt_count = 0
                        merge_gap_repair_attempts: list[dict[str, Any]] = []
                        batch_gap_count = max(
                            0,
                            public_batch_target - len(accepted_batch_cases),
                        )
                        while (
                            batch_gap_count > 0
                            and merge_gap_repair_attempt_count < 2
                        ):
                            merge_gap_repair_attempt_count += 1
                            gap_before_repair = int(batch_gap_count)
                            duplicate_before_repair = int(
                                batch_merge_result.get("duplicate_count") or 0
                            )
                            containment_drop_before_repair = int(
                                batch_merge_result.get(
                                    "semantic_containment_dropped_count"
                                )
                                or 0
                            )
                            batch_results = (
                                assign_public_batch_merge_gap_repair_targets(
                                    batch_results,
                                    merge_result=batch_merge_result,
                                    gap_count=batch_gap_count,
                                    accepted_batch_cases=accepted_batch_cases,
                                    history_summaries=history_summaries,
                                    accepted_history_cases=(
                                        accepted_public_batch_cases
                                    ),
                                )
                            )
                            merge_gap_repair_requests = (
                                build_parallel_gap_repair_requests(
                                    requests=batch_requests,
                                    accepted_results=batch_results,
                                    repair_attempt=(
                                        1 + merge_gap_repair_attempt_count
                                    ),
                                )
                            )
                            if not merge_gap_repair_requests:
                                break
                            merge_gap_repair_request_count += len(
                                merge_gap_repair_requests
                            )
                            batch_repair_shard_ids.update(
                                str(
                                    (request.get("shard") or {}).get(
                                        "repair_of_shard_id"
                                    )
                                    or (request.get("shard") or {}).get(
                                        "shard_id"
                                    )
                                    or ""
                                )
                                for request in merge_gap_repair_requests
                                if str(
                                    (request.get("shard") or {}).get(
                                        "repair_of_shard_id"
                                    )
                                    or (request.get("shard") or {}).get(
                                        "shard_id"
                                    )
                                    or ""
                                )
                            )
                            yield (
                                f"@@STATUS@@:第 {public_batch_index}/"
                                f"{len(public_batch_execution_plan)} 批次合并去重后"
                                f"缺口 {batch_gap_count} 条，仅在本批局部补生成...\n"
                            )
                            try:
                                merge_gap_stream = stream_parallel_shard_requests(
                                    requests=merge_gap_repair_requests,
                                    requirement=requirement,
                                    clean_and_parse_json_fn=clean_and_parse_json,
                                    max_workers=int(parallel_config.max_workers),
                                    heartbeat_interval_seconds=(
                                        stream_batch_heartbeat_interval_seconds()
                                    ),
                                )
                                while True:
                                    try:
                                        merge_gap_progress = next(merge_gap_stream)
                                    except StopIteration as stop:
                                        merge_gap_repair_results = list(
                                            stop.value or []
                                        )
                                        break
                                    if (
                                        str(merge_gap_progress.get("kind") or "")
                                        == "heartbeat"
                                    ):
                                        completed = int(
                                            merge_gap_progress.get(
                                                "completed_count"
                                            )
                                            or 0
                                        )
                                        total = int(
                                            merge_gap_progress.get("total_count")
                                            or len(merge_gap_repair_requests)
                                        )
                                        yield (
                                            "@@STATUS@@:批内合并缺口补生成仍在执行 "
                                            f"({completed}/{total})，连接保持活跃...\n"
                                        )
                            except Exception as exc:
                                merge_gap_repair_results = [
                                    {
                                        "shard": dict(request.get("shard") or {}),
                                        "status": "exception",
                                        "error": str(exc)[:300],
                                        "error_codes": [
                                            "public_batch_merge_gap_repair_exception"
                                        ],
                                        "cases": [],
                                    }
                                    for request in merge_gap_repair_requests
                                ]
                            merge_gap_repair_results = (
                                normalize_and_accept_parallel_shard_results(
                                    merge_gap_repair_results,
                                    normalize_json_structure_fn=_normalize_generated_json_structure,
                                    accept_candidates_fn=_accept_parallel_candidates,
                                    semantic_rejections=case_semantic_rejections,
                                    start_id=batch_start_id,
                                )
                            )
                            batch_results = merge_parallel_shard_attempts(
                                batch_results,
                                merge_gap_repair_results,
                            )
                            batch_merge_result = merge_public_batch_against_accepted_history(
                                batch_results,
                                accepted_history_cases=accepted_public_batch_cases,
                                build_case_signature_fn=_build_case_signature,
                                start_id=int(start_id or 1),
                                expected_batch_count=public_batch_target,
                            )
                            batch_acceptance = _accept_parallel_candidates(
                                [
                                    case
                                    for case in (
                                        batch_merge_result.get("cases") or []
                                    )
                                    if isinstance(case, dict)
                                ],
                                limit=public_batch_target,
                                start_id=batch_start_id,
                            )
                            accepted_batch_cases = list(batch_acceptance.cases)
                            batch_gap_count = max(
                                0,
                                public_batch_target
                                - len(accepted_batch_cases),
                            )
                            merge_gap_repair_attempts.append(
                                {
                                    "attempt": int(
                                        merge_gap_repair_attempt_count
                                    ),
                                    "gap_before": int(gap_before_repair),
                                    "gap_after": int(batch_gap_count),
                                    "request_count": int(
                                        len(merge_gap_repair_requests)
                                    ),
                                    "raw_parsed_case_count": int(
                                        sum(
                                            int(
                                                result.get(
                                                    "raw_parsed_case_count"
                                                )
                                                or 0
                                            )
                                            for result in merge_gap_repair_results
                                            if isinstance(result, dict)
                                        )
                                    ),
                                    "normalized_case_count": int(
                                        sum(
                                            int(
                                                result.get(
                                                    "normalized_case_count"
                                                )
                                                or 0
                                            )
                                            for result in merge_gap_repair_results
                                            if isinstance(result, dict)
                                        )
                                    ),
                                    "semantic_rejection_count": int(
                                        sum(
                                            int(
                                                result.get(
                                                    "semantic_rejection_count"
                                                )
                                                or 0
                                            )
                                            for result in merge_gap_repair_results
                                            if isinstance(result, dict)
                                        )
                                    ),
                                    "duplicate_dropped_count": max(
                                        0,
                                        int(
                                            batch_merge_result.get(
                                                "duplicate_count"
                                            )
                                            or 0
                                        )
                                        - duplicate_before_repair,
                                    ),
                                    "containment_dropped_count": max(
                                        0,
                                        int(
                                            batch_merge_result.get(
                                                "semantic_containment_dropped_count"
                                            )
                                            or 0
                                        )
                                        - containment_drop_before_repair,
                                    ),
                                    "relation_samples": [
                                        {
                                            "relation": str(
                                                sample.get("relation") or ""
                                            ),
                                            "action": str(
                                                sample.get("action") or ""
                                            ),
                                            "reasons": list(
                                                sample.get("reasons") or []
                                            )[:4],
                                            "dropped_shard_id": str(
                                                sample.get("dropped_shard_id")
                                                or ""
                                            ),
                                            "dropped_fact_ids": list(
                                                sample.get("dropped_fact_ids")
                                                or []
                                            )[:8],
                                            "retained_fact_ids": list(
                                                sample.get("retained_fact_ids")
                                                or []
                                            )[:8],
                                        }
                                        for sample in list(
                                            batch_merge_result.get(
                                                "semantic_relation_samples"
                                            )
                                            or []
                                        )[-8:]
                                        if isinstance(sample, dict)
                                    ],
                                }
                            )
                        batch_status = (
                            "accepted" if batch_gap_count == 0 else "failed"
                        )
                        if batch_gap_count == 0:
                            accepted_public_batch_cases.extend(
                                dict(case) for case in accepted_batch_cases
                            )
                            accepted_public_batch_diagnostics[
                                int(public_batch_index)
                            ] = {
                                "input_case_count": int(
                                    batch_merge_result.get("input_case_count")
                                    or 0
                                ),
                                "duplicate_count": int(
                                    batch_merge_result.get("duplicate_count")
                                    or 0
                                ),
                                "semantic_duplicate_count": int(
                                    batch_merge_result.get(
                                        "semantic_duplicate_count"
                                    )
                                    or 0
                                ),
                                "containment_count": int(
                                    batch_merge_result.get("containment_count")
                                    or 0
                                ),
                                "semantic_containment_dropped_count": int(
                                    batch_merge_result.get(
                                        "semantic_containment_dropped_count"
                                    )
                                    or 0
                                ),
                            }
                        for case in accepted_batch_cases:
                            history_summaries.append(
                                f"{case.get('id', '')}: "
                                f"{case.get('description', '')}"
                            )
                        for result in batch_results:
                            result["_public_batch_prepared"] = True
                        _emit_stream_gen_diag(
                            {
                                "kind": "parallel_public_batch_result",
                                "project_id": int(project_id),
                                "request_id": str(request_id or ""),
                                "batch_index": int(public_batch_index),
                                "total_batches": int(
                                    len(public_batch_execution_plan)
                                ),
                                "batch_target_count": int(public_batch_target),
                                "subshard_target_counts": [
                                    int(
                                        (request.get("shard") or {}).get(
                                            "target_count"
                                        )
                                        or 0
                                    )
                                    for request in batch_requests
                                ],
                                "accepted_case_count": int(
                                    len(accepted_batch_cases)
                                ),
                                "accepted_history_case_count": int(
                                    batch_merge_result.get(
                                        "accepted_history_case_count"
                                    )
                                    or 0
                                ),
                                "cross_batch_semantic_drop_count": int(
                                    batch_merge_result.get(
                                        "cross_batch_semantic_drop_count"
                                    )
                                    or 0
                                ),
                                "gap_count": int(batch_gap_count),
                                "status": str(batch_status),
                                "failure_reason": (
                                    "public_batch_merge_gap_unresolved"
                                    if batch_gap_count > 0
                                    else ""
                                ),
                                "repair_shard_count": int(
                                    len(batch_repair_shard_ids)
                                ),
                                "repair_request_count": int(
                                    len(batch_repair_requests)
                                    + merge_gap_repair_request_count
                                ),
                                "merge_gap_repair_attempt_count": int(
                                    merge_gap_repair_attempt_count
                                ),
                                "merge_gap_repair_request_count": int(
                                    merge_gap_repair_request_count
                                ),
                                "merge_gap_repair_attempts": list(
                                    merge_gap_repair_attempts
                                ),
                            }
                        )
                        if stream_batch_diags:
                            yield stream_batch_diags.pop()
                        if batch_gap_count > 0:
                            parallel_public_batch_failure = {
                                "abort_code": "PUBLIC_BATCH_UNDERFILLED_ABORT",
                                "failure_reason": "public_batch_merge_gap_unresolved",
                                "batch_index": int(public_batch_index),
                                "total_batches": int(
                                    len(public_batch_execution_plan)
                                ),
                                "batch_target_count": int(public_batch_target),
                                "accepted_case_count": int(
                                    len(accepted_batch_cases)
                                ),
                                "gap_count": int(batch_gap_count),
                                "merge_gap_repair_attempt_count": int(
                                    merge_gap_repair_attempt_count
                                ),
                                "merge_gap_repair_attempts": list(
                                    merge_gap_repair_attempts
                                ),
                            }
                            yield (
                                f"@@STATUS@@:第 {public_batch_index}/"
                                f"{len(public_batch_execution_plan)} 批次局部补生成后"
                                f"仍缺 {batch_gap_count} 条，本批已明确标记失败。\n"
                            )
                        yield (
                            f"@@STATUS@@:第 {public_batch_index}/"
                            f"{len(public_batch_execution_plan)} 批次验收 "
                            f"{len(accepted_batch_cases)}/{public_batch_target} 条。\n"
                        )
                        shard_results.extend(batch_results)
                        if parallel_public_batch_failure:
                            break

                    if parallel_public_batch_failure:
                        parallel_fallback_reason = str(
                            parallel_public_batch_failure.get(
                                "failure_reason"
                            )
                            or "public_batch_merge_gap_unresolved"
                        )

                merge_result: dict[str, Any] = {}
                parallel_incomplete_rows: list[dict[str, Any]] = []
                parallel_module_contract_summary: dict[str, Any] = {}
                if not parallel_fallback_reason:
                    # 公共批次已在进入下一批之前完成归一化和局部补生成。
                    if not all(
                        bool(result.get("_public_batch_prepared"))
                        for result in shard_results
                    ):
                        shard_results = normalize_and_accept_parallel_shard_results(
                            shard_results,
                            normalize_json_structure_fn=_normalize_generated_json_structure,
                            accept_candidates_fn=_accept_parallel_candidates,
                            semantic_rejections=case_semantic_rejections,
                            start_id=int(start_id or 1),
                        )
                    for result in shard_results:
                        shard = result.get("shard") if isinstance(result.get("shard"), dict) else {}
                        _record_timing_event(
                            "parallel_shard_attempt",
                            parallel_started,
                            shard_id=str(shard.get("shard_id") or ""),
                            shard_index=int(shard.get("shard_index") or 0),
                            requested_count=int(shard.get("target_count") or 0),
                            response_case_count=int(result.get("raw_parsed_case_count") or 0),
                            normalized_case_count=int(result.get("normalized_case_count") or 0),
                            semantic_rejection_count=int(result.get("semantic_rejection_count") or 0),
                            accepted_case_count=int(result.get("accepted_case_count") or 0),
                            duration_ms=int(result.get("duration_ms") or 0),
                            attempt_status=str(result.get("status") or ""),
                            error_codes=list(result.get("error_codes") or []),
                            provider_error=str(result.get("error") or "")[:200],
                        )

                    # 补生成前把字段级拒绝原因和主链契约缺口回灌到对应分片。
                    for result in shard_results:
                        shard = result.get("shard") if isinstance(result.get("shard"), dict) else {}
                        repair_instructions: list[str] = []
                        semantic_rejections = [
                            dict(item)
                            for item in (result.get("semantic_rejections") or [])
                            if isinstance(item, dict)
                        ]
                        if semantic_rejections:
                            repair_instructions.append(
                                build_case_semantic_retry_instruction(
                                    semantic_rejections
                                )
                            )
                        if str(shard.get("shard_kind") or "") == "main_chain":
                            main_chain_coverage = evaluate_required_stage_candidate_coverage(
                                [
                                    dict(case)
                                    for case in (result.get("cases") or [])
                                    if isinstance(case, dict)
                                ],
                                workflow_blueprints=requirement_workflow_blueprints,
                            )
                            result["main_chain_coverage_before_repair"] = dict(
                                main_chain_coverage or {}
                            )
                            if (
                                main_chain_coverage.get("active") is True
                                and main_chain_coverage.get(
                                    "source_generation_allowed"
                                )
                                is True
                                and main_chain_coverage.get(
                                    "required_stage_coverage_complete"
                                )
                                is not True
                            ):
                                actionable_stage_ids = list(
                                    main_chain_coverage.get("actionable_stage_ids")
                                    or main_chain_coverage.get(
                                        "missing_required_stage_ids"
                                    )
                                    or []
                                )
                                result["repair_target_count"] = max(
                                    1,
                                    len(actionable_stage_ids),
                                )
                                result["error_codes"] = list(
                                    dict.fromkeys(
                                        [
                                            *(result.get("error_codes") or []),
                                            "main_chain_contract_incomplete",
                                        ]
                                    )
                                )
                                stage_instruction = (
                                    build_required_stage_coverage_instruction(
                                        main_chain_coverage
                                    )
                                )
                                if stage_instruction:
                                    repair_instructions.append(stage_instruction)
                        result["repair_instruction"] = "\n\n".join(
                            item for item in repair_instructions if item
                        )

                    repair_requests = (
                        []
                        if all(
                            bool(result.get("_public_batch_prepared"))
                            for result in shard_results
                        )
                        else build_parallel_gap_repair_requests(
                            requests=shard_requests,
                            accepted_results=shard_results,
                            repair_attempt=1,
                        )
                    )
                    if repair_requests:
                        yield (
                            f"@@STATUS@@:仅对 {len(repair_requests)} 个失败或不足分片"
                            "进行局部补生成...\n"
                        )
                        try:
                            repair_stream = stream_parallel_shard_requests(
                                requests=repair_requests,
                                requirement=requirement,
                                clean_and_parse_json_fn=clean_and_parse_json,
                                max_workers=int(parallel_config.max_workers),
                                heartbeat_interval_seconds=(
                                    stream_batch_heartbeat_interval_seconds()
                                ),
                            )
                            while True:
                                try:
                                    repair_progress = next(repair_stream)
                                except StopIteration as stop:
                                    repair_results = list(stop.value or [])
                                    break
                                completed = int(
                                    repair_progress.get("completed_count") or 0
                                )
                                total = int(
                                    repair_progress.get("total_count")
                                    or len(repair_requests)
                                )
                                if (
                                    str(repair_progress.get("kind") or "")
                                    == "heartbeat"
                                ):
                                    yield (
                                        "@@STATUS@@:局部补生成仍在执行 "
                                        f"({completed}/{total})，连接保持活跃...\n"
                                    )
                                else:
                                    yield (
                                        "@@STATUS@@:局部补生成已完成 "
                                        f"{completed}/{total}...\n"
                                    )
                        except Exception as exc:
                            repair_results = [
                                {
                                    "shard": dict(request.get("shard") or {}),
                                    "status": "exception",
                                    "error": str(exc)[:300],
                                    "error_codes": ["parallel_repair_executor_exception"],
                                    "cases": [],
                                }
                                for request in repair_requests
                            ]
                        repair_results = normalize_and_accept_parallel_shard_results(
                            repair_results,
                            normalize_json_structure_fn=_normalize_generated_json_structure,
                            accept_candidates_fn=_accept_parallel_candidates,
                            semantic_rejections=case_semantic_rejections,
                            start_id=int(start_id or 1),
                        )
                        shard_results = merge_parallel_shard_attempts(
                            shard_results,
                            repair_results,
                        )

                    # 局部补生成后只更新对应分片缺口，不丢弃其他已验收分片。
                    main_chain_contract_complete = True
                    for result in shard_results:
                        shard = result.get("shard") if isinstance(result.get("shard"), dict) else {}
                        target_count = max(0, int(shard.get("target_count") or 0))
                        accepted_count = int(len(result.get("cases") or []))
                        gap_count = max(0, target_count - accepted_count)
                        result["accepted_case_count"] = accepted_count
                        result["gap_count"] = gap_count
                        result["status"] = "accepted" if gap_count == 0 else (
                            "underfilled" if accepted_count else "failed"
                        )
                        if str(shard.get("shard_kind") or "") == "main_chain":
                            main_chain_coverage = evaluate_required_stage_candidate_coverage(
                                [
                                    dict(case)
                                    for case in (result.get("cases") or [])
                                    if isinstance(case, dict)
                                ],
                                workflow_blueprints=requirement_workflow_blueprints,
                            )
                            result["main_chain_coverage_after_repair"] = dict(
                                main_chain_coverage or {}
                            )
                            main_chain_contract_complete = bool(
                                main_chain_coverage.get(
                                    "required_stage_coverage_complete"
                                )
                                is True
                            )
                            if not main_chain_contract_complete:
                                result["status"] = "contract_incomplete"

                    public_batch_selection_frozen = bool(
                        accepted_public_batch_cases
                    ) and all(
                        bool(result.get("_public_batch_prepared"))
                        for result in shard_results
                    )
                    merge_result = merge_parallel_shard_cases(
                        (
                            [
                                {
                                    "shard": {
                                        "shard_id": "PUBLIC-BATCH-ACCEPTED",
                                        "merge_order": 1,
                                    },
                                    "cases": list(
                                        accepted_public_batch_cases
                                    ),
                                }
                            ]
                            if public_batch_selection_frozen
                            else shard_results
                        ),
                        build_case_signature_fn=_build_case_signature,
                        start_id=int(start_id or 1),
                        expected_count=int(expected_count or 0),
                    )
                    if public_batch_selection_frozen:
                        merge_result["selection_source"] = (
                            "accepted_public_batches"
                        )
                        merge_result["raw_shard_candidate_count"] = int(
                            sum(
                                len(result.get("cases") or [])
                                for result in shard_results
                                if isinstance(result, dict)
                            )
                        )
                    semantic_duplicate_gap = max(
                        0,
                        int(generation_target_count)
                        - int(merge_result.get("unique_case_count") or 0),
                    )
                    if (
                        not public_batch_selection_frozen
                        and semantic_duplicate_gap > 0
                        and (
                            int(merge_result.get("semantic_duplicate_count") or 0) > 0
                            or int(
                                merge_result.get(
                                    "semantic_containment_dropped_count"
                                )
                                or 0
                            )
                            > 0
                        )
                    ):
                        shard_results = assign_cross_shard_duplicate_repair_targets(
                            shard_results,
                            merge_result,
                            gap_count=semantic_duplicate_gap,
                        )
                        semantic_repair_requests = build_parallel_gap_repair_requests(
                            requests=shard_requests,
                            accepted_results=shard_results,
                            repair_attempt=2,
                        )
                        if semantic_repair_requests:
                            yield (
                                "@@STATUS@@:检测到跨分片语义重复或包含，仅对被删除用例所属分片补生成"
                                f" {semantic_duplicate_gap} 条替代用例...\n"
                            )
                            try:
                                semantic_repair_stream = stream_parallel_shard_requests(
                                    requests=semantic_repair_requests,
                                    requirement=requirement,
                                    clean_and_parse_json_fn=clean_and_parse_json,
                                    max_workers=int(parallel_config.max_workers),
                                    heartbeat_interval_seconds=(
                                        stream_batch_heartbeat_interval_seconds()
                                    ),
                                )
                                while True:
                                    try:
                                        semantic_repair_progress = next(
                                            semantic_repair_stream
                                        )
                                    except StopIteration as stop:
                                        semantic_repair_results = list(
                                            stop.value or []
                                        )
                                        break
                                    completed = int(
                                        semantic_repair_progress.get(
                                            "completed_count"
                                        )
                                        or 0
                                    )
                                    total = int(
                                        semantic_repair_progress.get("total_count")
                                        or len(semantic_repair_requests)
                                    )
                                    if (
                                        str(
                                            semantic_repair_progress.get("kind")
                                            or ""
                                        )
                                        == "heartbeat"
                                    ):
                                        yield (
                                            "@@STATUS@@:跨分片重复补生成仍在执行 "
                                            f"({completed}/{total})，连接保持活跃...\n"
                                        )
                                    else:
                                        yield (
                                            "@@STATUS@@:跨分片重复补生成已完成 "
                                            f"{completed}/{total}...\n"
                                        )
                            except Exception as exc:
                                semantic_repair_results = [
                                    {
                                        "shard": dict(request.get("shard") or {}),
                                        "status": "exception",
                                        "error": str(exc)[:300],
                                        "error_codes": [
                                            "semantic_duplicate_repair_executor_exception"
                                        ],
                                        "cases": [],
                                    }
                                    for request in semantic_repair_requests
                                ]
                            semantic_repair_results = normalize_and_accept_parallel_shard_results(
                                semantic_repair_results,
                                normalize_json_structure_fn=_normalize_generated_json_structure,
                                accept_candidates_fn=_accept_parallel_candidates,
                                semantic_rejections=case_semantic_rejections,
                                start_id=int(start_id or 1),
                            )
                            shard_results = merge_parallel_shard_attempts(
                                shard_results,
                                semantic_repair_results,
                            )
                            merge_result = merge_parallel_shard_cases(
                                shard_results,
                                build_case_signature_fn=_build_case_signature,
                                start_id=int(start_id or 1),
                                expected_count=int(expected_count or 0),
                            )
                    parallel_acceptance = _accept_parallel_candidates(
                        [case for case in (merge_result.get("cases") or []) if isinstance(case, dict)],
                        limit=int(expected_count or 0),
                        start_id=int(start_id or 1),
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
                    accepted_parallel_count = int(len(merge_result.get("cases") or []))
                    parallel_gap_count = max(
                        0,
                        int(generation_target_count) - accepted_parallel_count,
                    )
                    parallel_outcome_status = (
                        "accepted"
                        if parallel_gap_count == 0 and main_chain_contract_complete
                        else ("partial" if accepted_parallel_count else "failed")
                    )
                else:
                    parallel_gap_count = int(generation_target_count)
                    parallel_outcome_status = "failed"

                parallel_result_summary = {
                    "kind": "parallel_coverage_shard_result",
                    "project_id": int(project_id),
                    "request_id": str(request_id or ""),
                    "status": str(parallel_outcome_status),
                    "fallback_reason": str(parallel_fallback_reason or ""),
                    "shard_count": int(len(shard_plan)),
                    "public_batch_count": int(len(public_batch_execution_plan)),
                    "public_batch_targets": [
                        int(item.get("target_count") or 0)
                        for item in public_batch_execution_plan
                    ],
                    "selection_source": str(
                        merge_result.get("selection_source") or "raw_shard_results"
                    ),
                    "raw_shard_candidate_count": int(
                        merge_result.get("raw_shard_candidate_count")
                        or merge_result.get("input_case_count")
                        or 0
                    ),
                    "input_case_count": int(merge_result.get("input_case_count") or 0),
                    "unique_case_count": int(merge_result.get("unique_case_count") or 0),
                    "accepted_case_count": int(len(merge_result.get("cases") or [])),
                    "gap_count": int(parallel_gap_count),
                    "main_chain_contract_complete": bool(
                        main_chain_contract_complete
                        if not parallel_fallback_reason
                        else False
                    ),
                    "repair_shard_count": int(
                        sum(
                            1
                            for result in shard_results
                            if int(result.get("repair_attempt_count") or 0) > 0
                        )
                    ),
                    "duplicate_count": int(merge_result.get("duplicate_count") or 0),
                    "exact_duplicate_count": int(
                        merge_result.get("exact_duplicate_count") or 0
                    ),
                    "semantic_duplicate_count": int(
                        merge_result.get("semantic_duplicate_count") or 0
                    ),
                    "containment_count": int(
                        merge_result.get("containment_count") or 0
                    ),
                    "semantic_containment_dropped_count": int(
                        merge_result.get("semantic_containment_dropped_count")
                        or 0
                    ),
                    "semantic_relation_samples": list(
                        merge_result.get("semantic_relation_samples") or []
                    )[:20],
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
                            "public_batch_index": int(
                                (result.get("shard") or {}).get(
                                    "public_batch_index"
                                )
                                or 0
                            ),
                            "public_batch_target_count": int(
                                (result.get("shard") or {}).get(
                                    "public_batch_target_count"
                                )
                                or 0
                            ),
                            "target_count": int(
                                (result.get("shard") or {}).get("target_count")
                                or 0
                            ),
                            "status": str(result.get("status") or ""),
                            "duration_ms": int(result.get("duration_ms") or 0),
                            "response_case_count": int(result.get("response_case_count") or 0),
                            "raw_response_chars": int(result.get("raw_response_chars") or 0),
                            "raw_parsed_case_count": int(result.get("raw_parsed_case_count") or 0),
                            "normalized_case_count": int(result.get("normalized_case_count") or 0),
                            "semantic_rejection_count": int(result.get("semantic_rejection_count") or 0),
                            "semantic_rejection_codes": list(result.get("semantic_rejection_codes") or []),
                            "accepted_case_count": int(result.get("accepted_case_count") or 0),
                            "gap_count": int(result.get("gap_count") or 0),
                            "repair_attempt_count": int(result.get("repair_attempt_count") or 0),
                            "error_codes": list(result.get("error_codes") or []),
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
                    accepted=parallel_outcome_status == "accepted",
                    outcome_status=str(parallel_outcome_status),
                    fallback_reason=str(parallel_fallback_reason or ""),
                    input_case_count=int(merge_result.get("input_case_count") or 0),
                    unique_case_count=int(merge_result.get("unique_case_count") or 0),
                    accepted_case_count=int(len(merge_result.get("cases") or [])),
                    duplicate_rate=float(merge_result.get("duplicate_rate") or 0.0),
                )

                if not parallel_fallback_reason:
                    emitted_public_batches, public_batch_emission_consistency = (
                        _build_frozen_public_batch_emission(
                            accepted_public_batch_cases=(
                                accepted_public_batch_cases
                            ),
                            public_batch_execution_plan=(
                                public_batch_execution_plan
                            ),
                            expected_count=int(generation_target_count),
                            workflow_blueprints=(
                                requirement_workflow_blueprints
                            ),
                        )
                    )
                    public_batch_emission_consistency.update(
                        {
                            "project_id": int(project_id),
                            "request_id": str(request_id or ""),
                        }
                    )
                    _emit_stream_gen_diag(public_batch_emission_consistency)
                    if stream_batch_diags:
                        yield stream_batch_diags.pop()
                    if public_batch_emission_consistency.get("passed") is not True:
                        parallel_public_batch_failure = {
                            "abort_code": "PUBLIC_BATCH_EMISSION_MISMATCH_ABORT",
                            "failure_reason": "public_batch_emission_mismatch",
                            "batch_index": 0,
                            "total_batches": int(
                                len(public_batch_execution_plan)
                            ),
                            "expected_count": int(generation_target_count),
                            "accepted_case_count": int(
                                len(accepted_public_batch_cases)
                            ),
                            "gap_count": max(
                                0,
                                int(generation_target_count)
                                - int(len(accepted_public_batch_cases)),
                            ),
                            "emission_consistency": dict(
                                public_batch_emission_consistency
                            ),
                        }
                        parallel_fallback_reason = (
                            "public_batch_emission_mismatch"
                        )

                    parsed_batch_cases = [
                        dict(case)
                        for _batch, batch_cases in emitted_public_batches
                        for case in batch_cases
                    ]
                    if (
                        public_batch_emission_consistency.get("passed") is True
                        and parsed_batch_cases
                    ):
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
                        for public_batch, owned_cases in emitted_public_batches:
                            public_batch_index = int(
                                public_batch.get("batch_index") or 0
                            )
                            public_batch_target = int(
                                public_batch.get("target_count") or 0
                            )
                            owned_shard_results = [
                                result
                                for result in shard_results
                                if int(
                                    (result.get("shard") or {}).get(
                                        "public_batch_index"
                                    )
                                    or 0
                                )
                                == public_batch_index
                            ]
                            owned_batch_diagnostics = dict(
                                accepted_public_batch_diagnostics.get(
                                    public_batch_index
                                )
                                or {}
                            )
                            generated_case_count += int(len(owned_cases))
                            full_content += json.dumps(
                                owned_cases,
                                ensure_ascii=False,
                                indent=2,
                            )
                            full_content += "\n"
                            yield _public_case_batch_json(owned_cases)
                            yield "\n"
                            batch_metric, low_gain_streak = (
                                build_stream_batch_quality_metric(
                                    parsed_batch_cases=owned_cases,
                                    seen_case_signatures=seen_case_signatures,
                                    batch_index=public_batch_index,
                                    build_case_signature_fn=_build_case_signature,
                                    is_non_assertable_expected_result_fn=_is_non_assertable_expected_result,
                                    previous_low_gain_streak=low_gain_streak,
                                )
                            )
                            owned_shards = [
                                dict(result.get("shard") or {})
                                for result in owned_shard_results
                            ]
                            batch_metric.update(
                                {
                                    "batch_target_count": int(
                                        public_batch_target
                                    ),
                                    "batch_gap_count": max(
                                        0,
                                        public_batch_target - len(owned_cases),
                                    ),
                                    "parallel_shards_used": True,
                                    "parallel_shard_count": int(
                                        len(owned_shards)
                                    ),
                                    "parallel_shard_target_counts": [
                                        int(shard.get("target_count") or 0)
                                        for shard in owned_shards
                                    ],
                                    "parallel_input_case_count": int(
                                        owned_batch_diagnostics.get(
                                            "input_case_count"
                                        )
                                        or len(owned_cases)
                                    ),
                                    "parallel_duplicate_count": int(
                                        owned_batch_diagnostics.get(
                                            "duplicate_count"
                                        )
                                        or 0
                                    ),
                                    "parallel_semantic_duplicate_count": int(
                                        owned_batch_diagnostics.get(
                                            "semantic_duplicate_count"
                                        )
                                        or 0
                                    ),
                                    "parallel_containment_count": int(
                                        owned_batch_diagnostics.get(
                                            "containment_count"
                                        )
                                        or 0
                                    ),
                                    "parallel_semantic_containment_dropped_count": int(
                                        owned_batch_diagnostics.get(
                                            "semantic_containment_dropped_count"
                                        )
                                        or 0
                                    ),
                                    "parallel_selection_source": (
                                        "accepted_public_batch_freeze"
                                    ),
                                }
                            )
                            batch_quality_metrics.append(batch_metric)
                            _emit_stream_batch_quality_diag(
                                batch_quality_metrics[-1]
                            )
                            if stream_batch_diags:
                                yield stream_batch_diags.pop()
                    else:
                        full_content += "[]\n"
                    completed_batches = int(len(public_batch_execution_plan))
                    current_id = int(start_id or 1) + (
                        int(len(parsed_batch_cases))
                        if public_batch_emission_consistency.get("passed") is True
                        else 0
                    )
                    parallel_completed = True
                else:
                    # 并发链路启动后即使整个执行器异常，也不再回退重生整批。
                    full_content += "[]\n"
                    completed_batches = int(
                        parallel_public_batch_failure.get("batch_index")
                        or len(public_batch_execution_plan)
                    )
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

                # 历史摘要只通过 history_context 传递一次，避免在 base prompt 中重复注入。
                testcase_context = prompt_context.get("testcase_context") or "(empty)"

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

                request_timeout_seconds = stream_batch_request_timeout_seconds()
                heartbeat_interval_seconds = (
                    stream_batch_heartbeat_interval_seconds()
                )
                stream = client.generate_response_stream(
                    requirement,
                    system_prompt,
                    task_type="generation",
                    request_timeout_seconds=request_timeout_seconds,
                    heartbeat_interval_seconds=heartbeat_interval_seconds,
                    reasoning_effort="low",
                    disable_thinking=True,
                )
                chunk_acc = ""
                attempt_content = ""
                provider_error = None
                for chunk in stream:
                    is_heartbeat = getattr(client, "is_stream_heartbeat", None)
                    if callable(is_heartbeat) and is_heartbeat(chunk):
                        elapsed_seconds = max(
                            0,
                            int(time.perf_counter() - attempt_started),
                        )
                        yield (
                            f"@@STATUS@@:第 {batch_index + 1}/{total_batches} 批次"
                            f"模型仍在生成（已等待 {elapsed_seconds} 秒）...\n"
                        )
                        continue
                    chunk_acc += chunk
                    attempt_content += chunk
                    if chunk.startswith("Error:") or chunk.startswith("[额度耗尽]") or chunk.startswith("Exception occurred:"):
                        provider_error = chunk
                        # 继续耗尽 provider generator，确保 finally 中的首 Token、推理长度和总耗时回写到 client metadata。
                        continue

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

            batch_underfill_count = max(
                0,
                int(current_batch_count) - int(len(parsed_batch_cases)),
            )
            if batch_underfill_count:
                underfill_summary = {
                    "batch_index": int(batch_index + 1),
                    "requested_count": int(current_batch_count),
                    "accepted_case_count": int(len(parsed_batch_cases)),
                    "underfill_count": int(batch_underfill_count),
                    "attempt_count": int(attempt),
                    "source_incomplete_case_count": int(incomplete_case_count),
                    "underfill_detected": True,
                }
                batch_underfill_summaries.append(underfill_summary)
                _emit_stream_batch_quality_diag(underfill_summary)
                if stream_batch_diags:
                    yield stream_batch_diags.pop()
                yield (
                    f"@@STATUS@@:第 {batch_index + 1}/{total_batches} 批次验收"
                    f" {len(parsed_batch_cases)}/{current_batch_count} 条，"
                    f"明确记录缺口 {batch_underfill_count} 条。\n"
                )

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
                "stream_batch_underfill_summaries": list(
                    batch_underfill_summaries
                ),
                "stream_early_stop_triggered": bool(early_stop_triggered),
                "stream_early_stop_reason": str(early_stop_reason or ""),
                "stream_parallel_shards_enabled": bool(parallel_config.enabled),
                "stream_parallel_shards_used": bool(parallel_completed),
                "stream_parallel_shard_gate_reason": str(parallel_gate_reason or ""),
                "stream_parallel_shard_result": parallel_result_summary if isinstance(parallel_result_summary, dict) else {},
                "stream_public_batch_emission_consistency": dict(
                    public_batch_emission_consistency or {}
                ),
                "primary_generation_failed": bool(
                    parallel_public_batch_failure
                ),
                "primary_generation_failure": dict(
                    parallel_public_batch_failure or {}
                ),
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
        if parallel_public_batch_failure:
            abort_code = str(
                parallel_public_batch_failure.get("abort_code")
                or "PUBLIC_BATCH_UNDERFILLED_ABORT"
            )
            emission_mismatch = (
                abort_code == "PUBLIC_BATCH_EMISSION_MISMATCH_ABORT"
            )
            abort_diagnostic = {
                "kind": (
                    "public_batch_emission_mismatch_abort"
                    if emission_mismatch
                    else "public_batch_underfilled_abort"
                ),
                "request_id": str(request_id or ""),
                "project_id": int(project_id),
                **dict(parallel_public_batch_failure),
            }
            error_payload = {
                "error": abort_code,
                "abort_code": abort_code,
                "message": (
                    "公共批次冻结集与待输出集不一致，已终止流式输出及后续补生成。"
                    if emission_mismatch
                    else "公共批次在批内合并去重和局部补生成后仍未补齐，已终止后续批次及全局缺口补生成。"
                ),
                "diagnostic": abort_diagnostic,
            }
            _emit_stream_gen_diag(abort_diagnostic)
            if stream_batch_diags:
                yield stream_batch_diags.pop()
            yield json.dumps(error_payload, ensure_ascii=False)
            return None
        return state
