from __future__ import annotations

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
