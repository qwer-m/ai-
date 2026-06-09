from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import os
import sys

# Compatibility package for historical imports:
# modules.testing.test_generation_components.*
_COMPAT_PREFIX = __name__
_REAL_PREFIX = "modules.test_generation_components"
_REAL_PACKAGE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "test_generation_components")
)

if not os.path.isdir(_REAL_PACKAGE_DIR):
    raise ModuleNotFoundError(
        f"Missing compatibility target directory: {_REAL_PACKAGE_DIR}"
    )

_real_package = importlib.import_module(_REAL_PREFIX)

# Point this package's submodule search path to the real package directory.
__path__ = list(getattr(_real_package, "__path__", [_REAL_PACKAGE_DIR]))


def _to_real_name(fullname: str) -> str:
    return _REAL_PREFIX + fullname[len(_COMPAT_PREFIX):]


def _alias_loaded_real_modules() -> None:
    for name, module in list(sys.modules.items()):
        if name == _REAL_PREFIX or name.startswith(_REAL_PREFIX + "."):
            compat_name = _COMPAT_PREFIX + name[len(_REAL_PREFIX):]
            sys.modules.setdefault(compat_name, module)


class _CompatAliasLoader(importlib.abc.Loader):
    def create_module(self, spec):
        real_module = importlib.import_module(_to_real_name(spec.name))
        sys.modules[spec.name] = real_module
        return real_module

    def exec_module(self, module) -> None:
        return None


class _CompatAliasFinder(importlib.abc.MetaPathFinder):
    marker = "qoder_test_generation_components_compat_alias"

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(_COMPAT_PREFIX + "."):
            return None
        real_name = _to_real_name(fullname)
        real_spec = importlib.util.find_spec(real_name)
        if real_spec is None:
            return None
        is_package = real_spec.submodule_search_locations is not None
        spec = importlib.machinery.ModuleSpec(
            fullname,
            _CompatAliasLoader(),
            is_package=is_package,
        )
        if is_package:
            spec.submodule_search_locations = list(real_spec.submodule_search_locations or [])
        return spec


if not any(getattr(finder, "marker", "") == _CompatAliasFinder.marker for finder in sys.meta_path):
    sys.meta_path.insert(0, _CompatAliasFinder())

_alias_loaded_real_modules()

