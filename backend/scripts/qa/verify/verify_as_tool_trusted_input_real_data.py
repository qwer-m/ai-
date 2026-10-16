"""真实来源与历史专业输出回放：咨询问题不能替换 as_tool 的平台输入。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import inspect
import json
from pathlib import Path
import sys
from types import FunctionType

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.agent_tool_input import resolve_agent_tool_input
from agents.result import RunResult
from agents.tool_context import ToolContext
from sqlalchemy import text

from core.ai.ai_client import get_client_for_user
from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _project_agent_map_input
from modules.agent_platform.sdk_adapter import _build_sdk_agent, OutputPostprocessingError


def actual_hooks(tool):
    """读取 SDK 工具持有的真实回调，再调用公开 input_builder 解析入口；不替换 SDK。"""
    pending, visited, found = [tool.on_invoke_tool], set(), {}
    while pending:
        function = pending.pop()
        if id(function) in visited:
            continue
        visited.add(id(function))
        if not isinstance(function, FunctionType):
            implementation = getattr(function, "_invoke_tool_impl", None)
            if implementation is not None:
                pending.append(implementation)
            continue
        values = inspect.getclosurevars(function).nonlocals
        for name in ("input_builder", "custom_output_extractor"):
            if name in values:
                found[name] = values[name]
        pending.extend(value for value in values.values() if isinstance(value, FunctionType))
    assert set(found) == {"input_builder", "custom_output_extractor"}
    return found


async def verify(db, run_id: int, node_id: int, item_index: int) -> dict:
    runs, definitions = AgentRunRepository(db), AgentDefinitionRepository(db)
    run, node = db.get(AgentRun, run_id), db.get(AgentNodeRun, node_id)
    assert run is not None and node is not None and node.run_id == run.id
    source = runs.read_node_input(node)["items"][item_index]
    definition = db.get(AgentDefinition, node.agent_definition_id)
    child_key = definition.runtime_config["subagent_keys"][0]
    donor_run = db.get(AgentRun, 28)
    donor_node = runs.latest_node_run(run_id=donor_run.id, node_key="generation")
    donor_sources = runs.read_node_input(donor_node)["items"]
    donor_state = next(value for value in donor_node.sdk_state["items"]
                       if any(call["tool_key"] == f"agent:{child_key}" for call in value.get("tool_calls", []))
                       and donor_sources[value["item_index"]]["case_budget"] != source["case_budget"])
    donor_source = donor_sources[donor_state["item_index"]]
    donor_result = next(call["result"] for call in donor_state["tool_calls"] if call["tool_key"] == f"agent:{child_key}")
    donor_submission = next(call for call in donor_state["tool_calls"] if call["tool_key"] == "submit_scenario_design_guidance")
    original_hash = action_state_hash({"input": source, "donor_input": donor_source, "output": donor_result})
    clients, checks = [], []
    client = get_client_for_user(run.user_id, db)

    def build(actual_run, actual_node, raw):
        context = ToolExecutionContext(db=db, user_id=actual_run.user_id, project_id=actual_run.project_id,
                                       run_id=actual_run.id, node_key=actual_node.node_key,
                                       run_input=deepcopy(actual_run.input_payload))
        parent_definition = db.get(AgentDefinition, actual_node.agent_definition_id)
        original_input = deepcopy(raw)
        model_input, _ = _project_agent_map_input(definition=parent_definition, raw_item=raw)
        expected_projection = deepcopy(model_input)
        agent, _ = _build_sdk_agent(
            db=db, client=client, agent_definition=parent_definition, execution_context=context,
            trusted_input_payload=original_input, model_input_payload=model_input,
            allow_subagents=True, allocated_clients=clients,
        )
        tool = next(item for item in agent.tools if item.name == child_key)
        assert set(tool.params_json_schema["properties"]) == {"input"}
        hooks = actual_hooks(tool)
        captured_definition = inspect.signature(hooks["custom_output_extractor"]).parameters["definition"].default
        orm_definition = definitions.get_agent(project_id=actual_run.project_id, agent_key=child_key)
        assert captured_definition is not orm_definition
        assert captured_definition.output_schema == orm_definition.output_schema
        # 只修改调用方的内存副本，证实构建后输入身份已经固定。
        original_input.clear()
        model_input.clear()
        return context, tool, hooks, expected_projection

    async def replay(actual_run, actual_node, raw, question_source, *, accepted):
        context, tool, hooks, projection = build(actual_run, actual_node, raw)
        question = json.dumps(question_source, ensure_ascii=False)
        rendered = await resolve_agent_tool_input(params={"input": question}, input_builder=hooks["input_builder"])
        assert json.loads(rendered) == {**projection, "consultation_question": question}
        # 再用同一真实 JSON 的截断文本验证：问题文本无需是可解析 JSON。
        truncated = await resolve_agent_tool_input(params={"input": question[:-1]}, input_builder=hooks["input_builder"])
        assert json.loads(truncated) == {**projection, "consultation_question": question[:-1]}
        context.tool_calls.append(deepcopy(donor_submission))
        # 只把持久历史输出装入 SDK 原生结果容器供 extractor 回放；不伪造模型响应或 usage。
        replay_result = RunResult(
            input=rendered, new_items=[], raw_responses=[], final_output=deepcopy(donor_result),
            input_guardrail_results=[], output_guardrail_results=[],
            tool_input_guardrail_results=[], tool_output_guardrail_results=[],
            context_wrapper=ToolContext(context=context, tool_name=tool.name,
                                        tool_call_id=f"historical-node-{donor_node.id}-item-{donor_state['item_index']}",
                                        tool_arguments=json.dumps({"input": question}, ensure_ascii=False)),
            _last_agent=tool._agent_instance,
        )
        try:
            output = json.loads(await hooks["custom_output_extractor"](replay_result))
        except OutputPostprocessingError as error:
            assert not accepted
            checks.append({"input_run_id": actual_run.id, "accepted": False,
                           "error_type": type(error).__name__, "message": str(error),
                           "trusted_case_budget": raw["case_budget"],
                           "question_case_budget": question_source["case_budget"]})
        else:
            assert accepted and output == donor_result
            receipt = context.tool_calls[-1]
            assert receipt["arguments"] == {"input": question}
            assert receipt["input_hash"] == action_state_hash(raw)
            checks.append({"input_run_id": actual_run.id, "accepted": True,
                           "trusted_case_budget": raw["case_budget"],
                           "question_case_budget": question_source["case_budget"],
                           "output_sha256": action_state_hash(output)})
        assert not context.artifacts and not context.action_state

    try:
        await replay(run, node, source, donor_source, accepted=False)
        await replay(donor_run, donor_node, donor_source, source, accepted=True)
        missing_context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                                               run_id=run.id, node_key=node.node_key, run_input=run.input_payload)
        try:
            _build_sdk_agent(db=db, client=client, agent_definition=definition, execution_context=missing_context,
                             allow_subagents=True, allocated_clients=clients)
        except ValueError as error:
            assert "平台原始验收输入" in str(error)
        else:
            raise AssertionError("缺少可信源输入仍允许构建专业咨询")
    finally:
        await asyncio.gather(*(allocated.close() for allocated in clients))
    assert action_state_hash({"input": source, "donor_input": donor_source, "output": donor_result}) == original_hash
    assert not db.new and not db.dirty and not db.deleted
    return {
        "status": "passed", "verification_scope": "real_sdk_hooks_with_persisted_inputs_and_specialist_output",
        "source_run_id": run.id, "source_node_id": node.id, "source_item_index": item_index,
        "historical_output_run_id": donor_run.id, "historical_output_node_id": donor_node.id,
        "historical_output_item_index": donor_state["item_index"], "extractor_checks": checks,
        "question_json_cannot_replace_source": True, "truncated_json_is_only_question_text": True,
        "model_projection_preserved": True, "captured_input_and_definition_snapshots_isolated": True,
        "missing_trusted_input_rejected": True, "source_evidence_unchanged": True,
        "mysql_transaction": "READ ONLY", "online_database_writes": 0, "model_requests_sent": 0,
        "sdk_result_metadata_reconstructed_for_local_replay": True, "new_model_behavior_verified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--node-id", type=int, required=True)
    parser.add_argument("--item-index", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise ValueError("报告已存在，拒绝覆盖历史证据")
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        report = asyncio.run(verify(db, options.run_id, options.node_id, options.item_index))
        db.rollback()
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "source_run_id", "model_requests_sent")}))


if __name__ == "__main__":
    main()
