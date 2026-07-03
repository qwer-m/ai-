"""
测试生成模块门面层。

该文件保留历史导入路径，实际生成实现延迟到调用时加载，避免路由、
任务注册和架构测试在 import 阶段拉起数据库、缓存和完整生成链路。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


def _legacy_module():
    return import_module("modules.testing.test_generation_components.legacy_generation_impl")


def _json_processing_module():
    return import_module("modules.test_generation_components.postprocess.json_processing")


def clean_and_parse_json(response_text: str) -> Any:
    return _json_processing_module().clean_and_parse_json(response_text)


def normalize_json_structure(data: Any) -> Any:
    return _json_processing_module().normalize_json_structure(data)


class LazyTestGenerator:
    def __init__(self) -> None:
        object.__setattr__(self, "_target", None)

    def _resolve(self) -> Any:
        target = object.__getattribute__(self, "_target")
        if target is None:
            target = _legacy_module().TestGenerationModule()
            object.__setattr__(self, "_target", target)
        return target

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_target":
            object.__setattr__(self, name, value)
            return
        setattr(self._resolve(), name, value)


class TestGenerationModule:
    __test__ = False

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        return _legacy_module().TestGenerationModule(*args, **kwargs)


test_generator = LazyTestGenerator()


__all__ = [
    "LazyTestGenerator",
    "TestGenerationModule",
    "clean_and_parse_json",
    "normalize_json_structure",
    "test_generator",
]
