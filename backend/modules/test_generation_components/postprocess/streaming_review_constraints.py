from __future__ import annotations

from typing import Any, Callable

from .case_access import case_priority
from .streaming_case_keys import case_signature, review_case_id
from .streaming_postprocess_utils import _dict_case_items
from .streaming_review_keys import review_domain, review_scenario

ReviewRankFn = Callable[..., tuple[int, ...]]


def build_review_selection_constraints(
    cases: list[dict[str, Any]],
    *,
    reference_count: int,
    generation_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
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

    reference = max(1, int(reference_count or total))
    profile = dict(generation_profile or {})
    coverage_mode = str(profile.get("coverage_mode") or "").strip()

    priority_counts: dict[str, int] = {}
    scenario_counts: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    for case in candidate_cases:
        priority = case_priority(case)
        if priority in {"P0", "P1", "P2"}:
            priority_counts[priority] = int(priority_counts.get(priority, 0)) + 1
        scenario = review_scenario(case)
        scenario_counts[scenario] = int(scenario_counts.get(scenario, 0)) + 1
        domain = review_domain(case)
        domain_counts[domain] = int(domain_counts.get(domain, 0)) + 1

    priority_min: dict[str, int] = {}
    if int(priority_counts.get("P0") or 0) > 0:
        priority_min["P0"] = 1
    if int(priority_counts.get("P1") or 0) > 0:
        priority_min["P1"] = min(int(priority_counts.get("P1") or 0), 2)
    if int(priority_counts.get("P2") or 0) > 0:
        priority_min["P2"] = 1

    scenario_min: dict[str, int] = {}
    for scenario in ("happy", "state", "exception"):
        if int(scenario_counts.get(scenario) or 0) > 0:
            scenario_min[scenario] = 1

    domain_min: dict[str, int] = {}
    for domain in ("permission", "report"):
        if int(domain_counts.get(domain) or 0) > 0:
            domain_min[domain] = 1

    active_priority_count = int(sum(1 for value in priority_counts.values() if int(value) > 0))
    active_scenario_count = int(sum(1 for value in scenario_counts.values() if int(value) > 0))
    active_domain_count = int(sum(1 for value in domain_counts.values() if int(value) > 0))
    diversity_floor = int((active_priority_count * 2) + active_scenario_count + min(2, active_domain_count))

    target_min = min(
        total,
        max(
            8,
            int(round(total * 0.24)),
            int(diversity_floor),
        ),
    )
    target_max = min(
        total,
        max(
            int(target_min + 6),
            int(round(total * 0.42)),
            int(round(target_min * 1.6)),
        ),
    )

    if coverage_mode == "full_functional_regression":
        target_min = min(
            total,
            max(
                target_min,
                int(round(total * 0.65)),
                int(round(reference * 0.35)),
            ),
        )
        target_max = min(
            total,
            max(
                target_min,
                int(round(total * 0.90)),
                int(round(reference * 0.75)),
            ),
        )
    elif coverage_mode == "expanded_regression":
        target_min = min(
            total,
            max(
                target_min,
                int(round(total * 0.80)),
                int(round(reference * 0.80)),
            ),
        )
        target_max = min(
            total,
            max(
                target_min,
                int(round(total * 0.96)),
                int(round(reference * 1.10)),
            ),
        )
    elif coverage_mode == "standard_regression":
        target_min = min(
            total,
            max(
                target_min,
                int(round(total * 0.45)),
                int(round(reference * 0.35)),
            ),
        )
        target_max = min(
            total,
            max(
                target_min,
                int(round(total * 0.70)),
                int(round(reference * 0.65)),
            ),
        )
    else:
        reference_cap = max(12, int(round(reference * 0.65)))
        target_max = min(target_max, total, reference_cap)
    target_min = min(target_min, target_max)

    return {
        "target_min_count": int(target_min),
        "target_max_count": int(target_max),
        "priority_min": priority_min,
        "scenario_min": scenario_min,
        "domain_min": domain_min,
    }


def enforce_review_selection_constraints(
    *,
    selected_cases: list[dict[str, Any]],
    pool_cases: list[dict[str, Any]],
    constraints: dict[str, Any],
    coverage_context: dict[str, Any] | None,
    rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None,
    rank_case_fn: ReviewRankFn,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    selected: list[dict[str, Any]] = []
    selected_signature_set: set[str] = set()
    selected_case_id_set: set[str] = set()
    constraint_reasons: dict[str, str] = {}

    def _append(case: dict[str, Any], reason: str = "") -> None:
        signature = case_signature(case)
        if not signature or signature in selected_signature_set:
            return
        selected.append(case)
        selected_signature_set.add(signature)
        case_id = review_case_id(case)
        if case_id:
            selected_case_id_set.add(case_id)
        if reason and signature:
            constraint_reasons[signature] = reason

    for case in selected_cases:
        if isinstance(case, dict):
            _append(case)

    all_pool_cases = _dict_case_items(pool_cases)
    remaining_pool = [item for item in all_pool_cases if case_signature(item) not in selected_signature_set]

    def _select_best(predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any] | None:
        candidates = [item for item in remaining_pool if predicate(item)]
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: tuple(
                -value
                for value in rank_case_fn(
                    item,
                    coverage_context=coverage_context,
                    rule_diagnostics=rule_diagnostics,
                )
            )
            + (review_case_id(item),)
        )
        return candidates[0]

    def _selected_priority_count(priority: str) -> int:
        return int(sum(1 for item in selected if case_priority(item) == str(priority).upper()))

    def _selected_scenario_count(scenario: str) -> int:
        return int(sum(1 for item in selected if review_scenario(item) == str(scenario).strip().lower()))

    def _selected_domain_count(domain: str) -> int:
        return int(sum(1 for item in selected if review_domain(item) == str(domain).strip().lower()))

    priority_min = dict(constraints.get("priority_min") or {})
    for priority, min_count in priority_min.items():
        required = max(0, int(min_count or 0))
        while _selected_priority_count(priority) < required:
            best = _select_best(lambda item, p=priority: case_priority(item) == str(p).upper())
            if best is None:
                break
            _append(best, reason=f"retained_by_constraint_priority_{priority}")
            remaining_pool = [item for item in remaining_pool if case_signature(item) != case_signature(best)]

    scenario_min = dict(constraints.get("scenario_min") or {})
    for scenario, min_count in scenario_min.items():
        required = max(0, int(min_count or 0))
        while _selected_scenario_count(scenario) < required:
            best = _select_best(lambda item, s=scenario: review_scenario(item) == str(s).strip().lower())
            if best is None:
                break
            _append(best, reason=f"retained_by_constraint_scenario_{scenario}")
            remaining_pool = [item for item in remaining_pool if case_signature(item) != case_signature(best)]

    domain_min = dict(constraints.get("domain_min") or {})
    for domain, min_count in domain_min.items():
        required = max(0, int(min_count or 0))
        while _selected_domain_count(domain) < required:
            best = _select_best(lambda item, d=domain: review_domain(item) == str(d).strip().lower())
            if best is None:
                break
            _append(best, reason=f"retained_by_constraint_domain_{domain}")
            remaining_pool = [item for item in remaining_pool if case_signature(item) != case_signature(best)]

    target_min_count = max(1, int(constraints.get("target_min_count") or 1))
    while len(selected) < target_min_count:
        best = _select_best(lambda item: True)
        if best is None:
            break
        _append(best, reason="retained_by_constraint_target_min")
        remaining_pool = [item for item in remaining_pool if case_signature(item) != case_signature(best)]

    target_max_count = max(target_min_count, int(constraints.get("target_max_count") or target_min_count))
    if len(selected) > target_max_count:
        priority_min = {
            str(key).strip().upper(): max(0, int(value or 0))
            for key, value in dict(constraints.get("priority_min") or {}).items()
        }
        scenario_min = {
            str(key).strip().lower(): max(0, int(value or 0))
            for key, value in dict(constraints.get("scenario_min") or {}).items()
        }
        domain_min = {
            str(key).strip().lower(): max(0, int(value or 0))
            for key, value in dict(constraints.get("domain_min") or {}).items()
        }

        def _can_remove(case: dict[str, Any], current: list[dict[str, Any]]) -> bool:
            priority = case_priority(case)
            scenario = review_scenario(case)
            domain = review_domain(case)
            if priority in priority_min:
                count = sum(1 for item in current if case_priority(item) == priority)
                if count <= int(priority_min.get(priority) or 0):
                    return False
            if scenario in scenario_min:
                count = sum(1 for item in current if review_scenario(item) == scenario)
                if count <= int(scenario_min.get(scenario) or 0):
                    return False
            if domain in domain_min:
                count = sum(1 for item in current if review_domain(item) == domain)
                if count <= int(domain_min.get(domain) or 0):
                    return False
            return True

        removal_candidates = list(selected)
        removal_candidates.sort(
            key=lambda item: tuple(
                rank_case_fn(
                    item,
                    coverage_context=coverage_context,
                    rule_diagnostics=rule_diagnostics,
                )
            )
            + (review_case_id(item),)
        )

        for case in removal_candidates:
            if len(selected) <= target_max_count:
                break
            if not _can_remove(case, selected):
                continue
            signature = case_signature(case)
            selected = [item for item in selected if case_signature(item) != signature]
            if signature in selected_signature_set:
                selected_signature_set.remove(signature)
            case_id = review_case_id(case)
            if case_id and case_id in selected_case_id_set:
                selected_case_id_set.remove(case_id)
            if signature not in constraint_reasons:
                constraint_reasons[signature] = "dropped_by_target_max"

    return selected, constraint_reasons


__all__ = [
    "build_review_selection_constraints",
    "enforce_review_selection_constraints",
]
