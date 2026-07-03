import json

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
