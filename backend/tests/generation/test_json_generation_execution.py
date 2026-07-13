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
        "expected_count": 2,
        "batch_size": 5,
        "start_id": 7,
        "normalized_generation_mode": "multi_pass",
        "multi_pass": True,
        "generation_mode": "multi_pass",
        "strategy_plan": {"focus": "order"},
        "doc_type": "requirement",
        "clean_and_parse_json_fn": lambda text: text,
        "normalize_json_structure_fn": lambda payload: payload,
        "deduplicate_test_cases_fn": lambda cases: cases,
        "reorder_cases_by_closed_loop_fn": lambda cases, **_: cases,
        "run_multi_pass_generation_fn": lambda **_: {},
        "finalize_generated_cases_fn": lambda response, **_: response,
        "analyze_coverage_fn": lambda requirement, cases: {"covered_count": len(cases)},
        "build_closed_loop_base_prompt_fn": lambda *args, **kwargs: "BASE",
    }
    kwargs.update(overrides)
    return kwargs


def test_run_json_generation_execution_uses_pipeline_for_multi_pass() -> None:
    calls = {}

    def fake_multi_pass_generation(**kwargs):
        calls.update(kwargs)
        return {
            "final_cases": [_case("C-001")],
            "stage_logs": [{"kind": "generation_stage", "stage": "primary", "case_count": 1}],
            "coverage": {"kind": "coverage_check", "covered_count": 1},
            "raw": {"primary": "payload"},
        }

    result = run_json_generation_execution(
        **_base_kwargs(run_multi_pass_generation_fn=fake_multi_pass_generation)
    )

    assert calls["base_prompt"] == "SYSTEM"
    assert calls["requirement_context"] == "order flow context"
    assert calls["current_biz_key"] == "order"
    assert result.result == [_case("C-001")]
    assert result.coverage_check_payload == {"kind": "coverage_check", "covered_count": 1}
    assert result.raw_response_payload == {"primary": "payload"}


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
            multi_pass=False,
            generation_mode="",
            finalize_generated_cases_fn=lambda response, **_: [_case("C-002")],
        )
    )

    assert result.result == [_case("C-002")]
    assert result.stage_logs[0]["mode"] == "single_pass"
    assert result.stage_logs[-1]["stage"] == "review"
    assert result.coverage_check_payload == {"kind": "coverage_check", "covered_count": 1}
    assert result.raw_response_payload == "raw response"
