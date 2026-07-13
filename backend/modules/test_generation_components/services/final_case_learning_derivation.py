"""Derive learning samples from generated/final case pairs."""

from __future__ import annotations

from typing import Any

from .final_case_parsing import _normalize_case_dict
from .final_case_sample_learning import (
    _MAX_DERIVED_NEGATIVE_SAMPLES,
    _MAX_DERIVED_POSITIVE_PATTERNS,
    _MAX_DERIVED_POSITIVE_SAMPLES,
    _MAX_POSITIVE_SAMPLES_PER_PATTERN_KEY,
    _aggregate_positive_pattern_samples,
    _build_negative_sample,
    _build_positive_sample,
    _case_is_grounded_in_requirement,
    _clear_negative_reason,
    _compact_quality_ledger,
    _match_generated_to_final,
)
from .final_case_workflow_learning import _build_workflow_blueprint_sample

def build_learning_samples_from_final_cases(
    *,
    generated_cases: list[dict[str, Any]],
    final_cases: list[dict[str, Any]],
    requirement_text: str = "",
    generation_id: int | None = None,
    linked_doc_ids: list[int] | None = None,
    include_negative_samples: bool = True,
    quality_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build positive and limited negative samples from generated vs final cases.

    Human-final cases are always positive evidence, including cases that expand
    beyond the uploaded requirement. AI-only cases become negative only when
    they show a concrete quality failure.
    """
    normalized_generated = [_normalize_case_dict(item) for item in generated_cases if isinstance(item, dict)]
    normalized_final = [_normalize_case_dict(item) for item in final_cases if isinstance(item, dict)]
    ledger_summary = _compact_quality_ledger(quality_ledger)

    positive_candidates: list[dict[str, Any]] = []
    for idx, case in enumerate(normalized_final[:_MAX_DERIVED_POSITIVE_SAMPLES], start=1):
        extension = bool(str(requirement_text or "").strip()) and not _case_is_grounded_in_requirement(
            case,
            requirement_text,
        )
        positive_candidates.append(
            _build_positive_sample(
                case,
                index=idx,
                generation_id=generation_id,
                linked_doc_ids=linked_doc_ids or [],
                manual_business_extension=extension,
                quality_ledger=ledger_summary,
            )
        )
    positives = _aggregate_positive_pattern_samples(positive_candidates)
    workflow_blueprint_sample = _build_workflow_blueprint_sample(
        normalized_final,
        generation_id=generation_id,
        linked_doc_ids=linked_doc_ids or [],
        quality_ledger=ledger_summary,
    )
    if workflow_blueprint_sample is not None:
        positives = [workflow_blueprint_sample, *positives]

    negatives: list[dict[str, Any]] = []
    if include_negative_samples:
        matched_generated_indexes = _match_generated_to_final(normalized_generated, normalized_final)
        for idx, case in enumerate(normalized_generated, start=1):
            if (idx - 1) in matched_generated_indexes:
                continue
            reason = _clear_negative_reason(case)
            if not reason:
                continue
            negatives.append(
                _build_negative_sample(
                    case,
                    index=idx,
                    reason=reason,
                    generation_id=generation_id,
                    quality_ledger=ledger_summary,
                )
            )
            if len(negatives) >= _MAX_DERIVED_NEGATIVE_SAMPLES:
                break

    return {
        "positive_samples": positives,
        "negative_samples": negatives,
        "samples": positives + negatives,
        "diagnostics": {
            "generated_case_count": len(normalized_generated),
            "final_case_count": len(normalized_final),
            "positive_candidate_count": len(positive_candidates),
            "positive_sample_count": len(positives),
            "workflow_blueprint_sample_count": 1 if workflow_blueprint_sample is not None else 0,
            "negative_sample_count": len(negatives),
            "manual_business_extension_count": sum(
                1 for item in positives if item.get("manual_business_extension") is True
            ),
            "manual_business_extension_candidate_count": sum(
                1 for item in positive_candidates if item.get("manual_business_extension") is True
            ),
            "positive_aggregation_policy": (
                f"pattern_key_top{_MAX_POSITIVE_SAMPLES_PER_PATTERN_KEY}_cap{_MAX_DERIVED_POSITIVE_PATTERNS}"
            ),
            "negative_policy": "ai_only_clear_quality_failure_only",
            "quality_ledger_attached": bool(ledger_summary),
        },
    }

__all__ = ["build_learning_samples_from_final_cases"]
