"""从真实来源与初始规划草案验证自主规划；默认仅准备，显式 --execute 才调用模型。"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from time import perf_counter
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from core.db.database import SessionLocal
from core.db.model_defs import (
    AgentApproval, AgentDefinition, AgentNodeRun, AgentRun, AgentRunEvent,
    AgentToolDefinition, AgentWorkflowDefinition,
)
from modules.agent_platform.action_journal import AgentActionJournal, action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks, AgentExecutionBudgetExceeded
from modules.agent_platform.contracts import parse_execution_definition
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.lifecycle import new_claim_token, transition_run
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.request_diagnostics import ModelRequestDiagnostics, safe_exception_evidence
from modules.agent_platform.registry import ToolExecutionContext, tool_registry
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _agent_request_reservation, _node_input
from modules.agent_platform.sdk_adapter import AgentOutputContract, resolve_agent_model_metadata, run_agent_async
from modules.agent_platform.sources import SOURCE_ARTIFACT_KEY
from modules.agent_platform.test_generation_builtin_specs import (
    BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS, BUILTIN_WORKFLOW_SPECS,
)
from scripts.qa.verify.verify_autonomous_generation_real_model import safe_observations
from scripts.qa.verify.verify_lifecycle_sources_real_data import copy_record, sqlite_longtext as _sqlite_longtext
from scripts.qa.verify.verify_review_agent_real_model import builtin_definition


@contextmanager
def planning_journal(path: Path, *, run, workflow, source_node, graph_spec, node_spec, raw_input, timeout,
                     verification="real_model_isolated_planning"):
    """仅复制真实记录到新 SQLite，初始化独立节点而不改变线上运行。"""
    if path.exists():
        raise FileExistsError("隔离行动数据库已存在，请使用新的报告路径")
    engine = create_engine("sqlite+pysqlite:///" + path.resolve().as_posix())
    try:
        for model in (AgentWorkflowDefinition, AgentRun, AgentNodeRun, AgentRunEvent, AgentApproval):
            model.__table__.create(engine)
        copied_workflow, copied_run, copied_node = copy_record(workflow), copy_record(run), copy_record(source_node)
        copied_workflow.definition = deepcopy(graph_spec["definition"])
        copied_run.status, copied_run.finished_at, copied_run.claim_token = "pending", None, None
        copied_run.current_node_key = node_spec.node_key
        copied_run.output_payload, copied_run.error_message = {}, ""
        copied_node.id = None
        copied_node.node_key, copied_node.node_type = node_spec.node_key, node_spec.node_type
        copied_node.agent_definition_id, copied_node.tool_definition_id = None, None
        copied_node.status, copied_node.attempt = "running", 1
        copied_node.started_at, copied_node.finished_at = datetime.utcnow(), None
        copied_node.input_payload = deepcopy(raw_input)
        copied_node.output_payload, copied_node.sdk_state, copied_node.error_message = {}, {}, ""
        token = new_claim_token()
        with Session(engine, expire_on_commit=False) as db:
            db.add_all([copied_workflow, copied_run, copied_node])
            db.commit()
            repo = AgentRunRepository(db)
            transition_run(repo, copied_run, "running", event_type="run_started", payload={
                "source_run_id": run.id, "verification": verification,
            })
            copied_run.claim_token = token
            stage_seconds = float(node_spec.time_budget_seconds or timeout)
            copied_run.lease_expires_at = datetime.utcnow() + timedelta(seconds=stage_seconds + 300)
            db.commit()
            yield AgentActionJournal(repo, run_id=run.id, node_run_id=copied_node.id, expected_claim_token=token), copied_node, token
    finally:
        engine.dispose()


def source_fingerprint(repo, node):
    return action_state_hash({
        "input": repo.read_node_input(node), "output": node.output_payload, "sdk_state": node.sdk_state,
        "status": node.status, "error_message": node.error_message,
    })


def checkpoint_evidence(journal):
    saved = journal.load()
    if saved is None:
        return {"initialized": False}
    receipts = []
    for value in saved["receipts"].values():
        item = {key: deepcopy(value[key]) for key in (
            "tool_call_id", "tool_key", "status", "result", "pre_state_hash", "post_state_hash",
        ) if key in value}
        if value.get("error"):
            item["error_type"] = value["error"].get("error_type")
        receipts.append(item)
    return {
        "initialized": True, "state_hash": saved["state_hash"], "input_hash": saved["input_hash"],
        "execution_semantic_hash": saved.get("execution_semantic_hash"),
        "completed": saved["state"].get("completed"),
        "versions": {key: saved["state"][key] for key in ("source_version", "draft_version", "plan_version") if key in saved["state"]},
        "evidence_reads": deepcopy(saved["state"].get("evidence_reads")),
        "state_bytes": len(json.dumps(saved["state"], ensure_ascii=False).encode()),
        "receipt_count": len(receipts), "receipts": receipts, "budget": deepcopy(saved["budget"]),
    }


async def verify(run_id: int, sqlite_path: Path, *, execute: bool) -> dict[str, Any]:
    report = {
        "execution_mode": "real_model_execution" if execute else "prepare_only_no_model",
        "source_mode": "original", "source_run_id": run_id,
        "independent_diagnostic_not_formal_workflow_run": True,
        "historical_downstream_feedback_injected": False,
        "forced_action_order": False, "forced_invalid_candidate": False,
        "status": "failed", "sdk_invoked": False,
        "mysql_transaction": "READ ONLY", "online_database_writes": 0,
        "isolated_action_database": str(sqlite_path.resolve()),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    started = perf_counter()
    context = hooks = diagnostics = None
    sources, source_hashes = {}, {}
    specialist_definitions, specialist_original_hashes = [], {}
    with SessionLocal() as db:
        try:
            db.execute(text("SET TRANSACTION READ ONLY"))
            repo = AgentRunRepository(db)
            run = repo.get_run(run_id=run_id)
            if run is None:
                raise ValueError("真实来源运行不存在")
            workflow = db.get(AgentWorkflowDefinition, run.workflow_definition_id)
            if workflow is None:
                raise ValueError("真实来源工作流不存在")
            graph_spec = next(item for item in BUILTIN_WORKFLOW_SPECS if item["workflow_key"] == workflow.workflow_key)
            graph = parse_execution_definition(graph_spec["definition"])
            node = next(item for item in graph.nodes if item.node_key == "resolve_planning")
            # 始终按当前正式节点映射读取原始上游产物，不拼入历史路由缺口或下游答案。
            sources = {key: repo.latest_node_run(run_id=run_id, node_key=key) for key in node.depends_on}
            if any(value is None or value.status != "success" for value in sources.values()):
                raise ValueError("来源运行缺少自主规划节点依赖的真实成功上游产物")
            source_hashes = {key: source_fingerprint(repo, value) for key, value in sources.items()}
            raw_input = deepcopy(_node_input(run, node, {key: value.output_payload for key, value in sources.items()}))
            original_run_hash = action_state_hash([run.input_payload, run.run_context, run.output_payload, run.status])
            original_artifacts = (run.run_context or {}).get("artifacts") or {}
            source_artifact_keys = (
                SOURCE_ARTIFACT_KEY, "requirement_evidence",
                "source_semantics_source_pages", "source_semantics_coordinate_maps",
            )
            artifacts = {key: deepcopy(original_artifacts[key]) for key in source_artifact_keys if key in original_artifacts}
            spec = next(item for item in BUILTIN_AGENT_SPECS if item["agent_key"] == node.reference_key)
            definition = builtin_definition(AgentDefinition, spec, user_id=run.user_id, project_id=run.project_id)
            config = dict(definition.runtime_config)
            tools_by_key = {item["tool_key"]: item for item in BUILTIN_TOOL_SPECS}
            tools = [builtin_definition(AgentToolDefinition, tools_by_key[key], user_id=run.user_id, project_id=run.project_id) for key in config["tool_keys"]]
            definitions = AgentDefinitionRepository(db)
            agents_by_key = {item["agent_key"]: item for item in BUILTIN_AGENT_SPECS}
            specialists = []
            for key in config["managed_agent_keys"]:
                child = definitions.get_agent(project_id=run.project_id, agent_key=key)
                if child is None or not child.enabled:
                    raise ValueError(f"真实专业 Agent 不可用: {key}")
                child_spec = agents_by_key[key]
                assert child.output_schema == child_spec["output_schema"], f"真实专业 Agent 输出契约与当前内置规格不同：{key}"
                original_fields = {field: deepcopy(getattr(child, field)) for field in (
                    "instructions", "runtime_config", "output_schema", "model",
                )}
                specialist_original_hashes[key] = action_state_hash(original_fields)
                # 只同步当前会话中的真实 ORM 对象，保留强引用供 SDK 仓储再次读取；不产生 UPDATE。
                set_committed_value(child, "instructions", deepcopy(child_spec["instructions"]))
                set_committed_value(child, "runtime_config", deepcopy(child_spec["runtime_config"]))
                specialist_definitions.append(child)
                specialists.append({
                    "agent_key": key, "agent_definition_id": child.id, "name": child.name,
                    "instructions_sha256": action_state_hash(child.instructions),
                    "runtime_config_sha256": action_state_hash(child.runtime_config),
                    "output_schema_sha256": action_state_hash(child.output_schema),
                    "model": resolve_agent_model_metadata(db=db, user_id=run.user_id, agent_definition=child),
                    "applied_current_builtin_override": {
                        "method": "set_committed_value_on_real_session_identity",
                        "fields": ["instructions", "runtime_config"],
                        "stored_definition_sha256": specialist_original_hashes[key],
                        "stored_instructions_sha256": action_state_hash(original_fields["instructions"]),
                        "stored_runtime_config_sha256": action_state_hash(original_fields["runtime_config"]),
                        "output_schema_unchanged": True, "explicit_model_unchanged": True,
                        "stored_model_route": original_fields["runtime_config"].get("model_route"),
                        "current_model_route": child.runtime_config.get("model_route"),
                    },
                    "current_builtin_matches": {
                        field: action_state_hash(getattr(child, field)) == action_state_hash(agents_by_key[key][field])
                        for field in ("instructions", "runtime_config", "output_schema")
                    },
                })
            timeout = float(config["request_timeout_seconds"])
            context = ToolExecutionContext(
                db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                node_key=node.node_key, run_input=deepcopy(run.input_payload), artifacts=artifacts,
                action_input=deepcopy(raw_input),
            )
            source_node = sources["plan_batches"]
            report.update({
                "source_run_status": run.status, "graph_node_count": len(graph.nodes), "node_key": node.node_key,
                "source_node_ids": {key: value.id for key, value in sources.items()},
                "source_node_snapshot_sha256": source_hashes,
                "raw_input_sha256": action_state_hash(raw_input), "raw_input_fields": sorted(raw_input),
                "source_artifact_sha256": {key: action_state_hash(value) for key, value in artifacts.items()},
                "source_artifact_keys": sorted(artifacts),
                "coordinator": {"agent_key": definition.agent_key, "instructions_sha256": action_state_hash(definition.instructions),
                                "runtime_config_sha256": action_state_hash(config),
                                "model": resolve_agent_model_metadata(db=db, user_id=run.user_id, agent_definition=definition)},
                "available_tools": [tool.tool_key for tool in tools], "specialists": specialists,
                "request_timeout_seconds": timeout,
            })
            assert all(all(item["current_builtin_matches"].values()) for item in specialists)
            assert not db.new and not db.dirty and not db.deleted
            with planning_journal(sqlite_path, run=run, workflow=workflow, source_node=source_node,
                                  graph_spec=graph_spec, node_spec=node, raw_input=raw_input, timeout=timeout) as (journal, isolated_node, token):
                context.action_journal, context.claim_token = journal, token
                try:
                    state = tool_registry.resolve(config["state_initializer"])(context, raw_input)
                    model_input = tool_registry.resolve(config["state_observer"])(context, {})
                    reservation = _agent_request_reservation(definition=definition, tools=tools, input_payload=model_input)
                    hooks = AutonomousBudgetHooks(reservation, on_change=journal.save_budget)
                    diagnostics = ModelRequestDiagnostics(journal=journal, on_event=lambda event, item: print(json.dumps({event: item}, ensure_ascii=False), flush=True))
                    journal.init(state=state, input_hash=action_state_hash(raw_input), budget=hooks.snapshot())
                    report.update({
                        "isolated_node_run_id": isolated_node.id, "model_input_sha256": action_state_hash(model_input),
                        "initial_observation": deepcopy(model_input), "request_reservation": reservation,
                    })
                    assert not db.new and not db.dirty and not db.deleted
                    if not execute:
                        report["status"] = "prepared"
                    else:
                        contract = AgentOutputContract(deepcopy(raw_input), node.map_config)
                        report["sdk_invoked"] = True
                        result = await run_agent_async(
                            db=db, agent_definition=definition, tool_definitions=tools, execution_context=context,
                            input_payload=model_input, request_timeout_seconds=timeout,
                            disable_server_output_schema=bool(config.get("disable_server_output_schema")),
                            skip_output_postprocessor=True, output_contract=contract,
                            request_budget=reservation, shared_budget_hooks=hooks, request_diagnostics=diagnostics,
                            execution_node=node,
                        )
                        observations = safe_observations(result.tool_observations)
                        report.update({
                            "tool_observations": observations, "tool_calls": deepcopy(result.tool_calls),
                            "candidate": deepcopy(result.output), "validated_output": deepcopy(result.validated_output),
                            "validated_output_sha256": action_state_hash(result.validated_output),
                        })
                        accepted = [item for item in observations if item.get("status") == "accepted"]
                        assert isinstance(result.validated_output, dict), "没有通过验收的完整规划产物"
                        assert len(accepted) == 1 and observations[-1] == accepted[0], "必须以唯一成功验收结束"
                        assert accepted[0]["tool_key"] in config["stop_at_tool_keys"]
                        assert contract.validate(definition, context, deepcopy(result.output)) == result.validated_output
                        persisted = journal.load()
                        assert persisted["state"] == context.action_state and persisted["state"]["completed"] is True
                        assert hooks.snapshot()["pending_requests"] == 0
                        report.update(status="passed", full_validation_rechecked=True)
                        # 关闭会话后恢复同一已完成检查点，验证不会再次调用模型或改变预算。
                        isolated_db = journal.repo.db
                        isolated_engine = isolated_db.get_bind()
                        isolated_db.close()
                        with Session(isolated_engine, expire_on_commit=False) as reopened_db:
                            restored_journal = AgentActionJournal(AgentRunRepository(reopened_db), run_id=run.id,
                                                                 node_run_id=journal.node_run_id, expected_claim_token=token)
                            restored_context = replace(context, action_journal=restored_journal,
                                                       action_state=deepcopy(persisted["state"]), tool_calls=[], executed_tools=[], invoke_agent=None)
                            restored = await run_agent_async(
                                db=db, agent_definition=definition, tool_definitions=tools, execution_context=restored_context,
                                input_payload=model_input, request_timeout_seconds=timeout,
                                disable_server_output_schema=bool(config.get("disable_server_output_schema")),
                                skip_output_postprocessor=True, output_contract=contract,
                                request_budget=reservation, execution_node=node,
                            )
                            assert restored.validated_output == result.validated_output
                            assert restored.model_requests == [] and restored.budget_usage == result.budget_usage
                            assert restored_journal.load() == persisted
                            report["completed_checkpoint_sdk_restore"] = {
                                "sqlite_session_reopened": True, "additional_model_requests": 0,
                                "validated_output_unchanged": True, "budget_unchanged": True, "checkpoint_unchanged": True,
                            }
                    assert action_state_hash(raw_input) == report["raw_input_sha256"]
                    assert all(action_state_hash(context.artifacts[key]) == value
                               for key, value in report["source_artifact_sha256"].items())
                    report["source_artifacts_unchanged"] = True
                finally:
                    report["checkpoint"] = checkpoint_evidence(journal)
            assert not db.new and not db.dirty and not db.deleted
            db.refresh(run)
            assert action_state_hash([run.input_payload, run.run_context, run.output_payload, run.status]) == original_run_hash
            report["source_run_unchanged"] = True
        except Exception as error:
            if diagnostics is not None:
                diagnostics.record_failure(error)
            diagnostic = dict(getattr(error, "diagnostic", None) or {})
            report.update({
                "status": "failed", "error_type": type(error).__name__, "exception_chain": safe_exception_evidence(error),
                "error_message": str(error)[:4000] if isinstance(error, (OutputValidationError, AgentExecutionBudgetExceeded, AssertionError, FileExistsError))
                                 else "平台异常详情未写入报告，以免包含认证或连接信息。",
                "tool_observations": safe_observations(diagnostic.get("tool_observations") or report.get("tool_observations") or []),
                "tool_calls": deepcopy(context.tool_calls) if context is not None else [],
                "orm_pending_changes": bool(db.new or db.dirty or db.deleted),
            })
        finally:
            if hooks is not None:
                report["budget_usage"] = hooks.snapshot()
                report["model_usage"] = hooks.snapshot()["reported_usage"]
            report["model_requests"] = diagnostics.requests if diagnostics is not None else []
            report["request_counts_by_agent_name"] = dict(Counter(item["agent_name"] for item in report["model_requests"]))
            statuses = Counter(item.get("status") for item in report.get("tool_observations", []))
            report["accepted_submission_count"], report["rejected_action_count"] = statuses["accepted"], statuses["rejected"]
            if source_hashes:
                try:
                    for key, value in sources.items():
                        db.refresh(value)
                        assert source_fingerprint(repo, value) == source_hashes[key]
                    report["source_node_records_unchanged"] = True
                except Exception as error:
                    report.update(status="failed", source_node_records_unchanged=False, source_verification_error_type=type(error).__name__)
            if specialist_definitions:
                try:
                    assert not db.new and not db.dirty and not db.deleted
                    for child in specialist_definitions:
                        db.refresh(child)
                        refreshed = {field: getattr(child, field) for field in (
                            "instructions", "runtime_config", "output_schema", "model",
                        )}
                        assert action_state_hash(refreshed) == specialist_original_hashes[child.agent_key]
                    report["online_specialist_definitions_unchanged"] = True
                except Exception as error:
                    report.update(status="failed", online_specialist_definitions_unchanged=False,
                                  specialist_verification_error_type=type(error).__name__)
            db.rollback()
    report["elapsed_seconds"] = round(perf_counter() - started, 3)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, default=31)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="执行真实模型；不指定时只验证真实来源和初始化状态")
    options = parser.parse_args()
    if options.output.exists() or options.output.with_suffix(".sqlite").exists():
        parser.error("报告或隔离行动数据库已存在，请指定新路径以保留证据")
    options.output.parent.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(verify(options.run_id, options.output.with_suffix(".sqlite"), execute=options.execute))
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report.get(key) for key in (
        "execution_mode", "source_mode", "source_run_id", "status", "sdk_invoked", "model_usage",
        "accepted_submission_count", "rejected_action_count", "error_type", "elapsed_seconds",
    )}, ensure_ascii=False))
    print(f"报告：{options.output.resolve()}")
    if report["status"] not in {"passed", "prepared"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
