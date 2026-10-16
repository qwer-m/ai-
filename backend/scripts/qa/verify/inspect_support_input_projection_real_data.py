"""只读核对真实支持复核的逐事实原文投影，完整坐标仍留在可信验收输入。"""

from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.runtime import _payload_size_diagnostics, _project_agent_map_input
from modules.agent_platform.sdk_adapter import AgentOutputContract
from modules.agent_platform.test_generation_planning import _planning_fact_model_view
from modules.agent_platform.test_generation_semantics import _planning_scopes
from scripts.qa.verify.verify_generation_agent_real_model import _capture_current_definitions


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--accepted-report", required=True, type=Path)
    options = parser.parse_args()
    if options.output.exists():
        raise FileExistsError(options.output)
    source_bytes = options.source.read_bytes()
    source = json.loads(source_bytes)
    raw = deepcopy(source["input"])
    original_hash = action_state_hash(raw)
    trusted = deepcopy(raw)
    # 仅移除历史实验中的传输目录；事实自身原文已完整保存，不改任何来源或模型判断。
    historical_catalog = trusted.pop("source_contexts", {})
    for fact in trusted["candidate_facts"]:
        reference = fact.pop("source_context_ref", None)
        if reference is not None:
            assert historical_catalog[reference] == fact["source_context"]
    accepted_bytes = options.accepted_report.read_bytes()
    accepted = json.loads(accepted_bytes)
    assert accepted["input"] == raw and accepted["status"] == "contract_passed"
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            run = db.get(AgentRun, source["source_run_id"])
            definitions, _, _ = _capture_current_definitions(db, run, "test_planning_route_gap_reviewer")
            definition, _ = definitions["test_planning_route_gap_reviewer"]
            # 复用当前正式定义和运行时投影，不另造模型投影逻辑。
            projection = deepcopy(definition.runtime_config["input_projection"])
            model_input, projection_diagnostics = _project_agent_map_input(definition=definition, raw_item=trusted)
            contract = AgentOutputContract(deepcopy(trusted),
                AgentMapConfig(item_postprocessor="testing.postprocess_planning_route_repair_item"))
            context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                run_id=run.id, node_key="support-projection-offline", run_input=deepcopy(run.input_payload))
            assert contract.validate(definition, context, deepcopy(accepted["raw_output"])) == accepted["normalized_output"]
            assert contract.input_payload == trusted and contract.input_payload != model_input
            assert all("source_anchor" in fact for fact in contract.input_payload["candidate_facts"])
            assert "source_contexts" not in model_input
            for full, projected in zip(trusted["candidate_facts"], model_input["candidate_facts"], strict=True):
                assert full["fact_id"] == projected["fact_id"]
                assert full.get("source_context") == projected.get("source_context")
                assert all(full[key] == value for key, value in projected.items())
                assert "source_anchor" not in projected and "source_context_ref" not in projected
                assert "current_test_design_item_indexes" not in projected
            # 正式规划使用摘要作用域，原本没有 source_anchor；该路径也必须保留已有原文。
            ordinary = {key: deepcopy(value) for key, value in trusted.items() if key != "candidate_facts"}
            ordinary["candidate_facts"] = [
                {**_planning_fact_model_view(fact), "scope_id": scope["scope_id"]}
                for scope in _planning_scopes([fact for fact in trusted["candidate_facts"] if fact["status"] == "effective"])
                for fact in scope["facts"]
            ]
            ordinary_model, ordinary_diagnostics = _project_agent_map_input(definition=definition, raw_item=ordinary)
            assert ordinary_model == ordinary
            assert all("source_anchor" not in fact for fact in ordinary["candidate_facts"])
            assert not db.new and not db.dirty and not db.deleted
        finally:
            db.rollback()
    assert action_state_hash(raw) == original_hash
    sdk_path = Path(__file__).resolve().parents[3] / "modules/agent_platform/sdk_adapter.py"
    sdk_tree = ast.parse(sdk_path.read_text(encoding="utf-8"))
    managed = next(node for node in ast.walk(sdk_tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "invoke_agent")
    child_call = next(node for node in ast.walk(managed) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "run_agent_async")
    keywords = {item.arg: ast.unparse(item.value) for item in child_call.keywords}
    assert keywords["input_payload"] == "child_model_input" and keywords["output_contract"] == "contract"
    contract_call = next(node for node in ast.walk(managed) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "AgentOutputContract")
    assert ast.unparse(contract_call.args[0]) == "deepcopy(input_payload)"
    projection_call = next(node for node in ast.walk(managed) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "_project_agent_map_input")
    assert {item.arg: ast.unparse(item.value) for item in projection_call.keywords} == {"definition": "child", "raw_item": "input_payload"}
    transcript = options.source.with_suffix(".requests.jsonl")
    events = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    request = next(event for event in events if event["event"] == "sdk_request_content")
    old_items = request["input_items"]
    assert len(old_items) == 1 and old_items[0]["role"] == "user"
    actual_model_input = source.get("model_input", raw)
    assert json.loads(old_items[0]["content"]) == actual_model_input
    projected_items = deepcopy(old_items)
    projected_items[0]["content"] = json.dumps(model_input, ensure_ascii=False, separators=(",", ":"))
    context_bytes_delta = len(encode(old_items).encode()) - len(encode(projected_items).encode())
    raw_stats = _payload_size_diagnostics(raw)
    variants = {"saved_complete_input": raw_stats,
        "historical_actual_model_input": _payload_size_diagnostics(actual_model_input),
        "current_inline_model_input": _payload_size_diagnostics(model_input),
        "current_complete_trusted_input": _payload_size_diagnostics(trusted),
        "ordinary_planning_fact_model_view": _payload_size_diagnostics(ordinary_model)}
    for item in variants.values():
        item["reduction_from_raw_chars_ratio"] = round(1 - item["json_chars"] / raw_stats["json_chars"], 6)
        item["reduction_from_raw_bytes_ratio"] = round(1 - item["json_bytes"] / raw_stats["json_bytes"], 6)
    context_facts = [fact for fact in raw["candidate_facts"] if "source_context" in fact]
    distinct_contexts = {encode(fact["source_context"]) for fact in context_facts}
    result = {"status": "passed", "source_report": str(options.source.resolve()),
        "source_report_sha256": hashlib.sha256(source_bytes).hexdigest(), "source_input_sha256": original_hash,
        "accepted_report_sha256": hashlib.sha256(accepted_bytes).hexdigest(),
        "model_requests_sent": 0, "online_database_writes": 0,
        "fact_count": len(raw["candidate_facts"]), "context_occurrences": len(context_facts),
        "distinct_contexts": len(distinct_contexts),
        "original_repeated_context_json_chars": sum(len(encode(fact["source_context"])) for fact in context_facts),
        "unique_context_json_chars": sum(len(value) for value in distinct_contexts),
        "variants": variants,
        "projection_config": projection, "projection_diagnostics": projection_diagnostics,
        "inline_source_context_and_semantic_fields_unchanged": True,
        "full_source_anchors_retained_in_trusted_contract": True,
        "ordinary_facts_without_anchors_keep_source_context": True,
        "ordinary_projection_diagnostics": ordinary_diagnostics,
        "source_facts_status_assertion_and_raw_cell_text_unchanged": True,
        "current_production_fact_view_and_projection_used": True,
        "historical_actual_output_validated_against_complete_contract": True,
        "model_view_and_trusted_contract_are_distinct": True,
        "managed_boundary_static_wiring_verified": True,
        "managed_boundary_live_model_execution_verified": False,
        "sdk_adapter_sha256": hashlib.sha256(sdk_path.read_bytes()).hexdigest(),
        "actual_original_request_usage": source["model_requests"][0]["usage"],
        "actual_original_sdk_context_bytes": source["model_requests"][0]["measured_context_bytes"],
        "projected_sdk_context_bytes_with_same_prompt_and_tools": source["model_requests"][0]["measured_context_bytes"] - context_bytes_delta,
        "token_measurement_limit": "estimated_input_tokens沿用项目UTF-8字节/4启发式，仅用于并列比较，不是DeepSeek tokenizer结果或实际新用量。原请求usage含系统提示和工具定义；投影未调用模型。",
        "implementation_limit": "托管专家边界静态核对复用现有投影，完整contract保留来源锚点；普通规划事实仅复验现有纯字段投影，不伪造路由响应或应用规划状态；本报告只回放真实历史响应，未调用模型。",
        "projected_model_input": model_input}
    assert options.source.read_bytes() == source_bytes and options.accepted_report.read_bytes() == accepted_bytes
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "fact_count", "context_occurrences", "distinct_contexts", "variants", "original_repeated_context_json_chars", "unique_context_json_chars", "actual_original_sdk_context_bytes", "projected_sdk_context_bytes_with_same_prompt_and_tools")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
