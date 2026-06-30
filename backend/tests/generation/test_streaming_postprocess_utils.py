from __future__ import annotations

from modules.test_generation_components.control.feedback_control_state import FeedbackControlState
from modules.test_generation_components.postprocess.streaming_postprocess_utils import (
    LowQualityFilterStatsAccumulator,
    build_feedback_control_debug_payload,
    build_flow_project_profile_for_governance,
    resolve_generation_coverage_profile,
    resolve_generation_coverage_state,
)


def test_low_quality_filter_stats_accumulator_initializes_from_stats() -> None:
    accumulator = LowQualityFilterStatsAccumulator(
        {
            "invalid_structure_dropped": 2,
            "weak_case_dropped": 3,
            "semantic_dedup_dropped": 4,
            "governance_hard_drop": 5,
            "total_dropped": 14,
            "dropped_details": [
                {"case_id": "TC-001", "reason": "missing_steps"},
                "ignored",
            ],
        }
    )

    assert accumulator.low_quality_structural_dropped_total == 5
    assert accumulator.low_quality_dropped_total == 5
    assert accumulator.semantic_dedup_dropped_total == 4
    assert accumulator.governance_hard_drop_total == 5
    assert accumulator.postprocess_filter_drop_total == 14
    assert accumulator.dropped_details == [{"case_id": "TC-001", "reason": "missing_steps"}]
    assert accumulator.drop_details is accumulator.dropped_details
    assert accumulator.low_quality_drop_details is accumulator.dropped_details


def test_low_quality_filter_stats_accumulator_accumulates_multiple_batches() -> None:
    accumulator = LowQualityFilterStatsAccumulator(
        {
            "invalid_structure_dropped": 1,
            "semantic_dedup_dropped": 1,
            "total_dropped": 3,
            "dropped_details": [{"case_id": "TC-001"}],
        }
    )

    accumulator.accumulate(
        {
            "weak_case_dropped": 2,
            "governance_hard_drop": 3,
            "total_dropped": 5,
            "dropped_details": [{"case_id": "TC-002"}],
        }
    )
    accumulator.accumulate(
        {
            "invalid_structure_dropped": 4,
            "semantic_dedup_dropped": 2,
            "total_dropped": 7,
        }
    )

    assert accumulator.low_quality_structural_dropped_total == 7
    assert accumulator.low_quality_dropped_total == 7
    assert accumulator.semantic_dedup_dropped_total == 3
    assert accumulator.governance_hard_drop_total == 3
    assert accumulator.postprocess_filter_drop_total == 15
    assert accumulator.dropped_details == [{"case_id": "TC-001"}, {"case_id": "TC-002"}]


def test_low_quality_filter_stats_accumulator_copies_dropped_details() -> None:
    initial_detail = {"case_id": "TC-001", "reason": "initial"}
    extra_detail = {"case_id": "TC-002", "reason": "extra"}
    accumulator = LowQualityFilterStatsAccumulator({"dropped_details": [initial_detail]})

    accumulator.accumulate({"dropped_details": [extra_detail]})
    initial_detail["reason"] = "mutated"
    extra_detail["reason"] = "mutated"

    assert accumulator.dropped_details == [
        {"case_id": "TC-001", "reason": "initial"},
        {"case_id": "TC-002", "reason": "extra"},
    ]
    assert accumulator.dropped_details[0] is not initial_detail
    assert accumulator.dropped_details[1] is not extra_detail


def test_low_quality_filter_stats_accumulator_adds_postprocess_quality_drop() -> None:
    accumulator = LowQualityFilterStatsAccumulator(
        {
            "invalid_structure_dropped": 2,
            "total_dropped": 3,
        }
    )

    accumulator.add_postprocess_quality_drop(4)
    accumulator.add_postprocess_quality_drop(0)

    assert accumulator.low_quality_structural_dropped_total == 2
    assert accumulator.low_quality_dropped_total == 6
    assert accumulator.postprocess_filter_drop_total == 7


def test_low_quality_filter_stats_accumulator_preserves_quality_drop_after_later_batch() -> None:
    accumulator = LowQualityFilterStatsAccumulator({"invalid_structure_dropped": 2, "total_dropped": 2})

    accumulator.add_postprocess_quality_drop(4)
    accumulator.accumulate({"weak_case_dropped": 3, "total_dropped": 3})

    assert accumulator.low_quality_structural_dropped_total == 5
    assert accumulator.final_quality_dropped_total == 4
    assert accumulator.low_quality_dropped_total == 9
    assert accumulator.postprocess_filter_drop_total == 9


def test_resolve_generation_coverage_profile_uses_expected_count_floor() -> None:
    profile = resolve_generation_coverage_profile(
        expected_count=65,
        generation_mode="",
        generation_coverage_mode="standard_regression",
    )

    assert profile["expected_count_value"] == 65
    assert profile["effective_generation_coverage_mode"] == "expanded_regression"
    assert profile["effective_generation_coverage_mode_source"] == "expected_count"
    assert profile["explicit_generation_mode_override"] is False
    assert profile["explicit_expected_count_floor_preserved"] is False


def test_resolve_generation_coverage_profile_preserves_explicit_full_regression_floor() -> None:
    profile = resolve_generation_coverage_profile(
        expected_count=50,
        generation_mode="full_functional_regression",
        generation_coverage_mode="standard_regression",
    )

    assert profile["expected_count_value"] == 50
    assert profile["effective_generation_coverage_mode"] == "full_functional_regression"
    assert profile["effective_generation_coverage_mode_source"] == "generation_mode"
    assert profile["explicit_generation_mode_override"] is True
    assert profile["explicit_expected_count_floor_preserved"] is True


def test_resolve_generation_coverage_profile_falls_back_when_mode_is_unknown() -> None:
    profile = resolve_generation_coverage_profile(
        expected_count="not-a-number",
        generation_mode="unknown",
        generation_coverage_mode="legacy_mode",
    )

    assert profile["expected_count_value"] == 0
    assert profile["effective_generation_coverage_mode"] == "standard_regression"
    assert profile["effective_generation_coverage_mode_source"] == "fallback"
    assert profile["explicit_generation_mode_override"] is False


def test_resolve_generation_coverage_state_uses_expected_count_80_full_regression() -> None:
    state = resolve_generation_coverage_state(
        expected_count=80,
        generation_mode="",
        generation_coverage_mode="standard_regression",
        generation_target_case_range={"min": 20, "max": 120},
    )

    assert state["coverage_profile"]["expected_count_value"] == 80
    assert state["expected_count_value"] == 80
    assert state["effective_generation_coverage_mode"] == "full_functional_regression"
    assert state["effective_generation_coverage_mode_source"] == "expected_count"
    assert state["generation_coverage_mode"] == "full_functional_regression"
    assert state["explicit_generation_mode_override"] is False
    assert state["explicit_expected_count_floor_preserved"] is True
    assert state["full_regression_recommended_floor"] == 85
    assert state["resolved_full_regression_floor"] == 80


def test_resolve_generation_coverage_state_preserves_explicit_full_regression_mode() -> None:
    state = resolve_generation_coverage_state(
        expected_count=50,
        generation_mode="full_functional_regression",
        generation_coverage_mode="standard_regression",
        generation_target_case_range={"min": 40},
    )

    assert state["effective_generation_coverage_mode"] == "full_functional_regression"
    assert state["effective_generation_coverage_mode_source"] == "generation_mode"
    assert state["explicit_generation_mode_override"] is True
    assert state["explicit_expected_count_floor_preserved"] is True
    assert state["resolved_full_regression_floor"] == 50


def test_resolve_generation_coverage_state_falls_back_when_mode_is_unknown() -> None:
    state = resolve_generation_coverage_state(
        expected_count="bad",
        generation_mode="unknown_mode",
        generation_coverage_mode="legacy_mode",
        generation_target_case_range={"min": 0},
    )

    assert state["expected_count_value"] == 0
    assert state["effective_generation_coverage_mode"] == "standard_regression"
    assert state["effective_generation_coverage_mode_source"] == "fallback"
    assert state["generation_coverage_mode"] == "standard_regression"
    assert state["explicit_generation_mode_override"] is False
    assert state["explicit_expected_count_floor_preserved"] is False
    assert state["resolved_full_regression_floor"] == 85


def test_resolve_generation_coverage_state_raises_floor_from_generation_target_range() -> None:
    state = resolve_generation_coverage_state(
        expected_count=90,
        generation_mode="",
        generation_coverage_mode="expanded_regression",
        generation_target_case_range={"min": 96, "max": 120},
    )

    assert state["effective_generation_coverage_mode"] == "full_functional_regression"
    assert state["full_regression_recommended_floor"] == 85
    assert state["explicit_expected_count_floor_preserved"] is False
    assert state["resolved_full_regression_floor"] == 96


def test_build_flow_project_profile_for_governance_applies_expanded_mode_policy() -> None:
    profile = build_flow_project_profile_for_governance(
        {"scenario_cluster_policy": {"scenario_caps": {"existing": 3}}},
        generation_coverage_mode="expanded_regression",
    )

    policy = profile["scenario_cluster_policy"]
    assert policy["coverage_mode"] == "expanded_regression"
    assert policy["intent_duplicate_cap"] == 1
    assert policy["strict_duplicate_policy"] is True
    assert policy["scenario_caps"] == {"existing": 3}


def test_build_flow_project_profile_for_governance_merges_feedback_caps_without_mutating_input() -> None:
    original = {"scenario_cluster_policy": {"scenario_caps": {"same": 5, "lower": 1}}}

    profile = build_flow_project_profile_for_governance(
        original,
        generation_coverage_mode="full_functional_regression",
        feedback_redundant_caps={"same": 2, "lower": 4, "new": "bad", "": 9},
    )

    assert original == {"scenario_cluster_policy": {"scenario_caps": {"same": 5, "lower": 1}}}
    policy = profile["scenario_cluster_policy"]
    assert policy["coverage_mode"] == "full_functional_regression"
    assert policy["strict_duplicate_policy"] is False
    assert policy["feedback_redundant_caps_applied"] is True
    assert policy["scenario_caps"] == {"same": 2, "lower": 1, "new": 1}


def test_build_feedback_control_debug_payload_uses_control_and_profiles() -> None:
    control_state = FeedbackControlState(
        must_cover_rules=["RULE-PAYMENT", "RULE-REFUND"],
        forbidden_patterns=["display-only assertion"],
        preferred_patterns=["verify persisted payment status"],
        soft_constraints=["preserve main flow"],
        rule_quota={"RULE-REFUND": 2, "RULE-PAYMENT": 1},
        quality_fix_hints=["assert downstream refund ledger"],
        source_meta={"source": "final_case_learning"},
    )

    payload = build_feedback_control_debug_payload(
        control_state=control_state,
        generation_coverage_mode="expanded_regression",
        generation_target_case_range={"min": 20, "max": 35},
        fact_profile={
            "profile_source": "requirement_fact_profile",
            "confidence": 0.86,
            "confirmed_facts": ["payment status is persisted"],
            "forbidden_facts": ["skip refund audit"],
            "pending_items": ["third-party callback timeout"],
        },
        project_profile={
            "profile_source": "workflow_blueprint",
            "confidence": 0.91,
            "flow_outline": {"flow_order": ["submit", "pay", "refund"]},
        },
        manual_quality_profile={
            "profile_source": "trusted_final_cases",
            "profile_version": "2026-06",
            "trusted_sample_count": 12,
            "high_priority_ratio": 0.72,
            "display_ratio_cap": 0.18,
        },
    )

    assert payload["control_state_applied"] is True
    assert payload["generation_coverage_mode"] == "expanded_regression"
    assert payload["generation_target_case_range"] == {"min": 20, "max": 35}
    assert payload["fact_profile_source"] == "requirement_fact_profile"
    assert payload["fact_profile_confirmed_count"] == 1
    assert payload["fact_profile_forbidden_count"] == 1
    assert payload["fact_profile_pending_count"] == 1
    assert payload["project_profile_source"] == "workflow_blueprint"
    assert payload["project_profile_flow_count"] == 3
    assert payload["manual_quality_profile_source"] == "trusted_final_cases"
    assert payload["manual_quality_profile_version"] == "2026-06"
    assert payload["manual_quality_profile_trusted_count"] == 12
    assert payload["manual_quality_profile_high_priority_ratio"] == 0.72
    assert payload["manual_quality_profile_display_ratio_cap"] == 0.18
    assert payload["must_cover_rules_count"] == 2
    assert payload["rule_quota_keys"] == ["RULE-PAYMENT", "RULE-REFUND"]
    assert payload["soft_constraints_count"] == 1
    assert payload["quality_fix_hints_count"] == 1
    assert payload["preferred_patterns_count"] == 1
    assert payload["forbidden_patterns_count"] == 1
    assert payload["source_meta"] == {"source": "final_case_learning"}


def test_build_feedback_control_debug_payload_defaults_empty_profiles() -> None:
    payload = build_feedback_control_debug_payload(
        control_state=FeedbackControlState.empty(),
        generation_coverage_mode="",
        generation_target_case_range=None,
        fact_profile=None,
        project_profile=None,
        manual_quality_profile=None,
    )

    assert payload["control_state_applied"] is False
    assert payload["generation_coverage_mode"] == "core_smoke"
    assert payload["generation_target_case_range"] == {}
    assert payload["fact_profile_source"] == ""
    assert payload["fact_profile_confidence"] == 0.0
    assert payload["project_profile_source"] == ""
    assert payload["project_profile_confidence"] == 0.0
    assert payload["project_profile_flow_count"] == 0
    assert payload["manual_quality_profile_source"] == ""
    assert payload["manual_quality_profile_trusted_count"] == 0
    assert payload["manual_quality_profile_high_priority_ratio"] == 0.0
    assert payload["manual_quality_profile_display_ratio_cap"] == 0.0
    assert payload["must_cover_rules_count"] == 0
    assert payload["rule_quota_keys"] == []
    assert payload["source_meta"] == {}
