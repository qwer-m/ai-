from modules.test_generation_components.legacy.json_generation_execution import (
    run_json_generation_execution,
)


def _case(case_id: str) -> dict:
    return {
        "case_id": case_id,
        "description": f"case {case_id}",
        "steps": ["open"],
        "expected_result": "shown",
        "priority": "P1",
    }


def _base_kwargs(**overrides):
    kwargs = {
        "client": object(),
        "requirement": "order flow",
        "db": None,
        "system_prompt": "SYSTEM",
        "prompt_context": {"requirement_context": "order flow context"},
        "resolved_current_biz": "order",
        "start_id": 7,
        "normalized_generation_mode": "multi_pass",
        "clean_and_parse_json_fn": lambda text: text,
        "normalize_json_structure_fn": lambda payload: payload,
        "deduplicate_test_cases_fn": lambda cases: cases,
        "reorder_cases_by_closed_loop_fn": lambda cases, **_: cases,
        "finalize_generated_cases_fn": lambda response, **_: response,
        "analyze_coverage_fn": lambda requirement, cases: {"covered_count": len(cases)},
    }
    kwargs.update(overrides)
    return kwargs


def test_run_json_generation_execution_uses_one_global_candidate_call_for_multi_mode() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.call_count = 0

        def generate_response(self, requirement, system_prompt, **kwargs):
            self.call_count += 1
            assert requirement == "order flow"
            assert system_prompt == "SYSTEM"
            assert kwargs["task_type"] == "generation"
            return "raw response"

    client = FakeClient()
    result = run_json_generation_execution(
        **_base_kwargs(
            client=client,
            finalize_generated_cases_fn=lambda response, **_: [_case("C-001")],
        )
    )

    assert client.call_count == 1
    assert result.result == [_case("C-001")]
    assert result.coverage_check_payload == {"kind": "coverage_check", "covered_count": 1}
    assert result.raw_response_payload == "raw response"
    assert result.stage_logs[0]["mode"] == "multi_pass"


def test_run_json_generation_execution_builds_single_pass_stage_logs() -> None:
    class FakeClient:
        def generate_response(self, requirement, system_prompt, **kwargs):
            assert requirement == "order flow"
            assert system_prompt == "SYSTEM"
            assert kwargs["task_type"] == "generation"
            return "raw response"

    result = run_json_generation_execution(
        **_base_kwargs(
            client=FakeClient(),
            normalized_generation_mode="",
            finalize_generated_cases_fn=lambda response, **_: [_case("C-002")],
        )
    )

    assert result.result == [_case("C-002")]
    assert result.stage_logs[0]["mode"] == "global_candidates"
    assert result.stage_logs[-1]["stage"] == "gap"
    assert result.coverage_check_payload == {"kind": "coverage_check", "covered_count": 1}
    assert result.raw_response_payload == "raw response"
