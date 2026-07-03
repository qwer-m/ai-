from __future__ import annotations

from typing import Any


_STOP_REASON_LABELS = {
    "coverage_satisfied": "coverage_satisfied（核心规则覆盖已满足）",
    "stopped_due_to_diminishing_returns": "stopped_due_to_diminishing_returns（继续生成收益递减）",
    "optimal_case_set_reached": "optimal_case_set_reached（当前为最优测试用例集合）",
}


def render_stop_reason_text(stop_reasons: list[Any]) -> str:
    labels: list[str] = []
    for reason in stop_reasons:
        key = str(reason or "").strip()
        if not key:
            continue
        label = _STOP_REASON_LABELS.get(key, key)
        if label in labels:
            continue
        labels.append(label)
    return "；".join(labels)


__all__ = [
    "render_stop_reason_text",
]
