from __future__ import annotations

from typing import Any


def resolve_coverage_gap_state(coverage_result: dict[str, Any]) -> dict[str, Any]:
    missing_rules = list(coverage_result.get("missing_rules") or [])
    diagnostics = [
        item
        for item in (coverage_result.get("rule_diagnostics") or [])
        if isinstance(item, dict)
    ]
    has_missing_types = any(bool(item.get("missing_types")) for item in diagnostics)
    return {
        "missing_rules": missing_rules,
        "has_missing_types": bool(has_missing_types),
        "gap_count": int(len(missing_rules) + (1 if has_missing_types else 0)),
    }


__all__ = ["resolve_coverage_gap_state"]
