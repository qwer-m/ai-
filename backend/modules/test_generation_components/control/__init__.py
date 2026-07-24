from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "build_feedback_control_state": (".build_feedback_control_state", "build_feedback_control_state"),
    "CANONICAL_ROLE_SESSION_KEYS": (".actor_roles", "CANONICAL_ROLE_SESSION_KEYS"),
    "normalize_actor_role": (".actor_roles", "normalize_actor_role"),
    "session_key_for_role": (".actor_roles", "session_key_for_role"),
    "FeedbackControlState": (".feedback_control_state", "FeedbackControlState"),
    "CURRENT_REQUIREMENT_BLUEPRINT_REPOSITORY_SOURCE": (
        ".current_requirement_blueprint",
        "CURRENT_REQUIREMENT_BLUEPRINT_REPOSITORY_SOURCE",
    ),
    "CURRENT_REQUIREMENT_BLUEPRINT_SOURCE_TYPE": (
        ".current_requirement_blueprint",
        "CURRENT_REQUIREMENT_BLUEPRINT_SOURCE_TYPE",
    ),
    "extract_current_requirement_blueprints": (
        ".current_requirement_blueprint",
        "extract_current_requirement_blueprints",
    ),
    "merge_current_requirement_blueprint_control_state": (
        ".current_requirement_blueprint",
        "merge_current_requirement_blueprint_control_state",
    ),
    "normalize_current_requirement_blueprint_payload": (
        ".current_requirement_blueprint",
        "normalize_current_requirement_blueprint_payload",
    ),
    "build_fact_profile": (".fact_profile_activation", "build_fact_profile"),
    "merge_fact_profile_control_state": (
        ".fact_profile_activation",
        "merge_fact_profile_control_state",
    ),
    "normalize_fact_profile": (".fact_profile_activation", "normalize_fact_profile"),
    "build_project_profile": (".project_profile_activation", "build_project_profile"),
    "merge_project_profile_control_state": (
        ".project_profile_activation",
        "merge_project_profile_control_state",
    ),
    "normalize_project_profile": (".project_profile_activation", "normalize_project_profile"),
    "WorkflowBlueprintRepository": (".workflow_blueprint_repository", "WorkflowBlueprintRepository"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value
