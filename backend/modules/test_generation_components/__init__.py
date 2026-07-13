
"""Test generation components package."""

from __future__ import annotations

import importlib

# Compatibility re-exports for historical flat imports. Keep these lazy so
# importing the package does not recursively initialize legacy compatibility paths.
_EXPORT_MODULES = {
    "excel_export": ".export.excel_export",
    "generation_diagnostics": ".prompting.generation_diagnostics",
    "hybrid_context_builder": ".context.hybrid_context_builder",
    "hybrid_guard": ".context.hybrid_guard",
    "json_processing": ".postprocess.json_processing",
    "prompt_orchestration": ".prompting.prompt_orchestration",
    "result_postprocess": ".postprocess.result_postprocess",
    "snapshot_wait_gate": ".context.snapshot_wait_gate",
}

__all__ = [
    "excel_export",
    "generation_diagnostics",
    "hybrid_context_builder",
    "hybrid_guard",
    "json_processing",
    "prompt_orchestration",
    "result_postprocess",
    "snapshot_wait_gate",
]


def __getattr__(name: str):
    if name not in _EXPORT_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(_EXPORT_MODULES[name], __name__)
    globals()[name] = module
    return module
