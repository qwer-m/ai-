from __future__ import annotations

import os

# Compatibility package for historical imports:
# modules.testing.test_generation_components.*
_REAL_PACKAGE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "test_generation_components")
)

if not os.path.isdir(_REAL_PACKAGE_DIR):
    raise ModuleNotFoundError(
        f"Missing compatibility target directory: {_REAL_PACKAGE_DIR}"
    )

# Point this package's submodule search path to the real package directory.
__path__ = [_REAL_PACKAGE_DIR]

