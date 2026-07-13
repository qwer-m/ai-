from __future__ import annotations

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


class _RescueClient:
    """Simulate rescue fallback when the first streaming output is empty."""

    def __init__(self) -> None:
        self.rescue_calls = 0
        self.stream_calls = 0

    def generate_response(
        self,
        requirement: str,
        prompt: str,
        db: Any = None,
        **kwargs,
    ) -> str:
        self.rescue_calls += 1
        return """
        [
          {
            "id": "TC-001",
            "description": "rescue-case",
            "test_module": "account-module",
            "preconditions": [],
            "steps": ["submit account id"],
            "test_input": "valid-account",
            "expected_result": "success",
            "priority": "P1"
          }
        ]
        """

    def generate_response_stream(self, requirement: str, prompt: str, **kwargs):
        self.stream_calls += 1
        yield "[]"


def _run_generator_and_capture_return(gen):
    chunks: list[str] = []
    while True:
        try:
            chunks.append(next(gen))
        except StopIteration as stop:
            return chunks, stop.value


def test_stream_postprocess_empty_result_can_be_rescued():
    client = _RescueClient()
    generator = stream_postprocess_cases(
        client=client,
        requirement="login feature",
        base_prompt="generate test cases",
        kb_context="project context",
        full_content="[]",
        expected_count=1,
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
        build_supplement_closed_loop_instruction_fn=lambda **kwargs: "",
    )
    chunks, result = _run_generator_and_capture_return(generator)

    assert client.rescue_calls >= 1
    assert isinstance(result, dict)
    assert isinstance(result.get("cases"), list)
    assert len(result["cases"]) == 1
    assert result["cases"][0].get("description")
    assert any("@@STATUS@@" in chunk for chunk in chunks)
