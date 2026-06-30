from __future__ import annotations

from modules.test_generation_components.postprocess.case_access import (
    case_text_field,
)
from modules.test_generation_components.postprocess.streaming_case_keys import (
    case_focus_score,
    case_signature,
    review_case_id,
)
from modules.test_generation_components.postprocess.streaming_review_decision_table import (
    build_review_candidate_row_base_fields,
    build_review_candidate_row_diagnostic_fields,
    build_review_decision_table_context,
    resolve_review_candidate_drop_decision,
    resolve_review_priority_fields,
    resolve_review_priority_summary_flags,
    resolve_review_row_coverage_retention_fields,
)


def _case(case_id: str, *, priority: str = "P1") -> dict[str, str]:
    return {
        "id": case_id,
        "test_module": "course",
        "description": f"case {case_id}",
        "expected_result": f"{case_id} succeeds",
        "test_input": f"input {case_id}",
        "priority": priority,
    }


def test_build_review_decision_table_context_filters_non_dict_rows_and_cases() -> None:
    selected = _case("TC-001")
    final = _case("TC-002", priority="P0")

    context = build_review_decision_table_context(
        review_selection_input=[selected, "not a case", 123],
        review_gate_trace={},
        parsed_result=["not a final case", final],
        review_case_structure={
            "rows": [
                "not a row",
                {"candidate_index": "2", "structure_marker": "state-chain"},
            ],
        },
    )

    final_signature = case_signature(final)
    assert context.selection_signatures == {case_signature(selected)}
    assert context.final_signatures == {final_signature}
    assert context.final_priority_by_signature == {final_signature: "P0"}
    assert context.structure_rows_by_index == {
        2: {"candidate_index": "2", "structure_marker": "state-chain"}
    }


def test_build_review_decision_table_context_normalizes_trace_signature_sets() -> None:
    selected = _case("TC-SELECTED")
    dropped = _case("TC-DROPPED")
    selected_signature = case_signature(selected)
    dropped_signature = case_signature(dropped)

    context = build_review_decision_table_context(
        review_selection_input=[selected, dropped],
        review_gate_trace={
            "decisions": {
                selected_signature: {"bucket": "course|happy"},
            },
            "selected_signatures": [selected_signature, selected_signature, 101],
            "dedup_dropped_signatures": [dropped_signature, 202, dropped_signature],
        },
        parsed_result=[selected],
        review_case_structure={"rows": []},
    )

    assert context.trace_decisions == {selected_signature: {"bucket": "course|happy"}}
    assert context.selected_gate_signatures == {selected_signature, "101"}
    assert context.dedup_drop_signatures == {dropped_signature, "202"}


def test_build_review_candidate_row_base_fields_defaults_unknown_stage_and_scores() -> None:
    case = {
        **_case("TC-BASE"),
        "invalid_case_signals": [101, "missing_assert"],
    }

    row = build_review_candidate_row_base_fields(
        index=3,
        case=case,
        signature=case_signature(case),
        structure_row={},
        gate_info={},
        rule_keys=["RULE_A"],
        bucket="course|submit",
        adds_rule=False,
        adds_bucket=True,
        high_signal=False,
        has_coverage_value=True,
        retained_reason="retained_due_to_coverage_value",
        review_case_id_fn=review_case_id,
        case_text_field_fn=case_text_field,
        focus_score_fn=case_focus_score,
    )

    assert row["candidate_index"] == 3
    assert row["case_id"] == "TC-BASE"
    assert row["flow_stage"] == "unknown"
    assert row["flow_stage_label"] == "unknown"
    assert row["invalid_case_signals"] == ["101", "missing_assert"]
    assert row["rule_keys"] == ["RULE_A"]
    assert row["bucket"] == "course|submit"
    assert row["adds_bucket"] is True
    assert row["has_coverage_value"] is True
    assert row["retained_reason"] == "retained_due_to_coverage_value"
    assert row["rerank_rank"] == 0
    assert row["focus_score"] == 0


def test_build_review_candidate_row_base_fields_preserves_structure_values() -> None:
    case = {
        **_case("TC-STRUCTURE"),
        "expected_result_quality": "assertable",
        "case_quality": "needs_review",
        "invalid_case_signals": [404],
    }

    row = build_review_candidate_row_base_fields(
        index=5,
        case=case,
        signature="sig-structure",
        structure_row={
            "flow_stage": "submit",
            "flow_stage_label": "提交作业",
            "flow_rank": 2,
            "cross_cutting": ["auth", 9],
            "scenario_key": "course.submit",
            "is_scenario_duplicate": True,
            "duplicate_cluster_id": 12,
            "duplicate_cluster_size": "3",
            "duplicate_of_case_id": 88,
            "misordered_against_requirement_flow": True,
        },
        gate_info={"rank": "4", "focus_score": "11"},
        rule_keys=["RULE_B"],
        bucket="course|review",
        adds_rule=True,
        adds_bucket=False,
        high_signal=True,
        has_coverage_value=False,
        retained_reason="",
        review_case_id_fn=review_case_id,
        case_text_field_fn=case_text_field,
        focus_score_fn=case_focus_score,
    )

    assert row["signature"] == "sig-structure"
    assert row["flow_stage"] == "submit"
    assert row["flow_stage_label"] == "提交作业"
    assert row["flow_rank"] == 2
    assert row["cross_cutting"] == ["auth", "9"]
    assert row["scenario_key"] == "course.submit"
    assert row["is_scenario_duplicate"] is True
    assert row["duplicate_cluster_id"] == "12"
    assert row["duplicate_cluster_size"] == 3
    assert row["duplicate_of_case_id"] == "88"
    assert row["misordered_against_requirement_flow"] is True
    assert row["expected_result_quality"] == "assertable"
    assert row["case_quality"] == "needs_review"
    assert row["invalid_case_signals"] == ["404"]
    assert row["rerank_rank"] == 4
    assert row["focus_score"] == 11


def test_build_review_candidate_row_diagnostic_fields_normalizes_values() -> None:
    signature = "sig-review-candidate"
    evidence = {
        "has_positive_evidence": True,
        "source": "review_llm",
    }

    fields = build_review_candidate_row_diagnostic_fields(
        model_priority_value="P0",
        legacy_priority_value="P1",
        priority_final_value="P0",
        priority_decision_state_value="decided",
        priority_decision_source_value="semantic_priority_resolve",
        priority_confidence_value="high",
        priority_conflict_reason_value="",
        priority_resolution_reason_value="stable_main_chain",
        score_profile={
            "priority_score": "7",
            "suggested_priority": " p1 ",
            "reasons": ["main_chain", "", "  ", 202],
            "covered_rule_ids": ["RULE_SUBMIT", 303],
            "missing_rule_hits": ["MUST_COVER_REVIEW", 404],
            "core_rule_hits": ["CORE_LOGIN", 505],
            "coverage_gain_score": "3",
            "reuse_risk_hit": 1,
        },
        selected_by_review_llm=1,
        selected_by_review_must_keep=True,
        selected_by_review_constraints=False,
        review_constraint_reason="",
        review_llm_drop_reason_raw="raw low value",
        review_llm_drop_reason="duplicate_intent",
        review_llm_drop_reason_source="model",
        review_llm_drop_reason_evidence=evidence,
        has_positive_evidence=True,
        has_coverage_signal=False,
        has_high_signal=True,
        has_competition_signal=False,
        review_llm_applied=True,
        signature=signature,
        review_must_keep_signatures={signature},
        review_must_keep_reason_map={signature: ["main_path", "must_cover_rule"]},
        selected_gate_signatures=set(),
        retained=False,
        dropped_stage="review_llm",
        dropped_reason="drop_not_selected_by_review_llm:duplicate_intent",
        hit_must_cover_rule=True,
        violates_forbidden_pattern=False,
        hits_soft_constraint=True,
        satisfies_quality_hint=False,
    )

    assert fields["model_priority_current"] == "P0"
    assert fields["model_priority"] == "P0"
    assert fields["legacy_priority"] == "P1"
    assert fields["priority_final"] == "P0"
    assert fields["priority_score"] == 7
    assert fields["suggested_priority"] == "P1"
    assert fields["priority_reasons"] == ["main_chain", "202"]
    assert fields["selected_by_review_llm"] is True
    assert fields["selected_by_review_must_keep"] is True
    assert fields["selected_by_review_constraints"] is False
    assert fields["review_llm_drop_reason_resolved"] == "duplicate_intent"
    assert fields["review_llm_drop_reason_evidence"] == evidence
    assert fields["has_positive_evidence"] is True
    assert fields["has_coverage_signal"] is False
    assert fields["has_high_signal"] is True
    assert fields["has_competition_signal"] is False
    assert fields["review_llm_filter_applied"] is True
    assert fields["must_keep_candidate"] is True
    assert fields["must_keep_reasons"] == ["main_path", "must_cover_rule"]
    assert fields["selected_by_review_gate"] is False
    assert fields["retained_final"] is False
    assert fields["dropped_stage"] == "review_llm"
    assert fields["dropped_reason"] == "drop_not_selected_by_review_llm:duplicate_intent"
    assert fields["covered_rule_ids"] == ["RULE_SUBMIT", "303"]
    assert fields["missing_rule_hits"] == ["MUST_COVER_REVIEW", "404"]
    assert fields["core_rule_hits"] == ["CORE_LOGIN", "505"]
    assert fields["coverage_gain_score"] == 3
    assert fields["reuse_risk_hit"] is True
    assert fields["hit_must_cover_rule"] is True
    assert fields["violates_forbidden_pattern"] is False
    assert fields["hits_soft_constraint"] is True
    assert fields["satisfies_quality_hint"] is False


def test_resolve_review_priority_fields_reflects_retained_final_priority_map() -> None:
    case = _case("TC-RETAINED", priority="P2")
    signature = case_signature(case)

    fields = resolve_review_priority_fields(
        case=case,
        signature=signature,
        retained=True,
        final_priority_by_signature={signature: "p0"},
    )

    assert fields.priority_final_value == "P0"
    assert fields.priority_decision_state_value == "decided"
    assert fields.priority_decision_source_value == "execution_plan_final_priority"
    assert fields.priority_resolution_reason_value == "priority_final_reflected_from_execution_plan"


def test_resolve_review_priority_fields_unknown_state_with_final_priority_becomes_decided() -> None:
    case = {
        **_case("TC-UNKNOWN-STATE", priority="P2"),
        "priority_final": "p1",
        "priority_decision_state": "model_reviewed",
        "priority_decision_source": "semantic_priority_resolve",
    }

    fields = resolve_review_priority_fields(
        case=case,
        signature=case_signature(case),
        retained=False,
        final_priority_by_signature={},
    )

    assert fields.priority_final_value == "P1"
    assert fields.priority_decision_state_value == "decided"
    assert fields.priority_decision_source_value == "semantic_priority_resolve"


def test_resolve_review_priority_fields_backfills_missing_final_from_valid_legacy_priority() -> None:
    case = {
        **_case("TC-LEGACY-BACKFILL", priority="P2"),
        "priority_decision_state": "decided",
    }

    fields = resolve_review_priority_fields(
        case=case,
        signature=case_signature(case),
        retained=False,
        final_priority_by_signature={},
    )

    assert fields.priority_final_value == "P2"
    assert fields.priority_decision_state_value == "decided"
    assert fields.priority_resolution_reason_value == "priority_final_backfilled_from_legacy_priority"


def test_resolve_review_priority_fields_marks_unfillable_missing_final_invalid() -> None:
    case = {
        **_case("TC-MISSING-FINAL", priority="PX"),
        "priority_decision_state": "decided",
    }

    fields = resolve_review_priority_fields(
        case=case,
        signature=case_signature(case),
        retained=False,
        final_priority_by_signature={},
    )

    assert fields.priority_final_value == ""
    assert fields.priority_decision_state_value == "invalid"
    assert fields.priority_decision_source_value == "priority_final_missing_after_semantic_resolve"
    assert fields.priority_resolution_reason_value == "missing_priority_final_after_semantic_resolve"


def test_resolve_review_priority_summary_flags_keeps_true_summary_flag() -> None:
    flags = resolve_review_priority_summary_flags(
        {
            "needs_priority_review": True,
            "priority_conflict_count": 0,
            "priority_undetermined_count": 0,
            "priority_optional_count": 0,
        }
    )

    assert flags.priority_conflict_count == 0
    assert flags.priority_undetermined_count == 0
    assert flags.priority_optional_count == 0
    assert flags.needs_priority_review is True


def test_resolve_review_priority_summary_flags_conflict_or_undetermined_trigger_review() -> None:
    conflict_flags = resolve_review_priority_summary_flags(
        {
            "needs_priority_review": False,
            "priority_conflict_count": 2,
            "priority_undetermined_count": 0,
            "priority_optional_count": 0,
        }
    )
    undetermined_flags = resolve_review_priority_summary_flags(
        {
            "needs_priority_review": False,
            "priority_conflict_count": 0,
            "priority_undetermined_count": 1,
            "priority_optional_count": 0,
        }
    )

    assert conflict_flags.needs_priority_review is True
    assert undetermined_flags.needs_priority_review is True


def test_resolve_review_priority_summary_flags_optional_does_not_trigger_review() -> None:
    flags = resolve_review_priority_summary_flags(
        {
            "needs_priority_review": False,
            "priority_conflict_count": 0,
            "priority_undetermined_count": 0,
            "priority_optional_count": 3,
        }
    )

    assert flags.priority_optional_count == 3
    assert flags.needs_priority_review is False


def test_resolve_review_priority_summary_flags_missing_fields_default_empty() -> None:
    flags = resolve_review_priority_summary_flags({})

    assert flags.priority_conflict_count == 0
    assert flags.priority_undetermined_count == 0
    assert flags.priority_optional_count == 0
    assert flags.needs_priority_review is False


def _drop_decision(
    signature: str = "sig-a",
    **overrides,
):
    defaults = {
        "signature": signature,
        "review_llm_applied": False,
        "review_llm_selected_signatures": set(),
        "review_must_keep_signatures": set(),
        "review_constraint_retained_signatures": set(),
        "review_constraint_reason_map": {},
        "review_llm_drop_reason_raw_map": {},
        "review_llm_drop_reason_map": {},
        "review_llm_drop_reason_source_map": {},
        "review_llm_drop_reason_evidence_map": {},
        "selection_signatures": {signature},
        "append_cap_drop_signatures": set(),
        "final_description_dedup_drop_signatures": set(),
        "dedup_drop_signatures": set(),
        "selected_gate_signatures": {signature},
        "final_signatures": {signature},
        "gate_reason": "",
    }
    defaults.update(overrides)
    return resolve_review_candidate_drop_decision(**defaults)


def test_resolve_review_candidate_drop_decision_prefers_review_llm_drop() -> None:
    decision = _drop_decision(
        review_llm_applied=True,
        review_llm_selected_signatures=set(),
        review_llm_drop_reason_raw_map={"sig-a": "raw duplicate"},
        review_llm_drop_reason_map={"sig-a": "duplicate_intent"},
        review_llm_drop_reason_source_map={"sig-a": "model"},
        review_llm_drop_reason_evidence_map={"sig-a": {"has_coverage_signal": True}},
        selection_signatures={"sig-a"},
        append_cap_drop_signatures={"sig-a"},
        final_signatures=set(),
    )

    assert decision.dropped_stage == "review_llm"
    assert decision.dropped_reason == "drop_not_selected_by_review_llm:duplicate_intent"
    assert decision.selected_by_review_llm is False
    assert decision.review_llm_drop_reason_raw == "raw duplicate"
    assert decision.review_llm_drop_reason_source == "model"
    assert decision.has_positive_evidence is True
    assert decision.has_coverage_signal is True


def test_resolve_review_candidate_drop_decision_uses_selector_target_window_reason() -> None:
    decision = _drop_decision(
        review_llm_applied=True,
        review_llm_selected_signatures={"sig-a"},
        review_constraint_reason_map={"sig-a": "dropped_by_target_max"},
        selection_signatures=set(),
        final_signatures=set(),
    )

    assert decision.dropped_stage == "review_selector"
    assert decision.dropped_reason == "drop_outside_target_window"
    assert decision.selected_by_review_llm is True
    assert decision.review_constraint_reason == "dropped_by_target_max"


def test_resolve_review_candidate_drop_decision_marks_append_cap_before_late_dedup() -> None:
    decision = _drop_decision(
        append_cap_drop_signatures={"sig-a"},
        final_description_dedup_drop_signatures={"sig-a"},
        selected_gate_signatures={"sig-a"},
        final_signatures=set(),
    )

    assert decision.dropped_stage == "append_target_cap"
    assert decision.dropped_reason == "drop_exceeds_append_target_count"


def test_resolve_review_candidate_drop_decision_marks_final_description_duplicate() -> None:
    decision = _drop_decision(
        final_description_dedup_drop_signatures={"sig-a"},
        selected_gate_signatures={"sig-a"},
        final_signatures=set(),
    )

    assert decision.dropped_stage == "post_review_dedup_or_reorder"
    assert decision.dropped_reason == "drop_final_description_duplicate"


def test_resolve_review_candidate_drop_decision_marks_review_gate_before_post_gate_drop() -> None:
    decision = _drop_decision(
        selected_gate_signatures=set(),
        final_signatures=set(),
        gate_reason="drop_low_business_value",
    )

    assert decision.dropped_stage == "review_gate"
    assert decision.dropped_reason == "drop_low_business_value"


def test_resolve_review_candidate_drop_decision_marks_retained_row() -> None:
    decision = _drop_decision(
        review_llm_applied=False,
        selected_gate_signatures={"sig-a"},
        final_signatures={"sig-a"},
        review_llm_drop_reason_evidence_map={"sig-a": "not-a-dict"},
    )

    assert decision.dropped_stage == "retained"
    assert decision.dropped_reason == "retained"
    assert decision.selected_by_review_llm is True
    assert decision.review_llm_drop_reason_evidence == {}


def test_resolve_review_row_coverage_retention_fields_backfills_coverage_reason() -> None:
    fields = resolve_review_row_coverage_retention_fields(
        gate_info={},
        score_profile={
            "missing_rule_hits": ["MUST_COVER_SUBMIT"],
            "core_rule_hits": [],
            "unique_coverage_hits": [],
            "coverage_gain_score": 0,
            "reuse_risk_hit": False,
        },
        retained=True,
        adds_rule=False,
        adds_bucket=False,
    )

    assert fields.has_coverage_value_for_row is True
    assert fields.retained_reason_value == "retained_due_to_coverage_value"


def test_resolve_review_row_coverage_retention_fields_keeps_existing_and_reuse_risk() -> None:
    existing = resolve_review_row_coverage_retention_fields(
        gate_info={"has_coverage_value": True, "retained_reason": "retained_by_gate"},
        score_profile={"reuse_risk_hit": False},
        retained=True,
        adds_rule=False,
        adds_bucket=False,
    )
    risky = resolve_review_row_coverage_retention_fields(
        gate_info={},
        score_profile={"coverage_gain_score": 4, "reuse_risk_hit": True},
        retained=True,
        adds_rule=False,
        adds_bucket=False,
    )

    assert existing.has_coverage_value_for_row is True
    assert existing.retained_reason_value == "retained_by_gate"
    assert risky.has_coverage_value_for_row is True
    assert risky.retained_reason_value == ""
