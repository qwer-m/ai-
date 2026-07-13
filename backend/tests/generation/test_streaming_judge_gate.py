from modules.test_generation_components.postprocess.streaming_judge_gate import (
    run_streaming_judge_gate,
)


def _case(case_id: str) -> dict[str, str]:
    return {
        "id": case_id,
        "description": f"{case_id} flow",
        "expected_result": f"{case_id} done",
    }


def test_run_streaming_judge_gate_applies_training_outputs_and_payloads() -> None:
    def judge_cases_fn(**kwargs):
        assert kwargs["control_state"] == {"enabled": True}
        assert kwargs["requirement_semantics_context"] == {"facts": ["x"]}
        return {"judged": kwargs["cases"]}

    def repair_cases_fn(**kwargs):
        assert kwargs["strategy"] == "rule_first_llm_fallback"
        return {"repaired": kwargs["judged"]}

    def training_gate_fn(repaired):
        assert "repaired" in repaired
        return ([_case("TC-002"), _case("TC-001")], [_case("TC-003")], [_case("TC-004")], [])

    result = run_streaming_judge_gate(
        cases=[_case("TC-000")],
        requirement_semantics_context={"facts": ["x"]},
        feedback_control_state={"enabled": True},
        fact_profile={"profile_source": "test"},
        start_id=10,
        deduplicate_test_cases_fn=lambda cases: cases,
        reorder_cases_by_closed_loop_fn=lambda cases, **kwargs: [
            {**case, "id": f"TC-{index:03d}"}
            for index, case in enumerate(cases, start=kwargs["start_id"])
        ],
        review_case_id_fn=lambda case: str(case.get("id") or ""),
        build_judge_summary_payload_fn=lambda **kwargs: {
            "confirmed": len(kwargs["confirmed_pass_cases"]),
            "repaired": len(kwargs["repaired_pass_cases"]),
            "profile_source": kwargs["fact_profile"]["profile_source"],
        },
        build_judge_decision_table_payload_fn=lambda **kwargs: [
            {"case_id": "TC-010", "status": "PASS"}
        ],
        judge_cases_fn=judge_cases_fn,
        repair_cases_fn=repair_cases_fn,
        training_gate_fn=training_gate_fn,
    )

    assert [case["id"] for case in result.cases] == ["TC-010", "TC-011", "TC-012"]
    assert result.judge_summary_payload == {
        "confirmed": 2,
        "repaired": 1,
        "profile_source": "test",
    }
    assert result.judge_decision_table_payload == [{"case_id": "TC-010", "status": "PASS"}]


def test_run_streaming_judge_gate_preserves_current_cases_when_payload_build_fails() -> None:
    def training_gate_fn(repaired):
        return ([_case("TC-PASS")], [], [], [])

    result = run_streaming_judge_gate(
        cases=[_case("TC-ORIGINAL")],
        requirement_semantics_context={},
        feedback_control_state={},
        fact_profile={},
        start_id=1,
        deduplicate_test_cases_fn=lambda cases: cases,
        reorder_cases_by_closed_loop_fn=lambda cases, **kwargs: cases,
        review_case_id_fn=lambda case: str(case.get("id") or ""),
        build_judge_summary_payload_fn=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        build_judge_decision_table_payload_fn=lambda **kwargs: [],
        judge_cases_fn=lambda **kwargs: kwargs["cases"],
        repair_cases_fn=lambda **kwargs: kwargs["judged"],
        training_gate_fn=training_gate_fn,
    )

    assert result.cases == [_case("TC-PASS")]
    assert result.judge_summary_payload == {}
    assert result.judge_decision_table_payload == []
