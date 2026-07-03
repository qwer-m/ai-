import json
import time
from typing import Any, Iterator

from .batch_diagnostics import (
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
    emit_stream_batch_quality_diag as _emit_stream_batch_quality_diag_payload,
    emit_stream_batch_token_usage_diag as _emit_stream_batch_token_usage_diag_payload,
)
from .batch_flow_control import (
    build_existing_case_history,
    build_stream_batch_quality_metric,
    resolve_stream_batch_plan,
)
from .batch_prompt_runtime import (
    append_history_to_testcase_context,
    build_recent_history_context,
    build_stream_batch_system_prompt,
)
from .runtime import LazyAttrProxy, call_component


LogEntry = LazyAttrProxy("core.db.models", "LogEntry")


def analyze_coverage(*args: Any, **kwargs: Any) -> Any:
    return call_component("...coverage.coverage_analyzer", "analyze_coverage", *args, **kwargs)


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


def count_unique_test_cases(*args: Any, **kwargs: Any) -> Any:
    return call_component("..adapters", "count_unique_test_cases", *args, **kwargs)


def infer_case_kind(*args: Any, **kwargs: Any) -> Any:
    return call_component("..adapters", "infer_case_kind", *args, **kwargs)


def normalize_json_structure(*args: Any, **kwargs: Any) -> Any:
    return call_component("..adapters", "normalize_json_structure", *args, **kwargs)


class LegacyGenerationStreamBatchesMixin:

    def _stream_run_batches_phase(
        self,
        *,
        state: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        client = state["client"]
        requirement = state["requirement"]
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
        total_batches = int(batch_plan["total_batches"])
        current_id = start_id

        history_summaries, seen_case_signatures = build_existing_case_history(
            existing_cases,
            append=bool(append),
            build_case_signature_fn=_build_case_signature,
        )
        batch_quality_metrics: list[dict[str, Any]] = []
        low_gain_streak = 0
        early_stop_triggered = False
        early_stop_reason = ""
        stream_batch_diags: list[str] = []

        primary_batches_started = time.perf_counter()
        completed_batches = 0
        for batch_index in range(total_batches):
            remaining = expected_count - (current_id - start_id)
            current_batch_count = min(batch_size, remaining)
            if current_batch_count <= 0:
                break

            generated_in_batch = 0
            attempt = 0
            parsed_batch_cases: list[dict[str, Any]] = []

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
                    yield chunk
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
                    parsed_batch = clean_and_parse_json(attempt_content)
                    parsed_batch = normalize_json_structure(parsed_batch)
                    if isinstance(parsed_batch, list):
                        parsed_batch_cases = [case for case in parsed_batch if isinstance(case, dict)]
                        attempt_timing_event["parsed_case_count"] = int(len(parsed_batch_cases))
                        attempt_timing_event["attempt_status"] = "parsed"
                        if len(parsed_batch_cases) > int(need):
                            overflow_count = int(len(parsed_batch_cases) - int(need))
                            parsed_batch_cases = parsed_batch_cases[: int(need)]
                            _emit_stream_batch_quality_diag(
                                {
                                    "batch_index": int(batch_index + 1),
                                    "batch_overflow_trimmed": True,
                                    "requested_count": int(need),
                                    "overflow_count": int(overflow_count),
                                }
                            )
                            if stream_batch_diags:
                                yield stream_batch_diags.pop()
                        generated_in_batch = current_batch_count
                        if parsed_batch_cases:
                            full_content += json.dumps(parsed_batch_cases, ensure_ascii=False, indent=2)
                            full_content += "\n"
                            for case in parsed_batch_cases:
                                history_summaries.append(f"{case.get('id', '')}: {case.get('description', '')}")
                        else:
                            full_content += "[]\n"
                        break
                except Exception:
                    attempt_timing_event["attempt_status"] = "parse_failed"
                    pass

            if parsed_batch_cases:
                batch_metric, low_gain_streak = build_stream_batch_quality_metric(
                    parsed_batch_cases=parsed_batch_cases,
                    seen_case_signatures=seen_case_signatures,
                    batch_index=int(batch_index + 1),
                    build_case_signature_fn=_build_case_signature,
                    is_non_assertable_expected_result_fn=_is_non_assertable_expected_result,
                    previous_low_gain_streak=int(low_gain_streak),
                )
                batch_quality_metrics.append(batch_metric)
                _emit_stream_batch_quality_diag(batch_quality_metrics[-1])
                if stream_batch_diags:
                    yield stream_batch_diags.pop()

                if low_gain_streak >= 2:
                    early_stop_triggered = True
                    early_stop_reason = "low_incremental_gain_two_batches"
                    _emit_stream_batch_quality_diag(
                        {
                            "batch_index": int(batch_index + 1),
                            "early_stop_triggered": True,
                            "early_stop_reason": str(early_stop_reason),
                            "low_gain_streak": int(low_gain_streak),
                        }
                    )
                    if stream_batch_diags:
                        yield stream_batch_diags.pop()
                    yield "@@STATUS@@:\u68c0\u6d4b\u5230\u8fde\u7eed2\u6279\u4f4e\u4fe1\u606f\u589e\u76ca\uff0c\u63d0\u524d\u505c\u6b62\u540e\u7eed\u6279\u6b21\u751f\u6210\u3002\n"
            else:
                low_gain_streak = 0

            current_id += current_batch_count
            completed_batches += 1
            if early_stop_triggered:
                break

        _record_timing_event(
            "primary_batches",
            primary_batches_started,
            total_batches=int(total_batches),
            completed_batches=int(completed_batches),
            batch_size=int(batch_size),
            expected_count=int(expected_count or 0),
            early_stop_triggered=bool(early_stop_triggered),
            early_stop_reason=str(early_stop_reason or ""),
        )
        _record_timing_event(
            "stream_generation_phase",
            phase_started,
            total_batches=int(total_batches),
            completed_batches=int(completed_batches),
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
                "stream_early_stop_triggered": bool(early_stop_triggered),
                "stream_early_stop_reason": str(early_stop_reason or ""),
                "generation_timing_events": timing_events,
            }
        )
        return state
