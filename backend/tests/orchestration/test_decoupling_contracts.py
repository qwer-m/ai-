import sys
from pathlib import Path

# Ensure backend imports work when pytest runs from repo root.
sys.path.append(str(Path(__file__).resolve().parents[2]))

from routers.system import common


def _read_backend_file(relative_path: str) -> str:
    return (Path(__file__).resolve().parents[2] / relative_path).read_text(encoding="utf-8")


def test_pipeline_layers_do_not_depend_on_router_pipeline_routes_namespace() -> None:
    files = [
        "routers/orchestration/pipeline.py",
        "routers/orchestration/pipeline_runtime.py",
        "modules/orchestration_components/services/pipeline_run_service.py",
        "modules/orchestration_components/pipeline_runtime/schemas.py",
        "modules/orchestration_components/pipeline_runtime/support.py",
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

