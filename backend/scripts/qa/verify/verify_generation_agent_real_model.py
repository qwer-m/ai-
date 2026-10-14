"""从真实已验收规划和权威状态开始隔离诊断；只有 --execute 才调用模型。"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from functools import partial
import hashlib
import json
import re
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.ai.ai_client import get_client_for_user
from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentRunEvent, AgentToolDefinition, AgentWorkflowDefinition
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform import test_generation_planning_agent as planning
from modules.agent_platform.action_journal import AgentActionJournal, action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks, AgentExecutionBudgetExceeded
from modules.agent_platform.contracts import AgentRunCreate, parse_execution_definition
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.request_diagnostics import ModelRequestDiagnostics, safe_exception_evidence
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _autonomous_request_reservation, _node_input, _normalized_quota_usage
from modules.agent_platform.service import _resolved_execution_limits
from modules.agent_platform.semantic_identity import agent_execution_semantic_hash
from modules.agent_platform.sdk_adapter import AgentOutputContract, resolve_agent_model_metadata, run_agent_async
from modules.agent_platform.sources import SOURCE_ARTIFACT_KEY
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS, BUILTIN_WORKFLOW_SPECS
from scripts.qa.verify.verify_autonomous_generation_real_model import safe_observations
from scripts.qa.verify.verify_generation_agent_real_data import _apply_recorded, _input_items
from scripts.qa.verify.generation_contract_history import project_historical_generation_inputs
from scripts.qa.verify.verify_lifecycle_sources_real_data import copy_record
from scripts.qa.verify.verify_planning_agent_real_model import planning_journal, source_fingerprint
from scripts.qa.verify.verify_review_agent_real_model import builtin_definition, isolated_journal


def provider_error_categories(error):
    """仅记录响应中的已知故障类别，不写响应正文、地址或认证信息。"""
    categories, seen = set(), set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        body = getattr(current, "body", None)
        response = getattr(current, "response", None)
        if body is None and response is not None:
            body = response.text
        if body is not None:
            value = json.dumps(body, ensure_ascii=False) if isinstance(body, (dict, list)) else str(body)
            for name, pattern in {
                "quota_or_balance": r"quota|balance|credit|余额|额度|欠费",
                "rate_limit": r"rate.?limit|too many requests|限流",
                "overload": r"overload|capacity|过载|繁忙",
                "timeout": r"timed?\s*out|timeout|超时",
                "authentication": r"authentication|unauthorized|鉴权|认证",
                "upstream": r"upstream|上游",
                "no_available_channel": r"no available|无可用|没有可用",
            }.items():
                if re.search(pattern, value, re.IGNORECASE):
                    categories.add(name)
        current = current.__cause__ or current.__context__
    return sorted(categories)


def provider_error_message(error):
    """只保存短错误文本，先移除请求携带的认证值、地址和长令牌。"""
    seen = set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        response = getattr(current, "response", None)
        if response is not None:
            value = response.text
            request = getattr(response, "request", None)
            if request is not None:
                for key, secret in request.headers.items():
                    if any(word in key.lower() for word in ("authorization", "token", "key", "cookie")):
                        for part in (secret, secret.removeprefix("Bearer ")):
                            if part:
                                value = value.replace(part, "[已脱敏]")
                value = value.replace(str(request.url), "[地址已脱敏]")
            value = re.sub(r"https?://[^\s\"<>]+", "[地址已脱敏]", value)
            # 认证值先移除；标准错误中的请求编号用于网关日志关联，不与任意长令牌混同。
            value = re.sub(r"(request id: [A-Za-z0-9_-]{8,128})|[A-Za-z0-9+/=_-]{24,}",
                           lambda match: match.group(1) or "[长标识已脱敏]", value, flags=re.IGNORECASE)
            return value[:1000]
        current = current.__cause__ or current.__context__
    return None


def _definition_fields(value):
    fields = {column.key: deepcopy(getattr(value, column.key)) for column in value.__table__.columns}
    return {key: item.isoformat() if isinstance(item, datetime) else item for key, item in fields.items()}


def _definition_tree_identity(descriptions):
    return action_state_hash({item["agent_key"]: {
        "definition_sha256": item["current_definition_sha256"], "tools": item["tools"],
        "model": item["model"],
    } for item in descriptions})


def _capture_current_definitions(db, run, root_key):
    """当前真实内置定义作为隔离执行快照；保持线上记录及用户显式模型不变。"""
    repo = AgentDefinitionRepository(db)
    agents = {item["agent_key"]: item for item in BUILTIN_AGENT_SPECS}
    tools = {item["tool_key"]: item for item in BUILTIN_TOOL_SPECS}
    snapshots, originals, descriptions = {}, {}, []

    def capture(key, ancestry):
        if key in ancestry:
            raise ValueError("隔离执行的能力声明存在循环")
        if key in snapshots:
            return
        stored = repo.get_agent(project_id=run.project_id, agent_key=key)
        if key != root_key and (stored is None or not stored.enabled):
            raise ValueError(f"真实专业 Agent 不存在或已停用: {key}")
        identity = {"user_id": run.user_id, "project_id": run.project_id}
        if stored is not None:
            originals[(AgentDefinition, stored.id)] = (stored, action_state_hash(_definition_fields(stored)))
            identity.update(id=stored.id, user_id=stored.user_id, project_id=stored.project_id)
        definition = builtin_definition(AgentDefinition, agents[key], **identity)
        if stored is not None:
            definition.model = stored.model
        captured_tools = []
        for tool_key in definition.runtime_config.get("tool_keys") or []:
            if tool_key == "submit_result":
                continue
            stored_tool = repo.get_tool(project_id=run.project_id, tool_key=tool_key)
            tool_identity = {"user_id": run.user_id, "project_id": run.project_id}
            if stored_tool is not None:
                originals[(AgentToolDefinition, stored_tool.id)] = (
                    stored_tool, action_state_hash(_definition_fields(stored_tool)),
                )
                tool_identity.update(id=stored_tool.id, user_id=stored_tool.user_id, project_id=stored_tool.project_id)
            captured_tools.append(builtin_definition(AgentToolDefinition, tools[tool_key], **tool_identity))
        snapshots[key] = (definition, captured_tools)
        descriptions.append({
            "agent_key": key, "stored_definition_id": stored.id if stored is not None else None,
            "stored_definition_sha256": action_state_hash(_definition_fields(stored)) if stored is not None else None,
            "current_definition_sha256": action_state_hash(_definition_fields(definition)),
            "current_runtime_config": deepcopy(definition.runtime_config),
            "tools": [{"tool_key": item.tool_key, "input_schema_sha256": action_state_hash(item.input_schema),
                       "handler_key": item.handler_key} for item in captured_tools],
            "model": resolve_agent_model_metadata(db=db, user_id=run.user_id, agent_definition=definition),
            "definition_source": "current_builtin_snapshot_with_persisted_explicit_model",
        })
        for child_key in [*(definition.runtime_config.get("managed_agent_keys") or []),
                          *(definition.runtime_config.get("subagent_keys") or [])]:
            capture(child_key, [*ancestry, key])

    capture(root_key, [])
    return snapshots, originals, descriptions


def _prepare_real_initial_state(context, raw_input, nodes, repo, *, initial_stage, pending_indexes):
    generation.initialize_generation_state(context, raw_input)
    state = deepcopy(context.action_state)
    child = generation._planning_context(context, state)
    old_input = repo.read_node_input(nodes["resolve_planning"])
    assert all(raw_input[key] == value for key, value in old_input.items())
    saved = deepcopy(nodes["resolve_planning"].sdk_state["action_checkpoint"]["state"])
    child.action_state = deepcopy(saved)
    # 新图增加批次容量输入；仅重绑定容器身份，真实规划、来源、草案与路由逐字段保持原样。
    child.action_state.update(completed=False, input_sha256=action_state_hash(raw_input), goal=planning._goal(child))
    planning._commit(child, child.action_state)
    assert all(child.action_state[key] == value for key, value in saved.items()
               if key not in {"completed", "input_sha256", "goal", "domain_sha256"})
    assert planning._resolved_output(child) == nodes["resolve_planning"].output_payload
    state["planning"] = child.action_state
    generation._save(context, state)
    generation.accept_generation_plan(context, {})
    authority_inputs = _input_items(repo, nodes["authority"])
    _apply_recorded(context, nodes["authority"], authority_inputs,
                    generation.prepare_authority_action, generation.apply_authority_action)
    assert context.action_state["effective_facts"] == nodes["effective_facts"].output_payload
    generation_inputs, contract_projection = project_historical_generation_inputs(_input_items(repo, nodes["generation"]))
    assert context.action_state["generation_inputs"] == generation_inputs
    if initial_stage == "generation_all_pending":
        pending = list(range(len(generation_inputs)))
    else:
        if (not pending_indexes or len(set(pending_indexes)) != len(pending_indexes)
                or any(index < 0 or index >= len(generation_inputs) for index in pending_indexes)):
            raise ValueError("待重新生成索引必须引用真实且不重复的生成批次")
        pending = sorted(pending_indexes)
        completed = [index for index in range(len(generation_inputs)) if index not in pending]
        if completed:
            _apply_recorded(context, nodes["generation"], generation_inputs,
                            generation.prepare_generation_action, generation.apply_generation_action,
                            indexes=completed)
    observation = generation.observe_generation_state(context)
    assert observation["generation"]["pending_indexes"] == pending
    assert observation["problems"] == [] and observation["review"] is None
    assert context.action_state["review_cache"] == context.action_state["review_domains"] == {}
    return {
        "stage": initial_stage, "pending_generation_indexes": pending,
        "retained_real_generation_count": len(generation_inputs) - len(pending),
        "historical_generation_records_are_revalidated": True,
        "planning_payload_unchanged": True, "authority_payload_unchanged": True,
        "prior_work_not_charged_to_new_diagnostic_budget": True,
        "historical_problems_injected": False, "historical_review_verdicts_injected": False,
        "historical_generation_contract_projection": contract_projection,
    }


def _write_checkpoint(journal, path):
    saved = journal.load()
    if saved is None:
        return {"initialized": False}
    event_ids = [value for (value,) in journal.repo.db.query(AgentRunEvent.id).filter(
        AgentRunEvent.run_id == journal.run_id, AgentRunEvent.node_run_id == journal.node_run_id,
    ).order_by(AgentRunEvent.id).all()]
    events = [journal.repo.db.get(AgentRunEvent, value) for value in event_ids]
    document = {"checkpoint": saved, "events": [
        {"id": event.id, "event_type": event.event_type, "payload": event.payload} for event in events
    ]}
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state = saved["state"]
    scope_reports = sum(value["result_type"] == "planning_feedback" for value in state["generation_requests"].values())
    feedback_revisions = [item for item in state["revision_history"] if item.get("problem_ids")]
    completed_actions = Counter(receipt["tool_key"] for receipt in saved["receipts"].values()
                                if receipt["status"] == "completed")
    return {
        "initialized": True, "state_hash": saved["state_hash"], "input_hash": saved["input_hash"],
        "execution_semantic_hash": saved.get("execution_semantic_hash"),
        "completed": state["completed"], "budget": deepcopy(saved["budget"]),
        "receipt_count": len(saved["receipts"]), "receipt_status_counts": dict(Counter(
            receipt["status"] for receipt in saved["receipts"].values())),
        "planning_versions": {key: state["planning"][key] for key in ("source_version", "draft_version", "plan_version")},
        "layout_version": state["layout_version"], "generation_version": state["generation_version"],
        "revision_history": deepcopy(state["revision_history"]),
        "autonomy_evidence": {
            "natural_generation_scope_reports": scope_reports,
            "feedback_driven_revisions": len(feedback_revisions),
            "completed_actions": dict(completed_actions),
            "generation_feedback_to_revision_observed": bool(scope_reports and feedback_revisions),
            "downstream_feedback_to_revision_observed": bool(feedback_revisions),
            # 生成问题与独立审查问题均可触发上游修订，不能只统计生成者的范围报告。
            "requested_cross_stage_path_completed": bool(feedback_revisions and state["completed"]),
        },
        "full_evidence_path": str(path.resolve()), "full_evidence_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


async def verify(run_id, output, *, execute, initial_stage, pending_indexes, resume_report_path=None, revalidated_report_path=None):
    sqlite_path = output.with_suffix(".sqlite")
    request_path = output.with_suffix(".requests.jsonl")
    checkpoint_path = output.with_suffix(".checkpoint.json")
    request_path.touch(exist_ok=False)
    report = {
        "source_run_id": run_id, "execution_mode": "real_model_execution" if execute else "prepare_only_no_model",
        "independent_diagnostic_not_formal_workflow_run": True, "forced_action_order": False,
        "forced_invalid_candidate": False, "status": "failed", "sdk_invoked": False,
        "mysql_transaction": "READ ONLY", "online_database_writes": 0,
        "isolated_action_database": str(sqlite_path.resolve()),
        "request_event_log": str(request_path.resolve()), "started_at": datetime.now(timezone.utc).isoformat(),
    }
    started = perf_counter()
    context = hooks = diagnostics = None
    sources, source_hashes, originals, all_requests = {}, {}, {}, {}
    run = repo = None
    original_run_hash = None
    resume_report = None
    resume_source_path = None
    prior_budget = None
    if resume_report_path is not None:
        resume_report = json.loads(resume_report_path.read_text(encoding="utf-8"))
        if resume_report.get("source_run_id") != run_id:
            raise ValueError("恢复报告的真实来源运行与请求不一致")
        resume_source_path = Path(resume_report["isolated_action_database"]).resolve()
        report["resumed_execution"] = {
            "source_report": str(resume_report_path.resolve()),
            "source_report_sha256": hashlib.sha256(resume_report_path.read_bytes()).hexdigest(),
            "source_sqlite": str(resume_source_path),
            "source_sqlite_sha256": hashlib.sha256(resume_source_path.read_bytes()).hexdigest(),
        }

    def request_event(event, item):
        all_requests[item["sequence"]] = deepcopy(item)
        with request_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, "request": item}, ensure_ascii=False) + "\n")
        print(json.dumps({event: item}, ensure_ascii=False), flush=True)

    with SessionLocal() as db:
        try:
            db.execute(text("SET TRANSACTION READ ONLY"))
            repo = AgentRunRepository(db)
            run = repo.get_run(run_id=run_id)
            fresh_planning = initial_stage == "original_planning" or revalidated_report_path is not None or (
                resume_report is not None and resume_report["initial_state"]["stage"] in {"original_planning", "revalidated_authority_feedback"})
            persisted_failure = (initial_stage == "persisted_generation_failure" or
                                 (resume_report is not None and resume_report["initial_state"]["stage"] == "persisted_generation_failure"))
            if run is None or (not fresh_planning and run.status != ("failed" if persisted_failure else "success")):
                raise ValueError("隔离诊断的来源运行状态与所选真实起点不一致")
            original_run_hash = action_state_hash([run.input_payload, run.run_context, run.output_payload, run.status])
            workflow = db.get(AgentWorkflowDefinition, run.workflow_definition_id)
            graph_spec = next(item for item in BUILTIN_WORKFLOW_SPECS if item["workflow_key"] == workflow.workflow_key)
            graph = parse_execution_definition(graph_spec["definition"])
            node = next(item for item in graph.nodes if item.node_key == "resolve_generation")
            keys = {*node.depends_on, *([] if fresh_planning else ["resolve_generation"] if persisted_failure else
                                       ["resolve_planning", "authority", "effective_facts", "generation"])}
            sources = {key: repo.latest_node_run(run_id=run_id, node_key=key) for key in keys}
            if any(value is None or value.status != ("failed" if persisted_failure and key == "resolve_generation" else "success")
                   for key, value in sources.items()):
                raise ValueError("来源运行缺少当前统一节点需要的成功真实产物")
            source_hashes = {key: source_fingerprint(repo, value) for key, value in sources.items()}
            raw_input = deepcopy(_node_input(run, node, {key: value.output_payload for key, value in sources.items()}))
            original_artifacts = (run.run_context or {}).get("artifacts") or {}
            artifact_keys = (SOURCE_ARTIFACT_KEY, "requirement_evidence", "source_semantics_source_pages", "source_semantics_coordinate_maps")
            artifacts = {key: deepcopy(original_artifacts[key]) for key in artifact_keys if key in original_artifacts}
            snapshots, originals, descriptions = _capture_current_definitions(db, run, node.reference_key)
            definition, tools = snapshots[node.reference_key]
            model_client = get_client_for_user(run.user_id, db)
            config = definition.runtime_config
            timeout = float(config["request_timeout_seconds"])
            context = ToolExecutionContext(
                db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                node_key=node.node_key, run_input=deepcopy(run.input_payload), artifacts=artifacts,
            )
            definition_identity = _definition_tree_identity(descriptions)
            if resume_report is not None:
                if (source_hashes != resume_report["source_node_snapshot_sha256"]
                        or action_state_hash(raw_input) != resume_report["raw_input_sha256"]
                        or original_run_hash != resume_report["source_run_sha256"]):
                    raise ValueError("恢复报告的来源、初始输入或运行记录已变化")
                if definition_identity != _definition_tree_identity(resume_report["definition_snapshots"]):
                    raise ValueError("当前完整 Agent、工具或模型定义与原诊断不同，不能恢复")
                initial_state = deepcopy(resume_report["initial_state"])
                initial_observation = deepcopy(resume_report["initial_observation"])
                initial_domain_hash = resume_report.get("initial_domain_state_sha256")
                if initial_domain_hash is None:
                    previous = resume_report["checkpoint"]
                    if (resume_report["status"] != "prepared" or previous["receipt_count"]
                            or any(previous["budget"]["charged"].values())):
                        raise ValueError("执行过的诊断缺少初始领域身份，不能推断初始状态")
                    initial_domain_hash = previous["state_hash"]
            elif revalidated_report_path is not None:
                from scripts.qa.verify.verify_authority_support_feedback_real_data import verify as verify_authority_support_seed
                original_seed = json.loads(revalidated_report_path.read_text(encoding="utf-8"))
                seed = verify_authority_support_seed(Path(original_seed["source_report"]))
                if seed != original_seed or seed["raw_input_sha256"] != action_state_hash(raw_input):
                    raise ValueError("真实权威支持种子的来源或当前契约复验结果发生变化")
                context.action_input = deepcopy(raw_input)
                context.action_state = deepcopy(seed["current_generation_state"])
                generation.initialize_generation_state(context, raw_input)
                prior_budget = deepcopy(seed["previous_budget"])
                initial_state = {"stage": "revalidated_authority_feedback", "historical_downstream_feedback_injected": True,
                                 "current_contract_revalidated_actual_calls": len(seed["validated_actual_calls"])}
                initial_observation = generation.observe_generation_state(context)
                initial_domain_hash = action_state_hash(context.action_state)
                report["revalidated_failure_seed"] = {key: deepcopy(seed[key]) for key in (
                    "source_report", "source_report_sha256", "source_sqlite_sha256", "source_execution_semantic_hash", "previous_budget")}
                report["revalidated_failure_seed"].update(ordinary_retry_or_resume=False, new_current_contract_diagnostic=True)
            elif fresh_planning:
                generation.initialize_generation_state(context, raw_input)
                initial_state = {"stage": "original_planning", "historical_downstream_feedback_injected": False,
                                 "historical_planning_or_generation_results_reused": False}
                initial_observation = generation.observe_generation_state(context)
                initial_domain_hash = action_state_hash(context.action_state)
            elif persisted_failure:
                from scripts.qa.verify.verify_generation_session_failure_real_data import prepare_failed_generation_state
                context.action_input = deepcopy(raw_input)
                initial_state = prepare_failed_generation_state(
                    context, sources["resolve_generation"].sdk_state,
                    generator_definition=snapshots["test_case_generator"][0],
                )
                initial_observation = generation.observe_generation_state(context)
                initial_domain_hash = action_state_hash(context.action_state)
                prior_budget = deepcopy(sources["resolve_generation"].sdk_state["action_checkpoint"]["budget"])
                report["revalidated_failure_seed"] = {
                    "source_node_id": sources["resolve_generation"].id,
                    "source_execution_semantic_hash": sources["resolve_generation"].sdk_state["action_checkpoint"].get("execution_semantic_hash"),
                    "previous_budget": deepcopy(prior_budget),
                    "ordinary_retry_or_resume": False,
                    "new_current_contract_diagnostic": True,
                }
            else:
                initial_state = _prepare_real_initial_state(context, raw_input, sources, repo,
                                                           initial_stage=initial_stage, pending_indexes=pending_indexes)
                initial_observation = generation.observe_generation_state(context)
                initial_domain_hash = action_state_hash(context.action_state)
            request = AgentRunCreate(project_id=run.project_id, workflow_key=workflow.workflow_key,
                                     input_payload=deepcopy(run.input_payload))
            limits = (deepcopy(run.run_context["execution_limits"]) if persisted_failure else _resolved_execution_limits(request))
            quota_usage = _normalized_quota_usage()
            if resume_report is not None and limits != resume_report["diagnostic_execution_limits"]:
                raise ValueError("平台执行限额与原诊断不同，不能重置或改写原预算")
            diagnostic_run = copy_record(run)
            diagnostic_run.run_context = {**deepcopy(run.run_context), "execution_limits": limits,
                                          "agent_instance_quota_usage": {}, "quota_usage": quota_usage}
            reservation = (deepcopy(resume_report["checkpoint"]["budget"]["limits"]) if resume_report is not None
                           else deepcopy(prior_budget["limits"]) if prior_budget is not None
                           else _autonomous_request_reservation(run=diagnostic_run, definition=definition,
                                                                tools=tools, quota_usage=quota_usage))
            report.update({
                "initial_state": initial_state, "initial_observation": initial_observation,
                "initial_domain_state_sha256": initial_domain_hash,
                "node_key": node.node_key, "graph_node_count": len(graph.nodes),
                "source_node_ids": {key: value.id for key, value in sources.items()},
                "source_node_snapshot_sha256": source_hashes,
                "source_run_sha256": original_run_hash, "raw_input_sha256": action_state_hash(raw_input),
                "source_artifact_sha256": {key: action_state_hash(value) for key, value in artifacts.items()},
                "model_input_sha256": action_state_hash(initial_observation), "request_reservation": reservation,
                "diagnostic_execution_limits": limits, "diagnostic_initial_quota_usage": quota_usage,
                "diagnostic_budget_scope": ("resumed_original_diagnostic_budget_not_recharged" if resume_report is not None
                                            else "new_diagnostic_preserving_prior_consumption" if prior_budget is not None
                                            else "new_isolated_diagnostic_not_resuming_source_run_usage"),
                "budget_limit_source": ("persisted_source_run_and_node_limits" if persisted_failure
                                        else "AgentRunCreate_defaults_resolved_by_current_service_and_runtime"),
                "request_timeout_seconds": timeout, "definition_snapshots": descriptions,
                "definition_tree_sha256": definition_identity,
                "available_tools": [tool.tool_key for tool in tools],
            })
            assert not db.new and not db.dirty and not db.deleted
            journal_factory = (partial(isolated_journal, resume_report=resume_report) if resume_report is not None
                               else partial(planning_journal, verification="real_model_isolated_generation"))
            with journal_factory(
                sqlite_path, run=diagnostic_run, workflow=workflow,
                source_node=sources["plan_batches" if fresh_planning else "resolve_generation" if persisted_failure else "resolve_planning"],
                graph_spec=graph_spec, node_spec=node, raw_input=raw_input, timeout=timeout,
            ) as (journal, isolated_node, token):
                context.action_journal, context.claim_token = journal, token
                saved = journal.load()
                if prior_budget is not None and resume_report is None:
                    # 新诊断先固定当前执行身份，再导入原消耗；绝不把旧执行身份改绑到新代码。
                    journal.init(state=context.action_state, input_hash=action_state_hash(raw_input),
                                 budget=AutonomousBudgetHooks(reservation).snapshot())
                    bound = journal.bind_execution_identity(agent_execution_semantic_hash(
                        db=db, user_id=run.user_id, project_id=run.project_id, node=node,
                        definition=definition, tools=tools, client=model_client, definition_snapshots=snapshots,
                    ))
                    report["revalidated_failure_seed"]["new_execution_semantic_hash"] = bound["execution_semantic_hash"]
                if resume_report is not None:
                    if saved is None:
                        raise ValueError("恢复数据库缺少原始领域检查点，不能重新初始化")
                    previous = resume_report["checkpoint"]
                    evidence_path = Path(previous["full_evidence_path"])
                    if hashlib.sha256(evidence_path.read_bytes()).hexdigest() != previous["full_evidence_sha256"]:
                        raise ValueError("原诊断的完整检查点证据被修改")
                    if saved != json.loads(evidence_path.read_text(encoding="utf-8"))["checkpoint"]:
                        raise ValueError("复制检查点与原报告完整证据不一致")
                    if (saved["state_hash"] != previous["state_hash"] or saved["input_hash"] != previous["input_hash"]
                            or saved["budget"] != previous["budget"] or len(saved["receipts"]) != previous["receipt_count"]):
                        raise ValueError("恢复检查点的状态、输入、回执或预算与原报告不一致")
                    if not saved.get("execution_semantic_hash") and (saved["receipts"] or any(saved["budget"]["charged"].values())):
                        raise ValueError("执行过的检查点缺少真实执行身份，不能恢复")
                    context.action_state = deepcopy(saved["state"])
                    generation.initialize_generation_state(context, raw_input)
                    prior_budget = deepcopy(saved["budget"])
                hooks = AutonomousBudgetHooks(reservation, on_change=journal.save_budget, checkpoint=prior_budget)
                diagnostics = ModelRequestDiagnostics(journal=journal, max_records=200, on_event=request_event)
                journal.init(state=context.action_state, input_hash=action_state_hash(raw_input), budget=hooks.snapshot())
                if prior_budget is not None:
                    restored_budget = hooks.snapshot()
                    assert restored_budget["charged"] == prior_budget["charged"]
                    assert restored_budget["reported_usage"] == prior_budget["reported_usage"]
                    assert restored_budget["pending_requests"] == 0
                    assert restored_budget["interrupted_requests"] == prior_budget["interrupted_requests"] + prior_budget["pending_requests"]
                    journal.save_budget(restored_budget)
                    report["resumed_execution" if resume_report is not None else "revalidated_failure_seed"].update({
                        "domain_state_and_receipts_restored": resume_report is not None,
                        "seeded_prior_work_reexecuted": False,
                        "initial_domain_state_sha256": initial_domain_hash,
                        "previous_state_hash": saved["state_hash"] if saved else None,
                        "previous_receipt_count": len(saved["receipts"]) if saved else 0,
                        "previous_budget": prior_budget, "restored_budget": restored_budget,
                        "charged_budget_preserved": True,
                        "execution_semantic_hash": saved.get("execution_semantic_hash") if saved else None,
                    })
                model_input = generation.observe_generation_state(context)
                report["invocation_initial_observation"] = deepcopy(model_input)
                report["invocation_model_input_sha256"] = action_state_hash(model_input)
                report["isolated_node_run_id"] = isolated_node.id
                contract = AgentOutputContract(deepcopy(raw_input), node.map_config)
                try:
                    if not execute:
                        report["status"] = "prepared"
                    else:
                        report["sdk_invoked"] = True
                        result = await run_agent_async(
                            db=db, agent_definition=definition, tool_definitions=tools, execution_context=context,
                            input_payload=model_input, request_timeout_seconds=timeout,
                            disable_server_output_schema=bool(config.get("disable_server_output_schema")),
                            skip_output_postprocessor=True, output_contract=contract,
                            request_budget=reservation, shared_budget_hooks=hooks, request_diagnostics=diagnostics,
                            execution_node=node, captured_definitions=snapshots, model_client=model_client,
                        )
                        report.update(tool_observations=safe_observations(result.tool_observations),
                                      tool_calls=deepcopy(context.tool_calls), candidate=deepcopy(result.output),
                                      validated_output=deepcopy(result.validated_output))
                        assert result.validated_output == contract.validate(definition, context, deepcopy(result.output))
                        assert result.validated_output["audit_summary"]["approved"]
                        assert result.validated_output["final_review"]["approved"]
                        assert not result.validated_output["final_review"]["differences"]
                        assert hooks.snapshot()["pending_requests"] == 0
                        persisted = journal.load()
                        assert persisted["state"] == context.action_state and persisted["state"]["completed"]
                        report.update(status="passed", full_validation_rechecked=True,
                                      case_count=len(result.validated_output["generation"]["test_cases"]))
                        # 用新 SQLite 会话恢复同一完成检查点，不能新发模型或重置任何额度。
                        isolated_db = journal.repo.db
                        isolated_engine = isolated_db.get_bind()
                        isolated_db.close()
                        with Session(isolated_engine, expire_on_commit=False) as reopened:
                            restored_journal = AgentActionJournal(AgentRunRepository(reopened), run_id=run.id,
                                                                 node_run_id=journal.node_run_id, expected_claim_token=token)
                            restored_context = replace(context, action_journal=restored_journal,
                                                       action_state=deepcopy(persisted["state"]), tool_calls=[],
                                                       executed_tools=[], invoke_agent=None)
                            restored = await run_agent_async(
                                db=db, agent_definition=definition, tool_definitions=tools, execution_context=restored_context,
                                input_payload=model_input, request_timeout_seconds=timeout,
                                disable_server_output_schema=bool(config.get("disable_server_output_schema")),
                                skip_output_postprocessor=True, output_contract=contract,
                                request_budget=reservation, execution_node=node, captured_definitions=snapshots, model_client=model_client,
                            )
                            assert restored.model_requests == [] and restored.validated_output == result.validated_output
                            assert restored.budget_usage == result.budget_usage and restored_journal.load() == persisted
                            report["completed_checkpoint_restore"] = {"additional_model_requests": 0,
                                                                      "budget_and_output_unchanged": True}
                except Exception as error:
                    diagnostics.record_failure(error)
                    raise
                finally:
                    report["checkpoint"] = _write_checkpoint(journal, checkpoint_path)
        except Exception as error:
            report.update({
                "status": "failed", "error_type": type(error).__name__, "exception_chain": safe_exception_evidence(error),
                "provider_error_categories": provider_error_categories(error),
                "provider_error_message_redacted": provider_error_message(error),
                "error_message": str(error)[:4000] if isinstance(error, (OutputValidationError, AgentExecutionBudgetExceeded, AssertionError, FileExistsError))
                                 else "平台异常详情未写入报告，以免包含认证或连接信息。",
                "tool_observations": safe_observations(dict(getattr(error, "diagnostic", None) or {}).get("tool_observations") or report.get("tool_observations") or []),
                "tool_calls": deepcopy(context.tool_calls) if context is not None else [],
            })
        finally:
            report["model_requests"] = [all_requests[key] for key in sorted(all_requests)]
            report["model_requests_scope"] = "all_current_invocation_requests_from_streamed_events"
            report["request_counts_by_agent_name"] = dict(Counter(item["agent_name"] for item in report["model_requests"]))
            report["gateway_504_requests"] = [item for item in report["model_requests"]
                                              if any(error.get("http_status") == 504 for error in item.get("exception_chain", []))]
            if hooks is not None:
                report["budget_usage"] = hooks.snapshot()
                report["model_usage"] = hooks.snapshot()["reported_usage"]
                report["model_usage_scope"] = "cumulative_diagnostic_checkpoint"
                if prior_budget is not None:
                    report["new_model_usage"] = {key: value - prior_budget["reported_usage"][key]
                                                  for key, value in report["model_usage"].items()}
                    report["new_attempted_requests"] = report["budget_usage"]["charged"]["attempted_requests"] - prior_budget["charged"]["attempted_requests"]
            report["orm_pending_changes"] = bool(db.new or db.dirty or db.deleted)
            try:
                assert not report["orm_pending_changes"]
                if original_run_hash is not None:
                    db.refresh(run)
                    assert action_state_hash([run.input_payload, run.run_context, run.output_payload, run.status]) == original_run_hash
                for key, expected in source_hashes.items():
                    db.refresh(sources[key])
                    assert source_fingerprint(repo, sources[key]) == expected
                for value, expected in originals.values():
                    db.refresh(value)
                    assert action_state_hash(_definition_fields(value)) == expected
                report["source_run_nodes_and_definitions_unchanged"] = True
            except Exception as error:
                report.update(status="failed", source_run_nodes_and_definitions_unchanged=False,
                              source_verification_error_type=type(error).__name__)
            db.rollback()
            if resume_report_path is not None:
                unchanged = (hashlib.sha256(resume_report_path.read_bytes()).hexdigest() == report["resumed_execution"]["source_report_sha256"]
                             and hashlib.sha256(resume_source_path.read_bytes()).hexdigest() == report["resumed_execution"]["source_sqlite_sha256"])
                report["resumed_execution"]["original_report_and_database_unchanged"] = unchanged
                if not unchanged:
                    report.update(status="failed", error_type="OriginalDiagnosticEvidenceChanged")
    report.update(elapsed_seconds=round(perf_counter() - started, 3), finished_at=datetime.now(timezone.utc).isoformat())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume-report", type=Path, help="只读复制已有诊断的SQLite，恢复原状态、行动和已消费预算")
    parser.add_argument("--revalidated-authority-report", type=Path, help="以当前契约重验真实权威缺口前缀，建立新身份诊断并保留全部消费")
    parser.add_argument("--initial-stage", choices=("original_planning", "generation_all_pending", "generation_selected_pending", "persisted_generation_failure"),
                        default=None)
    parser.add_argument("--pending-indexes", type=int, nargs="+", default=None)
    args = parser.parse_args()
    evidence_paths = [args.output, *(args.output.with_suffix(suffix) for suffix in (".sqlite", ".requests.jsonl", ".checkpoint.json"))]
    if any(path.exists() for path in evidence_paths):
        parser.error("报告或隔离证据已存在，请使用新的输出路径")
    if args.resume_report is not None and (args.initial_stage is not None or args.pending_indexes is not None):
        parser.error("恢复必须保留原始初态，不能同时指定 --initial-stage 或 --pending-indexes")
    if args.revalidated_authority_report is not None and (args.resume_report is not None or args.initial_stage is not None or args.pending_indexes is not None):
        parser.error("新身份真实权威种子不能与恢复、其他初态或批次选择混用")
    source_report = json.loads(args.resume_report.read_text(encoding="utf-8")) if args.resume_report is not None else None
    run_id = args.run_id if args.run_id is not None else source_report["source_run_id"] if source_report is not None else 36
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(verify(run_id, args.output, execute=args.execute,
                                initial_stage=args.initial_stage or "generation_selected_pending",
                                pending_indexes=args.pending_indexes or [14], resume_report_path=args.resume_report,
                                revalidated_report_path=args.revalidated_authority_report))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report.get(key) for key in ("source_run_id", "status", "sdk_invoked", "initial_state",
                                                     "model_usage", "error_type", "elapsed_seconds")}, ensure_ascii=False))
    print(f"报告：{args.output.resolve()}")
    if report["status"] not in {"prepared", "passed"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
