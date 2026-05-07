from __future__ import annotations

from modules.test_generation_components.control.generation_mode_activation import (
    build_generation_mode_control_state,
    infer_generation_coverage_profile,
)
from modules.testing.test_generation_components.legacy.adapters import (
    clean_and_parse_json,
    count_unique_test_cases,
    deduplicate_test_cases,
    infer_case_kind,
    normalize_json_structure,
    reorder_cases_by_closed_loop,
)
from modules.testing.test_generation_components.postprocess.result_postprocess import stream_postprocess_cases
from modules.test_generation_components.prompting.structured_context import build_structured_prompt_context


class _NoopClient:
    def generate_response(self, requirement: str, prompt: str, db=None, **kwargs):  # noqa: ANN001, ARG002
        return "[]"

    def generate_response_stream(self, requirement: str, prompt: str, **kwargs):  # noqa: ANN001, ARG002
        yield "[]"


def _drain_with_return(gen):
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value


def test_full_functional_regression_profile_activates_from_expected_count() -> None:
    profile = infer_generation_coverage_profile(
        requirement_text="近期课程和排课功能调整",
        expected_count=100,
    )

    assert profile["coverage_mode"] == "full_functional_regression"
    assert profile["target_case_range"] == {"min": 80, "max": 120}
    assert len(profile["coverage_layers"]) >= 5


def test_standard_regression_profile_activates_from_document_intent() -> None:
    profile = infer_generation_coverage_profile(
        requirement_text="本次改版需要做标准回归，确保原有模块不受影响",
        expected_count=20,
    )

    assert profile["coverage_mode"] == "standard_regression"
    assert profile["target_case_range"] == {"min": 30, "max": 50}


def test_generation_mode_control_reaches_structured_prompt_context() -> None:
    state = build_generation_mode_control_state(
        requirement_text="全功能测试：覆盖入口、核心流程、异常和跨模块回归",
        expected_count=100,
    )
    prompt_context = build_structured_prompt_context(
        requirement="全功能测试：覆盖入口、核心流程、异常和跨模块回归",
        feedback_control_state=state,
    )

    summary = dict(prompt_context.get("control_summary") or {})
    control_text = str(prompt_context.get("control_context") or "")
    assert summary["generation_coverage_mode"] == "full_functional_regression"
    assert summary["generation_target_case_range"] == {"min": 80, "max": 120}
    assert "GENERATION COVERAGE MODE" in control_text
    assert "full_functional_regression" in control_text
    assert "not a quota" in control_text


def test_full_functional_mode_prevents_review_gate_compressing_dense_case_set() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for a management workflow",
        expected_count=100,
    )
    cases = [
        {
            "id": f"TC-{idx:03d}",
            "description": f"Validate management workflow checkpoint {idx}",
            "test_module": f"module-{idx % 8}",
            "preconditions": ["User is logged in", f"Dataset {idx} exists"],
            "steps": [
                f"1. Open workflow area {idx % 8}",
                f"2. Execute action for checkpoint {idx}",
                f"3. Refresh and re-open checkpoint {idx}",
            ],
            "test_input": f"checkpoint-{idx}",
            "expected_result": (
                f"Checkpoint {idx} shows saved state {idx} after refresh, "
                f"and related module {idx % 8} displays the updated record."
            ),
            "priority": "P1" if idx % 3 else "P2",
        }
        for idx in range(1, 41)
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="full functional regression for a management workflow",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    assert isinstance(result, dict)
    assert len(result.get("cases") or []) >= 30
    summary = dict(result.get("generation_summary") or {})
    assert summary["generation_coverage_mode"] == "full_functional_regression"
    assert summary["recommended_range"] == "80-120"
    debug = dict(result.get("feedback_control_debug") or {})
    assert debug["generation_coverage_mode"] == "full_functional_regression"
