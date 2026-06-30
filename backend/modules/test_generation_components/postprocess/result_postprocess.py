from __future__ import annotations

import json
import re
from typing import Any, Callable

from .postprocess_priority_config import (
    invalid_case_quality_markers,
    quality_check_fields,
    reasoning_leakage_signals,
)
from .priority_anchor_rules import apply_priority_override, p0_cross_domain_essay_case, p0_main_path_anchor
from .case_access import case_flat_text, case_priority, case_text_field, case_text_parts
from .result_postprocess_priority_semantics import (
    apply_priority_semantics_to_case,
    apply_priority_semantics_to_cases,
    resolve_case_priority_decision,
    resolve_case_priority,
    score_case_priority,
)
from .streaming_execution_plan_ordering import apply_existing_execution_group_ordering

_REASONING_LEAKAGE_SIGNALS = reasoning_leakage_signals()


def _case_has_reasoning_leakage(case: dict[str, Any]) -> bool:
    parts = case_text_parts(case, ("description", "preconditions", "steps", "test_input", "expected_result"))
    text = "\n".join(parts).lower()
    return any(str(signal).lower() in text for signal in _REASONING_LEAKAGE_SIGNALS)


def _public_priority_signature(case: dict[str, Any]) -> str:
    return "|".join(
        [
            case_text_field(case, "test_module"),
            case_text_field(case, "description"),
            case_text_field(case, "expected_result"),
            case_text_field(case, "test_input"),
        ]
    )


def filter_invalid_final_cases(result: Any) -> Any:
    """Remove invalid cases that must never enter final persistence."""
    if not isinstance(result, list):
        return result
    filtered: list[dict[str, Any]] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        quality = str(item.get(quality_check_fields()[0]) or item.get(quality_check_fields()[1]) or "").strip().lower()
        if quality in invalid_case_quality_markers() or _case_has_reasoning_leakage(item):
            continue
        filtered.append(item)
    return filtered


def normalize_final_case_priorities(result: Any, *, requirement_text: str = "") -> Any:
    """Re-apply priority semantics to public final cases before persistence."""
    if not isinstance(result, list):
        return result
    cases = [dict(item) for item in result if isinstance(item, dict)]
    if not cases:
        return []
    def _is_cross_domain_essay_case(case: dict[str, Any]) -> bool:
        return p0_cross_domain_essay_case(case, requirement_text=str(requirement_text or ""))

    def _public_p0_main_path_anchor(case: dict[str, Any]) -> bool:
        return p0_main_path_anchor(case, requirement_text=str(requirement_text or ""))

    forced_priority_by_signature: dict[str, str] = {}
    for item in cases:
        source = str(item.get("priority_decision_source") or "").strip()
        final_priority = case_priority(item, prefer_final=True)
        is_execution_main_smoke = str(item.get("execution_group") or "").strip() == "main_smoke"
        if is_execution_main_smoke and final_priority in {"P0", "P1", "P2"}:
            if final_priority == "P0" and _is_cross_domain_essay_case(item):
                continue
            signature = _public_priority_signature(item)
            if signature:
                forced_priority_by_signature[signature] = final_priority
        elif source in {
            "main_path_anchor_floor",
            "main_path_anchor_demoted_non_blocking",
            "model_p0_guard_downgrade",
            "execution_plan_final_priority",
            "execution_plan_main_support_step_demoted",
        } and final_priority in {"P0", "P1", "P2"}:
            if final_priority == "P0" and _is_cross_domain_essay_case(item):
                continue
            signature = _public_priority_signature(item)
            if signature:
                forced_priority_by_signature[signature] = final_priority
        elif final_priority == "P0" and _public_p0_main_path_anchor(item):
            signature = _public_priority_signature(item)
            if signature:
                forced_priority_by_signature[signature] = "P0"
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
        if _is_cross_domain_essay_case(updated) and case_priority(updated) == "P0":
            apply_priority_override(
                updated,
                priority="P1",
                source="domain_mismatch_p0_demoted",
            )
            restored.append(updated)
            continue
        if forced_priority in {"P0", "P1", "P2"}:
            if forced_priority == "P0" and _is_cross_domain_essay_case(updated):
                forced_priority = "P1"
            apply_priority_override(
                updated,
                priority=forced_priority,
                source=(
                    "preserved_execution_plan_priority"
                    if str(updated.get("execution_group") or "").strip() == "main_smoke"
                    else "preserved_priority_override"
                ),
            )
        restored.append(updated)
    if len(restored) >= 80:
        target_p0_count = min(12, max(8, int((len(restored) + 9) // 10)))
        current_p0 = sum(1 for item in restored if case_priority(item) == "P0")
        if current_p0 < target_p0_count:
            promoted = 0
            for item in restored:
                if current_p0 + promoted >= target_p0_count:
                    break
                if case_priority(item) == "P0":
                    continue
                if not _public_p0_main_path_anchor(item):
                    continue
                apply_priority_override(
                    item,
                    priority="P0",
                    source="public_main_path_anchor_floor",
                )
                promoted += 1
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


def _semantic_merge_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def _semantic_merge_tokens(case: dict[str, Any]) -> set[str]:
    text = case_flat_text(
        case,
        fields=("test_module", "description", "test_input", "expected_result", "steps"),
        separator=" ",
    )
    normalized = _semantic_merge_text(text)
    tokens = set(re.findall(r"[a-z0-9_]{2,}", normalized))
    chinese_text = "".join(re.findall(r"[\u4e00-\u9fff]+", normalized))
    for size in (2, 3):
        for index in range(0, max(0, len(chinese_text) - size + 1)):
            tokens.add(chinese_text[index : index + size])
    return tokens


def _semantic_merge_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_tokens = _semantic_merge_tokens(left)
    right_tokens = _semantic_merge_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))


def _is_append_semantic_duplicate(candidate: dict[str, Any], kept: dict[str, Any]) -> bool:
    left_module = _semantic_merge_text(case_text_field(candidate, "test_module"))
    right_module = _semantic_merge_text(case_text_field(kept, "test_module"))
    if left_module and right_module and left_module != right_module:
        return False
    left_desc = _semantic_merge_text(case_text_field(candidate, "description"))
    right_desc = _semantic_merge_text(case_text_field(kept, "description"))
    if left_desc and right_desc and (left_desc == right_desc or left_desc in right_desc or right_desc in left_desc):
        return True
    return _semantic_merge_similarity(candidate, kept) >= 0.58


def _semantic_merge_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for item in cases:
        if not isinstance(item, dict):
            continue
        if any(_is_append_semantic_duplicate(item, existed) for existed in kept):
            continue
        kept.append(item)
    return kept


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
        result = filter_invalid_final_cases(result)
        result = deduplicate_test_cases_fn(result)
        result = reorder_cases_by_closed_loop_fn(
            result,
            start_id=start_id,
            renumber_ids=True,
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
    reorder_cases_by_closed_loop_fn: Callable[..., list[dict[str, Any]]],
) -> Any:
    """Merge append-mode historical cases with new cases before persistence."""
    if not isinstance(new_cases, list):
        return new_cases

    merged_result: list[dict[str, Any]] = []
    if isinstance(existing_cases, list):
        merged_result.extend(existing_cases)
    merged_result.extend(new_cases)
    merged_result = filter_invalid_final_cases(merged_result)
    merged_result = deduplicate_test_cases_fn(merged_result)
    merged_result = _semantic_merge_cases(merged_result)
    merged_result = reorder_cases_by_closed_loop_fn(
        merged_result,
        start_id=1,
        renumber_ids=True,
    )
    merged_result = apply_existing_execution_group_ordering(
        merged_result,
        start_id=1,
        renumber_ids=True,
    )
    merged_result = strip_case_meta_fields(merged_result)
    return merged_result

from .result_postprocess_streaming import stream_postprocess_cases

