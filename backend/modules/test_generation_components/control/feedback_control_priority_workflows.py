from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .feedback_control_config import (
    _MAX_WORKFLOW_BLUEPRINTS,
    _MIN_PRIORITY_POOL_PATTERN_CONFIDENCE,
)
from .feedback_control_priority_retrieval_text import _sample_matches_primary_domain
from .feedback_control_priority_signals import (
    _is_pattern_active,
    _pattern_confidence,
)
from .feedback_control_sample_access import (
    sample_case_id as _sample_case_id,
    sample_value as _sample_value,
)
from ..coverage.domain_gate import current_domain_gate
from ..coverage.scenario_registry import infer_primary_domain_tag


def _workflow_blueprint_from_sample(
    sample: dict[str, Any],
    *,
    sample_value_fn: Callable[..., Any] | None = None,
    sample_case_id_fn: Callable[[dict[str, Any]], str] | None = None,
) -> dict[str, Any] | None:
    sample_value = sample_value_fn or _sample_value
    sample_case_id = sample_case_id_fn or _sample_case_id
    grain = str(sample_value(sample, "pattern_grain", "patternGrain") or "").strip().lower()
    if grain != "workflow_blueprint":
        return None
    raw = sample_value(sample, "workflow_blueprint", "workflowBlueprint")
    blueprint = dict(raw) if isinstance(raw, dict) else {}
    selection_source = str(blueprint.get("selection_source") or "").strip().lower()
    if selection_source not in {"explicit_main_smoke", "trusted_workflow_contract"}:
        return None
    steps = blueprint.get("steps")
    if not isinstance(steps, list) or len(steps) < 2:
        return None
    normalized_steps: list[dict[str, Any]] = []
    for index, step in enumerate(steps or [], start=1):
        if not isinstance(step, dict):
            return None
        normalized_step = dict(step)
        label = str(
            normalized_step.get("label")
            or normalized_step.get("action")
            or normalized_step.get("description")
            or ""
        ).strip()
        required = (
            label,
            normalized_step.get("action"),
            normalized_step.get("actor"),
            normalized_step.get("state_in"),
            normalized_step.get("state_out"),
            normalized_step.get("stage_kind"),
        )
        if not all(str(value or "").strip() for value in required):
            return None
        normalized_step["id"] = str(normalized_step.get("id") or f"step_{index:03d}").strip()
        normalized_step["label"] = label[:160]
        normalized_steps.append(normalized_step)
    if len(normalized_steps) < 2:
        return None
    title = str(
        blueprint.get("name")
        or blueprint.get("title")
        or sample_value(sample, "pattern_summary", "patternSummary")
        or "workflow_blueprint"
    ).strip()
    return {
        **blueprint,
        "id": str(blueprint.get("id") or sample_case_id(sample) or "workflow_blueprint"),
        "name": title[:160],
        "steps": normalized_steps[:12],
        "source": str(sample_value(sample, "source", "source_type", "sourceType") or "priority_sample_pool"),
    }


def _priority_pool_sample_identity(
    sample: dict[str, Any],
    *,
    sample_value_fn: Callable[..., Any] | None = None,
    sample_case_id_fn: Callable[[dict[str, Any]], str] | None = None,
) -> str:
    sample_value = sample_value_fn or _sample_value
    sample_case_id = sample_case_id_fn or _sample_case_id
    return str(
        sample_value(sample, "sample_id", "sampleId")
        or sample_case_id(sample)
        or sample_value(sample, "source_case_id", "sourceCaseId")
        or id(sample)
    ).strip()


def _select_priority_pool_workflow_blueprint_samples(
    *,
    samples: list[dict[str, Any]],
    requirement_text: str,
    workflow_blueprint_from_sample_fn: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    priority_pool_sample_identity_fn: Callable[[dict[str, Any]], str] | None = None,
    sample_value_fn: Callable[..., Any] | None = None,
    is_pattern_active_fn: Callable[[dict[str, Any]], bool] | None = None,
    pattern_confidence_fn: Callable[[dict[str, Any]], float] | None = None,
    sample_matches_primary_domain_fn: Callable[[dict[str, Any], str], bool] | None = None,
    max_workflow_blueprints: int = _MAX_WORKFLOW_BLUEPRINTS,
    min_pattern_confidence: float = _MIN_PRIORITY_POOL_PATTERN_CONFIDENCE,
) -> list[dict[str, Any]]:
    sample_value = sample_value_fn or _sample_value
    workflow_blueprint_from_sample = workflow_blueprint_from_sample_fn or _workflow_blueprint_from_sample
    priority_pool_sample_identity = priority_pool_sample_identity_fn or _priority_pool_sample_identity
    is_pattern_active = is_pattern_active_fn or _is_pattern_active
    pattern_confidence = pattern_confidence_fn or _pattern_confidence
    sample_matches_primary_domain = sample_matches_primary_domain_fn or _sample_matches_primary_domain

    query = str(requirement_text or "").strip()
    if not bool(current_domain_gate(query).get("allows_historical_profile")):
        return []
    primary_query_domain = infer_primary_domain_tag(query)
    candidates: list[dict[str, Any]] = []
    for sample in samples:
        grain = str(sample_value(sample, "pattern_grain", "patternGrain") or "").strip().lower()
        if grain != "workflow_blueprint":
            continue
        if not is_pattern_active(sample):
            continue
        if pattern_confidence(sample) < float(min_pattern_confidence):
            continue
        if primary_query_domain and not sample_matches_primary_domain(sample, primary_query_domain):
            continue
        if workflow_blueprint_from_sample(sample) is None:
            continue
        candidates.append(sample)
    candidates.sort(
        key=lambda item: (
            float(sample_value(item, "pattern_weight") or 0.0),
            float(sample_value(item, "pattern_quality_score") or 0.0),
            priority_pool_sample_identity(item),
        ),
        reverse=True,
    )
    return candidates[:max_workflow_blueprints]
