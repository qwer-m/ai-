"""Compatibility re-exports for JSON postprocessing helpers."""

from __future__ import annotations

from typing import Any

from .json_normalizer import normalize_json_structure as _normalize_json_structure
from .json_parser import clean_and_parse_json as _clean_and_parse_json
from .json_repair import (
    count_unique_test_cases as _count_unique_test_cases,
    deduplicate_test_cases as _deduplicate_test_cases,
)
from .json_validator import (
    _safe_text_join,
    extract_module_order_from_cases as _extract_module_order_from_cases,
    infer_case_kind as _infer_case_kind,
    reorder_cases_by_closed_loop as _reorder_cases_by_closed_loop,
)

__all__ = [
    "clean_and_parse_json",
    "count_unique_test_cases",
    "deduplicate_test_cases",
    "extract_module_order_from_cases",
    "infer_case_kind",
    "normalize_json_structure",
    "reorder_cases_by_closed_loop",
]


def clean_and_parse_json(response_text: str) -> Any:
    return _clean_and_parse_json(response_text)


def deduplicate_test_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _deduplicate_test_cases(cases)


def count_unique_test_cases(cases: list[dict[str, Any]]) -> int:
    return _count_unique_test_cases(cases)


def normalize_json_structure(data: Any, **kwargs: Any) -> Any:
    return _normalize_json_structure(data, **kwargs)


def infer_case_kind(case: dict[str, Any]) -> str:
    return _infer_case_kind(case)


def extract_module_order_from_cases(
    cases: list[dict[str, Any]],
    module_order_hint: list[str] | None = None,
) -> list[str]:
    return _extract_module_order_from_cases(cases, module_order_hint=module_order_hint)


def reorder_cases_by_closed_loop(
    cases: list[dict[str, Any]],
    *,
    start_id: int = 1,
    renumber_ids: bool = True,
    module_order_hint: list[str] | None = None,
) -> list[dict[str, Any]]:
    return _reorder_cases_by_closed_loop(
        cases,
        start_id=start_id,
        renumber_ids=renumber_ids,
        module_order_hint=module_order_hint,
    )
