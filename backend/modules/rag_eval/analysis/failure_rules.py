from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FailureThresholds:
    """失败归因阈值配置。"""

    low_rank_cutoff: int = 5
    context_precision_threshold: float = 0.35
    faithfulness_threshold: float = 0.6
    answer_correctness_threshold: float = 0.75


DEFAULT_FAILURE_THRESHOLDS = FailureThresholds()

