from typing import Any

from modules.test_generation_components.excel_export import (
    convert_json_to_excel as _convert_json_to_excel_impl,
)
from modules.test_generation_components.json_processing import (
    clean_and_parse_json as _clean_and_parse_json_impl,
)
from modules.test_generation_components.json_processing import (
    normalize_json_structure as _normalize_json_structure_impl,
)
from modules.test_generation_components.json_processing import (
    deduplicate_test_cases as _deduplicate_test_cases_impl,
)
from modules.test_generation_components.json_processing import (
    count_unique_test_cases as _count_unique_test_cases_impl,
)
from modules.test_generation_components.json_processing import (
    infer_case_kind as _infer_case_kind_impl,
)
from modules.test_generation_components.json_processing import (
    reorder_cases_by_closed_loop as _reorder_cases_by_closed_loop_impl,
)


def clean_and_parse_json(response_text: str) -> Any:
    return _clean_and_parse_json_impl(response_text)


def normalize_json_structure(data: Any) -> Any:
    return _normalize_json_structure_impl(data)


def deduplicate_test_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _deduplicate_test_cases_impl(cases)


def count_unique_test_cases(cases: list[dict[str, Any]]) -> int:
    return _count_unique_test_cases_impl(cases)


def infer_case_kind(case: dict[str, Any]) -> str:
    return _infer_case_kind_impl(case)


def reorder_cases_by_closed_loop(
    cases: list[dict[str, Any]],
    *,
    start_id: int = 1,
    renumber_ids: bool = True,
    module_order_hint: list[str] | None = None,
) -> list[dict[str, Any]]:
    return _reorder_cases_by_closed_loop_impl(
        cases,
        start_id=start_id,
        renumber_ids=renumber_ids,
        module_order_hint=module_order_hint,
    )


def convert_json_to_excel(json_data: list | dict) -> bytes:
    return _convert_json_to_excel_impl(json_data)
