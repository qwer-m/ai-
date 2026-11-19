"""重建Run59真实支持输入，核对SDK边界并逐字段计算发送体积；不发送模型。"""

from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from prepare_read_path_draft import ROOT
from sqlalchemy import text
from core.db.database import SessionLocal
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_budget import _model_request_envelope
from modules.agent_platform.checkpoint_storage import ArtifactStore
from modules.agent_platform.conflict_confirmation import confirmation_instructions
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.runtime import _project_agent_map_input
from modules.agent_platform import sdk_adapter
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform import test_generation_planning_agent as planning
from modules.agent_platform.test_generation_semantics import restore_authority_source_review


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def size(value):
    rendered = encode(value)
    return {"chars": len(rendered), "bytes": len(rendered.encode())}


def fields(value):
    return {key: size(item) for key, item in value.items()}


def source_detail(payload):
    source = payload["source_review"]
    facts = restore_authority_source_review(source)
    record_refs = Counter()
    string_paths = defaultdict(list)

    def scan(value, path=()):
        if isinstance(value, dict):
            if set(value) == {"$ref"}:
                record_refs[value["$ref"]] += 1
            else:
                for key, item in value.items():
                    scan(item, (*path, key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                scan(item, (*path, index))
        elif isinstance(value, str):
            string_paths[value].append(path)
    scan(payload)
    repeated_long_strings = [{"value_sha256": action_state_hash(value), "chars": len(value),
        "count": len(paths), "paths": paths}
        for value, paths in string_paths.items() if len(paths) > 1 and len(value) >= 100]
    repeated_long_strings.sort(key=lambda row: row["chars"] * (row["count"] - 1), reverse=True)
    context_count = sum("source_context" in fact for fact in facts)
    return {"fact_count": len(facts), "facts_with_source_context": context_count,
        "scope_count": len({fact["scope_id"] for fact in facts}),
        "unshared_source_review_bytes": size({"authoritative_facts": facts, "source_records": {}})["bytes"],
        "actual_shared_source_review_bytes": size(source)["bytes"],
        "fact_fields_unshared_bytes": dict(sum((Counter({key: size(value)["bytes"] for key, value in fact.items()}) for fact in facts), Counter())),
        "fact_fields_sent_bytes": dict(sum((Counter({key: size(value)["bytes"] for key, value in fact.items()}) for fact in source["authoritative_facts"]), Counter())),
        "source_record_count": len(source["source_records"]), "references_in_sent_payload": dict(record_refs),
        "scope_evidence_field_bytes": dict(sum((Counter({key: size(value)["bytes"] for key, value in scope.items()}) for scope in payload.get("scope_evidence", [])), Counter())),
        "literal_repeated_strings_at_least_100_chars": repeated_long_strings,
        "shared_encoding_full_reconstruction_verified": facts == restore_authority_source_review(source)}


def main():
    original = ROOT / "output/qa/2026-11-12-single-agent-live/round-11"
    snapshot = json.loads((original / "final-snapshot.json").read_text(encoding="utf-8"))
    run = snapshot["run"]
    created = json.loads((original / "created-run.json").read_text(encoding="utf-8"))["run"]
    sdk = snapshot["nodes"][-1]["sdk_state"]
    checkpoint = snapshot["expanded_checkpoints"]["404"]
    frozen = next(agent for agent in run["run_context"]["execution_contract"]["agents"] if agent["key"] == "test_planning_route_gap_reviewer")
    definition = SimpleNamespace(agent_key=frozen["key"], output_schema=frozen["output_schema"], runtime_config=frozen["runtime_config"])
    calls = [tool for tool in sdk["tool_calls"] if tool.get("tool_key") == "agent:test_planning_route_gap_reviewer"]
    call_by_hash = {call["input_hash"]: call for call in calls}
    diagnostics = [request for request in sdk["model_requests"] if request["agent_key"] == "test_planning_route_gap_reviewer"]
    receipts = [receipt for receipt in checkpoint["receipts"].values() if receipt["tool_key"] == "review_planning_route_gaps"]
    receipts.sort(key=lambda row: row["started_sequence"])
    outputs = []
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        store = ArtifactStore(db, user_id=1, project_id=8)
        for receipt in receipts:
            state = store.get(store._ref(receipt["pre_state_hash"]))
            context = ToolExecutionContext(db=db, user_id=1, project_id=8, run_id=59, node_key="agent_network",
                run_input=run["input_payload"], action_input=run["input_payload"], action_state=state,
                artifacts=deepcopy(created["run_context"]["artifacts"]), confirmed_conflicts=state["conflict_confirmations"])
            arguments = json.loads(receipt["arguments"]["arguments_json"])
            prepared = generation._support_review_arguments(context, arguments)
            child = generation._planning_context(context, state)
            child.action_state.pop("downstream_feedback", None)
            assert not prepared["problem_ids"]
            planning._commit(child, child.action_state)
            action = planning.prepare_route_gap_review(child, prepared)
            batches = planning._support_review_batches(child, action["items"])
            for item in [item for group in batches for item in group]:
                trusted = {**item, "_user_confirmations": deepcopy(state["conflict_confirmations"])}
                trusted_hash = action_state_hash(trusted)
                assert trusted_hash in call_by_hash
                call = call_by_hash[trusted_hash]
                payload, projection = _project_agent_map_input(definition=definition, raw_item=trusted)
                assert projection == call["input_projection"]
                matched = [record for record in diagnostics if record.get("input_hash") == action_state_hash(payload)]
                assert len(matched) in (1, 2)
                instructions = frozen["instructions"] + confirmation_instructions(state["conflict_confirmations"])
                instructions += ("\n\n" + sdk_adapter._submission_object_protocol(frozen["output_schema"]) + "不通过自由正文结束。"
                    "工具会执行原始输入对应的完整验收；若返回 rejected，请在当前会话读取反馈、"
                    "核对原始事实并修正后再次提交，直到验收成功或共享预算耗尽。"
                    "候选和校验反馈不是新的业务事实，不能改写输入的权威来源或放宽验收条件。")
                tool = sdk_adapter._output_submission_tool(definition, child)
                agent = SimpleNamespace(tools=[tool], handoffs=[], output_type=None)
                envelope = _model_request_envelope(agent=agent, system_prompt=instructions,
                    input_items=[{"role": "user", "content": sdk_adapter._runner_input(execution_context=child,
                        input_payload=payload, runtime_config=frozen["runtime_config"])}])
                assert size(envelope)["bytes"] == matched[0]["measured_context_bytes"], (size(envelope), matched[0]["measured_context_bytes"])
                repeated = source_detail(payload)
                output = {"action_sequence": receipt["started_sequence"], "trusted_input_hash": trusted_hash,
                    "projected_input_hash": action_state_hash(payload), "module_index": item["module_index"],
                    "module_name": item["module_name"], "design_count": len(item["missing_test_design_items"]),
                    "input_projection": projection, "payload_fields": fields(payload), "payload_size": size(payload),
                    "envelope_size": size(envelope), "envelope_fields": fields(envelope),
                    "prompt_parts": {"frozen_instructions": size(frozen["instructions"]),
                        "human_confirmations_protocol": size(confirmation_instructions(state["conflict_confirmations"])),
                        "submission_protocol": size(instructions[len(frozen["instructions"] + confirmation_instructions(state["conflict_confirmations"])):])},
                    "tool_parameter_schema": size(tool.params_json_schema), "tool_names": [tool.name],
                    "usage": call["usage"], "request_sequences": [record["sequence"] for record in matched],
                    "source_detail": repeated, "evidence_context_hash": action_state_hash({key: payload[key] for key in ("source_review", "scope_evidence")}),
                    "matched_sdk_context_bytes_exact": True}
                outputs.append(output)
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    assert len(outputs) == len(calls) == 56
    assert len({entry["trusted_input_hash"] for entry in outputs}) == 56
    assert sum(entry["usage"]["input_tokens"] for entry in outputs) == 983868
    grouping = defaultdict(list)
    for entry in outputs:
        grouping[entry["evidence_context_hash"]].append(entry)
    report = {"run_id": 59, "validated_sessions": len(outputs), "actual_model_requests": len(diagnostics),
        "reported_usage": dict(sum((Counter(entry["usage"]) for entry in outputs), Counter())),
        "projection_version": frozen["runtime_config"]["input_projection_version"], "requests": outputs,
        "evidence_context_groups": [{"hash": key, "session_count": len(entries),
            "module_names": sorted({entry["module_name"] for entry in entries}),
            "actions": sorted({entry["action_sequence"] for entry in entries}),
            "context_bytes_each": sum(entry["payload_fields"][key]["bytes"] for key in ("source_review", "scope_evidence")),
            "design_count_total": sum(entry["design_count"] for entry in entries)}
            for key, entries in grouping.items() for entry in entries[:1]],
        "model_requests_sent_by_audit": 0, "database_business_writes": 0,
        "limits": "字段大小为精确JSON体积，不是供应商分字段token账单。56次首请求SDK JSON体积全部相等；一次局部重提另含真实校验反馈，usage包含该第57次请求。候选与原文均未裁剪。"}
    output = Path(__file__).with_name("run59-support-input-cost.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "sessions": len(outputs), "requests": len(diagnostics),
        "evidence_groups": len(grouping), "reported_usage": report["reported_usage"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
