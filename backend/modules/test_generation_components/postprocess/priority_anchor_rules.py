from __future__ import annotations

from typing import Any

from .postprocess_priority_config import (
    p0_core_tokens,
    p0_critical_families,
    p0_essay_domain_negative_tokens,
    p0_essay_domain_positive_tokens,
    p0_essay_domain_primary_tokens,
    p0_essay_exclusion_tokens,
    p0_low_value_tokens,
)

_COURSE_PERMISSION_FAMILIES = {
    "free_first_lesson",
    "locked_member_courses",
    "member_all_courses",
    "permission",
}


def p0_case_anchor_text(case: dict[str, Any] | Any) -> str:
    if not isinstance(case, dict):
        return str(case or "").lower()
    steps = case.get("steps")
    steps_text = (
        " ".join(str(step) for step in steps if str(step).strip())
        if isinstance(steps, list)
        else str(steps or "")
    )
    return " ".join(
        [
            str(case.get("test_module") or ""),
            str(case.get("description") or ""),
            str(case.get("expected_result") or ""),
            str(case.get("test_input") or ""),
            steps_text,
        ]
    ).lower()


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
