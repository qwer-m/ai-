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
    current_biz_key: str = "",
    multi_pass: bool = True,
    generation_mode: str = "",
) -> Iterator[dict[str, Any]]:
    """流式后处理：primary 解析 + (可选) gap + review，并输出阶段统计与覆盖结果。"""

    from modules.testing.test_generation_components.coverage.coverage_analyzer import analyze_coverage
    from modules.testing.test_generation_components.prompting.prompt_orchestration import (
        build_gap_fill_prompt,
        build_review_select_prompt,
    )

    def _merged_unique_total(new_cases: Any) -> int:
        merged: list[dict[str, Any]] = []
        if append and isinstance(existing_cases, list):
            merged.extend(existing_cases)
        if isinstance(new_cases, list):
            merged.extend(new_cases)
        return count_unique_test_cases_fn(merged)

    def _signature(case: dict[str, Any]) -> str:
        module = str(case.get("test_module") or "").strip().lower()
        desc = str(case.get("description") or "").strip().lower()
        expected = str(case.get("expected_result") or "").strip().lower()
        test_input = str(case.get("test_input") or "").strip().lower()
        return f"{module}|{desc}|{expected}|{test_input}"

    def _pick_subset(candidates: list[dict[str, Any]], reviewed: list[dict[str, Any]], target_count: int) -> list[dict[str, Any]]:
        candidate_map = {_signature(item): item for item in candidates if isinstance(item, dict)}
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in reviewed:
            if not isinstance(item, dict):
                continue
            key = _signature(item)
            if key in seen:
                continue
            original = candidate_map.get(key)
            if original is None:
                continue
            selected.append(original)
            seen.add(key)
            if len(selected) >= max(1, int(target_count or 1)):
                break
        return selected

    def _priority_score(case: dict[str, Any]) -> int:
        value = str(case.get("priority") or "").strip().upper()
        return 3 if value == "P0" else 2 if value == "P1" else 1 if value == "P2" else 0

    def _focus_score(case: dict[str, Any]) -> int:
        text = " ".join(
            [
                str(case.get("description") or ""),
                str(case.get("expected_result") or ""),
                str(case.get("test_input") or ""),
                " ".join([str(x) for x in case.get("steps", [])]) if isinstance(case.get("steps"), list) else "",
            ]
        ).lower()
        score = 0
        if any(k in text for k in ["边界", "最大", "最小", "临界", "boundary", "max", "min"]):
            score += 2
        if any(k in text for k in ["异常", "失败", "错误", "拒绝", "exception", "error", "invalid"]):
            score += 2
        if any(k in text for k in ["状态", "流转", "state", "transition"]):
            score += 1
        return score

    parsed_result = clean_and_parse_json_fn(full_content)
    parsed_result = normalize_json_structure_fn(parsed_result)
    if not isinstance(parsed_result, list):
        parsed_result = []
    parsed_result = deduplicate_test_cases_fn(parsed_result)

    stage_counts = {
        "primary": len(parsed_result),
        "gap": 0,
        "review": 0,
    }

    current_total = _merged_unique_total(parsed_result)
    if current_total == 0 and int(expected_count or 0) > 0:
        yield "@@STATUS@@:初次流式结果为空，尝试一次非流式补救...\n"
        rescue_prompt = f"""
{base_prompt}

RESCUE INSTRUCTION:
- Return at least {max(1, int(expected_count))} test cases.
- Return ONLY strict JSON array.
"""
        try:
            rescue_raw = client.generate_response(requirement, rescue_prompt, db=db, task_type="generation")
            rescue_parsed = clean_and_parse_json_fn(str(rescue_raw or ""))
            rescue_parsed = normalize_json_structure_fn(rescue_parsed)
            if isinstance(rescue_parsed, list) and rescue_parsed:
                parsed_result = deduplicate_test_cases_fn(rescue_parsed)
                stage_counts["primary"] = len(parsed_result)
                current_total = _merged_unique_total(parsed_result)
                yield f"@@STATUS@@:补救成功，恢复 {len(parsed_result)} 条用例。\n"
        except Exception as rescue_err:
            yield f"@@STATUS@@:补救失败({str(rescue_err)})，继续后续流程。\n"

    normalized_mode = str(generation_mode or "").strip().lower()
    if normalized_mode not in {"single_pass", "multi_pass", "biz_key_multi_pass"}:
        normalized_mode = "multi_pass" if bool(multi_pass) else "single_pass"

    if normalized_mode in {"multi_pass", "biz_key_multi_pass"} and isinstance(parsed_result, list):
        coverage_primary = analyze_coverage(requirement, [x for x in parsed_result if isinstance(x, dict)])
        missing_rules = list(coverage_primary.get("missing_rules") or [])
        diagnostics = [item for item in (coverage_primary.get("rule_diagnostics") or []) if isinstance(item, dict)]
        has_missing_types = any(bool(item.get("missing_types")) for item in diagnostics)

        need_gap = current_total < int(expected_count or 0) or bool(missing_rules) or has_missing_types
        if need_gap:
            yield "@@STATUS@@:[multi-pass] 阶段2/3 缺口补齐开始...\n"
            before_gap = len(parsed_result)
            missing = max(0, int(expected_count or 0) - current_total)
            supplement_attempt = 0

            while supplement_attempt < 3 and (missing > 0 or missing_rules):
                supplement_attempt += 1
                yield f"@@STATUS@@:缺口补齐第 {supplement_attempt} 次...\n"

                supplement_source: list[dict[str, Any]] = []
                if append and isinstance(existing_cases, list):
                    supplement_source.extend([x for x in existing_cases if isinstance(x, dict)])
                supplement_source.extend([x for x in parsed_result if isinstance(x, dict)])

                closed_loop_instruction = build_supplement_closed_loop_instruction_fn(
                    all_cases=supplement_source,
                    requirement=requirement,
                    infer_case_kind_fn=infer_case_kind_fn,
                )
                gap_prompt = build_gap_fill_prompt(
                    requirement_context=requirement,
                    existing_cases=supplement_source,
                    coverage_result=coverage_primary,
                    missing_rules=missing_rules,
                    current_biz_key=current_biz_key,
                    pretty_json=False,
                )
                system_prompt = f"""
{gap_prompt}

CLOSED_LOOP_HINT:
{closed_loop_instruction}

TARGET_APPEND_COUNT: {max(1, missing) if missing > 0 else 1}
"""

                extra_content = ""
                extra_stream = client.generate_response_stream(requirement, system_prompt, task_type="generation")
                provider_error = None
                for chunk in extra_stream:
                    extra_content += chunk
                    yield chunk
                    if chunk.startswith("Error:") or chunk.startswith("[额度耗尽]") or chunk.startswith("Exception occurred:"):
                        provider_error = chunk
                        break
                if provider_error:
                    yield "\n@@STATUS@@:生成失败\n"
                    yield f"{provider_error}\n"
                    break

                try:
                    extra_parsed = clean_and_parse_json_fn(extra_content)
                    extra_parsed = normalize_json_structure_fn(extra_parsed)
                    if isinstance(extra_parsed, list) and extra_parsed:
                        parsed_result.extend([x for x in extra_parsed if isinstance(x, dict)])
                        parsed_result = normalize_json_structure_fn(parsed_result)
                        parsed_result = deduplicate_test_cases_fn(parsed_result)
                except Exception:
                    pass

                current_total = _merged_unique_total(parsed_result)
                missing = max(0, int(expected_count or 0) - current_total)
                coverage_primary = analyze_coverage(requirement, parsed_result)
                missing_rules = list(coverage_primary.get("missing_rules") or [])
                diagnostics = [item for item in (coverage_primary.get("rule_diagnostics") or []) if isinstance(item, dict)]
                has_missing_types = any(bool(item.get("missing_types")) for item in diagnostics)
                if missing <= 0 and not missing_rules and not has_missing_types:
                    break

            stage_counts["gap"] = max(0, len(parsed_result) - before_gap)

        yield "@@STATUS@@:[multi-pass] 阶段3/3 审查筛选开始...\n"
        target_count = max(1, int(expected_count or 1))
        if append and isinstance(existing_cases, list):
            target_count = max(1, int(expected_count or 1) - int(existing_unique_count or 0))

        candidate_cases = [x for x in parsed_result if isinstance(x, dict)]
        review_prompt = build_review_select_prompt(
            requirement_context=requirement,
            candidate_cases=candidate_cases,
            target_count=target_count,
            current_biz_key=current_biz_key,
            pretty_json=False,
        )
        try:
            review_response = client.generate_response(review_prompt, "You are a QA Auditor.", db=db, task_type="generation")
            reviewed_cases = clean_and_parse_json_fn(str(review_response or ""))
            reviewed_cases = normalize_json_structure_fn(reviewed_cases)
            if isinstance(reviewed_cases, list) and reviewed_cases:
                selected = _pick_subset(candidate_cases, reviewed_cases, target_count)
                if selected:
                    parsed_result = selected
        except Exception:
            pass

        if len(parsed_result) > target_count:
            scored = sorted(
                [x for x in parsed_result if isinstance(x, dict)],
                key=lambda item: (-_priority_score(item), -_focus_score(item), _signature(item)),
            )
            parsed_result = scored[:target_count]
        stage_counts["review"] = len(parsed_result)
    else:
        stage_counts["review"] = len(parsed_result)

    parsed_result = normalize_json_structure_fn(parsed_result)
    parsed_result = deduplicate_test_cases_fn(parsed_result)
    parsed_result = reorder_cases_by_closed_loop_fn(parsed_result, start_id=start_id, renumber_ids=True)

    coverage = {
        "kind": "coverage_check",
        **analyze_coverage(requirement, [x for x in parsed_result if isinstance(x, dict)]),
    }
    return {
        "cases": parsed_result,
        "stage_counts": stage_counts,
        "coverage": coverage,
    }
