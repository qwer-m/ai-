"""JSON repair helpers for test generation postprocessing."""

from __future__ import annotations

import json
from typing import Any

from .case_access import (
    case_priority,
    case_step_lines,
    case_text_field,
    case_text_list_field,
)
from .streaming_review_semantics import (
    compact_structured_case_risk,
    compact_verified_case_semantics,
)


def _normalize_for_dedup(text: Any) -> str:
    """Normalize text for duplicate detection."""
    return str(text or "").strip().lower().replace("\r", "").replace("\n", " ")


def deterministic_case_dedup_key(
    case: dict[str, Any],
    *,
    include_priority: bool = True,
) -> str:
    """只删除完整公开行为与核验语义都一致的确定性重复。"""
    payload = {
        "test_module": _normalize_for_dedup(case_text_field(case, "test_module")),
        "description": _normalize_for_dedup(case_text_field(case, "description")),
        "preconditions": [
            _normalize_for_dedup(item)
            for item in case_text_list_field(case, "preconditions", split_lines=True)
        ],
        "steps": [_normalize_for_dedup(item) for item in case_step_lines(case)],
        "test_input": _normalize_for_dedup(case_text_field(case, "test_input")),
        "expected_result": _normalize_for_dedup(case_text_field(case, "expected_result")),
        "_semantic": compact_verified_case_semantics(case),
        "structured_risk": compact_structured_case_risk(case),
    }
    if include_priority:
        payload["priority"] = case_priority(case, prefer_final=True)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def deduplicate_test_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the first case for each exact structural signature.

    Near-semantic overlap is intentionally left to Review/Judge diagnostics so
    the generator does not collapse a candidate pool before coverage scoring.
    """
    if not isinstance(cases, list):
        return []
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        key = deterministic_case_dedup_key(case)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(case)
    return deduped


def count_unique_test_cases(cases: list[dict[str, Any]]) -> int:
    """Count unique test cases using the deduplication key."""
    return len(deduplicate_test_cases(cases))
