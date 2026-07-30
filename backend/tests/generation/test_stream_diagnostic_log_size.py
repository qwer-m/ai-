from __future__ import annotations

import json

from modules.test_generation_components.legacy.stream.persistence_post_persist_diagnostics import (
    _MAX_GEN_DIAG_MESSAGE_BYTES,
    add_diagnostic_log,
)

def test_add_diagnostic_log_compacts_oversized_payload_without_breaking_json() -> None:
    large_suite = {
        "kind": "execution_suite",
        "case_count": 120,
        "execution_readiness": "partial",
        "suites": [
            {
                "suite_id": f"suite-{index}",
                "cases": [
                    {
                        "case_id": f"TC-{index:03d}-{case_index:03d}",
                        "description": "x" * 800,
                        "steps": ["step " + ("y" * 200)] * 5,
                    }
                    for case_index in range(8)
                ],
            }
            for index in range(30)
        ],
    }
    payload = {
        "kind": "generation_execution_suite",
        "generation_id": 506,
        "project_id": 2,
        "request_id": "req-large",
        "source": "persistence_gate_pre_projection",
        "case_count": 120,
        "execution_readiness": "partial",
        "execution_suite": large_suite,
    }

    line = add_diagnostic_log(
        db=None,
        log_entry_type=object,
        project_id=2,
        user_id=1,
        payload=payload,
    )

    assert len(line.encode("utf-8")) <= _MAX_GEN_DIAG_MESSAGE_BYTES
    assert line.startswith("GEN_DIAG:")
    fitted = json.loads(line.split(":", 1)[1])
    assert fitted["kind"] == "generation_execution_suite"
    assert fitted["generation_id"] == 506
    assert fitted["payload_omitted_due_to_size"] is True
    assert fitted["execution_suite_omitted_due_to_size"] is True
    assert "execution_suite" not in fitted
    assert fitted["execution_suite_summary"]["case_count"] == 120
    assert fitted["execution_suite_compact"]["case_count"] == 120
    assert fitted["execution_suite_compact"]["suites"][0]["case_count"] == 8
    compact_case = fitted["execution_suite_compact"]["suites"][0]["cases"][0]
    assert compact_case["case_id"] == "TC-000-000"
    assert "description" not in compact_case
    assert "steps" not in compact_case


def test_quality_ledger_compaction_preserves_deductions_and_scalar_inputs() -> None:
    payload = {
        "kind": "generation_quality_ledger",
        "generation_id": 530,
        "quality_score": 97,
        "quality_score_grade": "high",
        "quality_score_deductions": [
            {
                "key": "fact_pending",
                "label": "命中待确认事实",
                "count": 2,
                "points": 3.0,
            }
        ],
        "quality_score_inputs": {
            "final_count": 75,
            "fact_profile_pending_count": 2,
            "nested_debug": {"large": "x" * 70000},
        },
        "control": {"large_graph": "y" * 70000},
    }

    line = add_diagnostic_log(
        db=None,
        log_entry_type=object,
        project_id=2,
        user_id=1,
        payload=payload,
    )

    assert len(line.encode("utf-8")) <= _MAX_GEN_DIAG_MESSAGE_BYTES
    fitted = json.loads(line.split(":", 1)[1])
    assert fitted["payload_omitted_due_to_size"] is True
    assert fitted["quality_score_deductions"] == [
        {
            "key": "fact_pending",
            "label": "命中待确认事实",
            "count": 2,
            "points": 3.0,
        }
    ]
    assert fitted["quality_score_inputs"] == {
        "final_count": 75,
        "fact_profile_pending_count": 2,
    }


def test_feedback_control_compaction_preserves_semantic_compilation_evidence() -> None:
    attempt = {
        "attempt": 1,
        "semantic_attempt": 1,
        "compilation_mode": "independent_recompile",
        "status": "validated",
        "evidence_binding": {
            "declared_ref_count": 24,
            "resolved_ref_count": 24,
            "unknown_ref_count": 0,
            "raw_evidence_count": 0,
        },
        "workflow_topology_status": "independent_only",
        "workflow_topology_error_codes": [],
        "large_unrelated_diagnostic": "x" * 70000,
    }
    payload = {
        "kind": "feedback_control_state",
        "request_id": "req-semantic-compact",
        "source_meta": {
            "semantic_compile_status": "applied_independent_only",
            "semantic_compile_success": True,
            "semantic_compile_attempt_count": 1,
            "semantic_compile_independent_recompile_used": True,
            "semantic_compile_independent_recompile_outcome": "validated",
            "semantic_compile_attempts": [attempt],
            "workflow_declaration_status": "applied_independent_only",
            "source_evidence_catalog": {
                "version": "source-evidence-catalog-v1",
                "count": 24,
                "fingerprint": "e" * 64,
                "injected": True,
            },
            "source_evidence_catalog_coverage": {
                "source_key_chars": 4096,
                "covered_key_chars": 4096,
                "complete": True,
            },
            "semantic_graph_diagnostics": {
                "fact_count": 24,
                "node_count": 12,
                "edge_count": 8,
                "workflow_topology_status": "independent_only",
                "workflow_topology_error_codes": [],
                "large_unrelated_diagnostic": "y" * 70000,
            },
            "requirement_semantic_contract": {
                "large_graph": "z" * 70000,
            },
        },
    }

    line = add_diagnostic_log(
        db=None,
        log_entry_type=object,
        project_id=2,
        user_id=1,
        payload=payload,
    )

    assert len(line.encode("utf-8")) <= _MAX_GEN_DIAG_MESSAGE_BYTES
    fitted = json.loads(line.split(":", 1)[1])
    source_meta = fitted["source_meta"]
    assert fitted["payload_omitted_due_to_size"] is True
    assert source_meta["compaction"] == "semantic_compilation_summary"
    assert source_meta["source_evidence_catalog_coverage"]["complete"] is True
    assert source_meta["semantic_graph_diagnostics"]["workflow_topology_status"] == (
        "independent_only"
    )
    assert source_meta["semantic_compile_attempts"][0]["evidence_binding"] == {
        "declared_ref_count": 24,
        "resolved_ref_count": 24,
        "unknown_ref_count": 0,
        "raw_evidence_count": 0,
    }
    assert "large_unrelated_diagnostic" not in source_meta["semantic_compile_attempts"][0]
