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
    """中文注释：首轮空结果时，模拟非流式补救返回最小可用 JSON。"""

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
            "description": "补救用例",
            "test_module": "账户模块",
            "preconditions": [],
            "steps": ["输入账号并提交"],
            "test_input": "有效账号",
            "expected_result": "返回成功",
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
        requirement="登录功能",
        base_prompt="请生成测试用例",
        kb_context="项目背景",
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
    assert any("补救" in chunk for chunk in chunks)
