from __future__ import annotations

import re
from typing import Any

from .coverage_strategy import complexity_hints
from .rule_coverage import _normalize_text
from ..postprocess.case_access import case_steps, case_text_field

_COMPLEXITY_HINTS = complexity_hints()


def case_complexity_profile(case: dict[str, Any]) -> dict[str, Any]:
    step_count = len(case_steps(case))
    expected = _normalize_text(case_text_field(case, "expected_result"))
    description = _normalize_text(case_text_field(case, "description"))
    punctuation_parts = len([part for part in re.split(r"[;；。.!?？]|(?:\s+and\s+)", expected) if part.strip()])
    comma_parts = len([part for part in re.split(r"[，,、]", expected) if part.strip()])
    hint_hits = [hint for hint in _COMPLEXITY_HINTS if hint and hint.lower() in f"{description}\n{expected}".lower()]
    score = 0
    reasons: list[str] = []
    if step_count > 5:
        score += step_count - 5
        reasons.append("too_many_steps")
    if punctuation_parts >= 4 or comma_parts >= 5:
        score += 2
        reasons.append("many_expected_clauses")
    if len(hint_hits) >= 2:
        score += 1
        reasons.append("multi_assertion_language")
    if len(expected) > 220:
        score += 1
        reasons.append("long_expected_result")
    return {
        "step_count": int(step_count),
        "expected_clause_count": int(max(punctuation_parts, comma_parts)),
        "complexity_score": int(score),
        "complexity_reasons": reasons,
        "is_complex_multi_assertion": bool(score >= 3),
    }
