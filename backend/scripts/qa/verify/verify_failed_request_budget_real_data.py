"""用真实 HTTP 500 检查点和真实输入回放请求中断账本，不发送模型请求。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents import Agent, RunContextWrapper
from sqlalchemy.orm import Session

from core.db.model_defs import AgentToolDefinition
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks
from modules.agent_platform.sdk_adapter import _function_tool, _model_settings, _output_submission_tool, _runner_input
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_TOOL_SPECS
from scripts.qa.verify.verify_generation_action_availability_real_data import context_for, definition_for, source_bundle
from scripts.qa.verify.verify_persistent_input_filter_real_data import sqlite_engine


async def verify(source_path: Path, output: Path):
    bundle = source_bundle(source_path)
    report, checkpoint = bundle["report"], bundle["checkpoint"]
    failed = [item for item in report["model_requests"] if item["status"] == "ended_without_model_response"]
    assert len(failed) == 1 and any(item.get("http_status") == 500 for item in failed[0]["exception_chain"])
    saved = checkpoint["budget"]
    assert saved["pending_requests"] == 1
    # 准入记录有实际字节计量和输出预留；只重建末次发送前的真实账本，不构造模型响应。
    reservation = {"attempted_requests": 1,
                   "input_tokens": failed[0]["measured_context_bytes"] + 8192,
                   "output_tokens": failed[0]["reserved_output_tokens"]}
    reservation["total_tokens"] = reservation["input_tokens"] + reservation["output_tokens"]
    before = deepcopy(saved)
    before["pending_requests"] = 0
    before["charged"] = {key: value - reservation[key] for key, value in saved["charged"].items()}
    assert before["charged"]["attempted_requests"] == before["completed_requests"]
    persisted = []
    hooks = AutonomousBudgetHooks(before["limits"], checkpoint=before,
                                  on_change=lambda value: persisted.append(deepcopy(value)))
    engine = sqlite_engine(bundle["paths"][1])
    try:
        with Session(engine) as db:
            context, _, _ = context_for(db, bundle)
            domain_hash = action_state_hash(context.action_state)
            parent = definition_for("test_generation_coordinator")
            observation = generation.observe_generation_state(context)
            parent_input = _runner_input(execution_context=context, input_payload=observation,
                                         runtime_config=parent.runtime_config)
            parent_agent = Agent(name=parent.name, instructions=parent.instructions,
                                 tools=[_function_tool(AgentToolDefinition(**{key: deepcopy(value)
                                     for key, value in spec.items() if key != "version"}), context)
                                     for spec in BUILTIN_TOOL_SPECS if spec["tool_key"] in parent.runtime_config["tool_keys"]],
                                 model_settings=_model_settings(parent.runtime_config,
                                     use_json_object_output=False, disable_model_thinking=False))
            parent_wrapper = RunContextWrapper(context)
            await hooks.on_llm_start(parent_wrapper, parent_agent, parent.instructions,
                                     [{"role": "user", "content": parent_input}])
            parent_reserved = hooks.snapshot()
            # 第二个调用域也来自已持久化专业输入；验证父请求收尾不误伤其他正在等待的请求。
            child_input = deepcopy(context.action_state["review"]["input"]["review_inputs"][0])
            child_context = replace(context, action_input=child_input, action_state={})
            specialist = definition_for("test_generation_final_reviewer")
            child_agent = Agent(name=specialist.name, instructions=specialist.instructions,
                                tools=[_output_submission_tool(specialist, child_context)],
                                model_settings=_model_settings(specialist.runtime_config,
                                    use_json_object_output=False, disable_model_thinking=False))
            child_scope = hooks.scoped_requests(6)
            await child_scope.on_llm_start(RunContextWrapper(child_context), child_agent, specialist.instructions,
                [{"role": "user", "content": _runner_input(execution_context=child_context,
                    input_payload=child_input, runtime_config=specialist.runtime_config)}])
            both = hooks.snapshot()
            assert both["pending_requests"] == 2
            hooks.interrupt_requests(execution_context=context)
            root_ended = hooks.snapshot()
            assert root_ended["pending_requests"] == 1
            assert root_ended["interrupted_requests"] == before["interrupted_requests"] + 1
            assert root_ended["charged"] == both["charged"] and root_ended["reported_usage"] == both["reported_usage"]
            count = len(persisted)
            hooks.interrupt_requests(execution_context=context)
            assert hooks.snapshot() == root_ended and len(persisted) == count
            child_scope.interrupt_requests(execution_context=child_context)
            ended = hooks.snapshot()
            assert ended["pending_requests"] == 0
            assert ended["interrupted_requests"] == before["interrupted_requests"] + 2
            assert ended["charged"] == both["charged"] and ended["reported_usage"] == before["reported_usage"]
            assert AutonomousBudgetHooks(ended["limits"], checkpoint=ended).snapshot() == ended
            assert action_state_hash(context.action_state) == domain_hash
            assert not db.new and not db.dirty and not db.deleted
    finally:
        engine.dispose()
    assert bundle["hashes"] == {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in bundle["paths"]}
    result = {"status": "passed", "source_run_id": report["source_run_id"], "source_hashes": bundle["hashes"],
              "real_failed_request": failed[0], "original_terminal_budget": saved,
              "reconstructed_budget_before_failed_request": before,
              "current_context_parent_reservation": parent_reserved, "two_real_input_contexts_reserved": both,
              "parent_interrupted_child_pending": root_ended, "all_ended": ended,
              "scope_isolation": True, "idempotent": True, "no_refund": True,
              "restore_unchanged": True, "historical_files_and_domain_unchanged": True,
              "model_requests_sent": 0, "database_writes": 0,
              "limits": "真实失败账本及输入的生命周期回放；未注入异常、伪造响应或复现新 HTTP 500；当前输入计量不是旧请求报文重建。"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("验证报告已存在，禁止覆盖")
    result = asyncio.run(verify(args.source_report, args.output))
    print(json.dumps({"status": result["status"], "model_requests_sent": 0,
                      "output": str(args.output.resolve())}, ensure_ascii=False))
