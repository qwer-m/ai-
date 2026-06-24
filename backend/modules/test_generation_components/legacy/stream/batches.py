from typing import Any, Iterator
import json

from core.db.models import LogEntry
from ...coverage.coverage_analyzer import (
    analyze_coverage,
)
from ...prompting.prompt_orchestration import (
    build_append_closed_loop_coverage_instruction,
    build_closed_loop_base_prompt,
)
from ...prompting.structured_context import (
    build_structured_prompt_context,
)
from ...prompting.generation_diagnostics import (
    build_prompt_context_intake_diagnostics,
)
from ..adapters import (
    clean_and_parse_json,
    count_unique_test_cases,
    infer_case_kind,
    normalize_json_structure,
)


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

        def _extract_requirement_semantics_payload(prompt_context: dict[str, Any]) -> dict[str, list[str]]:
            payload: dict[str, list[str]] = {}
            for key in (
                "confirmed_facts",
                "scoped_rules",
                "pending_items",
                "reuse_declarations",
                "hard_flow_constraints",
                "reuse_risks",
            ):
                values = prompt_context.get(key)
                if isinstance(values, list):
                    payload[key] = [str(item).strip() for item in values if str(item).strip()]
                else:
                    payload[key] = []
            return payload

        def _emit_biz_key_diag(prompt_context: dict[str, Any]) -> None:
            """中文注释：把 biz_key 隔离检查写入 GEN_DIAG，便于前端日志区观察。"""
            nonlocal biz_diag_emitted
            if biz_diag_emitted or (not self._is_active_db_session(db)):
                return
            try:
                payload = dict(prompt_context.get("biz_key_isolation_log") or {})
                if not payload:
                    return
                payload.update(
                    {
                        "project_id": int(project_id),
                        "request_id": request_id,
                        "source": "generate_test_cases_stream",
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
                db.commit()
                biz_diag_emitted = True
            except Exception as e:
                try:
                    db.rollback()
                except Exception:
                    pass
                print(f"Failed to emit biz key isolation log(stream): {e}")

        def _emit_stream_batch_quality_diag(batch_metric: dict[str, Any]) -> None:
            """中文注释：输出每批质量指标到 GEN_DIAG，便于实时观察低增益趋势。"""
            payload = {
                "kind": "stream_batch_quality",
                "project_id": int(project_id),
                "request_id": str(request_id or ""),
                "current_biz_key": str(current_biz_key or "unknown"),
                "multi_pass": bool(multi_pass),
                "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                **dict(batch_metric or {}),
            }
            if self._is_active_db_session(db):
                try:
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            user_id=user_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(payload, ensure_ascii=False)}",
                        )
                    )
                    db.commit()
                except Exception:
                    try:
                        db.rollback()
                    except Exception:
                        pass
            try:
                payload_json = json.dumps(payload, ensure_ascii=False)
                # 说明：写入前端 stream 诊断通道，不依赖 DB 持久化成功与否。
                stream_batch_diags.append(f"GEN_DIAG:{payload_json}\n")
            except Exception:
                pass

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
            actual_model = ""
            try:
                actual_model = str(client.select_model(f"{system_prompt_text or ''}{requirement or ''}", task_type="generation"))
            except Exception:
                actual_model = str(getattr(client, "model", "") or "")
            payload = build_prompt_context_intake_diagnostics(
                prompt_context=prompt_context,
                context_result=context_result if isinstance(context_result, dict) else {},
                requirement=requirement or "",
                kb_context=kb_context or "",
                base_prompt=base_prompt_text or "",
                system_prompt=system_prompt_text or "",
                mode="stream",
                doc_type=doc_type,
                compress=compress,
                project_id=project_id,
                request_id=request_id,
                batch_index=batch_index_value,
                total_batches=total_batches_value,
                attempt=attempt_value,
                expected_count=requested_count,
                multi_pass=bool(multi_pass),
                generation_mode=generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                model=actual_model,
                max_output_tokens=getattr(client, "max_tokens", None),
            )
            if self._is_active_db_session(db):
                try:
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            user_id=user_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(payload, ensure_ascii=False)}",
                        )
                    )
                    db.commit()
                except Exception:
                    try:
                        db.rollback()
                    except Exception:
                        pass
            try:
                stream_batch_diags.append(f"GEN_DIAG:{json.dumps(payload, ensure_ascii=False)}\n")
            except Exception:
                pass

        def _is_retryable_provider_error(message: str) -> bool:
            text = str(message or "").strip().lower()
            if not text:
                return False
            fatal_markers = (
                "[额度耗尽]",
                "insufficient_quota",
                "quota exceeded",
                "billing",
                "unauthorized",
                "invalid api key",
                "permission denied",
                "content policy",
                "safety",
                "forbidden",
            )
            if any(marker in text for marker in fatal_markers):
                return False
            retryable_markers = (
                "exception occurred:",
                "incomplete chunked read",
                "peer closed connection",
                "read operation timed out",
                "read timed out",
                "timeout",
                "connection reset",
                "connection aborted",
                "remote protocol error",
                "temporarily unavailable",
                "bad gateway",
                "service unavailable",
                "gateway timeout",
                "502",
                "503",
                "504",
            )
            return any(marker in text for marker in retryable_markers)

        def _build_batch_token_usage(
            *,
            batch_index: int,
            total_batches: int,
            attempt: int,
            need: int,
            system_prompt_text: str,
            requirement_text: str,
            output_text: str,
        ) -> dict[str, Any]:
            metadata = dict(getattr(client, "last_response_metadata", {}) or {})

            def _meta_int(*keys: str) -> int:
                for key in keys:
                    value = metadata.get(key)
                    try:
                        number = int(value)
                    except Exception:
                        continue
                    if number >= 0:
                        return number
                return -1

            input_tokens = _meta_int("input_tokens", "prompt_tokens")
            output_tokens = _meta_int("output_tokens", "completion_tokens")
            estimate_method = str(metadata.get("token_estimate_method") or "").strip()
            has_provider_usage = input_tokens >= 0 and output_tokens >= 0 and not estimate_method
            token_unavailable_reason = ""
            if not has_provider_usage:
                token_unavailable_reason = "provider_usage_missing"
                if estimate_method:
                    token_unavailable_reason = "provider_usage_estimated"
            return {
                "kind": "stream_batch_token_usage",
                "project_id": int(project_id),
                "request_id": str(request_id or ""),
                "current_biz_key": str(current_biz_key or "unknown"),
                "multi_pass": bool(multi_pass),
                "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                "batch_index": int(batch_index),
                "total_batches": int(total_batches),
                "attempt": int(attempt),
                "requested_count": int(need),
                "input_tokens": int(input_tokens) if has_provider_usage else None,
                "output_tokens": int(output_tokens) if has_provider_usage else None,
                "total_tokens": int(input_tokens + output_tokens) if has_provider_usage else None,
                "token_source": "provider" if has_provider_usage else "unavailable",
                "token_unavailable_reason": token_unavailable_reason,
                "estimate_method": estimate_method,
                "model": str(metadata.get("model") or getattr(client, "model", "") or ""),
            }

        def _emit_stream_batch_token_usage_diag(payload: dict[str, Any]) -> None:
            if self._is_active_db_session(db):
                try:
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            user_id=user_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(payload, ensure_ascii=False)}",
                        )
                    )
                    db.commit()
                except Exception:
                    try:
                        db.rollback()
                    except Exception:
                        pass
            try:
                stream_batch_diags.append(f"GEN_DIAG:{json.dumps(payload, ensure_ascii=False)}\n")
            except Exception:
                pass

        def _norm_text(value: Any) -> str:
            return "".join(str(value or "").strip().lower().split())

        def _build_case_signature(case: dict[str, Any]) -> str:
            steps = case.get("steps") if isinstance(case.get("steps"), list) else []
            return "|".join(
                [
                    _norm_text(case.get("test_module")),
                    _norm_text(case.get("description")),
                    _norm_text(case.get("test_input")),
                    _norm_text(case.get("expected_result")),
                    _norm_text(" ".join([str(step) for step in steps])),
                ]
            )

        def _is_non_assertable_expected_result(text: str) -> bool:
            normalized = _norm_text(text)
            if not normalized:
                return True
            weak_tokens = (
                "正常展示",
                "符合预期",
                "执行成功",
                "返回成功",
                "结果可核对",
                "结果正确",
                "shows expected result",
                "works as expected",
                "success",
            )
            return any(token in normalized for token in weak_tokens)

        def _build_stream_coverage_plan_lite(requirement_text: str) -> tuple[str, list[dict[str, Any]]]:
            coverage_seed = analyze_coverage(str(requirement_text or ""), [])
            diagnostics = [
                item
                for item in (coverage_seed.get("rule_diagnostics") or [])
                if isinstance(item, dict) and str(item.get("rule_text") or "").strip()
            ]
            rules = diagnostics[:16]
            if not rules:
                return "", []
            lines = [
                "# --- COVERAGE PLAN-LITE (internal planning, do not output this section) ---",
                "Use these confirmed requirement rules as the generation plan.",
                "Prefer one high-value case per distinct rule first; add boundary/exception/risk cases only when the rule itself supports them.",
            ]
            for index, item in enumerate(rules, start=1):
                rule_text = str(item.get("rule_text") or "").strip()
                rule_id = str(item.get("rule_id") or f"RULE-{index:03d}").strip()
                lines.append(f"{index}. {rule_id}: {rule_text[:180]}")
            lines.extend(
                [
                    "Before adding a case, identify its validation goal internally.",
                    "Do not generate cases for headings, notes, pending运营补充文案, or unsupported assumptions.",
                ]
            )
            return "\n".join(lines), rules

        # --- STEP 1: META-ANALYSIS ---
        if multi_pass:
            yield "@@STATUS@@:[multi-pass] 阶段1/3 主生成开始...\n"
        yield "@@STATUS@@:正在进行需求元分析 (Meta-Analysis)，识别系统类型与测试策略...\n"
        strategy_plan = self.analyze_requirement_context(requirement, kb_context, client, db)
        if not isinstance(strategy_plan, dict):
            # 中文注释：元分析异常时使用默认策略，避免主链路中断。
            strategy_plan = self._default_strategy_plan()
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
        coverage_plan_lite, coverage_plan_rules = _build_stream_coverage_plan_lite(requirement)
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

        # 计算批次参数
        import math

        existing_unique_count = count_unique_test_cases(existing_cases) if isinstance(existing_cases, list) else 0
        current_existing_count = existing_unique_count

        if append:
            needed_to_append = expected_count - current_existing_count
            if needed_to_append > 25:
                batch_size = 25
            else:
                batch_size = max(1, needed_to_append)
        else:
            batch_size = 25

        batch_size = max(1, batch_size)

        if append and expected_count <= existing_unique_count:
            yield (
                f"@@STATUS@@:当前用例数({existing_unique_count})已达预期({expected_count})，"
                f"自动增加 {batch_size} 条用例...\n"
            )
            expected_count = existing_unique_count + batch_size

        total_batches = math.ceil((expected_count - (start_id - 1)) / batch_size)
        if total_batches < 1 and expected_count > (start_id - 1):
            total_batches = 1

        current_id = start_id

        # 中文注释：追踪历史摘要用于去重提示。
        history_summaries: list[str] = []
        seen_case_signatures: set[str] = set()
        batch_quality_metrics: list[dict[str, Any]] = []
        low_gain_streak = 0
        early_stop_triggered = False
        early_stop_reason = ""
        stream_batch_diags: list[str] = []
        if append and isinstance(existing_cases, list):
            for case in existing_cases:
                if isinstance(case, dict):
                    history_summaries.append(f"{case.get('id', '')}: {case.get('description', '')}")
                    signature = _build_case_signature(case)
                    if signature:
                        seen_case_signatures.add(signature)

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
                yield (
                    f"@@STATUS@@:正在生成第 {batch_index + 1}/{total_batches} 批次"
                    f" ({current_batch_count} 条) - 第 {attempt} 次尝试...\n"
                )

                history_context_str = ""
                if history_summaries:
                    recent_history = history_summaries[-50:]
                    history_list_str = "\n".join([f"- {item}" for item in recent_history])
                    history_context_str = f"""
                    IMPORTANT - DE-DUPLICATION INSTRUCTION:
                    The following test scenarios have ALREADY been generated.
                    DO NOT generate duplicates or very similar cases to these:
                    {history_list_str}

                    Focus on NEW scenarios in the current module closed loop first.
                    """

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

                testcase_context = prompt_context.get("testcase_context") or "(empty)"
                if history_summaries:
                    recent_history_style = "\n".join(history_summaries[-50:])
                    testcase_context = f"{testcase_context}\n\n[本轮已生成摘要]\n{recent_history_style}"

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

                system_prompt = f"""
                {base_prompt}

                {coverage_instruction}

                {history_context_str}

                {coverage_plan_lite}

                # --- GENERATION STRATEGY ---
                1. ANALYZE the User's Requirement (provided in the next message) step-by-step.
                2. IDENTIFY the specific functionality, logic, and constraints in the User's Requirement.
                3. APPLY Testing Techniques:
                   - Equivalence Partitioning: Identify valid/invalid inputs.
                   - Boundary Value Analysis: Test edges (min, max, null, overflow).
                   - Scenario Testing: Cover happy paths and error paths.
                4. GENERATE new test cases that target the User's Requirement.
                   - Do NOT generate generic cases unrelated to the specific logic.
                   - Do NOT repeat test cases found in Reference Knowledge unless necessary.
                5. FINAL CHECK: Ensure the first test case corresponds to the *first step* of the User's Requirement (e.g., Entry Point).

                # --- VISUAL/LAYOUT TESTING RULE ---
                If the Requirement mentions UI layout, styles, or specific visual elements:
                - You MUST generate a "UI Verification" test case as the VERY FIRST case for that module.
                - Verify the visual appearance matches the description/image.
                - Do NOT skip visual details just because they are not "functional actions".

                BATCH GENERATION INSTRUCTION (quality-first):
                This is batch {batch_index + 1} of {total_batches}.
                Start the Test Case IDs from {int(current_id) + int(generated_in_batch)} (e.g., TC-{(int(current_id) + int(generated_in_batch)):03d}).
                Reference count: about {need} cases. This is NOT a quota.
                Generate fewer cases if additional cases would be:
                - duplicate of existing validation goals
                - weakly grounded in requirement evidence
                - non-assertable
                - only generic UI/database/permission checks
                - not adding new module, flow, rule, or scenario coverage

                Every case must pass these gates:
                1. It targets a specific business rule or workflow step.
                2. Its expected_result is concrete and verifiable.
                3. It adds new coverage compared with existing cases.
                4. Keep closed-loop continuity in current module first; do not jump modules only to match count.

                If no meaningful incremental cases remain, return [].

                Return ONLY the JSON array.
                """

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

                token_usage_diag = _build_batch_token_usage(
                    batch_index=batch_index + 1,
                    total_batches=total_batches,
                    attempt=attempt,
                    need=need,
                    system_prompt_text=system_prompt,
                    requirement_text=requirement,
                    output_text=attempt_content,
                )
                _emit_stream_batch_token_usage_diag(token_usage_diag)
                if stream_batch_diags:
                    yield stream_batch_diags.pop()

                if not provider_error and not chunk_acc.strip():
                    if attempt < 3:
                        yield "\n@@STATUS@@:模型未返回内容，正在重试...\n"
                        continue
                    yield "\n@@STATUS@@:生成失败\n"
                    yield "Error: 模型未返回内容（可能是模型配置/额度/网络/内容安全导致），请检查后重试\n"
                    attempt = 3
                    break

                if provider_error:
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
                    pass

            if parsed_batch_cases:
                new_valid_cases_count = int(len(parsed_batch_cases))
                unique_increment = 0
                non_assertable_count = 0
                for case in parsed_batch_cases:
                    signature = _build_case_signature(case)
                    if signature and signature not in seen_case_signatures:
                        seen_case_signatures.add(signature)
                        unique_increment += 1
                    if _is_non_assertable_expected_result(str(case.get("expected_result") or "")):
                        non_assertable_count += 1
                duplicate_count = max(0, new_valid_cases_count - unique_increment)
                duplicate_rate = float(duplicate_count) / float(new_valid_cases_count) if new_valid_cases_count > 0 else 1.0
                coverage_gain_count = int(unique_increment)
                low_quality_filtered_count = int(non_assertable_count)
                low_gain_detected = bool(
                    (coverage_gain_count <= 1)
                    or (duplicate_rate >= 0.6)
                    or (new_valid_cases_count > 0 and (float(non_assertable_count) / float(new_valid_cases_count)) >= 0.5)
                )
                if low_gain_detected:
                    low_gain_streak += 1
                else:
                    low_gain_streak = 0

                batch_quality_metrics.append(
                    {
                        "batch_index": int(batch_index + 1),
                        "new_valid_cases_count": int(new_valid_cases_count),
                        "duplicate_rate": round(float(duplicate_rate), 4),
                        "non_assertable_count": int(non_assertable_count),
                        "low_quality_filtered_count": int(low_quality_filtered_count),
                        "coverage_gain_count": int(coverage_gain_count),
                        "low_gain_detected": bool(low_gain_detected),
                        "low_gain_streak": int(low_gain_streak),
                    }
                )
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
                    yield "@@STATUS@@:检测到连续2批低信息增益，提前停止后续批次生成。\n"
            else:
                low_gain_streak = 0

            current_id += current_batch_count
            if early_stop_triggered:
                break

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
            }
        )
        return state
