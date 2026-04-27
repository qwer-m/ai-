"""
测试生成模块门面层。

说明：
1. 该文件保留历史导入路径与对外符号，避免影响路由与其他模块。
2. 具体实现迁移到 `test_generation_components/legacy_generation_impl.py`，
   以降低主文件体积并将复杂流程与门面职责分离。
"""

from typing import Any

from modules.testing.test_generation_components.legacy_generation_impl import (
    TestGenerationModule as _LegacyTestGenerationModule,
    clean_and_parse_json as _legacy_clean_and_parse_json,
    normalize_json_structure as _legacy_normalize_json_structure,
)


def clean_and_parse_json(response_text: str) -> Any:
    """兼容历史函数导出，委托给实现层。"""
    return _legacy_clean_and_parse_json(response_text)


def normalize_json_structure(data: Any) -> Any:
    """兼容历史函数导出，委托给实现层。"""
    return _legacy_normalize_json_structure(data)


class TestGenerationModule(_LegacyTestGenerationModule):
    """
    兼容类名保留。

    通过继承实现层类，确保现有方法签名、行为和外部调用方式保持不变。
    """

    pass


test_generator = TestGenerationModule()

