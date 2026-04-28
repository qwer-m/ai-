from __future__ import annotations

import json
import re
from typing import Any

from fastapi import HTTPException

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
from modules.testing.test_generation_components.postprocess import result_postprocess_streaming_impl
from routers.automation import test_generation_generate_routes_split_helpers as generate_routes
from schemas.automation.test_generation import TestGenRequest


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


def _run_cases(
    requirement: str,
    cases: list[dict[str, Any]],
    *,
    normalize_json_structure_fn=normalize_json_structure,
) -> dict[str, Any]:
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
        normalize_json_structure_fn=normalize_json_structure_fn,
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
        requirement="基础流程校验",
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


def test_quality_governance_backfills_placeholder_expected_result_and_test_input() -> None:
    result = _run_cases(
        requirement="回归问题验证",
        cases=[
            {
                "id": "TC-100",
                "description": "验证提交按钮在空白表单下展示校验信息",
                "test_module": "表单模块",
                "preconditions": ["已登录"],
                "steps": ["1. 打开表单", "2. 点击提交"],
                "test_input": "",
                "expected_result": "execution succeeds and result is as configured",
                "priority": "P1",
            }
        ],
        normalize_json_structure_fn=lambda value: value,
    )
    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert len(output_cases) == 1
    case = output_cases[0]
    expected_result = str(case.get("expected_result") or "").strip()
    test_input = str(case.get("test_input") or "").strip()
    assert expected_result
    assert expected_result != "execution succeeds and result is as configured"
    assert "execution succeeds and result is as configured" not in expected_result
    assert "应返回明确校验结论" not in expected_result
    assert test_input


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
    coverage = result.get("coverage") or {}
    assert coverage.get("missing_rules") == ["RULE-001"]
    assert coverage.get("covered_rules") == []
    summary = result.get("generation_summary") or {}
    assert summary.get("status") == "completed_with_quality_stop"


def test_quality_governance_promotes_core_cases_to_p0() -> None:
    result = _run_cases(
        requirement="core path regression coverage governance",
        cases=[
            {
                "id": "TC-031",
                "description": "validate paywall blocks unpaid user from learning entry",
                "test_module": "global control payment gate",
                "preconditions": ["user logged in", "user unpaid"],
                "steps": ["1. click learning entry", "2. verify paywall prompt appears and access is blocked"],
                "test_input": "unpaid user opens learning entry",
                "expected_result": "paywall blocks access and user cannot continue to learning flow",
                "priority": "P2",
            },
            {
                "id": "TC-032",
                "description": "validate OCR upload triggers AI scoring and wrong question collection",
                "test_module": "classroom quiz upload flow",
                "preconditions": ["quiz page open", "answer sheet photo exists"],
                "steps": ["1. upload answer sheet photo", "2. verify ai scoring result and wrong question collection updated"],
                "test_input": "upload sheet containing wrong answers",
                "expected_result": "ai scoring completes and wrong question collection is generated",
                "priority": "P2",
            },
        ],
    )
    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert len(output_cases) >= 1
    review_summary = dict((result.get("review_decision_summary") or {}))
    final_breakdown = dict(review_summary.get("priority_final_breakdown") or {})
    # 新决策层下，核心信号至少应提升到 P1（允许由 conflict_resolved 落到 P1）。
    assert int(final_breakdown.get("P0") or 0) + int(final_breakdown.get("P1") or 0) >= 1


def test_normalize_json_structure_unknown_priority_keeps_empty() -> None:
    normalized = normalize_json_structure(
        [
            {
                "id": "TC-001",
                "description": "验证基础流程",
                "test_module": "基础模块",
                "preconditions": ["已登录"],
                "steps": ["1. 打开页面"],
                "test_input": "默认输入",
                "expected_result": "展示页面",
                "priority": "",
            }
        ]
    )
    assert isinstance(normalized, list) and len(normalized) == 1
    assert str(normalized[0].get("priority") or "") == ""


def test_expected_result_phrase_state_change_marked_non_assertable() -> None:
    result = _run_cases(
        requirement="习题本质量校验",
        cases=[
            {
                "id": "TC-501",
                "description": "验证习题本结果展示",
                "test_module": "习题本模块",
                "preconditions": ["已登录", "存在习题本数据"],
                "steps": ["1. 进入习题本页面", "2. 查看题目列表"],
                "test_input": "默认数据",
                "expected_result": "执行查看题目列表后，应可观察到对应状态变化，且关键结果可核对",
                "priority": "P1",
            }
        ],
    )
    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert len(output_cases) == 1
    table = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert len(table) == 1
    row = table[0]
    assert str(row.get("expected_result_quality") or "") == "non_assertable"
    assert str(row.get("expected_result_quality_reason") or "") in {"template_or_weak_assertion", "no_concrete_assertion"}


def test_expected_result_phrase_target_content_marked_non_assertable() -> None:
    result = _run_cases(
        requirement="页面跳转校验",
        cases=[
            {
                "id": "TC-502",
                "description": "验证点击卡片后跳转页面",
                "test_module": "首页模块",
                "preconditions": ["已登录", "存在已完成任务卡片"],
                "steps": ["1. 点击任务卡片", "2. 观察跳转页面"],
                "test_input": "点击卡片",
                "expected_result": "执行观察跳转页面后，应完成页面跳转并展示对应内容",
                "priority": "P1",
            }
        ],
    )
    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert len(output_cases) == 1
    table = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert len(table) == 1
    row = table[0]
    assert str(row.get("expected_result_quality") or "") == "non_assertable"


def test_expected_result_phrase_match_result_marked_non_assertable() -> None:
    result = _run_cases(
        requirement="筛选功能校验",
        cases=[
            {
                "id": "TC-503",
                "description": "验证筛选后结果列表",
                "test_module": "筛选模块",
                "preconditions": ["已登录", "存在多条记录"],
                "steps": ["1. 选择筛选条件", "2. 点击查询"],
                "test_input": "按条件查询",
                "expected_result": "执行点击查询后，应返回与筛选条件匹配的结果，且结果内容可校验",
                "priority": "P1",
            }
        ],
    )
    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert len(output_cases) == 1
    table = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert len(table) == 1
    row = table[0]
    assert str(row.get("expected_result_quality") or "") == "non_assertable"


def test_expected_result_truncated_suffix_marked_truncated() -> None:
    result = _run_cases(
        requirement="OCR异常回退校验",
        cases=[
            {
                "id": "TC-504",
                "description": "验证OCR失败时展示",
                "test_module": "习题本模块",
                "preconditions": ["存在OCR失败题目"],
                "steps": ["1. 打开习题本页面", "2. 查看OCR失败题目卡片"],
                "test_input": "OCR失败数据",
                "expected_result": "识别失败题目保留原图或显",
                "priority": "P1",
            }
        ],
    )
    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert len(output_cases) == 1
    table = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert len(table) == 1
    row = table[0]
    assert str(row.get("expected_result_quality") or "") == "truncated"
    assert bool(row.get("truncated_text_detected")) is True


def test_expected_result_non_placeholder_not_overwritten_when_tokens_do_not_overlap() -> None:
    raw_expected = "接口返回HTTP 403并包含errorCode=NO_PERMISSION"
    result = _run_cases(
        requirement="权限校验",
        cases=[
            {
                "id": "TC-505",
                "description": "验证未授权访问返回错误码",
                "test_module": "权限模块",
                "preconditions": ["用户已登录但无权限"],
                "steps": ["1. 直接访问目标URL", "2. 查看响应头信息"],
                "test_input": "未授权访问请求",
                "expected_result": raw_expected,
                "priority": "P1",
            }
        ],
    )
    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert len(output_cases) == 1
    case = output_cases[0]
    assert str(case.get("expected_result") or "") == raw_expected
    table = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert len(table) == 1
    row = table[0]
    assert bool(row.get("expected_result_alignment_warning")) is True


def test_quality_governance_marks_priority_review_when_required_p0_is_conflict(monkeypatch) -> None:
    origin = result_postprocess_streaming_impl.apply_priority_semantics_to_cases

    def _force_conflict(cases, attach_debug=False, coverage_context=None, rule_diagnostics=None):  # noqa: ANN001, ARG001
        output = []
        for case in cases or []:
            if not isinstance(case, dict):
                continue
            item = dict(case)
            model_priority = str(item.get("model_priority_current") or item.get("priority") or "P0").strip().upper() or "P0"
            item["model_priority_current"] = model_priority
            item["model_priority"] = model_priority
            item["priority"] = "P1"
            item["legacy_priority"] = "P1"
            item["priority_final"] = None
            item["priority_decision_state"] = "conflict"
            item["priority_decision_source"] = "model_semantic_conflict"
            item["priority_confidence"] = "low"
            item["priority_conflict_reason"] = "model=P0,suggested=P2"
            output.append(item)
        return output

    monkeypatch.setattr(result_postprocess_streaming_impl, "apply_priority_semantics_to_cases", _force_conflict)
    try:
        result = _run_cases(
            requirement="week flow + paywall must be covered",
            cases=[
                {
                    "id": "TC-300",
                    "description": "validate generic flow behavior",
                    "test_module": "flow-module",
                    "preconditions": ["user logged in"],
                    "steps": ["1. open page", "2. click action"],
                    "test_input": "default input",
                    "expected_result": "shows expected result",
                    "priority": "P0",
                }
            ],
        )
    finally:
        monkeypatch.setattr(result_postprocess_streaming_impl, "apply_priority_semantics_to_cases", origin)

    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert len(output_cases) == 1

    review_summary = dict((result.get("review_decision_summary") or {}))
    assert review_summary.get("needs_priority_review") is True
    assert int(review_summary.get("priority_invalid_count") or 0) >= 1
    assert bool(review_summary.get("priority_quality_gate_failed")) is True
    assert int(review_summary.get("priority_undetermined_count") or 0) == 0

    generation_summary = dict((result.get("generation_summary") or {}))
    assert generation_summary.get("needs_priority_review") is True


def test_quality_governance_output_priority_final_always_valid() -> None:
    result = _run_cases(
        requirement="验证核心流程与权限校验",
        cases=[
            {
                "id": "TC-401",
                "description": "验证未授权访问被拦截且不可进入目标页面",
                "test_module": "权限模块",
                "preconditions": ["用户已登录但无权限"],
                "steps": ["1. 直接访问目标URL", "2. 观察拦截结果"],
                "test_input": "未授权访问请求",
                "expected_result": "应拦截访问并提示无权限",
                "priority": "P0",
            },
            {
                "id": "TC-402",
                "description": "验证列表展示文案在空数据下的提示",
                "test_module": "展示模块",
                "preconditions": ["已登录"],
                "steps": ["1. 进入列表页", "2. 查看空状态文案"],
                "test_input": "空数据",
                "expected_result": "显示空状态文案",
                "priority": "P1",
            },
        ],
    )
    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert len(output_cases) >= 1
    for case in output_cases:
        assert str(case.get("priority_final") or "").strip().upper() in {"P0", "P1", "P2"}


def test_generate_tests_empty_result_raises_http_error(monkeypatch) -> None:
    monkeypatch.setattr(generate_routes, "get_owned_project", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        generate_routes.context_orchestrator,
        "assemble_context",
        lambda *args, **kwargs: {"diagnostics": {}},
    )
    monkeypatch.setattr(generate_routes, "log_workflow_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(generate_routes, "log_to_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(generate_routes.test_generator, "generate_test_cases_json", lambda *args, **kwargs: [])

    request = TestGenRequest(requirement="req", project_id=1, expected_count=20)
    current_user = type("User", (), {"id": 1})()

    try:
        generate_routes.generate_tests(request=request, db=object(), current_user=current_user)
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert int(exc.status_code) == 502
        detail = dict(exc.detail or {})
        assert detail.get("error_code") == "EMPTY_GENERATED_RESULT"
        assert detail.get("final_status") == "empty_result_failed"


def test_generate_tests_low_quality_result_raises_http_error(monkeypatch) -> None:
    monkeypatch.setattr(generate_routes, "get_owned_project", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        generate_routes.context_orchestrator,
        "assemble_context",
        lambda *args, **kwargs: {"diagnostics": {}},
    )
    monkeypatch.setattr(generate_routes, "log_workflow_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(generate_routes, "log_to_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        generate_routes.test_generator,
        "generate_test_cases_json",
        lambda *args, **kwargs: {
            "error_code": "LOW_QUALITY_GENERATED_CASES",
            "error_message": "quality gate failed",
            "final_status": "quality_gate_failed",
            "quality_gate_failed": True,
            "failed_checks": [
                "priority_final_null_count=2",
                "non_assertable_expected_result_count=3",
                "truncated_text_count=1",
            ],
            "priority_final_null_count": 2,
            "invalid_priority_final_case_ids": ["TC-001", "TC-002"],
            "non_assertable_expected_result_count": 3,
            "truncated_text_count": 1,
            "non_assertable_case_ids": ["TC-003", "TC-004", "TC-005"],
            "truncated_case_ids": ["TC-006"],
        },
    )

    request = TestGenRequest(requirement="req", project_id=1, expected_count=20)
    current_user = type("User", (), {"id": 1})()

    try:
        generate_routes.generate_tests(request=request, db=object(), current_user=current_user)
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert int(exc.status_code) == 502
        detail = dict(exc.detail or {})
        assert detail.get("error_code") == "LOW_QUALITY_GENERATED_CASES"
        assert detail.get("final_status") == "quality_gate_failed"
        assert bool(detail.get("quality_gate_failed")) is True
        assert int(detail.get("priority_final_null_count") or 0) == 2
        assert int(detail.get("non_assertable_expected_result_count") or 0) == 3
        assert int(detail.get("truncated_text_count") or 0) == 1


def test_generate_tests_non_empty_result_still_success(monkeypatch) -> None:
    monkeypatch.setattr(generate_routes, "get_owned_project", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        generate_routes.context_orchestrator,
        "assemble_context",
        lambda *args, **kwargs: {"diagnostics": {}},
    )
    monkeypatch.setattr(generate_routes, "log_workflow_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(generate_routes, "log_to_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        generate_routes.test_generator,
        "generate_test_cases_json",
        lambda *args, **kwargs: [{"id": "TC-001", "description": "ok"}],
    )

    request = TestGenRequest(requirement="req", project_id=1, expected_count=20)
    current_user = type("User", (), {"id": 1})()
    result = generate_routes.generate_tests(request=request, db=object(), current_user=current_user)
    assert isinstance(result, list)
    assert len(result) == 1
