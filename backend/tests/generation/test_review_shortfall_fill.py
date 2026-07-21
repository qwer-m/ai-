import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.test_generation_components.legacy.multi_pass_pipeline import run_multi_pass_generation
from modules.test_generation_components.postprocess.result_postprocess import stream_postprocess_cases
from modules.test_generation_components.legacy.adapters import (
    clean_and_parse_json,
    count_unique_test_cases,
    deduplicate_test_cases,
    infer_case_kind,
    normalize_json_structure,
    reorder_cases_by_closed_loop,
)


class _ShortReviewClient:
    def generate_response(self, requirement: str, prompt: str, db: Any = None, **kwargs) -> str:
        if "MULTI-PASS STAGE: PRIMARY" in prompt:
            return """
            [
              {"id":"TC-001","description":"primary-1","test_module":"module-a","preconditions":[],"steps":["s1"],"test_input":"i1","expected_result":"ok1","priority":"P0"},
              {"id":"TC-002","description":"primary-2","test_module":"module-a","preconditions":[],"steps":["s2"],"test_input":"i2","expected_result":"ok2","priority":"P1"},
              {"id":"TC-003","description":"primary-3","test_module":"module-a","preconditions":[],"steps":["s3"],"test_input":"i3","expected_result":"ok3","priority":"P2"}
            ]
            """
        if "GAP FILL" in prompt:
            return "[]"
        # REVIEW: intentionally return fewer than target_count
        return """
        [
          {"id":"TC-001","description":"primary-1","test_module":"module-a","preconditions":[],"steps":["s1"],"test_input":"i1","expected_result":"ok1","priority":"P0"}
        ]
        """

    def generate_response_stream(self, requirement: str, prompt: str, **kwargs):
        yield "[]"


def _drain_with_return(gen):
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value


def test_multi_pass_is_not_forced_to_fill_to_target_count() -> None:
    client = _ShortReviewClient()
    result = run_multi_pass_generation(
        client=client,
        requirement="",
        db=None,
        base_prompt="BASE",
        requirement_context="",
        current_biz_key="default",
        expected_count=2,
        start_id=1,
        clean_and_parse_json_fn=clean_and_parse_json,
        normalize_json_structure_fn=normalize_json_structure,
        deduplicate_test_cases_fn=deduplicate_test_cases,
        reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
        multi_pass=True,
        generation_mode="multi_pass",
    )

    final_cases = result.get("final_cases") or []
    assert len(final_cases) == 3
    descriptions = {item.get("description") for item in final_cases}
    assert "primary-1" in descriptions
    assert len(descriptions) == 3


def test_stream_postprocess_does_not_backfill_on_review_shortfall() -> None:
    client = _ShortReviewClient()
    full_content = """
    [
      {"id":"TC-001","description":"create schedule record","test_module":"module-a","preconditions":["schedule form is open"],"steps":["submit a new schedule"],"test_input":"schedule=A","expected_result":"API returns 201 and the list contains schedule A with active status","priority":"P0"},
      {"id":"TC-002","description":"update schedule owner","test_module":"module-a","preconditions":["schedule A exists"],"steps":["change the owner to teacher B"],"test_input":"owner=teacher B","expected_result":"the detail page owner field equals teacher B and the version increases by one","priority":"P1"},
      {"id":"TC-003","description":"delete schedule record","test_module":"module-a","preconditions":["schedule B exists"],"steps":["delete schedule B"],"test_input":"schedule=B","expected_result":"searching by schedule B returns zero records and the delete audit event exists","priority":"P2"}
    ]
    """

    # REVIEW response intentionally returns one case; postprocess should not backfill by count.
    original_generate_response = client.generate_response

    def review_override(requirement: str, prompt: str, db: Any = None, **kwargs) -> str:
        if "QA Auditor" in prompt:
            return """
            [
              {"id":"TC-001","description":"create schedule record","test_module":"module-a","preconditions":["schedule form is open"],"steps":["submit a new schedule"],"test_input":"schedule=A","expected_result":"API returns 201 and the list contains schedule A with active status","priority":"P0"}
            ]
            """
        return original_generate_response(requirement, prompt, db=db, **kwargs)

    client.generate_response = review_override  # type: ignore[assignment]

    gen = stream_postprocess_cases(
        client=client,
        requirement="",
        base_prompt="BASE",
        kb_context="",
        full_content=full_content,
        expected_count=2,
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
        generation_mode="multi_pass",
    )
    result = _drain_with_return(gen)

    final_cases = (result or {}).get("cases") or []
    # Review may return fewer rows than target, but postprocess should recover
    # from candidate pool instead of collapsing to a single retained case.
    assert len(final_cases) >= 2
    descriptions = {item.get("description") for item in final_cases}
    assert "create schedule record" in descriptions
    assert len(descriptions) >= 2
