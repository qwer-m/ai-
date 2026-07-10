from __future__ import annotations

import re
from typing import Any

from ..coverage.scenario_registry import (
    cross_module_scenario_kinds,
    iter_scenario_family_policies,
    judge_duplicate_scenario_kinds,
    judge_duplicate_thresholds,
    scenario_pattern_entries,
)
from ..coverage.coverage_case_classifier import (
    classify_case_flow_stage,
    classify_case_intent_signature,
)
from ..postprocess.case_access import case_priority, case_steps, case_text_field

from .judge_text_utils import _normalize_text


_DUPLICATE_SCENARIO_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = scenario_pattern_entries()
_SCENARIO_POLICY_BY_KEY = {
    policy.key: policy
    for policy in iter_scenario_family_policies()
}

_REGISTERED_SCENARIO_KINDS = judge_duplicate_scenario_kinds()
_REGISTERED_SCENARIO_THRESHOLDS = judge_duplicate_thresholds()

_DUPLICATE_SIMPLE_SCENARIOS = {
    "title_format",
    "initial_popup",
    "filter_toggle",
    "empty_state",
    "card_element",
    "workbook_scope",
    "bad_image_review",
    "media_preview",
    "answer_analysis_placeholder",
    "source_consistency",
    "network_error",
    "permission",
    "save_delete",
    "manual_mark_correct",
    "manual_mark_wrong",
    "review_warm_hint",
    "review_detail_content",
    "review_status_color",
    "review_filter_tabs",
    "feedback",
    "plan_step1_scope",
    "plan_second_step_navigation",
    "plan_slice_auto_advance",
    "plan_slice_regeneration",
    "plan_fourth_summary",
    "sorting_limit",
    "print_export",
    "report_trigger",
    "generation_trigger",
    "share_friend",
    "share_moments",
    "report_comment_ai",
    "report_comment_voice",
    "report_comment_edit",
    "report_overview_cards",
    "report_wrong_analysis",
    "report_next_plan",
    "readonly",
    "quota_exhaustion",
    "quota_consumption",
    "quota_limit",
    "silent_refresh",
    "history_makeup",
}
_DUPLICATE_SIMPLE_SCENARIOS.update(_REGISTERED_SCENARIO_KINDS)

_DUPLICATE_SCENARIO_THRESHOLDS: dict[str, tuple[float, int]] = judge_duplicate_thresholds()

_CROSS_MODULE_DUPLICATE_SCENARIOS = set()
_CROSS_MODULE_DUPLICATE_SCENARIOS.update(cross_module_scenario_kinds())

_BROAD_GENERAL_DUPLICATE_MIN_SCORE = 0.62
_BROAD_GENERAL_DUPLICATE_MIN_OVERLAP = 12
_DIMENSIONAL_GENERAL_SCENARIO_KINDS = {
    "quota_limit",
    "quota_exhaustion",
    "quota_consumption",
    "sorting_limit",
    "media_preview",
}

_SEMANTIC_STOP_TOKENS = {
    "case",
    "default",
    "input",
    "module",
    "none",
    "null",
    "ok",
    "output",
    "step",
    "test",
    "测试",
    "验证",
    "用例",
}


def _priority_rank(value: Any) -> int:
    priority = str(value or "").strip().upper()
    if priority == "P0":
        return 3
    if priority == "P1":
        return 2
    if priority == "P2":
        return 1
    return 0


def _module_family(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if not re.search(r"[\u4e00-\u9fff]", text):
        return _normalize_text(text)
    root = re.split(r"[-_/(（]", text, maxsplit=1)[0]
    return _normalize_text(root or text)


def _semantic_similarity_text(case: dict[str, Any]) -> str:
    parts = [
        case_text_field(case, "description"),
        case_text_field(case, "test_module"),
        case_text_field(case, "test_input"),
        case_text_field(case, "expected_result"),
        *case_steps(case)[:3],
    ]
    return " ".join(part for part in parts if part)


def _semantic_tokens(value: Any) -> set[str]:
    text = str(value or "").strip().lower()
    if not text:
        return set()
    text = re.sub(r"tc-\d+", " ", text)
    tokens: set[str] = set()
    for segment in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text):
        if not segment:
            continue
        if re.fullmatch(r"[a-z0-9]+", segment):
            if len(segment) > 1 and not segment.isdigit() and segment not in _SEMANTIC_STOP_TOKENS:
                tokens.add(segment)
            continue
        if len(segment) == 1:
            tokens.add(segment)
            continue
        for width in (2, 3):
            if len(segment) < width:
                continue
            for index in range(0, len(segment) - width + 1):
                token = segment[index : index + width]
                if token not in _SEMANTIC_STOP_TOKENS:
                    tokens.add(token)
    return tokens


def _semantic_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_tokens = _semantic_tokens(_semantic_similarity_text(left))
    right_tokens = _semantic_tokens(_semantic_similarity_text(right))
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    jaccard = float(intersection) / float(union) if union else 0.0
    containment = float(intersection) / float(min(len(left_tokens), len(right_tokens)))
    return max(jaccard, containment)


def _semantic_overlap_size(left: dict[str, Any], right: dict[str, Any]) -> int:
    left_tokens = _semantic_tokens(_semantic_similarity_text(left))
    right_tokens = _semantic_tokens(_semantic_similarity_text(right))
    return len(left_tokens & right_tokens)


def _scenario_patterns_for_runtime(
    *,
    primary_domain: str = "",
    include_domain_specific: bool = False,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    primary = str(primary_domain or "").strip()
    return scenario_pattern_entries(
        primary_domain=primary,
        include_domain_specific=bool(include_domain_specific),
    )


def _simple_scenarios_for_runtime(
    *,
    primary_domain: str = "",
    include_domain_specific: bool = False,
) -> set[str]:
    registered = judge_duplicate_scenario_kinds(
        primary_domain=str(primary_domain or "").strip(),
        include_domain_specific=bool(include_domain_specific),
    )
    return set(_DUPLICATE_SIMPLE_SCENARIOS) | set(registered)


def _thresholds_for_runtime(
    *,
    primary_domain: str = "",
    include_domain_specific: bool = False,
) -> dict[str, tuple[float, int]]:
    return judge_duplicate_thresholds(
        primary_domain=str(primary_domain or "").strip(),
        include_domain_specific=bool(include_domain_specific),
    )


def _cross_module_scenarios_for_runtime(
    *,
    primary_domain: str = "",
    include_domain_specific: bool = False,
) -> set[str]:
    return set(
        cross_module_scenario_kinds(
            primary_domain=str(primary_domain or "").strip(),
            include_domain_specific=bool(include_domain_specific),
        )
    )


def _is_broad_general_scenario_kind(kind: str) -> bool:
    scenario_kind = str(kind or "")
    if scenario_kind not in _DIMENSIONAL_GENERAL_SCENARIO_KINDS:
        return False
    policy = _SCENARIO_POLICY_BY_KEY.get(str(kind or ""))
    if policy is None:
        return False
    return (
        str(policy.domain or "general").strip() == "general"
        and not bool(policy.specific)
    )


def _intent_signature(case: dict[str, Any]) -> str:
    try:
        stage = classify_case_flow_stage(case, {})
        return str(classify_case_intent_signature(case, stage) or "").strip()
    except Exception:
        return ""


def _scenario_kind(
    case: dict[str, Any],
    *,
    primary_domain: str = "",
    include_domain_specific: bool = False,
) -> str:
    text = _semantic_similarity_text(case).lower()
    compact = _normalize_text(text)
    best_kind = ""
    best_score = 0
    for kind, patterns in _scenario_patterns_for_runtime(
        primary_domain=primary_domain,
        include_domain_specific=include_domain_specific,
    ):
        matched: list[str] = []
        for pattern in patterns:
            marker = _normalize_text(pattern)
            if marker and marker in compact:
                matched.append(marker)
        if not matched:
            continue
        long_marker_count = sum(1 for marker in matched if len(marker) >= 4)
        if len(matched) < 2 and long_marker_count < 1:
            continue
        score = len(matched) * 10 + long_marker_count * 3 + max(len(marker) for marker in matched)
        if kind in {"manual_mark_correct", "manual_mark_wrong"}:
            score += 40
        if score > best_score:
            best_kind = kind
            best_score = score
    return best_kind


def _same_module_family(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_module = _module_family(case_text_field(left, "test_module"))
    right_module = _module_family(case_text_field(right, "test_module"))
    if not left_module or not right_module:
        return True
    return left_module == right_module or left_module in right_module or right_module in left_module


def _is_semantic_duplicate_case(
    candidate: dict[str, Any],
    existed: dict[str, Any],
    *,
    primary_domain: str = "",
    include_domain_specific: bool = False,
) -> tuple[bool, float]:
    candidate_desc = _normalize_text(case_text_field(candidate, "description"))
    existed_desc = _normalize_text(case_text_field(existed, "description"))
    if candidate_desc and existed_desc and candidate_desc == existed_desc:
        return True, 1.0
    score = _semantic_similarity(candidate, existed)
    overlap = _semantic_overlap_size(candidate, existed)
    candidate_kind = _scenario_kind(
        candidate,
        primary_domain=primary_domain,
        include_domain_specific=include_domain_specific,
    )
    existed_kind = _scenario_kind(
        existed,
        primary_domain=primary_domain,
        include_domain_specific=include_domain_specific,
    )
    broad_general_kind = bool(
        candidate_kind
        and candidate_kind == existed_kind
        and _is_broad_general_scenario_kind(candidate_kind)
    )
    runtime_thresholds = _thresholds_for_runtime(
        primary_domain=primary_domain,
        include_domain_specific=include_domain_specific,
    )
    runtime_simple_scenarios = _simple_scenarios_for_runtime(
        primary_domain=primary_domain,
        include_domain_specific=include_domain_specific,
    )
    runtime_cross_module_scenarios = _cross_module_scenarios_for_runtime(
        primary_domain=primary_domain,
        include_domain_specific=include_domain_specific,
    )
    if not _same_module_family(candidate, existed):
        if (
            candidate_kind
            and candidate_kind == existed_kind
            and candidate_kind in runtime_cross_module_scenarios
        ):
            simple_score, simple_overlap = runtime_thresholds.get(candidate_kind, (0.30, 8))
            if score >= simple_score and overlap >= simple_overlap:
                return True, score
        return False, 0.0
    if candidate_kind and candidate_kind == existed_kind:
        simple_score, simple_overlap = runtime_thresholds.get(candidate_kind, (0.30, 8))
        if broad_general_kind:
            candidate_intent = _intent_signature(candidate)
            existed_intent = _intent_signature(existed)
            same_intent = bool(candidate_intent and candidate_intent == existed_intent)
            if not same_intent:
                simple_score = max(float(simple_score), _BROAD_GENERAL_DUPLICATE_MIN_SCORE)
                simple_overlap = max(int(simple_overlap), _BROAD_GENERAL_DUPLICATE_MIN_OVERLAP)
        if candidate_kind in runtime_simple_scenarios:
            if score >= simple_score and overlap >= simple_overlap:
                return True, score
            if (
                not broad_general_kind
                and score >= 0.46
                and overlap >= 12
            ):
                return True, score
    if score >= 0.90 and overlap >= 30:
        return True, score
    return False, score


def _case_quality_key(case: dict[str, Any], original_index: int) -> tuple[int, int, int, int]:
    step_count = len(case_steps(case))
    concrete_text_len = len(_normalize_text(case_text_field(case, "expected_result"))) + len(
        _normalize_text(case_text_field(case, "description"))
    )
    return (
        _priority_rank(case_priority(case)),
        min(step_count, 6),
        min(concrete_text_len, 240),
        -int(original_index),
    )
