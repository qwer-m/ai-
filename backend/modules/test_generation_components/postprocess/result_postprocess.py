from __future__ import annotations

import json
from typing import Any, Callable, Iterator


def prepare_append_existing_cases(
    existing_generated_result: str | None,
    *,
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    count_unique_test_cases_fn: Callable[[list[dict[str, Any]]], int],
) -> tuple[list[dict[str, Any]], int, int]:
    """Load and normalize historical append cases before generation."""
    existing_cases: list[dict[str, Any]] = []
    existing_unique_count = 0
    start_id = 1

    if not existing_generated_result:
        return existing_cases, existing_unique_count, start_id

    try:
        parsed = json.loads(existing_generated_result)
        if isinstance(parsed, list):
            parsed = normalize_json_structure_fn(parsed)
            if not isinstance(parsed, list):
                parsed = []
            parsed = deduplicate_test_cases_fn(parsed)
            existing_cases = parsed
            existing_unique_count = count_unique_test_cases_fn(existing_cases)
            start_id = existing_unique_count + 1
    except Exception:
        pass

    return existing_cases, existing_unique_count, start_id


def finalize_generated_cases(
    generated_result: Any,
    *,
    start_id: int,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    reorder_cases_by_closed_loop_fn: Callable[..., list[dict[str, Any]]],
) -> Any:
    """Parse, normalize, deduplicate, and reorder generated cases."""
    if isinstance(generated_result, (list, dict)):
        result: Any = generated_result
    else:
        result = clean_and_parse_json_fn(str(generated_result))

    if isinstance(result, list):
        result = normalize_json_structure_fn(result)
        result = deduplicate_test_cases_fn(result)
        result = reorder_cases_by_closed_loop_fn(
            result,
            start_id=start_id,
            renumber_ids=True,
        )
    return result


def merge_cases_for_append(
    existing_cases: list[dict[str, Any]],
    new_cases: Any,
    *,
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    reorder_cases_by_closed_loop_fn: Callable[..., list[dict[str, Any]]],
) -> Any:
    """Merge append-mode historical cases with new cases before persistence."""
    if not isinstance(new_cases, list):
        return new_cases

    merged_result: list[dict[str, Any]] = []
    if isinstance(existing_cases, list):
        merged_result.extend(existing_cases)
    merged_result.extend(new_cases)
    merged_result = deduplicate_test_cases_fn(merged_result)
    merged_result = reorder_cases_by_closed_loop_fn(
        merged_result,
        start_id=1,
        renumber_ids=True,
    )
    return merged_result


def stream_postprocess_cases(
    *,
    client: Any,
    requirement: str,
    base_prompt: str,
    kb_context: str,
    full_content: str,
    expected_count: int,
    append: bool,
    existing_cases: list[dict[str, Any]],
    existing_unique_count: int,
    start_id: int,
    db: Any,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    reorder_cases_by_closed_loop_fn: Callable[..., list[dict[str, Any]]],
    count_unique_test_cases_fn: Callable[[list[dict[str, Any]]], int],
    infer_case_kind_fn: Callable[[dict[str, Any]], str],
    build_supplement_closed_loop_instruction_fn: Callable[..., str],
) -> Iterator[str]:
    """Post-process streamed content and return the final case payload."""

    def _merged_unique_total(new_cases: Any) -> int:
        merged: list[dict[str, Any]] = []
        if append and isinstance(existing_cases, list):
            merged.extend(existing_cases)
        if isinstance(new_cases, list):
            merged.extend(new_cases)
        return count_unique_test_cases_fn(merged)

    parsed_result = clean_and_parse_json_fn(full_content)
    parsed_result = normalize_json_structure_fn(parsed_result)
    if isinstance(parsed_result, list):
        parsed_result = deduplicate_test_cases_fn(parsed_result)

    current_total = _merged_unique_total(parsed_result)

    # 中文注释：流式返回空数组时，先尝试一次非流式补救，降低“空结果”失败率。
    if isinstance(parsed_result, list) and current_total == 0 and int(expected_count or 0) > 0:
        yield "@@STATUS@@:初次流式结果为空，尝试非流式补救...\n"
        rescue_prompt = f"""
                {base_prompt}

                Reference Knowledge (Use this style/info if relevant):
                {kb_context}

                RESCUE INSTRUCTION:
                - Return at least {max(1, int(expected_count))} test cases.
                - Return ONLY a strict JSON array of objects.
                - Do not include markdown/code fences/explanations.
                """
        try:
            rescue_raw = client.generate_response(
                requirement,
                rescue_prompt,
                db=db,
                task_type="generation",
            )
            rescue_parsed = clean_and_parse_json_fn(str(rescue_raw or ""))
            rescue_parsed = normalize_json_structure_fn(rescue_parsed)
            if isinstance(rescue_parsed, list) and rescue_parsed:
                parsed_result = deduplicate_test_cases_fn(rescue_parsed)
                current_total = _merged_unique_total(parsed_result)
                yield f"@@STATUS@@:非流式补救成功，恢复 {len(parsed_result)} 条用例。\n"
            else:
                yield "@@STATUS@@:非流式补救未得到有效用例，继续补齐策略...\n"
        except Exception as rescue_err:
            yield f"@@STATUS@@:非流式补救异常({str(rescue_err)})，继续补齐策略...\n"

    if isinstance(parsed_result, list) and expected_count:
        if current_total < expected_count:
            supplement_history: list[str] = []
            for c in parsed_result[-50:]:
                if isinstance(c, dict):
                    supplement_history.append(f"- {c.get('id', '')}: {c.get('description', '')}")
            if append and isinstance(existing_cases, list):
                for c in existing_cases[-50:]:
                    if isinstance(c, dict):
                        supplement_history.append(f"- {c.get('id', '')}: {c.get('description', '')}")

            supplement_history_str = ""
            if supplement_history:
                supplement_history_str = f"""
                        EXISTING CASES (Do NOT overlap or duplicate):
                        {chr(10).join(supplement_history)}
                        """

            missing = expected_count - current_total
            supplement_attempt = 0
            while missing > 0 and supplement_attempt < 3:
                supplement_attempt += 1
                yield f"@@STATUS@@:检测到缺少 {missing} 条用例，正在补齐(第 {supplement_attempt} 次)...\n"

                supplement_source: list[dict[str, Any]] = []
                if append and isinstance(existing_cases, list):
                    supplement_source.extend([x for x in existing_cases if isinstance(x, dict)])
                supplement_source.extend([x for x in parsed_result if isinstance(x, dict)])

                closed_loop_supplement_instruction = build_supplement_closed_loop_instruction_fn(
                    all_cases=supplement_source,
                    requirement=requirement,
                    infer_case_kind_fn=infer_case_kind_fn,
                )
                system_prompt = f"""
                        {base_prompt}

                        Reference Knowledge (Use this style/info if relevant):
                        {kb_context}

                        {supplement_history_str}

                        SUPPLEMENT INSTRUCTION (CLOSED-LOOP-FIRST):
                        Target additional count: {missing}.
                        Start the Test Case IDs from {current_total + 1} (e.g., TC-{(current_total + 1):03d}).
                        {closed_loop_supplement_instruction}
                        Each new case must have a DISTINCT verification point and must NOT overlap with existing cases.
                        Do not repeat the same test_input + expected_result + test_module combination.
                        Return ONLY the JSON array.
                        """

                extra_content = ""
                extra_stream = client.generate_response_stream(
                    requirement,
                    system_prompt,
                    task_type="generation",
                )
                provider_error = None
                for chunk in extra_stream:
                    extra_content += chunk
                    full_content += chunk
                    yield chunk
                    if (
                        chunk.startswith("Error:")
                        or chunk.startswith("[额度耗尽]")
                        or chunk.startswith("Exception occurred:")
                    ):
                        provider_error = chunk
                        break
                if provider_error:
                    yield "\n@@STATUS@@:生成失败\n"
                    yield f"{provider_error}\n"
                    break

                full_content += "\n"
                yield "\n"
                try:
                    extra_parsed = clean_and_parse_json_fn(extra_content)
                    extra_parsed = normalize_json_structure_fn(extra_parsed)
                    if isinstance(extra_parsed, list) and extra_parsed:
                        parsed_result.extend(extra_parsed)
                        parsed_result = normalize_json_structure_fn(parsed_result)
                        parsed_result = deduplicate_test_cases_fn(parsed_result)
                        current_total = _merged_unique_total(parsed_result)
                except Exception:
                    pass

                missing = expected_count - current_total

        if current_total > expected_count:
            target_new_count = expected_count
            if append and isinstance(existing_cases, list):
                target_new_count = expected_count - existing_unique_count
            target_new_count = max(0, target_new_count)

            if len(parsed_result) > target_new_count:
                excess = len(parsed_result) - target_new_count
                yield (
                    f"@@STATUS@@:已生成 {len(parsed_result)} 条新用例，需保留 {target_new_count} 条。"
                    "正在调用 QA Review Agent 进行过拟合审查...\n"
                )

                try:
                    candidates_json = json.dumps(parsed_result, ensure_ascii=False)

                    existing_context = ""
                    if append and isinstance(existing_cases, list) and existing_cases:
                        existing_summaries = []
                        for ec in existing_cases[-50:]:
                            mod = ec.get("test_module", "General")
                            desc = str(ec.get("description", ec.get("test_step", "")))[:100].replace("\n", " ")
                            existing_summaries.append(f"- [{mod}] {desc}")
                        existing_context = f"""
                                EXISTING TEST CASES (Reference Only - Do NOT Duplicate):
                                {chr(10).join(existing_summaries)}
                                {len(existing_cases) > 50 and f"... (and {len(existing_cases)-50} more earlier cases)" or ""}
                                """

                    review_prompt = f"""
                            You are a Senior QA Lead performing a Test Case Review.

                            CONTEXT:
                            We have generated a list of test cases, but we have exceeded the budget.
                            - Candidates Count: {len(parsed_result)}
                            - Max Allowed (Target): {target_new_count}
                            {existing_context}

                            TASK:
                            Select exactly {target_new_count} BEST test cases from the Candidates list below.

                            CRITERIA FOR SELECTION (Anti-Overfitting):
                            1. **Independence (Zero Coupling)**: Each case must be ATOMIC and test exactly one verification point.
                            2. **Eliminate Redundancy**: If multiple cases test the exact same logic path (just with different data), KEEP ONLY ONE.
                            3. **Check against Existing**: If a candidate duplicates logic already covered in "EXISTING TEST CASES", DISCARD IT.
                            4. **Clear Purpose**: Prefer cases with a single, specific goal over multi-goal cases.
                            5. **Remove Bloat**: Remove trivial cases (e.g., checking UI color) if core functionality is at risk.
                            6. **Prioritize Value**:
                               - MUST KEEP: P0 (Critical Path).
                               - MUST KEEP: Security / Performance / Data Integrity cases.
                               - MUST KEEP: Complex Boundary cases.
                               - DISCARD: P2/P3 cases that are low value or "water injection" (凑数).

                            CANDIDATES LIST:
                            {candidates_json}

                            OUTPUT:
                            Return a STRICT JSON array containing ONLY the selected {target_new_count} test case objects.
                            Do not modify the content of the test cases, just select them.
                            """

                    review_response = client.generate_response(
                        review_prompt,
                        "You are a QA Auditor.",
                        db=db,
                        task_type="generation",
                    )
                    reviewed_cases = clean_and_parse_json_fn(review_response)
                    reviewed_cases = normalize_json_structure_fn(reviewed_cases)
                    if isinstance(reviewed_cases, list) and reviewed_cases:
                        if len(reviewed_cases) > target_new_count:
                            reviewed_cases = reviewed_cases[:target_new_count]
                        parsed_result = deduplicate_test_cases_fn(reviewed_cases)
                        yield f"@@STATUS@@:QA Agent 审查完成，已剔除 {excess} 条冗余/低价值用例。\n"
                    else:
                        raise ValueError("QA Agent returned invalid result")

                except Exception as e:
                    yield f"@@STATUS@@:QA Agent 审查异常 ({str(e)})，改用规则评分筛选...\n"

                    def calculate_value(case: dict[str, Any]) -> int:
                        score = 0
                        p = str(case.get("priority", "P1")).upper()
                        if p == "P0":
                            score += 100
                        elif p == "P1":
                            score += 50
                        else:
                            score += 10

                        desc = str(case.get("description", "")).lower()
                        if any(k in desc for k in ["安全", "xss", "sql", "security", "性能", "perf", "并发"]):
                            score += 20
                        return score

                    annotated: list[dict[str, Any]] = []
                    for idx, case in enumerate(parsed_result):
                        if isinstance(case, dict):
                            annotated.append({"case": case, "score": calculate_value(case), "index": idx})

                    annotated.sort(key=lambda x: (x["score"], -x["index"]))
                    indices_to_remove = {annotated[i]["index"] for i in range(excess)}
                    final_result: list[dict[str, Any]] = []
                    for idx, case in enumerate(parsed_result):
                        if idx not in indices_to_remove and isinstance(case, dict):
                            final_result.append(case)

                    parsed_result = deduplicate_test_cases_fn(final_result)
                    yield "@@STATUS@@:规则筛选完成，保留高优先级与关键风险用例。\n"

    if isinstance(parsed_result, list):
        parsed_result = normalize_json_structure_fn(parsed_result)
        parsed_result = deduplicate_test_cases_fn(parsed_result)
        parsed_result = reorder_cases_by_closed_loop_fn(
            parsed_result,
            start_id=start_id,
            renumber_ids=True,
        )

    return parsed_result
