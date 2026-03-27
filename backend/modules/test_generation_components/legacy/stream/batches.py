from typing import Any, Iterator
import json

from modules.testing.test_generation_components.prompting.prompt_orchestration import (
    build_append_closed_loop_coverage_instruction,
    build_closed_loop_base_prompt,
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
        final_trace_emitted = False
        system_prompt = ""
        # --- STEP 1: META-ANALYSIS (Dynamic Strategy Planning) ---
        yield "@@STATUS@@:正在进行需求元分析 (Meta-Analysis)，识别系统类型与测试策略...\n"
        strategy_plan = self.analyze_requirement_context(requirement, kb_context, client, db)
        if not isinstance(strategy_plan, dict):
            # 中文注释：二次保护，防止外部 monkeypatch/异常返回导致主链路崩溃。
            strategy_plan = self._default_strategy_plan()
        yield f"@@STATUS@@:分析完成 - 系统类型: {strategy_plan.get('system_type')}, 复杂度: {strategy_plan.get('complexity')}, 策略: {json.dumps(strategy_plan.get('suggested_ratios'))}...\n"
        base_prompt = build_closed_loop_base_prompt(
            strategy_plan,
            doc_type=doc_type,
            pretty_json=True,
        )

        full_content = ""
        
        # Calculate batches
        import math

        # Dynamic Batch Size Adjustment based on User Request
        existing_unique_count = (
            count_unique_test_cases(existing_cases)
            if isinstance(existing_cases, list)
            else 0
        )
        current_existing_count = existing_unique_count
        
        if append:
            needed_to_append = expected_count - current_existing_count
            if needed_to_append > 25:
                batch_size = 25
            else:
                # If needed is small (e.g. 5), we generate all in one batch
                batch_size = max(1, needed_to_append)
        else:
            # For fresh generation, user requested 25 per batch
            batch_size = 25

        # Ensure batch_size is at least 1 to avoid infinite loop
        batch_size = max(1, batch_size)
        
        # Handle Append Mode: If expected_count is met, auto-increment
        current_count = existing_unique_count
        if append and expected_count <= current_count:
            yield f"@@STATUS@@:当前用例数({current_count})已达预期({expected_count})，自动增加 {batch_size} 条用例...\n"
            expected_count = current_count + batch_size

        total_batches = math.ceil((expected_count - (start_id - 1)) / batch_size)
        # Ensure at least 1 batch if needed
        if total_batches < 1 and expected_count > (start_id - 1):
            total_batches = 1
        
        current_id = start_id
        
        # History tracking for de-duplication
        history_summaries = []
        if append and isinstance(existing_cases, list):
            for c in existing_cases:
                if isinstance(c, dict):
                    history_summaries.append(f"{c.get('id', '')}: {c.get('description', '')}")

        def _merged_unique_total(new_cases: Any) -> int:
            """中文注释：统一计算“历史+新增”的唯一总数，避免补齐逻辑口径不一致。"""
            merged: list[dict[str, Any]] = []
            if append and isinstance(existing_cases, list):
                merged.extend(existing_cases)
            if isinstance(new_cases, list):
                merged.extend(new_cases)
            return count_unique_test_cases(merged)

        for i in range(total_batches):
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
                yield f"@@STATUS@@:正在生成第 {i+1}/{total_batches} 批次 ({current_batch_count} 条) - 第 {attempt} 次尝试...\n"

                # Build history context (last 50 items to save tokens)
                history_context_str = ""
                if history_summaries:
                    recent_history = history_summaries[-50:]
                    history_list_str = "\n".join([f"- {h}" for h in recent_history])
                    history_context_str = f"""
                    IMPORTANT - DE-DUPLICATION INSTRUCTION:
                    The following test scenarios have ALREADY been generated. 
                    DO NOT generate duplicates or very similar cases to these:
                    {history_list_str}
                    
                    Focus on NEW scenarios in the current module closed loop first.
                    """
                # --- COVERAGE & GAP ANALYSIS (CRITICAL) ---
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
                
                # --- REFERENCE KNOWLEDGE (RAG) ---
                The following content is retrieved from the knowledge base (Historical Test Cases / Docs).
                USAGE RULES:
                1. Use this ONLY for understanding the project's terminology, style, and format.
                2. DO NOT copy these test cases unless they are strictly relevant to the current requirement.
                3. If the Reference Knowledge conflicts with the current Requirement, FOLLOW THE CURRENT REQUIREMENT.
                4. IGNORE the order of test cases in the Reference Knowledge. You MUST follow the order of the *Current Requirement*.
                
                [START REFERENCE]
                {kb_context}
                [END REFERENCE]
                
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
                If the Requirement mentions UI layout, styles, or specific visual elements (e.g., "入口是什么样式", "图2"):
                - You MUST generate a "UI Verification" test case as the VERY FIRST case for that module.
                - Verify the visual appearance matches the description/image.
                - Do NOT skip visual details just because they are not "functional actions".
                
                BATCH GENERATION INSTRUCTION (workflow-first):
                This is batch {i+1} of {total_batches}.
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
                    yield chunk # Stream chunk directly for better performance
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
                        # Update history for next batch/retry
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
            }
        )
        return state

