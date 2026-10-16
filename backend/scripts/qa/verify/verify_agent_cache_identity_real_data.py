"""只读提取真实运行，验证执行身份及隔离 SQLite 行动绑定；不调用模型或写线上库。"""

from __future__ import annotations

import argparse
import asyncio
from copy import copy, deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import event, text

from core.ai.ai_client import get_client_for_user
from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun, AgentToolDefinition, AgentWorkflowDefinition
from modules.agent_platform.action_journal import ActionStateConflict, AgentActionJournal, action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks
from modules.agent_platform.contracts import WorkflowGraph, parse_execution_definition
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.lifecycle import new_claim_token
from modules.agent_platform.runtime import _agent_request_reservation, _project_agent_map_input
from modules.agent_platform.sdk_adapter import _build_sdk_agent
from modules.agent_platform.semantic_identity import (
    _model_contract,
    agent_execution_semantic_hash,
)
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS, BUILTIN_WORKFLOW_SPECS
from modules.agent_platform.test_generation_review_agent import initialize_review_state
from scripts.qa.verify.verify_lifecycle_sources_real_data import isolated_repository
from scripts.qa.verify.verify_review_agent_real_data import load_nodes, pending_budget, rejected, review_arguments
from scripts.qa.verify.verify_review_agent_real_model import builtin_definition


def verify_managed_identity(db, run, workflow, client) -> dict:
    """当前内置配置和真实专业 Agent 副本共同验证托管能力的完整身份。"""

    spec = next(item for item in BUILTIN_AGENT_SPECS if item["runtime_config"].get("managed_agent_keys"))
    root = builtin_definition(AgentDefinition, spec, project_id=run.project_id, user_id=run.user_id)
    tools = [builtin_definition(AgentToolDefinition, item, project_id=run.project_id, user_id=run.user_id)
             for item in BUILTIN_TOOL_SPECS if item["tool_key"] in root.runtime_config["tool_keys"]]
    current_graph = parse_execution_definition(next(
        item["definition"] for item in BUILTIN_WORKFLOW_SPECS if item["workflow_key"] == workflow.workflow_key
    ))
    node = next(item for item in current_graph.nodes if item.reference_key == root.agent_key)
    repo = AgentDefinitionRepository(db)
    snapshots = {root.agent_key: (root, tools)}
    for key in root.runtime_config["managed_agent_keys"]:
        child = repo.get_agent(project_id=run.project_id, agent_key=key)
        if child is None:
            raise ValueError(f"真实配置缺少专业 Agent: {key}")
        snapshots[key] = (deepcopy(child), deepcopy(repo.list_agent_tools(child.id, project_id=run.project_id)))
    kwargs = dict(db=db, user_id=run.user_id, project_id=run.project_id, node=node,
                  definition=root, tools=tools, client=client, definition_snapshots=snapshots)
    baseline = agent_execution_semantic_hash(**kwargs)
    assert root.id is None and all(tool.id is None for tool in tools)
    assert agent_execution_semantic_hash(**kwargs) == baseline
    assert agent_execution_semantic_hash(**{**kwargs, "definition_snapshots": None}) == baseline
    child_key = root.runtime_config["managed_agent_keys"][0]
    original_child = snapshots[child_key][0]
    donor = next(item for item in repo.list_agents(project_id=run.project_id)
                 if item.instructions != original_child.instructions and item.output_schema != original_child.output_schema)
    changed_hashes = {}
    for field in ("instructions", "output_schema"):
        changed = deepcopy(snapshots)
        setattr(changed[child_key][0], field, deepcopy(getattr(donor, field)))
        changed_hashes[field] = agent_execution_semantic_hash(**{**kwargs, "definition_snapshots": changed})
        assert changed_hashes[field] != baseline
    changed = deepcopy(snapshots)
    actual_model = _model_contract(client, original_child)[0]["model"]
    changed[child_key][0].model = next(str(value) for value in (
        client.model, client.turbo_model, client.review_model, client.vl_model,
    ) if value and str(value) != actual_model)
    changed_hashes["model"] = agent_execution_semantic_hash(**{**kwargs, "definition_snapshots": changed})
    assert changed_hashes["model"] != baseline
    changed = deepcopy(snapshots)
    changed[child_key] = (changed[child_key][0], [deepcopy(repo.list_tools(project_id=run.project_id)[0])])
    changed_hashes["tool_binding"] = agent_execution_semantic_hash(**{**kwargs, "definition_snapshots": changed})
    assert changed_hashes["tool_binding"] != baseline
    for key in ("state_initializer", "state_observer"):
        changed = deepcopy(snapshots)
        changed[root.agent_key][0].runtime_config.pop(key)
        assert agent_execution_semantic_hash(**{**kwargs, "definition_snapshots": changed}) != baseline
    for field in ("managed_agent_keys", "subagent_keys"):
        changed = deepcopy(snapshots)
        changed[root.agent_key][0].runtime_config[field] = [child_key, child_key]
        rejected(lambda: agent_execution_semantic_hash(**{**kwargs, "definition_snapshots": changed}), ValueError)
        changed = deepcopy(snapshots)
        changed[child_key][0].runtime_config[field] = [donor.agent_key]
        rejected(lambda: agent_execution_semantic_hash(**{**kwargs, "definition_snapshots": changed}), ValueError)
    missing = dict(snapshots)
    del missing[child_key]
    rejected(lambda: agent_execution_semantic_hash(**{**kwargs, "definition_snapshots": missing}), ValueError)

    nodes = load_nodes(db, run.id)
    arguments = review_arguments(run, nodes)
    context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                                   run_id=run.id, node_key=node.node_key, run_input=deepcopy(run.input_payload))
    state = initialize_review_state(context, arguments)
    source_node = nodes["prepare_final_review"]
    repair_input = nodes["prepare_final_review_repairs"].output_payload["items"][0]
    repair_definition = db.get(AgentDefinition, nodes["final_review_repairs"].agent_definition_id)
    binding_checks = []
    for phase in ("fresh", "reserved", "started", "charged"):
        with isolated_repository(workflow, run, source_node) as local:
            local_run = local.get_run(run_id=run.id)
            local_node = local.db.get(AgentNodeRun, source_node.id)
            token = new_claim_token()
            local_run.status, local_run.claim_token = "running", token
            local_run.lease_expires_at = datetime.utcnow() + timedelta(minutes=10)
            local_node.status, local_node.sdk_state = "running", {}
            local_node.input_payload = deepcopy(arguments)
            local.db.commit()
            journal = AgentActionJournal(local, run_id=run.id, node_run_id=source_node.id, expected_claim_token=token)
            journal.init(state=state, input_hash=action_state_hash(arguments))
            if phase == "reserved":
                reservation = _agent_request_reservation(definition=repair_definition, tools=[], input_payload=repair_input)
                journal.save_budget(AutonomousBudgetHooks(reservation).snapshot())
            elif phase == "started":
                journal.begin(tool_call_id=f"identity-replay-{source_node.id}", tool_key="repair_review_batch",
                              args={"batch_id": repair_input["review_batch"]["batch_id"]}, prehash=action_state_hash(state))
            elif phase == "charged":
                asyncio.run(pending_budget(repair_definition, repair_input, journal))
            before = journal.load()
            if phase in {"fresh", "reserved"}:
                bound = journal.bind_execution_identity(baseline)
                assert bound["execution_semantic_hash"] == baseline
                assert journal.bind_execution_identity(baseline) == bound
                for changed_hash in changed_hashes.values():
                    rejected(lambda: journal.bind_execution_identity(changed_hash), ActionStateConflict)
                    assert journal.load() == bound
            else:
                rejected(lambda: journal.bind_execution_identity(baseline), ActionStateConflict)
                assert journal.load() == before
            binding_checks.append(phase)
    return {"managed_child_changes_invalidate": list(changed_hashes),
            "transient_definition_and_tool_ids_supported": True, "state_handlers_included": True,
            "duplicate_nested_and_missing_children_rejected": True, "journal_identity_checks": binding_checks}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    options = parser.parse_args()

    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(AgentRun, options.run_id)
        if run is None or run.status != "success":
            raise ValueError("需要数据库中真实成功的运行记录")
        workflow = db.get(AgentWorkflowDefinition, run.workflow_definition_id)
        graph = parse_execution_definition(workflow.definition)
        if not isinstance(graph, WorkflowGraph):
            raise ValueError("需要包含真实 Agent 节点的 DAG 运行")
        repo = AgentDefinitionRepository(db)
        definitions = repo.list_agents(project_id=run.project_id)
        cache_nodes = []
        stable_nodes = []
        for node in graph.nodes:
            if node.node_type not in {"agent", "agent_map", "agent_network"}:
                continue
            definition = repo.get_agent(project_id=run.project_id, agent_key=node.reference_key)
            if definition is None or not isinstance((definition.runtime_config or {}).get("result_cache"), dict):
                continue
            tools = repo.list_agent_tools(definition.id, project_id=run.project_id)
            kwargs = dict(db=db, user_id=run.user_id, project_id=run.project_id, node=node, definition=definition, tools=tools)
            original = agent_execution_semantic_hash(**kwargs)
            assert original == agent_execution_semantic_hash(**kwargs)
            assert len(original) == 64
            cache_nodes.append((node, definition, tools))
            stable_nodes.append(node.node_key)
        if not cache_nodes:
            raise ValueError("真实工作流未配置结果缓存节点")
        node, definition, tools = next(item for item in cache_nodes if item[2])

        def fingerprint(*, agent=definition, current_node=node, current_tools=tools) -> str:
            return agent_execution_semantic_hash(
                db=db, user_id=run.user_id, project_id=run.project_id,
                node=current_node, definition=agent, tools=current_tools,
            )

        original = fingerprint()
        changed_fields = []
        for field in ("instructions", "output_schema"):
            donor = next(item for item in definitions if getattr(item, field) != getattr(definition, field))
            changed = deepcopy(definition)
            setattr(changed, field, deepcopy(getattr(donor, field)))
            assert fingerprint(agent=changed) != original, field
            changed_fields.append(field)

        client = get_client_for_user(run.user_id, db)
        actual_model = _model_contract(client, definition)[0]["model"]
        alternate_model = next(
            str(value) for value in (
                client.model, client.turbo_model, client.review_model, client.vl_model,
            ) if value and str(value) != actual_model
        )
        changed = deepcopy(definition)
        changed.model = alternate_model
        assert fingerprint(agent=changed) != original
        changed_fields.append("model")

        projection_owner = next(item for item in definitions if (item.runtime_config or {}).get("input_projection"))
        changed = deepcopy(definition)
        changed.runtime_config = deepcopy(dict(definition.runtime_config or {}))
        if changed.runtime_config.get("input_projection"):
            changed.runtime_config.pop("input_projection")
            changed.runtime_config.pop("input_projection_version", None)
        else:
            for key in ("input_projection", "input_projection_version"):
                changed.runtime_config[key] = deepcopy(projection_owner.runtime_config.get(key))
        assert fingerprint(agent=changed) != original
        changed_fields.append("input_projection")

        postprocessor_owner = next(
            item for item in definitions
            if (item.runtime_config or {}).get("output_postprocessor")
            and item.runtime_config["output_postprocessor"] != (definition.runtime_config or {}).get("output_postprocessor")
        )
        changed = deepcopy(definition)
        changed.runtime_config = deepcopy(dict(definition.runtime_config or {}))
        changed.runtime_config["output_postprocessor"] = postprocessor_owner.runtime_config["output_postprocessor"]
        assert fingerprint(agent=changed) != original
        changed_fields.append("output_postprocessor")

        all_tools = repo.list_tools(project_id=run.project_id)
        donor_tool = next(item for item in all_tools if item.handler_key != tools[0].handler_key)
        changed_tools = deepcopy(tools)
        changed_tools[0].handler_key = donor_tool.handler_key
        assert fingerprint(current_tools=changed_tools) != original
        changed_fields.append("tool_handler")

        different_node = next(item for item in graph.nodes if item.node_type == node.node_type and item.model_dump() != node.model_dump())
        assert fingerprint(current_node=different_node) != original
        changed_fields.append("node_contract")

        policy_only = deepcopy(definition)
        policy_only.runtime_config = deepcopy(dict(definition.runtime_config or {}))
        policy_only.runtime_config.pop("result_cache", None)
        assert fingerprint(agent=policy_only) == original

        def capture_sdk_snapshots(agent, bound_tools, current_node):
            """走真实 SDK 构建链捕获快照，构建完成立即关闭 HTTP 客户端，不发请求。"""
            runs = AgentRunRepository(db)
            source_node = runs.latest_node_run(run_id=run.id, node_key=current_node.node_key)
            source_input = runs.read_node_input(source_node)
            if current_node.map_config is not None:
                source_input = source_input[current_node.map_config.items_key][0]
            model_input, _ = _project_agent_map_input(definition=agent, raw_item=source_input)
            snapshots = {}
            allocated_clients = []
            try:
                _build_sdk_agent(
                    db=db, client=client, agent_definition=agent,
                    execution_context=ToolExecutionContext(
                        db=db, user_id=run.user_id, project_id=run.project_id,
                        run_id=run.id, node_key=current_node.node_key,
                        run_input=deepcopy(run.input_payload),
                    ),
                    allow_subagents=True, direct_tool_definitions=bound_tools,
                    trusted_input_payload=source_input, model_input_payload=model_input,
                    allocated_clients=allocated_clients, definition_snapshots=snapshots,
                )
            finally:
                async def close_clients():
                    for allocated_client in allocated_clients:
                        await allocated_client.close()
                asyncio.run(close_clients())
            return snapshots

        captured = capture_sdk_snapshots(definition, tools, node)
        snapshot_kwargs = dict(
            db=db, user_id=run.user_id, project_id=run.project_id,
            node=node, definition=definition, tools=tools,
            client=client, definition_snapshots=captured,
        )
        assert agent_execution_semantic_hash(**snapshot_kwargs) == original
        actual_root, actual_tools = captured[definition.agent_key]
        assert actual_root is not definition
        assert all(captured_tool is not original_tool for captured_tool, original_tool in zip(actual_tools, tools))
        # 即使调用方仍持有旧对象，实际请求身份也只能来自 SDK 捕获的快照。
        changed_root_argument = deepcopy(definition)
        changed_root_argument.instructions = next(item.instructions for item in definitions if item.instructions != definition.instructions)
        assert agent_execution_semantic_hash(**{
            **snapshot_kwargs, "definition": changed_root_argument, "tools": changed_tools,
        }) == original
        changed_snapshot = deepcopy(captured)
        changed_snapshot[definition.agent_key][0].instructions = changed_root_argument.instructions
        assert agent_execution_semantic_hash(**{
            **snapshot_kwargs, "definition_snapshots": changed_snapshot,
        }) != original

        changed_client = copy(client)
        route_attribute = {"main": "model", "turbo": "turbo_model", "review": "review_model", "vision": "vl_model"}[
            _model_contract(client, definition)[0]["route"]
        ]
        setattr(changed_client, route_attribute, alternate_model)
        assert agent_execution_semantic_hash(**{**snapshot_kwargs, "client": changed_client}) != original

        owner_node, owner = next(
            (candidate_node, candidate)
            for candidate_node in graph.nodes
            if candidate_node.node_type in {"agent", "agent_map", "agent_network"}
            and (candidate := repo.get_agent(project_id=run.project_id, agent_key=candidate_node.reference_key)) is not None
            and (candidate.runtime_config or {}).get("subagent_keys")
        )
        owner_tools = repo.list_agent_tools(owner.id, project_id=run.project_id)
        owner_kwargs = dict(
            db=db, user_id=run.user_id, project_id=run.project_id,
            node=owner_node, definition=owner, tools=owner_tools,
        )
        owner_baseline = agent_execution_semantic_hash(**owner_kwargs)
        owner_captured = capture_sdk_snapshots(owner, owner_tools, owner_node)
        query_count = 0

        def count_queries(*_args):
            nonlocal query_count
            query_count += 1

        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", count_queries)
        try:
            assert agent_execution_semantic_hash(
                **owner_kwargs, client=client, definition_snapshots=owner_captured,
            ) == owner_baseline
            child_key = next(key for key in owner_captured if key != owner.agent_key)
            changed_children = deepcopy(owner_captured)
            changed_children[child_key][0].instructions = definition.instructions
            assert changed_children[child_key][0].instructions != owner_captured[child_key][0].instructions
            assert agent_execution_semantic_hash(
                **owner_kwargs, client=client, definition_snapshots=changed_children,
            ) != owner_baseline
            missing_child = dict(owner_captured)
            del missing_child[child_key]
            try:
                agent_execution_semantic_hash(
                    **owner_kwargs, client=client, definition_snapshots=missing_child,
                )
            except ValueError as exc:
                assert "缺少实际构建的 Agent 快照" in str(exc)
            else:
                raise AssertionError("缺少子 Agent 快照时不得回读数据库")
        finally:
            event.remove(engine, "before_cursor_execute", count_queries)
        assert query_count == 0

        # 在客户端副本中移除真实认证信息；既不替换配置解析器，也不触发外部请求。
        no_credentials = copy(client)
        credential_values = []
        for name in ("provider", "turbo_provider", "review_provider", "vl_provider"):
            provider = getattr(client, name, None)
            if provider is None:
                continue
            provider_copy = copy(provider)
            if getattr(provider, "api_key", None):
                credential_values.append(str(provider.api_key))
            provider_copy.api_key = ""
            setattr(no_credentials, "_provider" if name == "provider" else name, provider_copy)
        model_identity = _model_contract(client, definition)
        assert _model_contract(no_credentials, definition) == model_identity
        serialized_identity = json.dumps(model_identity, ensure_ascii=False)
        assert "api_key" not in serialized_identity
        assert all(value not in serialized_identity for value in credential_values)
        assert agent_execution_semantic_hash(**{**snapshot_kwargs, "client": no_credentials}) == original
        managed_identity = verify_managed_identity(db, run, workflow, client)
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
        print(json.dumps({
            "run_id": options.run_id,
            "stable_cache_nodes": stable_nodes,
            "invalidated_by": changed_fields,
            "cache_policy_excluded": True,
            "credentials_excluded": True,
            "sdk_snapshot_equivalent": True,
            "sdk_snapshot_overrides_stale_arguments": True,
            "sdk_snapshot_changes_invalidate": ["root", "child", "actual_client"],
            "missing_child_snapshot_rejected": True,
            "snapshot_database_query_count": query_count,
            "managed_identity": managed_identity,
            "online_database_write_count": 0,
            "model_request_count": 0,
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
