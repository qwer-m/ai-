import json

from modules.test_generation_components.legacy.json_generation_payloads import (
    build_core_flow_backfill_apply_summary_payload,
)
from modules.test_generation_components.legacy.json_generation_persist_diagnostics import (
    build_post_persist_generation_diagnostic_payloads,
    emit_post_persist_coverage_audit_diagnostics,
)


def test_core_flow_backfill_apply_summary_payload_normalizes_counts_and_coverage() -> None:
    payload = build_core_flow_backfill_apply_summary_payload(
        request_id="req-1",
        normalized_generation_mode="",
        multi_pass=True,
        backfill_enabled=1,
        backfill_apply_to_final="yes",
        backfill_applied="",
        primary_case_count_before_backfill="3",
        result=[{"id": 1}, "ignored", {"id": 2}],
        core_flow_backfill_generation_result={
            "generated_backfill_candidate_cases": [{"id": "g1"}, {"id": "g2"}],
            "accepted_backfill_cases": [{"id": "a1"}],
            "rejected_backfill_cases": [],
            "accepted_for_preview_count": "4",
            "primary_retained_count": 2,
            "primary_trimmed_count": "1",
            "backfill_retained_count": 3,
            "backfill_trimmed_count": "0",
        },
        core_flow_coverage_before_apply={
            "core_flow_covered_count": "1",
            "core_flow_required_count": "4",
            "core_flow_coverage_ratio": "0.25",
        },
        core_flow_coverage_after_apply={
            "core_flow_covered_count": 3,
            "core_flow_required_count": 4,
            "core_flow_coverage_ratio": 0.75,
        },
        core_flow_still_missing_after_apply=("payment", "refund"),
        final_quality_gate_passed=0,
        apply_skip_reason=None,
    )

    assert payload == {
        "kind": "core_flow_backfill_apply_summary",
        "request_id": "req-1",
        "generation_mode": "multi_pass",
        "backfill_enabled": True,
        "backfill_apply_to_final": True,
        "backfill_applied": False,
        "primary_case_count": 3,
        "final_case_count": 2,
        "generated_backfill_candidate_count": 2,
        "accepted_backfill_candidate_count": 1,
        "rejected_backfill_candidate_count": 0,
        "accepted_for_preview_count": 4,
        "primary_retained_count": 2,
        "primary_trimmed_count": 1,
        "backfill_retained_count": 3,
        "backfill_trimmed_count": 0,
        "coverage_before": {
            "covered_count": 1,
            "required_count": 4,
            "coverage_ratio": 0.25,
        },
        "coverage_after": {
            "covered_count": 3,
            "required_count": 4,
            "coverage_ratio": 0.75,
        },
        "still_missing_core_flows": ["payment", "refund"],
        "final_quality_gate_passed": False,
        "apply_skip_reason": "",
    }


def test_core_flow_backfill_apply_summary_payload_prefers_normalized_generation_mode() -> None:
    payload = build_core_flow_backfill_apply_summary_payload(
        request_id="req-2",
        normalized_generation_mode="full",
        multi_pass=False,
        backfill_enabled=False,
        backfill_apply_to_final=False,
        backfill_applied=True,
        primary_case_count_before_backfill=1,
        result={"not": "a-list"},
        core_flow_backfill_generation_result={},
        core_flow_coverage_before_apply={},
        core_flow_coverage_after_apply={},
        core_flow_still_missing_after_apply=[],
        final_quality_gate_passed=True,
        apply_skip_reason="merged_result_quality_gate_failed",
    )

    assert payload["generation_mode"] == "full"
    assert payload["final_case_count"] == 0
    assert payload["coverage_before"] == {
        "covered_count": 0,
        "required_count": 0,
        "coverage_ratio": 0.0,
    }
    assert payload["coverage_after"] == {
        "covered_count": 0,
        "required_count": 0,
        "coverage_ratio": 0.0,
    }
    assert payload["apply_skip_reason"] == "merged_result_quality_gate_failed"


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


def test_emit_post_persist_coverage_audit_diagnostics_writes_coverage_and_backfill_logs(monkeypatch) -> None:
    import modules.test_generation_components.coverage.core_flow_backfill as backfill_mod
    import modules.test_generation_components.coverage.core_flow_coverage_contract as coverage_mod
    import modules.test_generation_components.legacy.json_generation_persist_diagnostics as diagnostics_mod
    from core.settings.config import settings

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
    monkeypatch.setattr(settings, "CORE_FLOW_BACKFILL_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "CORE_FLOW_BACKFILL_MAX_CANDIDATES", 3, raising=False)
    monkeypatch.setattr(
        coverage_mod,
        "audit_core_flow_coverage",
        lambda cases: {
            "core_flow_covered_count": 1,
            "core_flow_required_count": 2,
            "core_flow_coverage_ratio": 0.5,
            "core_flow_coverage_passed": False,
            "missing_core_flows": ["refund"],
            "false_positive_guard_notes": ["note"],
        },
    )

    def _plan_core_flow_backfill(**kwargs):
        assert kwargs["max_backfill_cases"] == 3
        return {
            "missing_core_flow_count": 1,
            "backfill_plan": [
                {
                    "flow_key": "refund",
                    "flow_name": "退款",
                    "suggested_priority": "P1",
                    "target_case_count": 1,
                }
            ],
        }

    monkeypatch.setattr(backfill_mod, "plan_core_flow_backfill", _plan_core_flow_backfill)

    db = _Db()
    emit_post_persist_coverage_audit_diagnostics(
        db=db,
        project_id=7,
        user_id=1001,
        request_id="req-coverage-1",
        generation_id=42,
        normalized_generation_mode="",
        multi_pass=True,
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
    core_flow_payload = json.loads(messages[1].removeprefix("GEN_DIAG:"))
    backfill_payload = json.loads(messages[2].removeprefix("GEN_DIAG:"))

    assert db.commit_count == 3
    assert coverage_payload["generated_count"] == 1
    assert core_flow_payload["kind"] == "core_flow_coverage"
    assert core_flow_payload["generation_mode"] == "multi_pass"
    assert core_flow_payload["missing_core_flows"] == ["refund"]
    assert backfill_payload["kind"] == "core_flow_backfill_dry_run"
    assert backfill_payload["backfill_plan_summary"] == [
        {
            "flow_key": "refund",
            "flow_name": "退款",
            "suggested_priority": "P1",
            "target_case_count": 1,
        }
    ]
