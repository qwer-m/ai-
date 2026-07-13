from __future__ import annotations

from typing import Any

from modules.test_generation_components.coverage.coverage_analyzer import analyze_coverage
from modules.test_generation_components.postprocess.json_processing import deduplicate_test_cases
from modules.test_generation_components.postprocess.streaming_case_keys import case_signature
from modules.test_generation_components.postprocess.streaming_postprocess_utils import _rule_diagnostics_payload
from modules.test_generation_components.postprocess.streaming_rule_rerank import rerank_and_cap_by_rule
from modules.test_generation_components.postprocess.streaming_text_match import CaseGovernanceMatcher


REQUIREMENT = """
课程排课需求：
REQ-101 保存排课时必须校验教师、教室、班级和时间段是否冲突。
REQ-102 重复提交或旧链路返回时不得覆盖最新草稿。
REQ-103 删除草稿后列表和详情入口都必须同步刷新。
"""


def _governance_matcher() -> CaseGovernanceMatcher:
    return CaseGovernanceMatcher.from_raw(
        reuse_risks=["旧链路返回风险"],
        soft_constraints=["避免重复提交"],
    )


def _case(
    case_id: str,
    *,
    rule: str,
    description: str,
    steps: list[str],
    expected_result: str,
    test_input: str = "教师A、班级1、周一第1节",
    priority: str = "P1",
) -> dict[str, Any]:
    return {
        "id": case_id,
        "test_module": "课程排课",
        "description": f"{rule} {description}",
        "preconditions": "教务老师已登录，并拥有课程排课权限",
        "steps": steps,
        "test_input": test_input,
        "expected_result": expected_result,
        "priority": priority,
    }


def _rerank(
    cases: list[dict[str, Any]],
    *,
    max_per_rule: int = 1,
    expected_count: int = 6,
    generation_profile: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    matcher = _governance_matcher()
    coverage_context = analyze_coverage(REQUIREMENT, cases)
    selected, trace = rerank_and_cap_by_rule(
        cases,
        expected_count=expected_count,
        deduplicate_test_cases_fn=deduplicate_test_cases,
        hits_reuse_risk_fn=matcher.hits_reuse_risk,
        hits_soft_constraint_fn=matcher.hits_soft_constraint,
        max_per_rule=max_per_rule,
        include_trace=True,
        coverage_context=coverage_context,
        rule_diagnostics=_rule_diagnostics_payload(coverage_context),
        generation_profile=generation_profile or {"coverage_mode": "standard_regression"},
    )
    return selected, trace


def _decision_for_case(trace: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    return dict((trace.get("decisions") or {}).get(case_signature(case)) or {})


def test_rule_cap_records_drop_rule_cap_for_extra_rule_cases() -> None:
    cases = [
        _case(
            "TC-101",
            rule="REQ-101",
            description="保存排课成功后刷新课程表",
            steps=["打开排课页", "选择教师A和教室101", "点击保存"],
            expected_result="系统保存排课并在课程表展示最新记录",
        ),
        _case(
            "TC-102",
            rule="REQ-101",
            description="保存排课时教室时间冲突",
            steps=["打开排课页", "选择教师B和已占用教室101", "点击保存"],
            expected_result="系统拦截保存，并提示教室时间冲突",
        ),
        _case(
            "TC-103",
            rule="REQ-101",
            description="保存排课时教师时间冲突",
            steps=["打开排课页", "选择已排课教师A", "点击保存"],
            expected_result="系统拦截保存，并提示教师时间冲突",
        ),
    ]

    selected, trace = _rerank(cases, max_per_rule=1)

    assert len(selected) == 1
    assert trace["summary"]["drop_rule_cap_count"] == 2
    assert trace["summary"]["rule_cap_drop_count"] == 2
    assert sorted(
        decision["drop_reason"]
        for decision in (trace.get("decisions") or {}).values()
        if not decision.get("selected")
    ) == ["drop_rule_cap", "drop_rule_cap"]
    assert all(_decision_for_case(trace, case)["rule_keys"] == ["REQ-101"] for case in cases)


def test_semantic_duplicate_is_dropped_after_structural_dedup_keeps_variants() -> None:
    duplicate_a = _case(
        "TC-201",
        rule="REQ-102",
        description="编辑排课草稿后保存并规避旧链路返回风险",
        steps=["打开草稿编辑页", "调整教师和教室", "点击保存", "校验旧链路返回风险未覆盖新草稿"],
        test_input="教师A、班级1、周一第1节",
        expected_result="系统保存最新草稿，并避免重复提交导致旧链路返回风险",
    )
    duplicate_b = _case(
        "TC-202",
        rule="REQ-102",
        description="编辑排课草稿后保存并规避旧链路返回风险",
        steps=["打开草稿编辑页", "调整教师和教室", "点击保存", "校验旧链路返回风险未覆盖新草稿"],
        test_input="教师B、班级2、周二第2节",
        expected_result="系统保存最新草稿，并避免重复提交导致旧链路返回风险",
    )

    selected, trace = _rerank(
        [duplicate_a, duplicate_b],
        max_per_rule=3,
        generation_profile={"coverage_mode": "standard_regression"},
    )

    dropped_decisions = [
        decision
        for decision in (trace.get("decisions") or {}).values()
        if decision.get("drop_reason") == "drop_semantic_duplicate"
    ]
    assert [case["id"] for case in selected] == ["TC-201"]
    assert trace["summary"]["dedup_drop_count"] == 0
    assert trace["summary"]["semantic_duplicate_drop_count"] == 1
    assert len(dropped_decisions) == 1
    assert dropped_decisions[0]["is_semantic_duplicate"] is True
    assert dropped_decisions[0]["duplicate_of_case_id"] == "TC-201"


def test_rule_cap_keeps_representative_when_pool_only_contains_capped_rule() -> None:
    cases = [
        _case(
            "TC-301",
            rule="REQ-101",
            description="保存排课主流程",
            steps=["打开排课页", "填写教师、班级、教室和时间段", "点击保存"],
            expected_result="系统保存成功，并展示排课详情",
        ),
        _case(
            "TC-302",
            rule="REQ-101",
            description="保存排课后返回列表",
            steps=["打开排课页", "填写教师、班级、教室和时间段", "点击保存后返回列表"],
            expected_result="列表展示刚保存的排课记录",
        ),
    ]

    selected, trace = _rerank(cases, max_per_rule=1)

    assert selected
    assert trace["summary"]["selected_count"] == 1
    assert trace["summary"]["drop_rule_cap_count"] == 1
    assert any(decision.get("drop_reason") == "retained" for decision in trace["decisions"].values())
    assert any(decision.get("drop_reason") == "drop_rule_cap" for decision in trace["decisions"].values())


def test_include_trace_summary_fields_explain_dedup_cap_and_selection_counts() -> None:
    duplicate = _case(
        "TC-401",
        rule="REQ-101",
        description="保存排课主流程",
        steps=["打开排课页", "填写教师、班级、教室和时间段", "点击保存"],
        expected_result="系统保存成功，并展示排课详情",
    )
    cases = [
        duplicate,
        {**duplicate, "id": "TC-401-DUP"},
        _case(
            "TC-402",
            rule="REQ-101",
            description="保存排课时教室冲突",
            steps=["打开排课页", "选择已占用教室101", "点击保存"],
            expected_result="系统拦截保存，并提示教室冲突",
        ),
    ]

    selected, trace = _rerank(cases, max_per_rule=1)
    summary = trace["summary"]

    assert summary["input_count"] == 3
    assert summary["dedup_input_count"] == 2
    assert summary["dedup_drop_count"] == 1
    assert summary["selected_count"] == len(selected) == 1
    assert summary["dropped_count"] == 1
    assert summary["drop_rule_cap_count"] == 1
    assert summary["input_count"] == summary["dedup_input_count"] + summary["dedup_drop_count"]
    assert summary["dedup_input_count"] == summary["selected_count"] + summary["dropped_count"]
    assert len(trace["ordered_signatures"]) == summary["dedup_input_count"]
    assert len(trace["dedup_dropped_signatures"]) == 1
    for decision in trace["decisions"].values():
        assert decision["rule_keys"] == ["REQ-101"]
        assert isinstance(decision["gate_sort_key"], list)
        assert isinstance(decision["retained_rank_within_rule"], int)
