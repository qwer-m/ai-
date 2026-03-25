"""
智能测试生成引擎（拆分后兼容门面）

说明：
1. 保留历史导入路径：`modules.testing.test_generation_components.legacy_generation_impl`
2. 对外符号不变：`TestGenerationModule`、`clean_and_parse_json`、`normalize_json_structure` 等
3. 具体实现按职责拆分到多个小文件（mixins + adapters）
"""

from typing import Any

from modules.testing.test_generation_components.legacy.adapters import (
    clean_and_parse_json as _clean_and_parse_json,
)
from modules.testing.test_generation_components.legacy.adapters import (
    count_unique_test_cases as _count_unique_test_cases,
)
from modules.testing.test_generation_components.legacy.adapters import (
    deduplicate_test_cases as _deduplicate_test_cases,
)
from modules.testing.test_generation_components.legacy.adapters import (
    infer_case_kind as _infer_case_kind,
)
from modules.testing.test_generation_components.legacy.adapters import (
    normalize_json_structure as _normalize_json_structure,
)
from modules.testing.test_generation_components.legacy.adapters import (
    reorder_cases_by_closed_loop as _reorder_cases_by_closed_loop,
)
from modules.testing.test_generation_components.legacy.context import (
    LegacyGenerationContextMixin,
)
from modules.testing.test_generation_components.legacy.estimation import (
    LegacyGenerationEstimationMixin,
)
from modules.testing.test_generation_components.legacy.json_generation import (
    LegacyGenerationJsonMixin,
)
from modules.testing.test_generation_components.legacy.stream import (
    LegacyGenerationStreamMixin,
)


def clean_and_parse_json(response_text: str) -> Any:
    """兼容旧调用入口。"""
    return _clean_and_parse_json(response_text)


def normalize_json_structure(data: Any) -> Any:
    """兼容旧调用入口。"""
    return _normalize_json_structure(data)


def deduplicate_test_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """兼容旧调用入口。"""
    return _deduplicate_test_cases(cases)


def count_unique_test_cases(cases: list[dict[str, Any]]) -> int:
    """兼容旧调用入口。"""
    return _count_unique_test_cases(cases)


def infer_case_kind(case: dict[str, Any]) -> str:
    """兼容旧调用入口。"""
    return _infer_case_kind(case)


def reorder_cases_by_closed_loop(
    cases: list[dict[str, Any]],
    *,
    start_id: int = 1,
    renumber_ids: bool = True,
    module_order_hint: list[str] | None = None,
) -> list[dict[str, Any]]:
    """兼容旧调用入口。"""
    return _reorder_cases_by_closed_loop(
        cases,
        start_id=start_id,
        renumber_ids=renumber_ids,
        module_order_hint=module_order_hint,
    )


class TestGenerationModule(
    LegacyGenerationEstimationMixin,
    LegacyGenerationContextMixin,
    LegacyGenerationJsonMixin,
    LegacyGenerationStreamMixin,
):
    """
    测试生成模块核心类（由拆分后的 mixin 组合而成）。
    保持对外 API 不变。
    """

    def __init__(self):
        pass
