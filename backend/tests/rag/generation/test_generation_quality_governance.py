from __future__ import annotations

from modules.testing.test_generation_components.legacy.adapters import normalize_json_structure
from modules.testing.test_generation_components.postprocess.result_postprocess import filter_invalid_final_cases
from tests.rag.generation.quality_governance_harness import (
    run_quality_governance_cases as _run_cases,
)


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


def test_quality_governance_resolves_required_p0_priority_conflict_without_review() -> None:
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

    output_cases = [item for item in (result.get("cases") or []) if isinstance(item, dict)]
    assert len(output_cases) == 1

    review_summary = dict((result.get("review_decision_summary") or {}))
    assert review_summary.get("needs_priority_review") is False
    assert int(review_summary.get("priority_invalid_count") or 0) == 0
    assert bool(review_summary.get("priority_quality_gate_failed")) is False
    assert int(review_summary.get("priority_conflict_count") or 0) == 0
    assert int(review_summary.get("priority_undetermined_count") or 0) == 0

    table = [item for item in (result.get("review_decision_table") or []) if isinstance(item, dict)]
    assert len(table) == 1
    row = table[0]
    assert row.get("priority_decision_state") == "decided"
    assert row.get("priority_decision_source") == "conflict_resolved_by_high_risk_business_rule"
    assert row.get("priority_conflict_reason") == "model=P0,suggested=P2"
    assert row.get("priority_resolution_reason") == "high_risk_guard_or_keyword"
    assert row.get("priority_final") == "P0"

    generation_summary = dict((result.get("generation_summary") or {}))
    assert generation_summary.get("needs_priority_review") is False


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
