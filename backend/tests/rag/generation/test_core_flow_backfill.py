"""
测试 core_flow_backfill planner — deterministic dry-run。

验证：
1. generation_id=443 这种 0/12 覆盖时可以生成 backfill_plan
2. 每个 missing_core_flow 都有 flow_key、required_focus、suggested_priority、must_include_assertions
3. backfill plan 不直接修改 existing_cases
4. LLM 自报 coverage_tags 不作为最终判定依据
5. dry-run 输出 backfill_not_applied=true
6. paid_gate / unauthorized_data_isolation / supervisor_report_generation 至少能生成明确断言 spec
"""

from __future__ import annotations

from modules.test_generation_components.coverage.core_flow_coverage_contract import (
    audit_core_flow_coverage,
    map_case_to_core_flows,
)
from modules.test_generation_components.coverage.core_flow_backfill import (
    BACKFILL_SPECS,
    plan_core_flow_backfill,
)

GEN_443_CASES = [
    {
        "id": "TC-001",
        "description": "验证作业题目列表正常加载，采用瀑布流布局展示本次作业的全部题目，每道题为独立卡片",
        "test_module": "作业题目列表瀑布流卡片",
        "priority_final": "P1",
        "steps": ["1. 进入作业题目列表页面", "2. 观察列表加载过程和展示结果"],
        "test_input": "无",
        "expected_result": "页面正常加载，题目以瀑布流方式排列，本次作业的全部题目均以独立卡片形式展示，无遗漏",
    },
    {
        "id": "TC-002",
        "description": "验证点击卡片上的'查看讲解'按钮，能够正确跳转到该题的讲解页面",
        "test_module": "作业题目列表瀑布流卡片",
        "priority_final": "P1",
        "steps": ["1. 在题目卡片上点击'查看讲解'按钮", "2. 检查跳转后的页面"],
        "test_input": "无",
        "expected_result": "成功跳转到对应题目的讲解页面，讲解内容与该题目匹配",
    },
    {
        "id": "TC-003",
        "description": "验证每张题卡的信息结构完整：状态标签（正确/错，颜色区分）、知识点标签、作业日期与类型元信息、题目文本、'查看讲解'按钮",
        "test_module": "作业题目列表瀑布流卡片",
        "priority_final": "P1",
        "steps": ["1. 查看一道正确题目的卡片", "2. 查看一道错误题目的卡片"],
        "test_input": "无",
        "expected_result": "正确题目卡片上显示绿色'正确'角标，错误题目显示红色'错'角标；所有卡片均显示AI识别的一级知识点、元信息、题目文本以及'查看讲解'按钮",
    },
    {
        "id": "TC-004",
        "description": "验证从题目讲解页返回题目列表时，列表状态保持不变（滚动位置、数据等）",
        "test_module": "作业题目列表瀑布流卡片",
        "priority_final": "P1",
        "steps": ["1. 进入题目列表并向下滚动一定距离", "2. 点击某张卡片的'查看讲解'跳转到讲解页", "3. 通过返回操作回到题目列表"],
        "test_input": "无",
        "expected_result": "返回后题目列表数据未重新刷新，滚动位置保持离开前的位置，无异常闪动",
    },
    {
        "id": "TC-005",
        "description": "验证网络异常（如离线或超时）时，题目列表加载失败的处理",
        "test_module": "作业题目列表瀑布流卡片",
        "priority_final": "P1",
        "steps": ["1. 进入作业题目列表页面", "2. 观察加载过程和异常提示"],
        "test_input": "无",
        "expected_result": "页面给出明确的网络异常提示（如'加载失败，请检查网络'），不会白屏或闪退，提供重试入口",
    },
    {
        "id": "TC-006",
        "description": "验证大量题目（如超过50道）时，瀑布流列表的滑动性能和分页加载",
        "test_module": "作业题目列表瀑布流卡片",
        "priority_final": "P1",
        "steps": ["1. 进入题目列表页", "2. 连续快速滑动列表", "3. 滑动至底部"],
        "test_input": "无",
        "expected_result": "列表滑动流畅，无卡顿或明显白块；所有题目均可正常加载展示（若采用分页，应自动加载后续题目）",
    },
    {
        "id": "TC-007",
        "description": "验证本次作业无题目时，题目列表页的空状态展示",
        "test_module": "作业题目列表瀑布流卡片",
        "priority_final": "P1",
        "steps": ["1. 进入作业题目列表页面"],
        "test_input": "无",
        "expected_result": "页面显示友好的空状态占位（如'暂无题目'文案），无任何题目卡片",
    },
    {
        "id": "TC-008",
        "description": "验证AI自动识别的知识点标签与题目内容匹配的准确性",
        "test_module": "作业题目列表瀑布流卡片",
        "priority_final": "P1",
        "steps": ["1. 进入题目列表页", "2. 逐一检查每道题的知识点标签"],
        "test_input": "无",
        "expected_result": "所有题目的知识点标签与题目实际考查的知识点相符，一级知识点命名准确",
    },
]


def test_all_12_flows_have_backfill_specs():
    """每个 flow_id 都有对应的 backfill spec"""
    from modules.test_generation_components.coverage.core_flow_coverage_contract import CORE_FLOWS
    flow_ids = {f["flow_id"] for f in CORE_FLOWS}
    spec_ids = set(BACKFILL_SPECS.keys())
    missing = flow_ids - spec_ids
    assert not missing, f"缺少 backfill spec: {missing}"
    extra = spec_ids - flow_ids
    assert not extra, f"多余 spec: {extra}"


def test_gen443_0_12_coverage_generates_backfill_plan():
    """generation_id=443 这种 0/12 覆盖时可以生成 backfill_plan"""
    plan = plan_core_flow_backfill(
        requirement_context="作业题目列表瀑布流卡片功能测试",
        existing_cases=GEN_443_CASES,
        max_backfill_cases=12,
    )
    assert plan["enabled"] is True
    assert plan["dry_run"] is True
    assert plan["primary_case_count"] == 8
    assert plan["coverage_before"]["covered_count"] == 0
    assert plan["coverage_before"]["coverage_ratio"] == 0.0
    assert plan["planned_backfill_count"] == 12
    assert len(plan["backfill_plan"]) == 12
    assert plan["coverage_after_theoretical"]["covered_count"] == 12
    assert plan["coverage_after_theoretical"]["coverage_ratio"] == 1.0
    assert plan["coverage_after_theoretical"]["coverage_passed"] is True


def test_backfill_plan_does_not_modify_existing_cases():
    """backfill plan 不直接修改 existing_cases"""
    original_cases = [dict(c) for c in GEN_443_CASES]
    plan_core_flow_backfill(
        requirement_context="test",
        existing_cases=GEN_443_CASES,
        max_backfill_cases=12,
    )
    for orig, current in zip(original_cases, GEN_443_CASES):
        assert orig == current, f"用例被修改: {orig.get('id')}"


def test_each_flow_has_required_fields():
    """每个 missing_core_flow 都有 flow_key、required_focus、suggested_priority、must_include_assertions"""
    plan = plan_core_flow_backfill(
        requirement_context="test",
        existing_cases=GEN_443_CASES,
        max_backfill_cases=12,
    )
    for item in plan["backfill_plan"]:
        assert item.get("flow_key"), f"缺少 flow_key: {item}"
        assert item.get("flow_name"), f"缺少 flow_name: {item}"
        assert item.get("required_focus"), f"缺少 required_focus: {item['flow_key']}"
        assert item.get("suggested_priority") in {"P0", "P1", "P2"}, (
            f"无效 priority: {item.get('suggested_priority')} for {item['flow_key']}"
        )
        assert isinstance(item.get("must_include_assertions"), list), (
            f"must_include_assertions 不是 list: {item['flow_key']}"
        )
        assert len(item["must_include_assertions"]) >= 2, (
            f"must_include_assertions 少于 2 条: {item['flow_key']}"
        )


def test_dry_run_output_backfill_not_applied_true():
    """dry-run 输出 backfill_not_applied=true"""
    plan = plan_core_flow_backfill(
        requirement_context="test",
        existing_cases=GEN_443_CASES,
        max_backfill_cases=12,
    )
    assert plan["backfill_not_applied"] is True
    assert plan["generated_backfill_candidate_count"] == 0
    assert plan["accepted_backfill_candidate_count"] == 0


def test_paid_gate_spec_has_concrete_assertions():
    """paid_gate 至少能生成明确断言 spec"""
    spec = BACKFILL_SPECS["paid_gate"]
    assert spec["suggested_priority"] == "P0"
    assert len(spec["must_include_assertions"]) >= 3
    assert "弹窗" in spec["required_focus"] or "付费" in spec["required_focus"]


def test_unauthorized_isolation_spec_has_concrete_assertions():
    """unauthorized_data_isolation 至少能生成明确断言 spec"""
    spec = BACKFILL_SPECS["unauthorized_data_isolation"]
    assert spec["suggested_priority"] == "P0"
    assert len(spec["must_include_assertions"]) >= 3
    assert "权限" in spec["required_focus"] or "鉴权" in spec["required_focus"]


def test_supervisor_report_spec_has_concrete_assertions():
    """supervisor_report_generation 至少能生成明确断言 spec"""
    spec = BACKFILL_SPECS["supervisor_report_generation"]
    assert spec["suggested_priority"] == "P1"
    assert len(spec["must_include_assertions"]) >= 3
    assert "督导端" in spec["required_focus"] or "报告" in spec["required_focus"]


def test_llm_coverage_tags_not_trusted():
    """
    LLM 自报 coverage_tags 不作为最终判定依据。
    模拟 LLM 返回 coverage_tags，证明 deterministic mapper 独立判定。
    """
    llm_case = {
        "id": "TC-BACKFILL-001",
        "description": "验证付费拦截弹窗展示",
        "test_module": "付费拦截",
        "steps": ["1. 未付费用户登录", "2. 点击学习入口"],
        "expected_result": "弹出付费拦截页面",
        "coverage_tags": ["wrong_only_filter"],  # LLM 自报错误 tag
    }
    hits = map_case_to_core_flows(llm_case)
    assert "paid_gate" in hits, f"deterministic mapper 应命中 paid_gate, 实际: {hits}"
    assert "wrong_only_filter" not in hits, (
        f"LLM 自报的 wrong_only_filter 不应被 deterministic mapper 确认, 实际: {hits}"
    )


def test_cover_all_p0_flows_have_p0_priority():
    """三个 P0 级核心闭环的 backfill spec 都是 P0"""
    p0_flows = {"paid_gate", "textbook_grade_isolation", "unauthorized_data_isolation", "weekend_classification"}
    for flow_id in p0_flows:
        spec = BACKFILL_SPECS.get(flow_id)
        assert spec is not None, f"缺少 spec: {flow_id}"
        assert spec["suggested_priority"] == "P0", (
            f"{flow_id} 应为 P0, 实际: {spec['suggested_priority']}"
        )


def test_empty_cases_plan():
    """空用例列表也能生成合理的 backfill plan"""
    plan = plan_core_flow_backfill(
        requirement_context="test",
        existing_cases=[],
        max_backfill_cases=12,
    )
    assert plan["primary_case_count"] == 0
    assert plan["planned_backfill_count"] == 12
    assert len(plan["backfill_plan"]) == 12


def test_max_backfill_cases_limiting():
    """max_backfill_cases 限制生效"""
    plan = plan_core_flow_backfill(
        requirement_context="test",
        existing_cases=GEN_443_CASES,
        max_backfill_cases=5,
    )
    assert plan["planned_backfill_count"] == 5
    assert len(plan["backfill_plan"]) == 5
    assert plan["coverage_after_theoretical"]["covered_count"] == 5


def test_backfill_plan_avoid_overlap():
    """backfill plan 包含 avoid_overlap_with_case_ids"""
    plan = plan_core_flow_backfill(
        requirement_context="test",
        existing_cases=GEN_443_CASES,
        max_backfill_cases=12,
    )
    existing_ids = {"TC-001", "TC-002", "TC-003", "TC-004", "TC-005", "TC-006", "TC-007", "TC-008"}
    for item in plan["backfill_plan"]:
        overlap = set(item.get("avoid_overlap_with_case_ids") or [])
        assert existing_ids.issubset(overlap), (
            f"{item['flow_key']} 的 avoid_overlap_with_case_ids 不包含所有已有用例"
        )


def test_all_12_p0_p1_p2_distribution_reasonable():
    """12 个 backfill spec 的 P0/P1/P2 分布合理"""
    plan = plan_core_flow_backfill(
        requirement_context="test",
        existing_cases=GEN_443_CASES,
        max_backfill_cases=12,
    )
    p0 = sum(1 for item in plan["backfill_plan"] if item["suggested_priority"] == "P0")
    p1 = sum(1 for item in plan["backfill_plan"] if item["suggested_priority"] == "P1")
    p2 = sum(1 for item in plan["backfill_plan"] if item["suggested_priority"] == "P2")
    assert p0 >= 3, f"P0 应 >= 3, 实际: {p0}"
    assert p1 >= 4, f"P1 应 >= 4, 实际: {p1}"
    assert p2 >= 0
    assert p0 + p1 + p2 == 12
