"""只读复制真实 Run，在隔离 SQLite 中验证输入去重、API 和续跑检查点。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun, AgentWorkflowDefinition
from modules.agent_platform.contracts import parse_execution_definition
from modules.agent_platform.input_snapshots import pack_node_input, restore_node_input
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.serialization import serialize_run
from modules.agent_platform.service import _restorable_checkpoints, _restore_checkpoint_node


def json_bytes(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def row_copy(row):
    """副本全部字段取自真实持久化记录，不构造业务样本。"""
    return type(row)(**{
        column.key: deepcopy(getattr(row, column.key)) for column in row.__table__.columns
    })


def checkpoint_summary(checkpoints):
    return [(item.node_run.node_key, item.has_repair_state, item.has_partial_map_output)
            for item in checkpoints]


def assert_rejected(node, source_nodes, reason: str) -> None:
    try:
        restore_node_input(node, source_nodes.get)
    except ValueError:
        return
    raise AssertionError(f"损坏的真实快照副本未被拒绝：{reason}")


def verify(run_id: int) -> dict:
    with SessionLocal() as source_db:
        source_db.execute(text("SET TRANSACTION READ ONLY"))
        source_repo = AgentRunRepository(source_db)
        source_run = source_repo.get_run(run_id=run_id)
        if source_run is None:
            raise ValueError(f"真实运行不存在：{run_id}")
        source_nodes = source_repo.list_node_runs(run_id=run_id)
        workflow = source_db.get(AgentWorkflowDefinition, source_run.workflow_definition_id)
        if workflow is None or not source_nodes:
            raise ValueError("真实运行缺少工作流或节点记录")
        graph = parse_execution_definition(workflow.definition)
        baseline_api = serialize_run(source_run, node_runs=source_nodes)
        baseline_checkpoints = checkpoint_summary(_restorable_checkpoints(
            execution=graph, node_runs=source_nodes,
        ))
        inputs = {node.id: source_repo.read_node_input(node) for node in source_nodes}
        copied_run = row_copy(source_run)
        copied_nodes = [row_copy(node) for node in source_nodes]
        original_storage = {
            "node_input_bytes": sum(json_bytes(value) for value in inputs.values()),
            "run_output_bytes": json_bytes(source_run.output_payload),
        }
        assert not source_db.new and not source_db.dirty and not source_db.deleted
        source_db.rollback()

    # 隔离数据库使用真实 SQLAlchemy 模型与 Repository，所有写入仅作用于此副本。
    isolated_engine = create_engine("sqlite:///:memory:")
    AgentRun.__table__.create(isolated_engine)
    AgentNodeRun.__table__.create(isolated_engine)
    started = perf_counter()
    with Session(isolated_engine) as db:
        output = dict(copied_run.output_payload or {})
        if "artifacts" in output:
            assert output["artifacts"] == copied_run.run_context.get("artifacts", {})
            output.pop("artifacts")
        copied_run.output_payload = output
        db.add(copied_run)
        db.flush()
        repo = AgentRunRepository(db)
        for node in copied_nodes:
            repo.write_node_input(node, inputs[node.id])
            db.add(node)
            db.flush()
        db.commit()

    with Session(isolated_engine) as db:
        repo = AgentRunRepository(db)
        run = repo.get_run(run_id=run_id)
        nodes = repo.list_node_runs(run_id=run_id)
        nodes_by_id = {node.id: node for node in nodes}
        for node in nodes:
            assert repo.read_node_input(node) == inputs[node.id], node.id
        assert serialize_run(run, node_runs=nodes) == baseline_api
        checkpoints = _restorable_checkpoints(execution=graph, node_runs=nodes)
        assert checkpoint_summary(checkpoints) == baseline_checkpoints
        packed_nodes = [node for node in nodes if "$agent_node_input" in node.input_payload]
        assert packed_nodes, "真实节点之间应存在可复用内容"
        packed_bytes = sum(json_bytes(node.input_payload) for node in nodes)
        output_bytes = json_bytes(run.output_payload)

        # 所有故障均来自真实紧凑快照的副本，只改动要验证的引用元数据。
        sample = packed_nodes[-1]
        wrong_run = row_copy(sample)
        wrong_run.input_payload["run_id"] += 1
        assert_rejected(wrong_run, nodes_by_id, "跨运行")
        wrong_hash = row_copy(sample)
        wrong_hash.input_payload["references"][0]["sha256"] = "0" * 64
        assert_rejected(wrong_hash, nodes_by_id, "错误内容摘要")
        missing_path = row_copy(sample)
        missing_path.input_payload["references"][0]["source_path"].append("不存在的快照路径")
        assert_rejected(missing_path, nodes_by_id, "不存在的路径")
        missing_node = row_copy(sample)
        missing_node.input_payload["references"][0]["node_run_id"] = max(nodes_by_id) + 1
        assert_rejected(missing_node, nodes_by_id, "不存在的来源节点")
        source_id = sample.input_payload["references"][0]["node_run_id"]
        cross_run_source = row_copy(nodes_by_id[source_id])
        cross_run_source.run_id += 1
        assert_rejected(sample, {**nodes_by_id, source_id: cross_run_source}, "跨运行来源")
        failed_source = row_copy(nodes_by_id[source_id])
        failed_source.status = "failed"
        assert_rejected(sample, {**nodes_by_id, source_id: failed_source}, "失败来源")
        modified_source = row_copy(nodes_by_id[source_id])
        modified_source.output_payload = {}
        assert_rejected(sample, {**nodes_by_id, source_id: modified_source}, "来源已改变")
        repeated_path = row_copy(sample)
        repeated_path.input_payload["references"].append(deepcopy(repeated_path.input_payload["references"][0]))
        assert_rejected(repeated_path, nodes_by_id, "重复引用")
        literal = deepcopy(inputs[sample.id])
        literal["$agent_node_input"] = "agent-node-input-v1"
        literal_node = row_copy(sample)
        literal_node.input_payload = pack_node_input(literal_node, literal, [])
        assert restore_node_input(literal_node) == literal

        # 直接运行生产续跑复制入口，在新 Run 内重建引用，并验证可独立删除父 Run。
        retry = row_copy(run)
        retry.id = None
        retry.parent_run_id = run.id
        retry.status = "pending"
        retry.output_payload = {}
        db.add(retry)
        db.flush()
        restored = [
            _restore_checkpoint_node(repo, checkpoint=item.node_run, run_id=retry.id)
            for item in checkpoints
        ]
        for previous, current in zip(checkpoints, restored, strict=True):
            assert repo.read_node_input(current) == inputs[previous.node_run.id]
            assert current.output_payload == previous.node_run.output_payload
            state = deepcopy(current.sdk_state)
            state.pop("checkpoint_restore")
            assert state == previous.node_run.sdk_state
            if "$agent_node_input" in current.input_payload:
                assert current.input_payload["run_id"] == retry.id
                for reference in current.input_payload["references"]:
                    assert db.get(AgentNodeRun, reference["node_run_id"]).run_id == retry.id
        restored_inputs = {node.id: repo.read_node_input(node) for node in restored}
        # 父运行删除只发生在隔离副本，用于确认无悬空跨运行引用。
        retry.parent_run_id = None
        db.query(AgentNodeRun).filter(AgentNodeRun.run_id == run_id).delete(synchronize_session=False)
        db.query(AgentRun).filter(AgentRun.id == run_id).delete(synchronize_session=False)
        db.commit()
        db.expire_all()
        for node in repo.list_node_runs(run_id=retry.id):
            assert repo.read_node_input(node) == restored_inputs[node.id]

        return {
            "source_run_id": run_id,
            "source_database_read_only": True,
            "validation_database": "isolated_sqlite_memory",
            "nodes_checked": len(nodes),
            "nodes_with_references": len(packed_nodes),
            "node_input_bytes_before": original_storage["node_input_bytes"],
            "node_input_bytes_after": packed_bytes,
            "node_input_reduction_ratio": round(1 - packed_bytes / original_storage["node_input_bytes"], 6),
            "run_output_bytes_before": original_storage["run_output_bytes"],
            "run_output_bytes_after": output_bytes,
            "api_response_equal": True,
            "checkpoint_plan_equal": True,
            "damaged_reference_copies_rejected": 8,
            "literal_marker_preserved": True,
            "restored_checkpoints": len(restored),
            "restored_run_independent_of_parent": True,
            "elapsed_seconds": round(perf_counter() - started, 3),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    report = verify(options.run_id)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
