from typing import Any, Iterator
import json

from core.db.models import LogEntry
from modules.testing.test_generation_components.prompting.prompt_orchestration import (
    build_append_closed_loop_coverage_instruction,
    build_closed_loop_base_prompt,
)
from modules.testing.test_generation_components.prompting.structured_context import (
    build_structured_prompt_context,
)
from modules.testing.test_generation_components.legacy.adapters import (
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
        requirement_semantics_context = _extract_requirement_semantics_payload(prompt_context)
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
        if append and isinstance(existing_cases, list):
            for case in existing_cases:
                if isinstance(case, dict):
                    history_summaries.append(f"{case.get('id', '')}: {case.get('description', '')}")

        for batch_index in range(total_batches):
            remaining = expected_count - (current_id - start_id)
            current_batch_count = min(batch_size, remaining)
            if current_batch_count <= 0:
                break

            generated_in_batch = 0
            attempt = 0
            batch_content = ""

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

                BATCH GENERATION INSTRUCTION (workflow-first):
                This is batch {batch_index + 1} of {total_batches}.
                Start the Test Case IDs from {int(current_id) + int(generated_in_batch)} (e.g., TC-{(int(current_id) + int(generated_in_batch)):03d}).
                Target this batch size: about {need} cases.
                Keep closed-loop continuity in current module first; do not jump modules just to match count.

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

                stream = client.generate_response_stream(
                    requirement,
                    system_prompt,
                    task_type="generation",
                )
                chunk_acc = ""
                provider_error = None
                for chunk in stream:
                    chunk_acc += chunk
                    full_content += chunk
                    batch_content += chunk
                    yield chunk
                    if chunk.startswith("Error:") or chunk.startswith("[额度耗尽]") or chunk.startswith("Exception occurred:"):
                        provider_error = chunk
                        break

                if not provider_error and not chunk_acc.strip():
                    if attempt < 3:
                        yield "\n@@STATUS@@:模型未返回内容，正在重试...\n"
                        continue
                    yield "\n@@STATUS@@:生成失败\n"
                    yield "Error: 模型未返回内容（可能是模型配置/额度/网络/内容安全导致），请检查后重试\n"
                    attempt = 3
                    break

                if provider_error:
                    yield "\n@@STATUS@@:生成失败\n"
                    yield f"{provider_error}\n"
                    attempt = 3
                    break

                full_content += "\n"
                batch_content += "\n"
                yield "\n"

                try:
                    parsed_batch = clean_and_parse_json(batch_content)
                    parsed_batch = normalize_json_structure(parsed_batch)
                    if isinstance(parsed_batch, list):
                        generated_in_batch = len(parsed_batch)
                        for case in parsed_batch:
                            if isinstance(case, dict):
                                history_summaries.append(f"{case.get('id', '')}: {case.get('description', '')}")
                except Exception:
                    pass

            current_id += current_batch_count

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
                "requirement_semantics_context": requirement_semantics_context,
            }
        )
        return state
