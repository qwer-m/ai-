import json

from modules.test_generation_components.legacy.json_generation_persist_diagnostics import (
    build_post_persist_generation_diagnostic_payloads,
    emit_post_persist_coverage_audit_diagnostics,
)


def test_post_persist_generation_diagnostic_payloads_build_defaults_and_judge_table() -> None:
    payloads = build_post_persist_generation_diagnostic_payloads(
        project_id=7,
        request_id="req-payment-1",
        generation_id=42,
        normalized_generation_mode="",
        multi_pass=True,
        resolved_current_biz="payment",
        doc_type="requirement",
        compress=False,
        expected_count=5,
        result=[
            {"case_id": "TC-001", "description": "支付成功后生成订单"},
            {"case_id": "TC-002", "description": "支付失败时提示原因"},
        ],
        generated_count=2,
        candidate_total_before_judge=4,
        final_case_count=2,
        empty_result_guard_triggered=True,
        empty_result_stage="persistence_gate",
        gen_diag_payload={},
        compression_event_payload={},
        review_decision_summary_payload={},
        review_decision_table_payload=[{"case_id": "TC-001"}, "ignored"],
        convergence_payload={},
        generation_summary_payload={},
        judge_summary_payload={"rejected_out_count": 1, "pending_out_count": 0},
        judge_decision_table_payload=[
            {
                "case_id": "TC-003",
                "status": "REJECT",
                "reject_reason": "missing_core_flow",
                "signals": {"missing_core_flow": True, "confirmed_fact_hits": ["order"]},
                "before_case_snapshot": {"description": "缺少支付核心链路"},
            },
            {
                "case_id": "TC-001",
                "status": "PASS",
                "signals": {"confirmed_fact_hits": ["payment"]},
                "after_case_snapshot": {"description": "支付成功后生成订单"},
            },
        ],
    )

    pre_judge_by_kind = {payload["kind"]: payload for payload in payloads.pre_judge_payloads}

    assert pre_judge_by_kind["generation_persisted"]["generation_id"] == 42
    assert pre_judge_by_kind["generation_mode"]["mode"] == "multi_pass"
    assert pre_judge_by_kind["gen_diag"]["generated_count"] == 2
    assert pre_judge_by_kind["generation_context_compression"]["snapshot_id"] == ""
    assert pre_judge_by_kind["review_decision_summary"]["dropped_total"] == 2
    assert pre_judge_by_kind["review_decision_table"]["row_count"] == 1
    assert pre_judge_by_kind["generation_convergence"]["review_selected_count"] == 2
    assert pre_judge_by_kind["generation_summary"]["error_code"] == "EMPTY_GENERATED_RESULT"
    assert pre_judge_by_kind["generation_summary"]["error_message"] == "生成完成但最终测试用例为空"

    assert payloads.judge_table_payload is not None
    assert payloads.judge_table_payload["rows_scope"] == "reject_pending_only"
    assert payloads.judge_table_payload["row_count"] == 1
    assert payloads.judge_table_payload["row_count_total"] == 2
    assert payloads.judge_table_payload["row_evidence_incomplete"] is False
    assert payloads.judge_table_payload["rows"][0]["signals"]["missing_core_flow"] is True


def test_emit_pre_persist_generation_diagnostics_writes_logs_and_returns_reusable_payloads(monkeypatch) -> None:
    import modules.test_generation_components.legacy.json_generation_persist_diagnostics as diagnostics_mod
    from modules.test_generation_components.legacy.json_generation_persist_diagnostics import (
        emit_pre_persist_generation_diagnostics,
    )

    class _Client:
        max_tokens = 4096

        def select_model(self, full_input: str, task_type: str) -> str:
            assert "REQ" in full_input
            assert task_type == "generation"
            return "deterministic-model"

    class _LogEntry:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _Db:
        def __init__(self) -> None:
            self.logs: list[_LogEntry] = []
            self.commit_count = 0

        def add(self, obj: _LogEntry) -> None:
            self.logs.append(obj)

        def commit(self) -> None:
            self.commit_count += 1

    monkeypatch.setattr(diagnostics_mod, "LogEntry", _LogEntry)
    db = _Db()
    result = emit_pre_persist_generation_diagnostics(
        db=db,
        client=_Client(),
        project_id=7,
        user_id=1001,
        request_id="req-pre-1",
        normalized_generation_mode="",
        multi_pass=True,
        stage_logs=[{"kind": "generation_stage", "stage": "primary", "case_count": 2}],
        coverage_check_payload={"kind": "coverage_check", "missing_rules": []},
        feedback_control_diag_payload={"workflow_blueprint_count": 2},
        judge_summary_payload={"total": 2, "rejected_out_count": 0},
        memory_diag={"memory_enabled": True},
        system_prompt="SYS",
        requirement="REQ",
        result=[{"id": "TC-001"}, {"id": "TC-002"}],
        context_result={"fusion_debug": {"source": "rag"}},
        doc_type="requirement",
        compress=False,
        expected_count=2,
        kb_context="KB",
        count_unique_test_cases_fn=lambda cases: len(cases),
        build_context_compression_diagnostics_fn=lambda **kwargs: {
            "compression_ratio": 0.25,
            "retained_chunk_count": 3,
            "relevance_distribution": {"high": 2},
        },
    )

    payloads = [json.loads(str(item.message).removeprefix("GEN_DIAG:")) for item in db.logs]
    kinds = [payload["kind"] for payload in payloads]

    assert db.commit_count == 1
    assert kinds == [
        "generation_stage",
        "coverage_check",
        "feedback_control_state",
        "judge_summary",
        "memory_fabric_diag",
        "gen_diag",
        "generation_context_compression",
    ]
    assert payloads[0]["request_id"] == "req-pre-1"
    assert payloads[2]["workflow_blueprint_count"] == 2
    assert result.gen_diag_payload["generated_count"] == 2
    assert result.gen_diag_payload["model"] == "deterministic-model"
    assert result.compression_event_payload["compression_ratio"] == 0.25


def test_emit_post_persist_coverage_audit_diagnostics_writes_generic_coverage_log(monkeypatch) -> None:
    import modules.test_generation_components.legacy.json_generation_persist_diagnostics as diagnostics_mod

    class _LogEntry:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _Db:
        def __init__(self) -> None:
            self.logs: list[_LogEntry] = []
            self.commit_count = 0

        def add(self, obj: _LogEntry) -> None:
            self.logs.append(obj)

        def commit(self) -> None:
            self.commit_count += 1

    monkeypatch.setattr(diagnostics_mod, "LogEntry", _LogEntry)

    db = _Db()
    emit_post_persist_coverage_audit_diagnostics(
        db=db,
        project_id=7,
        user_id=1001,
        result=[
            {
                "id": "TC-001",
                "test_module": "支付",
                "description": "支付成功",
                "expected_result": "订单状态为已支付",
            }
        ],
        requirement="支付后需要支持退款链路。",
        kb_context="支付上下文",
        context_result={"fusion_debug": {"context_source": "rag"}},
        expected_count=3,
        coverage_diagnostics_enabled=True,
        build_coverage_diagnostics_fn=lambda **kwargs: {
            "kind": "coverage_check",
            "generated_count": len(kwargs["generated_cases"]),
            "expected_count": kwargs["expected_count"],
        },
    )

    messages = [str(item.message) for item in db.logs]
    assert messages[0].startswith("GEN_COVERAGE_DIAG:")
    coverage_payload = json.loads(messages[0].removeprefix("GEN_COVERAGE_DIAG:"))

    assert db.commit_count == 1
    assert len(messages) == 1
    assert coverage_payload["generated_count"] == 1
