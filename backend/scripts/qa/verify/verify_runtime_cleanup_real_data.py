"""只读回放真实运行配置、完成检查点与证据目录；不调用模型、不执行工具。"""

from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun, AgentWorkflowDefinition
from modules.agent_platform import context_compression, runtime
from modules.agent_platform.contracts import parse_execution_definition
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.sdk_adapter import effective_max_turns


def baseline_function(directory: Path, module: object, name: str):
    """只加载基线函数本身，避免已删除的旧入口影响独立前后比较。"""
    source = directory / "agent_platform" / Path(module.__file__).name
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
    namespace = dict(vars(module))
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
    return namespace[name]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--baseline-dir", type=Path, required=True)
    options = parser.parse_args()
    old_reservation = baseline_function(options.baseline_dir, runtime, "_agent_request_reservation")
    old_compress = baseline_function(options.baseline_dir, context_compression, "compress_evidence_catalog")

    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(AgentRun, options.run_id)
        if run is None or run.status != "success":
            raise ValueError("需要数据库中真实成功的运行记录")
        original_context = deepcopy(run.run_context)
        workflow = db.get(AgentWorkflowDefinition, run.workflow_definition_id)
        graph = parse_execution_definition(workflow.definition)
        definitions = AgentDefinitionRepository(db)
        nodes = db.query(AgentNodeRun).filter(
            AgentNodeRun.run_id == run.id, AgentNodeRun.node_type == "agent_map",
            AgentNodeRun.status == "success",
        ).all()
        assert nodes, "真实运行必须包含成功的映射节点"
        original_checkpoints = {
            node.id: deepcopy((node.input_payload, node.output_payload, node.sdk_state))
            for node in nodes
        }
        counts = {"reservations": 0, "completed_resume_copies": 0, "nonempty_failure_counts": 0}
        agent_ids: set[int] = set()
        subagent_owner = None
        for node_run in nodes:
            definition = db.get(AgentDefinition, node_run.agent_definition_id)
            tools = definitions.list_agent_tools(definition.id, project_id=run.project_id)
            agent_ids.add(definition.id)
            node = deepcopy(next(item for item in graph.nodes if item.node_key == node_run.node_key))
            node_input = AgentRunRepository(db).read_node_input(node_run)
            items = node_input[node.map_config.items_key]
            for item in items:
                arguments = {"definition": definition, "tools": tools, "input_payload": item}
                reservation = runtime._agent_request_reservation(**arguments)
                assert reservation == old_reservation(**arguments), node_run.id
                assert reservation["attempted_requests"] == effective_max_turns(
                    definition.runtime_config, has_tools=bool(tools),
                )
                counts["reservations"] += 1
            if (definition.runtime_config or {}).get("subagent_keys"):
                subagent_owner = (definition, items[0])

            # 仅将真实完成节点的配置副本切到顺序恢复；全部项已完成，不能进入模型或写入分支。
            assert len(node_run.output_payload[node.map_config.output_key]) == len(items)
            node.map_config.max_concurrency = 1
            output, state = runtime._execute_agent_map(
                repo=AgentRunRepository(db), run=run, node=node, node_run=deepcopy(node_run),
                definition=definition, model_metadata=node_run.sdk_state["model"], tools=tools,
                execution_context=ToolExecutionContext(
                    db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                    node_key=node.node_key, run_input=deepcopy(run.input_payload),
                ),
                node_input=node_input, previous=deepcopy(node_run),
            )
            assert output == node_run.output_payload
            for key in ("items", "usage", "attempted_requests", "failure_counts", "model", "last_agent_name"):
                assert state[key] == node_run.sdk_state[key], (node_run.id, key)
            counts["completed_resume_copies"] += 1
            counts["nonempty_failure_counts"] += bool(state["failure_counts"])
        assert counts["nonempty_failure_counts"], "需要真实失败后成功的检查点覆盖计数保留"
        assert subagent_owner is not None, "需要真实专业 Agent 工具声明"
        definition, item = subagent_owner
        # 移除直接工具的配置副本仍保留真实专业 Agent，验证两层采用同一工具能力定义。
        reservation = runtime._agent_request_reservation(definition=definition, tools=[], input_payload=item)
        assert reservation["attempted_requests"] == effective_max_turns(definition.runtime_config, has_tools=False)
        invalid = deepcopy(definition)
        invalid.runtime_config = {**invalid.runtime_config, "max_turns": 0}
        for operation in (
            lambda: effective_max_turns(invalid.runtime_config, has_tools=True),
            lambda: runtime._agent_request_reservation(definition=invalid, tools=[], input_payload=item),
        ):
            try:
                operation()
            except ValueError as exc:
                assert "max_turns 必须大于 0" in str(exc)
            else:
                raise AssertionError("真实配置副本注入的零轮次必须在两层同时拒绝")

        artifacts = run.run_context["artifacts"]
        catalog = deepcopy(artifacts["requirement_evidence"]["evidence_catalog"])
        original_catalog = deepcopy(catalog)
        configured_budget = artifacts["context_compression"]["max_tokens"]
        compression_checks = 0
        for enabled in (True, False):
            for budget in (configured_budget, 128, 32768):
                actual = context_compression.compress_evidence_catalog(catalog, enabled=enabled, max_tokens=budget)
                assert actual == old_compress(catalog, enabled=enabled, max_tokens=budget)
                compression_checks += 1
            # 在真实证据目录副本上注入零预算，验证入口归一化一致且原证据不丢失。
            output, stats = context_compression.compress_evidence_catalog(catalog, enabled=enabled, max_tokens=0)
            assert stats["max_tokens"] == context_compression.context_compression_max_tokens(
                {"context_compression_max_tokens": 0},
            ) == 128
            assert output["items"] == original_catalog["items"]
        assert catalog == original_catalog
        assert not db.new and not db.dirty and not db.deleted
        assert run.run_context == original_context
        assert all(
            (node.input_payload, node.output_payload, node.sdk_state) == original_checkpoints[node.id]
            for node in nodes
        )
        print(json.dumps({
            "run_id": run.id, "agent_definitions": len(agent_ids), **counts,
            "subagent_owner_id": definition.id, "invalid_max_turns_rejected": True,
            "compression_baseline_comparisons": compression_checks,
            "evidence_count": len(catalog["items"]), "zero_budget_normalized": 128,
            "database_unchanged": True, "model_called": False,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
