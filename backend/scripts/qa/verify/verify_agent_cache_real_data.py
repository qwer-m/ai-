"""只读回放真实 Agent 输出，验证缓存身份、原始输出复验和拒绝路径。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text
from sqlalchemy.orm.attributes import set_committed_value

from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun, AgentWorkflowDefinition
from modules.agent_platform.contracts import parse_execution_definition
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.input_snapshots import pack_node_input
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.result_cache import payload_hash, reusable_agent_node_output, validate_cached_agent_output
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.semantic_identity import agent_execution_semantic_hash


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, type=int)
    args = parser.parse_args()
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        repo = AgentRunRepository(db)
        source_run = db.get(AgentRun, args.run_id)
        assert source_run is not None and source_run.status == "success"
        graph = parse_execution_definition(db.get(AgentWorkflowDefinition, source_run.workflow_definition_id).definition)
        nodes = repo.list_node_runs(run_id=source_run.id)
        # 只使用历史记录真正保存的终止工具结果，不从归一化结果猜造原始模型响应。
        candidate = next(n for n in nodes if n.status == "success" and n.node_type == "agent_map" and all(
            any(call.get("tool_key") == "submit_source_semantics" for call in item.get("tool_calls", []))
            for item in (n.sdk_state or {}).get("items", [])
        ) and (n.sdk_state or {}).get("items"))
        definition = db.get(AgentDefinition, candidate.agent_definition_id)
        node = next(n for n in graph.nodes if n.node_key == candidate.node_key)
        inputs = repo.read_node_input(candidate)
        original_state = deepcopy(candidate.sdk_state)
        original_input = deepcopy(candidate.input_payload)
        original_output = deepcopy(candidate.output_payload)
        validation_outputs = []
        for item in original_state["items"]:
            index = item["item_index"]
            terminal = [call for call in item["tool_calls"] if call["tool_key"] == "submit_source_semantics"]
            assert len(terminal) == 1
            validation_outputs.append({
                "item_index": index, "input_hash": payload_hash(inputs[node.map_config.items_key][index]),
                "output": deepcopy(terminal[0]["result"]),
            })
        context = ToolExecutionContext(
            db=db, user_id=source_run.user_id, project_id=source_run.project_id,
            run_id=source_run.id, node_key=node.node_key, run_input=deepcopy(source_run.input_payload),
            artifacts=deepcopy(source_run.run_context.get("artifacts") or {}),
        )
        tools = AgentDefinitionRepository(db).list_agent_tools(definition.id, project_id=source_run.project_id)
        semantic_hash = agent_execution_semantic_hash(
            db=db, user_id=source_run.user_id, project_id=source_run.project_id,
            node=node, definition=definition, tools=tools,
        )
        for proof in validation_outputs:
            proof["semantic_hash"] = semantic_hash
        assert validate_cached_agent_output(
            node=node, definition=definition, node_input=inputs, semantic_hash=semantic_hash,
            output=original_output, validation_outputs=validation_outputs, execution_context=context,
        ) == original_output
        cache_state = {"result_cache": {
            "hit": False, "semantic_hash": semantic_hash, "input_hash": payload_hash(inputs),
            "validation_outputs": validation_outputs,
        }}
        # 为同一真实结果的副本补充新格式元数据，回放未来运行的读取；不写库、不替换生产方法。
        replay = deepcopy(source_run)
        replay.id = max(row[0] for row in db.query(AgentRun.id).all()) + 1
        replay.input_payload = {**replay.input_payload, "disable_result_cache": False}
        checks = []

        def lookup(expected: bool, *, identity: str = semantic_hash):
            rejected = []
            found = reusable_agent_node_output(
                repo=repo, run=replay, node=node, definition=definition, node_input=inputs,
                semantic_hash=identity, execution_context=context, rejections=rejected,
            )
            assert (found is not None) is expected, rejected
            return found, rejected

        try:
            # 原始历史记录没有新指纹，不能冒充已经被新规则验证的缓存。
            assert not lookup(False)[1]
            checks.append("旧记录无自动指纹时失效")
            set_committed_value(candidate, "sdk_state", deepcopy(cache_state))
            hit, rejected = lookup(True)
            assert hit.output == original_output and not rejected
            checks.append("同输入同语义的真实输出经完整校验后命中")
            assert lookup(False, identity=payload_hash(semantic_hash))[0] is None
            checks.append("语义身份不匹配时拒绝")
            # 已命中的结果仍须保留原始依据；下一次复用同样需要完整校验。
            chained = deepcopy(cache_state)
            chained["result_cache"].update(hit=True, validation_outputs=hit.validation_outputs)
            set_committed_value(candidate, "sdk_state", chained)
            assert lookup(True)[0].output == original_output
            checks.append("命中链保留原始复验依据")
            damaged = deepcopy(cache_state)
            damaged["result_cache"]["validation_outputs"][0]["output"] = {}
            set_committed_value(candidate, "sdk_state", damaged)
            assert lookup(False)[1][0]["reason"] == "validation_failed"
            checks.append("原始输出缺失必需事实时拒绝")
            set_committed_value(candidate, "sdk_state", deepcopy(cache_state))
            tampered = deepcopy(original_output)
            tampered["completed_count"] -= 1
            set_committed_value(candidate, "output_payload", tampered)
            assert lookup(False)[1][0]["reason"] == "validation_failed"
            checks.append("已保存结果与完整复验结果不一致时拒绝")
            set_committed_value(candidate, "output_payload", original_output)
            damaged = deepcopy(cache_state)
            damaged["result_cache"]["validation_outputs"][0]["input_hash"] = payload_hash(inputs)
            set_committed_value(candidate, "sdk_state", damaged)
            assert lookup(False)[1][0]["reason"] == "validation_failed"
            checks.append("单项复验输入身份不一致时拒绝")
            damaged = deepcopy(cache_state)
            damaged["result_cache"]["validation_outputs"][0]["semantic_hash"] = payload_hash(semantic_hash)
            set_committed_value(candidate, "sdk_state", damaged)
            assert lookup(False)[1][0]["reason"] == "validation_failed"
            checks.append("子项实际执行契约不一致时拒绝")
            set_committed_value(candidate, "sdk_state", deepcopy(cache_state))
            compact = pack_node_input(candidate, inputs, nodes)
            assert "references" in compact
            set_committed_value(candidate, "input_payload", compact)
            assert lookup(True)[0].output == original_output
            checks.append("引用快照还原后按完整输入命中")
            damaged_input = deepcopy(compact)
            damaged_input["references"][0]["sha256"] = payload_hash(compact)
            set_committed_value(candidate, "input_payload", damaged_input)
            assert lookup(False)[1][0]["reason"] == "source_input_invalid"
            checks.append("历史候选引用损坏时拒绝缓存而不中止新任务")
            set_committed_value(candidate, "input_payload", original_input)
            replay.input_payload = {**replay.input_payload, "disable_result_cache": True}
            assert lookup(False)[0] is None
            checks.append("运行级禁用缓存生效")
        finally:
            set_committed_value(candidate, "sdk_state", original_state)
            set_committed_value(candidate, "input_payload", original_input)
            set_committed_value(candidate, "output_payload", original_output)
        assert not db.new and not db.dirty and not db.deleted
        with SessionLocal() as verification_db:
            verification_db.execute(text("SET TRANSACTION READ ONLY"))
            saved = verification_db.get(AgentNodeRun, candidate.id)
            assert saved.sdk_state == original_state and saved.output_payload == original_output
            assert saved.input_payload == original_input
        print(json.dumps({
            "run_id": source_run.id, "node_run_id": candidate.id,
            "真实原始输出数量": len(validation_outputs), "通过检查": checks,
            "数据库未修改": True, "模型调用次数": 0,
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
