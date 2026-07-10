from __future__ import annotations

from typing import Any, Callable


_EXECUTION_SIDE_SUITE_ORDER = (
    ("permission", "permission/security"),
    ("exception", "exception/recovery"),
    ("boundary", "boundary/state rollback"),
    ("independent_functional", "independent functional"),
    ("display", "UI/display"),
)
_GROUP_RANK = {
    "main_smoke": 0,
    "permission": 1,
    "exception": 2,
    "boundary": 3,
    "independent_functional": 4,
    "independent": 4,
    "display": 5,
}
_PRIORITY_RANK = {
    "P0": 0,
    "P1": 1,
    "P2": 2,
}


def _text(value: Any) -> str:
    return str(value or "")


def _default_case_execution_group(case: dict[str, Any]) -> str:
    return str(case.get("execution_group") or "").strip()


def _default_clip_text(value: Any, limit: int) -> str:
    return str(value or "")[: max(0, int(limit))]


def _dict_case_copies(items: list[Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in items if isinstance(item, dict)]


def _case_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("case_id") or "")


def _orchestration_group(group: Any) -> str:
    value = _text(group).strip()
    return value or "independent_functional"


def _orchestration_rank(group: Any) -> int:
    return _GROUP_RANK.get(_orchestration_group(group), 9)


def _suite_id(group: str) -> str:
    return "main_smoke_chain" if group == "main_smoke" else f"{group}_suite"


def execution_group_order_rank(group: Any) -> int:
    return _orchestration_rank(group)


def execution_side_suite_order_labels() -> list[str]:
    return [label for _group, label in _EXECUTION_SIDE_SUITE_ORDER]


def execution_side_suite_order_text(separator: str = " -> ") -> str:
    return str(separator).join(execution_side_suite_order_labels())


def build_execution_orchestration_plan(
    cases: list[Any],
    *,
    case_execution_group_fn: Callable[[dict[str, Any]], str] | None = None,
    case_id_fn: Callable[[dict[str, Any]], str] | None = None,
) -> dict[str, Any]:
    execution_group = case_execution_group_fn or _default_case_execution_group
    resolve_case_id = case_id_fn or _case_id
    dict_cases = _dict_case_copies(list(cases or []))
    main_chain: list[dict[str, Any]] = []
    suites_by_group: dict[str, dict[str, Any]] = {}

    for index, item in enumerate(dict_cases, start=1):
        group = _orchestration_group(execution_group(item))
        case_id = resolve_case_id(item)
        if group == "main_smoke":
            main_chain.append(
                {
                    "case_id": case_id,
                    "execution_sequence": int(item.get("execution_sequence") or index),
                    "main_chain_step": int(item.get("main_chain_step") or len(main_chain) + 1),
                    "stage": str(item.get("main_chain_stage") or ""),
                    "stage_kind": str(item.get("main_chain_stage_kind") or ""),
                    "source_state": str(item.get("source_state") or ""),
                    "target_state": str(item.get("target_state") or ""),
                    "role": str(item.get("role") or ""),
                    "depends_on": list(item.get("depends_on") or []),
                }
            )
            continue

        suite = suites_by_group.setdefault(
            group,
            {
                "suite_id": _suite_id(group),
                "execution_group": group,
                "rank": _orchestration_rank(group),
                "case_ids": [],
                "case_count": 0,
            },
        )
        suite["case_ids"].append(case_id)
        suite["case_count"] = int(suite["case_count"]) + 1

    side_suites = sorted(
        suites_by_group.values(),
        key=lambda item: (int(item.get("rank") or 9), str(item.get("execution_group") or "")),
    )
    execution_group_order = ["main_smoke"] if main_chain else []
    execution_group_order.extend(str(item.get("execution_group") or "") for item in side_suites)

    return {
        "plan_first": True,
        "planned_case_count": int(len(dict_cases)),
        "main_chain_case_count": int(len(main_chain)),
        "side_suite_count": int(len(side_suites)),
        "execution_group_order": execution_group_order,
        "main_chain": main_chain,
        "side_suites": side_suites,
    }


def order_independent_cases_by_execution_plan(
    independent_cases: list[Any],
    *,
    case_execution_group_fn: Callable[[dict[str, Any]], str] | None = None,
) -> list[dict[str, Any]]:
    execution_group = case_execution_group_fn or _default_case_execution_group
    cases = _dict_case_copies(list(independent_cases or []))
    return [
        dict(item)
        for _rank, _index, item in sorted(
            (
                (_orchestration_rank(execution_group(item)), index, item)
                for index, item in enumerate(cases)
            ),
            key=lambda row: (row[0], row[1]),
        )
    ]


def apply_existing_execution_group_ordering(
    cases: list[Any],
    *,
    start_id: int = 1,
    renumber_ids: bool = True,
    case_execution_group_fn: Callable[[dict[str, Any]], str] | None = None,
) -> list[dict[str, Any]]:
    execution_group = case_execution_group_fn or _default_case_execution_group
    dict_cases = _dict_case_copies(list(cases or []))
    if not dict_cases:
        return []
    non_empty_groups = [
        _orchestration_group(execution_group(item))
        for item in dict_cases
        if str(execution_group(item) or "").strip()
    ]
    if not non_empty_groups:
        return dict_cases
    if any(group not in _GROUP_RANK for group in non_empty_groups):
        return dict_cases

    def _sequence(item: dict[str, Any], fallback: int) -> int:
        try:
            value = int(item.get("execution_sequence") or 0)
        except (TypeError, ValueError):
            value = 0
        return value if value > 0 else fallback

    ordered = [
        dict(item)
        for _rank, _sequence_value, _index, item in sorted(
            (
                (
                    _orchestration_rank(execution_group(item)),
                    _sequence(item, index),
                    index,
                    item,
                )
                for index, item in enumerate(dict_cases, start=1)
            ),
            key=lambda row: (row[0], row[1], row[2]),
        )
    ]
    safe_start = max(1, int(start_id or 1))
    for index, item in enumerate(ordered, start=1):
        if renumber_ids:
            item["id"] = f"TC-{safe_start + index - 1:03d}"
        item["execution_sequence"] = int(index)
    return ordered


def order_execution_plan_cases(
    candidate_cases: list[dict[str, Any]],
    selected_by_stage: list[tuple[str, str, dict[str, Any]]],
    selected_signatures: set[str],
    signature_fn: Callable[[dict[str, Any]], str],
    infer_group_fn: Callable[..., str],
    case_priority_fn: Callable[[dict[str, Any]], str],
    case_text_field_fn: Callable[[dict[str, Any], str], str],
) -> list[dict[str, Any]]:
    main_chain_cases = [
        dict(item)
        for _stage_key, _stage_label, item in selected_by_stage
    ]
    main_chain_signatures = {
        signature_fn(item)
        for _stage_key, _stage_label, item in selected_by_stage
    }
    selected_signature_set = set(selected_signatures)

    remaining_cases: list[dict[str, Any]] = []
    for item in candidate_cases:
        signature = signature_fn(item)
        if signature in main_chain_signatures or signature in selected_signature_set:
            continue
        remaining_cases.append(dict(item))

    remaining_cases.sort(
        key=lambda item: (
            _orchestration_rank(infer_group_fn(item, in_main_chain=False)),
            _PRIORITY_RANK.get(_text(case_priority_fn(item)), 2),
            _text(case_text_field_fn(item, "test_module")),
            _text(case_text_field_fn(item, "description")),
        )
    )
    return [*main_chain_cases, *remaining_cases]


def _renumber_execution_sequence(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, item in enumerate(cases, start=1):
        updated = dict(item)
        updated["execution_sequence"] = int(index)
        output.append(updated)
    return output


def assign_presentation_order(
    execution_ordered_cases: list[dict[str, Any]],
    *,
    presentation_ordered_cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    order_by_id: dict[str, int] = {}
    for index, item in enumerate(_dict_case_copies(presentation_ordered_cases), start=1):
        case_id = _case_id(item)
        if not case_id or case_id in order_by_id:
            continue
        order_by_id[case_id] = int(index)

    output: list[dict[str, Any]] = []
    fallback_start = len(order_by_id) + 1
    for fallback_index, item in enumerate(_dict_case_copies(execution_ordered_cases), start=fallback_start):
        updated = dict(item)
        case_id = _case_id(updated)
        updated["presentation_order"] = int(order_by_id.get(case_id) or fallback_index)
        output.append(updated)
    return output


_assign_presentation_order = assign_presentation_order


def apply_final_independent_case_ordering(
    parsed_result: list[Any],
    *,
    requirement: str,
    start_id: int,
    flow_project_profile: dict[str, Any] | None,
    flow_profile_with_scenario_policy_fn: Callable[..., dict[str, Any]],
    govern_cases_by_flow_structure_fn: Callable[..., tuple[list[Any], dict[str, Any]]],
    case_execution_group_fn: Callable[[dict[str, Any]], str] | None = None,
    clip_text_fn: Callable[[Any, int], str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    original_cases = _dict_case_copies(parsed_result)
    execution_group = case_execution_group_fn or _default_case_execution_group
    clip_text = clip_text_fn or _default_clip_text

    try:
        main_chain_cases = [
            dict(item)
            for item in original_cases
            if execution_group(item) == "main_smoke"
        ]
        independent_cases = [
            dict(item)
            for item in original_cases
            if execution_group(item) != "main_smoke"
        ]
        final_order_profile = flow_profile_with_scenario_policy_fn(
            flow_project_profile,
            disable_scenario_pruning=True,
            intent_duplicate_cap=1_000_000,
            final_order_only=True,
        )
        ordered_independent, final_order_flow_governance_summary = govern_cases_by_flow_structure_fn(
            requirement,
            independent_cases,
            start_id=start_id + len(main_chain_cases),
            renumber_ids=False,
            max_per_scenario=2,
            project_profile=final_order_profile,
        )
        ordered_independent_cases = _dict_case_copies(list(ordered_independent or []))
        if len(ordered_independent_cases) == len(independent_cases):
            planned_independent_cases = order_independent_cases_by_execution_plan(
                ordered_independent_cases,
                case_execution_group_fn=execution_group,
            )
            presentation_ordered_cases = [*main_chain_cases, *ordered_independent_cases]
            ordered_cases = _renumber_execution_sequence([*main_chain_cases, *planned_independent_cases])
            ordered_cases = _assign_presentation_order(
                ordered_cases,
                presentation_ordered_cases=presentation_ordered_cases,
            )
            summary = dict(final_order_flow_governance_summary or {})
            summary["execution_orchestration_plan"] = build_execution_orchestration_plan(
                ordered_cases,
                case_execution_group_fn=execution_group,
            )
            summary["execution_group_order"] = list(
                summary["execution_orchestration_plan"].get("execution_group_order") or []
            )
            return ordered_cases, summary
        return original_cases, dict(final_order_flow_governance_summary or {})
    except Exception as exc:
        return original_cases, {
            "applied": False,
            "reason": "exception",
            "exception": clip_text(exc, 200),
        }
