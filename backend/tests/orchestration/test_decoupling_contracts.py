import sys
import re
import os
import subprocess
import ast
from pathlib import Path

# Ensure backend imports work when pytest runs from repo root.
sys.path.append(str(Path(__file__).resolve().parents[2]))

from routers.system import common


def _read_backend_file(relative_path: str) -> str:
    return (Path(__file__).resolve().parents[2] / relative_path).read_text(encoding="utf-8")


def _iter_backend_python_files(*roots: str) -> list[Path]:
    backend_root = Path(__file__).resolve().parents[2]
    files: list[Path] = []
    for root in roots:
        files.extend((backend_root / root).rglob("*.py"))
    return files


def _relative_backend_path(path: Path) -> str:
    return path.relative_to(Path(__file__).resolve().parents[2]).as_posix()


def _assert_imports_do_not_load_celery_runtime(modules: list[str]) -> None:
    backend_root = Path(__file__).resolve().parents[2]
    leaked_modules = [
        "celery_config",
        "modules.orchestration.adapters.celery_task_runtime",
    ]
    probe = "\n".join(
        [
            "import importlib, sys",
            f"modules = {modules!r}",
            f"leaked_modules = {leaked_modules!r}",
            "for name in modules:",
            "    importlib.import_module(name)",
            "loaded = [name for name in leaked_modules if name in sys.modules]",
            "if loaded:",
            "    raise SystemExit('loaded celery runtime modules: ' + ', '.join(loaded))",
        ]
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(backend_root)
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(backend_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_knowledge_detail_route_keeps_get_and_delete_contract() -> None:
    methods = set()
    for route in common.router.routes:
        if getattr(route, "path", "") == "/knowledge/{doc_id}":
            methods.update(getattr(route, "methods", set()))
    assert "GET" in methods
    assert "DELETE" in methods


def test_celery_runtime_access_stays_behind_orchestration_adapter() -> None:
    allowed = {
        "modules/orchestration/adapters/celery_task_runtime.py",
    }
    forbidden_patterns = [
        r"\.send_task\(",
        r"\.apply_async\(",
        r"\.delay\(",
        r"\bAsyncResult\(",
        r"from\s+celery\.result\s+import\s+AsyncResult",
    ]

    violations: list[str] = []
    for path in _iter_backend_python_files("routers", "modules"):
        rel = _relative_backend_path(path)
        if rel in allowed or rel.startswith("tests/"):
            continue
        content = path.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            if re.search(pattern, content):
                violations.append(f"{rel}: {pattern}")

    assert violations == []


def test_api_routes_do_not_import_queue_runtime_directly() -> None:
    forbidden_patterns = [
        r"from\s+celery\b",
        r"import\s+celery\b",
        r"from\s+celery_config\b",
        r"import\s+celery_config\b",
        r"from\s+modules\.orchestration\.task_runtime\b",
        r"from\s+modules\.orchestration\.task_dispatcher\b",
        r"from\s+modules\.orchestration\.adapters\b",
    ]

    violations: list[str] = []
    for path in _iter_backend_python_files("routers"):
        rel = _relative_backend_path(path)
        content = path.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            if re.search(pattern, content):
                violations.append(f"{rel}: {pattern}")

    assert violations == []


def test_governance_and_route_imports_do_not_initialize_celery_runtime() -> None:
    _assert_imports_do_not_load_celery_runtime(
        [
            "modules.orchestration.background_task_governance",
            "modules.orchestration.ports.task_runtime_port",
            "modules.orchestration.task_dispatcher",
            "modules.orchestration.task_names",
            "modules.orchestration.task_runtime",
            "modules.orchestration.task_status",
            "routers.system.tasks",
            "routers.system.common",
            "routers.orchestration.evaluation_execute_routes",
        ]
    )


def test_celery_tasks_do_not_import_http_routes() -> None:
    content = _read_backend_file("modules/orchestration/tasks.py")

    assert "from routers" not in content
    assert "import routers" not in content


def test_process_background_entrypoints_are_governed() -> None:
    allowed = {
        "modules/orchestration/background_task_governance.py",
    }
    forbidden_patterns = [
        r"\bthreading\.Thread\(",
        r"\bbackground_tasks\.add_task\(",
        r"\bBackgroundTasks\.add_task\(",
        r"\basyncio\.create_task\(",
    ]

    violations: list[str] = []
    for path in _iter_backend_python_files("routers", "modules"):
        rel = _relative_backend_path(path)
        if rel in allowed or rel.startswith("tests/"):
            continue
        content = path.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            if re.search(pattern, content):
                violations.append(f"{rel}: {pattern}")

    assert violations == []


def test_in_request_threadpool_usage_is_explicitly_reviewed() -> None:
    allowed = {
        "modules/orchestration/background_task_governance.py",
    }

    actual = {
        _relative_backend_path(path)
        for path in _iter_backend_python_files("routers", "modules")
        if "ThreadPoolExecutor" in path.read_text(encoding="utf-8")
        or "ProcessPoolExecutor" in path.read_text(encoding="utf-8")
    }

    assert actual <= allowed


def test_fastapi_run_in_threadpool_is_only_request_scoped_offload() -> None:
    allowed: set[tuple[str, str]] = set()

    def _call_name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = _call_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        return ""

    actual: set[tuple[str, str]] = set()
    unawaited: list[str] = []
    for path in _iter_backend_python_files("routers"):
        content = path.read_text(encoding="utf-8")
        if "run_in_threadpool" not in content:
            continue
        rel = _relative_backend_path(path)
        tree = ast.parse(content)
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _call_name(node.func) != "run_in_threadpool":
                continue
            target = _call_name(node.args[0]) if node.args else ""
            actual.add((rel, target))

            current: ast.AST | None = node
            awaited = False
            while current in parents:
                current = parents[current]
                if isinstance(current, ast.Await):
                    awaited = True
                    break
                if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    break
            if not awaited:
                unawaited.append(f"{rel}:{node.lineno}:{target}")

    assert unawaited == []
    assert actual <= allowed


def test_obsolete_generation_chain_is_removed() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    removed_paths = [
        "modules/testing/test_generation.py",
        "modules/test_generation_components/legacy_generation_impl.py",
        "modules/test_generation_components/legacy",
        "routers/automation/test_generation.py",
        "routers/automation/test_generation_generate_routes.py",
        "routers/automation/test_generation_generate_routes_impl.py",
        "modules/testing/manual_quality_profile.py",
        "modules/testing/case_fact_relations.py",
        "modules/testing/coverage/coverage_case_classifier.py",
        "modules/testing/coverage/coverage_case_complexity.py",
        "modules/testing/coverage/domain_gate.py",
        "modules/testing/coverage/flow_outline.py",
        "modules/testing/coverage/flow_structure_governance.py",
        "modules/testing/coverage/scenario_registry.py",
        "modules/testing/coverage/scenario_registry_data.json",
        "core/db/model_defs/testing_patterns.py",
        "modules/testing/evaluation.py",
        "modules/testing/evaluation_compare_background.py",
        "modules/testing/evaluation_artifacts.py",
        "modules/test_generation_components/coverage",
        "modules/testing/coverage",
        "modules/testing_components/repositories/evaluation_artifact_repository.py",
        "modules/orchestration_components/services/evaluation_history_service.py",
        "modules/orchestration_components/repositories/evaluation_history_repository.py",
    ]

    assert all(not (backend_root / path).exists() for path in removed_paths)

    config_content = _read_backend_file("core/settings/config.py")
    obsolete_settings = [
        "TEST_GENERATION_BATCH_SIZE",
        "GENERATION_STREAM_COVERAGE_SHARD",
        "CASE_QUALITY_ENFORCE_MIN_ACCEPTABLE_FINAL",
    ]
    assert all(name not in config_content for name in obsolete_settings)


def test_standard_api_request_does_not_use_diagnostic_route() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    main_content = _read_backend_file("main.py")
    standard_api_content = _read_backend_file("modules/testing/standard_api.py")

    assert not (backend_root / "routers/system/diagnostics/debug.py").exists()
    assert "debug_router" not in main_content
    assert '@router.post("/request")' in standard_api_content
    assert "ENABLE_DIAGNOSTIC_ROUTES" not in _read_backend_file("core/settings/config.py")


def test_evaluation_history_does_not_write_back_to_knowledge_base() -> None:
    route_content = _read_backend_file("routers/orchestration/evaluation_history_routes.py")

    assert '"/evaluation/history/{project_id}"' in route_content
    assert '"/evaluation/latest-supplement/{project_id}"' not in route_content
    assert '"/evaluation/save-knowledge"' not in route_content
    assert "AgentPlatformRepository" in route_content
    assert "KnowledgeDocument" not in route_content
    assert "knowledge_base" not in route_content


def test_celery_task_registration_import_is_lightweight() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    forbidden_loaded_modules = [
        "core.db.database",
        "core.db.model_defs",
        "core.cache_layer.cache",
        "modules.domain.knowledge_base",
        "modules.knowledge_base_components.document.index_audit",
        "modules.knowledge_base_components.document.offline_parse",
        "modules.agent_platform.runtime",
        "modules.agent_platform.recovery",
        "modules.rag_eval.services.rag_eval_service",
        "modules.testing.evaluation_compare_background",
    ]
    probe = "\n".join(
        [
            "import importlib, sys",
            "for name in ['modules.orchestration.tasks']:",
            "    importlib.import_module(name)",
            f"forbidden = {forbidden_loaded_modules!r}",
            "loaded = [name for name in forbidden if name in sys.modules]",
            "if loaded:",
            "    raise SystemExit('loaded celery task runtime modules: ' + ', '.join(loaded))",
        ]
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(backend_root)
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(backend_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    output = result.stdout + result.stderr
    assert "Successfully connected to MySQL" not in output
    assert "Redis connected:" not in output
    assert "Local L1 cache backend:" not in output

