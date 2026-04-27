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


def _drain_with_return(gen):
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value


class _EchoReviewClient:
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


def _run_cases(requirement: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    gen = stream_postprocess_cases(
        client=_EchoReviewClient(),
        requirement=requirement,
        base_prompt="BASE",
        kb_context="",
        full_content=json.dumps(cases, ensure_ascii=False),
        expected_count=30,
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
        multi_pass=True,
        generation_mode="multi_pass",
    )
    result = _drain_with_return(gen)
    assert isinstance(result, dict)
    return result


def test_quality_governance_deduplicates_and_normalizes_steps_preconditions() -> None:
    result = _run_cases(
        requirement="登录与支付基础流程校验",
        cases=[
            {
                "id": "TC-001",
                "description": "周末流程完成后返回首页并标记完成",
                "test_module": "学习流程",
                "preconditions": [],
                "steps": ["1. 打开周末任务", "2. 完成任务", "3. 返回首页并标记完成"],
                "test_input": "正常数据",
                "expected_result": "任务完成并标记成功",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "周末流程完成后返回首页并标记完成",
                "test_module": "学习流程",
                "preconditions": [],
                "steps": ["step1 打开周末任务", "step2 完成任务", "step3 返回首页并标记完成"],
                "test_input": "正常数据",
                "expected_result": "任务完成并标记成功",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "支付拦截提示展示",
                "test_module": "支付模块",
                "preconditions": [],
                "steps": ["3) 点击购买按钮"],
                "test_input": "未订阅用户",
                "expected_result": "显示付费拦截",
                "priority": "P1",
            },
        ],
    )
    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert len(output_cases) >= 1
    assert len(output_cases) < 3
    for case in output_cases:
        preconditions = case.get("preconditions")
        assert isinstance(preconditions, list) and len(preconditions) > 0
        steps = [str(x) for x in (case.get("steps") or [])]
        assert len(steps) > 0
        for idx, step in enumerate(steps, start=1):
            assert step.startswith(f"{idx}. ")


def test_quality_governance_uncertain_requirement_downgrades_case_priority() -> None:
    result = _run_cases(
        requirement="能力模型评分需教研确认，本期可以不做",
        cases=[
            {
                "id": "TC-010",
                "description": "能力模型评分结果展示",
                "test_module": "能力模型评分",
                "preconditions": ["已完成学习任务"],
                "steps": ["1. 进入能力模型页", "2. 查看评分结果"],
                "test_input": "标准学习数据",
                "expected_result": "展示能力模型评分结果",
                "priority": "P0",
            }
        ],
    )
    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert len(output_cases) == 1
    case = output_cases[0]
    assert str(case.get("priority") or "").upper() == "P2"
    assert "可选/视配置" in str(case.get("expected_result") or "")


def test_quality_governance_fails_when_required_p0_coverage_missing() -> None:
    result = _run_cases(
        requirement="主流程必须覆盖周中->周末->学习报告->完成闭环",
        cases=[
            {
                "id": "TC-020",
                "description": "仅校验通用设置页展示",
                "test_module": "设置页",
                "preconditions": ["已登录"],
                "steps": ["1. 打开设置页", "2. 查看基础信息"],
                "test_input": "默认配置",
                "expected_result": "展示设置页内容",
                "priority": "P1",
            }
        ],
    )
    assert result.get("cases") == []
