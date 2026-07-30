from __future__ import annotations

from typing import Any

from .streaming_postprocess_utils import _dict_case_items


def build_review_selection_constraints(
    cases: list[dict[str, Any]],
    *,
    reference_count: int,
    generation_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """根据当前生成目标形成评审窗口，不再从候选分布反推固定配额。"""
    candidate_cases = _dict_case_items(cases)
    total = int(len(candidate_cases))
    if total <= 0:
        return {
            "target_min_count": 1,
            "target_max_count": 1,
            "priority_min": {},
            "scenario_min": {},
            "domain_min": {},
        }

    profile = dict(generation_profile or {})
    target_range = profile.get("target_case_range")
    target_range = dict(target_range) if isinstance(target_range, dict) else {}
    try:
        requested_min = int(target_range.get("min") or 0)
    except (TypeError, ValueError):
        requested_min = 0
    try:
        requested_max = int(target_range.get("max") or 0)
    except (TypeError, ValueError):
        requested_max = 0
    try:
        explicit_expected_floor = max(
            0,
            int(profile.get("explicit_expected_count_floor") or 0),
        )
    except (TypeError, ValueError):
        explicit_expected_floor = 0

    # 没有结构化目标时保留全部候选，让全局评审决定是否存在真实冗余。
    if requested_min <= 0 and requested_max <= 0:
        requested_min = max(1, int(reference_count or total))
        requested_max = requested_min
    if explicit_expected_floor > 0:
        requested_min = max(requested_min, explicit_expected_floor)
        requested_max = max(requested_max, explicit_expected_floor)
    target_min = min(total, max(1, requested_min))
    target_max = min(total, max(target_min, requested_max or requested_min))
    target_min = min(target_min, target_max)

    return {
        "target_min_count": int(target_min),
        "target_max_count": int(target_max),
        "priority_min": {},
        "scenario_min": {},
        "domain_min": {},
        "constraint_source": (
            "explicit_expected_count"
            if explicit_expected_floor > 0
            else "generation_target_case_range"
            if target_range
            else "reference_target"
        ),
        "explicit_expected_count_floor": int(explicit_expected_floor),
    }

__all__ = [
    "build_review_selection_constraints",
]
