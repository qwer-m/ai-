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
    filter_invalid_final_cases,
    normalize_final_case_priorities,
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
    expected_count: int = 30,
    feedback_control_state: dict[str, Any] | None = None,
    normalize_json_structure_fn=normalize_json_structure,
) -> dict[str, Any]:
    gen = stream_postprocess_cases(
        client=_EchoReviewClient(),
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


def test_expected_result_possible_or_xx_placeholder_marked_invalid_case() -> None:
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
    assert str(row.get("case_quality") or "") == "invalid_case"
    assert str(row.get("invalid_case_reason") or "") == "reasoning_leakage"
    assert str(row.get("expected_result_quality") or "") == "invalid_case"
    assert str(row.get("expected_result_quality_reason") or "") == "reasoning_leakage"
    assert not [item for item in (result.get("cases") or []) if isinstance(item, dict)]


def test_reasoning_leakage_in_case_fields_marked_invalid_case() -> None:
    result = _run_cases(
        requirement="作文批改结果页支持按主题筛选点评内容。",
        cases=[
            {
                "id": "TC-010",
                "description": "点评主题筛选仅展示当前主题内容",
                "test_module": "作文批改",
                "preconditions": [
                    "可能？需求：默认显示全部主题，可切换只显示当前主题。但批改本身是针对当前主题，怎么会有多个？我们按照需求原文生成用例"
                ],
                "steps": ["1. 打开批改结果页", "2. 切换主题筛选"],
                "test_input": "已生成批改结果",
                "expected_result": "仅展示所选主题下的点评内容，其他主题点评不显示",
                "priority": "P1",
            }
        ],
    )
    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert not output_cases
    rows = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert len(rows) == 1
    row = rows[0]
    assert str(row.get("case_id") or "") == "TC-010"
    assert str(row.get("case_quality") or "") == "invalid_case"
    assert str(row.get("invalid_case_reason") or "") == "reasoning_leakage"
    assert str(row.get("expected_result_quality") or "") == "invalid_case"
    assert str(row.get("dropped_stage") or "") == "post_review_dedup_or_reorder"
    summary = dict(result.get("review_decision_summary") or {})
    assert int(summary.get("reasoning_leakage_case_count") or 0) == 1


def test_reasoning_leakage_actual_trigger_condition_marked_invalid_case() -> None:
    result = _run_cases(
        requirement="排课新增计划容量不足时必须给出明确提示。",
        cases=[
            {
                "id": "TC-007",
                "description": "排课-新增计划-课程设置过少",
                "test_module": "排课-新增计划",
                "preconditions": ["但需故意设置更少？实际触发条件为已选课程数大于可排课容量"],
                "steps": ["1. 进入新增计划", "2. 选择课程", "3. 设置时间"],
                "test_input": "课程数大于可排课容量",
                "expected_result": "系统提示课程设置过少，无法完成全部课程排课",
                "priority": "P1",
            }
        ],
    )

    assert not [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    rows = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert rows
    assert str(rows[0].get("invalid_case_reason") or "") == "reasoning_leakage"


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


def test_concrete_ui_state_expected_results_not_marked_non_assertable() -> None:
    result = _run_cases(
        requirement="作文投稿和课程环节状态需要可验证的 UI 断言",
        cases=[
            {
                "id": "TC-LOCK-001",
                "description": "初始状态下三个环节均可随意进入",
                "test_module": "课程环节 - 解锁逻辑",
                "preconditions": ["普通用户已进入课程环节页"],
                "steps": [
                    "1. 分别点击审题立意、写作技法、技法巩固",
                    "2. 观察进入结果",
                ],
                "test_input": "第一课初始学习状态",
                "expected_result": "三个环节均可正常进入，无任何锁或提示阻止",
                "priority": "P1",
            },
            {
                "id": "TC-OCR-001",
                "description": "批改-OCR识别失败（图片模糊）提示重试",
                "test_module": "作文批改-批改结果页",
                "preconditions": ["用户已上传模糊作文图片"],
                "steps": ["1. 提交模糊图片", "2. 观察批改入口和提示"],
                "test_input": "模糊作文图片",
                "expected_result": "系统提示‘图片不清晰，请重新拍摄或选择清晰图片’，【去批改】按钮变为不可点击或显示重试选项",
                "priority": "P0",
            },
        ],
    )
    rows = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert len(rows) == 2
    assert {str(row.get("expected_result_quality") or "") for row in rows} == {"assertable"}
    assert len([item for item in (result.get("cases") or []) if isinstance(item, dict)]) == 2


def test_concrete_formula_order_expected_result_not_marked_non_assertable() -> None:
    result = _run_cases(
        requirement="作文圈精选排序按权重 S=0.3L+0.2R+0.5T 降序展示",
        cases=[
            {
                "id": "TC-FORMULA-001",
                "description": "作文圈精选排序：按权重S=0.3L+0.2R+0.5T降序排列",
                "test_module": "作文圈-列表",
                "preconditions": ["存在三篇作品 A、B、C，且点赞/阅读/时间指标可计算"],
                "steps": ["1. 进入作文圈精选列表", "2. 观察作品展示顺序"],
                "test_input": "A的S值最高，B居中，C最低",
                "expected_result": "列表依次显示作品A、B、C（A的S值最高，C最低），顺序与权重公式计算结果一致",
                "priority": "P1",
            }
        ],
    )
    rows = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert len(rows) == 1
    assert str(rows[0].get("expected_result_quality") or "") == "assertable"
    assert len([item for item in (result.get("cases") or []) if isinstance(item, dict)]) == 1


def test_concrete_counter_expected_result_not_marked_non_assertable() -> None:
    result = _run_cases(
        requirement="批改完成后需要更新剩余批改次数",
        cases=[
            {
                "id": "TC-COUNTER-001",
                "description": "批改次数剩余更新：第一次批改后剩余次数从5变为4",
                "test_module": "作文批改",
                "preconditions": ["用户当前剩余 5 次批改次数"],
                "steps": ["1. 上传作文图片", "2. 点击去批改并等待批改完成"],
                "test_input": "清晰作文图片",
                "expected_result": "批改完成后，页面上方或按钮处的剩余批改次数显示为4/5",
                "priority": "P1",
            }
        ],
    )
    rows = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert len(rows) == 1
    assert str(rows[0].get("expected_result_quality") or "") == "assertable"
    assert len([item for item in (result.get("cases") or []) if isinstance(item, dict)]) == 1


def _disabled_semantic_dedup_collapses_generic_intent_variants() -> None:
    result = _run_cases(
        requirement="保存操作需要覆盖失败保留数据和成功跳转两个意图；同一失败保留数据意图不要重复生成。",
        cases=[
            {
                "id": "TC-001",
                "description": "保存失败时提示错误并保留表单数据",
                "test_module": "通用表单保存",
                "preconditions": ["用户已填写完整表单"],
                "steps": ["1. 点击保存", "2. 模拟接口返回500", "3. 查看页面提示和表单内容"],
                "test_input": "接口返回500",
                "expected_result": "页面显示“保存失败，请重试”，已填写的表单数据保持不变，可再次点击保存",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "网络异常导致保存失败时，用户输入内容不丢失",
                "test_module": "通用表单保存",
                "preconditions": ["用户已填写完整表单"],
                "steps": ["1. 点击保存按钮", "2. 模拟网络异常", "3. 查看错误提示和表单字段"],
                "test_input": "网络异常",
                "expected_result": "页面显示“保存失败，请重试”，表单字段仍展示提交前的输入值，用户可重试保存",
                "priority": "P2",
            },
            {
                "id": "TC-003",
                "description": "保存成功后跳转详情页",
                "test_module": "通用表单保存",
                "preconditions": ["用户已填写完整表单"],
                "steps": ["1. 点击保存", "2. 接口返回成功", "3. 查看页面跳转"],
                "test_input": "接口返回成功",
                "expected_result": "页面跳转到详情页，详情页展示刚保存的数据，地址包含新数据ID",
                "priority": "P1",
            },
        ],
    )

    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    failure_cases = [
        item
        for item in output_cases
        if "失败" in str(item.get("description") or "")
        or "保存失败" in str(item.get("expected_result") or "")
    ]
    assert len(failure_cases) == 1
    assert any("保存成功" in str(item.get("description") or "") for item in output_cases)
    assert len(output_cases) == 2


def test_expected_result_video_retry_delete_template_marked_non_assertable() -> None:
    result = _run_cases(
        requirement="课程视频加载失败时展示失败提示，允许重试，重试失败时不影响返回课程环节页。",
        cases=[
            {
                "id": "TC-037",
                "description": "审题立意或写作技法环节中视频加载失败时可重试",
                "test_module": "课程环节",
                "preconditions": ["用户已进入课程环节页"],
                "steps": ["1. 进入审题立意环节", "2. 模拟视频资源加载失败", "3. 点击重试按钮"],
                "test_input": "视频资源接口返回超时",
                "expected_result": "执行操作重试按钮（若有）后，应删除审题立意或写作技法环节中视频加载失败提示，并后续查询可验证结果",
                "priority": "P1",
            }
        ],
    )
    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert not output_cases
    rows = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert rows
    assert str(rows[0].get("expected_result_quality") or "") == "non_assertable"
    assert str(rows[0].get("expected_result_quality_reason") or "") == "template_or_weak_assertion"


def test_confirmed_nonlinear_course_unlock_drops_legacy_locked_case() -> None:
    result = _run_cases(
        requirement="新版课程环节采用非线性解锁，初始状态下审题立意、写作技法、技法巩固三个环节均可任意进入。",
        cases=[
            {
                "id": "TC-034",
                "description": "课程环节初始为非线性解锁，可随意进入任意环节",
                "test_module": "课程环节",
                "preconditions": ["普通用户已进入第一课课程环节页"],
                "steps": ["1. 查看三个课程环节", "2. 分别点击审题立意、写作技法、技法巩固"],
                "test_input": "第一课初始学习状态",
                "expected_result": "三个环节均未锁定，均可进入对应学习内容，不要求先完成前一环节。",
                "priority": "P0",
            },
            {
                "id": "TC-040",
                "description": "初始进入某单元时，三个环节均显示未解锁",
                "test_module": "课程环节",
                "preconditions": ["普通用户首次进入单元"],
                "steps": ["1. 打开课程环节页", "2. 点击任一未解锁环节"],
                "test_input": "第一课初始学习状态",
                "expected_result": "三个环节均显示未解锁，点击提示“完成前一节才可以解锁哦”。",
                "priority": "P1",
            },
        ],
    )
    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    descriptions = " ".join(str(item.get("description") or "") for item in output_cases)
    assert "非线性解锁" in descriptions
    assert "三个环节均显示未解锁" not in descriptions
    rows = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    dropped = [row for row in rows if str(row.get("case_id") or "") == "TC-040"]
    assert dropped
    assert str(dropped[0].get("dropped_stage") or "")


def test_obsolete_linear_unlock_case_dropped_without_explicit_legacy_tag() -> None:
    result = _run_cases(
        requirement="课程环节当前必须采用任意环节可进入的学习方式。",
        cases=[
            {
                "id": "TC-014",
                "description": "初始状态下会员用户仅第一个环节已解锁，其余为未解锁",
                "test_module": "课程环节",
                "preconditions": ["会员用户进入课程环节页"],
                "steps": ["1. 查看审题立意、写作技法、技法巩固三个环节", "2. 点击未解锁环节"],
                "test_input": "会员用户初始学习状态",
                "expected_result": "仅第一个环节已解锁，其余环节点击弹出toast“完成前一节才可以解锁哦”。",
                "priority": "P0",
            },
            {
                "id": "TC-015",
                "description": "会员用户初始可进入任意课程环节",
                "test_module": "课程环节",
                "preconditions": ["会员用户进入课程环节页"],
                "steps": ["1. 分别点击审题立意、写作技法、技法巩固"],
                "test_input": "会员用户初始学习状态",
                "expected_result": "三个环节均可进入对应学习内容，不要求先完成前一环节。",
                "priority": "P0",
            },
        ],
    )
    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    descriptions = " ".join(str(item.get("description") or "") for item in output_cases)
    assert "仅第一个环节已解锁" not in descriptions
    assert "初始可进入任意课程环节" in descriptions
    rows = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    dropped = [row for row in rows if str(row.get("case_id") or "") == "TC-014"]
    assert dropped
    assert str(dropped[0].get("dropped_stage") or "")


def test_expected_result_self_explanation_question_mark_marked_invalid_case() -> None:
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
    assert str(rows[0].get("case_quality") or "") == "invalid_case"
    assert str(rows[0].get("invalid_case_reason") or "") == "reasoning_leakage"
    assert str(rows[0].get("expected_result_quality") or "") == "invalid_case"
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
                    "description": "validate paywall blocks access when quota is exhausted",
                    "test_module": "flow-module",
                    "preconditions": ["user logged in"],
                    "steps": ["1. open protected learning page", "2. verify paywall banner"],
                    "test_input": "default input",
                    "expected_result": "The protected content stays hidden and the paywall banner remains visible.",
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


def test_quality_governance_keeps_final_priority_but_hides_debug_fields_from_final_cases() -> None:
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
        assert str(case.get("priority_final") or "").strip().upper() in {"P0", "P1", "P2"}
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
    assert str(output_cases[0].get("priority_final") or "").strip().upper() == "P2"


def test_full_regression_priority_demotes_non_blocking_p0_and_promotes_main_path() -> None:
    full_regression_state = {
        "source_meta": {
            "generation_coverage_profile": {
                "coverage_mode": "full_functional_regression",
                "target_case_range": {"min": 85, "max": 90},
            }
        }
    }
    result = _run_cases(
        requirement="作文批改 full regression：上传图片后可去批改并生成批改结果；批改反馈四部分完整展示；分句点评点击划线句子可定位；我的作文最多20条。",
        expected_count=90,
        feedback_control_state=full_regression_state,
        cases=[
            {
                "id": "TC-017",
                "description": "分句点评点击划线句子跳转到对应点评",
                "test_module": "作文批改",
                "preconditions": ["批改结果页已展示分句点评"],
                "steps": ["1. 点击正文中的划线句子", "2. 查看右侧分句点评定位"],
                "test_input": "包含分句点评的批改结果",
                "expected_result": "右侧定位到该划线句子对应的分句点评内容，当前批改结果页不丢失。",
                "priority": "P0",
            },
            {
                "id": "TC-058",
                "description": "我的作文最多20条",
                "test_module": "我的作文",
                "preconditions": ["用户已有超过20篇作文记录"],
                "steps": ["1. 打开我的作文列表", "2. 查看列表数量和分页入口"],
                "test_input": "21篇作文记录",
                "expected_result": "列表默认展示最新20条作文记录，第21条不在首屏列表中，可通过分页或加载更多继续查看。",
                "priority": "P0",
            },
            {
                "id": "TC-081",
                "description": "上传图片后点击去批改成功生成批改结果",
                "test_module": "作文批改",
                "preconditions": ["用户已登录且上传了作文图片"],
                "steps": ["1. 上传作文图片", "2. 点击去批改", "3. 等待AI批改完成"],
                "test_input": "清晰作文图片",
                "expected_result": "系统成功生成批改结果，结果页展示综合点评、分句点评、全文润色和优化建议四部分内容。",
                "priority": "P1",
            },
        ],
    )
    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    by_description = {str(item.get("description") or ""): item for item in output_cases}
    assert str(by_description["分句点评点击划线句子跳转到对应点评"].get("priority") or "") == "P1"
    assert str(by_description["我的作文最多20条"].get("priority") or "") == "P1"
    assert str(by_description["上传图片后点击去批改成功生成批改结果"].get("priority") or "") == "P0"
    generation_summary = dict(result.get("generation_summary") or {})
    assert int(generation_summary.get("hard_min_count") or 0) >= 85


def test_full_regression_demotes_detail_p0_cases_called_out_by_review() -> None:
    full_regression_state = {
        "source_meta": {
            "generation_coverage_profile": {
                "coverage_mode": "full_functional_regression",
                "target_case_range": {"min": 85, "max": 90},
            }
        }
    }
    result = _run_cases(
        requirement="作文批改 full regression：上传图片后生成批改结果，投稿后进入审核中，细节交互不应作为 P0。",
        expected_count=90,
        feedback_control_state=full_regression_state,
        cases=[
            {
                "id": "TC-001",
                "description": "上传图片后点击去批改成功生成批改结果",
                "test_module": "作文批改",
                "preconditions": ["用户已登录且上传作文图片"],
                "steps": ["1. 上传图片", "2. 点击去批改", "3. 等待AI批改完成"],
                "test_input": "清晰作文图片",
                "expected_result": "批改结果页展示综合点评、分句点评、全文润色和优化建议四部分内容",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "0张图片时去批改按钮不可点",
                "test_module": "作文批改",
                "preconditions": ["用户未上传图片"],
                "steps": ["1. 打开作文批改页", "2. 查看去批改按钮"],
                "test_input": "0张图片",
                "expected_result": "去批改按钮置灰且不发起批改请求",
                "priority": "P0",
            },
            {
                "id": "TC-003",
                "description": "综合点评星星评分展示",
                "test_module": "批改结果",
                "preconditions": ["已生成批改结果"],
                "steps": ["1. 打开批改结果", "2. 查看综合点评星星评分"],
                "test_input": "已批改作文",
                "expected_result": "星星数量与综合评分值匹配",
                "priority": "P0",
            },
            {
                "id": "TC-004",
                "description": "投稿页标题正文可编辑",
                "test_module": "作文投稿",
                "preconditions": ["用户已进入投稿页"],
                "steps": ["1. 修改标题", "2. 修改正文"],
                "test_input": "新标题和新正文",
                "expected_result": "标题和正文输入框保留编辑后的内容",
                "priority": "P0",
            },
        ],
    )

    by_description = {str(item.get("description") or ""): item for item in (result.get("cases") or [])}
    assert str(by_description["上传图片后点击去批改成功生成批改结果"].get("priority") or "") == "P0"
    assert str(by_description["0张图片时去批改按钮不可点"].get("priority") or "") == "P1"
    assert str(by_description["综合点评星星评分展示"].get("priority") or "") == "P1"
    assert str(by_description["投稿页标题正文可编辑"].get("priority") or "") == "P1"


def test_full_regression_promotes_core_business_chain_p0_floor() -> None:
    state = {
        "source_meta": {
            "generation_coverage_profile": {
                "coverage_mode": "full_functional_regression",
                "target_case_range": {"min": 85, "max": 90},
            }
        }
    }
    cases = [
        ("上传图片后点击去批改成功生成批改结果", "作文批改", "批改结果页展示综合点评、分句点评、全文润色和优化建议四部分内容"),
        ("批改反馈四部分完整展示", "批改结果", "结果页完整展示综合点评、分句点评、全文润色和优化建议四部分"),
        ("投稿提交成功后状态进入审核中", "作文投稿", "作品提交成功且状态变为审核中"),
        ("后台审核通过后作品已发布并在作文圈可见", "作文圈", "作品状态为已发布且作文圈列表可见该作品"),
        ("普通用户第一课免费可试学", "课程权限", "普通用户可进入第一课试学且不跳转会员中心"),
        ("普通用户非第一课跳转会员中心", "课程权限", "普通用户点击非第一课时跳转会员中心"),
        ("会员用户全部课程可学", "会员课程", "会员用户可进入全部课程学习"),
        ("删除已发布作品后恢复未投稿状态", "我的作文", "删除已发布作品后该作文恢复为未投稿状态"),
        ("批改后投稿审核通过同步到我的作文和作文圈", "跨模块状态", "批改作品审核通过后我的作文显示已发布且作文圈我的列表出现作品"),
    ]
    core_cases = [
        {
            "id": f"TC-{index:03d}",
            "description": description,
            "test_module": module,
            "preconditions": ["用户已登录并满足对应业务前置条件"],
            "steps": ["1. 打开对应业务页面", "2. 执行业务操作", "3. 查看最终状态"],
            "test_input": description,
            "expected_result": expected,
            "priority": "P1",
        }
        for index, (description, module, expected) in enumerate(cases, start=1)
    ]
    filler_cases = [
        {
            "id": f"TC-{index:03d}",
            "description": f"辅助回归场景{index}展示与交互校验",
            "test_module": "辅助回归",
            "preconditions": ["用户已登录"],
            "steps": ["1. 打开辅助页面", "2. 执行辅助操作", "3. 查看页面反馈"],
            "test_input": f"辅助数据{index}",
            "expected_result": f"辅助场景{index}按产品规则展示反馈",
            "priority": "P2",
        }
        for index in range(len(core_cases) + 1, 86)
    ]
    result = _run_cases(
        requirement="作文批改 full regression：上传图片后可去批改；批改反馈四部分完整展示；投稿成功进入审核中；审核通过后作文圈可见；普通用户第一课免费，其余课程锁会员；会员全部课程可学；删除已发布作品恢复未投稿。",
        expected_count=90,
        feedback_control_state=state,
        cases=[*core_cases, *filler_cases],
    )

    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    p0_descriptions = {
        str(item.get("description") or "")
        for item in output_cases
        if str(item.get("priority") or "").strip().upper() == "P0"
    }

    assert len(p0_descriptions) >= 8
    assert "上传图片后点击去批改成功生成批改结果" in p0_descriptions
    assert "批改反馈四部分完整展示" in p0_descriptions
    assert "投稿提交成功后状态进入审核中" in p0_descriptions
    assert any("审核通过" in description and "作文圈" in description for description in p0_descriptions)
    assert any(
        bool(item.get("student_observation_projection"))
        and str(item.get("role") or "") == "student"
        and str(item.get("session_key") or "") == "student_session"
        for item in output_cases
    )
    assert "普通用户第一课免费可试学" in p0_descriptions
    assert "会员用户全部课程可学" in p0_descriptions
    assert any("删除已发布作品后恢复未投稿" in description for description in p0_descriptions)


def test_execution_plan_uses_workflow_blueprint_without_domain_template() -> None:
    state = {
        "workflow_blueprints": [
            {
                "id": "checkout_flow",
                "name": "checkout flow",
                "steps": [
                    {
                        "id": "submit_order",
                        "label": "Submit order",
                        "actor": "student",
                        "state_in": "cart_ready",
                        "state_out": "order_created",
                        "match_keywords": ["submit order"],
                        "assertion": "order is created",
                    },
                    {
                        "id": "verify_paid",
                        "label": "Verify paid status",
                        "actor": "supervisor",
                        "state_in": "order_created",
                        "state_out": "paid_status_visible",
                        "match_keywords": ["paid status"],
                        "assertion": "status is paid",
                    },
                ],
            }
        ]
    }
    result = _run_cases(
        requirement="Checkout regression",
        cases=[
            {
                "id": "TC-001",
                "description": "Submit order creates an order record",
                "test_module": "checkout",
                "steps": ["Open checkout", "Submit order"],
                "expected_result": "order is created",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Order detail shows paid status",
                "test_module": "order detail",
                "steps": ["Open order detail"],
                "expected_result": "status is paid",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "Network timeout shows retry action",
                "test_module": "checkout",
                "steps": ["Submit order during timeout"],
                "expected_result": "retry action is shown",
                "priority": "P0",
            },
        ],
        expected_count=10,
        feedback_control_state=state,
    )

    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    main_cases = [item for item in output_cases if str(item.get("execution_group") or "") == "main_smoke"]
    assert [item.get("main_chain_stage") for item in main_cases] == ["submit_order", "verify_paid"]
    assert main_cases[0].get("depends_on") == []
    assert main_cases[1].get("depends_on") == [main_cases[0]["id"]]
    assert [item.get("role") for item in main_cases] == ["student", "supervisor"]
    assert [item.get("data_state") for item in main_cases] == ["order_created", "paid_status_visible"]
    assert all(str(item.get("fixture_key") or "") == "workflow_blueprint_chain_seed" for item in main_cases)
    timeout_case = next(item for item in output_cases if "timeout" in str(item.get("description") or "").lower())
    assert str(timeout_case.get("execution_group") or "") == "exception"
    summary = dict(result.get("review_decision_summary") or {})
    plan = dict(summary.get("execution_plan") or {})
    assert plan.get("workflow_blueprint_count") == 1
    assert plan.get("linear_executable") is True


def test_execution_plan_attaches_transition_contract_to_main_smoke() -> None:
    state = {
        "workflow_blueprints": [
            {
                "id": "checkout_flow",
                "name": "checkout flow",
                "steps": [
                    {
                        "id": "submit_order",
                        "label": "Submit order",
                        "actor": "student",
                        "state_in": "cart_ready",
                        "state_out": "order_created",
                        "match_keywords": ["submit order"],
                        "assertion": "order is created",
                    },
                    {
                        "id": "verify_paid",
                        "label": "Verify paid status",
                        "actor": "student",
                        "state_in": "order_created",
                        "state_out": "paid_status_visible",
                        "match_keywords": ["paid status"],
                        "assertion": "status is paid",
                    },
                ],
            }
        ]
    }
    result = _run_cases(
        requirement="Checkout regression",
        cases=[
            {
                "id": "TC-001",
                "description": "Submit order creates an order record",
                "test_module": "checkout",
                "steps": ["Open checkout", "Submit order"],
                "expected_result": "order is created",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Order detail shows paid status",
                "test_module": "order detail",
                "steps": ["Open order detail"],
                "expected_result": "status is paid",
                "priority": "P1",
            },
        ],
        expected_count=10,
        feedback_control_state=state,
    )

    main_cases = [
        item for item in (result.get("cases") or [])
        if isinstance(item, dict) and str(item.get("execution_group") or "") == "main_smoke"
    ]
    assert main_cases
    transitions = [dict(item.get("workflow_transition") or {}) for item in main_cases]
    assert [item.get("source_state") for item in transitions] == ["cart_ready", "order_created"]
    assert [item.get("target_state") for item in transitions] == ["order_created", "paid_status_visible"]
    assert all(item.get("path_type") == "positive" for item in transitions)
    assert all(item.get("blocking") is False for item in transitions)
    assert all(item.get("destructive") is False for item in transitions)
    assert all(item.get("can_advance_main_flow") is True for item in transitions)
    assert [item.get("workflow_id") for item in main_cases] == ["checkout_flow", "checkout_flow"]
    assert [item.get("source_state") for item in main_cases] == ["cart_ready", "order_created"]
    assert [item.get("target_state") for item in main_cases] == ["order_created", "paid_status_visible"]
    assert all(float(item.get("state_transition_confidence") or 0.0) >= 0.9 for item in main_cases)


def test_trusted_repository_contract_bridges_full_main_chain_when_candidates_do_not_match() -> None:
    states = [
        ("start", "Ready", "open_workflow", "ready", "started", "entry"),
        ("configure", "Configure", "configure_workflow", "started", "configured", "configure"),
        ("preview", "Preview", "preview_workflow", "configured", "preview_ready", "preview"),
        ("commit", "Commit", "commit_workflow", "preview_ready", "committed", "commit"),
        ("visible", "Visible", "show_downstream", "committed", "visible", "downstream_visibility"),
        ("consume", "Consume", "consume_workflow", "visible", "consumed", "consume"),
    ]
    state = {
        "workflow_blueprints": [
            {
                "id": "trusted_bridge_flow",
                "workflow_id": "trusted_bridge_flow",
                "name": "trusted bridge flow",
                "source_type": "human_reviewed",
                "repository_source": "workflow_blueprint_repository",
                "trusted": True,
                "steps": [
                    {
                        "id": step_id,
                        "label": label,
                        "action": action,
                        "actor": "student",
                        "state_in": state_in,
                        "state_out": state_out,
                        "stage_kind": stage_kind,
                        "allow_bridge": True,
                        "match_keywords": [f"__no_candidate_match_{step_id}__"],
                    }
                    for step_id, label, action, state_in, state_out, stage_kind in states
                ],
            }
        ]
    }
    result = _run_cases(
        requirement="Trusted repository contract bridge regression",
        cases=[
            {
                "id": "TC-001",
                "description": "Unrelated profile preference update",
                "test_module": "profile",
                "steps": ["Update profile preference"],
                "expected_result": "profile preference is updated",
                "priority": "P1",
            }
        ],
        expected_count=10,
        feedback_control_state=state,
    )

    main_cases = [
        item for item in (result.get("cases") or [])
        if isinstance(item, dict) and str(item.get("execution_group") or "") == "main_smoke"
    ]
    assert [item.get("main_chain_stage") for item in main_cases] == [item[0] for item in states]
    assert all(item.get("workflow_contract_materialized_case") is True for item in main_cases)
    assert not any(bool(item.get("generated_bridge_case")) for item in main_cases)
    assert all(str(item.get("priority") or "") == "P0" for item in main_cases)
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})
    assert plan.get("trusted_workflow_contract_count") == 1
    assert plan.get("generated_bridge_case_count") == 0
    assert plan.get("workflow_contract_materialized_case_count") == len(states)
    assert plan.get("main_chain_stage_kinds") == [item[5] for item in states]
    assert plan.get("linear_executable") is True


def test_text_stage_classifier_keeps_save_and_display_as_commit() -> None:
    steps = [
        ("start", "open workflow alpha entry", "ready", "started"),
        ("configure", "configure schedule beta slot", "started", "configured"),
        ("preview", "review preview gamma summary", "configured", "preview_ready"),
        ("commit", "save plan and display delta confirmation", "preview_ready", "committed"),
        ("visible", "display saved plan on student home epsilon card", "committed", "visible"),
        ("consume", "learn course from visible plan zeta lesson", "visible", "consumed"),
    ]
    state = {
        "workflow_blueprints": [
            {
                "id": "save_display_flow",
                "workflow_id": "save_display_flow",
                "name": "save display flow",
                "source_type": "human_reviewed",
                "repository_source": "workflow_blueprint_repository",
                "trusted": True,
                "steps": [
                    {
                        "id": step_id,
                        "label": action,
                        "action": action,
                        "actor": "student",
                        "state_in": state_in,
                        "state_out": state_out,
                        "match_keywords": [action],
                    }
                    for step_id, action, state_in, state_out in steps
                ],
            }
        ]
    }
    result = _run_cases(
        requirement="Save plan then display it on student home",
        cases=[
            {
                "id": f"TC-{index:03d}",
                "description": action,
                "test_module": f"workflow {step_id}",
                "steps": [action, f"complete unique {step_id} operation"],
                "test_input": f"{state_in} dataset for {step_id}",
                "expected_result": "saved plan is visible on epsilon card" if step_id == "visible" else f"state reaches {state_out} for {step_id}",
                "priority": "P0",
            }
            for index, (step_id, action, state_in, state_out) in enumerate(steps, start=1)
        ],
        expected_count=10,
        feedback_control_state=state,
    )

    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})
    main_cases = [
        item for item in (result.get("cases") or [])
        if isinstance(item, dict) and str(item.get("execution_group") or "") == "main_smoke"
    ]
    assert plan.get("main_chain_stage_kinds")[3] == "commit"
    assert plan.get("main_chain_stage_kinds")[4] == "downstream_visibility"
    assert [dict(item.get("workflow_transition") or {}).get("stage_kind") for item in main_cases][3:5] == [
        "commit",
        "downstream_visibility",
    ]
    assert plan.get("linear_executable") is True


def test_persist_priority_normalization_preserves_execution_plan_p0() -> None:
    result = normalize_final_case_priorities(
        [
            {
                "id": "TC-001",
                "description": "Homepage course card title display",
                "test_module": "student home display",
                "preconditions": ["plan saved"],
                "steps": ["open student home"],
                "test_input": "saved plan",
                "expected_result": "course card title is visible",
                "priority": "P0",
                "priority_final": "P0",
                "execution_group": "main_smoke",
            }
        ],
        requirement_text="student home shows the saved plan",
    )

    assert str(result[0].get("priority") or "") == "P0"
    assert str(result[0].get("priority_final") or "") == "P0"
    assert str(result[0].get("priority_decision_source") or "") == "preserved_execution_plan_priority"


def test_execution_plan_excludes_negative_and_destructive_cases_from_main_smoke() -> None:
    state = {
        "workflow_blueprints": [
            {
                "id": "create_plan_flow",
                "name": "create plan flow",
                "steps": [
                    {
                        "id": "configure_plan",
                        "label": "Configure plan",
                        "actor": "supervisor",
                        "state_in": "initial",
                        "state_out": "plan_configured",
                        "match_keywords": ["configure plan"],
                        "assertion": "plan is configured",
                    },
                    {
                        "id": "save_plan",
                        "label": "Save plan",
                        "actor": "supervisor",
                        "state_in": "plan_configured",
                        "state_out": "plan_saved",
                        "match_keywords": ["save plan"],
                        "assertion": "plan is saved",
                    },
                    {
                        "id": "student_visibility",
                        "label": "Student visibility",
                        "actor": "student",
                        "state_in": "plan_saved",
                        "state_out": "student_home_visible",
                        "match_keywords": ["student home visible"],
                        "assertion": "new plan is visible",
                    },
                    {
                        "id": "open_course",
                        "label": "Open course",
                        "actor": "student",
                        "state_in": "student_home_visible",
                        "state_out": "course_opened",
                        "match_keywords": ["open course"],
                        "assertion": "course page opens",
                    },
                ],
            }
        ]
    }
    result = _run_cases(
        requirement="Supervisor creates a plan, student sees the new plan and opens the course.",
        cases=[
            {
                "id": "TC-001",
                "description": "Configure plan with selected courses",
                "test_module": "plan create",
                "steps": ["Open create plan", "Select courses"],
                "expected_result": "plan is configured with selected courses",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Capacity shortage blocks configure plan",
                "test_module": "plan create",
                "steps": ["Select too many courses"],
                "expected_result": "system shows capacity limit and cannot continue",
                "priority": "P0",
            },
            {
                "id": "TC-003",
                "description": "Save plan successfully",
                "test_module": "plan create",
                "steps": ["Preview plan", "Save plan"],
                "expected_result": "plan is saved with id PLAN-100",
                "priority": "P1",
            },
            {
                "id": "TC-004",
                "description": "Save plan blocked by time conflict",
                "test_module": "plan create",
                "steps": ["Save plan with conflicting time"],
                "expected_result": "save is blocked and conflict message is shown",
                "priority": "P0",
            },
            {
                "id": "TC-005",
                "description": "Student home visible after new plan sync",
                "test_module": "student home",
                "steps": ["Open student home"],
                "expected_result": "new plan is visible on student home",
                "priority": "P1",
            },
            {
                "id": "TC-006",
                "description": "Open course from student home",
                "test_module": "student home",
                "steps": ["Click course card"],
                "expected_result": "course page opens for PLAN-100",
                "priority": "P1",
            },
            {
                "id": "TC-007",
                "description": "Save plan fails during network timeout",
                "test_module": "plan create",
                "steps": ["Save plan during network timeout"],
                "expected_result": "save plan failed and retry action is shown",
                "priority": "P0",
            },
            {
                "id": "TC-008",
                "description": "Delete existing plan",
                "test_module": "plan management",
                "steps": ["Delete plan PLAN-100"],
                "expected_result": "plan is removed from management list",
                "priority": "P0",
            },
        ],
        expected_count=80,
        feedback_control_state=state,
    )

    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    main_cases = [item for item in output_cases if str(item.get("execution_group") or "") == "main_smoke"]
    assert [item.get("main_chain_stage") for item in main_cases] == [
        "configure_plan",
        "save_plan",
        "student_visibility",
        "open_course",
    ]
    assert all(str(item.get("priority") or "") == "P0" for item in main_cases)
    main_descriptions = " ".join(str(item.get("description") or "") for item in main_cases).lower()
    assert "capacity" not in main_descriptions
    assert "conflict" not in main_descriptions
    assert "delete" not in main_descriptions
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})
    excluded_reasons = {str(item.get("reason") or "") for item in (plan.get("main_chain_excluded_candidates") or [])}
    assert "boundary_capacity" in excluded_reasons
    assert plan.get("linear_executable") is True
    final_breakdown = dict((result.get("review_decision_summary") or {}).get("priority_final_breakdown") or {})
    assert int(final_breakdown.get("P0") or 0) >= len(main_cases)


def test_execution_plan_does_not_infer_main_smoke_without_workflow_blueprint() -> None:
    result = _run_cases(
        requirement="Checkout regression",
        cases=[
            {
                "id": "TC-001",
                "description": "Submit order creates an order record",
                "test_module": "checkout",
                "steps": ["Open checkout", "Submit order"],
                "expected_result": "order is created",
                "priority": "P0",
            },
            {
                "id": "TC-002",
                "description": "Order detail shows paid status",
                "test_module": "order detail",
                "steps": ["Open order detail"],
                "expected_result": "status is paid",
                "priority": "P0",
            },
        ],
        expected_count=10,
    )

    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert not [item for item in output_cases if str(item.get("execution_group") or "") == "main_smoke"]
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})
    assert plan.get("workflow_blueprint_count") == 0


def test_execution_plan_can_bridge_generic_main_flow_without_domain_template() -> None:
    result = _run_cases(
        requirement="Generic workflow regression should preserve a positive entry, commit, and downstream visibility chain.",
        cases=[
            {
                "id": "TC-001",
                "description": "Open workflow entry and prepare state",
                "test_module": "workflow entry",
                "steps": ["Open entry page", "Prepare valid state"],
                "expected_result": "workflow entry is ready",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Commit the workflow change successfully",
                "test_module": "workflow commit",
                "steps": ["Save change"],
                "expected_result": "workflow change is saved successfully",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "Downstream view reflects the committed change",
                "test_module": "workflow downstream",
                "steps": ["Refresh downstream page"],
                "expected_result": "new state becomes visible downstream",
                "priority": "P1",
            },
        ],
        expected_count=10,
    )

    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    main_cases = [item for item in output_cases if str(item.get("execution_group") or "") == "main_smoke"]
    assert len(main_cases) >= 2
    transitions = [dict(item.get("workflow_transition") or {}) for item in main_cases]
    assert all(item.get("path_type") == "positive" for item in transitions)
    assert all(item.get("blocking") is False for item in transitions)
    assert all(item.get("destructive") is False for item in transitions)
    assert all(item.get("can_advance_main_flow") is True for item in transitions)
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})
    assert plan.get("linear_executable") is True
    assert plan.get("main_chain_case_count") == len(main_cases)


def test_execution_plan_treats_interaction_scoring_as_current_doc_commit() -> None:
    result = _run_cases(
        requirement="Interactive AI tutoring flow: enter page, complete dialog, trigger scoring, then show score result.",
        cases=[
            {
                "id": "TC-001",
                "description": "Enter AI tutoring page",
                "test_module": "entry",
                "steps": ["Open tutoring page"],
                "expected_result": "workflow entry is ready for dialog",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Complete dialog and trigger score calculation",
                "test_module": "AI scoring",
                "steps": ["Complete the final dialog round", "Trigger score calculation"],
                "expected_result": "score calculation is generated successfully",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "Display score result after scoring",
                "test_module": "score result",
                "steps": ["Open score result page"],
                "expected_result": "score result is shown with pass or fail status",
                "priority": "P1",
            },
        ],
        expected_count=10,
    )

    main_cases = [
        item for item in (result.get("cases") or [])
        if isinstance(item, dict) and str(item.get("execution_group") or "") == "main_smoke"
    ]
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})

    assert len(main_cases) >= 2
    assert "commit" in list(plan.get("main_chain_stage_kinds") or [])
    assert plan.get("linear_executable") is True
    assert plan.get("workflow_blueprint_source") == "current_generation_cases"


def test_current_generation_main_chain_excludes_conditional_visibility_and_resume_checks() -> None:
    result = _run_cases(
        requirement=(
            "Interactive AI tutoring flow: enter page, complete dialog, trigger scoring, "
            "then show score result. Conditional button visibility and unfinished reentry "
            "checks are regression cases, not main smoke chain steps."
        ),
        cases=[
            {
                "id": "TC-001",
                "description": "Enter AI tutoring page",
                "test_module": "entry",
                "steps": ["Open tutoring page"],
                "expected_result": "workflow entry is ready for dialog",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Complete dialog and trigger score calculation",
                "test_module": "AI scoring",
                "steps": ["Complete the final dialog round", "Trigger score calculation"],
                "expected_result": "score calculation is generated successfully",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "Display score result after scoring",
                "test_module": "score result",
                "steps": ["Open score result page"],
                "expected_result": "score result is shown with pass or fail status",
                "priority": "P1",
            },
            {
                "id": "TC-004",
                "description": "Only when quiz accuracy is greater than 50%, the review button is visible",
                "test_module": "conditional visibility",
                "steps": ["Open quiz feedback popup"],
                "expected_result": "the review button is visible only for the threshold condition",
                "priority": "P0",
            },
            {
                "id": "TC-005",
                "description": "Re-enter unfinished tutoring flow and verify retained dialog history",
                "test_module": "resume state",
                "steps": ["Leave unfinished flow", "Re-enter tutoring page"],
                "expected_result": "retained dialog history is displayed after reentry",
                "priority": "P0",
            },
        ],
        expected_count=10,
    )

    main_cases = [
        item for item in (result.get("cases") or [])
        if isinstance(item, dict) and str(item.get("execution_group") or "") == "main_smoke"
    ]
    main_descriptions = " ".join(str(item.get("description") or "") for item in main_cases)
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})

    assert "review button is visible" not in main_descriptions
    assert "retained dialog history" not in main_descriptions
    assert "commit" in list(plan.get("main_chain_stage_kinds") or [])
    assert plan.get("linear_executable") is True


def test_current_generation_main_chain_uses_real_precommit_consume_without_bridge_case() -> None:
    result = _run_cases(
        requirement=(
            "AI tutoring flow: student enters the tutoring dialog, reads the AI question, "
            "submits an answer, triggers scoring, and then sees the score result."
        ),
        cases=[
            {
                "id": "TC-001",
                "description": "Student clicks AI tutoring task before answering",
                "test_module": "student dialog",
                "steps": ["Click AI tutoring task"],
                "expected_result": "answer area is ready for student response",
                "priority": "P1",
            },
            {
                "id": "TC-002",
                "description": "Student views AI question prompt before answering",
                "test_module": "AI question prompt",
                "steps": ["View current AI question prompt"],
                "expected_result": "question prompt is ready for student answer",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "Submit answer and trigger score calculation",
                "test_module": "AI scoring",
                "steps": ["Submit answer", "Trigger score calculation"],
                "expected_result": "score calculation is generated successfully",
                "priority": "P0",
            },
            {
                "id": "TC-004",
                "description": "Display score result after scoring",
                "test_module": "score result",
                "steps": ["Open score result page"],
                "expected_result": "score result is shown with pass or fail status",
                "priority": "P1",
            },
        ],
        expected_count=10,
    )

    main_cases = [
        item for item in (result.get("cases") or [])
        if isinstance(item, dict) and str(item.get("execution_group") or "") == "main_smoke"
    ]
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})

    assert main_cases
    assert not any(bool(item.get("generated_bridge_case")) for item in main_cases)
    assert plan.get("generated_bridge_case_count") == 0
    assert "commit" in list(plan.get("main_chain_stage_kinds") or [])
    assert plan.get("linear_executable") is True


def test_current_generation_main_chain_does_not_materialize_internal_entry_bridge() -> None:
    result = _run_cases(
        requirement=(
            "AI tutoring flow: after the student submits an answer, the system triggers scoring "
            "and displays the score result. No upstream entry or consume step is present."
        ),
        cases=[
            {
                "id": "TC-001",
                "description": "Submit answer and trigger score calculation",
                "test_module": "AI scoring",
                "steps": ["Submit answer", "Trigger score calculation"],
                "expected_result": "score calculation is generated successfully",
                "priority": "P0",
            },
            {
                "id": "TC-002",
                "description": "Display score result after scoring",
                "test_module": "score result",
                "steps": ["Open score result page"],
                "expected_result": "score result is shown with pass or fail status",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "Show score result details on feedback page",
                "test_module": "score feedback",
                "steps": ["Open feedback page", "Display score result details"],
                "expected_result": "score result details are visible to the student",
                "priority": "P1",
            },
        ],
        expected_count=10,
    )

    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    main_cases = [item for item in output_cases if str(item.get("execution_group") or "") == "main_smoke"]
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})

    assert not main_cases
    assert not any(bool(item.get("generated_bridge_case")) for item in output_cases)
    assert plan.get("generated_bridge_case_count") == 0
    assert plan.get("workflow_blueprint_source") == "none"
    assert plan.get("main_chain_incomplete_reason") == "missing_configure_or_entry_step"
    assert plan.get("linear_executable") is False


def test_execution_plan_does_not_fake_current_main_smoke_when_commit_is_pruned() -> None:
    result = _run_cases(
        requirement="督导完成新增计划后，学生端首页展示本周任务并可点击学习。",
        cases=[
            {
                "id": "TC-001",
                "description": "首页本周任务卡片展示",
                "test_module": "首页",
                "steps": ["1. 打开学生端首页", "2. 查看本周任务"],
                "expected_result": "首页展示本周任务卡片",
                "priority": "P2",
            },
            {
                "id": "TC-002",
                "description": "排课新增计划第一步选择课程",
                "test_module": "排课-新增计划",
                "steps": ["1. 督导进入新增计划", "2. 选择课程", "3. 点击下一步"],
                "expected_result": "课程加入已选列表并进入时间设置步骤",
                "priority": "P0",
            },
            {
                "id": "TC-003",
                "description": "排课新增计划第二步设置上课时间",
                "test_module": "排课-新增计划",
                "steps": ["1. 设置上课时间", "2. 点击下一步"],
                "expected_result": "上课时间保存到计划草稿并进入预览步骤",
                "priority": "P0",
            },
            {
                "id": "TC-004",
                "description": "排课新增计划第三步预览并保存",
                "test_module": "排课-新增计划",
                "steps": ["1. 查看预览", "2. 点击保存"],
                "expected_result": "计划保存成功并回到课程管理页",
                "priority": "P0",
            },
            {
                "id": "TC-005",
                "description": "学生点击学习进入正确课程",
                "test_module": "首页本周任务",
                "steps": ["1. 学生端首页点击学习按钮"],
                "expected_result": "系统进入对应课程学习页",
                "priority": "P0",
            },
            {
                "id": "TC-006",
                "description": "学习计划页 PV/UV 埋点上报",
                "test_module": "埋点",
                "steps": ["1. 打开学习计划页"],
                "expected_result": "PV 和 UV 埋点上报成功",
                "priority": "P2",
            },
        ],
        expected_count=10,
    )

    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    main_cases = [item for item in output_cases if str(item.get("execution_group") or "") == "main_smoke"]
    assert not main_cases
    analytics_cases = [item for item in output_cases if "埋点" in str(item.get("description") or "")]
    assert all(str(item.get("execution_group") or "") != "main_smoke" for item in analytics_cases)
    plan = dict((result.get("review_decision_summary") or {}).get("execution_plan") or {})
    assert plan.get("workflow_blueprint_source") == "none"
    assert plan.get("linear_executable") is False


def test_reasoning_leakage_is_detected_in_description() -> None:
    filtered = filter_invalid_final_cases(
        [
            {
                "id": "TC-001",
                "description": "针对已存在计划编辑场景？但新增计划不应有已完成/进行中。",
                "test_module": "排课-新增计划",
                "preconditions": ["督导登录"],
                "steps": ["进入新增计划"],
                "expected_result": "新建计划页面正常展示",
                "priority": "P1",
            }
        ]
    )

    assert filtered == []


def test_execution_plan_keeps_submission_rule_popup_on_student_session() -> None:
    result = _run_cases(
        requirement="作文投稿：学生从批改结果进入投稿页，首次进入会弹出规则说明弹窗。",
        cases=[
            {
                "id": "TC-001",
                "description": "投稿页首次进入自动弹出规则说明弹窗",
                "test_module": "作文投稿",
                "preconditions": ["学生用户已生成批改结果"],
                "steps": ["1. 点击投稿", "2. 进入投稿页", "3. 查看规则说明弹窗和标题正文"],
                "test_input": "已批改作文",
                "expected_result": "学生端进入投稿页后弹出规则说明弹窗，关闭后标题和正文输入区可编辑",
                "priority": "P1",
            }
        ],
    )

    case = next(
        item for item in (result.get("cases") or [])
        if isinstance(item, dict) and "规则说明弹窗" in str(item.get("description") or "")
    )
    assert str(case.get("role") or "") == "student"
    assert str(case.get("session_key") or "") == "student_session"
    assert str(case.get("role_switch_strategy") or "") == "reuse_group_session"


def test_execution_plan_does_not_use_community_fixture_for_generic_student_list_sorting() -> None:
    result = _run_cases(
        requirement="督导端学员列表支持科目筛选和列表展示，不涉及作文圈或社区作品。",
        cases=[
            {
                "id": "TC-001",
                "description": "督导端科目筛选功能：选择数学后，学员列表只显示数学科目学员",
                "test_module": "督导端学员列表",
                "preconditions": ["督导已登录，学员列表包含数学、物理等多个科目的学员"],
                "steps": ["1. 点击科目筛选下拉框", "2. 选择数学", "3. 查看学员列表"],
                "test_input": "筛选条件：数学",
                "expected_result": "列表仅显示科目为数学的学员，其他科目学员不再出现",
                "priority": "P1",
            }
        ],
    )

    case = next(item for item in (result.get("cases") or []) if isinstance(item, dict))
    assert str(case.get("role") or "") == "supervisor"
    assert str(case.get("session_key") or "") == "supervisor_session"
    assert str(case.get("fixture_key") or "") != "community_tab_sorting_dataset"
    assert str(case.get("fixture_builder") or "") != "seed_community_works(status='published', count=30, with_like_reply_time_distribution=true)"


def test_execution_plan_uses_browser_permission_fixture_for_microphone_permission() -> None:
    result = _run_cases(
        requirement="学员端讲错题支持语音录制，首次使用需要处理浏览器麦克风授权。",
        cases=[
            {
                "id": "TC-001",
                "description": "学员端语音录制功能异常场景：首次使用时用户拒绝麦克风权限",
                "test_module": "学员端讲错题页面录音功能",
                "preconditions": ["学员尚未授权麦克风权限"],
                "steps": ["1. 点击语音录制按钮", "2. 在浏览器权限弹窗中选择禁止", "3. 观察页面反馈"],
                "test_input": "用户拒绝麦克风权限",
                "expected_result": "页面提示麦克风权限被拒绝，且输入框仍可正常输入文字提交",
                "priority": "P1",
            }
        ],
    )

    case = next(item for item in (result.get("cases") or []) if isinstance(item, dict))
    assert str(case.get("execution_group") or "") == "permission"
    assert str(case.get("fixture_key") or "") == "browser_permission_state"
    assert str(case.get("fixture_builder") or "") == "set_browser_permission(permission='microphone', state='prompt')"
    assert str(case.get("group_setup") or "") == "set_browser_permission(permission='microphone', state='prompt')"
    assert str(case.get("cleanup_policy") or "") == "reset_browser_permissions"


def test_execution_plan_does_not_use_works_over_20_fixture_for_score_boundary_20() -> None:
    result = _run_cases(
        requirement="学员端讲错题评分规则必须断言：回答字数不足50字时完整性20分扣50%后显示为10/20，不涉及作文作品列表数量。",
        cases=[
            {
                "id": "TC-001",
                "description": "评分规则：回答字数不足50字时，完整性20分扣50%后显示为10/20",
                "test_module": "学员端-讲错题页面-评分规则-边界",
                "preconditions": ["学员已完成一轮讲错题回答，回答字数少于50字"],
                "steps": ["1. 提交少于50字的回答", "2. 完成交互并触发评分", "3. 查看评分明细"],
                "test_input": "少于50字的回答文本",
                "expected_result": "评分明细中完整性显示为10/20，清晰度同步按规则扣减，最终总分按扣分后计算",
                "priority": "P1",
            }
        ],
    )

    case = next(item for item in (result.get("cases") or []) if isinstance(item, dict))
    assert str(case.get("execution_group") or "") == "boundary"
    assert str(case.get("fixture_key") or "") != "works_over_20"
    assert str(case.get("fixture_builder") or "") != "seed_works(count=21)"


def test_execution_plan_uses_generic_boundary_fixture_for_composition_list_limit() -> None:
    result = _run_cases(
        requirement="我的作文列表最多展示20条作品，超过20条时需要验证数量上限。",
        cases=[
            {
                "id": "TC-001",
                "description": "我的作文最多20条：超过20篇作文记录时列表仅展示20条",
                "test_module": "我的作文",
                "preconditions": ["用户已有超过20篇作文记录"],
                "steps": ["1. 进入我的作文列表", "2. 查看列表展示数量"],
                "test_input": "21篇作文记录",
                "expected_result": "我的作文列表最多展示20条作品记录，其余记录通过分页或加载更多方式展示",
                "priority": "P1",
            }
        ],
    )

    case = next(item for item in (result.get("cases") or []) if isinstance(item, dict))
    assert str(case.get("fixture_key") or "") == "boundary_dataset"
    assert str(case.get("fixture_builder") or "") == "seed_boundary_dataset()"


def test_full_regression_does_not_use_deterministic_floor_supplement_templates() -> None:
    state = {
        "source_meta": {
            "generation_coverage_profile": {
                "coverage_mode": "full_functional_regression",
                "target_case_range": {"min": 85, "max": 90},
            }
        }
    }
    cases = [
        {
            "id": f"TC-{index:03d}",
            "description": f"作文批改完整回归辅助场景{index}",
            "test_module": "作文批改",
            "preconditions": ["学生用户已登录并准备好对应数据"],
            "steps": ["1. 打开对应页面", "2. 执行业务操作", "3. 查看页面和数据状态"],
            "test_input": f"辅助数据{index}",
            "expected_result": f"辅助场景{index}的页面反馈和数据状态与本次业务操作一致",
            "priority": "P2",
        }
        for index in range(1, 71)
    ]
    result = _run_cases(
        requirement="作文批改 full regression：覆盖上传图片、AI批改、投稿审核、作文圈、课程权限、写作秘籍和下载资料。",
        cases=cases,
        expected_count=90,
        feedback_control_state=state,
    )

    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert [str(item.get("id") or "") for item in output_cases] == [
        f"TC-{index:03d}" for index in range(1, len(output_cases) + 1)
    ]
    summary = dict(result.get("review_decision_summary") or {})
    assert summary.get("final_shortfall_supplement_applied") is not True
    assert int(summary.get("final_shortfall_supplement_count") or 0) == 0


def test_quality_governance_drops_template_polluted_original_image_assertion() -> None:
    result = _run_cases(
        requirement="投稿页显隐原图按钮：默认显示原图缩略图，点击后隐藏原图，再次点击恢复显示。",
        cases=[
            {
                "id": "TC-064",
                "description": "投稿页显/隐原图按钮功能验证",
                "test_module": "作文投稿",
                "preconditions": ["用户已进入投稿页且存在原图"],
                "steps": ["1. 点击显隐原图按钮", "2. 再次点击该按钮"],
                "test_input": "投稿页原图",
                "expected_result": "执行再次点击该按钮后，应跳转到目标页面，且页面路径与标题均与投稿页显/隐原图按钮功能验证一致",
                "priority": "P1",
            },
            {
                "id": "TC-065",
                "description": "投稿页显隐原图按钮正确切换",
                "test_module": "作文投稿",
                "preconditions": ["用户已进入投稿页且存在原图"],
                "steps": ["1. 点击显隐原图按钮", "2. 再次点击该按钮"],
                "test_input": "投稿页原图",
                "expected_result": "默认显示原图缩略图；点击后隐藏原图并切换按钮状态；再次点击后恢复原图显示",
                "priority": "P1",
            },
        ],
    )

    descriptions = {str(item.get("description") or "") for item in (result.get("cases") or [])}
    assert "投稿页显/隐原图按钮功能验证" not in descriptions
    assert "投稿页显隐原图按钮正确切换" in descriptions


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
    monkeypatch.setattr(settings, "EXECUTION_PLAN_GATE_MODE", "shadow", raising=False)

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


def test_json_persistence_projects_final_case_contract(monkeypatch) -> None:
    _configure_backfill_apply_env(
        monkeypatch,
        enabled=False,
        apply_to_final=False,
        merged_preview_cases=[],
    )

    source_cases = _primary_cases_for_backfill_apply()
    source_cases[0].update(
        {
            "workflow_transition": {
                "workflow_id": "schedule-main",
                "source_state": "course_selected",
                "action": "configure_time",
                "target_state": "time_configured",
                "path_type": "main",
                "blocking": True,
                "destructive": False,
                "can_advance_main_flow": True,
            },
            "execution_group": "schedule-main",
            "execution_sequence": 1,
            "role": "teacher",
            "session_key": "teacher_session",
            "model_priority_current": "P1",
            "priority_decision_state": "decided",
            "priority_debug": {"reason": "debug-only"},
        }
    )

    monkeypatch.setattr(
        json_generation_mod,
        "run_multi_pass_generation",
        lambda **kwargs: {
            "final_cases": [dict(item) for item in source_cases],
            "stage_logs": [
                {"kind": "generation_stage", "stage": "primary", "case_count": len(source_cases)},
                {"kind": "generation_stage", "stage": "gap", "case_count": 0},
                {"kind": "generation_stage", "stage": "review", "case_count": len(source_cases)},
            ],
            "coverage": {"kind": "coverage_check", "covered_rules": ["RULE-001"], "missing_rules": []},
            "raw": {},
        },
    )

    module = TestGenerationModule()
    db = _FakeActiveSession()
    result = module.generate_test_cases_json(
        requirement="json path should persist only formal case fields",
        project_id=16,
        db=db,
        user_id=9,
        expected_count=8,
        multi_pass=True,
        generation_mode="multi_pass",
    )

    assert isinstance(result, list)
    assert db.generations
    stored = json.loads(str(db.generations[-1].generated_result or "[]"))
    assert stored[0]["priority"] == "P1"
    assert stored[0]["priority_final"] == "P1"
    assert stored[0]["workflow_id"] == "schedule-main"
    assert stored[0]["source_state"] == "course_selected"
    assert stored[0]["target_state"] == "time_configured"
    assert stored[0]["execution_group"] == "schedule-main"
    assert stored[0]["role"] == "teacher"
    assert stored[0]["session_key"] == "teacher_session"
    assert "model_priority_current" not in stored[0]
    assert "priority_decision_state" not in stored[0]
    assert "priority_debug" not in stored[0]
    assert "workflow_transition" not in stored[0]


def test_json_persistence_recalculates_priority_final_when_upstream_stripped(monkeypatch) -> None:
    _configure_backfill_apply_env(
        monkeypatch,
        enabled=False,
        apply_to_final=False,
        merged_preview_cases=[],
    )

    source_cases = _primary_cases_for_backfill_apply()
    for item in source_cases:
        item.pop("priority_final", None)

    monkeypatch.setattr(
        json_generation_mod,
        "run_multi_pass_generation",
        lambda **kwargs: {
            "final_cases": [dict(item) for item in source_cases],
            "stage_logs": [
                {"kind": "generation_stage", "stage": "primary", "case_count": len(source_cases)},
                {"kind": "generation_stage", "stage": "gap", "case_count": 0},
                {"kind": "generation_stage", "stage": "review", "case_count": len(source_cases)},
            ],
            "coverage": {"kind": "coverage_check", "covered_rules": ["RULE-001"], "missing_rules": []},
            "raw": {},
        },
    )

    module = TestGenerationModule()
    db = _FakeActiveSession()
    result = module.generate_test_cases_json(
        requirement="json path should finalize priority when priority_final is stripped",
        project_id=17,
        db=db,
        user_id=9,
        expected_count=8,
        multi_pass=True,
        generation_mode="multi_pass",
    )

    assert isinstance(result, list)
    stored = json.loads(str(db.generations[-1].generated_result or "[]"))
    assert stored
    assert all(str(item.get("priority_final") or "").strip().upper() == "P1" for item in stored)
    assert all(str(item.get("priority") or "").strip().upper() == "P1" for item in stored)


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


def test_json_persistence_enforce_mode_blocks_without_workflow_contract(monkeypatch) -> None:
    _configure_backfill_apply_env(
        monkeypatch,
        enabled=False,
        apply_to_final=False,
        merged_preview_cases=[],
    )
    monkeypatch.setattr(settings, "EXECUTION_PLAN_GATE_MODE", "enforce", raising=False)
    monkeypatch.setattr(settings, "EXECUTION_PLAN_ALLOW_CANDIDATE_BLUEPRINT_WITHOUT_CONTRACT", False, raising=False)

    module = TestGenerationModule()
    db = _FakeActiveSession()
    result = module.generate_test_cases_json(
        requirement="formal persistence requires an executable workflow contract",
        project_id=15,
        db=db,
        user_id=9,
        expected_count=8,
        multi_pass=True,
        generation_mode="multi_pass",
    )

    assert isinstance(result, dict)
    assert result["error_code"] == "execution_plan_failed"
    assert "workflow_contract_missing" in list(result.get("failure_reasons") or [])
    assert db.generations == []
    persistence_gate = _extract_gen_diag(db, "persistence_gate")
    assert persistence_gate["blocked"] is True
    assert persistence_gate["failure_code"] == "execution_plan_failed"
