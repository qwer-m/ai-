"""从真实初审或已完成分批复审产物验证自主终审；线上只读，检查点仅写隔离 SQLite。"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from time import perf_counter
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from core.db.database import SessionLocal
from core.db.model_defs import (
    AgentApproval, AgentDefinition, AgentNodeRun, AgentRun, AgentRunEvent,
    AgentToolDefinition, AgentWorkflowDefinition,
)
from modules.agent_platform.action_journal import AgentActionJournal, action_state_hash
from modules.agent_platform.autonomous_budget import (
    AutonomousBudgetHooks, AgentExecutionBudgetExceeded,
)
from modules.agent_platform.contracts import parse_execution_definition
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.lifecycle import new_claim_token, transition_run
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.request_diagnostics import ModelRequestDiagnostics, safe_exception_evidence
from modules.agent_platform.registry import ToolExecutionContext, tool_registry
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _agent_request_reservation, _node_input
from modules.agent_platform.sdk_adapter import AgentOutputContract, resolve_agent_model_metadata, run_agent_async
from modules.agent_platform.test_generation_builtin_specs import (
    BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS, BUILTIN_WORKFLOW_SPECS,
)
from modules.agent_platform.test_generation_review import (
    merge_final_review_recheck_records, refresh_final_review_input,
)
from scripts.qa.verify.verify_autonomous_generation_real_model import safe_observations
from scripts.qa.verify.verify_lifecycle_sources_real_data import copy_record, sqlite_longtext as _sqlite_longtext


def builtin_definition(model, spec: dict[str, Any], **identity: Any):
    """按当前内置规格构造未入库 ORM 定义，不执行 seed 或覆盖线上配置。"""
    columns = {column.key for column in model.__table__.columns}
    values = {key: deepcopy(value) for key, value in spec.items() if key in columns}
    values.update(identity, enabled=True, builtin=True)
    return model(**values)


@contextmanager
def isolated_journal(path: Path, *, run, workflow, source_node, graph_spec, node_spec, raw_input, timeout,
                     resume_report: dict[str, Any] | None = None):
    if path.exists():
        raise FileExistsError("隔离行动数据库已存在；请指定新的报告路径以保留既有执行证据")
    if resume_report is not None:
        source_path = Path(resume_report["isolated_action_database"]).resolve()
        if not source_path.is_file() or source_path == path.resolve():
            raise ValueError("恢复需要独立且存在的来源 SQLite 数据库")
        # SQLite backup 会复制一致的数据库快照；来源连接明确只读，原证据不参与后续执行。
        with sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(path.resolve()) as destination:
                source.backup(destination)
    engine = create_engine("sqlite+pysqlite:///" + path.resolve().as_posix())
    try:
        if resume_report is not None:
            token = new_claim_token()
            with Session(engine, expire_on_commit=False) as db:
                repo = AgentRunRepository(db)
                copied_run = db.get(AgentRun, run.id)
                copied_node = db.get(AgentNodeRun, int(resume_report["isolated_node_run_id"]))
                if (copied_run is None or copied_node is None or copied_node.run_id != run.id
                        or copied_node.node_key != node_spec.node_key or copied_node.node_type != node_spec.node_type
                        or copied_run.status != "running" or copied_node.status != "running"):
                    raise ValueError("来源行动数据库不是本次工作流节点的可恢复运行")
                saved_input = repo.read_node_input(copied_node)
                if action_state_hash(saved_input) != action_state_hash(raw_input):
                    raise ValueError("来源节点原始输入与当前真实上游产物不一致，不能续跑")
                if action_state_hash(saved_input) != resume_report["raw_input_sha256"]:
                    raise ValueError("来源节点输入与原报告指纹不一致，不能续跑")
                # 只在新副本中认领租约，不重置节点状态、动作回执、已消费预算或执行身份。
                copied_run.claim_token = token
                copied_run.heartbeat_at = datetime.utcnow()
                copied_run.lease_expires_at = datetime.utcnow() + timedelta(seconds=timeout + 300)
                db.commit()
                yield AgentActionJournal(
                    repo, run_id=run.id, node_run_id=copied_node.id, expected_claim_token=token,
                ), copied_node, token
            return
        for model in (AgentWorkflowDefinition, AgentRun, AgentNodeRun, AgentRunEvent, AgentApproval):
            model.__table__.create(engine)
        copied_workflow = copy_record(workflow)
        copied_workflow.definition = deepcopy(graph_spec["definition"])
        copied_run, copied_node = copy_record(run), copy_record(source_node)
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
                "source_run_id": run.id, "verification": "real_model_isolated_review",
            })
            copied_run.claim_token = token
            copied_run.lease_expires_at = datetime.utcnow() + timedelta(seconds=timeout + 300)
            db.commit()
            yield AgentActionJournal(
                repo, run_id=run.id, node_run_id=copied_node.id, expected_claim_token=token,
            ), copied_node, token
    finally:
        engine.dispose()


def checkpoint_evidence(journal: AgentActionJournal) -> dict[str, Any]:
    saved = journal.load()
    if saved is None:
        return {"initialized": False}
    receipts = []
    for value in saved["receipts"].values():
        # 原始平台异常可能含连接细节；报告只留下错误类型，SQLite 保留原行动事务。
        item = {key: deepcopy(value[key]) for key in (
            "tool_call_id", "tool_key", "status", "result", "pre_state_hash", "post_state_hash",
        ) if key in value}
        if value.get("error"):
            item["error_type"] = value["error"].get("error_type")
        receipts.append(item)
    return {
        "initialized": True, "state_hash": saved["state_hash"], "input_hash": saved["input_hash"],
        "execution_semantic_hash": saved.get("execution_semantic_hash"),
        "completed": saved["state"]["completed"],
        "generation_version": saved["state"]["generation_version"],
        "state_bytes": len(json.dumps(saved["state"], ensure_ascii=False).encode("utf-8")),
        "receipt_count": len(receipts), "receipts": receipts,
        "budget": deepcopy(saved["budget"]),
    }


def load_review_source(db: Session, run: AgentRun, node, *, source_stage: str = "initial_review") -> tuple[
    dict[str, Any], dict[str, AgentNodeRun], dict[str, Any],
]:
    """只沿真实成功节点重建领域输入；完成分批审查是独立验证来源，不是失败预算续跑。"""
    if source_stage not in {"initial_review", "completed_batch_review"}:
        raise ValueError("未知的真实审查来源阶段")
    repo = AgentRunRepository(db)
    sources = {key: repo.latest_node_run(run_id=run.id, node_key=key) for key in node.depends_on}
    if any(value is None or value.status != "success" for value in sources.values()):
        raise ValueError("真实运行缺少新自主终审阶段依赖的成功产物")
    raw_input = deepcopy(_node_input(run, node, {key: value.output_payload for key, value in sources.items()}))
    initial_input_hash = action_state_hash(raw_input)
    generation_key = "generated_cases"
    reconstruction = None
    if source_stage == "completed_batch_review":
        for key in ("repaired_cases", "prepare_final_review_rechecks", "final_review_rechecks"):
            source = repo.latest_node_run(run_id=run.id, node_key=key)
            if source is None or source.status != "success":
                raise ValueError(f"真实完成分批审查缺少成功来源节点: {key}")
            sources[key] = source
        generation_key = "repaired_cases"
        generation = deepcopy(sources[generation_key].output_payload)
        replacements = deepcopy(sources["prepare_final_review_rechecks"].output_payload["items"])
        replacement_records = deepcopy(sources["final_review_rechecks"].output_payload["items"])
        # 复审结果必须对应最终持久化用例，不能用不同版本的通过结论跨版本拼接。
        for item in replacements:
            refreshed = refresh_final_review_input(
                item, generation=generation, generation_inputs=raw_input["generation_inputs"],
            )
            if any(item[key] != refreshed[key] for key in ("test_cases", "case_fact_bindings")):
                raise ValueError("历史分批复审输入未对应持久化 repaired_cases 版本")
        context = ToolExecutionContext(
            db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
            node_key=node.node_key, run_input=deepcopy(run.input_payload),
        )
        merged = merge_final_review_recheck_records(context, {
            "baseline_inputs": raw_input["review_inputs"], "baseline_records": raw_input["review_records"],
            "replacement_inputs": replacements, "replacement_records": replacement_records,
        })
        raw_input["generation"] = generation
        raw_input["review_inputs"] = [refresh_final_review_input(
            item, generation=generation, generation_inputs=raw_input["generation_inputs"],
        ) for item in raw_input["review_inputs"]]
        raw_input["review_records"] = merged["items"]
        reconstruction = {
            "initial_review_input_sha256": initial_input_hash,
            "replacement_review_input_sha256": action_state_hash(replacements),
            "replacement_review_records_sha256": action_state_hash(replacement_records),
            "replacement_review_count": len(replacements),
            "merged_review_count": len(raw_input["review_records"]),
            "generation_matches_persisted_repaired_cases": raw_input["generation"] == sources[generation_key].output_payload,
        }
    return raw_input, sources, {
        "source_generation": {
            "node_key": generation_key, "node_run_id": sources[generation_key].id,
            "output_sha256": action_state_hash(raw_input["generation"]),
            "case_count": len(raw_input["generation"]["test_cases"]),
            "binding_count": len(raw_input["generation"]["case_fact_bindings"]),
        },
        "source_review_reconstruction": reconstruction,
    }


async def verify(run_id: int, sqlite_path: Path, *, resume_from_report: Path | None = None,
                 source_stage: str = "initial_review") -> dict[str, Any]:
    if source_stage not in {"initial_review", "completed_batch_review"}:
        raise ValueError("未知的真实审查来源阶段")
    if source_stage == "completed_batch_review" and resume_from_report is not None:
        raise ValueError("已完成分批审查来源只能启动独立验证，不能与失败运行续跑组合")
    report: dict[str, Any] = {
        "execution_mode": "real_model_execution", "replay": False, "source_run_id": run_id,
        "source_stage": source_stage,
        "source_scope": "completed_batch_review_stage" if source_stage == "completed_batch_review" else "complete_initial_review_stage",
        "status": "failed", "sdk_invoked": False,
        "mysql_transaction": "READ ONLY", "online_database_writes": 0,
        "isolated_action_database": str(sqlite_path.resolve()),
        "forced_action_order": False, "forced_invalid_candidate": False,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    started = perf_counter()
    context = None
    hooks = None
    diagnostics = None
    resume_report = None
    resume_source_path = None
    prior_budget = None
    with SessionLocal() as db:
        try:
            if resume_from_report is not None:
                source_report_bytes = resume_from_report.read_bytes()
                resume_report = json.loads(source_report_bytes)
                if (resume_report.get("source_run_id") != run_id
                        or resume_report.get("execution_mode") != "real_model_execution"
                        or resume_report.get("replay") is not False
                        or resume_report.get("source_scope") != report["source_scope"]):
                    raise ValueError("续跑报告必须来自同一真实运行的模型验证")
                resume_source_path = Path(resume_report["isolated_action_database"]).resolve()
                report["resumed_execution"] = {
                    "source_report": str(resume_from_report.resolve()),
                    "source_report_sha256": hashlib.sha256(source_report_bytes).hexdigest(),
                    "source_sqlite": str(resume_source_path),
                    "source_sqlite_sha256": hashlib.sha256(resume_source_path.read_bytes()).hexdigest(),
                    "copied_with_sqlite_backup": True,
                    "state_and_receipts_restored": False,
                }
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
            node = next(item for item in graph.nodes if item.node_key == "resolve_final_review")
            raw_input, sources, source_evidence = load_review_source(db, run, node, source_stage=source_stage)
            report.update(source_evidence)
            report["source_node_ids"] = {key: value.id for key, value in sources.items()}
            spec = next(item for item in BUILTIN_AGENT_SPECS if item["agent_key"] == node.reference_key)
            definition = builtin_definition(AgentDefinition, spec, user_id=run.user_id, project_id=run.project_id)
            config = dict(definition.runtime_config)
            tools_by_key = {item["tool_key"]: item for item in BUILTIN_TOOL_SPECS}
            tools = [builtin_definition(
                AgentToolDefinition, tools_by_key[key], user_id=run.user_id, project_id=run.project_id,
            ) for key in config["tool_keys"]]
            definitions = AgentDefinitionRepository(db)
            specialists = []
            for key in config["managed_agent_keys"]:
                child = definitions.get_agent(project_id=run.project_id, agent_key=key)
                if child is None or not child.enabled:
                    raise ValueError(f"真实专业 Agent 不可用: {key}")
                specialists.append({
                    "agent_key": key, "agent_definition_id": child.id, "name": child.name,
                    "instructions_sha256": action_state_hash(child.instructions),
                    "runtime_config_sha256": action_state_hash(child.runtime_config),
                    "output_schema_sha256": action_state_hash(child.output_schema),
                    "model": resolve_agent_model_metadata(db=db, user_id=run.user_id, agent_definition=child),
                    "request_timeout_seconds": float((child.runtime_config or {}).get("request_timeout_seconds") or 180),
                })
            timeout = float(config["request_timeout_seconds"])
            context = ToolExecutionContext(
                db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                node_key=node.node_key, run_input=deepcopy(run.input_payload), action_input=deepcopy(raw_input),
            )
            with isolated_journal(
                sqlite_path, run=run, workflow=workflow, source_node=sources["final_review_batches"],
                graph_spec=graph_spec, node_spec=node, raw_input=raw_input, timeout=timeout,
                resume_report=resume_report,
            ) as (journal, isolated_node, token):
                context.action_journal, context.claim_token = journal, token
                try:
                    saved = journal.load()
                    if resume_report is not None:
                        if saved is None:
                            raise ValueError("来源节点缺少行动检查点，不能重置为全新执行")
                        if not saved.get("execution_semantic_hash"):
                            raise ValueError("来源检查点缺少既有执行身份，不能作为持久执行续跑")
                        previous = resume_report["checkpoint"]
                        if (saved["state_hash"] != previous["state_hash"]
                                or saved["input_hash"] != previous["input_hash"]
                                or saved["budget"] != previous["budget"]
                                or len(saved["receipts"]) != previous["receipt_count"]):
                            raise ValueError("来源 SQLite 检查点与原报告不一致，不能续跑")
                    context.action_state = deepcopy(saved["state"]) if saved is not None else {}
                    state = tool_registry.resolve(config["state_initializer"])(context, raw_input)
                    model_input = tool_registry.resolve(config["state_observer"])(context, {})
                    if source_stage == "completed_batch_review":
                        assert state["global_review"] is None and state["completed"] is False
                        assert state["audit_summary"]["approved"] is True
                        assert not state["pending_rechecks"] and not model_input["repairable_batch_ids"]
                        assert all(record["output"]["approved"] and not record["output"]["differences"]
                                   for record in state["review_records"])
                        assert model_input["can_review_all_cases"] is True and model_input["can_finish"] is False
                        report["source_batch_review_fully_validated"] = True
                    # 恢复后的观察摘要可能更短；必须沿用首次预留，不能重算额度或退回未知请求。
                    reservation = deepcopy(saved["budget"]["limits"]) if saved is not None else _agent_request_reservation(
                        definition=definition, tools=tools, input_payload=model_input,
                    )
                    prior_budget = deepcopy(saved["budget"]) if saved is not None else None
                    hooks = AutonomousBudgetHooks(
                        reservation, on_change=journal.save_budget, checkpoint=prior_budget,
                    )
                    diagnostics = ModelRequestDiagnostics(
                        journal=journal,
                        on_event=lambda event, item: print(json.dumps({event: item}, ensure_ascii=False), flush=True),
                    )
                    journal.init(state=state, input_hash=action_state_hash(raw_input), budget=hooks.snapshot())
                    if saved is not None:
                        restored_budget = hooks.snapshot()
                        assert restored_budget["charged"] == prior_budget["charged"]
                        assert restored_budget["reported_usage"] == prior_budget["reported_usage"]
                        assert restored_budget["pending_requests"] == 0
                        assert restored_budget["interrupted_requests"] == prior_budget["interrupted_requests"] + prior_budget["pending_requests"]
                        journal.save_budget(restored_budget)
                        report["resumed_execution"].update({
                            "state_and_receipts_restored": True, "initial_state_hash": saved["state_hash"],
                            "initial_generation_version": state["generation_version"],
                            "initial_receipt_count": len(saved["receipts"]),
                            "execution_semantic_hash": saved["execution_semantic_hash"],
                            "previous_budget": prior_budget, "restored_budget": restored_budget,
                            "charged_budget_preserved": True, "lease_reclaimed_in_copy": True,
                        })
                    contract = AgentOutputContract(deepcopy(raw_input), node.map_config)
                    report.update({
                        "graph_node_count": len(graph.nodes), "node_key": node.node_key,
                        "source_node_ids": {key: value.id for key, value in sources.items()},
                        "isolated_node_run_id": isolated_node.id,
                        "raw_input_sha256": action_state_hash(raw_input),
                        "model_input_sha256": action_state_hash(model_input),
                        "initial_observation": deepcopy(model_input), "request_reservation": reservation,
                        "request_timeout_seconds": timeout,
                        "coordinator": {
                            "agent_key": definition.agent_key,
                            "instructions_sha256": action_state_hash(definition.instructions),
                            "runtime_config_sha256": action_state_hash(config),
                            "model": resolve_agent_model_metadata(db=db, user_id=run.user_id, agent_definition=definition),
                        },
                        "available_tools": [tool.tool_key for tool in tools], "specialists": specialists,
                    })
                    assert not db.new and not db.dirty and not db.deleted
                    report["sdk_invoked"] = True
                    result = await run_agent_async(
                        db=db, agent_definition=definition, tool_definitions=tools, execution_context=context,
                        input_payload=model_input, request_timeout_seconds=timeout,
                        disable_server_output_schema=bool(config.get("disable_server_output_schema")),
                        skip_output_postprocessor=True, output_contract=contract,
                        request_budget=reservation, shared_budget_hooks=hooks,
                        request_diagnostics=diagnostics,
                        execution_node=node,
                    )
                    observations = safe_observations(result.tool_observations)
                    report.update({
                        "tool_observations": observations, "tool_calls": deepcopy(result.tool_calls),
                        "model_usage": dict(result.usage), "budget_usage": deepcopy(result.budget_usage),
                        "candidate": deepcopy(result.output), "validated_output": deepcopy(result.validated_output),
                        "validated_output_sha256": action_state_hash(result.validated_output),
                    })
                    accepted = [item for item in observations if item.get("status") == "accepted"]
                    assert isinstance(result.validated_output, dict), "没有完整验收的标准化产物"
                    assert len(accepted) == 1 and observations[-1] == accepted[0], "必须以唯一成功验收结束"
                    assert accepted[0]["tool_key"] == "finish_review"
                    assert contract.validate(definition, context, deepcopy(result.output)) == result.validated_output
                    assert result.validated_output["audit_summary"]["approved"] is True
                    assert result.validated_output["final_review"]["approved"] is True
                    assert not result.validated_output["final_review"]["differences"]
                    assert journal.load()["state"] == context.action_state
                    if saved is not None:
                        resumed_checkpoint = journal.load()
                        assert resumed_checkpoint["execution_semantic_hash"] == saved["execution_semantic_hash"]
                        assert all(
                            resumed_checkpoint["receipts"].get(key) == value
                            for key, value in saved["receipts"].items() if value["status"] != "started"
                        )
                        report["resumed_execution"].update({
                            "execution_identity_unchanged": True, "previous_terminal_receipts_unchanged": True,
                        })
                    assert not db.new and not db.dirty and not db.deleted
                    assert action_state_hash(raw_input) == report["raw_input_sha256"]
                    report.update(status="passed", full_validation_rechecked=True,
                                  case_count=len(result.validated_output["generation"]["test_cases"]))
                    # 关闭行动数据库会话，再经实际 SDK 恢复已完成提交；不得再次请求模型。
                    persisted = journal.load()
                    isolated_db = journal.repo.db
                    isolated_engine = isolated_db.get_bind()
                    isolated_db.close()
                    with Session(isolated_engine, expire_on_commit=False) as reopened_db:
                        restored_journal = AgentActionJournal(
                            AgentRunRepository(reopened_db), run_id=run.id,
                            node_run_id=journal.node_run_id, expected_claim_token=token,
                        )
                        restored_context = replace(
                            context, action_journal=restored_journal,
                            action_state=deepcopy(persisted["state"]), tool_calls=[], executed_tools=[],
                            invoke_agent=None,
                        )
                        restored = await run_agent_async(
                            db=db, agent_definition=definition, tool_definitions=tools,
                            execution_context=restored_context, input_payload=model_input,
                            request_timeout_seconds=timeout,
                            disable_server_output_schema=bool(config.get("disable_server_output_schema")),
                            skip_output_postprocessor=True, output_contract=contract,
                            request_budget=reservation, execution_node=node,
                        )
                        assert restored.validated_output == result.validated_output
                        assert restored.budget_usage == result.budget_usage
                        assert restored.model_requests == []
                        assert restored.usage == persisted["budget"]["reported_usage"] == result.usage
                        assert restored_journal.load() == persisted
                        assert [item["tool_key"] for item in restored.tool_observations] == ["finish_review"]
                        report["completed_checkpoint_sdk_restore"] = {
                            "sqlite_session_reopened": True, "additional_model_requests": 0,
                            "validated_output_unchanged": True, "budget_unchanged": True,
                            "checkpoint_unchanged": True,
                        }
                finally:
                    report["checkpoint"] = checkpoint_evidence(journal)
        except Exception as error:
            if diagnostics is not None:
                diagnostics.record_failure(error)
            diagnostic = dict(getattr(error, "diagnostic", None) or {})
            report.update({
                "status": "failed",
                "error_type": type(error).__name__,
                "exception_chain": safe_exception_evidence(error),
                "error_message": str(error)[:4000] if isinstance(error, (OutputValidationError, AgentExecutionBudgetExceeded, AssertionError, FileExistsError))
                                 else "平台异常详情未写入报告，以免包含认证或连接信息。",
                "tool_observations": safe_observations(diagnostic.get("tool_observations") or report.get("tool_observations") or []),
                "tool_calls": deepcopy(context.tool_calls) if context is not None else [],
                "orm_pending_changes": bool(db.new or db.dirty or db.deleted),
            })
        finally:
            if hooks is not None:
                report["model_requests"] = diagnostics.requests if diagnostics is not None else []
                report["model_requests_scope"] = "current_invocation"
                report["budget_usage"] = hooks.snapshot()
                report["model_usage"] = hooks.snapshot()["reported_usage"]
                report["model_usage_scope"] = "cumulative_checkpoint"
                report["request_counts_by_agent_name"] = dict(Counter(item["agent_name"] for item in (diagnostics.requests if diagnostics is not None else [])))
                if prior_budget is not None:
                    report["new_model_usage"] = {
                        key: value - prior_budget["reported_usage"][key]
                        for key, value in report["model_usage"].items()
                    }
                    report["new_attempted_requests"] = report["budget_usage"]["charged"]["attempted_requests"] - prior_budget["charged"]["attempted_requests"]
            statuses = Counter(item.get("status") for item in report.get("tool_observations", []))
            report["accepted_submission_count"] = statuses["accepted"]
            report["rejected_action_count"] = statuses["rejected"]
            db.rollback()
            if resume_source_path is not None and "resumed_execution" in report:
                try:
                    source_unchanged = (
                        hashlib.sha256(resume_source_path.read_bytes()).hexdigest()
                        == report["resumed_execution"]["source_sqlite_sha256"]
                        and hashlib.sha256(resume_from_report.read_bytes()).hexdigest()
                        == report["resumed_execution"]["source_report_sha256"]
                    )
                except OSError:
                    source_unchanged = False
                report["resumed_execution"]["original_evidence_unchanged"] = source_unchanged
                if not source_unchanged:
                    report.update(status="failed", error_type="SourceEvidenceChanged",
                                  error_message="续跑期间来源报告或 SQLite 证据发生变化")
    report["elapsed_seconds"] = round(perf_counter() - started, 3)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--resume-from-report", type=Path,
                        help="复制既有真实模型报告的 SQLite，在新副本中恢复状态、回执和原预算")
    parser.add_argument("--source-stage", choices=("initial_review", "completed_batch_review"),
                        default="initial_review", help="选择真实初审或已完成分批复审产物；后者启动独立验证")
    options = parser.parse_args()
    if options.source_stage == "completed_batch_review" and options.resume_from_report is not None:
        parser.error("--source-stage completed_batch_review 不能与 --resume-from-report 同时使用")
    run_id = options.run_id
    if run_id is None:
        run_id = int(json.loads(options.resume_from_report.read_text(encoding="utf-8"))["source_run_id"]) if options.resume_from_report else 28
    report_scope = "global-scope" if options.source_stage == "completed_batch_review" else "initial-review"
    output = options.output or Path("output/qa") / (
        f"review-agent-run{run_id}-{report_scope}-{datetime.now():%Y%m%d-%H%M%S}-real-model.json"
    )
    if output.exists() or output.with_suffix(".sqlite").exists():
        parser.error("报告或隔离行动数据库已存在，请使用新的 --output 路径以保留既有证据")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(verify(
        run_id, output.with_suffix(".sqlite"), resume_from_report=options.resume_from_report,
        source_stage=options.source_stage,
    ))
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report.get(key) for key in (
        "execution_mode", "source_run_id", "source_scope", "status", "sdk_invoked", "case_count",
        "model_usage", "accepted_submission_count", "rejected_action_count", "error_type", "elapsed_seconds",
    )}, ensure_ascii=False))
    print(f"报告：{output.resolve()}")
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
