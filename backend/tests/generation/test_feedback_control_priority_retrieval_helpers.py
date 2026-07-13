from __future__ import annotations

from modules.test_generation_components.control.feedback_control_priority_quota import (
    _apply_signal_quota as _apply_signal_quota_direct,
)
from modules.test_generation_components.control.feedback_control_priority_retrieval import (
    _apply_signal_quota,
    _select_priority_pool_samples_by_requirement,
)
from modules.test_generation_components.control.feedback_control_priority_retrieval_meta import (
    build_priority_retrieval_meta,
)
from modules.test_generation_components.control.feedback_control_priority_retrieval_text import (
    _sample_text_for_retrieval,
)


def test_priority_retrieval_text_collects_pattern_and_case_aliases() -> None:
    sample = {
        "patternSummary": "Prefer full payment lifecycle",
        "sourceCaseTitle": "Refund restores course permission",
        "sourceCaseModule": "Course payment",
        "sourceCaseSteps": ["Buy course", {"action": "Refund order"}],
        "sourceCaseExpectedResult": {"status": "permission restored"},
        "businessAssertion": "order and permission states remain consistent",
    }

    text = _sample_text_for_retrieval(sample)

    assert "Prefer full payment lifecycle" in text
    assert "Refund restores course permission" in text
    assert "Buy course Refund order" in text
    assert "permission restored" in text
    assert "order and permission states remain consistent" in text


def test_priority_signal_quota_preserves_wrapper_contract_and_updates_meta() -> None:
    candidates = [
        {"id": "negative-1", "signal_type": "negative"},
        {"id": "positive-1", "signal_type": "positive"},
        {"id": "negative-2", "signal_type": "negative"},
        {"id": "positive-2", "pattern_usage": "prefer"},
    ]
    direct_meta: dict[str, object] = {}
    wrapper_meta: dict[str, object] = {}

    direct = _apply_signal_quota_direct(
        candidates,
        retrieval_meta=direct_meta,
        max_retrieval_top_k=3,
        min_positive_top_k=2,
        max_negative_top_k=1,
    )
    wrapped = _apply_signal_quota(
        candidates,
        retrieval_meta=wrapper_meta,
        max_retrieval_top_k=3,
        min_positive_top_k=2,
        max_negative_top_k=1,
    )

    assert [item["id"] for item in direct] == ["positive-1", "positive-2", "negative-1"]
    assert wrapped == direct
    assert wrapper_meta["retrieval_selected_positive_count"] == 2
    assert wrapper_meta["retrieval_selected_negative_count"] == 1
    assert wrapper_meta["retrieval_signal_quota_applied"] is True


def test_priority_retrieval_meta_helper_matches_empty_selection_contract() -> None:
    meta = build_priority_retrieval_meta(
        requirement_text="payment regression",
        max_retrieval_top_k=5,
        max_cluster_cap=2,
        min_positive_top_k=2,
        max_negative_top_k=1,
        min_pattern_confidence=0.7,
    )
    selected, empty_meta = _select_priority_pool_samples_by_requirement(
        samples=[],
        project_id=1,
        user_id=1,
        requirement_text="payment regression",
        max_retrieval_top_k=5,
        max_cluster_cap=2,
        min_positive_top_k=2,
        max_negative_top_k=1,
        min_pattern_confidence=0.7,
    )

    assert selected == []
    assert empty_meta == meta
    assert empty_meta["retrieval_query_used"] is True
    assert empty_meta["retrieval_top_k"] == 5
    assert empty_meta["retrieval_diversity_cluster_cap"] == 2
