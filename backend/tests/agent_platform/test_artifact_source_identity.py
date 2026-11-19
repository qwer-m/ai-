from __future__ import annotations

import ast
from pathlib import Path
import shutil

import pytest

from modules.agent_platform.registry import tool_registry
from modules.agent_platform.semantic_sources import source_dependency_manifest


_BACKEND = Path(__file__).resolve().parents[2]
_ROOTS = [("modules.agent_platform.test_generation_agent", "initialize_generation_state")]


def test_pure_cache_permission_comes_from_real_handler_registration() -> None:
    from modules.agent_platform.test_generation_builtin_specs import BUILTIN_TOOL_SPECS

    for spec in BUILTIN_TOOL_SPECS:
        assert not tool_registry.is_cache_safe(spec["handler_key"])
    assert not tool_registry.is_cache_safe("testing.generation.finish_generation")
    assert "testing.persist_test_cases" not in tool_registry.keys()


def test_real_transitive_validator_changes_invalidate_but_unrelated_sources_do_not(tmp_path: Path) -> None:
    for directory in (_BACKEND / "core", _BACKEND / "modules"):
        for source in directory.rglob("*.py"):
            destination = tmp_path / source.relative_to(_BACKEND)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    original = source_dependency_manifest(_ROOTS, backend_root=tmp_path)
    assert "modules/agent_platform/generation_evidence.py:_text" in original
    assert not any("test_case_evaluation_feedback.py" in key for key in original)
    unrelated = tmp_path / "modules/agent_platform/test_case_evaluation_feedback.py"
    unrelated.write_text(unrelated.read_text(encoding="utf-8") + "\n无关模块身份验证 = 1\n", encoding="utf-8")
    assert source_dependency_manifest(_ROOTS, backend_root=tmp_path) == original
    relevant = tmp_path / "modules/agent_platform/generation_evidence.py"
    tree = ast.parse(relevant.read_text(encoding="utf-8"))
    validator = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_text")
    validator.body.append(ast.Pass())
    relevant.write_text(ast.unparse(ast.fix_missing_locations(tree)), encoding="utf-8")
    assert source_dependency_manifest(_ROOTS, backend_root=tmp_path) != original


def test_unknown_declared_production_dependency_is_rejected() -> None:
    with pytest.raises(ValueError, match="入口"):
        source_dependency_manifest([("modules.agent_platform.test_generation_agent", "missing_validator")])
