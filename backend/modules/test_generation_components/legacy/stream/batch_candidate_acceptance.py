from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class StreamBatchCandidateAcceptance:
    """流式批次在进入计数与覆盖评估前的唯一接纳结果。"""

    cases: list[dict[str, Any]]
    incomplete_rows: list[dict[str, Any]]
    module_contract_summary: dict[str, Any]


def accept_stream_batch_candidates(
    cases: list[dict[str, Any]],
    *,
    limit: int,
    start_id: int,
    project_profile: Any,
    select_complete_generated_cases_fn: Callable[..., tuple[list[dict[str, Any]], list[dict[str, Any]]]],
    is_placeholder_expected_result_fn: Callable[[str], bool],
    enforce_functional_module_contract_fn: Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]],
) -> StreamBatchCandidateAcceptance:
    """统一执行字段完整性、功能模块与交互契约，再生成连续公共编号。"""

    candidates = [dict(item) for item in cases if isinstance(item, dict)]
    complete_cases, incomplete_rows = select_complete_generated_cases_fn(
        candidates,
        # 先校验全部候选，避免前面的冲突用例占满 limit 后挤掉后面的有效用例。
        limit=len(candidates),
        start_id=int(start_id or 1),
        is_placeholder_expected_result_fn=is_placeholder_expected_result_fn,
    )
    contracted_cases, raw_summary = enforce_functional_module_contract_fn(
        complete_cases,
        project_profile=project_profile,
    )
    accepted_limit = max(0, int(limit or 0))
    accepted_cases = [dict(item) for item in contracted_cases[:accepted_limit]]
    for offset, case in enumerate(accepted_cases):
        case["id"] = f"TC-{int(start_id or 1) + offset:03d}"

    summary = dict(raw_summary or {})
    summary.update(
        {
            "candidate_count": int(len(candidates)),
            "schema_accepted_count": int(len(complete_cases)),
            "incomplete_case_count": int(len(incomplete_rows)),
            "module_rejected_case_count": max(
                0,
                int(len(complete_cases)) - int(len(contracted_cases)),
            ),
            "accepted_count": int(len(accepted_cases)),
        }
    )
    return StreamBatchCandidateAcceptance(
        cases=accepted_cases,
        incomplete_rows=[dict(item) for item in incomplete_rows if isinstance(item, dict)],
        module_contract_summary=summary,
    )


__all__ = [
    "StreamBatchCandidateAcceptance",
    "accept_stream_batch_candidates",
]
