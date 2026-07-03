from __future__ import annotations

from typing import Any

from ..postprocess.case_access import case_id as case_access_id, case_priority


_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2}


def _case_id(case: dict[str, Any]) -> str:
    return case_access_id(case)


def _matched_flows(case: dict[str, Any]) -> set[str]:
    mapper_hits = case.get("mapper_hits")
    if isinstance(mapper_hits, dict):
        return {str(key) for key in mapper_hits.keys() if str(key).strip()}
    return set()


def _coverage_priority_sort_key(case: dict[str, Any], flow_order: dict[str, int]) -> tuple[int, int, str]:
    source_flow = str(case.get("source_flow_key") or "")
    priority = case_priority(case, prefer_final=True, default="P2")
    return (
        int(flow_order.get(source_flow, 9999)),
        int(_PRIORITY_RANK.get(priority, 9)),
        _case_id(case),
    )


def select_merged_preview_cases(
    existing_cases: list[dict[str, Any]],
    accepted_backfill_cases: list[dict[str, Any]],
    required_flow_keys: list[str],
    max_cases: int = 18,
    min_cases: int = 12,
) -> dict[str, Any]:
    max_cases = max(1, int(max_cases or 18))
    min_cases = max(1, int(min_cases or 12))
    existing_items = [dict(item) for item in (existing_cases or []) if isinstance(item, dict)]
    accepted_items = [dict(item) for item in (accepted_backfill_cases or []) if isinstance(item, dict)]
    flow_order = {
        str(flow_key): idx
        for idx, flow_key in enumerate(required_flow_keys or [])
        if str(flow_key).strip()
    }

    selected_backfill: list[dict[str, Any]] = []
    selected_backfill_ids: set[str] = set()

    def _add_backfill(case: dict[str, Any]) -> bool:
        cid = _case_id(case)
        if not cid or cid in selected_backfill_ids:
            return False
        selected_backfill.append(case)
        selected_backfill_ids.add(cid)
        return True

    for flow_key in required_flow_keys or []:
        flow_key = str(flow_key or "").strip()
        if not flow_key:
            continue
        exact = [
            case
            for case in accepted_items
            if str(case.get("source_flow_key") or "").strip() == flow_key and flow_key in _matched_flows(case)
        ]
        if exact:
            exact.sort(key=lambda case: _coverage_priority_sort_key(case, flow_order))
            _add_backfill(exact[0])
            continue
        fallback = [case for case in accepted_items if flow_key in _matched_flows(case)]
        if fallback:
            fallback.sort(key=lambda case: _coverage_priority_sort_key(case, flow_order))
            _add_backfill(fallback[0])

    remaining_capacity = max(0, max_cases - len(selected_backfill))
    retained_primary = existing_items[:remaining_capacity]
    remaining_capacity = max(0, remaining_capacity - len(retained_primary))

    extra_backfills = [case for case in accepted_items if _case_id(case) not in selected_backfill_ids]
    extra_backfills.sort(key=lambda case: _coverage_priority_sort_key(case, flow_order))
    retained_extra_backfills = extra_backfills[:remaining_capacity]
    merged_backfills = list(selected_backfill) + list(retained_extra_backfills)

    merged_cases = (merged_backfills + list(retained_primary))[:max_cases]
    merged_case_ids = {_case_id(case) for case in merged_cases}
    retained_backfill_case_ids = [
        _case_id(case)
        for case in merged_cases
        if _case_id(case).startswith("BF-")
    ]

    primary_ids = [_case_id(case) for case in existing_items]
    retained_primary_case_ids = [cid for cid in primary_ids if cid in merged_case_ids]
    trimmed_primary_case_ids = [cid for cid in primary_ids if cid and cid not in merged_case_ids]

    accepted_backfill_ids = [_case_id(case) for case in accepted_items if _case_id(case)]
    dropped_backfill_due_to_limit_case_ids = [
        cid for cid in accepted_backfill_ids if cid not in set(retained_backfill_case_ids)
    ]

    return {
        "merged_preview_cases": merged_cases,
        "accepted_for_preview_count": int(len(retained_backfill_case_ids)),
        "primary_retained_count": int(len(retained_primary_case_ids)),
        "primary_trimmed_count": int(len(trimmed_primary_case_ids)),
        "backfill_retained_count": int(len(retained_backfill_case_ids)),
        "backfill_trimmed_count": int(len(dropped_backfill_due_to_limit_case_ids)),
        "coverage_first_selection_applied": True,
        "trimmed_primary_case_ids": trimmed_primary_case_ids,
        "retained_backfill_case_ids": retained_backfill_case_ids,
        "dropped_backfill_due_to_limit_case_ids": dropped_backfill_due_to_limit_case_ids,
        "selection_target_min": int(min_cases),
        "selection_target_max": int(max_cases),
    }
