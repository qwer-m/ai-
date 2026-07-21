from __future__ import annotations

from modules.test_generation_components.postprocess.streaming_flow_conflicts import (
    filter_cases_conflicting_with_confirmed_flow_facts,
)


def test_flow_conflict_filter_returns_copy_when_no_conflict_context_exists() -> None:
    cases = [
        {"id": "normal", "test_module": "记录", "description": "进入记录详情", "expected_result": "页面打开"},
        "bad",
    ]

    kept, dropped = filter_cases_conflicting_with_confirmed_flow_facts(
        cases,  # type: ignore[arg-type]
        requirement="记录详情正常打开",
        kb_context="",
        fact_profile={},
    )

    assert dropped == 0
    assert kept == [{"id": "normal", "test_module": "记录", "description": "进入记录详情", "expected_result": "页面打开"}]
    assert kept[0] is not cases[0]


def test_flow_conflict_filter_drops_legacy_blocked_stage_when_nonlinear_context_confirmed() -> None:
    cases = [
        {
            "id": "legacy",
            "test_module": "审批阶段",
            "description": "复核阶段完成前一阶段才可以解锁",
            "expected_result": "上一阶段完成前不可进入",
        },
        {
            "id": "valid",
            "test_module": "审批阶段",
            "description": "复核阶段任意进入",
            "expected_result": "无需前置即可进入",
        },
    ]

    kept, dropped = filter_cases_conflicting_with_confirmed_flow_facts(
        cases,
        requirement="审批阶段均可进入，无需前置",
        kb_context="",
        fact_profile={"confirmed": ["non-linear workflow stage"]},
    )

    assert dropped == 1
    assert [case["id"] for case in kept] == ["valid"]


def test_flow_conflict_filter_preserves_compatible_legacy_linear_unlock_cases() -> None:
    cases = [
        {
            "id": "compatible",
            "test_module": "审批阶段",
            "description": "旧配置开启线性解锁时，仅第一个环节可进入",
            "expected_result": "legacy_unlock_mode 下其余环节未解锁",
        }
    ]

    kept, dropped = filter_cases_conflicting_with_confirmed_flow_facts(
        cases,
        requirement="审批阶段均可进入",
        kb_context="",
        fact_profile={},
    )

    assert dropped == 0
    assert [case["id"] for case in kept] == ["compatible"]


def test_flow_conflict_filter_can_trigger_from_case_level_nonlinear_signal() -> None:
    cases = [
        {
            "id": "positive",
            "test_module": "workflow stage",
            "description": "any stage enterable with no prerequisite",
            "expected_result": "student can enter any stage",
        },
        {
            "id": "obsolete",
            "test_module": "workflow stage",
            "description": "only first stage is available",
            "expected_result": "previous stage must be completed",
        },
    ]

    kept, dropped = filter_cases_conflicting_with_confirmed_flow_facts(
        cases,
        requirement="",
        kb_context="",
        fact_profile={},
    )

    assert dropped == 1
    assert [case["id"] for case in kept] == ["positive"]


def test_flow_conflict_filter_accepts_alias_case_fields() -> None:
    cases = [
        {
            "caseId": "positive",
            "module": "workflow stage",
            "title": "any stage enterable with no prerequisite",
            "expectedResult": "student can enter any stage",
            "testSteps": ["open any workflow stage"],
        },
        {
            "caseId": "obsolete",
            "module": "workflow stage",
            "title": "only first stage is available",
            "expectedResult": "previous stage must be completed",
            "testSteps": ["open locked workflow stage"],
        },
    ]

    kept, dropped = filter_cases_conflicting_with_confirmed_flow_facts(
        cases,
        requirement="",
        kb_context="",
        fact_profile={},
    )

    assert dropped == 1
    assert [case["caseId"] for case in kept] == ["positive"]
