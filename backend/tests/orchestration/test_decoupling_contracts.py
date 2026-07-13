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


def _generation_route_module_names() -> list[str]:
    backend_root = Path(__file__).resolve().parents[2]
    route_dir = backend_root / "routers" / "automation"
    return sorted(
        f"routers.automation.{path.stem}"
        for path in route_dir.glob("test_generation_generate_routes*.py")
    )


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


def test_pipeline_layers_do_not_depend_on_router_pipeline_routes_namespace() -> None:
    files = [
        "routers/orchestration/pipeline.py",
        "routers/orchestration/pipeline_runtime.py",
        "modules/orchestration_components/services/pipeline_run_service.py",
        "modules/orchestration_components/pipeline_runtime/schemas.py",
        "modules/orchestration_components/pipeline_runtime/schema_compat.py",
        "modules/orchestration_components/pipeline_runtime/support.py",
        "modules/orchestration_components/pipeline_runtime/dispatcher.py",
        "modules/orchestration_components/pipeline_runtime/recovery.py",
        "modules/orchestration_components/pipeline_runtime/runner.py",
        "modules/orchestration_components/pipeline_runtime/stage_ops.py",
        "modules/orchestration_components/pipeline_runtime/agent_ops.py",
        "modules/orchestration_components/pipeline_runtime/agent_decision.py",
        "modules/orchestration_components/pipeline_runtime/agent_ops_impl.py",
    ]
    for rel in files:
        content = _read_backend_file(rel)
        assert "routers.pipeline_routes" not in content, f"unexpected coupling found in {rel}"


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
        + _generation_route_module_names()
    )


def test_celery_tasks_do_not_import_pipeline_router_runtime() -> None:
    content = _read_backend_file("modules/orchestration/tasks.py")

    assert "routers.orchestration.pipeline_runtime" not in content


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
    allowed = {
        (
            "routers/automation/test_generation_generate_routes_estimate.py",
            "test_generator.estimate_test_count",
        ),
        (
            "routers/automation/test_generation_generate_routes_file.py",
            "test_generator.generate_test_cases_json",
        ),
    }

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


def test_generation_generate_routes_impl_stays_thin_aggregator() -> None:
    impl = _read_backend_file("routers/automation/test_generation_generate_routes_impl.py")
    split_helpers = _read_backend_file("routers/automation/test_generation_generate_routes_split_helpers.py")

    assert "@router.post" not in impl
    assert "router.include_router" in impl
    assert "test_generator." not in impl
    assert "parse_requirement_for_generation" not in impl
    assert split_helpers.strip() == "from .test_generation_generate_routes_impl import *  # noqa: F401,F403"


def test_generation_route_imports_do_not_initialize_db_or_cache_runtime() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    modules = _generation_route_module_names()
    forbidden_loaded_modules = [
        "core.ai.ai_client",
        "core.authn.auth",
        "core.db.database",
        "core.db.models",
        "core.processing.utils",
        "core.processing.workflow",
        "core.cache_layer.cache",
        "modules.domain.knowledge_base",
        "modules.orchestration.context_orchestrator",
        "modules.testing.test_generation",
        "routers.test_generation_routes.support",
    ]
    probe = "\n".join(
        [
            "import importlib, sys",
            f"modules = {modules!r}",
            f"forbidden = {forbidden_loaded_modules!r}",
            "for name in modules:",
            "    importlib.import_module(name)",
            "loaded = [name for name in forbidden if name in sys.modules]",
            "if loaded:",
            "    raise SystemExit('loaded heavy route runtime modules: ' + ', '.join(loaded))",
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


def test_pipeline_stage_ops_import_does_not_initialize_execution_backends() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    forbidden_loaded_modules = [
        "core.db.database",
        "core.cache_layer.cache",
        "modules.testing.api_testing",
        "modules.testing.evaluation",
        "modules.testing.test_generation",
        "modules.testing.ui_automation",
    ]
    probe = "\n".join(
        [
            "import importlib, sys",
            "importlib.import_module('modules.orchestration_components.pipeline_runtime.stage_ops')",
            f"forbidden = {forbidden_loaded_modules!r}",
            "loaded = [name for name in forbidden if name in sys.modules]",
            "if loaded:",
            "    raise SystemExit('loaded pipeline stage execution backends: ' + ', '.join(loaded))",
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


def test_pipeline_dispatcher_and_recovery_imports_are_lightweight() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    forbidden_loaded_modules = [
        "core.db.database",
        "core.db.models",
        "core.cache_layer.cache",
        "modules.orchestration_components.repositories.pipeline_runtime_repository",
        "modules.orchestration_components.pipeline_runtime.runner",
        "modules.orchestration_components.pipeline_runtime.schema_compat",
        "modules.orchestration_components.pipeline_runtime.support",
    ]
    probe = "\n".join(
        [
            "import importlib, sys",
            "for name in [",
            "    'modules.orchestration_components.pipeline_runtime.dispatcher',",
            "    'modules.orchestration_components.pipeline_runtime.recovery',",
            "]:",
            "    importlib.import_module(name)",
            f"forbidden = {forbidden_loaded_modules!r}",
            "loaded = [name for name in forbidden if name in sys.modules]",
            "if loaded:",
            "    raise SystemExit('loaded pipeline dispatch runtime modules: ' + ', '.join(loaded))",
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


def test_pipeline_runner_does_not_run_schema_compat_at_import_time() -> None:
    source = _read_backend_file("modules/orchestration_components/pipeline_runtime/runner.py")
    tree = ast.parse(source)
    top_level_calls: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if isinstance(func, ast.Name):
            top_level_calls.append(func.id)
        elif isinstance(func, ast.Attribute):
            top_level_calls.append(func.attr)

    assert "ensure_pipeline_table" not in top_level_calls


def test_pipeline_runner_import_is_lightweight() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    forbidden_loaded_modules = [
        "core.db.database",
        "core.db.models",
        "core.processing.workflow",
        "core.cache_layer.cache",
        "modules.orchestration_components.pipeline_runtime.agent_ops",
        "modules.orchestration_components.pipeline_runtime.schema_compat",
        "modules.orchestration_components.pipeline_runtime.support",
        "modules.orchestration_components.repositories.pipeline_runtime_repository",
    ]
    probe = "\n".join(
        [
            "import importlib, sys",
            "importlib.import_module('modules.orchestration_components.pipeline_runtime.runner')",
            f"forbidden = {forbidden_loaded_modules!r}",
            "loaded = [name for name in forbidden if name in sys.modules]",
            "if loaded:",
            "    raise SystemExit('loaded pipeline runner runtime modules: ' + ', '.join(loaded))",
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


def test_celery_task_registration_import_is_lightweight() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    forbidden_loaded_modules = [
        "core.db.database",
        "core.db.models",
        "core.cache_layer.cache",
        "modules.domain.knowledge_base",
        "modules.knowledge_base_components.document.index_audit",
        "modules.knowledge_base_components.document.offline_parse",
        "modules.orchestration_components.pipeline_runtime.runner",
        "modules.orchestration_components.pipeline_runtime.recovery",
        "modules.rag_eval.services.rag_eval_service",
        "modules.testing.evaluation_compare_background",
        "modules.testing.test_generation",
    ]
    probe = "\n".join(
        [
            "import importlib, sys",
            "for name in ['modules.orchestration.tasks_split_helpers', 'modules.orchestration.tasks']:",
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

