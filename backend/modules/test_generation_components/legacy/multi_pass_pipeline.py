from __future__ import annotations

from typing import Any, Callable

from modules.testing.test_generation_components.coverage.coverage_analyzer import analyze_coverage
from modules.testing.test_generation_components.prompting.prompt_orchestration import (
    build_gap_fill_prompt,
    build_review_select_prompt,
)


_VALID_GENERATION_MODES = {"single_pass", "multi_pass", "biz_key_multi_pass"}


def _resolve_generation_mode(*, multi_pass: bool, generation_mode: str) -> str:
    normalized = str(generation_mode or "").strip().lower()
    if normalized in _VALID_GENERATION_MODES:
        return normalized
    return "multi_pass" if bool(multi_pass) else "single_pass"


def _to_case_list(
    payload: Any,
    *,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """中文注释：统一把模型输出解析为结构化 case 列表。"""
    data: Any = payload
    if isinstance(payload, str):
        data = clean_and_parse_json_fn(payload)
    data = normalize_json_structure_fn(data)
    if not isinstance(data, list):
        return []
    return [item for item in deduplicate_test_cases_fn(data) if isinstance(item, dict)]


def _case_signature(case: dict[str, Any]) -> str:
    module = str(case.get("test_module") or "").strip().lower()
    desc = str(case.get("description") or "").strip().lower()
    expected = str(case.get("expected_result") or "").strip().lower()
    test_input = str(case.get("test_input") or "").strip().lower()
    return f"{module}|{desc}|{expected}|{test_input}"


def _priority_weight(priority: str) -> int:
    value = str(priority or "").strip().upper()
    if value == "P0":
        return 3
    if value == "P1":
        return 2
    if value == "P2":
        return 1
    return 0


def _focus_weight(case: dict[str, Any]) -> int:
    text = " ".join(
        [
            str(case.get("description") or ""),
            str(case.get("expected_result") or ""),
            str(case.get("test_input") or ""),
            " ".join([str(x) for x in case.get("steps", [])]) if isinstance(case.get("steps"), list) else "",
        ]
    ).lower()
    score = 0
    if any(keyword in text for keyword in ("边界", "最大", "最小", "临界", "boundary", "max", "min")):
        score += 2
    if any(keyword in text for keyword in ("异常", "失败", "错误", "拒绝", "exception", "error", "invalid")):
        score += 2
    if any(keyword in text for keyword in ("状态", "流转", "state", "transition")):
        score += 1
    return score


def _filter_new_cases(base_cases: list[dict[str, Any]], candidate_cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """中文注释：Gap 阶段只补新验证目标，不覆盖历史目标。"""
    seen = {_case_signature(case) for case in base_cases if isinstance(case, dict)}
    output: list[dict[str, Any]] = []
    for case in candidate_cases:
        if not isinstance(case, dict):
            continue
        signature = _case_signature(case)
        if signature in seen:
            continue
        seen.add(signature)
        output.append(case)
    return output


def _deterministic_select(cases: list[dict[str, Any]], target_count: int) -> list[dict[str, Any]]:
    deduped = _filter_new_cases([], [x for x in cases if isinstance(x, dict)])
    return sorted(
        deduped,
        key=lambda item: (-_priority_weight(item.get("priority") or ""), -_focus_weight(item), _case_signature(item)),
    )[: max(1, int(target_count or 1))]


def _pick_cases_from_review_output(
    *, candidate_cases: list[dict[str, Any]], reviewed_cases: list[dict[str, Any]], target_count: int
) -> list[dict[str, Any]]:
    candidate_map = {_case_signature(case): case for case in candidate_cases if isinstance(case, dict)}
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for case in reviewed_cases:
        if not isinstance(case, dict):
            continue
        signature = _case_signature(case)
        if signature in seen:
            continue
        original = candidate_map.get(signature)
        if original is None:
            continue
        selected.append(original)
        seen.add(signature)
        if len(selected) >= max(1, int(target_count or 1)):
            break
    return selected


def _should_run_gap(coverage_result: dict[str, Any], *, expected_count: int, generated_count: int) -> bool:
    if int(generated_count or 0) < int(expected_count or 0):
        return True
    if coverage_result.get("missing_rules"):
        return True
    diagnostics = [item for item in (coverage_result.get("rule_diagnostics") or []) if isinstance(item, dict)]
    return any(bool(item.get("missing_types")) for item in diagnostics)


def _run_single_round(
    *,
    client: Any,
    requirement: str,
    db: Any,
    base_prompt: str,
    requirement_context: str,
    current_biz_key: str,
    expected_count: int,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> dict[str, Any]:
    """中文注释：单个 biz_key 的 primary->gap->review 闭环。"""
    expected = max(1, int(expected_count or 1))
    stage_counts: dict[str, int] = {"primary": 0, "gap": 0, "review": 0}

    primary_prompt = f"""{base_prompt}

MULTI-PASS STAGE: PRIMARY
- 第一轮主生成。
- 目标数量约 {expected} 条。
- Return ONLY JSON array.
"""
    primary_raw = client.generate_response(requirement, primary_prompt, db=db, task_type="generation")
    primary_cases = _to_case_list(
        primary_raw,
        clean_and_parse_json_fn=clean_and_parse_json_fn,
        normalize_json_structure_fn=normalize_json_structure_fn,
        deduplicate_test_cases_fn=deduplicate_test_cases_fn,
    )
    stage_counts["primary"] = len(primary_cases)
    coverage_primary = analyze_coverage(requirement_context, primary_cases)

    gap_cases: list[dict[str, Any]] = []
    if _should_run_gap(coverage_primary, expected_count=expected, generated_count=len(primary_cases)):
        gap_prompt = build_gap_fill_prompt(
            requirement_context=requirement_context,
            existing_cases=primary_cases,
            coverage_result=coverage_primary,
            missing_rules=list(coverage_primary.get("missing_rules") or []),
            current_biz_key=current_biz_key,
            pretty_json=False,
        )
        gap_raw = client.generate_response(requirement, gap_prompt, db=db, task_type="generation")
        gap_candidates = _to_case_list(
            gap_raw,
            clean_and_parse_json_fn=clean_and_parse_json_fn,
            normalize_json_structure_fn=normalize_json_structure_fn,
            deduplicate_test_cases_fn=deduplicate_test_cases_fn,
        )
        gap_cases = _filter_new_cases(primary_cases, gap_candidates)
    stage_counts["gap"] = len(gap_cases)

    combined_cases = deduplicate_test_cases_fn(primary_cases + gap_cases)
    review_prompt = build_review_select_prompt(
        requirement_context=requirement_context,
        candidate_cases=combined_cases,
        target_count=expected,
        current_biz_key=current_biz_key,
        pretty_json=False,
    )
    review_raw = client.generate_response(requirement, review_prompt, db=db, task_type="generation")
    reviewed_cases = _to_case_list(
        review_raw,
        clean_and_parse_json_fn=clean_and_parse_json_fn,
        normalize_json_structure_fn=normalize_json_structure_fn,
        deduplicate_test_cases_fn=deduplicate_test_cases_fn,
    )
    selected_cases = _pick_cases_from_review_output(
        candidate_cases=combined_cases,
        reviewed_cases=reviewed_cases,
        target_count=expected,
    )
    if not selected_cases:
        selected_cases = _deterministic_select(combined_cases, expected)
    stage_counts["review"] = len(selected_cases)

    return {
        "primary_cases": primary_cases,
        "gap_cases": gap_cases,
        "final_cases": deduplicate_test_cases_fn(selected_cases),
        "coverage": coverage_primary,
        "stage_counts": stage_counts,
        "raw": {"primary": str(primary_raw or "")[:1200], "review": str(review_raw or "")[:1200]},
    }


def _resolve_biz_key_order(prompt_context: dict[str, Any], current_biz_key: str) -> list[str]:
    order = [str(x).strip() for x in (prompt_context.get("biz_key_order") or []) if str(x).strip()]
    if not order:
        context_by_biz = prompt_context.get("context_by_biz")
        if isinstance(context_by_biz, dict):
            order = [str(k).strip() for k in context_by_biz.keys() if str(k).strip()]
    if not order:
        order = [str(current_biz_key or "unknown")]
    current = str(current_biz_key or "unknown").strip() or "unknown"
    if current in order:
        order = [current] + [item for item in order if item != current]
    return order


def run_multi_pass_generation(
    *,
    client: Any,
    requirement: str,
    db: Any,
    base_prompt: str,
    requirement_context: str,
    current_biz_key: str,
    expected_count: int,
    start_id: int,
    clean_and_parse_json_fn: Callable[[str], Any],
    normalize_json_structure_fn: Callable[[Any], Any],
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    reorder_cases_by_closed_loop_fn: Callable[..., list[dict[str, Any]]],
    multi_pass: bool = True,
    generation_mode: str = "",
    prompt_context: dict[str, Any] | None = None,
    build_base_prompt_fn: Callable[[str, str, str, str], str] | None = None,
) -> dict[str, Any]:
    """中文注释：multi-pass 主流程，支持 biz_key_multi_pass 分轮生成。"""
    mode = _resolve_generation_mode(multi_pass=multi_pass, generation_mode=generation_mode)
    context = dict(prompt_context or {})
    current_biz_key = str(current_biz_key or "unknown").strip() or "unknown"
    expected = max(1, int(expected_count or 1))

    biz_order = _resolve_biz_key_order(context, current_biz_key)
    if mode == "biz_key_multi_pass" and len(biz_order) <= 1:
        mode = "multi_pass"

    stage_logs: list[dict[str, Any]] = [
        {
            "kind": "generation_mode",
            "mode": mode,
            "biz_keys": biz_order,
            "current_biz_key": current_biz_key,
        }
    ]
    raw_payload: dict[str, Any] = {}

    if mode == "single_pass":
        single = _run_single_round(
            client=client,
            requirement=requirement,
            db=db,
            base_prompt=base_prompt,
            requirement_context=requirement_context,
            current_biz_key=current_biz_key,
            expected_count=expected,
            clean_and_parse_json_fn=clean_and_parse_json_fn,
            normalize_json_structure_fn=normalize_json_structure_fn,
            deduplicate_test_cases_fn=deduplicate_test_cases_fn,
        )
        final_cases = reorder_cases_by_closed_loop_fn(
            deduplicate_test_cases_fn(single["final_cases"]),
            start_id=start_id,
            renumber_ids=True,
        )
        coverage = analyze_coverage(requirement_context, final_cases)
        for stage, count in single["stage_counts"].items():
            stage_logs.append({"kind": "generation_stage", "stage": stage, "case_count": int(count)})
        return {
            "final_cases": final_cases,
            "primary_cases": single["primary_cases"],
            "gap_cases": single["gap_cases"],
            "review_cases": final_cases,
            "coverage": {"kind": "coverage_check", **coverage, "missing_rules_count": len(coverage.get("missing_rules") or [])},
            "stage_logs": stage_logs,
            "raw": single["raw"],
        }

    if mode == "biz_key_multi_pass":
        context_by_biz = context.get("context_by_biz") if isinstance(context.get("context_by_biz"), dict) else {}
        if not isinstance(context_by_biz, dict):
            context_by_biz = {}
        per_biz_target = max(1, expected // max(1, len(biz_order)))
        all_selected: list[dict[str, Any]] = []
        all_primary: list[dict[str, Any]] = []
        all_gap: list[dict[str, Any]] = []

        for idx, biz_key in enumerate(biz_order):
            biz_key = str(biz_key or "unknown").strip() or "unknown"
            scoped = context_by_biz.get(biz_key) if isinstance(context_by_biz.get(biz_key), dict) else {}
            scoped_requirement = str(scoped.get("requirement_context") or requirement_context)
            scoped_testcase = str(scoped.get("testcase_context") or context.get("testcase_context") or "(empty)")
            scoped_supplement = str(scoped.get("supplement_context") or context.get("supplement_context") or "(empty)")
            scoped_prompt = (
                build_base_prompt_fn(scoped_requirement, scoped_testcase, scoped_supplement, biz_key)
                if callable(build_base_prompt_fn)
                else base_prompt
            )
            scoped_target = expected - per_biz_target * (len(biz_order) - 1) if idx == 0 else per_biz_target
            round_result = _run_single_round(
                client=client,
                requirement=requirement,
                db=db,
                base_prompt=scoped_prompt,
                requirement_context=scoped_requirement,
                current_biz_key=biz_key,
                expected_count=max(1, scoped_target),
                clean_and_parse_json_fn=clean_and_parse_json_fn,
                normalize_json_structure_fn=normalize_json_structure_fn,
                deduplicate_test_cases_fn=deduplicate_test_cases_fn,
            )
            all_primary.extend(round_result["primary_cases"])
            all_gap.extend(round_result["gap_cases"])
            all_selected.extend(round_result["final_cases"])
            raw_payload[biz_key] = round_result["raw"]
            for stage, count in round_result["stage_counts"].items():
                stage_logs.append(
                    {
                        "kind": "biz_key_pass_stage",
                        "biz_key": biz_key,
                        "stage": stage,
                        "case_count": int(count),
                    }
                )

        merged = deduplicate_test_cases_fn(all_selected)
        if len(merged) > expected:
            merged = _deterministic_select(merged, expected)
        final_cases = reorder_cases_by_closed_loop_fn(
            deduplicate_test_cases_fn(merged),
            start_id=start_id,
            renumber_ids=True,
        )
        coverage_requirement = str(context.get("requirement_context") or requirement_context)
        coverage = analyze_coverage(coverage_requirement, final_cases)
        return {
            "final_cases": final_cases,
            "primary_cases": deduplicate_test_cases_fn(all_primary),
            "gap_cases": deduplicate_test_cases_fn(all_gap),
            "review_cases": final_cases,
            "coverage": {"kind": "coverage_check", **coverage, "missing_rules_count": len(coverage.get("missing_rules") or [])},
            "stage_logs": stage_logs,
            "raw": raw_payload,
        }

    single_round = _run_single_round(
        client=client,
        requirement=requirement,
        db=db,
        base_prompt=base_prompt,
        requirement_context=requirement_context,
        current_biz_key=current_biz_key,
        expected_count=expected,
        clean_and_parse_json_fn=clean_and_parse_json_fn,
        normalize_json_structure_fn=normalize_json_structure_fn,
        deduplicate_test_cases_fn=deduplicate_test_cases_fn,
    )
    final_cases = reorder_cases_by_closed_loop_fn(
        deduplicate_test_cases_fn(single_round["final_cases"]),
        start_id=start_id,
        renumber_ids=True,
    )
    coverage = analyze_coverage(requirement_context, final_cases)
    for stage, count in single_round["stage_counts"].items():
        stage_logs.append({"kind": "generation_stage", "stage": stage, "case_count": int(count)})
    return {
        "final_cases": final_cases,
        "primary_cases": single_round["primary_cases"],
        "gap_cases": single_round["gap_cases"],
        "review_cases": final_cases,
        "coverage": {"kind": "coverage_check", **coverage, "missing_rules_count": len(coverage.get("missing_rules") or [])},
        "stage_logs": stage_logs,
        "raw": single_round["raw"],
    }
