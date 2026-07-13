from __future__ import annotations

import json
from typing import Any, Callable

from ..coverage.coverage_analyzer import analyze_coverage
from ..postprocess.result_postprocess import (
    apply_priority_semantics_to_case,
)


_VALID_GENERATION_MODES = {"single_pass", "multi_pass", "biz_key_multi_pass"}
_MAX_ROUNDS = 6
_MAX_EXISTING_CASES_IN_PROMPT = 80

from .multi_pass_pipeline_split_helpers import (
    _resolve_generation_mode,
    _to_case_list,
    _case_signature,
    _priority_weight,
    _focus_weight,
    _coverage_bucket,
    _is_high_signal,
    _attach_priority_debug,
    _filter_new_cases,
    _missing_types_count,
    _coverage_satisfied,
    _coverage_gap_summary,
    _dump_existing_cases,
    _compute_information_gain,
    _build_primary_prompt,
)

def _run_quality_coverage_rounds(
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
    accumulated_cases: list[dict[str, Any]] = []
    primary_cases: list[dict[str, Any]] = []
    raw_payload: dict[str, Any] = {}
    round_evaluations: list[dict[str, Any]] = []

    consecutive_low_new_rounds = 0

    for round_index in range(1, _MAX_ROUNDS + 1):
        before_cases = list(accumulated_cases)
        coverage_before = analyze_coverage(requirement_context, before_cases)

        primary_prompt = _build_primary_prompt(
            base_prompt=base_prompt,
            round_index=round_index,
            expected_count=expected_count,
            current_biz_key=current_biz_key,
            accumulated_cases=before_cases,
            coverage_before=coverage_before,
        )
        primary_raw = client.generate_response(requirement, primary_prompt, db=db, task_type="generation")
        raw_payload[f"round_{round_index}"] = str(primary_raw or "")[:1200]

        generated_cases = _to_case_list(
            primary_raw,
            clean_and_parse_json_fn=clean_and_parse_json_fn,
            normalize_json_structure_fn=normalize_json_structure_fn,
            deduplicate_test_cases_fn=deduplicate_test_cases_fn,
        )
        primary_cases.extend(generated_cases)

        new_cases = _filter_new_cases(before_cases, generated_cases)
        if new_cases:
            accumulated_cases = deduplicate_test_cases_fn(before_cases + new_cases)

        generated_count = len(generated_cases)
        new_valid_cases_count = len(new_cases)
        duplication_rate = 0.0
        if generated_count > 0:
            duplication_rate = float(generated_count - new_valid_cases_count) / float(generated_count)

        coverage_after = analyze_coverage(requirement_context, accumulated_cases)
        information_gain, gain_detail = _compute_information_gain(
            coverage_before=coverage_before,
            coverage_after=coverage_after,
            before_cases=before_cases,
            new_cases=new_cases,
        )
        coverage_ok = _coverage_satisfied(coverage_after)

        if new_valid_cases_count < 2:
            consecutive_low_new_rounds += 1
        else:
            consecutive_low_new_rounds = 0

        stop_reasons: list[str] = []
        if consecutive_low_new_rounds >= 2:
            stop_reasons.append("consecutive_low_new_valid_cases")
        if duplication_rate > 0.5:
            stop_reasons.append("duplication_rate_gt_50pct")
        if coverage_ok:
            stop_reasons.append("coverage_satisfied")
        if not information_gain:
            stop_reasons.append("no_information_gain")

        stop = bool(stop_reasons)

        round_evaluations.append(
            {
                "round": int(round_index),
                "generated_count": int(generated_count),
                "new_valid_cases_count": int(new_valid_cases_count),
                "duplication_rate": float(duplication_rate),
                "information_gain": bool(information_gain),
                "information_gain_detail": gain_detail,
                "coverage_satisfied": bool(coverage_ok),
                "total_cases_after_round": int(len(accumulated_cases)),
                "stop": bool(stop),
                "stop_reasons": stop_reasons,
            }
        )

        if stop:
            break

    final_cases = deduplicate_test_cases_fn(accumulated_cases)
    stage_counts = {
        "primary": int(len(final_cases)),
        "gap": 0,
        "review": int(len(final_cases)),
    }
    coverage_final = analyze_coverage(requirement_context, final_cases)

    return {
        "primary_cases": deduplicate_test_cases_fn([item for item in primary_cases if isinstance(item, dict)]),
        "gap_cases": [],
        "final_cases": final_cases,
        "coverage": coverage_final,
        "stage_counts": stage_counts,
        "raw": raw_payload,
        "round_evaluations": round_evaluations,
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


def _append_round_stage_logs(
    *,
    stage_logs: list[dict[str, Any]],
    round_evaluation: dict[str, Any],
    kind: str,
    biz_key: str | None = None,
) -> None:
    common: dict[str, Any] = {"kind": kind, "round": int(round_evaluation.get("round") or 0)}
    if biz_key is not None:
        common["biz_key"] = biz_key

    stage_logs.append(
        {
            **common,
            "stage": "primary_generation",
            "case_count": int(round_evaluation.get("generated_count") or 0),
        }
    )
    stage_logs.append(
        {
            **common,
            "stage": "evaluate_quality",
            "case_count": int(round_evaluation.get("new_valid_cases_count") or 0),
            "duplication_rate": float(round_evaluation.get("duplication_rate") or 0.0),
        }
    )
    stage_logs.append(
        {
            **common,
            "stage": "evaluate_coverage",
            "case_count": int(round_evaluation.get("total_cases_after_round") or 0),
            "coverage_satisfied": bool(round_evaluation.get("coverage_satisfied")),
            "information_gain": bool(round_evaluation.get("information_gain")),
        }
    )
    stage_logs.append(
        {
            **common,
            "stage": "decide_continue_or_stop",
            "case_count": int(round_evaluation.get("total_cases_after_round") or 0),
            "stop": bool(round_evaluation.get("stop")),
            "stop_reasons": list(round_evaluation.get("stop_reasons") or []),
        }
    )
    if not bool(round_evaluation.get("stop")):
        stage_logs.append(
            {
                **common,
                "stage": "optional_next_round",
                "case_count": int(round_evaluation.get("total_cases_after_round") or 0),
            }
        )


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
    build_base_prompt_fn: Callable[[str, str, str, str, str, str], str] | None = None,
) -> dict[str, Any]:
    mode = _resolve_generation_mode(multi_pass=multi_pass, generation_mode=generation_mode)
    context = dict(prompt_context or {})
    current_biz_key = str(current_biz_key or "unknown").strip() or "unknown"

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

    if mode in {"single_pass", "multi_pass"}:
        single = _run_quality_coverage_rounds(
            client=client,
            requirement=requirement,
            db=db,
            base_prompt=base_prompt,
            requirement_context=requirement_context,
            current_biz_key=current_biz_key,
            expected_count=expected_count,
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
        final_cases = _attach_priority_debug(final_cases, coverage_context=coverage)

        for round_evaluation in single.get("round_evaluations") or []:
            _append_round_stage_logs(
                stage_logs=stage_logs,
                round_evaluation=round_evaluation,
                kind="generation_stage",
            )

        return {
            "final_cases": final_cases,
            "primary_cases": single.get("primary_cases") or [],
            "gap_cases": [],
            "review_cases": final_cases,
            "coverage": {"kind": "coverage_check", **coverage, "missing_rules_count": len(coverage.get("missing_rules") or [])},
            "stage_logs": stage_logs,
            "raw": single.get("raw") or {},
        }

    context_by_biz = context.get("context_by_biz") if isinstance(context.get("context_by_biz"), dict) else {}
    if not isinstance(context_by_biz, dict):
        context_by_biz = {}

    all_selected: list[dict[str, Any]] = []
    all_primary: list[dict[str, Any]] = []
    raw_payload: dict[str, Any] = {}

    for biz_key in biz_order:
        biz_key = str(biz_key or "unknown").strip() or "unknown"
        scoped = context_by_biz.get(biz_key) if isinstance(context_by_biz.get(biz_key), dict) else {}
        scoped_requirement = str(scoped.get("requirement_context") or requirement_context)
        scoped_requirement_semantics = str(
            scoped.get("requirement_semantics_context")
            or context.get("requirement_semantics_context")
            or "(empty)"
        )
        scoped_testcase = str(scoped.get("testcase_context") or context.get("testcase_context") or "(empty)")
        scoped_supplement = str(scoped.get("supplement_context") or context.get("supplement_context") or "(empty)")
        scoped_control = str(scoped.get("control_context") or context.get("control_context") or "")
        scoped_prompt = (
            build_base_prompt_fn(
                scoped_requirement,
                scoped_requirement_semantics,
                scoped_testcase,
                scoped_supplement,
                scoped_control,
                biz_key,
            )
            if callable(build_base_prompt_fn)
            else base_prompt
        )

        round_result = _run_quality_coverage_rounds(
            client=client,
            requirement=requirement,
            db=db,
            base_prompt=scoped_prompt,
            requirement_context=scoped_requirement,
            current_biz_key=biz_key,
            expected_count=expected_count,
            clean_and_parse_json_fn=clean_and_parse_json_fn,
            normalize_json_structure_fn=normalize_json_structure_fn,
            deduplicate_test_cases_fn=deduplicate_test_cases_fn,
        )
        all_primary.extend(round_result.get("primary_cases") or [])
        scoped_final_cases = reorder_cases_by_closed_loop_fn(
            deduplicate_test_cases_fn(round_result.get("final_cases") or []),
            start_id=1,
            renumber_ids=False,
        )
        all_selected.extend(scoped_final_cases)
        raw_payload[biz_key] = round_result.get("raw") or {}

        for round_evaluation in round_result.get("round_evaluations") or []:
            _append_round_stage_logs(
                stage_logs=stage_logs,
                round_evaluation=round_evaluation,
                kind="biz_key_pass_stage",
                biz_key=biz_key,
            )

    merged = deduplicate_test_cases_fn(all_selected)
    final_cases = reorder_cases_by_closed_loop_fn(
        deduplicate_test_cases_fn(merged),
        start_id=start_id,
        renumber_ids=True,
    )
    coverage_requirement = str(context.get("requirement_context") or requirement_context)
    coverage = analyze_coverage(coverage_requirement, final_cases)
    final_cases = _attach_priority_debug(final_cases, coverage_context=coverage)

    return {
        "final_cases": final_cases,
        "primary_cases": deduplicate_test_cases_fn(all_primary),
        "gap_cases": [],
        "review_cases": final_cases,
        "coverage": {"kind": "coverage_check", **coverage, "missing_rules_count": len(coverage.get("missing_rules") or [])},
        "stage_logs": stage_logs,
        "raw": raw_payload,
    }
