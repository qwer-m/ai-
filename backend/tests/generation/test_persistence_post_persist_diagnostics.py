import json

from modules.test_generation_components.execution.execution_suite import build_execution_suite
from modules.test_generation_components.legacy.stream.persistence_post_persist_diagnostics import (
    add_diagnostic_log,
    build_stream_post_persist_diagnostic_payloads,
    stream_generation_mode,
)


class _LogEntry:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _Db:
    def __init__(self) -> None:
        self.entries: list[_LogEntry] = []

    def add(self, item: _LogEntry) -> None:
        self.entries.append(item)


def _kinds(payloads: list[dict]) -> list[str]:
    return [str(payload.get("kind") or "") for payload in payloads]


def test_stream_generation_mode_defaults_from_multi_pass() -> None:
    assert stream_generation_mode("", True) == "multi_pass"
    assert stream_generation_mode("", False) == "single_pass"
    assert stream_generation_mode("full_functional_regression", True) == "full_functional_regression"


def test_build_stream_post_persist_diagnostic_payloads_preserves_order_and_tables() -> None:
    payloads = build_stream_post_persist_diagnostic_payloads(
        generation_id=42,
        project_id=7,
        request_id="req-stream-1",
        generation_mode="",
        multi_pass=True,
        current_biz_key="payment",
        timing_payload={"kind": "generation_timing_ledger", "event_count": 3},
        stage_counts={"primary": 2, "gap": 1, "review": 2},
        duration_by_stage_ms={"primary": 10, "gap": 20, "review": 30},
        doc_type="requirement",
        compress=False,
        expected_count=5,
        generated_count=3,
        requirement_length=100,
        kb_length=200,
        model="deterministic-model",
        max_tokens=4096,
        compression_diag_payload={
            "compression_ratio": 0.5,
            "retained_chunk_count": 2,
            "relevance_distribution": {"high": 1},
        },
        convergence_payload={"final_count": 3},
        review_decision_summary_payload={"candidate_total": 4, "retained_total": 3},
        feedback_control_debug_payload={"workflow_blueprint_count": 1},
        judge_summary_payload={"rejected_out_count": 1, "pending_out_count": 0},
        judge_decision_table_payload=[
            {
                "case_id": "TC-002",
                "status": "REJECT",
                "reject_reason": "semantic_duplicate",
                "signals": {"missing_core_flow": True},
                "before_case_snapshot": {"description": "before"},
            },
            {"case_id": "TC-001", "status": "PASS"},
        ],
        memory_diag={"enabled": True},
        review_decision_table_payload=[
            {"case_id": "TC-001", "retained_final": True},
            {
                "case_id": "TC-002",
                "dropped_stage": "review_llm",
                "dropped_reason": "duplicate",
                "review_llm_drop_reason_evidence": {"selected_case_ids": ["TC-001"]},
            },
        ],
        generation_summary_payload={"status": "completed", "final_count": 3},
        quality_ledger_payload={
            "kind": "generation_quality_ledger",
            "case_quality_gate": {"kind": "case_quality_gate", "passed": True},
        },
        coverage_payload={"kind": "coverage_check", "coverage_rate": 1.0},
    )

    assert _kinds(payloads.before_generation_summary) == [
        "generation_persisted",
        "generation_timing_ledger",
        "generation_mode",
        "generation_stage",
        "generation_stage",
        "generation_stage",
        "gen_diag",
        "generation_context_compression",
        "generation_convergence",
        "review_decision_summary",
        "feedback_control_state",
        "judge_summary",
        "judge_decision_table",
        "memory_fabric_diag",
        "review_decision_table",
        "review_decision_table_compact",
    ]
    assert payloads.generation_summary == {
        "kind": "generation_summary",
        "status": "completed",
        "final_count": 3,
        "multi_pass": True,
        "generation_mode": "multi_pass",
    }
    assert _kinds(payloads.after_generation_summary) == [
        "generation_quality_ledger",
        "case_quality_gate",
        "coverage_check",
    ]

    judge_table = next(
        payload for payload in payloads.before_generation_summary if payload.get("kind") == "judge_decision_table"
    )
    assert judge_table["rows_scope"] == "reject_pending_only"
    assert judge_table["row_count"] == 1
    assert judge_table["row_count_total"] == 2
    assert judge_table["rows"][0]["signals"]["missing_core_flow"] is True
    assert payloads.after_generation_summary[1]["generation_id"] == 42
    assert payloads.after_generation_summary[1]["request_id"] == "req-stream-1"


def test_add_diagnostic_log_writes_entry_and_returns_stream_line() -> None:
    db = _Db()
    line = add_diagnostic_log(
        db=db,
        log_entry_type=_LogEntry,
        project_id=7,
        user_id=1001,
        payload={"kind": "generation_mode", "mode": "multi_pass"},
    )

    assert line.endswith("\n")
    assert line.startswith("GEN_DIAG:")
    assert len(db.entries) == 1
    assert db.entries[0].project_id == 7
    assert db.entries[0].user_id == 1001
    assert json.loads(db.entries[0].message.removeprefix("GEN_DIAG:")) == {
        "kind": "generation_mode",
        "mode": "multi_pass",
    }


def test_large_independent_execution_suite_compaction_preserves_workflow_absence_declaration() -> None:
    cases = [
        {
            "id": f"TC-{index:03d}",
            "description": "independent behavior " + ("x" * 900),
            "test_module": "independent module",
            "steps": ["run independent behavior"],
            "expected_result": "independent behavior succeeds",
            "priority": "P1",
            "priority_final": "P1",
            "execution_group": "independent_functional",
            "execution_sequence": index,
            "chain_id": "independent_functional_chain",
            "role": "business_user",
            "session_key": "business_user_session",
        }
        for index in range(1, 81)
    ]
    execution_suite = build_execution_suite(
        cases,
        workflow_absence_declared=True,
    )
    db = _Db()

    line = add_diagnostic_log(
        db=db,
        log_entry_type=_LogEntry,
        project_id=7,
        user_id=1001,
        payload={
            "kind": "generation_execution_suite",
            "generation_id": 42,
            "execution_suite": execution_suite,
        },
    )

    payload = json.loads(line.removeprefix("GEN_DIAG:"))
    assert payload["payload_omitted_due_to_size"] is True
    assert payload["execution_suite_omitted_due_to_size"] is True
    assert payload["execution_suite_compact"]["workflow_absence_declared"] is True
    assert payload["execution_suite_compact"]["execution_readiness"] == "independent_ready"
    assert db.entries[0].message.rstrip("\n") == line.rstrip("\n")


def test_large_semantic_diagnostic_keeps_only_compact_graph_attempt_summary() -> None:
    db = _Db()

    line = add_diagnostic_log(
        db=db,
        log_entry_type=_LogEntry,
        project_id=7,
        user_id=1001,
        payload={
            "kind": "feedback_control_state",
            "padding": [{"value": "x" * 1_000} for _ in range(200)],
            "source_meta": {
                "semantic_compile_status": "contract_invalid",
                "semantic_compile_attempts": [
                    {
                        "attempt": 1,
                        "candidate_mode": "fresh_candidate",
                        "compilation_mode": "initial",
                        "status": "contract_invalid",
                        "workflow_topology_status": "not_linearizable",
                        "workflow_topology_error_codes": [
                            "required_control_cycle"
                        ],
                        "workflow_consistency_rejection_count": 2,
                        "workflow_consistency_rejection_codes": [
                            "adjacent_state_discontinuity"
                        ],
                        "projection_error_codes": [
                            "active_scope_id_mismatch"
                        ],
                        "raw_candidate": {
                            "需求原文": "不得进入持久化诊断",
                        },
                    }
                ],
            },
        },
    )

    payload = json.loads(line.removeprefix("GEN_DIAG:"))
    attempt = payload["source_meta"]["semantic_compile_attempts"][0]
    assert attempt["workflow_consistency_rejection_count"] == 2
    assert attempt["workflow_consistency_rejection_codes"] == [
        "adjacent_state_discontinuity"
    ]
    assert attempt["projection_error_codes"] == [
        "active_scope_id_mismatch"
    ]
    assert "raw_candidate" not in attempt
    assert "需求原文" not in line


def test_large_semantic_diagnostic_preserves_a1_v2_partition_group_summary() -> None:
    db = _Db()
    secret = "a1-raw-candidate-must-not-persist"

    line = add_diagnostic_log(
        db=db,
        log_entry_type=_LogEntry,
        project_id=7,
        user_id=1001,
        payload={
            "kind": "feedback_control_state",
            "padding": [{"value": "x" * 1_000} for _ in range(200)],
            "source_meta": {
                "fact_ledger_compile_status": "contract_invalid",
                "fact_ledger_compile_success": False,
                "fact_ledger_compile_candidate_attempt_count": 5,
                "fact_ledger_compile_candidate_attempt_limit": 6,
                "fact_ledger_compile_fresh_candidate_used": True,
                "fact_ledger_compile_fresh_candidate_trigger_codes": [
                    "fact_ledger_contract_invalid"
                ],
                "fact_ledger_compile_last_parseable_candidate_attempt": 4,
                "fact_ledger_compile_last_parseable_candidate_status": (
                    "contract_invalid"
                ),
                "fact_ledger_compile_last_parseable_candidate_fingerprint": (
                    "a" * 64
                ),
                "fact_ledger_compile_last_parseable_candidate_error_codes": [
                    "fact_evidence_unknown"
                ],
                "fact_ledger_compile_stop_reason": "contract_invalid",
                "fact_ledger_compile_chunked": True,
                "fact_ledger_compile_chunk_count": 3,
                "fact_ledger_compile_chunk_limit": 16,
                "fact_ledger_compile_chunk_budget_units": 500,
                "fact_ledger_compile_catalog_budget_units": 1_250,
                "fact_ledger_compile_partition_group_count": 5,
                "fact_ledger_compile_oversized_partition_group_count": 1,
                "fact_ledger_compile_completed_chunk_count": 2,
                "fact_ledger_compile_failed_chunk_index": 3,
                "fact_ledger_compile_global_status": "chunk_failed",
                "fact_ledger_compile_global_error_codes": [
                    "fact_ledger_chunk_manifest_invalid"
                ],
                "fact_ledger_compile_collapsed_duplicate_fact_count": 2,
                "fact_ledger_compile_chunk_summaries": [
                    {
                        "chunk_index": 1,
                        "status": "validated",
                        "target_source_evidence_count": 12,
                        "budget_units": 480,
                        "target_fingerprint": "b" * 64,
                        "candidate_attempt_count": 1,
                        "envelope_count": 1,
                        "physical_call_count": 1,
                        "validated_attempt": 1,
                        "ledger_fingerprint": "c" * 64,
                        "fact_count": 10,
                        "source_disposition_count": 12,
                        "raw_declarations": secret,
                    }
                ],
                "fact_ledger_compile_attempts": [
                    {
                        "attempt": 1,
                        "candidate_mode": "initial",
                        "compilation_mode": "initial",
                        "chunk_index": 1,
                        "chunk_count": 3,
                        "chunk_source_evidence_count": 12,
                        "status": "contract_invalid",
                        "raw_chars": 4_096,
                        "contract_error_count": 1,
                        "contract_error_codes": ["fact_evidence_unknown"],
                        "fact_ledger_diagnostics": {"statement": secret},
                        "model_envelope": {"raw_candidate": secret},
                    }
                ],
                # v1 已废弃字段即使由旧调用方传入，也不得重新落入压缩诊断。
                "fact_ledger_compile_oversized_source_count": 99,
                "raw_candidate": secret,
            },
        },
    )

    payload = json.loads(line.removeprefix("GEN_DIAG:"))
    source_meta = payload["source_meta"]
    assert payload["payload_omitted_due_to_size"] is True
    assert source_meta["fact_ledger_compile_partition_group_count"] == 5
    assert source_meta["fact_ledger_compile_oversized_partition_group_count"] == 1
    assert source_meta["fact_ledger_compile_global_error_codes"] == [
        "fact_ledger_chunk_manifest_invalid"
    ]
    assert source_meta["fact_ledger_compile_last_parseable_candidate_error_codes"] == [
        "fact_evidence_unknown"
    ]
    assert "fact_ledger_compile_oversized_source_count" not in source_meta

    chunk = source_meta["fact_ledger_compile_chunk_summaries"][0]
    assert chunk["chunk_index"] == 1
    assert chunk["target_source_evidence_count"] == 12
    assert chunk["fact_count"] == 10
    assert "raw_declarations" not in chunk

    attempt = source_meta["fact_ledger_compile_attempts"][0]
    assert attempt["chunk_index"] == 1
    assert attempt["chunk_count"] == 3
    assert attempt["contract_error_codes"] == ["fact_evidence_unknown"]
    assert "fact_ledger_diagnostics" not in attempt
    assert "model_envelope" not in attempt
    assert secret not in line


def test_large_semantic_diagnostic_preserves_a2_three_stage_summary() -> None:
    db = _Db()
    secret = "a2-raw-candidate-must-not-persist"

    line = add_diagnostic_log(
        db=db,
        log_entry_type=_LogEntry,
        project_id=7,
        user_id=1001,
        payload={
            "kind": "feedback_control_state",
            "padding": [{"value": "x" * 1_000} for _ in range(200)],
            "source_meta": {
                "scope_ledger_compile_mode": (
                    "global_boundary_selection_then_membership_then_binding_shards"
                ),
                "scope_ledger_compile_global_status": "membership_assignment_failed",
                "scope_ledger_boundary_selection_status": "validated",
                "scope_ledger_boundary_selection_fingerprint": "a" * 64,
                "scope_ledger_boundary_selection_count": 5,
                "scope_ledger_membership_assignment_status": "contract_invalid",
                "scope_ledger_membership_assignment_count": 4,
                "scope_ledger_membership_none_count": 1,
                "scope_ledger_source_topology": {
                    "version": "requirement-source-outline-v1",
                    "relation_count": 8,
                    "raw_source": secret,
                },
                "scope_ledger_compile_attempts": [
                    {
                        "attempt": 1,
                        "phase": "membership",
                        "status": "contract_invalid",
                        "contract_error_codes": [
                            "membership_assignment_root_forbidden"
                        ],
                        "model_envelope": {"raw_candidate": secret},
                    }
                ],
            },
        },
    )

    payload = json.loads(line.removeprefix("GEN_DIAG:"))
    source_meta = payload["source_meta"]
    assert source_meta["scope_ledger_boundary_selection_status"] == "validated"
    assert source_meta["scope_ledger_membership_assignment_status"] == (
        "contract_invalid"
    )
    assert source_meta["scope_ledger_source_topology"] == {
        "version": "requirement-source-outline-v1",
        "relation_count": 8,
    }
    attempt = source_meta["scope_ledger_compile_attempts"][0]
    assert attempt["phase"] == "membership"
    assert attempt["contract_error_codes"] == [
        "membership_assignment_root_forbidden"
    ]
    assert "model_envelope" not in attempt
    assert secret not in line
