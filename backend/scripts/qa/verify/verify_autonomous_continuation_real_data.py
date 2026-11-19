"""使用真实运行的路由输入和真实模型，验证非终止正文的续谈及累计预算。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents import RunConfig
from agents.exceptions import MaxTurnsExceeded
from core.ai.ai_client import get_client_for_user
from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks
from modules.agent_platform.autonomous_tools import AutonomousToolLoop
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.request_diagnostics import ModelRequestDiagnostics, ObservedRunHooks
from modules.agent_platform.sdk_adapter import AgentOutputContract, _build_sdk_agent, _run_sdk_agent_async, _SDKClientPool


async def verify(run_id: int, output_path: Path) -> dict:
    with SessionLocal(autoflush=False) as db:
        run = db.get(AgentRun, run_id)
        if run is None:
            raise ValueError("真实运行不存在")
        node_ids = db.query(AgentNodeRun.id).filter_by(run_id=run_id, node_key="resolve_generation").all()
        nodes = [db.get(AgentNodeRun, key) for key, in node_ids]
        node = max(nodes, key=lambda value: value.id)
        checkpoint = node.sdk_state["action_checkpoint"]
        inputs = checkpoint["state"]["planning"]["route_inputs"]
        payload = min(inputs, key=lambda item: sum(len(scope["facts"]) for scope in item["scopes"]))
        repository = AgentDefinitionRepository(db)
        definition = deepcopy(repository.get_agent(project_id=run.project_id, agent_key="test_planning_scope_router"))
        tools = deepcopy(repository.list_agent_tools(definition.id, project_id=run.project_id))
        client = get_client_for_user(run.user_id, db)
        report = {"source_run_id": run_id, "source_node_id": node.id,
                  "real_input_hash": action_state_hash(payload), "online_database_writes": 0, "scenarios": []}
        for always_prose in (False, True):
            context = ToolExecutionContext(
                db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                node_key=node.node_key, run_input=run.input_payload,
                artifacts=deepcopy(run.run_context["artifacts"]),
            )
            contract = AgentOutputContract(deepcopy(payload), AgentMapConfig(
                item_postprocessor="testing.postprocess_planning_scope_routing_item"))
            max_turns = 2 if always_prose else 3
            limits = {**checkpoint["budget"]["limits"], "attempted_requests": max_turns}
            budget = AutonomousBudgetHooks(limits)
            loop = AutonomousToolLoop(
                terminal_tool_names={"submit_result"}, max_tool_calls=max_turns, recoverable_errors=(),
                validate_submission=lambda output: contract.validate(definition, context, output),
            )
            responses = []

            class Observer(ObservedRunHooks):
                async def on_llm_end(self, context, agent, response):
                    responses.append([item.type for item in response.output])
                    await super().on_llm_end(context, agent, response)

            observer = Observer(ModelRequestDiagnostics(), budget)
            pool = _SDKClientPool()
            try:
                agent, _ = _build_sdk_agent(
                    db=db, client=client, agent_definition=definition, execution_context=context,
                    allow_subagents=False, direct_tool_definitions=tools, sdk_client_pool=pool,
                    autonomous_loop=loop, request_timeout_seconds=120,
                )
                # 只控制本次协议测试的回复顺序，业务输入及提交校验仍使用真实运行原值。
                agent.instructions += (
                    "\n本次为会话协议验证：每一轮都只回复『尚在处理』，不要调用任何工具。"
                    if always_prose else
                    "\n本次为会话协议验证：第一轮只回复『已收到』，不要调用工具。"
                    "收到平台要求继续的反馈后，再按上述真实输入调用 submit_result 完成完整提交。"
                )
                if always_prose:
                    # 用真实 API 参数保证本轮只返回正文，避免把提示词服从性当作额度测试条件。
                    agent.model_settings = replace(agent.model_settings, tool_choice="none")
                failure = None
                result = None
                try:
                    result = await _run_sdk_agent_async(
                        agent=agent, runner_input=json.dumps(payload, ensure_ascii=False),
                        execution_context=context, max_turns=max_turns, hooks=observer,
                        submission_loop=loop, run_config=RunConfig(tracing_disabled=True),
                        request_timeout_seconds=120,
                    )
                except MaxTurnsExceeded as error:
                    failure = type(error).__name__
                charged = budget.snapshot()["charged"]["attempted_requests"]
                report["scenarios"].append({"always_prose": always_prose, "response_item_types": responses,
                    "attempted_requests": charged, "max_turns": max_turns,
                    "forced_tool_choice_none": always_prose,
                    "validated_submission": loop.has_submission, "expected_budget_error": failure})
                # 验收前落盘本次真实响应摘要，即使检查失败也保留证据。
                output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(report["scenarios"][-1], ensure_ascii=False), flush=True)
                assert responses and "function_call" not in responses[0], responses
                assert charged == len(responses)
                if always_prose:
                    assert failure == "MaxTurnsExceeded" and not loop.has_submission
                    assert charged == max_turns
                else:
                    assert failure is None and loop.has_submission
                    assert 1 < charged <= max_turns
                    assert result.context_wrapper.usage.requests == charged
                    assert "function_call" in responses[-1]
                    loop.require_submission()
            finally:
                await pool.close()
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("报告已存在，请使用新路径保留证据")
    report = asyncio.run(verify(args.run_id, args.output))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
