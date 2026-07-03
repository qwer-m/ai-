from __future__ import annotations

from modules.testing.test_generation_components.judge import (
    judge_fact_negative,
    judge_fact_rules,
)
from modules.testing.test_generation_components.judge.test_case_judge import (
    _NEGATIVE_MARKERS as judge_case_negative_markers,
    _violates_negative_fact as judge_case_violates_negative_fact,
)


def test_judge_fact_rules_facade_reexports_negative_helpers() -> None:
    assert judge_fact_rules._NEGATIVE_MARKERS is judge_fact_negative._NEGATIVE_MARKERS
    assert judge_fact_rules._violates_negative_fact is judge_fact_negative._violates_negative_fact
    assert judge_case_negative_markers is judge_fact_negative._NEGATIVE_MARKERS
    assert judge_case_violates_negative_fact is judge_fact_negative._violates_negative_fact


def test_violates_negative_fact_when_case_states_forbidden_tail_positively() -> None:
    fact = "Archived records must not appear in active workbook"

    assert judge_fact_negative._violates_negative_fact(
        "Archived records appear in active workbook after data sync",
        fact,
    )


def test_negative_fact_keeps_case_that_negates_the_forbidden_tail() -> None:
    fact = "Archived records must not appear in active workbook"

    assert not judge_fact_negative._violates_negative_fact(
        "Archived records must not appear in active workbook after data sync",
        fact,
    )


def test_temporal_shutdown_fact_only_rejects_positive_access_inside_scope() -> None:
    fact = "活动结束后入口关闭且课程不可进入"

    assert not judge_fact_negative._violates_negative_fact(
        "会员用户点击课程卡片后可进入课程学习页",
        fact,
    )
    assert judge_fact_negative._violates_negative_fact(
        "活动结束后入口仍可点击并进入课程学习页",
        fact,
    )
    assert not judge_fact_negative._violates_negative_fact(
        "活动结束后入口关闭，课程不可进入",
        fact,
    )
