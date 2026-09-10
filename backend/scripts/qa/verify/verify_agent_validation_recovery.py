"""用数据库中的真实候选复验校验与修复链路，不调用模型、不写数据库。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.exceptions import ModelBehaviorError

from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun, AgentWorkflowDefinition
from modules.agent_platform.contracts import parse_execution_definition
from modules.agent_platform.registry import ToolExecutionContext, runtime_registry_signature
from modules.agent_platform.runtime import (
    _agent_map_item_retry_feedback,
    _agent_map_output_diagnostic,
    _agent_map_repair_context,
    _payload_hash,
    _postprocess_agent_map_output,
    _restored_agent_retry_context,
)
from modules.agent_platform.sdk_adapter import AgentExecutionResult
from modules.agent_platform.service import _has_restorable_repair_state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, action="append", required=True)
    parser.add_argument("--node-key", action="append")
    parser.add_argument("--require-rejected-candidate", action="store_true")
    parser.add_argument(
        "--revalidate-stored-output", action="store_true",
        help="仅用于已确认可重复执行的校验器；标准化后的用例不能当作原始模型输出",
    )
    options = parser.parse_args()
    report = {"stored_records_checked": 0, "outputs_revalidated": 0, "rejected_candidates": 0, "repairs": []}
    signature = runtime_registry_signature()
    with SessionLocal() as db:
        for run_id in options.run_id:
            run = db.get(AgentRun, run_id)
            if run is None:
                raise ValueError(f"运行不存在: {run_id}")
            if run.run_context.get("runtime_registry_signature") != signature:
                raise ValueError(f"运行 {run_id} 的契约版本不同，不能作为当前版本的回归证据")
            workflow = db.get(AgentWorkflowDefinition, run.workflow_definition_id)
            graph = parse_execution_definition(workflow.definition)
            specs = {node.node_key: node for node in graph.nodes}
            query = db.query(AgentNodeRun).filter(AgentNodeRun.run_id == run_id)
            if options.node_key:
                query = query.filter(AgentNodeRun.node_key.in_(options.node_key))
            for node_run in query.all():
                spec = specs[node_run.node_key]
                if spec.node_type != "agent_map":
                    continue
                definition = db.get(AgentDefinition, node_run.agent_definition_id)
                inputs = node_run.input_payload[spec.map_config.items_key]
                context = ToolExecutionContext(
                    db=db, user_id=run.user_id, project_id=run.project_id,
                    run_id=run.id, node_key=node_run.node_key,
                    run_input=deepcopy(run.input_payload),
                    artifacts=deepcopy(run.run_context.get("artifacts") or {}),
                )
                for record in node_run.output_payload.get(spec.map_config.output_key, []):
                    item_input = inputs[record["item_index"]]
                    assert record["input_hash"] == _payload_hash(item_input)
                    assert isinstance(record["output"], dict)
                    report["stored_records_checked"] += 1
                    if options.revalidate_stored_output:
                        output = _postprocess_agent_map_output(
                            config=spec.map_config, definition=definition, execution_context=context,
                            item_input=item_input, item_output=deepcopy(record["output"]),
                        )
                        assert output == record["output"], f"成功结果发生变化: {node_run.id}"
                        report["outputs_revalidated"] += 1
                states = list(node_run.sdk_state.get("items") or [])
                if node_run.sdk_state.get("failed_item"):
                    states.append(node_run.sdk_state["failed_item"])
                for state in states:
                    for diagnostic in state.get("validation_diagnostics") or []:
                        if diagnostic.get("output_text_truncated"):
                            continue
                        text = diagnostic.get("normalized_model_output_text")
                        if not text:
                            continue
                        try:
                            candidate = json.loads(text)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(candidate, dict):
                            continue
                        item_input = inputs[state["item_index"]]
                        result = AgentExecutionResult(
                            output=candidate, final_text=text, last_agent_name=definition.name,
                            usage=state.get("usage") or {}, tool_calls=state.get("tool_calls") or [],
                        )
                        try:
                            _postprocess_agent_map_output(
                                config=spec.map_config, definition=definition, execution_context=context,
                                item_input=item_input, item_output=deepcopy(candidate),
                            )
                        except ModelBehaviorError as exc:
                            feedback = _agent_map_item_retry_feedback(
                                previous_feedback=None, exc=exc, item_input=item_input,
                            )
                            repair = _agent_map_repair_context(
                                result=result, exc=exc, validation_feedback=feedback,
                                item_input=item_input,
                            )
                            assert repair is not None
                            if repair["mode"] == "minimal_patch":
                                assert repair["candidate_output"] == candidate
                            persisted = {
                                "input_hash": _payload_hash(item_input),
                                "retry_feedback": feedback, "repair_context": repair,
                            }
                            assert _has_restorable_repair_state(persisted)
                            assert _restored_agent_retry_context(persisted, item_input) == (feedback, repair)
                            other_input = next((item for item in inputs if item != item_input), None)
                            if other_input is not None:
                                try:
                                    _restored_agent_retry_context(persisted, other_input)
                                except ValueError:
                                    pass
                                else:
                                    raise AssertionError("不同真实任务错误复用了修复候选")
                            recorded = _agent_map_output_diagnostic(
                                result=result, item_attempt=diagnostic["item_attempt"], exc=exc,
                            )
                            assert recorded["normalized_model_output_text"] == text
                            report["rejected_candidates"] += 1
                            report["repairs"].append({
                                "run_id": run_id, "node_key": node_run.node_key,
                                "item_index": state["item_index"], "mode": repair["mode"],
                            })
        db.rollback()
    assert report["stored_records_checked"], "没有读取到真实成功结果"
    if options.require_rejected_candidate:
        assert report["rejected_candidates"], "没有复现真实失败候选"
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
