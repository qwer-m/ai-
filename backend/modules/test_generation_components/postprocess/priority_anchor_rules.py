from __future__ import annotations

from typing import Any, Callable

from .case_access import case_flat_text, case_priority
from .postprocess_priority_config import (
    p0_core_tokens,
    p0_critical_families,
    p0_essay_domain_negative_tokens,
    p0_essay_domain_positive_tokens,
    p0_essay_domain_primary_tokens,
    p0_essay_exclusion_tokens,
    p0_low_value_tokens,
)
from .priority_anchor_floor_policy import MainPathAnchorPolicy
from .streaming_case_normalization import normalize_priority_value
from .streaming_postprocess_utils import _dict_case_copies

_COURSE_PERMISSION_FAMILIES = {
    "free_first_lesson",
    "locked_member_courses",
    "member_all_courses",
    "permission",
}


def p0_main_path_target_count(case_count: int, *, coverage_mode: str = "") -> int:
    count = max(0, int(case_count or 0))
    if count <= 0:
        return 0
    mode = str(coverage_mode or "")
    if mode == "full_functional_regression":
        if count >= 80:
            target_count = min(12, max(8, int((count + 9) // 10)))
        elif count >= 40:
            target_count = min(10, max(9, int(round(count * 0.12))))
        else:
            target_count = min(6, max(3, int(round(count * 0.14))))
    elif mode == "expanded_regression":
        target_count = (
            min(4, max(3, int(round(count * 0.06))))
            if count >= 50
            else min(3, max(1, int(round(count * 0.08))))
        )
    else:
        target_count = 0
    return min(max(0, int(target_count)), count)


def apply_priority_override(
    case: dict[str, Any],
    *,
    priority: str,
    source: str,
    state: str = "overridden",
) -> None:
    normalized_priority = str(priority or "").strip().upper()
    if normalized_priority not in {"P0", "P1", "P2"}:
        return
    case["priority"] = normalized_priority
    case["priority_final"] = normalized_priority
    case["priority_decision_state"] = str(state or "overridden")
    case["priority_decision_source"] = str(source or "").strip()


def p0_case_anchor_text(case: dict[str, Any] | Any) -> str:
    if not isinstance(case, dict):
        return str(case or "").lower()
    return case_flat_text(
        case,
        fields=("test_module", "description", "expected_result", "test_input", "steps"),
        separator=" ",
        lower=True,
    )


def p0_essay_domain_active(requirement_text: str = "") -> bool:
    requirement_text_lower = str(requirement_text or "").lower()
    return any(
        token.lower() in requirement_text_lower
        for token in p0_essay_domain_primary_tokens()
    ) or (
        any(token.lower() in requirement_text_lower for token in p0_essay_domain_positive_tokens())
        and not any(token.lower() in requirement_text_lower for token in p0_essay_domain_negative_tokens())
    )


def p0_cross_domain_essay_case(case: dict[str, Any], *, requirement_text: str = "") -> bool:
    if p0_essay_domain_active(requirement_text):
        return False
    text = p0_case_anchor_text(case)
    return any(token.lower() in text for token in p0_essay_exclusion_tokens())


def p0_has_low_value_signal(case_or_text: dict[str, Any] | str) -> bool:
    text = p0_case_anchor_text(case_or_text)
    return any(token.lower() in text for token in p0_low_value_tokens())


def p0_has_core_signal(case_or_text: dict[str, Any] | str) -> bool:
    text = p0_case_anchor_text(case_or_text)
    return any(token.lower() in text for token in p0_core_tokens())


def p0_configured_anchor_family(
    case_or_text: dict[str, Any] | str,
    *,
    requirement_text: str = "",
    course_only_when_non_essay: bool = True,
) -> str:
    if isinstance(case_or_text, dict) and p0_cross_domain_essay_case(
        case_or_text,
        requirement_text=requirement_text,
    ):
        return ""
    text = p0_case_anchor_text(case_or_text)
    critical_families = p0_critical_families()
    if course_only_when_non_essay and not p0_essay_domain_active(requirement_text):
        critical_families = tuple(
            item
            for item in critical_families
            if str(item[0]) in _COURSE_PERMISSION_FAMILIES
        )
    for family, tokens in critical_families:
        if all(token.lower() in text for token in tokens):
            return str(family)
    return ""


def p0_main_path_anchor(case: dict[str, Any], *, requirement_text: str = "") -> bool:
    if p0_cross_domain_essay_case(case, requirement_text=requirement_text):
        return False
    if p0_configured_anchor_family(case, requirement_text=requirement_text):
        return True
    return p0_has_core_signal(case) and not p0_has_low_value_signal(case)


def _default_case_signature(case: dict[str, Any]) -> str:
    return "\n".join(
        str(case.get(field) or "")
        for field in ("test_module", "description", "expected_result", "test_input")
    )


def enforce_main_path_p0_anchors(
    cases: list[dict[str, Any]],
    *,
    coverage_mode: str = "",
    requirement_text: str = "",
    case_signature_fn: Callable[[dict[str, Any]], str] | None = None,
    case_complexity_profile_fn: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    candidate_cases = _dict_case_copies(cases)
    mode = str(coverage_mode or "")
    if mode not in {"expanded_regression", "full_functional_regression"}:
        return candidate_cases
    case_count = len(candidate_cases)
    if case_count <= 0:
        return candidate_cases
    target_count = p0_main_path_target_count(case_count, coverage_mode=mode)
    signature_fn = case_signature_fn or _default_case_signature
    policy = MainPathAnchorPolicy(
        configured_anchor_family_fn=lambda text: p0_configured_anchor_family(
            text,
            requirement_text=str(requirement_text or ""),
            course_only_when_non_essay=False,
        ),
        has_core_signal_fn=p0_has_core_signal,
        has_low_value_signal_fn=p0_has_low_value_signal,
        complexity_profile_fn=case_complexity_profile_fn,
    )

    for item in candidate_cases:
        if normalize_priority_value(case_priority(item)) != "P0":
            continue
        if p0_cross_domain_essay_case(item, requirement_text=str(requirement_text or "")):
            apply_priority_override(
                item,
                priority="P1",
                source="main_path_anchor_demoted_domain_mismatch",
            )
            continue
        text = p0_case_anchor_text(item)
        if policy.should_demote_non_blocking(text):
            apply_priority_override(
                item,
                priority="P1",
                source="main_path_anchor_demoted_non_blocking",
            )

    existing_p0_signatures = {
        signature_fn(item)
        for item in candidate_cases
        if normalize_priority_value(case_priority(item)) == "P0"
    }
    if len(existing_p0_signatures) >= target_count:
        return candidate_cases

    ranked: list[tuple[int, int, str, dict[str, Any]]] = []
    for index, item in enumerate(candidate_cases):
        if signature_fn(item) in existing_p0_signatures:
            continue
        if p0_cross_domain_essay_case(item, requirement_text=str(requirement_text or "")):
            continue
        text = p0_case_anchor_text(item)
        normalized_priority = normalize_priority_value(case_priority(item))
        rank = policy.primary_rank(
            item=item,
            index=index,
            text=text,
            normalized_priority=normalized_priority,
        )
        if rank is not None:
            ranked.append(rank)

    if len(ranked) < max(1, target_count - len(existing_p0_signatures)):
        ranked_signatures = {signature_fn(item) for _score, _neg_index, _family, item in ranked}
        for index, item in enumerate(candidate_cases):
            signature = signature_fn(item)
            if signature in existing_p0_signatures or signature in ranked_signatures:
                continue
            if p0_cross_domain_essay_case(item, requirement_text=str(requirement_text or "")):
                continue
            text = p0_case_anchor_text(item)
            normalized_priority = normalize_priority_value(case_priority(item))
            rank = policy.fallback_rank(
                item=item,
                index=index,
                text=text,
                normalized_priority=normalized_priority,
                mode=mode,
            )
            if rank is not None:
                ranked.append(rank)
                ranked_signatures.add(signature)

    if not ranked:
        return candidate_cases
    ranked.sort(reverse=True)
    promoted_signatures: set[str] = set(existing_p0_signatures)
    promoted_families: set[str] = set()
    output = _dict_case_copies(candidate_cases)
    for _score, _neg_index, family, item in ranked:
        if len(promoted_signatures) >= target_count:
            break
        if family in promoted_families and family != "general":
            continue
        signature = signature_fn(item)
        if signature in promoted_signatures:
            continue
        promoted_signatures.add(signature)
        promoted_families.add(family)
        for updated in output:
            if signature_fn(updated) == signature:
                apply_priority_override(
                    updated,
                    priority="P0",
                    source="main_path_anchor_floor",
                )
                break
    if len(promoted_signatures) < target_count:
        for _score, _neg_index, _family, item in ranked:
            if len(promoted_signatures) >= target_count:
                break
            signature = signature_fn(item)
            if signature in promoted_signatures:
                continue
            promoted_signatures.add(signature)
            for updated in output:
                if signature_fn(updated) == signature:
                    apply_priority_override(
                        updated,
                        priority="P0",
                        source="main_path_anchor_floor",
                    )
                    break
    return output
