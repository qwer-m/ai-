from __future__ import annotations

from typing import Any

from .feedback_control_priority_signals import _is_preferred_signal_sample


def _apply_signal_quota(
    candidates: list[dict[str, Any]],
    *,
    retrieval_meta: dict[str, Any],
    max_retrieval_top_k: int,
    min_positive_top_k: int,
    max_negative_top_k: int,
) -> list[dict[str, Any]]:
    if not candidates:
        retrieval_meta["retrieval_signal_quota_applied"] = True
        retrieval_meta["retrieval_selected_positive_count"] = 0
        retrieval_meta["retrieval_selected_negative_count"] = 0
        retrieval_meta["retrieval_after_quota_merge_positive_count"] = 0
        retrieval_meta["retrieval_after_quota_merge_negative_count"] = 0
        retrieval_meta["retrieval_final_selected_positive_count"] = 0
        retrieval_meta["retrieval_final_selected_negative_count"] = 0
        retrieval_meta["retrieval_signal_quota_relaxed"] = False
        return []

    max_retrieval_top_k = max(1, int(max_retrieval_top_k))
    min_positive_top_k = max(0, int(min_positive_top_k))
    max_negative_top_k = max(0, int(max_negative_top_k))
    target_total = min(int(max_retrieval_top_k), int(len(candidates)))
    min_positive_quota = min(int(min_positive_top_k), int(target_total))
    max_negative_quota = min(int(max_negative_top_k), int(target_total))

    positive_indices = [idx for idx, item in enumerate(candidates) if _is_preferred_signal_sample(item)]
    effective_positive_quota = min(int(min_positive_quota), int(len(positive_indices)))
    relaxed_negative_cap = bool(effective_positive_quota < min_positive_quota)
    effective_negative_cap = int(target_total) if relaxed_negative_cap else int(max_negative_quota)

    selected_indices: list[int] = list(positive_indices[:effective_positive_quota])
    selected_index_set: set[int] = set(selected_indices)
    negative_selected_count = 0
    for idx in selected_indices:
        if not _is_preferred_signal_sample(candidates[idx]):
            negative_selected_count += 1

    for idx, item in enumerate(candidates):
        if len(selected_indices) >= target_total:
            break
        if idx in selected_index_set:
            continue
        is_positive = _is_preferred_signal_sample(item)
        if (not is_positive) and negative_selected_count >= effective_negative_cap:
            continue
        selected_indices.append(idx)
        selected_index_set.add(idx)
        if not is_positive:
            negative_selected_count += 1

    if len(selected_indices) < target_total:
        for idx, _ in enumerate(candidates):
            if len(selected_indices) >= target_total:
                break
            if idx in selected_index_set:
                continue
            selected_indices.append(idx)
            selected_index_set.add(idx)

    selected = [candidates[idx] for idx in selected_indices]
    selected_positive_count = sum(1 for item in selected if _is_preferred_signal_sample(item))
    selected_negative_count = int(len(selected) - selected_positive_count)
    retrieval_meta["retrieval_signal_quota_applied"] = True
    retrieval_meta["retrieval_selected_positive_count"] = int(selected_positive_count)
    retrieval_meta["retrieval_selected_negative_count"] = int(selected_negative_count)
    retrieval_meta["retrieval_after_quota_merge_positive_count"] = int(selected_positive_count)
    retrieval_meta["retrieval_after_quota_merge_negative_count"] = int(selected_negative_count)
    retrieval_meta["retrieval_final_selected_positive_count"] = int(selected_positive_count)
    retrieval_meta["retrieval_final_selected_negative_count"] = int(selected_negative_count)
    retrieval_meta["retrieval_signal_quota_relaxed"] = bool(relaxed_negative_cap)
    retrieval_meta["retrieval_positive_min_quota"] = int(min_positive_quota)
    retrieval_meta["retrieval_negative_max_quota"] = int(max_negative_quota)
    return selected


__all__ = ["_apply_signal_quota"]
