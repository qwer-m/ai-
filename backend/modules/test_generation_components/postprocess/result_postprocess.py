from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterator

from .result_postprocess_priority_semantics import (
    apply_priority_semantics_to_case,
    apply_priority_semantics_to_cases,
    resolve_case_priority_decision,
    resolve_case_priority,
    score_case_priority,
)

_REASONING_LEAKAGE_SIGNALS = (
    "可能",
    "似乎",
    "不合理",
    "再读需求",
    "我们按照",
    "假设此处",
    "需求说",
    "按需求原文",
    "怎么会有",
    "此处假设",
    "暂且认为",
    "assume here",
    "assuming here",
    "maybe",
    "seems",
    "reread requirement",
)


def _case_has_reasoning_leakage(case: dict[str, Any]) -> bool:
    parts: list[str] = []
    for field in ("preconditions", "steps"):
        value = case.get(field)
        if isinstance(value, list):
            parts.extend(str(item) for item in value if str(item).strip())
        elif value is not None:
            parts.append(str(value))
    parts.append(str(case.get("expected_result") or ""))
    text = "\n".join(parts).lower()
    return any(str(signal).lower() in text for signal in _REASONING_LEAKAGE_SIGNALS)


def filter_invalid_final_cases(result: Any) -> Any:
    """Remove invalid cases that must never enter final persistence."""
    if not isinstance(result, list):
        return result
    filtered: list[dict[str, Any]] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        quality = str(item.get("case_quality") or item.get("expected_result_quality") or "").strip().lower()
        if quality == "invalid_case" or _case_has_reasoning_leakage(item):
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
    requirement_text_lower = str(requirement_text or "").lower()
    essay_domain_active = any(
        token in requirement_text_lower
        for token in ("\u4f5c\u6587", "\u6295\u7a3f", "\u4f5c\u6587\u5708", "\u5199\u4f5c")
    ) or (
        any(token in requirement_text_lower for token in ("\u6279\u6539", "ocr", "\u53bb\u6279\u6539"))
        and not any(token in requirement_text_lower for token in ("\u6392\u8bfe", "\u8fd1\u671f\u8bfe\u7a0b", "\u5b66\u4e60\u8ba1\u5212", "\u672c\u5468\u8bfe\u7a0b"))
    )

    def _public_p0_main_path_anchor(case: dict[str, Any]) -> bool:
        text = " ".join(
            [
                str(case.get("test_module") or ""),
                str(case.get("description") or ""),
                str(case.get("expected_result") or ""),
                str(case.get("test_input") or ""),
                " ".join(str(step) for step in (case.get("steps") or []) if str(step).strip())
                if isinstance(case.get("steps"), list)
                else "",
            ]
        ).lower()
        critical_families = (
            ("generation_result", ("上传", "去批改", "批改结果")),
            ("result_display", ("批改反馈", "四部分")),
            ("result_display", ("综合点评", "分句点评", "提升思路", "全文润色")),
            ("submission", ("提交", "投稿成功", "审核中")),
            ("submission", ("投稿页", "提交后", "审核中")),
            ("approval", ("后台审核通过", "已发布")),
            ("approval", ("审核通过", "作文圈", "可见")),
            ("free_first_lesson", ("普通用户", "第一课", "试学")),
            ("locked_member_courses", ("普通用户", "锁", "会员中心")),
            ("locked_member_courses", ("其余课程", "锁", "会员中心")),
            ("member_all_courses", ("会员用户", "所有课程", "可学习")),
            ("member_all_courses", ("会员", "所有课程", "无锁")),
            ("delete_restore", ("删除", "已发布", "恢复为未投稿")),
            ("delete_restore", ("删除", "作文圈", "未投稿")),
        )
        if not essay_domain_active:
            critical_families = tuple(
                item
                for item in critical_families
                if item[0] in {"free_first_lesson", "locked_member_courses", "member_all_courses"}
            )
        has_critical_anchor = any(
            all(token.lower() in text for token in tokens)
            for _family, tokens in critical_families
        )
        core_tokens = (
            "upload",
            "submit",
            "publish",
            "generate",
            "generated",
            "result",
            "approval",
            "approved",
            "review pass",
            "permission",
            "member",
            "locked",
            "paywall",
            "first lesson",
            "all courses",
            "\u4e0a\u4f20",
            "\u63d0\u4ea4",
            "\u6295\u7a3f",
            "\u53d1\u5e03",
            "\u751f\u6210",
            "\u6279\u6539\u7ed3\u679c",
            "\u5ba1\u6838\u901a\u8fc7",
            "\u6743\u9650",
            "\u4f1a\u5458",
            "\u9501\u5b9a",
            "\u7b2c\u4e00\u8bfe",
            "\u5168\u90e8\u8bfe\u7a0b",
        )
        low_value_tokens = (
            "copy",
            "toast",
            "popup",
            "modal",
            "format",
            "layout",
            "sort",
            "ranking",
            "download",
            "pdf",
            "0 images",
            "disabled button",
            "star rating",
            "countdown",
            "\u590d\u5236",
            "\u5f39\u7a97",
            "\u683c\u5f0f",
            "\u6837\u5f0f",
            "\u6392\u5e8f",
            "\u4e0b\u8f7d",
            "\u6700\u591a20\u6761",
            "0\u5f20",
            "\u6309\u94ae\u4e0d\u53ef\u70b9",
            "\u5269\u4f59\u6b21\u6570",
            "\u661f\u661f\u8bc4\u5206",
            "\u5012\u8ba1\u65f6",
            "\u5206\u53e5\u70b9\u8bc4",
        )
        if not essay_domain_active and any(
            token in text
            for token in ("\u4f5c\u6587", "\u6295\u7a3f", "\u4f5c\u6587\u5708", "\u53bb\u6279\u6539")
        ):
            return False
        if has_critical_anchor:
            return True
        return any(token in text for token in core_tokens) and not any(token in text for token in low_value_tokens)

    forced_priority_by_signature: dict[str, str] = {}
    for item in cases:
        source = str(item.get("priority_decision_source") or "").strip()
        final_priority = str(item.get("priority_final") or item.get("priority") or "").strip().upper()
        if source in {
            "main_path_anchor_floor",
            "main_path_anchor_demoted_non_blocking",
            "model_p0_guard_downgrade",
        } and final_priority in {"P0", "P1", "P2"}:
            signature = "|".join(
                [
                    str(item.get("test_module") or "").strip(),
                    str(item.get("description") or "").strip(),
                    str(item.get("expected_result") or "").strip(),
                    str(item.get("test_input") or "").strip(),
                ]
            )
            if signature:
                forced_priority_by_signature[signature] = final_priority
        elif final_priority == "P0" and _public_p0_main_path_anchor(item):
            signature = "|".join(
                [
                    str(item.get("test_module") or "").strip(),
                    str(item.get("description") or "").strip(),
                    str(item.get("expected_result") or "").strip(),
                    str(item.get("test_input") or "").strip(),
                ]
            )
            if signature:
                forced_priority_by_signature[signature] = "P0"
    try:
        from ..coverage.coverage_analyzer import analyze_coverage
    except Exception:
        from modules.testing.test_generation_components.coverage.coverage_analyzer import analyze_coverage

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
        signature = "|".join(
            [
                str(updated.get("test_module") or "").strip(),
                str(updated.get("description") or "").strip(),
                str(updated.get("expected_result") or "").strip(),
                str(updated.get("test_input") or "").strip(),
            ]
        )
        forced_priority = forced_priority_by_signature.get(signature) if forced_priority_by_signature else None
        if forced_priority in {"P0", "P1", "P2"}:
            updated["priority"] = forced_priority
            updated["priority_final"] = forced_priority
            updated["priority_decision_state"] = "overridden"
            updated["priority_decision_source"] = "preserved_priority_override"
        restored.append(updated)
    if len(restored) >= 80:
        target_p0_count = min(12, max(8, int((len(restored) + 9) // 10)))
        current_p0 = sum(1 for item in restored if str(item.get("priority") or "").strip().upper() == "P0")
        if current_p0 < target_p0_count:
            promoted = 0
            for item in restored:
                if current_p0 + promoted >= target_p0_count:
                    break
                if str(item.get("priority") or "").strip().upper() == "P0":
                    continue
                if not _public_p0_main_path_anchor(item):
                    continue
                item["priority"] = "P0"
                item["priority_final"] = "P0"
                item["priority_decision_state"] = "overridden"
                item["priority_decision_source"] = "public_main_path_anchor_floor"
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
        "priority_final",
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
        for field in debug_fields:
            case.pop(field, None)
        cleaned.append(case)
    return cleaned

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
    merged_result = reorder_cases_by_closed_loop_fn(
        merged_result,
        start_id=1,
        renumber_ids=True,
    )
    merged_result = strip_case_meta_fields(merged_result)
    return merged_result

from .result_postprocess_streaming import stream_postprocess_cases

