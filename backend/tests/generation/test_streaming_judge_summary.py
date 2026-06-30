from __future__ import annotations

from modules.test_generation_components.judge.judge_types import (
    JudgeBatchResult,
    JudgeResult,
    JudgeSignalSet,
    JudgeStatus,
)
from modules.test_generation_components.postprocess.streaming_judge_summary import (
    build_judge_decision_table_payload,
    build_judge_summary_payload,
)


def _case(case_id: str) -> dict[str, str]:
    return {
        "id": case_id,
        "test_module": "course",
        "description": f"{case_id} submits homework",
        "expected_result": f"{case_id} is visible to teacher",
    }


def test_build_judge_summary_payload_counts_outputs_and_fact_profile() -> None:
    rejected = JudgeResult(
        case_id="TC-REJECT",
        status=JudgeStatus.REJECT,
        signals=JudgeSignalSet(violates_confirmed_fact=True),
        reject_reason="confirmed_fact_violation",
        before_case=_case("TC-REJECT"),
    )
    repaired = JudgeBatchResult(
        cases=[
            JudgeResult(
                case_id="TC-PASS",
                status=JudgeStatus.PASS,
                signals=JudgeSignalSet(),
                before_case=_case("TC-PASS"),
            ),
            JudgeResult(
                case_id="TC-REPAIR",
                status=JudgeStatus.REPAIRABLE,
                signals=JudgeSignalSet(missing_reuse_risk=True),
                repaired=True,
                repaired_pass=True,
                before_case=_case("TC-REPAIR"),
                after_case=_case("TC-REPAIR-FIXED"),
            ),
            rejected,
            JudgeResult(
                case_id="TC-PENDING",
                status=JudgeStatus.PENDING,
                signals=JudgeSignalSet(contains_pending_logic=True),
                pending_reason="pending_requirement",
                before_case=_case("TC-PENDING"),
            ),
        ],
        core_flow_covered=True,
        reuse_risk_covered=False,
        pass_count=1,
        repairable_count=1,
        reject_count=1,
        pending_count=1,
        appended_case_count=0,
        repaired_case_count=1,
    )

    payload = build_judge_summary_payload(
        repaired=repaired,
        confirmed_pass_cases=[_case("TC-PASS")],
        repaired_pass_cases=[_case("TC-REPAIR-FIXED")],
        rejected_cases=[_case("TC-REJECT")],
        pending_cases=[_case("TC-PENDING")],
        fact_profile={
            "profile_source": "requirement_semantics",
            "confidence": 0.75,
            "confirmed_facts": ["homework submit creates teacher-visible record"],
            "forbidden_facts": ["teacher cannot see student draft before submit"],
            "pending_items": ["late submit policy"],
        },
    )

    assert payload["pass_count"] == 1
    assert payload["repairable_count"] == 1
    assert payload["raw_repairable_count"] == 1
    assert payload["remaining_repairable_count"] == 0
    assert payload["unrepaired_repairable_count"] == 0
    assert payload["reject_count"] == 1
    assert payload["pending_count"] == 1
    assert payload["confirmed_pass_out_count"] == 1
    assert payload["repaired_pass_out_count"] == 1
    assert payload["rejected_out_count"] == 1
    assert payload["pending_out_count"] == 1
    assert payload["fact_violation_count"] == 1
    assert payload["core_flow_covered"] is True
    assert payload["reuse_risk_covered"] is False
    assert payload["fact_profile_source"] == "requirement_semantics"
    assert payload["fact_profile_confidence"] == 0.75
    assert payload["fact_profile_confirmed_count"] == 1
    assert payload["fact_profile_forbidden_count"] == 1
    assert payload["fact_profile_pending_count"] == 1


def test_build_judge_decision_table_payload_expands_signals_and_case_snapshots() -> None:
    repaired = JudgeBatchResult(
        cases=[
            JudgeResult(
                case_id="TC-DUP",
                status=JudgeStatus.REJECT,
                signals=JudgeSignalSet(
                    violates_confirmed_fact=True,
                    missing_core_flow=True,
                    missing_reuse_risk=True,
                    contains_pending_logic=True,
                    is_semantic_duplicate=True,
                    duplicate_of_case_id="TC-001",
                    duplicate_similarity=0.92,
                    confirmed_fact_hits=["submit_record"],
                    confirmed_fact_violations=["draft_visible_to_teacher"],
                    reuse_risk_hits=["same_student_reuse"],
                    missing_reuse_risk_items=["different_student_scope"],
                    pending_hits=["late_submit_policy"],
                    vague_or_unconfirmed_hits=["maybe asynchronous"],
                    notes=["dropped by judge"],
                ),
                reject_reason="semantic_duplicate",
                before_case=_case("TC-DUP-BEFORE"),
                after_case=_case("TC-DUP-AFTER"),
            )
        ]
    )

    rows = build_judge_decision_table_payload(repaired=repaired)

    assert len(rows) == 1
    row = rows[0]
    assert row["case_id"] == "TC-DUP"
    assert row["status"] == "REJECT"
    assert row["reject_reason"] == "semantic_duplicate"
    assert row["has_before_case"] is True
    assert row["has_after_case"] is True
    assert row["before_case_id"] == "TC-DUP-BEFORE"
    assert row["after_case_id"] == "TC-DUP-AFTER"
    assert row["violates_confirmed_fact"] is True
    assert row["contains_pending_logic"] is True
    assert row["missing_core_flow"] is True
    assert row["missing_reuse_risk"] is True
    assert row["confirmed_fact_hits"] == ["submit_record"]
    assert row["confirmed_fact_violations"] == ["draft_visible_to_teacher"]
    assert row["reuse_risk_hits"] == ["same_student_reuse"]
    assert row["missing_reuse_risk_items"] == ["different_student_scope"]
    assert row["pending_hits"] == ["late_submit_policy"]
    assert row["vague_or_unconfirmed_hits"] == ["maybe asynchronous"]
    assert row["is_semantic_duplicate"] is True
    assert row["duplicate_of_case_id"] == "TC-001"
    assert row["duplicate_similarity"] == 0.92
    assert row["before_case_snapshot"] == _case("TC-DUP-BEFORE")
    assert row["after_case_snapshot"] == _case("TC-DUP-AFTER")
    assert row["notes"] == ["dropped by judge"]
    assert row["signals"]["duplicate_similarity"] == 0.92
