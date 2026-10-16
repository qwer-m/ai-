"""只读对比真实运行与清理前源码，验证来源查询、恢复计划和运行响应。"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import importlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import event, inspect, select, text
from sqlalchemy.orm import load_only

from core.db.database import SessionLocal
from core.db.model_defs import AgentApproval, AgentRun, AgentWorkflowDefinition, User
from modules.agent_platform.contracts import WorkflowGraph, parse_execution_definition
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.input_snapshots import restore_node_input
from modules.agent_platform.results import persisted_run_output
from modules.agent_platform.serialization import serialize_run, serialize_run_summary
from modules.agent_platform.service import AgentPlatformService, _restorable_checkpoints


def load_baseline(directory: Path) -> dict[str, Any]:
    """加载实际保存的旧源码包，比较两套真实实现，不替换生产依赖。"""
    package_name = "agent_service_cleanup_baseline"
    package_dir = directory / "agent_platform"
    spec = importlib.util.spec_from_file_location(
        package_name, package_dir / "__init__.py", submodule_search_locations=[str(package_dir)],
    )
    if spec is None or spec.loader is None:
        raise ValueError("无法加载清理前源码目录")
    package = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = package
    spec.loader.exec_module(package)
    return {
        name: importlib.import_module(f"{package_name}.{name}")
        for name in ("contracts", "service", "serialization", "run_repository")
    }


@contextmanager
def document_query_count(db) -> Iterator[list[int]]:
    """统计实际文档 SELECT，确认复用候选入口只查一次文档。"""
    counts = [0]
    connection = db.connection()

    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(statement.lower().replace("`", "").split())
        if "from knowledge_documents" in normalized:
            counts[0] += 1

    event.listen(connection, "before_cursor_execute", record)
    try:
        yield counts
    finally:
        event.remove(connection, "before_cursor_execute", record)


def source_resolution(repo, run):
    try:
        source = repo.resolve_source_snapshot(project_id=run.project_id, input_payload=run.input_payload)
    except ValueError as exc:
        return {"error": str(exc)}
    return source.to_dict() if source is not None else None


def verify_summary_projection(run_id: int, baseline) -> None:
    fields = (
        AgentRun.id, AgentRun.project_id, AgentRun.user_id, AgentRun.workflow_definition_id,
        AgentRun.status, AgentRun.current_node_key, AgentRun.input_payload,
        AgentRun.error_message, AgentRun.parent_run_id, AgentRun.task_id,
        AgentRun.created_at, AgentRun.started_at, AgentRun.finished_at,
    )
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.scalar(select(AgentRun).where(AgentRun.id == run_id).options(
            load_only(*fields, raiseload=True),
        ))
        assert run is not None
        unloaded = set(inspect(run).unloaded)
        assert {"run_context", "output_payload"} <= unloaded
        assert serialize_run_summary(run) == baseline.serialize_run_summary(run)
        assert set(inspect(run).unloaded) == unloaded
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()


def verify_checkpoints(graph, old_graph, nodes, old_service) -> list[dict[str, Any]]:
    sources = {node.id: node for node in nodes}
    expanded = []
    for node in nodes:
        copied = deepcopy(node)
        copied.input_payload = restore_node_input(node, sources.get)
        expanded.append(copied)
    expanded_by_id = {node.id: node for node in expanded}
    old_checkpoints = old_service._restorable_node_runs(execution=old_graph, node_runs=expanded)
    checkpoints = _restorable_checkpoints(execution=graph, node_runs=nodes)
    assert [item.node_run.id for item in checkpoints] == [node.id for node in old_checkpoints]
    nodes_by_key = {node.node_key: node for node in old_graph.nodes}
    restored = []
    for item in checkpoints:
        node_run = item.node_run
        node = nodes_by_key[node_run.node_key]
        failed = node_run.status != "success"
        repair = failed and old_service._has_restorable_node_repair_state(node, expanded_by_id[node_run.id])
        completed = (node_run.output_payload or {}).get(node.map_config.output_key) if (
            failed and node.node_type == "agent_map" and node.map_config is not None
        ) else None
        assert item.has_repair_state == repair
        assert item.has_partial_map_output == (isinstance(completed, list) and bool(completed))
        restored.append({
            "node_run_id": node_run.id, "repair": item.has_repair_state,
            "partial_map": item.has_partial_map_output,
        })
    return restored


def verify_runs(db, runs, baseline) -> list[dict[str, Any]]:
    repo = AgentRunRepository(db)
    old_repo = baseline["run_repository"].AgentRunRepository(db)
    service = AgentPlatformService(db)
    old_service = baseline["service"].AgentPlatformService(db)
    results = []
    for run in runs:
        nodes = repo.list_node_runs(run_id=run.id)
        approvals = repo.list_approvals(run_id=run.id)
        expanded_run = deepcopy(run)
        expanded_run.output_payload = persisted_run_output(run)
        expanded_nodes = []
        for node in nodes:
            copied = deepcopy(node)
            copied.input_payload = repo.read_node_input(node)
            expanded_nodes.append(copied)
        assert serialize_run(run, node_runs=nodes, approvals=approvals) == baseline["serialization"].serialize_run(
            expanded_run, node_runs=expanded_nodes, approvals=approvals,
        )
        assert serialize_run_summary(run) == baseline["serialization"].serialize_run_summary(run)
        assert source_resolution(repo, run) == source_resolution(old_repo, run)
        verify_summary_projection(run.id, baseline["serialization"])
        workflow = db.get(AgentWorkflowDefinition, run.workflow_definition_id)
        assert workflow is not None
        graph = parse_execution_definition(workflow.definition)
        restored = []
        failed_attempts = []
        if isinstance(graph, WorkflowGraph):
            old_graph = baseline["contracts"].parse_execution_definition(workflow.definition)
            restored = verify_checkpoints(graph, old_graph, nodes, baseline["service"])
            # 使用原样持久化的失败尝试，避免后续成功尝试覆盖其修复状态。
            for node_run in expanded_nodes:
                if node_run.status == "success":
                    continue
                failed_attempts.append({
                    "node_run_id": node_run.id,
                    "checkpoints": verify_checkpoints(graph, old_graph, [node_run], baseline["service"]),
                })
        candidate = None
        document_id = (run.input_payload or {}).get("requirement_doc_id")
        if document_id is not None:
            arguments = {
                "project_id": run.project_id, "user_id": run.user_id,
                "workflow_key": workflow.workflow_key, "requirement_doc_id": int(document_id),
            }
            with document_query_count(db) as previous_queries:
                previous = old_service.get_generation_reuse_candidate(**arguments)
            with document_query_count(db) as current_queries:
                current = service.get_generation_reuse_candidate(**arguments)
            assert current == previous
            assert current_queries[0] <= 1
            candidate = {
                "status": current[1], "previous_document_queries": previous_queries[0],
                "current_document_queries": current_queries[0],
            }
            other_user = db.scalar(select(User.id).where(User.id != run.user_id).order_by(User.id).limit(1))
            if other_user is not None:
                denied = {**arguments, "user_id": other_user}
                assert service.get_generation_reuse_candidate(**denied) == (None, "project_not_found")
                assert old_service.get_generation_reuse_candidate(**denied) == (None, "project_not_found")
        results.append({
            "run_id": run.id, "node_count": len(nodes), "approval_count": len(approvals),
            "serialization_and_source_resolution": "passed", "summary_without_lazy_load": "passed",
            "restored_checkpoints": restored, "failed_attempts": failed_attempts,
            "reuse_candidate": candidate,
        })
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    baseline = load_baseline(options.baseline)
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        ids = db.scalars(select(AgentRun.id).order_by(AgentRun.id)).all()
        if not ids:
            raise ValueError("没有可验证的真实运行")
        runs = [db.get(AgentRun, run_id) for run_id in ids]
        assert all(run is not None for run in runs)
        report = {
            "run_count": len(runs), "runs": verify_runs(db, runs, baseline),
            "approval_records": db.query(AgentApproval).count(),
            "approval_rejection_replay": "not_tested_requires_real_pending_approval_copy",
            "online_transaction": "READ ONLY", "online_database_writes": 0, "model_calls": 0,
        }
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if options.output is not None:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(encoded + "\n", encoding="utf-8")
    print(json.dumps({
        "run_count": report["run_count"],
        "node_count": sum(item["node_count"] for item in report["runs"]),
        "checkpoint_count": sum(len(item["restored_checkpoints"]) for item in report["runs"]),
        "failed_attempts": [attempt for item in report["runs"] for attempt in item["failed_attempts"]],
        "reuse_candidates": [{"run_id": item["run_id"], **item["reuse_candidate"]}
                             for item in report["runs"] if item["reuse_candidate"] is not None],
        "approval_records": report["approval_records"],
        "online_database_writes": 0, "model_calls": 0,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
