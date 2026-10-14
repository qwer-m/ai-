"""只读回放真实工作流；非法场景仅在真实定义的副本上注入，不执行工作流。"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import select

from core.db.database import engine
from core.db.model_defs import AgentWorkflowDefinition
from modules.agent_platform.contracts import WorkflowDefinitionCreate, parse_execution_definition
from modules.agent_platform.registry import BUILTIN_WORKFLOW_SPECS


@pytest.fixture(scope="module")
def real_workflows() -> list[dict]:
    with engine.connect() as connection:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        rows = connection.execute(select(
            AgentWorkflowDefinition.project_id,
            AgentWorkflowDefinition.workflow_key,
            AgentWorkflowDefinition.name,
            AgentWorkflowDefinition.version,
            AgentWorkflowDefinition.definition,
        ).order_by(AgentWorkflowDefinition.id)).mappings().all()
    assert rows, "验证必须读取到真实 MySQL 工作流"
    records = [dict(row) for row in rows]
    projects = {row["workflow_key"]: row["project_id"] for row in records}
    for spec in BUILTIN_WORKFLOW_SPECS:
        records.append({
            "project_id": projects[spec["workflow_key"]],
            "workflow_key": spec["workflow_key"],
            "name": spec["name"],
            "version": spec.get("version", 1),
            "definition": deepcopy(spec["definition"]),
        })
    return records


@pytest.fixture(scope="module")
def generation_workflow(real_workflows: list[dict]) -> dict:
    return next(item for item in reversed(real_workflows) if item["workflow_key"] == "test_generation")


@pytest.mark.parametrize("omit_mode", [False, True])
def test_real_definitions_have_consistent_creation_and_loading(real_workflows: list[dict], omit_mode: bool) -> None:
    for record in real_workflows:
        payload = deepcopy(record)
        if omit_mode:
            if payload["definition"].get("execution_mode", "dag") != "dag":
                continue
            payload["definition"].pop("execution_mode", None)
        loaded = parse_execution_definition(payload["definition"])
        created = WorkflowDefinitionCreate.model_validate(payload).definition
        assert created.model_dump() == loaded.model_dump(), record["workflow_key"]
        assert parse_execution_definition(loaded) is loaded
        assert WorkflowDefinitionCreate.model_validate({**payload, "definition": loaded}).definition is loaded


def _invalid_definition(definition: dict, fault: str) -> dict:
    """注入副本错误，原始内置定义和数据库记录始终不变。"""
    value = deepcopy(definition)
    nodes = value["nodes"]
    first = nodes[0]
    missing_key = first["node_key"] + "__removed_for_validation"
    if fault == "duplicate_node":
        nodes.append(deepcopy(first))
    elif fault == "unknown_dependency":
        first.setdefault("depends_on", []).append(missing_key)
    elif fault == "self_dependency":
        first.setdefault("depends_on", []).append(first["node_key"])
    elif fault == "cycle":
        child = next(node for node in nodes if node.get("depends_on"))
        parent = next(node for node in nodes if node["node_key"] == child["depends_on"][0])
        parent.setdefault("depends_on", []).append(child["node_key"])
    elif fault == "missing_output":
        value["output_node_key"] = missing_key
    elif fault == "duplicate_stage":
        value["display_stages"].append(deepcopy(value["display_stages"][0]))
    elif fault == "unknown_stage_node":
        value["display_stages"][0]["node_keys"].append(missing_key)
    elif fault == "missing_mode_invalid_dag":
        value.pop("execution_mode", None)
        value.pop("output_node_key")
        value["entry_agent_key"] = next(node["reference_key"] for node in nodes if node["node_type"] in {"agent", "agent_map", "agent_network"})
    else:
        raise AssertionError(f"未知的副本错误类型：{fault}")
    return value


@pytest.mark.parametrize("fault", [
    "duplicate_node", "unknown_dependency", "self_dependency", "cycle",
    "missing_output", "duplicate_stage", "unknown_stage_node", "missing_mode_invalid_dag",
])
def test_invalid_real_definition_copies_are_rejected_by_both_entries(generation_workflow: dict, fault: str) -> None:
    original = deepcopy(generation_workflow)
    invalid = _invalid_definition(original["definition"], fault)
    with pytest.raises(ValueError):
        parse_execution_definition(invalid)
    with pytest.raises(ValueError):
        WorkflowDefinitionCreate.model_validate({**original, "definition": invalid})
    assert generation_workflow == original


def test_real_parallel_nodes_keep_declaration_order(generation_workflow: dict) -> None:
    definition = deepcopy(generation_workflow["definition"])
    # 仅倒转真实节点的声明顺序，不改变节点、依赖及工具引用。
    definition["nodes"].reverse()
    ordered = parse_execution_definition(definition).execution_order()
    positions = {node.node_key: index for index, node in enumerate(ordered)}
    assert len(positions) == len(definition["nodes"])
    for node in ordered:
        assert all(positions[key] < positions[node.node_key] for key in node.depends_on)
    by_dependencies: dict[frozenset[str], list[str]] = {}
    for node in definition["nodes"]:
        by_dependencies.setdefault(frozenset(node.get("depends_on", [])), []).append(node["node_key"])
    parallel_groups = [keys for keys in by_dependencies.values() if len(keys) > 1]
    assert parallel_groups, "真实定义需要包含依赖相同、可同时就绪的节点"
    for declared in parallel_groups:
        assert [node.node_key for node in ordered if node.node_key in declared] == declared


@pytest.mark.parametrize("fault", ["unknown_dependency", "self_dependency", "cycle"])
def test_mutating_real_graph_dependencies_fails_without_hanging(generation_workflow: dict, fault: str) -> None:
    invalid = _invalid_definition(generation_workflow["definition"], fault)
    code = """
import json, sys
from modules.agent_platform.contracts import parse_execution_definition
source = json.load(sys.stdin)
graph = parse_execution_definition(source['original'])
for node, changed in zip(graph.nodes, source['changed']['nodes']):
    node.depends_on[:] = changed.get('depends_on', [])
try:
    graph.execution_order()
except ValueError:
    raise SystemExit(0)
raise SystemExit('构造后修改依赖没有被拒绝')
"""
    # 子进程超时能捕获停滞检查被误删导致的死循环，避免阻塞整组验证。
    result = subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps({"original": generation_workflow["definition"], "changed": invalid}),
        text=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
