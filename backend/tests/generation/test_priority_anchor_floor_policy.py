from __future__ import annotations

from modules.test_generation_components.postprocess.priority_anchor_floor_policy import (
    MainPathAnchorPolicy,
)


def _policy(
    *,
    configured_family: str = "",
    has_core_signal: bool = False,
    has_low_value_signal: bool = False,
) -> MainPathAnchorPolicy:
    return MainPathAnchorPolicy(
        configured_anchor_family_fn=lambda _text: configured_family,
        has_core_signal_fn=lambda _text: has_core_signal,
        has_low_value_signal_fn=lambda _text: has_low_value_signal,
    )


def test_policy_demotes_non_blocking_detail_without_business_anchor() -> None:
    policy = _policy(has_low_value_signal=True)

    assert policy.should_demote_non_blocking("star rating max 20") is True
    assert policy.should_demote_non_blocking("generate correction result") is False


def test_policy_primary_rank_promotes_configured_critical_family() -> None:
    policy = _policy(configured_family="permission")

    rank = policy.primary_rank(
        item={},
        index=2,
        text="member all courses",
        normalized_priority="P1",
    )

    assert rank is not None
    assert rank[0] >= 70
    assert rank[1] == -2
    assert rank[2] == "permission"


def test_policy_fallback_rank_requires_strong_anchor_in_full_mode() -> None:
    policy = _policy()

    assert (
        policy.fallback_rank(
            item={},
            index=1,
            text="ordinary supplemental validation",
            normalized_priority="P2",
            mode="full_functional_regression",
        )
        is None
    )
