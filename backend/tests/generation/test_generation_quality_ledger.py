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
