import modules.test_generation_components.legacy.json_generation_review_postprocess as review_postprocess_mod
from modules.test_generation_components.legacy.json_generation_review_postprocess import (
    run_json_review_postprocess,
)


def _case(case_id: str) -> dict:
    return {
        "case_id": case_id,
        "description": f"case {case_id}",
        "steps": ["open", "submit"],
        "expected_result": "saved",
        "priority": "P1",
    }


def _base_kwargs(**overrides):
    kwargs = {
        "result": [_case("C-001")],
        "db": None,
        "client": object(),
        "requirement": "order flow",
        "base_prompt": "BASE",
        "kb_context": "",
        "expected_count": 2,
        "start_id": 1,
        "resolved_current_biz": "order",
        "multi_pass": True,
        "generation_mode": "multi_pass",
        "feedback_control_state": {},
        "prompt_context": {"requirement_context": "order flow"},
        "stage_counts": {"primary": 1, "gap": 0, "review": 1},
        "coverage_check_payload": {"kind": "coverage_check", "covered_count": 1},
        "clean_and_parse_json_fn": lambda text: text,
        "normalize_json_structure_fn": lambda payload: payload,
        "deduplicate_test_cases_fn": lambda cases: cases,
        "reorder_cases_by_closed_loop_fn": lambda cases, **_: cases,
        "count_unique_test_cases_fn": lambda cases: len(cases),
        "infer_case_kind_fn": lambda case: "normal",
        "build_supplement_closed_loop_instruction_fn": lambda **_: "",
        "build_requirement_semantics_payload_fn": lambda prompt_context: {
            "confirmed_facts": [prompt_context["requirement_context"]]
        },
        "stream_postprocess_cases_fn": lambda **_: iter(()),
    }
    kwargs.update(overrides)
    return kwargs


def test_run_json_review_postprocess_maps_stream_payload_when_session_available(monkeypatch) -> None:
    class FakeSession:
        pass

    monkeypatch.setattr(review_postprocess_mod, "Session", FakeSession)

    def fake_stream_postprocess_cases(**kwargs):
        assert kwargs["current_biz_key"] == "order"
        assert kwargs["requirement_semantics_context"] == {"confirmed_facts": ["order flow"]}
        if False:
            yield ""
        return {
            "cases": [_case("C-002")],
            "stage_counts": {"primary": 1, "gap": 0, "review": 1},
            "coverage": {"kind": "coverage_check", "covered_count": 1},
            "convergence_debug": {"candidate_count_before_review": 4},
            "generation_summary": {"final_count": 1},
            "review_decision_summary": {"candidate_total": 4, "retained_total": 1},
            "review_decision_table": [{"case_id": "C-002"}],
            "judge_summary": {"pass_count": 1},
            "judge_decision_table": [{"case_id": "C-002", "status": "pass"}],
        }

    result = run_json_review_postprocess(
        **_base_kwargs(
            db=FakeSession(),
            stream_postprocess_cases_fn=fake_stream_postprocess_cases,
        )
    )

    assert result.stream_postprocess_applied is True
    assert result.result == [_case("C-002")]
    assert result.candidate_total_before_judge == 4
    assert result.final_case_count == 1
    assert result.review_decision_summary_payload == {"candidate_total": 4, "retained_total": 1}
    assert result.judge_summary_payload == {"pass_count": 1}


def test_run_json_review_postprocess_fails_closed_without_stream_session() -> None:
    result = run_json_review_postprocess(**_base_kwargs())

    assert result.stream_postprocess_applied is False
    assert result.result["error_code"] == "GLOBAL_REVIEW_REQUIRED"
    assert result.result["review_failure_reason"] == "active_review_session_unavailable"
    assert result.candidate_total_before_judge == 1
    assert result.final_case_count == 0
    assert result.review_decision_summary_payload["review_applied"] is False


def test_run_json_review_postprocess_fails_closed_when_global_review_raises(monkeypatch) -> None:
    class FakeSession:
        pass

    monkeypatch.setattr(review_postprocess_mod, "Session", FakeSession)

    def broken_stream_postprocess(**_kwargs):
        raise RuntimeError("review unavailable")
        yield  # pragma: no cover

    result = run_json_review_postprocess(
        **_base_kwargs(
            db=FakeSession(),
            stream_postprocess_cases_fn=broken_stream_postprocess,
        )
    )

    assert result.stream_postprocess_applied is False
    assert result.result["error_code"] == "GLOBAL_REVIEW_REQUIRED"
    assert result.result["review_failure_reason"] == "global_review_exception:RuntimeError"
