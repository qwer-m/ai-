from __future__ import annotations

import json
import re
from typing import Any, Callable

from .priority_anchor_rules import (
    apply_priority_override,
    has_explicit_blocking_or_critical,
)
from .case_access import (
    case_id as case_access_id,
    case_priority,
    case_text_field,
)
from .case_contract import project_persistable_cases, summarize_persistable_case_contract
from .result_postprocess_priority_semantics import (
    apply_priority_semantics_to_case,
    apply_priority_semantics_to_cases,
    resolve_case_priority_decision,
    resolve_case_priority,
    score_case_priority,
)
from .streaming_execution_plan_ordering import (
    apply_existing_execution_group_ordering,
    assign_presentation_order,
)

_PRESERVED_PRIORITY_DECISION_SOURCES = {
    "entry_path_availability_p0",
    "execution_plan_declared_critical_p0",
    "execution_plan_final_priority",
    "execution_plan_main_support_step_demoted",
    "execution_plan_non_main_p0_demoted",
    "model_p0_guard_downgrade",
    "pure_ui_non_blocking_p2",
    "review_model_p0_demotion_preserved",
}


def _public_priority_signature(case: dict[str, Any]) -> str:
    return "|".join(
        [
            case_text_field(case, "test_module"),
            case_text_field(case, "description"),
            case_text_field(case, "expected_result"),
            case_text_field(case, "test_input"),
        ]
    )


def retain_structured_case_candidates(result: Any) -> Any:
    """Review 前仅保留对象候选，不依据正文词表或模型自评字段删除。"""
    if not isinstance(result, list):
        return result
    return [item for item in result if isinstance(item, dict)]


def normalize_final_case_priorities(result: Any, *, requirement_text: str = "") -> Any:
    """Re-apply priority semantics to public final cases before persistence."""
    if not isinstance(result, list):
        return result
    cases = [dict(item) for item in result if isinstance(item, dict)]
    if not cases:
        return []

    forced_priority_by_signature: dict[str, tuple[str, str]] = {}
    for item in cases:
        source = str(item.get("priority_decision_source") or "").strip()
        final_priority = case_priority(item, prefer_final=True)
        is_execution_main_smoke = str(item.get("execution_group") or "").strip() == "main_smoke"
        if final_priority not in {"P0", "P1", "P2"}:
            continue
        explicit_risk = has_explicit_blocking_or_critical(item)
        preserve_source = ""
        if final_priority == "P0" and explicit_risk:
            preserve_source = source or "preserved_structured_critical_priority"
        elif final_priority == "P0":
            preserve_source = ""
        elif is_execution_main_smoke:
            preserve_source = source or "preserved_execution_plan_priority"
        elif source in _PRESERVED_PRIORITY_DECISION_SOURCES:
            preserve_source = source
        elif explicit_risk:
            preserve_source = source or "preserved_structured_critical_priority"
        if not preserve_source:
            continue
        signature = _public_priority_signature(item)
        if signature:
            forced_priority_by_signature[signature] = (final_priority, preserve_source)
    from ..coverage.coverage_analyzer import analyze_coverage

    coverage_context = analyze_coverage(str(requirement_text or ""), cases)
    normalized = apply_priority_semantics_to_cases(
        cases,
        attach_debug=False,
        coverage_context=coverage_context,
        rule_diagnostics={"rule_diagnostics": coverage_context.get("rule_diagnostics") or []},
    )
    restored: list[dict[str, Any]] = []
    for item in normalized:
        if not isinstance(item, dict):
            continue
        updated = dict(item)
        signature = _public_priority_signature(updated)
        forced_priority = forced_priority_by_signature.get(signature) if forced_priority_by_signature else None
        if forced_priority and forced_priority[0] in {"P0", "P1", "P2"}:
            apply_priority_override(
                updated,
                priority=forced_priority[0],
                source=forced_priority[1],
            )
        restored.append(updated)
    return restored


def strip_case_meta_fields(result: Any) -> Any:
    """Remove debug/meta payload from case outputs."""
    if not isinstance(result, list):
        return result
    debug_fields = {
        "meta",
        "displayPriority",
        "rawPriority",
        "finalPriority",
        "model_priority_current",
        "model_priority",
        "legacy_priority",
        "priority_decision_state",
        "priority_decision_source",
        "priority_confidence",
        "priority_conflict_reason",
        "priority_resolution_reason",
        "priority_score",
        "suggested_priority",
        "priority_reasons",
    }
    cleaned: list[dict[str, Any]] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        case = dict(item)
        final_priority = str(case.get("priority_final") or "").strip().upper()
        if final_priority in {"P0", "P1", "P2"}:
            case["priority"] = final_priority
            case["priority_final"] = final_priority
        for field in debug_fields:
            case.pop(field, None)
        cleaned.append(case)
    return cleaned


_CASE_ID_PATTERN = re.compile(r"^TC-(\d+)$", re.IGNORECASE)


def _append_case_id_number(case: dict[str, Any]) -> int:
    match = _CASE_ID_PATTERN.fullmatch(case_access_id(case).strip())
    return int(match.group(1)) if match else 0


def _next_append_start_id(cases: list[dict[str, Any]], *, fallback_count: int = 0) -> int:
    max_existing_number = max((_append_case_id_number(item) for item in cases), default=0)
    return max(int(fallback_count or 0), max_existing_number) + 1


def _append_public_case(case: dict[str, Any]) -> dict[str, Any]:
    projected = project_persistable_cases([case])
    return dict(projected[0]) if projected else {}


def _append_exact_public_key(case: dict[str, Any]) -> str:
    public_case = _append_public_case(case)
    if not public_case:
        return ""
    public_case.pop("id", None)
    return json.dumps(public_case, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _valid_new_append_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for item in retain_structured_case_candidates(cases):
        if not isinstance(item, dict):
            continue
        public_case = _append_public_case(item)
        if not public_case:
            continue
        if not summarize_persistable_case_contract([public_case]).get("passed"):
            continue
        valid.append(dict(item))
    return valid


def _assign_non_conflicting_append_ids(
    cases: list[dict[str, Any]],
    *,
    existing_cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    used_ids = {
        case_access_id(item).strip().casefold()
        for item in existing_cases
        if case_access_id(item).strip()
    }
    next_number = _next_append_start_id(existing_cases, fallback_count=len(existing_cases))
    output: list[dict[str, Any]] = []
    for item in cases:
        updated = dict(item)
        current_id = case_access_id(updated).strip()
        if current_id and current_id.casefold() not in used_ids:
            used_ids.add(current_id.casefold())
            output.append(updated)
            continue
        while f"tc-{next_number:03d}" in used_ids:
            next_number += 1
        updated["id"] = f"TC-{next_number:03d}"
        used_ids.add(updated["id"].casefold())
        next_number += 1
        output.append(updated)
    return output


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
            # 追加模式的历史用例是不可变基线：不在读取阶段去重、重排或重编号。
            existing_cases = [dict(item) for item in parsed if isinstance(item, dict)]
            existing_unique_count = count_unique_test_cases_fn(existing_cases)
            start_id = _next_append_start_id(
                existing_cases,
                fallback_count=existing_unique_count,
            )
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
        result = retain_structured_case_candidates(result)
        result = deduplicate_test_cases_fn(result)
        result = reorder_cases_by_closed_loop_fn(
            result,
            start_id=start_id,
            renumber_ids=True,
        )
        result = assign_presentation_order(
            result,
            presentation_ordered_cases=result,
        )
        result = apply_existing_execution_group_ordering(
            result,
            start_id=start_id,
            renumber_ids=True,
        )
        result = strip_case_meta_fields(result)
    return result


def merge_cases_for_append(
    existing_cases: list[dict[str, Any]],
    new_cases: Any,
    *,
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> Any:
    """保护历史基线，仅校验和精确去重新增用例后追加。"""
    if not isinstance(new_cases, list):
        return new_cases

    baseline = [dict(item) for item in (existing_cases or []) if isinstance(item, dict)]
    valid_new_cases = _valid_new_append_cases(
        [dict(item) for item in new_cases if isinstance(item, dict)]
    )
    exact_new_cases = [
        dict(item)
        for item in deduplicate_test_cases_fn(valid_new_cases)
        if isinstance(item, dict)
    ]

    seen_public_keys = {
        key
        for key in (_append_exact_public_key(item) for item in baseline)
        if key
    }
    unique_new_cases: list[dict[str, Any]] = []
    for item in exact_new_cases:
        key = _append_exact_public_key(item)
        if not key or key in seen_public_keys:
            continue
        seen_public_keys.add(key)
        unique_new_cases.append(item)

    unique_new_cases = _assign_non_conflicting_append_ids(
        unique_new_cases,
        existing_cases=baseline,
    )
    # 公开用例顺序不承载执行含义；主链与依赖由 execution suite 单独表达。
    return [*baseline, *unique_new_cases]

from .result_postprocess_streaming import stream_postprocess_cases

