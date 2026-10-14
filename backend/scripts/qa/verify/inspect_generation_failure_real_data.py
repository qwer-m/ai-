"""只读导出真实失败生成项、可见事实及同运行权威协调证据，不调用模型。"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _project_agent_map_input


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def bound_fact_ids(candidate: dict) -> set[str]:
    result = set()
    for case in candidate["test_cases"]:
        for condition in case.get("preconditions", []):
            result.update(condition.get("fact_ids", []))
        result.update(case.get("test_input", {}).get("fact_ids", []))
        for step in case.get("steps", []):
            for values in step.get("fact_bindings", {}).values():
                result.update(values)
    return result


def inspect(node_id: int, item_index: int, authority_fact_ids: list[str]) -> dict:
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        repo = AgentRunRepository(db)
        node = db.get(AgentNodeRun, node_id)
        assert node and node.status == "failed"
        before = digest([node.input_payload, node.output_payload, node.sdk_state])
        all_inputs = repo.read_node_input(node)["items"]
        raw = deepcopy(all_inputs[item_index])
        state = deepcopy(next(item for item in node.sdk_state["items"] if item["item_index"] == item_index))
        definition = db.get(AgentDefinition, node.agent_definition_id)
        model_input, projection = _project_agent_map_input(definition=definition, raw_item=raw)
        # 通过持久请求尺寸核对投影重建，报告明确这是恢复的模型输入而非捕获HTTP报文。
        assert projection["model_json_chars"] == state["request"]["model_json_chars"]
        assert projection["model_json_bytes"] == state["request"]["model_json_bytes"]
        observations = state["error_diagnostic"]["tool_observations"]
        calls = state["tool_calls"]
        submissions = []
        for call, observation in zip(calls, observations, strict=True):
            assert call["tool_key"] == observation["tool_key"]
            candidate = call["arguments"]
            submissions.append({"tool_key": call["tool_key"], "tool_call_id": observation["tool_call_id"],
                                "candidate": candidate, "feedback": observation["feedback"],
                                "case_count": len(candidate["test_cases"]),
                                "bound_fact_ids": sorted(bound_fact_ids(candidate))})
        required = set(raw["case_fact_contract"]["required_fact_ids"])
        visible = {fact["fact_id"]: fact for fact in model_input["authoritative_facts"]}
        designs = {item["test_design_item_id"] for item in model_input["plan"]["test_design_items"]}
        assert required <= set(visible)
        for observation in observations:
            details = observation["feedback"]["details"]
            assert set(details.get("missing_fact_ids", [])) <= set(visible)
            assert set(details.get("missing_test_design_item_ids", [])) <= designs
        facts = []
        for fact in raw["authoritative_facts"]:
            fact_id = fact["fact_id"]
            facts.append({**deepcopy(fact), "visible_to_model": fact_id in visible,
                          "required_by_contract": fact_id in required,
                          "absent_from_candidate_bindings": [i + 1 for i, submission in enumerate(submissions)
                                                             if fact_id not in submission["bound_fact_ids"]],
                          "routed_design_ids": raw["case_fact_contract"]["fact_design_item_ids"].get(fact_id, [])})
        authority = repo.latest_node_run(run_id=node.run_id, node_key="authority")
        authority_definition = db.get(AgentDefinition, authority.agent_definition_id)
        authority_evidence = []
        requested = set(authority_fact_ids)
        for index, source in enumerate(repo.read_node_input(authority)["items"]):
            authority_model, authority_projection = _project_agent_map_input(definition=authority_definition, raw_item=source)
            matches = [fact for fact in authority_model["authoritative_facts"] if fact["fact_id"] in requested]
            if not matches:
                continue
            record = next(record for record in authority.output_payload["items"] if record["item_index"] == index)
            item_state = next(record for record in authority.sdk_state["items"] if record["item_index"] == index)
            authority_evidence.append({"node_run_id": authority.id, "item_index": index,
                                       "module_name": source["module"]["name"],
                                       "requested_facts_co_visible": requested <= {fact["fact_id"] for fact in matches},
                                       "matched_facts": matches, "model_input": authority_model,
                                       "projection": authority_projection, "actual_output": record,
                                       "actual_usage": item_state["usage"]})
        report = {"run_id": node.run_id, "node_run_id": node_id, "item_index": item_index,
                  "status": state["status"], "error_type": state["error_type"],
                  "model_input_evidence": "按绑定定义重建投影，并核对持久化请求字符数及字节数；不是HTTP报文捕获",
                  "raw_input": raw, "model_input": model_input, "projection": projection,
                  "instructions": definition.instructions, "output_schema": definition.output_schema,
                  "request_budget_config": {key: definition.runtime_config.get(key)
                                            for key in ("max_turns", "max_output_tokens", "subagent_keys", "tool_keys")},
                  "required_facts_all_visible": True, "missing_facts_and_designs_all_visible": True,
                  "fact_count": len(facts), "design_count": len(designs), "case_budget": raw["case_budget"],
                  "fact_table": facts, "submissions": submissions,
                  "actual_tool_counts": dict(Counter(call["tool_key"] for call in calls)),
                  "actual_budget_usage": state["error_diagnostic"]["budget_usage"],
                  "same_module_batches": [{"item_index": index, "batch_id": item["batch"]["batch_id"],
                                           "facts": len(item["authoritative_facts"]), "case_budget": item["case_budget"],
                                           "coverage_focus": item["batch"]["coverage_focus"]}
                                          for index, item in enumerate(all_inputs)
                                          if item["batch"]["module_index"] == raw["batch"]["module_index"]],
                  "authority_fact_ids": authority_fact_ids, "authority_evidence": authority_evidence,
                  "model_requests_by_inspection": 0, "online_database_writes": 0}
        db.refresh(node)
        assert digest([node.input_payload, node.output_payload, node.sdk_state]) == before
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-id", type=int, required=True)
    parser.add_argument("--item-index", type=int, required=True)
    parser.add_argument("--authority-fact-id", action="append", default=[])
    parser.add_argument("--report", type=Path, required=True)
    options = parser.parse_args()
    report = inspect(options.node_id, options.item_index, options.authority_fact_id)
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f"# Run {report['run_id']} 生成失败事实核对", "",
             f"节点 {report['node_run_id']}，item {report['item_index']}，目标 {report['case_budget']} 条用例。",
             "仅回放真实持久记录，不调用模型、不写线上库。各轮缺失指候选所有字段的事实绑定，不等于语义覆盖证明。", "",
             "| 事实 ID | 真实事实 | 模型可见 | 未绑定轮次 |", "| --- | --- | --- | --- |"]
    for fact in report["fact_table"]:
        lines.append(f"| {fact['fact_id']} | {fact['assertion'].replace('|', '／')} | 是 | "
                     + ",".join(str(index) for index in fact["absent_from_candidate_bindings"]) + " |")
    lines.extend(["", "| 提交轮次 | 用例数量 | 真实拒绝原因 |", "| --- | --- | --- |"])
    for index, submission in enumerate(report["submissions"], 1):
        lines.append(f"| {index} | {submission['case_count']} | {submission['feedback']['message'].replace('|', '／')} |")
    options.report.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("run_id", "node_run_id", "item_index", "fact_count", "design_count",
                     "case_budget", "required_facts_all_visible", "actual_tool_counts", "same_module_batches")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
