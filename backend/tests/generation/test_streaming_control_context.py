from __future__ import annotations

from modules.test_generation_components.control.feedback_control_state import FeedbackControlState
from modules.test_generation_components.postprocess.streaming_control_context import (
    resolve_streaming_control_context,
)


def _workflow_contract(workflow_id: str) -> dict[str, object]:
    return {
        "id": workflow_id,
        "source_type": "human_reviewed",
        "trusted": True,
        "steps": [
            {
                "state_in": "draft",
                "state_out": "submitted",
                "action": "submit homework",
            },
            {
                "state_in": "submitted",
                "state_out": "reviewed",
                "action": "teacher reviews homework",
            },
        ],
    }


def test_resolve_streaming_control_context_uses_empty_defaults() -> None:
    context = resolve_streaming_control_context(None)

    assert isinstance(context.control_state, FeedbackControlState)
    assert context.source_meta == {}
    assert context.generation_coverage_profile == {}
    assert context.fact_profile == {}
    assert context.project_profile == {}
    assert context.manual_quality_profile == {}
    assert context.generation_coverage_mode == "core_smoke"
    assert context.generation_target_case_range == {}
    assert context.priority_pool_redundant_scenario_caps == {}
    assert context.must_cover_rule_set == set()
    assert context.forbidden_patterns == []
    assert context.reuse_risks == []
    assert context.soft_constraints == []
    assert context.quality_fix_hints == []
    assert context.workflow_blueprints == []
    assert context.trusted_workflow_contracts == []
    assert context.current_requirement_workflow_blueprints == []
    assert context.authoritative_workflow_blueprints == []


def test_resolve_streaming_control_context_extracts_source_meta_profiles() -> None:
    context = resolve_streaming_control_context(
        {
            "must_cover_rules": [" p0_rule ", "P1_rule"],
            "forbidden_patterns": [" stale state ", ""],
            "reuse_risks": ["shared cache"],
            "soft_constraints": ["avoid display-only"],
            "quality_fix_hints": ["expected result must assert state"],
            "source_meta": {
                "generation_coverage_profile": {
                    "coverage_mode": "full_functional_regression",
                    "target_case_range": {"min": 18, "max": 30},
                },
                "fact_profile": {"profile_source": "requirement", "confirmed_facts": ["A"]},
                "project_profile": {"profile_source": "project", "confidence": 0.8},
                "manual_quality_profile": {"profile_source": "manual", "trusted_sample_count": 3},
                "priority_pool_redundant_scenario_caps": {"display": 1},
            },
        }
    )

    assert context.generation_coverage_mode == "full_functional_regression"
    assert context.generation_target_case_range == {"min": 18, "max": 30}
    assert context.fact_profile == {"profile_source": "requirement", "confirmed_facts": ["A"]}
    assert context.project_profile == {"profile_source": "project", "confidence": 0.8}
    assert context.manual_quality_profile == {"profile_source": "manual", "trusted_sample_count": 3}
    assert context.priority_pool_redundant_scenario_caps == {"display": 1}
    assert context.must_cover_rule_set == {"P0_RULE", "P1_RULE"}
    assert context.forbidden_patterns == ["stale state"]
    assert context.reuse_risks == ["shared cache"]
    assert context.soft_constraints == ["avoid display-only"]
    assert context.quality_fix_hints == ["expected result must assert state"]


def test_resolve_streaming_control_context_classifies_workflow_blueprints() -> None:
    trusted_contract = _workflow_contract("trusted-flow")
    current_requirement_blueprint = {
        "id": "current-flow",
        "repository_source": "current_requirement_blueprint",
        "steps": [{"label": "open"}, {"label": "submit"}],
    }
    current_extracted_blueprint = {
        "id": "current-extracted-flow",
        "source_type": "current_requirement_extracted",
        "steps": [{"label": "select"}, {"label": "confirm"}],
    }
    invalid_blueprint = {"id": "missing-steps"}

    context = resolve_streaming_control_context(
        {
            "workflow_blueprints": [
                trusted_contract,
                current_requirement_blueprint,
                current_extracted_blueprint,
                invalid_blueprint,
                "not a blueprint",
            ]
        }
    )

    assert [item["id"] for item in context.workflow_blueprints] == [
        "trusted-flow",
        "current-flow",
        "current-extracted-flow",
    ]
    assert [step["label"] for step in context.workflow_blueprints[0]["steps"]] == [
        "submit homework",
        "teacher reviews homework",
    ]
    assert [item["id"] for item in context.trusted_workflow_contracts] == ["trusted-flow"]
    assert [item["id"] for item in context.current_requirement_workflow_blueprints] == [
        "current-flow",
        "current-extracted-flow",
    ]
    assert [item["id"] for item in context.authoritative_workflow_blueprints] == [
        "trusted-flow",
        "current-flow",
        "current-extracted-flow",
    ]


def test_resolve_streaming_control_context_keeps_fallback_blueprint_diagnostic_only() -> None:
    fallback_blueprint = {
        "id": "current_requirement_fallback_main_flow",
        "repository_source": "current_requirement_blueprint",
        "source_type": "current_requirement_extracted",
        "fallback": True,
        "allow_final_materialization": False,
        "steps": [
            {"label": "configure", "allow_bridge": False},
            {"label": "submit", "allow_bridge": False},
        ],
    }
    trusted_contract = _workflow_contract("trusted-flow")

    context = resolve_streaming_control_context(
        {"workflow_blueprints": [fallback_blueprint, trusted_contract]}
    )

    assert [item["id"] for item in context.workflow_blueprints] == ["trusted-flow"]
    assert [item["id"] for item in context.current_requirement_workflow_blueprints] == [
        "current_requirement_fallback_main_flow"
    ]
    assert [item["id"] for item in context.authoritative_workflow_blueprints] == ["trusted-flow"]
