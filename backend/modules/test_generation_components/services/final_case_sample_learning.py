"""Build reusable sample-pool learning signals from final-case pairs."""

from __future__ import annotations

import re
from typing import Any

from ..postprocess.case_access import case_priority, case_text_parts
from ..postprocess.streaming_expected_result_quality import is_non_assertable_expected_result
from .final_case_parsing import _text

_MAX_DERIVED_POSITIVE_SAMPLES = 120
_MAX_DERIVED_POSITIVE_PATTERNS = 40
_MAX_POSITIVE_SAMPLES_PER_PATTERN_KEY = 2
_MAX_DERIVED_NEGATIVE_SAMPLES = 80
_EXECUTION_GROUP_PATTERN_CATEGORIES = {
    "main_smoke": "main_smoke_flow",
    "permission": "permission_guard",
    "exception": "exception_path",
    "boundary": "boundary_condition",
    "display": "display_behavior",
    "independent_functional": "functional_behavior",
}
_INVALID_EXPECTED_RESULT_QUALITIES = frozenset({"invalid_case", "non_assertable", "truncated"})


def _case_text(case: dict[str, Any]) -> str:
    return " ".join(
        case_text_parts(
            case,
            ("description", "test_module", "preconditions", "steps", "test_input", "expected_result"),
            dedupe=False,
        )
    ).strip()


def _fingerprint(raw: str) -> str:
    return re.sub(r"\s+", "", str(raw or "").lower())[:5000]


def _stable_case_id(case: dict[str, Any]) -> str:
    return _text(case.get("id") or case.get("case_id") or case.get("caseId"))


def _match_generated_to_final(generated_cases: list[dict[str, Any]], final_cases: list[dict[str, Any]]) -> set[int]:
    final_ids = {_stable_case_id(case) for case in final_cases}
    final_ids.discard("")
    return {
        index
        for index, case in enumerate(generated_cases)
        if _stable_case_id(case) in final_ids
    }


def _infer_pattern_category(case: dict[str, Any]) -> str:
    explicit = _text(case.get("pattern_category") or case.get("patternCategory")).lower()
    if explicit and _text(case.get("category_source") or case.get("categorySource")):
        return explicit[:64]
    execution_group = _text(case.get("execution_group") or case.get("executionGroup")).lower()
    if execution_group in _EXECUTION_GROUP_PATTERN_CATEGORIES:
        return _EXECUTION_GROUP_PATTERN_CATEGORIES[execution_group]
    transition = case.get("workflow_transition")
    if isinstance(transition, dict) and (
        _text(transition.get("source_state") or transition.get("state_in"))
        and _text(transition.get("target_state") or transition.get("state_out"))
    ):
        return "state_transition"
    relation = _text(case.get("learning_relation") or case.get("learningRelation")).lower()
    if relation in {"final_added", "final_modified", "final_unchanged"}:
        return relation
    return "human_final_case"


def _priority(case: dict[str, Any]) -> str:
    value = case_priority(case) or str(case.get("model_priority") or "P2").strip().upper()
    return value if value in {"P0", "P1", "P2"} else "P2"


def _aggregate_positive_pattern_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep representative final-case patterns instead of storing every final case."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        key = _positive_pattern_key(sample)
        buckets.setdefault(key, []).append(sample)

    selected: list[dict[str, Any]] = []
    # 中文注释：类别仅作为上游结构化元数据，排序由显式优先级与稳定键决定。
    for _key, bucket in sorted(buckets.items(), key=lambda item: _positive_bucket_rank(item[1])):
        selected.extend(bucket[:_MAX_POSITIVE_SAMPLES_PER_PATTERN_KEY])
        if len(selected) >= _MAX_DERIVED_POSITIVE_PATTERNS:
            break
    return selected[:_MAX_DERIVED_POSITIVE_PATTERNS]


def _positive_pattern_key(sample: dict[str, Any]) -> str:
    return "|".join(
        [
            str(sample.get("pattern_category") or ""),
            str(sample.get("expected_priority") or ""),
            "ext" if sample.get("manual_business_extension") is True else "req",
            _summarize_module_hint(str(sample.get("source_case_module") or "")).lower(),
        ]
    )


def _positive_bucket_rank(bucket: list[dict[str, Any]]) -> tuple[int, int, str]:
    first = bucket[0] if bucket else {}
    priority = str(first.get("expected_priority") or "P2")
    priority_rank = {"P0": 0, "P1": 1, "P2": 2}.get(priority, 2)
    relation_rank = 0 if str(first.get("pattern_category") or "") == "final_modified" else 1
    return (priority_rank, relation_rank, _positive_pattern_key(first))


def _build_positive_sample(
    case: dict[str, Any],
    *,
    index: int,
    generation_id: int | None,
    linked_doc_ids: list[int],
    manual_business_extension: bool,
    quality_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    description = _text(case.get("description")) or f"final-case-{index}"
    module = _text(case.get("test_module"))
    expected_result = _text(case.get("expected_result"))
    steps = _text(case.get("steps"))
    category = _infer_pattern_category(case)
    pattern_summary = _summarize_positive_pattern(description, module, category)
    extension_note = "manual_business_extension" if manual_business_extension else "requirement_grounded_final_case"
    return {
        "signal_type": "positive",
        "pattern_usage": "prefer",
        "pattern_category": category,
        "reason_category": category,
        "expected_priority": _priority(case),
        "case_id": _text(case.get("id")) or f"final-{index}",
        "title": description[:120],
        "user_comment": _text(case.get("user_comment") or case.get("comment"))[:240],
        "pattern_summary": pattern_summary,
        "pattern_grain": "pattern",
        "source_case_title": description[:160],
        "source_case_module": module[:80],
        "source_case_steps": steps[:240],
        "source_case_expected_result": expected_result[:240],
        "business_assertion": expected_result[:240],
        "source": (
            "linked_final_case_business_extension"
            if manual_business_extension
            else "linked_final_case_pattern"
        ),
        "source_type": (
            "linked_final_case_business_extension"
            if manual_business_extension
            else "linked_final_case_pattern"
        ),
        "source_id": int(generation_id) if generation_id is not None else None,
        "source_case_id": _text(case.get("id")) or None,
        "learning_signal_source": extension_note,
        "pattern_scope": "project",
        "pattern_confidence": _pattern_confidence_from_ledger(quality_ledger, positive=True),
        "quality_ledger": dict(quality_ledger or {}),
        "manual_business_extension": manual_business_extension,
        "generation_id": generation_id,
        "linked_doc_ids": linked_doc_ids,
    }


def _summarize_positive_pattern(description: str, module: str, category: str) -> str:
    """Convert a human-final case into a reusable generation pattern."""
    module_hint = _summarize_module_hint(module)
    detail = _positive_pattern_detail(category)
    parts = [category, detail]
    if module_hint:
        parts.append(f"领域:{module_hint}")
    return " | ".join(part for part in parts if part)[:180]


def _summarize_module_hint(module: str) -> str:
    normalized = re.sub(r"\s+", " ", _text(module)).strip()
    if not normalized:
        return ""
    # Keep only a compact domain hint; the concrete case title is stored separately.
    normalized = re.sub(r"[-_]+", " ", normalized)
    return normalized[:40]


def _positive_pattern_detail(category: str) -> str:
    return f"structured_source:{category or 'human_final_case'}"


def _clear_negative_reason(case: dict[str, Any]) -> str:
    expected = _text(case.get("expected_result"))
    expected_quality = _text(
        case.get("expected_result_quality") or case.get("expectedResultQuality")
    ).lower()
    if expected_quality in _INVALID_EXPECTED_RESULT_QUALITIES:
        return f"expected_result_quality:{expected_quality}"
    if expected_quality and expected_quality != "assertable":
        return ""
    if expected and is_non_assertable_expected_result(expected):
        return "non_assertable_expected_result"

    priority = _priority(case)
    execution_group = _text(case.get("execution_group") or case.get("executionGroup")).lower()
    if priority == "P0" and execution_group == "display":
        return "priority_overpromotion_for_display_case"
    return ""


def _build_negative_sample(
    case: dict[str, Any],
    *,
    index: int,
    reason: str,
    generation_id: int | None,
    quality_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    description = _text(case.get("description")) or f"generated-case-{index}"
    expected_result = _text(case.get("expected_result"))
    steps = _text(case.get("steps"))
    module = _text(case.get("test_module"))
    return {
        "signal_type": "negative",
        "pattern_usage": "avoid",
        "pattern_category": reason,
        "reason_category": reason,
        "expected_priority": "P2",
        "case_id": _text(case.get("id")) or f"generated-{index}",
        "title": description[:120],
        "user_comment": _text(case.get("user_comment") or case.get("comment"))[:240],
        "pattern_summary": f"{reason} | {description}"[:180],
        "pattern_grain": "anti_pattern",
        "source_case_title": description[:160],
        "source_case_module": module[:80],
        "source_case_steps": steps[:240],
        "source_case_expected_result": expected_result[:240],
        "business_assertion": expected_result[:240],
        "source": "quality_evaluation_defect",
        "source_type": "quality_evaluation_defect",
        "source_id": int(generation_id) if generation_id is not None else None,
        "source_case_id": _text(case.get("id")) or None,
        "pattern_scope": "project",
        "pattern_confidence": _pattern_confidence_from_ledger(quality_ledger, positive=False),
        "quality_ledger": dict(quality_ledger or {}),
        "generation_id": generation_id,
    }


def _compact_quality_ledger(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload:
        return {}
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    funnel = payload.get("funnel") if isinstance(payload.get("funnel"), dict) else {}
    review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
    judge = payload.get("judge") if isinstance(payload.get("judge"), dict) else {}
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    remediation = (
        payload.get("quality_remediation")
        if isinstance(payload.get("quality_remediation"), dict)
        else {}
    )
    remediation_actions = [
        str(item.get("action_id") or "")
        for item in (remediation.get("actions") or [])
        if isinstance(item, dict) and str(item.get("action_id") or "").strip()
    ]
    return {
        "generation_id": int(payload.get("generation_id") or 0),
        "quality_assessment": str(payload.get("quality_assessment") or ""),
        "initial_quality_score": int(payload.get("initial_quality_score") or payload.get("quality_score") or 0),
        "quality_score_grade": str(payload.get("quality_score_grade") or ""),
        "final_count": int(payload.get("final_count") or 0),
        "coverage_rate": float(coverage.get("coverage_rate") or 0.0),
        "missing_rules_count": int(coverage.get("missing_rules_count") or 0),
        "non_blocking_rules_count": int(coverage.get("non_blocking_rules_count") or 0),
        "review_candidate_total": int(review.get("candidate_total") or funnel.get("candidate_count_before_review") or 0),
        "review_retained_total": int(review.get("retained_total") or funnel.get("review_selected_count") or 0),
        "judge_rejected_out_count": int(judge.get("rejected_out_count") or 0),
        "judge_pending_out_count": int(judge.get("pending_out_count") or 0),
        "snapshot_used": bool(context.get("snapshot_used")),
        "fusion_mode": str(context.get("fusion_mode") or ""),
        "quality_primary_action": str(remediation.get("primary_action") or ""),
        "quality_action_ids": remediation_actions[:8],
    }


def _pattern_confidence_from_ledger(payload: dict[str, Any] | None, *, positive: bool) -> float:
    if not isinstance(payload, dict) or not payload:
        return 0.72 if positive else 0.65
    coverage_rate = float(payload.get("coverage_rate") or 0.0)
    missing_rules = int(payload.get("missing_rules_count") or 0)
    rejected = int(payload.get("judge_rejected_out_count") or 0) + int(payload.get("judge_pending_out_count") or 0)
    confidence = 0.68
    if coverage_rate >= 0.9:
        confidence += 0.08
    if missing_rules <= 2:
        confidence += 0.06
    if rejected <= 0:
        confidence += 0.04
    if positive:
        confidence += 0.06
    else:
        confidence -= 0.02
    return round(max(0.35, min(0.92, confidence)), 4)
