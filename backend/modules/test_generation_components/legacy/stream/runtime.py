from __future__ import annotations

from functools import lru_cache
from importlib import import_module
from typing import Any


@lru_cache(maxsize=None)
def lazy_attr(module_name: str, attr_name: str, package: str | None = None) -> Any:
    module = import_module(module_name, package=package)
    return getattr(module, attr_name)


def component_attr(module_name: str, attr_name: str) -> Any:
    package = __package__ if module_name.startswith(".") else None
    return lazy_attr(module_name, attr_name, package)


def call_component(module_name: str, attr_name: str, *args: Any, **kwargs: Any) -> Any:
    return component_attr(module_name, attr_name)(*args, **kwargs)


def resolve_lazy_attr(value: Any) -> Any:
    target = getattr(value, "_target", None)
    if callable(target):
        return target()
    return value


class LazyAttrProxy:
    def __init__(self, module_name: str, attr_name: str):
        object.__setattr__(self, "_module_name", module_name)
        object.__setattr__(self, "_attr_name", attr_name)

    def _target(self) -> Any:
        return component_attr(self._module_name, self._attr_name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._target()(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"_module_name", "_attr_name"}:
            object.__setattr__(self, name, value)
            return
        setattr(self._target(), name, value)

    def __delattr__(self, name: str) -> None:
        if name in {"_module_name", "_attr_name"}:
            object.__delattr__(self, name)
            return
        delattr(self._target(), name)
