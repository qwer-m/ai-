from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..control.feedback_control_state import FeedbackControlState
from ..control.workflow_blueprint_repository import is_trusted_workflow_contract


@dataclass(frozen=True)
class StreamingControlContext:
    control_state: FeedbackControlState
    source_meta: dict[str, Any]
    generation_coverage_profile: dict[str, Any]
    fact_profile: dict[str, Any]
    project_profile: dict[str, Any]
    manual_quality_profile: dict[str, Any]
    generation_coverage_mode: str
    generation_target_case_range: dict[str, Any]
    priority_pool_redundant_scenario_caps: dict[str, Any]
    must_cover_rule_set: set[str]
    forbidden_patterns: list[str]
    reuse_risks: list[str]
    soft_constraints: list[str]
    quality_fix_hints: list[str]
    workflow_blueprints: list[dict[str, Any]]
    trusted_workflow_contracts: list[dict[str, Any]]
    current_requirement_workflow_blueprints: list[dict[str, Any]]
    authoritative_workflow_blueprints: list[dict[str, Any]]


def _dict_or_empty(value: Any) -> dict[str, Any]:
    try:
        return dict(value or {})
    except (TypeError, ValueError):
        return {}


def _text_list(values: Any) -> list[str]:
    return [str(item).strip() for item in (values or []) if str(item).strip()]


def _is_current_requirement_workflow_blueprint(item: dict[str, Any]) -> bool:
    source = str(item.get("repository_source") or item.get("source") or "").strip()
    source_type = str(item.get("source_type") or "").strip()
    return source == "current_requirement_blueprint" or source_type == "current_requirement_extracted"


def _is_fallback_current_requirement_workflow_blueprint(item: dict[str, Any]) -> bool:
    if not _is_current_requirement_workflow_blueprint(item):
        return False
    workflow_id = str(item.get("workflow_id") or item.get("id") or "").strip()
    return bool(
        item.get("fallback") is True
        or item.get("allow_final_materialization") is False
        or workflow_id == "current_requirement_fallback_main_flow"
    )


def resolve_streaming_control_context(feedback_control_state: Any) -> StreamingControlContext:
    control_state = FeedbackControlState.from_any(feedback_control_state)
    source_meta = _dict_or_empty(control_state.source_meta)
    generation_coverage_profile = _dict_or_empty(source_meta.get("generation_coverage_profile"))
    fact_profile = _dict_or_empty(source_meta.get("fact_profile"))
    project_profile = _dict_or_empty(source_meta.get("project_profile"))
    manual_quality_profile = _dict_or_empty(source_meta.get("manual_quality_profile"))
    generation_coverage_mode = str(generation_coverage_profile.get("coverage_mode") or "core_smoke")
    generation_target_case_range = _dict_or_empty(generation_coverage_profile.get("target_case_range"))
    raw_redundant_caps = source_meta.get("priority_pool_redundant_scenario_caps")
    priority_pool_redundant_scenario_caps = dict(raw_redundant_caps) if isinstance(raw_redundant_caps, dict) else {}
    must_cover_rule_set = {
        str(rule).strip().upper()
        for rule in (control_state.must_cover_rules or [])
        if str(rule).strip()
    }
    forbidden_patterns = _text_list(control_state.forbidden_patterns)
    reuse_risks = _text_list(control_state.reuse_risks)
    soft_constraints = _text_list(control_state.soft_constraints)
    quality_fix_hints = _text_list(control_state.quality_fix_hints)
    raw_workflow_blueprints = [
        dict(item)
        for item in (control_state.workflow_blueprints or [])
        if isinstance(item, dict) and isinstance(item.get("steps"), list)
    ]
    workflow_blueprints = [
        item for item in raw_workflow_blueprints if not _is_fallback_current_requirement_workflow_blueprint(item)
    ]
    trusted_workflow_contracts = [
        item for item in workflow_blueprints if is_trusted_workflow_contract(item)
    ]
    current_requirement_workflow_blueprints = [
        item
        for item in raw_workflow_blueprints
        if _is_current_requirement_workflow_blueprint(item)
    ]
    authoritative_workflow_blueprints = [
        *trusted_workflow_contracts,
        *[
            item
            for item in current_requirement_workflow_blueprints
            if item not in trusted_workflow_contracts
            and not _is_fallback_current_requirement_workflow_blueprint(item)
        ],
    ]
    return StreamingControlContext(
        control_state=control_state,
        source_meta=source_meta,
        generation_coverage_profile=generation_coverage_profile,
        fact_profile=fact_profile,
        project_profile=project_profile,
        manual_quality_profile=manual_quality_profile,
        generation_coverage_mode=generation_coverage_mode,
        generation_target_case_range=generation_target_case_range,
        priority_pool_redundant_scenario_caps=priority_pool_redundant_scenario_caps,
        must_cover_rule_set=must_cover_rule_set,
        forbidden_patterns=forbidden_patterns,
        reuse_risks=reuse_risks,
        soft_constraints=soft_constraints,
        quality_fix_hints=quality_fix_hints,
        workflow_blueprints=workflow_blueprints,
        trusted_workflow_contracts=trusted_workflow_contracts,
        current_requirement_workflow_blueprints=current_requirement_workflow_blueprints,
        authoritative_workflow_blueprints=authoritative_workflow_blueprints,
    )
