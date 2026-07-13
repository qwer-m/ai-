from __future__ import annotations

from modules.test_generation_components.control.fact_profile_activation import normalize_fact_profile


def test_normalize_fact_profile_keeps_negative_confirmed_fact_out_of_forbidden_bucket() -> None:
    profile = normalize_fact_profile(
        {
            "confirmed_facts": [
                "操作按钮：未开始显示学习；已完成后不显示再次提交入口",
            ],
        }
    )

    assert profile["confirmed_facts"] == ["操作按钮：未开始显示学习；已完成后不显示再次提交入口"]
    assert profile["forbidden_facts"] == []


def test_normalize_fact_profile_removes_forbidden_fact_that_duplicates_confirmed_fact() -> None:
    profile = normalize_fact_profile(
        {
            "confirmed_facts": ["已完成后不可重复打分"],
            "forbidden_facts": ["已完成后不可重复打分"],
        }
    )

    assert profile["confirmed_facts"] == ["已完成后不可重复打分"]
    assert profile["forbidden_facts"] == []


def test_normalize_fact_profile_keeps_explicit_non_overlapping_forbidden_fact() -> None:
    profile = normalize_fact_profile(
        {
            "confirmed_facts": ["保存成功后列表展示新记录"],
            "forbidden_facts": ["保存失败后仍展示成功提示"],
        }
    )

    assert profile["forbidden_facts"] == ["保存失败后仍展示成功提示"]
