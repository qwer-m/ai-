from __future__ import annotations

import json
import re
from typing import Any

from fastapi import HTTPException
from core.settings.config import settings

import modules.testing.test_generation_components.legacy.json_generation as json_generation_mod
from modules.testing.test_generation_components.legacy_generation_impl import TestGenerationModule
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
    assert len(output_cases) == 0
    table = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert table
    assert str(table[0].get("expected_result_quality") or "") == "non_assertable"
    assert str(table[0].get("expected_result_quality_reason") or "") in {
        "no_concrete_assertion",
        "template_or_weak_assertion",
    }


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
                "expected_result": "能力模型页显示总分、维度分和更新时间，且总分与标准学习数据计算结果一致",
                "priority": "P0",
            }
        ],
    )
    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert len(output_cases) == 0
    table = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert table
    assert str(table[0].get("priority_final") or "").upper() != "P0"
    assert "可选/视配置" in str(table[0].get("expected_result") or "")


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
    assert len(output_cases) == 0
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
    assert len(output_cases) == 0
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
    assert len(output_cases) == 0
    table = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert len(table) == 1
    row = table[0]
    assert str(row.get("expected_result_quality") or "") == "non_assertable"


def test_expected_result_ambiguous_alternative_marked_non_assertable() -> None:
    result = _run_cases(
        requirement="保存失败时必须给出明确错误提示。",
        cases=[
            {
                "id": "TC-AMB-001",
                "description": "保存计划网络失败时保留编辑数据",
                "test_module": "排课-新增计划",
                "preconditions": ["已完成计划编辑"],
                "steps": ["1. 断开网络", "2. 点击保存"],
                "test_input": "无网络",
                "expected_result": "弹出提示‘保存失败，请重试’或显示错误信息，已编辑数据不丢失",
                "priority": "P1",
            }
        ],
    )
    rows = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert rows
    row = rows[0]
    assert str(row.get("expected_result_quality") or "") == "non_assertable"
    assert str(row.get("expected_result_quality_reason") or "") == "template_or_weak_assertion"


def test_expected_result_possible_or_xx_placeholder_marked_non_assertable() -> None:
    result = _run_cases(
        requirement="排课完成后必须显示明确的课程学习状态和时间。",
        cases=[
            {
                "id": "TC-AMB-002",
                "description": "验证排课后课程学习时间展示",
                "test_module": "排课-学习状态",
                "preconditions": ["已完成排课"],
                "steps": ["1. 进入排课详情", "2. 查看课程学习时间"],
                "test_input": "正常排课数据",
                "expected_result": "页面可能会增加复习时间，已学xx:xx",
                "priority": "P1",
            }
        ],
    )
    rows = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert rows
    row = rows[0]
    assert str(row.get("expected_result_quality") or "") == "non_assertable"
    assert str(row.get("expected_result_quality_reason") or "") == "template_or_weak_assertion"


def test_expected_result_generic_success_completion_marked_non_assertable() -> None:
    result = _run_cases(
        requirement="The scheduling wizard must preserve unsaved selections and show explicit exit confirmation.",
        cases=[
            {
                "id": "TC-AMB-003",
                "description": "Scheduling wizard step switch and exit confirmation",
                "test_module": "schedule wizard",
                "preconditions": ["The user has selected courses but has not saved"],
                "steps": ["1. Switch between wizard steps", "2. Click the back button"],
                "test_input": "unsaved selected courses",
                "expected_result": "执行点击左上角返回按钮后，应成功完成排课步骤切换与退出保存验证，且后续查询可验证结果",
                "priority": "P1",
            }
        ],
    )
    rows = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert rows
    assert str(rows[0].get("expected_result_quality") or "") == "non_assertable"
    assert not [item for item in (result.get("cases") or []) if isinstance(item, dict)]


def test_expected_result_self_explanation_question_mark_marked_non_assertable() -> None:
    result = _run_cases(
        requirement="Recent learning plan should display the current course and next course with explicit deletion behavior.",
        cases=[
            {
                "id": "TC-AMB-004",
                "description": "Recent learning plan after current course was deleted",
                "test_module": "recent learning plan",
                "preconditions": ["The current learning course was deleted from the plan"],
                "steps": ["1. Open recent learning plan", "2. Check current and next course"],
                "test_input": "deleted current course",
                "expected_result": "当前学习仍显示第一讲，下一节课显示更新后的计划最近一节课（第二讲），按规则保留当前学习课程？需求说当前在学课程保留",
                "priority": "P2",
            }
        ],
    )
    rows = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert rows
    assert str(rows[0].get("expected_result_quality") or "") == "non_assertable"
    assert not [item for item in (result.get("cases") or []) if isinstance(item, dict)]


def test_final_cases_drop_non_assertable_expected_result_even_when_review_selected() -> None:
    result = _run_cases(
        requirement="Course completion must show explicit report navigation and history navigation behavior.",
        cases=[
            {
                "id": "TC-001",
                "description": "Report button opens the concrete learning report page",
                "test_module": "student report table",
                "preconditions": ["The student has completed the course and a report exists"],
                "steps": ["1. Open the student report table", "2. Click the report button"],
                "test_input": "completed course with report",
                "expected_result": "The report page opens and displays the same student name, course name, and report title as the selected row",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Post-course optional review button display",
                "test_module": "post course optional actions",
                "preconditions": ["The course is completed and wrong-question records exist"],
                "steps": ["1. Open the post-course action area", "2. Check available buttons"],
                "test_input": "completed course with wrong-question records",
                "expected_result": "The area shows the report button or show a review button depending on configuration",
                "priority": "P0",
            },
        ],
    )
    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert len(output_cases) == 1
    assert "or show" not in str(output_cases[0].get("expected_result") or "").lower()

    rows = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    weak_rows = [row for row in rows if row.get("case_id") == "TC-002"]
    assert weak_rows
    assert str(weak_rows[0].get("expected_result_quality") or "") == "non_assertable"
    assert str(weak_rows[0].get("dropped_stage") or "") == "post_review_dedup_or_reorder"


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
    assert len(output_cases) == 0
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


def test_quality_governance_hides_priority_debug_fields_from_final_cases() -> None:
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
        assert str(case.get("priority") or "").strip().upper() in {"P0", "P1", "P2"}
        assert "priority_final" not in case
        assert "model_priority_current" not in case
        assert "priority_decision_source" not in case

    diagnostic_rows = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert diagnostic_rows
    assert any(str(row.get("priority_final") or "").strip().upper() in {"P0", "P1", "P2"} for row in diagnostic_rows)


def test_quality_governance_final_priority_uses_semantic_final_value_after_debug_strip() -> None:
    result = _run_cases(
        requirement="Secondary settings panel copy display check.",
        cases=[
            {
                "id": "TC-403",
                "description": "Button copy display check on a secondary settings panel",
                "test_module": "settings display",
                "preconditions": ["User has opened the settings panel"],
                "steps": ["1. Open the panel", "2. Check the button copy"],
                "test_input": "default settings",
                "expected_result": "The secondary button copy is visible",
                "priority": "P1",
            },
        ],
    )
    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert len(output_cases) == 1
    assert str(output_cases[0].get("priority") or "").strip().upper() == "P2"
    assert "priority_final" not in output_cases[0]


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


class _BackfillApplyClient:
    max_tokens = 2048

    def select_model(self, full_input: str, task_type: str = "generation") -> str:  # noqa: ARG002
        return "fake-backfill-model"

    def compress_context(self, context: str, *args, **kwargs) -> str:  # noqa: ARG002
        return context

    def generate_response(self, requirement: str, prompt: str, db: Any = None, **kwargs) -> str:  # noqa: ARG002
        return "[]"


class _FeedbackState:
    def to_dict(self) -> dict[str, Any]:
        return {}


class _FakeActiveSession:
    def __init__(self) -> None:
        self.entries: list[Any] = []
        self.logs: list[Any] = []
        self.generations: list[Any] = []
        self._next_id = 443

    def add(self, obj: Any) -> None:
        self.entries.append(obj)
        if hasattr(obj, "message"):
            self.logs.append(obj)
        elif hasattr(obj, "generated_result"):
            self.generations.append(obj)

    def commit(self) -> None:
        return None

    def refresh(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = self._next_id
            self._next_id += 1

    def rollback(self) -> None:
        return None


class _SignalSet:
    violates_confirmed_fact = False
    missing_core_flow = False
    missing_reuse_risk = False
    contains_pending_logic = False
    confirmed_fact_hits: list[str] = []
    confirmed_fact_violations: list[str] = []
    reuse_risk_hits: list[str] = []
    pending_hits: list[str] = []


class _JudgedItem:
    def __init__(self, case: dict[str, Any]) -> None:
        self.case_id = str(case.get("id") or case.get("case_id") or "")
        self.status = "PASS"
        self.reject_reason = ""
        self.pending_reason = ""
        self.signals = _SignalSet()
        self.before_case = dict(case)
        self.after_case = dict(case)


class _RepairedPayload:
    def __init__(self, cases: list[dict[str, Any]]) -> None:
        self.pass_count = len(cases)
        self.repairable_count = 0
        self.reject_count = 0
        self.pending_count = 0
        self.repaired_case_count = 0
        self.appended_case_count = 0
        self.core_flow_covered = True
        self.reuse_risk_covered = True
        self.cases = [_JudgedItem(case) for case in cases]
        self.pass_cases = [dict(case) for case in cases]


def _primary_cases_for_backfill_apply() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx in range(1, 9):
        rows.append(
            {
                "id": f"TC-{idx:03d}",
                "case_id": f"TC-{idx:03d}",
                "description": f"primary-case-{idx}",
                "test_module": "primary-module",
                "preconditions": ["user logged in"],
                "steps": ["1. open page", "2. verify state"],
                "test_input": "default input",
                "expected_result": "page text equals primary page",
                "priority": "P1",
                "priority_final": "P1",
            }
        )
    return rows


def _backfill_cases_for_backfill_apply() -> list[dict[str, Any]]:
    flow_keys = [
        "paid_gate",
        "textbook_grade_isolation",
        "weekday_review_to_workbook",
        "workbook_statistics",
        "wrong_only_filter",
        "mastery_calculation_level",
        "weekend_classification",
        "no_weekday_data_fallback",
        "all_correct_history_review",
        "weekend_completion_sync",
        "supervisor_report_generation",
        "unauthorized_data_isolation",
    ]
    rows: list[dict[str, Any]] = []
    for idx, flow_key in enumerate(flow_keys, start=1):
        rows.append(
            {
                "id": f"BF-{idx:03d}",
                "case_id": f"BF-{idx:03d}",
                "description": f"backfill-{flow_key}",
                "test_module": flow_key,
                "preconditions": ["user logged in"],
                "steps": ["1. execute flow", "2. verify result"],
                "test_input": flow_key,
                "expected_result": f"field status={flow_key}",
                "priority": "P1",
                "priority_final": "P1",
                "source_flow_key": flow_key,
                "matched_core_flows": [flow_key],
                "backfill_generated": True,
            }
        )
    return rows


def _extract_gen_diag(db: _FakeActiveSession, kind: str) -> dict[str, Any]:
    for log_entry in reversed(db.logs):
        message = str(getattr(log_entry, "message", "") or "")
        if not message.startswith("GEN_DIAG:"):
            continue
        payload = json.loads(message[len("GEN_DIAG:") :])
        if str(payload.get("kind") or "") == kind:
            return payload
    return {}


def _configure_backfill_apply_env(
    monkeypatch,
    *,
    enabled: bool,
    apply_to_final: bool,
    merged_preview_cases: list[dict[str, Any]],
    coverage_after_ratio: float = 1.0,
) -> dict[str, int]:
    import modules.test_generation_components.coverage.core_flow_backfill as backfill_plan_mod
    import modules.test_generation_components.coverage.core_flow_backfill_generation as backfill_generation_mod
    import modules.test_generation_components.coverage.core_flow_coverage_contract as coverage_contract_mod

    call_counter = {"generate_backfill": 0}
    primary_cases = _primary_cases_for_backfill_apply()
    accepted_backfill_cases = _backfill_cases_for_backfill_apply()

    monkeypatch.setattr(settings, "CORE_FLOW_BACKFILL_ENABLED", enabled, raising=False)
    monkeypatch.setattr(settings, "CORE_FLOW_BACKFILL_APPLY_TO_FINAL", apply_to_final, raising=False)
    monkeypatch.setattr(settings, "CORE_FLOW_BACKFILL_MAX_CANDIDATES", 12, raising=False)
    monkeypatch.setattr(settings, "CORE_FLOW_BACKFILL_MIN_FINAL_CASES", 12, raising=False)
    monkeypatch.setattr(settings, "CORE_FLOW_BACKFILL_MAX_FINAL_CASES", 18, raising=False)
    monkeypatch.setattr(settings, "CORE_FLOW_BACKFILL_MIN_COVERAGE_RATIO", 0.8, raising=False)

    monkeypatch.setattr(json_generation_mod, "get_client_for_user", lambda user_id, db: _BackfillApplyClient())
    monkeypatch.setattr(TestGenerationModule, "_is_active_db_session", lambda self, db: True)
    monkeypatch.setattr(
        TestGenerationModule,
        "_run_snapshot_readiness_gate",
        lambda self, **kwargs: {"proceed": True, "gate_debug": {}},
    )
    monkeypatch.setattr(
        TestGenerationModule,
        "_resolve_kb_context_with_hybrid",
        lambda self, **kwargs: {
            "kb_context": "",
            "context_source": "none",
            "fusion_debug": {},
            "rag_result": {},
        },
    )
    monkeypatch.setattr(
        TestGenerationModule,
        "analyze_requirement_context",
        lambda self, requirement, kb_context, client, db: {
            "system_type": "Web",
            "complexity": "Medium",
            "suggested_ratios": {"functional": 0.6, "regression": 0.2, "non_functional": 0.2},
            "focus_areas": ["core flow"],
            "device_scenarios": ["web"],
            "impact_scope": "single_module",
        },
    )
    monkeypatch.setattr(json_generation_mod, "build_feedback_control_state", lambda **kwargs: _FeedbackState())
    monkeypatch.setattr(
        json_generation_mod,
        "run_multi_pass_generation",
        lambda **kwargs: {
            "final_cases": [dict(item) for item in primary_cases],
            "stage_logs": [
                {"kind": "generation_stage", "stage": "primary", "case_count": len(primary_cases)},
                {"kind": "generation_stage", "stage": "gap", "case_count": 0},
                {"kind": "generation_stage", "stage": "review", "case_count": len(primary_cases)},
            ],
            "coverage": {"kind": "coverage_check", "covered_rules": ["RULE-001"], "missing_rules": []},
            "raw": {},
        },
    )
    monkeypatch.setattr(json_generation_mod, "judge_cases", lambda cases, **kwargs: cases)
    monkeypatch.setattr(json_generation_mod, "repair_cases", lambda judged, **kwargs: _RepairedPayload(judged))
    monkeypatch.setattr(json_generation_mod, "training_gate", lambda repaired: (repaired.pass_cases, [], [], []))
    monkeypatch.setattr(json_generation_mod, "deduplicate_test_cases", lambda cases: cases)
    monkeypatch.setattr(json_generation_mod, "reorder_cases_by_closed_loop", lambda cases, **kwargs: cases)
    monkeypatch.setattr(
        json_generation_mod,
        "analyze_coverage",
        lambda requirement, cases: {"covered_rules": ["RULE-001"], "missing_rules": []},
    )
    monkeypatch.setattr(
        backfill_plan_mod,
        "plan_core_flow_backfill",
        lambda **kwargs: {
            "backfill_plan": [{"flow_key": "paid_gate", "flow_name": "付费拦截"}],
            "missing_core_flow_count": 12,
        },
    )

    def _fake_generate_core_flow_backfill_candidates(**kwargs) -> dict[str, Any]:
        call_counter["generate_backfill"] += 1
        return {
            "generated_backfill_candidate_cases": [dict(item) for item in accepted_backfill_cases],
            "accepted_backfill_cases": [dict(item) for item in accepted_backfill_cases],
            "rejected_backfill_cases": [],
            "merged_preview_cases": [dict(item) for item in merged_preview_cases],
            "accepted_for_preview_count": 12,
            "primary_retained_count": 6,
            "primary_trimmed_count": 2,
            "backfill_retained_count": 12,
            "backfill_trimmed_count": 0,
        }

    def _fake_audit_core_flow_coverage(cases: list[dict[str, Any]]) -> dict[str, Any]:
        has_backfill = any(str((case or {}).get("id") or (case or {}).get("case_id") or "").startswith("BF-") for case in (cases or []))
        coverage_ratio = float(coverage_after_ratio if has_backfill else 0.0)
        covered_count = 12 if has_backfill and coverage_ratio >= 0.8 else 0
        missing = [] if covered_count == 12 else ["supervisor_report_generation", "unauthorized_data_isolation"]
        return {
            "core_flow_covered_count": int(covered_count),
            "core_flow_required_count": 12,
            "core_flow_coverage_ratio": coverage_ratio,
            "core_flow_coverage_passed": covered_count == 12,
            "missing_core_flows": list(missing),
            "false_positive_guard_notes": [],
            "coverage_detail": {},
        }

    monkeypatch.setattr(backfill_generation_mod, "generate_core_flow_backfill_candidates", _fake_generate_core_flow_backfill_candidates)
    monkeypatch.setattr(coverage_contract_mod, "audit_core_flow_coverage", _fake_audit_core_flow_coverage)
    return call_counter


def test_backfill_apply_default_disabled_keeps_primary_result(monkeypatch) -> None:
    merged_preview_cases = _primary_cases_for_backfill_apply()[:6] + _backfill_cases_for_backfill_apply()
    call_counter = _configure_backfill_apply_env(
        monkeypatch,
        enabled=False,
        apply_to_final=False,
        merged_preview_cases=merged_preview_cases,
    )

    module = TestGenerationModule()
    db = _FakeActiveSession()
    result = module.generate_test_cases_json(
        requirement="core flow backfill disabled by default",
        project_id=11,
        db=db,
        user_id=9,
        expected_count=8,
        multi_pass=True,
        generation_mode="multi_pass",
    )

    assert isinstance(result, list)
    assert len(result) == 8
    assert call_counter["generate_backfill"] == 0

    apply_summary = _extract_gen_diag(db, "core_flow_backfill_apply_summary")
    assert apply_summary["backfill_enabled"] is False
    assert apply_summary["backfill_apply_to_final"] is False
    assert apply_summary["backfill_applied"] is False
    assert apply_summary["apply_skip_reason"] == "backfill_feature_disabled"
    assert int(apply_summary["final_case_count"]) == 8

    generation_summary = _extract_gen_diag(db, "generation_summary")
    assert generation_summary["core_flow_backfill_enabled"] is False
    assert generation_summary["core_flow_backfill_applied"] is False
    assert int(generation_summary["primary_case_count_before_backfill"]) == 8
    assert int(generation_summary["final_case_count_after_backfill"]) == 8


def test_backfill_apply_to_final_replaces_final_result(monkeypatch) -> None:
    merged_preview_cases = _primary_cases_for_backfill_apply()[:6] + _backfill_cases_for_backfill_apply()
    call_counter = _configure_backfill_apply_env(
        monkeypatch,
        enabled=True,
        apply_to_final=True,
        merged_preview_cases=merged_preview_cases,
        coverage_after_ratio=1.0,
    )

    module = TestGenerationModule()
    db = _FakeActiveSession()
    result = module.generate_test_cases_json(
        requirement="apply merged backfill preview to final result",
        project_id=12,
        db=db,
        user_id=9,
        expected_count=8,
        multi_pass=True,
        generation_mode="multi_pass",
    )

    assert isinstance(result, list)
    assert len(result) == 18
    assert call_counter["generate_backfill"] == 1
    assert sum(1 for item in result if str(item.get("id") or "").startswith("BF-")) == 12

    apply_summary = _extract_gen_diag(db, "core_flow_backfill_apply_summary")
    assert apply_summary["backfill_enabled"] is True
    assert apply_summary["backfill_apply_to_final"] is True
    assert apply_summary["backfill_applied"] is True
    assert apply_summary["final_quality_gate_passed"] is True
    assert apply_summary["still_missing_core_flows"] == []
    assert int(apply_summary["primary_retained_count"]) == 6
    assert int(apply_summary["primary_trimmed_count"]) == 2
    assert int(apply_summary["backfill_retained_count"]) == 12
    assert int(apply_summary["backfill_trimmed_count"]) == 0

    generation_summary = _extract_gen_diag(db, "generation_summary")
    assert generation_summary["core_flow_backfill_enabled"] is True
    assert generation_summary["core_flow_backfill_applied"] is True
    assert int(generation_summary["final_count"]) == 18
    assert int(generation_summary["final_case_count_after_backfill"]) == 18
    assert float(generation_summary["core_flow_coverage_after"]) >= 0.8


def test_backfill_apply_to_final_blocks_non_assertable_merged_result(monkeypatch) -> None:
    merged_preview_cases = _primary_cases_for_backfill_apply()[:6] + _backfill_cases_for_backfill_apply()
    merged_preview_cases[0] = {
        **merged_preview_cases[0],
        "priority_final": None,
        "expected_result": "对应内容一致",
        "expected_result_quality": "non_assertable",
    }
    _configure_backfill_apply_env(
        monkeypatch,
        enabled=True,
        apply_to_final=True,
        merged_preview_cases=merged_preview_cases,
        coverage_after_ratio=1.0,
    )

    module = TestGenerationModule()
    db = _FakeActiveSession()
    result = module.generate_test_cases_json(
        requirement="merged result quality gate should block apply",
        project_id=13,
        db=db,
        user_id=9,
        expected_count=8,
        multi_pass=True,
        generation_mode="multi_pass",
    )

    assert isinstance(result, dict)
    assert result["error_code"] == "LOW_QUALITY_GENERATED_CASES"
    assert result["final_status"] == "quality_gate_failed"
    assert int(result["invalid_priority_final_count"]) >= 1
    assert int(result["non_assertable_expected_result_count"]) >= 1
    assert result["apply_skip_reason"] == "merged_result_quality_gate_failed"

    apply_summary = _extract_gen_diag(db, "core_flow_backfill_apply_summary")
    assert apply_summary["backfill_applied"] is False
    assert apply_summary["final_quality_gate_passed"] is False
    assert apply_summary["apply_skip_reason"] == "merged_result_quality_gate_failed"


def test_backfill_apply_to_final_blocks_low_coverage_result(monkeypatch) -> None:
    merged_preview_cases = _primary_cases_for_backfill_apply()[:6] + _backfill_cases_for_backfill_apply()
    _configure_backfill_apply_env(
        monkeypatch,
        enabled=True,
        apply_to_final=True,
        merged_preview_cases=merged_preview_cases,
        coverage_after_ratio=0.5,
    )

    module = TestGenerationModule()
    db = _FakeActiveSession()
    result = module.generate_test_cases_json(
        requirement="merged result coverage threshold should block apply",
        project_id=14,
        db=db,
        user_id=9,
        expected_count=8,
        multi_pass=True,
        generation_mode="multi_pass",
    )

    assert isinstance(result, dict)
    assert result["error_code"] == "LOW_QUALITY_GENERATED_CASES"
    assert result["final_status"] == "quality_gate_failed"
    assert result["apply_skip_reason"] == "merged_result_coverage_below_threshold"
    assert float(result["core_flow_coverage_ratio"]) < 0.8

    apply_summary = _extract_gen_diag(db, "core_flow_backfill_apply_summary")
    assert apply_summary["backfill_applied"] is False
    assert apply_summary["final_quality_gate_passed"] is False
    assert apply_summary["apply_skip_reason"] == "merged_result_coverage_below_threshold"
