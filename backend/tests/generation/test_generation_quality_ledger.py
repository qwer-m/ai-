from __future__ import annotations

from modules.test_generation_components.services.final_case_sample_learning import _compact_quality_ledger
from modules.testing.test_generation_components.legacy.stream.persist import _build_quality_ledger_payload


def test_quality_ledger_compacts_generation_evidence() -> None:
    payload = _build_quality_ledger_payload(
        generation_id=460,
        request_id="req-1",
        mode="multi_pass",
        stage_counts={"primary": 100, "gap": 0, "review": 83},
        coverage_payload={
            "coverage_rate": 0.9823,
            "total_rules": 113,
            "total_extracted_rules": 120,
            "missing_rules": ["RULE-010", "RULE-074"],
            "missing_types": {"boundary": [], "exception": []},
            "non_blocking_rules": ["RULE-052"],
        },
        convergence_payload={
            "final_count": 83,
            "candidate_count_before_review": 100,
            "review_selected_count": 83,
            "post_review_dedup_drop": 8,
            "low_quality_dropped_count": 0,
            "low_quality_dropped_examples": [
                {
                    "stage": "post_judge_quality_filter",
                    "reason": "non_assertable_expected_result",
                    "case_id": "TC-009",
                    "description": "weak expected result",
                }
            ],
            "semantic_dedup_dropped_count": 8,
        },
        generation_summary_payload={
            "final_count": 83,
            "quality_assessment": "high",
            "stop_reason": ["coverage_satisfied"],
        },
        review_decision_summary_payload={
            "candidate_total": 100,
            "retained_total": 83,
            "drop_by_review_llm_count": 9,
            "drop_by_review_gate_count": 0,
            "drop_by_post_review_dedup_count": 8,
        },
        judge_summary_payload={"total": 11, "rejected_out_count": 0, "pending_out_count": 0},
        feedback_control_debug_payload={
            "control_state_applied": True,
            "generation_coverage_mode": "full_functional_regression",
            "must_cover_rules_count": 3,
            "quality_fix_hints_count": 2,
            "source_meta": {
                "requirement_semantic_contract": {
                    "semantic_contract_version": "requirement-semantic-v1",
                    "functional_architecture": {
                        "functional_modules": [
                            {"module_key": "forum", "module_name": "Forum"}
                        ],
                        "module_interactions": [],
                    },
                    "workflow_blueprints": [],
                    "workflow_absence_declared": True,
                }
            },
        },
        compression_diag_payload={"compression_ratio": 0.2, "retained_chunk_count": 1},
        context_result={
            "context_debug": {
                "snapshot_status": "success",
                "snapshot_used": True,
                "realtime_rag_used": True,
                "current_document_used": True,
            },
            "fusion_debug": {"mode": "snapshot+rag"},
        },
    )

    assert payload["kind"] == "generation_quality_ledger"
    assert payload["final_count"] == 83
    assert payload["initial_quality_score"] == payload["quality_score"]
    assert payload["quality_score_source"] == "backend_diagnostic_v1"
    assert payload["quality_score_basis"] == "coverage+review+judge+funnel+context"
    assert payload["quality_score_inputs"]["missing_rules_count"] == 2
    assert any(item["key"] == "missing_rules" for item in payload["quality_score_deductions"])
    assert payload["coverage"]["missing_rules_count"] == 2
    assert payload["coverage"]["non_blocking_rules_count"] == 1
    assert payload["review"]["drop_by_review_llm_count"] == 9
    assert payload["funnel"]["low_quality_dropped_examples"][0]["case_id"] == "TC-009"
    assert payload["funnel"]["low_quality_dropped_examples"][0]["reason"] == "non_assertable_expected_result"
    assert payload["context"]["snapshot_used"] is True
    assert payload["context"]["realtime_rag_used"] is True
    assert payload["context"]["current_document_used"] is True
    assert payload["context"]["fusion_mode"] == "snapshot+rag"
    assert payload["control"]["generation_coverage_mode"] == "full_functional_regression"
    assert payload["control"]["requirement_semantic_contract"]["semantic_contract_version"] == (
        "requirement-semantic-v1"
    )


def test_quality_ledger_uses_context_source_when_fusion_mode_missing() -> None:
    payload = _build_quality_ledger_payload(
        generation_id=461,
        request_id="req-2",
        mode="multi_pass",
        stage_counts={"primary": 10, "gap": 0, "review": 8},
        coverage_payload={"coverage_rate": 1.0, "total_rules": 1, "missing_rules": [], "missing_types": {}},
        convergence_payload={"final_count": 8},
        generation_summary_payload={"final_count": 8, "quality_assessment": "medium", "stop_reason": []},
        review_decision_summary_payload={},
        judge_summary_payload={},
        feedback_control_debug_payload={},
        compression_diag_payload={"context_source": "rag_only", "compression_ratio": 0.3, "retained_chunk_count": 1},
        context_result={
            "context_source": "rag_only",
            "context_debug": {
                "snapshot_status": "stale",
                "snapshot_used": False,
                "realtime_rag_used": True,
                "current_document_used": True,
            },
            "fusion_debug": {},
        },
    )

    assert payload["context"]["snapshot_status"] == "stale"
    assert payload["context"]["snapshot_used"] is False
    assert payload["context"]["realtime_rag_used"] is True
    assert payload["context"]["current_document_used"] is True
    assert payload["context"]["fusion_mode"] == "rag_only"
    assert payload["initial_quality_score"] == 100
    assert payload["quality_score_grade"] == "high"


def test_quality_ledger_keeps_low_quality_count_and_examples_together() -> None:
    payload = _build_quality_ledger_payload(
        generation_id=462,
        request_id="req-3",
        mode="multi_pass",
        stage_counts={},
        coverage_payload={"coverage_rate": 1.0, "total_rules": 0, "missing_rules": [], "missing_types": {}},
        convergence_payload={
            "final_count": 12,
            "low_quality_dropped_count": 2,
            "low_quality_dropped_examples": [
                {"case_id": "TC-001", "reason": "non_assertable_expected_result"},
                {"case_id": "TC-002", "reason": "truncated_text"},
            ],
        },
        generation_summary_payload={"final_count": 12, "quality_assessment": "low", "stop_reason": []},
        review_decision_summary_payload={},
        judge_summary_payload={},
        feedback_control_debug_payload={},
        compression_diag_payload={},
        context_result={},
    )

    assert payload["funnel"]["low_quality_dropped_count"] == 2
    assert len(payload["funnel"]["low_quality_dropped_examples"]) == 2
    assert any(item["key"] == "low_quality_dropped" for item in payload["quality_score_deductions"])


def test_quality_ledger_assessment_follows_score_grade_not_stale_summary() -> None:
    payload = _build_quality_ledger_payload(
        generation_id=502,
        request_id="req-stale-summary",
        mode="multi_pass",
        stage_counts={"primary": 67, "gap": 0, "review": 67},
        coverage_payload={"coverage_rate": 1.0, "total_rules": 64, "missing_rules": [], "missing_types": {}},
        convergence_payload={
            "final_count": 84,
            "low_quality_dropped_count": 5,
            "low_quality_dropped_examples": [
                {"case_id": "TC-029", "reason": "non_assertable_expected_result"}
            ],
        },
        generation_summary_payload={
            "final_count": 84,
            "min_acceptable_final": 80,
            "quality_assessment": "low",
            "stop_reason": ["coverage_satisfied"],
        },
        review_decision_summary_payload={
            "candidate_total": 67,
            "retained_total": 59,
            "final_flow_misordered_count": 0,
            "final_flow_missing_stage_count": 0,
            "final_scenario_duplicate_case_count": 0,
            "final_scenario_duplicate_cluster_count": 0,
            "final_reasoning_leakage_case_count": 0,
            "fact_profile_pending_count": 1,
        },
        judge_summary_payload={"total": 69, "rejected_out_count": 1, "pending_out_count": 1},
        feedback_control_debug_payload={"control_state_applied": True},
        compression_diag_payload={},
        context_result={"context_debug": {"current_document_used": True, "realtime_rag_used": True}},
        judge_decision_table_payload=[
            {"case_id": "TC-003", "status": "REJECT", "reject_reason": "semantic_duplicate:TC-001"},
            {"case_id": "TC-032", "status": "PENDING", "reject_reason": "contains_pending_logic"},
        ],
    )

    assert payload["quality_score"] == 82
    assert payload["quality_score_grade"] == "medium"
    assert payload["quality_assessment"] == "medium"


def test_quality_ledger_score_penalizes_real_diagnostic_risks() -> None:
    payload = _build_quality_ledger_payload(
        generation_id=463,
        request_id="req-4",
        mode="multi_pass",
        stage_counts={},
        coverage_payload={"coverage_rate": 1.0, "total_rules": 22, "missing_rules": [], "missing_types": {}},
        convergence_payload={"final_count": 85, "semantic_dedup_dropped_count": 30},
        generation_summary_payload={"final_count": 85, "quality_assessment": "low", "stop_reason": []},
        review_decision_summary_payload={
            "candidate_total": 131,
            "retained_total": 85,
            "flow_misordered_count": 106,
            "scenario_duplicate_cluster_count": 45,
            "scenario_duplicate_case_count": 91,
        },
        judge_summary_payload={"total": 118, "rejected_out_count": 16, "pending_out_count": 5, "repairable_count": 1},
        feedback_control_debug_payload={"control_state_applied": True},
        compression_diag_payload={},
        context_result={"context_debug": {"current_document_used": True, "realtime_rag_used": True}},
    )

    assert payload["coverage"]["coverage_rate"] == 1.0
    assert payload["initial_quality_score"] < 50
    assert payload["quality_score_grade"] == "critical"
    deduction_keys = {item["key"] for item in payload["quality_score_deductions"]}
    assert {"flow_misordered", "scenario_duplicates", "judge_rejected", "judge_pending"}.issubset(
        deduction_keys
    )


def test_quality_ledger_scores_final_structure_instead_of_candidate_noise() -> None:
    payload = _build_quality_ledger_payload(
        generation_id=464,
        request_id="req-5",
        mode="multi_pass",
        stage_counts={},
        coverage_payload={"coverage_rate": 1.0, "total_rules": 22, "missing_rules": [], "missing_types": {}},
        convergence_payload={"final_count": 104},
        generation_summary_payload={
            "final_count": 104,
            "min_acceptable_final": 104,
            "quality_assessment": "medium",
            "stop_reason": [],
        },
        review_decision_summary_payload={
            "candidate_total": 149,
            "retained_total": 104,
            "flow_misordered_count": 98,
            "scenario_duplicate_cluster_count": 53,
            "scenario_duplicate_case_count": 155,
            "final_flow_misordered_count": 0,
            "final_scenario_duplicate_cluster_count": 0,
            "final_scenario_duplicate_case_count": 0,
            "final_reasoning_leakage_case_count": 0,
        },
        judge_summary_payload={"total": 104, "rejected_out_count": 0, "pending_out_count": 0},
        feedback_control_debug_payload={"control_state_applied": True},
        compression_diag_payload={},
        context_result={"context_debug": {"current_document_used": True, "realtime_rag_used": True}},
    )

    assert payload["quality_score_inputs"]["structure_metric_scope"] == "final_cases"
    assert payload["quality_score_inputs"]["flow_misordered_count"] == 0
    assert payload["quality_score_inputs"]["scenario_duplicate_case_count"] == 0
    assert payload["quality_score"] == 100
    assert payload["case_quality_gate"]["passed"] is True


def test_quality_ledger_treats_flow_outline_missing_as_advisory_when_execution_plan_closes() -> None:
    rows = [
        {
            "case_id": "TC-101",
            "status": "REJECT",
            "reject_reason": "semantic_duplicate:TC-001",
            "signals": {"is_semantic_duplicate": True},
        },
        {
            "case_id": "TC-102",
            "status": "REJECT",
            "reject_reason": "semantic_duplicate:TC-050",
            "signals": {"is_semantic_duplicate": True},
        },
    ]
    payload = _build_quality_ledger_payload(
        generation_id=500,
        request_id="req-flow-outline-advisory",
        mode="multi_pass",
        stage_counts={},
        coverage_payload={"coverage_rate": 1.0, "total_rules": 64, "missing_rules": [], "missing_types": {}},
        convergence_payload={"final_count": 86},
        generation_summary_payload={
            "final_count": 86,
            "min_acceptable_final": 80,
            "quality_assessment": "medium",
            "stop_reason": ["coverage_satisfied"],
        },
        review_decision_summary_payload={
            "candidate_total": 81,
            "retained_total": 76,
            "final_flow_missing_stage_count": 4,
            "final_flow_missing_stages": [
                "stage:optional-popup",
                "stage:optional-message-tab",
            ],
            "final_flow_misordered_count": 0,
            "final_scenario_duplicate_cluster_count": 0,
            "final_scenario_duplicate_case_count": 0,
            "final_reasoning_leakage_case_count": 0,
            "linear_executable": True,
            "linear_scope": "main_smoke_chain_only",
            "main_chain_case_count": 6,
            "broken_dependency_count": 0,
            "state_conflict_count": 0,
            "execution_plan": {
                "linear_executable": True,
                "main_chain_case_count": 6,
                "main_chain_incomplete_reason": "",
            },
        },
        judge_summary_payload={"total": 75, "rejected_out_count": 2, "pending_out_count": 0},
        feedback_control_debug_payload={"control_state_applied": True},
        compression_diag_payload={},
        context_result={"context_debug": {"current_document_used": True, "realtime_rag_used": True}},
        judge_decision_table_payload=rows,
    )

    inputs = payload["quality_score_inputs"]
    assert inputs["raw_flow_missing_count"] == 4
    assert inputs["flow_missing_advisory_count"] == 4
    assert inputs["flow_missing_count"] == 0
    assert inputs["filtered_semantic_duplicate_reject_count"] == 2
    deduction_keys = {item["key"] for item in payload["quality_score_deductions"]}
    assert "flow_missing" not in deduction_keys
    assert "judge_rejected" not in deduction_keys
    assert payload["quality_score"] == 100
    assert payload["quality_remediation"]["primary_action"] == ""


def test_quality_ledger_treats_flow_outline_as_advisory_for_declared_independent_suite() -> None:
    payload = _build_quality_ledger_payload(
        generation_id=502,
        request_id="req-independent-suite",
        mode="multi_pass",
        stage_counts={},
        coverage_payload={"coverage_rate": 1.0, "total_rules": 8, "missing_rules": [], "missing_types": {}},
        convergence_payload={"final_count": 12},
        generation_summary_payload={
            "final_count": 12,
            "min_acceptable_final": 10,
            "quality_assessment": "medium",
            "stop_reason": ["coverage_satisfied"],
        },
        review_decision_summary_payload={
            "candidate_total": 15,
            "retained_total": 12,
            "final_flow_missing_stage_count": 3,
            "final_flow_misordered_count": 0,
            "final_scenario_duplicate_cluster_count": 0,
            "final_scenario_duplicate_case_count": 0,
            "final_reasoning_leakage_case_count": 0,
            "linear_executable": False,
            "main_chain_case_count": 0,
            "execution_plan": {
                "workflow_absence_declared": True,
                "independent_suite_executable": True,
                "linear_executable": False,
                "main_chain_case_count": 0,
                "main_chain_incomplete_reason": "workflow_absence_declared",
            },
        },
        judge_summary_payload={"total": 12, "rejected_out_count": 0, "pending_out_count": 0},
        feedback_control_debug_payload={"control_state_applied": True},
        compression_diag_payload={},
        context_result={"context_debug": {"current_document_used": True, "realtime_rag_used": True}},
    )

    inputs = payload["quality_score_inputs"]
    assert inputs["raw_flow_missing_count"] == 3
    assert inputs["flow_missing_advisory_count"] == 3
    assert inputs["flow_missing_count"] == 0
    assert not any(item["key"] == "flow_missing" for item in payload["quality_score_deductions"])
    assert payload["quality_remediation"]["primary_action"] == ""


def test_quality_ledger_penalizes_flow_missing_when_execution_plan_is_incomplete() -> None:
    payload = _build_quality_ledger_payload(
        generation_id=501,
        request_id="req-flow-missing-real",
        mode="multi_pass",
        stage_counts={},
        coverage_payload={"coverage_rate": 1.0, "total_rules": 64, "missing_rules": [], "missing_types": {}},
        convergence_payload={"final_count": 86},
        generation_summary_payload={
            "final_count": 86,
            "min_acceptable_final": 80,
            "quality_assessment": "medium",
            "stop_reason": ["coverage_satisfied"],
        },
        review_decision_summary_payload={
            "candidate_total": 81,
            "retained_total": 76,
            "final_flow_missing_stage_count": 2,
            "final_flow_misordered_count": 0,
            "final_scenario_duplicate_cluster_count": 0,
            "final_scenario_duplicate_case_count": 0,
            "final_reasoning_leakage_case_count": 0,
            "linear_executable": False,
            "main_chain_case_count": 3,
            "execution_plan": {
                "linear_executable": False,
                "main_chain_case_count": 3,
                "main_chain_incomplete_reason": "missing_commit_stage",
            },
        },
        judge_summary_payload={"total": 75, "rejected_out_count": 0, "pending_out_count": 0},
        feedback_control_debug_payload={"control_state_applied": True},
        compression_diag_payload={},
        context_result={"context_debug": {"current_document_used": True, "realtime_rag_used": True}},
    )

    inputs = payload["quality_score_inputs"]
    assert inputs["raw_flow_missing_count"] == 2
    assert inputs["flow_missing_advisory_count"] == 0
    assert inputs["flow_missing_count"] == 2
    assert any(item["key"] == "flow_missing" for item in payload["quality_score_deductions"])
    assert payload["quality_remediation"]["primary_action"] == "repair_final_flow_structure"


def test_quality_ledger_does_not_penalize_profile_forbidden_or_dropped_repairable_noise() -> None:
    payload = _build_quality_ledger_payload(
        generation_id=469,
        request_id="req-final-quality",
        mode="multi_pass",
        stage_counts={},
        coverage_payload={"coverage_rate": 1.0, "total_rules": 22, "missing_rules": [], "missing_types": {}},
        convergence_payload={"final_count": 87},
        generation_summary_payload={"final_count": 87, "quality_assessment": "medium", "stop_reason": []},
        review_decision_summary_payload={
            "candidate_total": 112,
            "retained_total": 87,
            "final_flow_misordered_count": 0,
            "final_scenario_duplicate_case_count": 0,
            "fact_profile_forbidden_count": 1,
            "fact_profile_pending_count": 0,
        },
        judge_summary_payload={
            "total": 102,
            "rejected_out_count": 0,
            "pending_out_count": 0,
            "raw_repairable_count": 1,
            "remaining_repairable_count": 0,
            "unrepaired_repairable_count": 1,
            "fact_violation_count": 0,
        },
        feedback_control_debug_payload={"control_state_applied": True},
        compression_diag_payload={},
        context_result={"context_debug": {"current_document_used": True, "realtime_rag_used": True}},
    )

    deduction_keys = {item["key"] for item in payload["quality_score_deductions"]}
    assert "fact_forbidden" not in deduction_keys
    assert "judge_repairable" not in deduction_keys
    assert payload["quality_score_inputs"]["fact_profile_forbidden_count"] == 1
    assert payload["quality_score_inputs"]["fact_violation_count"] == 0
    assert payload["quality_score_inputs"]["raw_repairable_count"] == 1
    assert payload["quality_score_inputs"]["repairable_count"] == 0
    assert payload["judge"]["raw_repairable_count"] == 1
    assert payload["judge"]["repairable_count"] == 0


def test_quality_ledger_clusters_judge_reject_reasons_and_keeps_quality_gate_shadow() -> None:
    rows = [
        {
            "case_id": f"TC-{index:03d}",
            "status": "REJECT",
            "reject_reason": f"semantic_duplicate:TC-{index - 1:03d}",
            "signals": {"is_semantic_duplicate": True},
        }
        for index in range(2, 24)
    ]
    payload = _build_quality_ledger_payload(
        generation_id=465,
        request_id="req-6",
        mode="multi_pass",
        stage_counts={},
        coverage_payload={"coverage_rate": 1.0, "total_rules": 22, "missing_rules": [], "missing_types": {}},
        convergence_payload={"final_count": 83},
        generation_summary_payload={
            "final_count": 83,
            "min_acceptable_final": 104,
            "quality_assessment": "low",
            "stop_reason": [],
        },
        review_decision_summary_payload={
            "final_flow_misordered_count": 0,
            "final_scenario_duplicate_case_count": 0,
            "final_reasoning_leakage_case_count": 0,
        },
        judge_summary_payload={"total": 104, "rejected_out_count": 22, "pending_out_count": 0},
        feedback_control_debug_payload={"control_state_applied": True},
        compression_diag_payload={},
        context_result={"context_debug": {"current_document_used": True, "realtime_rag_used": True}},
        judge_decision_table_payload=rows,
    )

    assert payload["judge"]["reason_clusters"] == {"semantic_duplicate": 22}
    assert payload["judge"]["dominant_reason"] == "semantic_duplicate"
    assert payload["quality_score_inputs"]["raw_rejected_count"] == 22
    assert payload["quality_score_inputs"]["rejected_count"] == 22
    assert payload["quality_score_inputs"]["semantic_duplicate_reject_count"] == 22
    assert payload["quality_score_inputs"]["filtered_semantic_duplicate_reject_count"] == 0
    assert payload["case_quality_gate"]["metrics"]["raw_judge_rejected_count"] == 22
    assert payload["case_quality_gate"]["metrics"]["judge_rejected_count"] == 22
    assert payload["case_quality_gate"]["metrics"]["semantic_duplicate_reject_count"] == 22
    assert payload["case_quality_gate"]["metrics"]["filtered_semantic_duplicate_reject_count"] == 0
    assert payload["case_quality_gate"]["mode"] == "shadow"
    assert payload["case_quality_gate"]["blocked"] is False
    assert payload["case_quality_gate"]["passed"] is False
    assert "final_count_below_min_acceptable" not in set(payload["case_quality_gate"]["failure_reasons"])
    assert "judge_rejected_above_threshold" in set(payload["case_quality_gate"]["failure_reasons"])


def test_quality_ledger_marks_candidate_insufficient_underfill_as_advisory() -> None:
    payload = _build_quality_ledger_payload(
        generation_id=476,
        request_id="req-underfill-advisory",
        mode="multi_pass",
        stage_counts={},
        coverage_payload={"coverage_rate": 1.0, "total_rules": 22, "missing_rules": [], "missing_types": {}},
        convergence_payload={
            "final_count": 60,
            "candidate_count_before_review": 60,
            "review_selected_count": 60,
        },
        generation_summary_payload={
            "final_count": 60,
            "min_acceptable_final": 85,
            "quality_assessment": "medium",
            "underfilled": True,
            "underfill_reason": "valid_candidate_insufficient",
            "underfill_root_cause": "candidate_insufficient",
            "stop_reason": ["underfilled"],
        },
        review_decision_summary_payload={
            "candidate_total": 60,
            "retained_total": 60,
            "final_flow_misordered_count": 0,
            "final_scenario_duplicate_case_count": 0,
        },
        judge_summary_payload={"total": 61, "rejected_out_count": 4, "pending_out_count": 0},
        feedback_control_debug_payload={"control_state_applied": True},
        compression_diag_payload={},
        context_result={"context_debug": {"current_document_used": True, "realtime_rag_used": True}},
    )

    gate = payload["case_quality_gate"]
    assert gate["passed"] is True
    assert gate["metrics"]["quantity_shortfall_advisory"] is True
    assert "final_count_below_min_acceptable" not in set(gate["failure_reasons"])


def test_quality_ledger_filters_replaced_semantic_duplicate_rejects_from_final_score() -> None:
    rows = [
        {
            "case_id": f"TC-{index:03d}",
            "status": "REJECT",
            "reject_reason": f"semantic_duplicate:TC-{index - 1:03d}",
            "signals": {"is_semantic_duplicate": True},
        }
        for index in range(2, 9)
    ]
    payload = _build_quality_ledger_payload(
        generation_id=494,
        request_id="req-filtered-semantic-duplicates",
        mode="multi_pass",
        stage_counts={},
        coverage_payload={"coverage_rate": 1.0, "total_rules": 65, "missing_rules": [], "missing_types": {}},
        convergence_payload={"final_count": 87},
        generation_summary_payload={
            "final_count": 87,
            "min_acceptable_final": 80,
            "quality_assessment": "medium",
            "stop_reason": [],
        },
        review_decision_summary_payload={
            "candidate_total": 84,
            "retained_total": 69,
            "final_flow_missing_stage_count": 0,
            "final_flow_misordered_count": 0,
            "final_scenario_duplicate_cluster_count": 0,
            "final_scenario_duplicate_case_count": 0,
            "final_reasoning_leakage_case_count": 0,
        },
        judge_summary_payload={"total": 79, "rejected_out_count": 7, "pending_out_count": 0},
        feedback_control_debug_payload={"control_state_applied": True},
        compression_diag_payload={},
        context_result={"context_debug": {"current_document_used": True, "realtime_rag_used": True}},
        judge_decision_table_payload=rows,
    )

    inputs = payload["quality_score_inputs"]
    assert inputs["raw_rejected_count"] == 7
    assert inputs["semantic_duplicate_reject_count"] == 7
    assert inputs["filtered_semantic_duplicate_reject_count"] == 7
    assert inputs["rejected_count"] == 0
    assert "judge_rejected" not in {item["key"] for item in payload["quality_score_deductions"]}
    assert payload["quality_score"] == 100
    assert payload["case_quality_gate"]["passed"] is True
    assert payload["case_quality_gate"]["metrics"]["raw_judge_rejected_count"] == 7
    assert payload["case_quality_gate"]["metrics"]["judge_rejected_count"] == 0


def test_quality_ledger_reports_manual_profile_delivery_drift_as_advisory() -> None:
    payload = _build_quality_ledger_payload(
        generation_id=466,
        request_id="req-7",
        mode="multi_pass",
        stage_counts={},
        coverage_payload={"coverage_rate": 1.0, "total_rules": 22, "missing_rules": [], "missing_types": {}},
        convergence_payload={"final_count": 10},
        generation_summary_payload={
            "final_count": 10,
            "quality_assessment": "medium",
            "stop_reason": [],
            "final_priority_breakdown": {"P0": 1, "P1": 1, "P2": 8},
            "final_module_breakdown_top": {"display": 5, "other": 5},
            "final_display_ratio": 0.5,
            "final_high_priority_ratio": 0.2,
        },
        review_decision_summary_payload={
            "final_flow_misordered_count": 0,
            "final_scenario_duplicate_case_count": 0,
        },
        judge_summary_payload={"total": 10, "rejected_out_count": 0, "pending_out_count": 0},
        feedback_control_debug_payload={
            "control_state_applied": True,
            "source_meta": {
                "manual_quality_profile": {
                    "kind": "manual_quality_profile",
                    "profile_source": "priority_sample_pool_manual_verified",
                    "profile_version": "stable-1",
                    "trusted_sample_count": 20,
                    "profile_case_count": 10,
                    "priority_distribution": {"P0": 4, "P1": 4, "P2": 2},
                    "module_distribution_top": {"core": 7, "plan": 3},
                    "high_priority_ratio": 0.8,
                    "display_ratio_cap": 0.25,
                }
            },
        },
        compression_diag_payload={},
        context_result={"context_debug": {"current_document_used": True, "realtime_rag_used": True}},
    )

    assert payload["manual_delivery"]["applied"] is True
    assert payload["manual_delivery"]["scoring_mode"] == "advisory"
    assert payload["manual_delivery"]["high_priority_ratio_shortfall"] == 0.6
    assert payload["manual_delivery"]["display_ratio_excess"] == 0.25
    deduction_keys = {item["key"] for item in payload["quality_score_deductions"]}
    assert "manual_high_priority_shortfall" not in deduction_keys
    assert "manual_display_ratio_excess" not in deduction_keys
    assert payload["quality_score_inputs"]["manual_quality_profile_version"] == "stable-1"
    assert payload["quality_score_basis"].endswith("+manual_profile_advisory")


def test_quality_ledger_calibrates_curated_profile_for_full_regression_suite_mix() -> None:
    payload = _build_quality_ledger_payload(
        generation_id=467,
        request_id="req-8",
        mode="multi_pass",
        stage_counts={},
        coverage_payload={"coverage_rate": 1.0, "total_rules": 22, "missing_rules": [], "missing_types": {}},
        convergence_payload={"final_count": 100},
        generation_summary_payload={
            "generation_coverage_mode": "full_functional_regression",
            "final_count": 100,
            "quality_assessment": "medium",
            "stop_reason": [],
            "final_priority_breakdown": {"P0": 10, "P1": 40, "P2": 50},
            "final_module_breakdown_top": {"core": 100},
            "final_display_ratio": 0.2,
            "final_high_priority_ratio": 0.5,
        },
        review_decision_summary_payload={
            "final_flow_misordered_count": 0,
            "final_scenario_duplicate_case_count": 0,
        },
        judge_summary_payload={"total": 100, "rejected_out_count": 0, "pending_out_count": 0},
        feedback_control_debug_payload={
            "control_state_applied": True,
            "source_meta": {
                "manual_quality_profile": {
                    "kind": "manual_quality_profile",
                    "profile_source": "priority_sample_pool_manual_verified",
                    "profile_version": "curated-1",
                    "trusted_sample_count": 30,
                    "profile_case_count": 20,
                    "priority_distribution": {"P0": 3, "P1": 17},
                    "module_distribution_top": {"core": 20},
                    "high_priority_ratio": 1.0,
                    "display_ratio_cap": 0.45,
                }
            },
        },
        compression_diag_payload={},
        context_result={"context_debug": {"current_document_used": True, "realtime_rag_used": True}},
    )

    manual = payload["manual_delivery"]
    assert manual["raw_target_high_priority_ratio"] == 1.0
    assert manual["target_high_priority_ratio"] == 0.6
    assert manual["high_priority_target_calibrated"] is True
    assert manual["high_priority_ratio_shortfall"] == 0.1
    assert manual["effective_priority_distribution_target"]["P2"] == 0.4


def test_quality_ledger_tolerates_successfully_pruned_semantic_duplicates() -> None:
    payload = _build_quality_ledger_payload(
        generation_id=468,
        request_id="req-9",
        mode="multi_pass",
        stage_counts={},
        coverage_payload={"coverage_rate": 1.0, "total_rules": 22, "missing_rules": [], "missing_types": {}},
        convergence_payload={"final_count": 87, "semantic_dedup_dropped_count": 14},
        generation_summary_payload={"final_count": 87, "quality_assessment": "medium", "stop_reason": []},
        review_decision_summary_payload={
            "candidate_total": 112,
            "retained_total": 87,
            "final_flow_misordered_count": 0,
            "final_scenario_duplicate_cluster_count": 0,
            "final_scenario_duplicate_case_count": 0,
        },
        judge_summary_payload={"total": 87, "rejected_out_count": 0, "pending_out_count": 0},
        feedback_control_debug_payload={"control_state_applied": True},
        compression_diag_payload={},
        context_result={"context_debug": {"current_document_used": True, "realtime_rag_used": True}},
    )

    assert payload["quality_score_inputs"]["semantic_dedup_dropped_count"] == 14
    assert payload["quality_score_inputs"]["semantic_dedup_penalty_count"] == 5
    semantic_dedup = [
        item for item in payload["quality_score_deductions"] if item["key"] == "semantic_dedup"
    ]
    assert semantic_dedup[0]["count"] == 5


def test_quality_ledger_exposes_actionable_remediation_for_critical_score() -> None:
    rows = [
        {
            "case_id": f"TC-{index:03d}",
            "status": "REJECT",
            "reject_reason": f"semantic_duplicate:TC-{index - 1:03d}",
            "signals": {"is_semantic_duplicate": True},
        }
        for index in range(2, 11)
    ]
    payload = _build_quality_ledger_payload(
        generation_id=482,
        request_id="req-critical",
        mode="multi_pass",
        stage_counts={"primary": 80, "gap": 8, "review": 87},
        coverage_payload={
            "coverage_rate": 0.9231,
            "total_rules": 65,
            "missing_rules": ["RULE-030", "RULE-034", "RULE-041", "RULE-050", "RULE-052"],
            "missing_types": {"boundary": ["RULE-B"], "exception": []},
        },
        convergence_payload={
            "final_count": 87,
            "low_quality_dropped_count": 4,
            "low_quality_dropped_examples": [
                {"case_id": "TC-LQ-1", "reason": "non_assertable_expected_result"}
            ],
            "semantic_dedup_dropped_count": 9,
        },
        generation_summary_payload={
            "final_count": 87,
            "quality_assessment": "low",
            "stop_reason": ["stopped_due_to_diminishing_returns"],
            "final_priority_breakdown": {"P0": 3, "P1": 20, "P2": 64},
            "final_display_ratio": 0.25,
            "final_high_priority_ratio": 0.2644,
        },
        review_decision_summary_payload={
            "candidate_total": 93,
            "retained_total": 80,
            "final_flow_misordered_count": 0,
            "final_scenario_duplicate_case_count": 0,
            "fact_profile_pending_count": 1,
        },
        judge_summary_payload={"total": 86, "rejected_out_count": 9, "pending_out_count": 2},
        feedback_control_debug_payload={
            "control_state_applied": True,
            "source_meta": {
                "manual_quality_profile": {
                    "kind": "manual_quality_profile",
                    "profile_source": "priority_sample_pool_manual_verified",
                    "profile_version": "stable-1",
                    "trusted_sample_count": 20,
                    "profile_case_count": 10,
                    "priority_distribution": {"P0": 4, "P1": 4, "P2": 2},
                    "module_distribution_top": {"core": 10},
                    "high_priority_ratio": 0.8,
                    "display_ratio_cap": 0.18,
                }
            },
        },
        compression_diag_payload={},
        context_result={"context_debug": {"current_document_used": True, "realtime_rag_used": True}},
        judge_decision_table_payload=rows,
    )

    remediation = payload["quality_remediation"]
    assert remediation["kind"] == "quality_remediation"
    assert remediation["primary_action"] == "cover_missing_rules"
    action_ids = [item["action_id"] for item in remediation["actions"]]
    assert "cover_missing_rules" in action_ids
    assert "reduce_semantic_duplicates" in action_ids
    assert "tighten_expected_results" in action_ids
    assert "rebalance_manual_quality_profile" not in action_ids
    assert remediation["next_run_controls"]["target_missing_rules"][:2] == ["RULE-030", "RULE-034"]
    assert remediation["actions"][0]["priority"] == "P0"

    compact = _compact_quality_ledger(payload)
    assert compact["quality_primary_action"] == "cover_missing_rules"
    assert "reduce_semantic_duplicates" in compact["quality_action_ids"]
