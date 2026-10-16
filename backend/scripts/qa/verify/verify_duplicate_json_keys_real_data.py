"""只读回放真实模型原始参数，验证重复键拒绝和既有提交反馈；不调用模型或数据库。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.exceptions import ModelBehaviorError
from agents.tool_context import ToolContext

from core.db.model_defs import AgentDefinition
from modules.agent_platform.action_journal import action_state_hash, project_action_failures
from modules.agent_platform.autonomous_tools import AutonomousToolLoop
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.sdk_adapter import (
    AgentOutputContract, StructuredOutputJSONError, ToolArgumentsJSONError,
    _normalize_final_output, _output_submission_tool, _parse_function_arguments,
)
from modules.agent_platform.serialization import DuplicateJSONKeyError, strict_json_loads
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS
from verify_action_receipt_order_real_data import bind_real_start_events


class ObjectPairs(list):
    """QA 保留每个原始键值对，记录旧默认解析到底覆盖了什么。"""


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def duplicate_entries(value, path="$"):
    result = []
    if isinstance(value, ObjectPairs):
        seen = {}
        for key, item in value:
            current = f"{path}.{key}"
            if key in seen:
                result.append({"path": current, "first_value": seen[key], "later_value": item})
            else:
                seen[key] = item
            result.extend(duplicate_entries(item, current))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.extend(duplicate_entries(item, f"{path}[{index}]"))
    return result


def read_submission(path):
    calls = [item for line in path.read_text(encoding="utf-8").splitlines()
             for event in [strict_json_loads(line)] if event.get("event") == "sdk_response_content"
             for item in event["response"]["output"]
             if item.get("type") == "function_call" and item.get("name") == "submit_result"]
    assert len(calls) == 1
    return calls[0]


def encoded_decisions(arguments):
    """只将真实原始数组二次序列化，保留内部原始重复键及全部业务文字。"""
    marker = '"decisions"'
    start = arguments.index(":", arguments.index(marker)) + 1
    while arguments[start].isspace():
        start += 1
    _, length = json.JSONDecoder(object_pairs_hook=ObjectPairs).raw_decode(arguments[start:])
    return arguments[:start] + json.dumps(arguments[start:start + length], ensure_ascii=False) + arguments[start + length:]


def rejected(call, error_class, *, expected_key=None):
    try:
        call()
    except error_class as error:
        diagnostic = deepcopy(error.diagnostic)
        if expected_key is not None:
            assert diagnostic["error_kind"] == "duplicate_key"
            assert diagnostic["duplicate_key"] == expected_key
            assert isinstance(error.__cause__, DuplicateJSONKeyError)
            assert not isinstance(error.__cause__, json.JSONDecodeError)
        return {"status": "rejected", "error_type": type(error).__name__, "diagnostic": diagnostic}
    raise AssertionError("真实损坏参数未被拒绝")


def failure_projection_checks(checkpoint, bad_call, schema, event_audit, run_id):
    """用真实正常失败回执作基线，另将真实错误参数接入其结构检验投影边界。"""
    original = deepcopy(checkpoint)
    run = next(item for item in event_audit["runs"] if item["run_id"] == run_id)
    node = next(item for item in run["nodes"] if item["checkpoint_sha256"] == action_state_hash(checkpoint))
    # 只使用真实事件顺序；JSON 对象成员顺序不能证明“后续成功”或历史前缀。
    historical, event_bindings, _ = bind_real_start_events(
        checkpoint, event_audit, run_id=run_id, node_id=node["node_id"])
    visible = project_action_failures(historical, state=checkpoint["state"])
    assert visible == node["event_order_failure_projection"] and visible
    for observation in visible:
        receipt = checkpoint["receipts"][observation["tool_call_id"]]
        assert observation["arguments"] == strict_json_loads(receipt["arguments"]["arguments_json"])
        assert observation["error"] == receipt["error"]
        assert observation["arguments_hash"] == receipt["arguments_hash"]
        assert observation["arguments_sha256"] == sha256(receipt["arguments"]["arguments_json"].encode())
    actual_receipt = historical["receipts"][visible[-1]["tool_call_id"]]
    derived = []
    for name, raw in (("actual_duplicate_arguments", bad_call["arguments"]),
                      ("single_prefix_damage_of_actual_arguments", "~" + bad_call["arguments"])):
        error = rejected(lambda: _parse_function_arguments(bad_call["name"], schema, raw), ToolArgumentsJSONError)
        receipt = deepcopy(actual_receipt)
        receipt.update(tool_key=bad_call["name"], tool_call_id=bad_call["call_id"],
            arguments={"arguments_json": raw}, arguments_hash=action_state_hash({"arguments_json": raw}),
            error={"error_type": error["error_type"], "recoverable": True,
                   "feedback": {"error_type": error["error_type"], "diagnostic": deepcopy(error["diagnostic"])}})
        replay = {**deepcopy(historical), "receipts": {receipt["tool_call_id"]: receipt}}
        before = deepcopy(replay)
        observation = project_action_failures(replay, state=replay["state"])
        assert len(observation) == 1 and observation[0]["arguments"] is None
        assert observation[0]["error"] == receipt["error"]
        assert observation[0]["tool_key"] == bad_call["name"] and observation[0]["tool_call_id"] == bad_call["call_id"]
        assert observation[0]["arguments_sha256"] == error["diagnostic"]["arguments_sha256"]
        assert observation[0]["arguments_hash"] == receipt["arguments_hash"]
        assert replay == before
        derived.append({"case": name, "projection": observation[0]})
    assert checkpoint == original
    return {"historical_valid_failed_receipts": visible, "real_event_bindings": event_bindings,
        "derived_invalid_arguments_cases": derived,
        "source_receipt_tool_call_id": actual_receipt["tool_call_id"], "checkpoint_unchanged": True,
        "previous_chronology_claim_corrected": "此前把JSON成员序误作事件顺序，后续成功消解失败的声明撤回；真实最后一次失败必须可见",
        "historical_limit": "正常失败来自真实Run39完整回执与事件顺序的离线关联，不是线上迁移；错误参数接入回执结构属于本地派生边界，未声称线上已持久发生"}


async def verify(options):
    paths = [options.duplicate_report, options.unique_report, options.run_report,
             options.duplicate_report.with_suffix(".requests.jsonl"), options.unique_report.with_suffix(".requests.jsonl"),
             options.checkpoint, options.event_audit]
    originals = {str(path.resolve()): path.read_bytes() for path in paths}
    bad_report, good_report, run_report = [strict_json_loads(path.read_text(encoding="utf-8")) for path in paths[:3]]
    bad_call, good_call = [read_submission(path) for path in paths[3:5]]
    bad, good = bad_call["arguments"], good_call["arguments"]
    assert bad_report["input"] == good_report["input"]
    assert action_state_hash(bad_report["input"]) == bad_report["input_sha256"] == good_report["input_sha256"]
    assert bad_report["source_run_id"] == good_report["source_run_id"] == run_report["new_run_id"]
    duplicates = duplicate_entries(json.loads(bad, object_pairs_hook=ObjectPairs))
    assert duplicates and not duplicate_entries(json.loads(good, object_pairs_hook=ObjectPairs))
    first_duplicate_key = duplicates[0]["path"].rsplit(".", 1)[-1]
    spec = next(item for item in BUILTIN_AGENT_SPECS if item["agent_key"] == "test_planning_route_gap_reviewer")
    definition = AgentDefinition(**{key: deepcopy(value) for key, value in spec.items() if key != "version"})
    schema = deepcopy(definition.output_schema)
    failure_projection = failure_projection_checks(strict_json_loads(options.checkpoint.read_text(encoding="utf-8")),
        bad_call, schema, strict_json_loads(options.event_audit.read_text(encoding="utf-8")), run_report["new_run_id"])
    context = ToolExecutionContext(db=None, user_id=run_report["source_user_id"],
        project_id=run_report["request"]["project_id"], run_id=run_report["new_run_id"],
        node_key="duplicate-json-keys-local-replay", run_input=deepcopy(run_report["request"]["input_payload"]))
    contract = AgentOutputContract(deepcopy(good_report["input"]),
        AgentMapConfig(item_postprocessor="testing.postprocess_planning_route_repair_item"))
    checks = {}
    for name, arguments in (("original_raw_arguments", bad), ("json_encoded_decisions_array", encoded_decisions(bad)),
                            ("json_encoded_root_object", json.dumps(bad, ensure_ascii=False))):
        checks[name] = rejected(lambda: _parse_function_arguments("submit_result", schema, arguments),
                                ToolArgumentsJSONError, expected_key=first_duplicate_key)
    checks["structured_final_text"] = rejected(lambda: _normalize_final_output(bad, schema),
        StructuredOutputJSONError, expected_key=first_duplicate_key)
    # 此项仅在真实正文插入一个控制字符，检验既有 C0 转义不能遮蔽原有重复键。
    controlled = bad.replace('"reason": "', '"reason": "\t', 1)
    assert controlled != bad
    checks["control_character_escape_then_duplicate"] = rejected(lambda: _normalize_final_output(controlled, schema),
        StructuredOutputJSONError, expected_key=first_duplicate_key)
    # 在原始 JSON 前加入单一损坏符号：先遇到语法错误也不能进入猜测修补。
    checks["syntax_damage_before_duplicate"] = rejected(lambda: _normalize_final_output("~" + bad, schema),
        StructuredOutputJSONError)
    unique_parsed = _parse_function_arguments("submit_result", schema, good)
    assert unique_parsed == good_report["raw_output"]
    assert _parse_function_arguments("submit_result", schema, encoded_decisions(good)) == unique_parsed
    assert _parse_function_arguments("submit_result", schema, json.dumps(good, ensure_ascii=False)) == unique_parsed
    assert _normalize_final_output(good, schema)[0] == unique_parsed
    assert contract.validate(definition, context, deepcopy(unique_parsed)) == good_report["normalized_output"]
    checks["unique_historical_response"] = {"status": "accepted", "original_and_valid_double_serialization_unchanged": True,
        "full_current_output_contract_matches_saved_result": True,
        "semantic_success_claimed": False, "semantic_note": "结构合法的历史响应仍保持原来的不支持结论，不声称其理由正确"}
    loop = AutonomousToolLoop(terminal_tool_names={"submit_result"},
        validate_submission=lambda candidate: contract.validate(definition, context, candidate),
        recoverable_errors=(ToolArgumentsJSONError,), max_tool_calls=2)
    tool = loop.wrap_tool(_output_submission_tool(definition, context), allow_feedback=True)
    bad_context = ToolContext(context=context, tool_name=bad_call["name"], tool_call_id=bad_call["call_id"], tool_arguments=bad)
    bad_feedback = strict_json_loads(await tool.on_invoke_tool(bad_context, bad))
    assert bad_feedback["status"] == "rejected"
    assert bad_feedback["feedback"]["diagnostic"]["error_kind"] == "duplicate_key"
    assert not context.executed_tools and not context.tool_calls
    try:
        loop.require_submission()
    except ModelBehaviorError:
        pass
    else:
        raise AssertionError("重复键候选不能成为已验收提交")
    good_context = ToolContext(context=context, tool_name=good_call["name"], tool_call_id=good_call["call_id"], tool_arguments=good)
    assert strict_json_loads(await tool.on_invoke_tool(good_context, good)) == unique_parsed
    accepted = loop.require_submission()
    assert accepted.raw_output == unique_parsed
    assert accepted.normalized_output == good_report["normalized_output"]
    assert context.executed_tools == ["submit_result"] and len(context.tool_calls) == 1
    assert [event["status"] for event in loop.events] == ["rejected", "accepted"]
    assert not context.action_state and not context.action_input
    assert all(Path(path).read_bytes() == value for path, value in originals.items())
    root = Path(__file__).resolve().parents[3] / "modules" / "agent_platform"
    return {"status": "passed", "verified_at": datetime.now(timezone.utc).isoformat(),
        "scope": "两次真实历史响应的本地结构与完整契约回放，不是模型新重试或工作流恢复",
        "source_run_id": context.run_id, "model_requests_sent": 0, "business_database_queries": 0, "database_writes": 0,
        "database_connection_note": "无业务 Session 或数据库查询；应用模块导入仍会初始化连接并执行连接探测",
        "source_files_unchanged": True, "source_files_sha256": {path: sha256(value) for path, value in originals.items()},
        "production_sha256": {name: sha256((root / name).read_bytes()) for name in ("sdk_adapter.py", "serialization.py", "action_journal.py")},
        "original_input_sha256": good_report["input_sha256"], "original_duplicate_arguments_sha256": sha256(bad.encode()),
        "original_unique_arguments_sha256": sha256(good.encode()), "actual_duplicate_entries": duplicates,
        "old_default_parser_lost_evidence": json.loads(bad)["decisions"][0]["support_gaps"][0]["evidence_fact_ids"],
        "derived_case_limit": "仅做真实参数的二次序列化或单字符运输损坏，用于边界验证，不作为新模型或新业务数据",
        "checks": checks, "failure_projection": failure_projection,
        "submission_loop_feedback": bad_feedback, "submission_loop_events": loop.events,
        "submission_loop_final_output": accepted.normalized_output,
        "rejected_arguments_reached_tool_handler": False, "validated_submission_count": len(context.tool_calls)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duplicate-report", type=Path, required=True)
    parser.add_argument("--unique-report", type=Path, required=True)
    parser.add_argument("--run-report", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--event-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise FileExistsError("保留已有报告，请指定新路径")
    report = asyncio.run(verify(options))
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "source_run_id", "model_requests_sent", "database_writes",
        "actual_duplicate_entries", "validated_submission_count")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
