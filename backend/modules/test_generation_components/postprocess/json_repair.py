"""JSON repair helpers for test generation postprocessing."""

from __future__ import annotations

from typing import Any


def _normalize_for_dedup(text: Any) -> str:
    """Normalize text for duplicate detection."""
    return str(text or "").strip().lower().replace("\r", "").replace("\n", " ")


def _case_dedup_key(case: dict[str, Any]) -> str:
    """Build a deduplication key that does not depend on ``id``."""
    module = _normalize_for_dedup(case.get("test_module"))
    desc = _normalize_for_dedup(case.get("description"))
    test_input = _normalize_for_dedup(case.get("test_input"))
    expected = _normalize_for_dedup(case.get("expected_result"))
    steps = case.get("steps") or []
    if isinstance(steps, list):
        steps_text = " | ".join(_normalize_for_dedup(s) for s in steps)
    else:
        steps_text = _normalize_for_dedup(steps)
    return f"{module}||{desc}||{test_input}||{expected}||{steps_text}"


def deduplicate_test_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the first case for each semantic signature."""
    if not isinstance(cases, list):
        return []
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        key = _case_dedup_key(case)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(case)
    return deduped


def count_unique_test_cases(cases: list[dict[str, Any]]) -> int:
    """Count unique test cases using the deduplication key."""
    return len(deduplicate_test_cases(cases))
