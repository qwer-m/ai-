from __future__ import annotations

from collections import Counter
from typing import Any, Callable

from .streaming_case_keys import candidate_identity_key, case_signature


def finalize_global_review_selection(
    cases: list[dict[str, Any]],
    *,
    deduplicate_test_cases_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    include_trace: bool = False,
) -> Any:
    """保留模型对整套候选的选择，仅执行确定性去重和诊断。"""
    candidates = [dict(item) for item in cases if isinstance(item, dict)]
    original_candidate_keys = [candidate_identity_key(item) for item in candidates]
    selected = [dict(item) for item in deduplicate_test_cases_fn(candidates) if isinstance(item, dict)]
    selected_candidate_keys = [candidate_identity_key(item) for item in selected]

    if not include_trace:
        return selected

    decisions = {
        candidate_key: {
            "candidate_key": candidate_key,
            "signature": candidate_key,
            "exact_signature": case_signature(item),
            "rank": index,
            "selected": True,
            "drop_reason": "retained_global_selection",
            "drop_reason_detail": "retained_global_selection",
            "rule_cap_applied": False,
            "is_semantic_duplicate": False,
        }
        for index, (candidate_key, item) in enumerate(
            zip(selected_candidate_keys, selected),
            start=1,
        )
    }
    original_counter = Counter(original_candidate_keys)
    selected_counter = Counter(selected_candidate_keys)
    dedup_dropped_candidate_keys = sorted(
        candidate_key
        for candidate_key, count in original_counter.items()
        if candidate_key and count > selected_counter.get(candidate_key, 0)
    )
    return selected, {
        "decisions": decisions,
        "selected_candidate_keys": selected_candidate_keys,
        "ordered_candidate_keys": selected_candidate_keys,
        "dedup_dropped_candidate_keys": dedup_dropped_candidate_keys,
        # 旧字段保留协议兼容，但值已统一为不会折叠物理候选的 candidate key。
        "selected_signatures": selected_candidate_keys,
        "ordered_signatures": selected_candidate_keys,
        "dedup_dropped_signatures": dedup_dropped_candidate_keys,
        "selected_exact_signatures": [case_signature(item) for item in selected],
        "summary": {
            "selection_policy": "global_review_then_deterministic_dedup",
            "input_count": len(original_candidate_keys),
            "dedup_input_count": len(selected_candidate_keys),
            "selected_count": len(selected),
            "dropped_count": 0,
            "dedup_drop_count": max(0, len(original_candidate_keys) - len(selected_candidate_keys)),
            "drop_rule_cap_count": 0,
            "rule_cap_drop_count": 0,
            "drop_no_new_signal_count": 0,
            "semantic_duplicate_drop_count": 0,
            "ui_like_drop_count": 0,
        },
    }


__all__ = ["finalize_global_review_selection"]
