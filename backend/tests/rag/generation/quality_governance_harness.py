from __future__ import annotations

import json
import re
from typing import Any

from modules.testing.test_generation_components.legacy.adapters import (
    clean_and_parse_json,
    count_unique_test_cases,
    deduplicate_test_cases,
    infer_case_kind,
    normalize_json_structure,
    reorder_cases_by_closed_loop,
)
from modules.testing.test_generation_components.postprocess.result_postprocess import (
    stream_postprocess_cases,
)


def drain_generator_with_return(gen):
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value


class DeterministicReviewClient:
    """Deterministic reviewer used to keep governance tests off network calls."""

    model = "deepseek-chat"
    turbo_model = "deepseek-chat"

    def select_model(self, full_input: str, task_type: str = "generation") -> str:  # noqa: ARG002
        return "deepseek-chat"

    def generate_response(self, requirement: str, prompt: str, db: Any = None, **kwargs) -> str:  # noqa: ARG002
        if str(prompt or "").strip() != "You are a QA Auditor.":
            return "[]"
        review_prompt = str(requirement or "")
        ids = sorted(set(re.findall(r"TC-\d{3}", review_prompt)))
        return json.dumps({"kept_case_ids": ids, "dropped": []}, ensure_ascii=False)

    def generate_response_stream(self, requirement: str, prompt: str, **kwargs):  # noqa: ANN001, ARG002
        yield "[]"


def run_quality_governance_cases(
    requirement: str,
    cases: list[dict[str, Any]],
    *,
    expected_count: int = 30,
    feedback_control_state: dict[str, Any] | None = None,
    normalize_json_structure_fn=normalize_json_structure,
) -> dict[str, Any]:
    gen = stream_postprocess_cases(
        client=DeterministicReviewClient(),
        requirement=requirement,
        base_prompt="BASE",
        kb_context="",
        full_content=json.dumps(cases, ensure_ascii=False),
        expected_count=expected_count,
        append=False,
        existing_cases=[],
        existing_unique_count=0,
        start_id=1,
        db=None,
        clean_and_parse_json_fn=clean_and_parse_json,
        normalize_json_structure_fn=normalize_json_structure_fn,
        deduplicate_test_cases_fn=deduplicate_test_cases,
        reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
        count_unique_test_cases_fn=count_unique_test_cases,
        infer_case_kind_fn=infer_case_kind,
        build_supplement_closed_loop_instruction_fn=lambda **_: "",
        multi_pass=True,
        generation_mode="multi_pass",
        feedback_control_state=feedback_control_state,
    )
    result = drain_generator_with_return(gen)
    assert isinstance(result, dict)
    return result
