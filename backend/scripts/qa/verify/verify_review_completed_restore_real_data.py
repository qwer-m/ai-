"""验证实际 SDK 完成回执恢复：支持历史结果回放或已完成真实模型报告，只读线上库。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from functools import partial
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.tool_context import ToolContext
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from core.ai.ai_client import get_client_for_user
from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentRun, AgentRunEvent, AgentToolDefinition, AgentWorkflowDefinition
from modules.agent_platform.action_journal import AgentActionJournal, action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks
from modules.agent_platform.autonomous_tools import AutonomousToolLoop
from modules.agent_platform.contracts import parse_execution_definition
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.request_diagnostics import ModelRequestDiagnostics
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _agent_request_reservation, _node_input
from modules.agent_platform.sdk_adapter import AgentOutputContract, _function_tool, run_agent_async
from modules.agent_platform.semantic_identity import agent_execution_semantic_hash
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS, BUILTIN_WORKFLOW_SPECS
from modules.agent_platform.test_generation_review_agent import observe_review_state
from scripts.qa.verify.verify_review_agent_real_data import load_nodes, replay_domain, review_arguments
from scripts.qa.verify.verify_review_agent_real_model import builtin_definition, isolated_journal, load_review_source


async def verify(run_id: int) -> dict:
    with SessionLocal() as online:
        online.execute(text("SET TRANSACTION READ ONLY"))
        run = online.get(AgentRun, run_id)
        if run is None or run.status != "success":
            raise ValueError("必须提供真实成功运行")
        workflow = online.get(AgentWorkflowDefinition, run.workflow_definition_id)
        nodes = load_nodes(online, run.id)
        arguments = review_arguments(run, nodes)
        domain, transitions, _ = replay_domain(online, run, nodes, arguments)
        graph_spec = next(item for item in BUILTIN_WORKFLOW_SPECS if item["workflow_key"] == workflow.workflow_key)
        graph = parse_execution_definition(graph_spec["definition"])
        node = next(item for item in graph.nodes if item.node_key == "resolve_final_review")
        raw_input = _node_input(run, node, {key: nodes[key].output_payload for key in node.depends_on})
        assert raw_input == arguments
        spec = next(item for item in BUILTIN_AGENT_SPECS if item["agent_key"] == node.reference_key)
        definition = builtin_definition(AgentDefinition, spec, project_id=run.project_id, user_id=run.user_id)
        config = dict(definition.runtime_config)
        tools_by_key = {item["tool_key"]: item for item in BUILTIN_TOOL_SPECS}
        tools = [builtin_definition(AgentToolDefinition, tools_by_key[key],
                                    project_id=run.project_id, user_id=run.user_id)
                 for key in config["tool_keys"]]
        definitions = AgentDefinitionRepository(online)
        snapshots = {definition.agent_key: deepcopy((definition, tools))}
        for key in config["managed_agent_keys"]:
            child = definitions.get_agent(project_id=run.project_id, agent_key=key)
            if child is None or not child.enabled:
                raise ValueError(f"真实专业 Agent 不可用: {key}")
            snapshots[key] = deepcopy((child, definitions.list_agent_tools(child.id, project_id=run.project_id)))
        client = get_client_for_user(run.user_id, online)
        semantic_hash = agent_execution_semantic_hash(
            db=online, user_id=run.user_id, project_id=run.project_id, node=node,
            definition=definition, tools=tools, client=client, definition_snapshots=snapshots,
        )
        context = ToolExecutionContext(
            db=online, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
            node_key=node.node_key, run_input=deepcopy(run.input_payload),
            action_input=deepcopy(raw_input), action_state=deepcopy(transitions[0]["before"]),
        )
        model_input = observe_review_state(context, {})
        reservation = _agent_request_reservation(definition=definition, tools=tools, input_payload=model_input)
        contract = AgentOutputContract(deepcopy(raw_input))
        with TemporaryDirectory(prefix="qoder-review-completed-restore-") as directory:
            path = Path(directory) / "actions.sqlite"
            with isolated_journal(
                path, run=run, workflow=workflow, source_node=nodes["final_review_batches"],
                graph_spec=graph_spec, node_spec=node, raw_input=raw_input,
                timeout=float(config["request_timeout_seconds"]),
            ) as (journal, local_node, token):
                node_run_id = local_node.id
                context.action_journal, context.claim_token = journal, token
                hooks = AutonomousBudgetHooks(reservation, on_change=journal.save_budget)
                journal.init(state=context.action_state, input_hash=action_state_hash(raw_input), budget=hooks.snapshot())
                journal.bind_execution_identity(semantic_hash)
                # 逐条重放真实领域处理器已经验收的历史转换，不替换 invoke_agent 或 Runner。
                for transition in transitions:
                    call_id = uuid4().hex
                    assert journal.load()["state"] == transition["before"]
                    journal.begin(
                        tool_call_id=call_id, tool_key=transition["tool_key"],
                        args={"arguments_json": json.dumps(transition["arguments"], ensure_ascii=False)},
                        prehash=action_state_hash(transition["before"]),
                    )
                    journal.complete(
                        tool_call_id=call_id, nextstate=transition["after"],
                        result={"output": json.dumps(transition["result"], ensure_ascii=False)},
                        budget=hooks.snapshot(),
                    )
                context.action_state = deepcopy(journal.load()["state"])
                assert context.action_state["completed"] is False
                ready = observe_review_state(context, {})
                assert ready["can_finish"] is True and ready["global_review_status"] == "approved"
                assert context.action_state["global_review"]["generation_sha256"] == context.action_state["generation_sha256"]
                assert len(context.action_state["reviewed_global_scope_hashes"]) == 1
                loop = AutonomousToolLoop(
                    terminal_tool_names=set(config["stop_at_tool_keys"]),
                    validate_submission=partial(contract.validate, definition, context),
                    recoverable_errors=(OutputValidationError,), max_tool_calls=reservation["attempted_requests"],
                    action_context=context, budget_snapshot=hooks.snapshot,
                )
                actual_tool = _function_tool(next(item for item in tools if item.tool_key == "finish_review"), context)
                finish_tool = loop.wrap_tool(actual_tool, allow_feedback=True)
                terminal_id = uuid4().hex
                await finish_tool.on_invoke_tool(ToolContext(
                    context=context, tool_name=finish_tool.name, tool_call_id=terminal_id, tool_arguments="{}",
                ), "{}")
                expected = loop.require_submission().normalized_output
                persisted = journal.load()
                assert persisted["receipts"][terminal_id]["status"] == "completed"
                assert persisted["state"]["completed"] is True
                assert expected["audit_summary"]["approved"] is True and expected["final_review"]["approved"] is True
                assert expected["generation"] == nodes["repaired_cases"].output_payload
                assert not any(persisted["budget"]["charged"].values())
                event_count = journal.repo.db.query(AgentRunEvent).count()
            # 原 SQLite 会话和 Engine 均已关闭；实际 SDK 只能依靠文件中的完成回执恢复。
            engine = create_engine("sqlite+pysqlite:///" + path.as_posix())
            try:
                with Session(engine, expire_on_commit=False) as reopened:
                    journal = AgentActionJournal(AgentRunRepository(reopened), run_id=run.id,
                                                 node_run_id=node_run_id, expected_claim_token=token)
                    saved = journal.load()
                    assert saved == persisted
                    restored_context = ToolExecutionContext(
                        db=online, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                        node_key=node.node_key, run_input=deepcopy(run.input_payload), claim_token=token,
                        action_input=deepcopy(raw_input), action_state=deepcopy(saved["state"]), action_journal=journal,
                    )
                    restored_hooks = AutonomousBudgetHooks(reservation, checkpoint=saved["budget"], on_change=journal.save_budget)
                    diagnostics = ModelRequestDiagnostics(journal=journal)
                    restored = await run_agent_async(
                        db=online, agent_definition=definition, tool_definitions=tools, execution_context=restored_context,
                        input_payload=observe_review_state(restored_context, {}), output_contract=contract,
                        request_timeout_seconds=float(config["request_timeout_seconds"]),
                        disable_server_output_schema=bool(config.get("disable_server_output_schema")),
                        request_budget=reservation, shared_budget_hooks=restored_hooks, execution_node=node, model_client=client,
                        request_diagnostics=diagnostics,
                    )
                    assert restored.output == restored.validated_output == expected
                    assert restored.budget_usage == saved["budget"] and diagnostics.requests == []
                    assert restored.model_requests == []
                    assert restored.usage == saved["budget"]["reported_usage"]
                    assert journal.load() == persisted and reopened.query(AgentRunEvent).count() == event_count
                    assert restored_context.action_state == persisted["state"]
                    assert [item["tool_key"] for item in restored.tool_observations] == ["finish_review"]
                    assert restored.tool_observations[0]["status"] == "accepted"
            finally:
                engine.dispose()
        assert not online.new and not online.dirty and not online.deleted
        online.rollback()
        return {
            "source_run_id": run_id, "execution_mode": "historical_results_replay_then_actual_sdk_completed_restore",
            "online_model_generation_completed": False, "history_domain_replay": domain,
            "history_actions_replayed": len(transitions), "completed_action_receipts": len(persisted["receipts"]),
            "actual_finish_function_tool_accepted": True, "sqlite_closed_and_reopened": True,
            "actual_sdk_run_agent_async_invoked": True, "execution_identity_unchanged": True,
            "current_global_approval_restored": True,
            "normalized_output_unchanged": True, "state_and_receipts_unchanged": True,
            "budget_unchanged": True, "events_unchanged": True, "model_requests_sent": 0,
            "budget_started_empty_and_remained_empty": True, "case_count": len(expected["generation"]["test_cases"]),
            "online_database_writes": 0, "mysql_concurrency_verified": False,
        }


async def verify_completed_model_report(source_report_path: Path, sqlite_path: Path) -> dict:
    """复制已完成检查点复验真实 SDK；保留原报告和原预算，不进入新的模型执行。"""
    source_bytes = source_report_path.read_bytes()
    source_report = json.loads(source_bytes)
    source_sqlite = Path(source_report["isolated_action_database"]).resolve()
    source_sqlite_sha256 = hashlib.sha256(source_sqlite.read_bytes()).hexdigest()
    assert source_report["execution_mode"] == "real_model_execution" and source_report["replay"] is False
    assert source_report["checkpoint"]["completed"] is True
    assert source_report["accepted_submission_count"] == 1
    expected = deepcopy(source_report["validated_output"])
    assert action_state_hash(expected) == source_report["validated_output_sha256"]
    with SessionLocal() as online:
        online.execute(text("SET TRANSACTION READ ONLY"))
        run = online.get(AgentRun, source_report["source_run_id"])
        workflow = online.get(AgentWorkflowDefinition, run.workflow_definition_id)
        graph_spec = next(item for item in BUILTIN_WORKFLOW_SPECS if item["workflow_key"] == workflow.workflow_key)
        node = next(item for item in parse_execution_definition(graph_spec["definition"]).nodes
                    if item.node_key == source_report["node_key"])
        raw_input, sources, _ = load_review_source(
            online, run, node, source_stage=source_report["source_stage"],
        )
        assert action_state_hash(raw_input) == source_report["raw_input_sha256"]
        assert {key: value.id for key, value in sources.items()} == source_report["source_node_ids"]
        spec = next(item for item in BUILTIN_AGENT_SPECS if item["agent_key"] == node.reference_key)
        definition = builtin_definition(AgentDefinition, spec, project_id=run.project_id, user_id=run.user_id)
        config = dict(definition.runtime_config)
        tool_specs = {item["tool_key"]: item for item in BUILTIN_TOOL_SPECS}
        tools = [builtin_definition(AgentToolDefinition, tool_specs[key],
                                    project_id=run.project_id, user_id=run.user_id)
                 for key in config["tool_keys"]]
        with isolated_journal(
            sqlite_path, run=run, workflow=workflow, source_node=sources["final_review_batches"],
            graph_spec=graph_spec, node_spec=node, raw_input=raw_input,
            timeout=float(config["request_timeout_seconds"]), resume_report=source_report,
        ) as (journal, _local_node, token):
            saved = journal.load()
            assert saved["state"]["completed"] is True
            assert saved["state_hash"] == source_report["checkpoint"]["state_hash"]
            assert saved["input_hash"] == source_report["checkpoint"]["input_hash"]
            assert saved["budget"] == source_report["checkpoint"]["budget"]
            assert saved["execution_semantic_hash"] == source_report["checkpoint"]["execution_semantic_hash"]
            assert len(saved["receipts"]) == source_report["checkpoint"]["receipt_count"]
            terminal = set(config["stop_at_tool_keys"])
            assert any(receipt["status"] == "completed" and receipt["tool_key"] in terminal
                       for receipt in saved["receipts"].values())
            event_count = journal.repo.db.query(AgentRunEvent).count()
            context = ToolExecutionContext(
                db=online, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                node_key=node.node_key, run_input=deepcopy(run.input_payload), claim_token=token,
                action_input=deepcopy(raw_input), action_state=deepcopy(saved["state"]), action_journal=journal,
            )
            hooks = AutonomousBudgetHooks(
                saved["budget"]["limits"], checkpoint=saved["budget"], on_change=journal.save_budget,
            )

            def reject_new_request(event: str, item: dict) -> None:
                # 观测钩子在发送请求前触发；离线复验一旦意外进入模型路径就立即终止。
                if event == "request_started":
                    raise RuntimeError("完成态回执复验不允许发起新模型请求")

            diagnostics = ModelRequestDiagnostics(journal=journal, on_event=reject_new_request)
            restored = await run_agent_async(
                db=online, agent_definition=definition, tool_definitions=tools, execution_context=context,
                input_payload=observe_review_state(context, {}),
                request_timeout_seconds=float(config["request_timeout_seconds"]),
                disable_server_output_schema=bool(config.get("disable_server_output_schema")),
                skip_output_postprocessor=True, output_contract=AgentOutputContract(deepcopy(raw_input), node.map_config),
                request_budget=saved["budget"]["limits"], shared_budget_hooks=hooks, execution_node=node,
                request_diagnostics=diagnostics,
            )
            assert diagnostics.requests == restored.model_requests == []
            assert restored.validated_output == expected
            assert restored.output == source_report["candidate"]
            assert restored.usage == saved["budget"]["reported_usage"] == source_report["model_usage"]
            assert restored.budget_usage == saved["budget"]
            assert journal.load() == saved and context.action_state == saved["state"]
            assert journal.repo.db.query(AgentRunEvent).count() == event_count
            assert [(item["tool_key"], item["status"]) for item in restored.tool_observations] == [("finish_review", "accepted")]
            report = {
                "status": "passed", "execution_mode": "actual_sdk_completed_model_checkpoint_restore",
                "source_report": str(source_report_path.resolve()),
                "source_report_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "source_report_status": source_report["status"], "source_scope": source_report["source_scope"],
                "source_run_id": run.id, "source_sqlite": str(source_sqlite),
                "source_sqlite_sha256": source_sqlite_sha256,
                "isolated_action_database": str(sqlite_path.resolve()),
                "source_checkpoint_completed": True, "source_global_approved": saved["state"]["global_review"]["output"]["approved"],
                "actual_sdk_run_agent_async_invoked": True, "additional_model_requests": len(diagnostics.requests),
                "reported_usage_scope": "cumulative_checkpoint", "reported_usage": restored.usage,
                "source_error_type": source_report.get("error_type"),
                "source_error_message": source_report.get("error_message"),
                "execution_identity_unchanged": True, "validated_output_unchanged": True,
                "validated_output_sha256": action_state_hash(restored.validated_output),
                "state_and_receipts_unchanged": True, "budget_unchanged": True, "events_unchanged": True,
                "case_count": len(expected["generation"]["test_cases"]), "online_database_writes": 0,
            }
        assert not online.new and not online.dirty and not online.deleted
        online.rollback()
    assert source_report_path.read_bytes() == source_bytes
    assert hashlib.sha256(source_sqlite.read_bytes()).hexdigest() == source_sqlite_sha256
    report["original_evidence_unchanged"] = True
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run-id", type=int)
    source.add_argument("--source-report", type=Path, help="在副本中复验已完成的真实模型报告，禁止新增模型请求")
    parser.add_argument("--report", type=Path)
    options = parser.parse_args()
    if options.source_report is not None:
        if options.report is None:
            parser.error("已完成模型报告复验必须指定新的 --report 路径")
        if options.report.exists() or options.report.with_suffix(".sqlite").exists():
            parser.error("复验报告或 SQLite 已存在；请指定新路径保留原证据")
        options.report.parent.mkdir(parents=True, exist_ok=True)
        report = asyncio.run(verify_completed_model_report(options.source_report, options.report.with_suffix(".sqlite")))
    else:
        report = asyncio.run(verify(options.run_id))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if options.report is not None:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
