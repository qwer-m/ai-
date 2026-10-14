"""使用真实历史输入调用当前生成 Agent，只读数据库，候选和行动证据仅写本地报告。"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text
from sqlalchemy.orm.attributes import set_committed_value

from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentWorkflowDefinition
from modules.agent_platform.contracts import parse_execution_definition
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _agent_request_reservation, _project_agent_map_input
from modules.agent_platform.sdk_adapter import (
    AgentOutputContract, resolve_agent_model_metadata, run_agent_async,
)
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS


def sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def safe_observations(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """保留业务校验反馈；平台异常只记录类型，避免原始网络异常携带路由信息。"""
    values = deepcopy(observations)
    for value in values:
        if value.get("status") == "error":
            value.pop("message", None)
    return values


def action_statistics(
    observations: list[dict[str, Any]], calls: list[dict[str, Any]], subagent_keys: list[str],
) -> dict[str, Any]:
    statuses = Counter(str(item.get("status") or "unknown") for item in observations)
    chosen = Counter(
        str(item["tool_key"]) for item in observations if item.get("tool_key") in subagent_keys
    )
    completed = Counter(
        str(item["tool_key"])[len("agent:"):]
        for item in calls if str(item.get("tool_key") or "").startswith("agent:")
    )
    return {
        "tool_observation_count": len(observations),
        "tool_status_counts": dict(statuses),
        "rejected_submission_count": sum(
            item.get("status") == "rejected" and item.get("tool_key") == "submit_generation_batch"
            for item in observations
        ),
        "accepted_submission_count": statuses["accepted"],
        "specialist_calls_chosen": dict(chosen),
        "specialist_calls_completed": dict(completed),
        "specialist_chosen_count": sum(chosen.values()),
        "specialist_completed_count": sum(completed.values()),
        "feedback_repair_observed": statuses["rejected"] > 0 and statuses["accepted"] == 1,
    }


async def verify(run_id: int, item_index: int, model_route: str | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "execution_mode": "real_model_execution",
        "replay": False,
        "source_run_id": run_id,
        "source_node_key": "generation",
        "source_item_index": item_index,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed",
        "sdk_invoked": False,
        "database_transaction": "READ ONLY",
        "persisted_new_run": False,
        "persisted_candidate": False,
        "forced_tool_choice_or_invalid_candidate": False,
        "model_usage": None,
        "diagnostic_model_route_override": model_route,
    }
    started = perf_counter()
    context = None
    subagent_keys: list[str] = []
    with SessionLocal() as db:
        try:
            db.execute(text("SET TRANSACTION READ ONLY"))
            repo = AgentRunRepository(db)
            run = repo.get_run(run_id=run_id)
            if run is None:
                raise ValueError("真实来源运行不存在")
            node = repo.latest_node_run(run_id=run_id, node_key="generation")
            if node is None or node.status not in {"success", "failed"} or node.agent_definition_id is None:
                raise ValueError("需要已终结且保留完整输入快照的真实 generation 节点")
            workflow = db.get(AgentWorkflowDefinition, run.workflow_definition_id)
            if workflow is None:
                raise ValueError("真实运行缺少工作流定义")
            graph = parse_execution_definition(workflow.definition)
            model_node = next(item for item in graph.nodes if item.node_key == node.node_key)
            if model_node.map_config is None:
                raise ValueError("generation 节点没有批次契约")
            inputs = repo.read_node_input(node).get(model_node.map_config.items_key)
            if not isinstance(inputs, list) or not 0 <= item_index < len(inputs):
                raise ValueError("请求的真实批次索引不存在")
            raw_input = deepcopy(inputs[item_index])
            if not isinstance(raw_input, dict):
                raise ValueError("真实批次输入不是对象")
            stored_definition = db.get(AgentDefinition, node.agent_definition_id)
            if stored_definition is None or stored_definition.agent_key != "test_case_generator":
                raise ValueError("真实节点没有绑定测试用例生成 Agent")
            spec = next(item for item in BUILTIN_AGENT_SPECS if item["agent_key"] == stored_definition.agent_key)
            definition = deepcopy(stored_definition)
            # 仅覆盖分离副本，不执行 seed、UPDATE 或 Session.add。
            set_committed_value(definition, "instructions", deepcopy(spec["instructions"]))
            set_committed_value(definition, "runtime_config", deepcopy(spec["runtime_config"]))
            if definition.output_schema != spec["output_schema"]:
                raise ValueError("真实 Agent 输出契约与当前内置契约不同，不能混用验证")
            config = definition.runtime_config
            if config.get("validate_before_submit") is not True:
                raise ValueError("当前内置生成 Agent 没有启用自主提交契约")
            timeout = float(config["request_timeout_seconds"])
            subagent_keys = list(config.get("subagent_keys") or [])
            definitions = AgentDefinitionRepository(db)
            specialist_specs = {item["agent_key"]: item for item in BUILTIN_AGENT_SPECS}
            specialist_definitions = []
            for key in subagent_keys:
                specialist = definitions.get_agent(project_id=run.project_id, agent_key=key)
                specialist_spec = specialist_specs[key]
                if specialist is None or specialist.output_schema != specialist_spec["output_schema"]:
                    raise ValueError("真实专业 Agent 定义缺失或输出契约与当前内置契约不一致")
                # 只读事务中同步当前内置提示；保留强引用供正式仓储读取同一会话对象，不产生数据库更新。
                set_committed_value(specialist, "instructions", deepcopy(specialist_spec["instructions"]))
                set_committed_value(specialist, "runtime_config", deepcopy(specialist_spec["runtime_config"]))
                specialist_definitions.append(specialist)
            tools = definitions.list_agent_tools(definition.id, project_id=run.project_id)
            model_input, projection = _project_agent_map_input(definition=definition, raw_item=raw_input)
            reservation = _agent_request_reservation(
                definition=definition, tools=tools, input_payload=model_input,
            )
            context = ToolExecutionContext(
                db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                node_key=f"{node.node_key}-verification-item-{item_index}",
                run_input=deepcopy(run.input_payload),
                artifacts=deepcopy(run.run_context.get("artifacts") or {}),
            )
            contract = AgentOutputContract(deepcopy(raw_input), model_node.map_config)
            report.update({
                "source_node_run_id": node.id,
                "source_node_status": node.status,
                "source_run_status": run.status,
                "independent_diagnostic_not_checkpoint_retry": True,
                "source_input_sha256": sha256_json(raw_input),
                "model_input_sha256": sha256_json(model_input),
                "builtin_instructions_sha256": sha256_json(definition.instructions),
                "builtin_runtime_config_sha256": sha256_json(config),
                "output_schema_sha256": sha256_json(definition.output_schema),
                "configured_model": resolve_agent_model_metadata(db=db, user_id=run.user_id, agent_definition=definition),
                "request_timeout_seconds": timeout,
                "request_reservation": reservation,
                "projection": projection,
                "available_tools": [tool.tool_key for tool in tools],
                "available_specialists": subagent_keys,
                "specialist_definition_sha256": {
                    child.agent_key: sha256_json({
                        "instructions": child.instructions,
                        "runtime_config": child.runtime_config,
                        "output_schema": child.output_schema,
                    }) for child in specialist_definitions
                },
                "expected_case_count": raw_input.get("case_budget"),
            })
            assert not db.new and not db.dirty and not db.deleted
            report["sdk_invoked"] = True
            result = await run_agent_async(
                db=db, agent_definition=definition, tool_definitions=tools,
                execution_context=context, input_payload=model_input,
                request_timeout_seconds=timeout,
                disable_server_output_schema=bool(config.get("disable_server_output_schema")),
                skip_output_postprocessor=True,
                output_contract=contract, request_budget=reservation,
                model_route_override=model_route,
            )
            observations = safe_observations(result.tool_observations)
            report.update({
                "tool_observations": observations,
                "tool_calls": deepcopy(result.tool_calls),
                "model_usage": dict(result.usage),
                "model_requests": deepcopy(result.model_requests),
                "actual_request_models": [deepcopy(request.get("model")) for request in result.model_requests],
                "budget_usage": deepcopy(result.budget_usage),
                "actions": action_statistics(observations, result.tool_calls, subagent_keys),
                "candidate": deepcopy(result.output),
                "validated_output": deepcopy(result.validated_output),
                "candidate_sha256": sha256_json(result.output),
                "validated_output_sha256": sha256_json(result.validated_output),
                "last_agent_name": result.last_agent_name,
            })
            accepted = [item for item in observations if item.get("status") == "accepted"]
            assert isinstance(result.validated_output, dict), "没有完整验收的标准化产物"
            assert len(accepted) == 1 and observations[-1] == accepted[0], "必须以唯一成功验收结束"
            assert accepted[0]["tool_key"] in config["stop_at_tool_keys"], "成功回执并非提交工具"
            assert contract.validate(definition, context, deepcopy(result.output)) == result.validated_output
            assert sha256_json(raw_input) == report["source_input_sha256"], "真实原始输入被修改"
            assert not db.new and not db.dirty and not db.deleted, "验证过程产生了待写数据库状态"
            report.update({
                "status": "passed", "new_model_response_validated": True,
                "case_count": len(result.validated_output["test_cases"]),
                "full_validation_rechecked": True,
                "online_database_writes": 0,
            })
        except Exception as error:
            # 不输出原始异常文本，避免网络或配置异常带出认证和连接细节。
            diagnostic = dict(getattr(error, "diagnostic", None) or {})
            observations = safe_observations(diagnostic.get("tool_observations") or report.get("tool_observations") or [])
            calls = deepcopy(context.tool_calls) if context is not None else []
            report.update({
                "error_type": type(error).__name__,
                "tool_observations": observations,
                "tool_calls": calls,
                "budget_usage": diagnostic.get("budget_usage") or report.get("budget_usage") or {},
                "failed_model_requests": deepcopy(diagnostic.get("failed_model_requests") or []),
                "actions": action_statistics(observations, calls, subagent_keys),
                "orm_pending_changes": bool(db.new or db.dirty or db.deleted),
            })
        finally:
            db.rollback()
    report["elapsed_seconds"] = round(perf_counter() - started, 3)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, default=28)
    parser.add_argument("--item-index", type=int, default=0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model-route", choices=("main", "review", "turbo"))
    options = parser.parse_args()
    output = options.output or Path("output/qa") / (
        f"autonomous-generation-run{options.run_id}-item{options.item_index}-real-model.json"
    )
    if output.exists():
        raise FileExistsError("报告路径已存在，请使用新路径保留原始证据")
    report = asyncio.run(verify(options.run_id, options.item_index, options.model_route))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        key: report.get(key) for key in (
            "execution_mode", "replay", "source_run_id", "source_item_index", "status",
            "sdk_invoked", "model_usage", "actions", "error_type", "elapsed_seconds",
        )
    }, ensure_ascii=False))
    print(f"报告：{output.resolve()}")
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
