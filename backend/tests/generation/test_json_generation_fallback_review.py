from modules.test_generation_components.judge.judge_types import (
    JudgeBatchResult,
    JudgeResult,
    JudgeSignalSet,
    JudgeStatus,
)
import modules.test_generation_components.legacy.json_generation_fallback_review as fallback_mod


def _case(
    case_id: str,
    *,
    description: str,
    priority: str = "P1",
    execution_group: str = "",
) -> dict:
    payload = {
        "case_id": case_id,
        "test_module": "order",
        "description": description,
        "steps": ["open order page", "submit order"],
        "test_input": "valid cart",
        "expected_result": "order is created",
        "priority": priority,
        "priority_final": priority,
        "priority_decision_state": "decided",
    }
    if execution_group:
        payload["execution_group"] = execution_group
    return payload


def test_run_json_fallback_review_builds_review_tables_from_judge_gate(monkeypatch) -> None:
    retained_case = _case("C-001", description="submit order successfully", priority="P0")
    rejected_case = _case("C-002", description="submit order without required confirmation", priority="P1")
    duplicate_case = dict(retained_case, case_id="C-003")

    def fake_judge_cases(*, cases, requirement_semantics_context, control_state):
        assert requirement_semantics_context["confirmed_facts"] == ["order requires confirmation"]
        assert control_state == {"workflow_blueprint_count": 1}
        assert [item["case_id"] for item in cases] == ["C-001", "C-002", "C-003"]
        return JudgeBatchResult(
            cases=[
                JudgeResult(
                    case_id="C-001",
                    status=JudgeStatus.PASS,
                    signals=JudgeSignalSet(confirmed_fact_hits=["order"]),
                    before_case=retained_case,
                    after_case=retained_case,
                ),
                JudgeResult(
                    case_id="C-002",
                    status=JudgeStatus.REJECT,
                    signals=JudgeSignalSet(missing_core_flow=True),
                    reject_reason="missing_confirmation_flow",
                    before_case=rejected_case,
                ),
                JudgeResult(
                    case_id="C-003",
                    status=JudgeStatus.PASS,
                    signals=JudgeSignalSet(confirmed_fact_hits=["order"]),
                    before_case=duplicate_case,
                    after_case=duplicate_case,
                ),
            ],
            core_flow_covered=True,
            reuse_risk_covered=False,
            pass_count=2,
            reject_count=1,
        )

    monkeypatch.setattr(fallback_mod, "judge_cases", fake_judge_cases)

    review = fallback_mod.run_json_fallback_review(
        result=[retained_case, rejected_case, duplicate_case],
        prompt_context={
            "requirement_context": "order requires confirmation",
            "confirmed_facts": ["order requires confirmation"],
        },
        requirement="order requires confirmation",
        feedback_control_state={"workflow_blueprint_count": 1},
        stage_logs=[{"kind": "generation_stage", "stage": "primary", "case_count": 3}],
        expected_count=3,
        start_id=7,
    )

    assert review.result == [
        {
            **retained_case,
            "id": "TC-007",
        }
    ]
    assert review.candidate_total_before_judge == 3
    assert review.final_case_count == 1
    assert review.empty_result_guard_triggered is False
    assert review.coverage_check_payload["kind"] == "coverage_check"

    assert review.judge_summary_payload["pass_count"] == 2
    assert review.judge_summary_payload["reject_count"] == 1
    assert review.judge_summary_payload["confirmed_pass_out_count"] == 1
    assert review.judge_summary_payload["rejected_out_count"] == 1

    assert review.review_decision_summary_payload["candidate_total"] == 3
    assert review.review_decision_summary_payload["retained_total"] == 1
    assert review.review_decision_summary_payload["dropped_total"] == 2
    assert review.review_decision_summary_payload["drop_by_review_gate_count"] == 1
    assert review.review_decision_summary_payload["drop_by_post_review_dedup_count"] == 1
    assert review.review_decision_summary_payload["priority_final_breakdown"] == {
        "P0": 1,
        "P1": 0,
        "P2": 0,
        "null": 0,
    }

    rows_by_case_id = {row["case_id"]: row for row in review.review_decision_table_payload}
    assert rows_by_case_id["C-001"]["retained_final"] is True
    assert rows_by_case_id["C-002"]["dropped_stage"] == "review_gate"
    assert rows_by_case_id["C-002"]["dropped_reason"] == "missing_confirmation_flow"
    assert rows_by_case_id["C-003"]["dropped_stage"] == "post_review_dedup"


def test_run_json_fallback_review_returns_empty_result_guard_when_gate_drops_all(monkeypatch) -> None:
    rejected_case = _case("C-010", description="submit order without payment", priority="P1")

    def fake_judge_cases(*, cases, requirement_semantics_context, control_state):
        assert len(cases) == 1
        return JudgeBatchResult(
            cases=[
                JudgeResult(
                    case_id="C-010",
                    status=JudgeStatus.REJECT,
                    signals=JudgeSignalSet(missing_core_flow=True),
                    reject_reason="missing_payment_flow",
                    before_case=rejected_case,
                )
            ],
            core_flow_covered=False,
            reuse_risk_covered=False,
            reject_count=1,
        )

    monkeypatch.setattr(fallback_mod, "judge_cases", fake_judge_cases)

    review = fallback_mod.run_json_fallback_review(
        result=[rejected_case],
        prompt_context={"requirement_context": "payment must complete"},
        requirement="payment must complete",
        feedback_control_state={},
        stage_logs=[],
        expected_count=1,
        start_id=1,
    )

    assert review.result["error_code"] == "EMPTY_GENERATED_RESULT"
    assert review.result["empty_result_guard_triggered"] is True
    assert review.empty_result_stage == "post_judge_training_gate"
    assert review.final_case_count == 0
    assert review.generation_summary_payload["status"] == "failed_empty_result"
    assert review.review_decision_summary_payload["drop_by_review_gate_count"] == 1
