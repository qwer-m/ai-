"""使用 Run41 实际已保存候选，验证提交协议层级反馈；不调用模型或恢复失败运行。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.sdk_adapter import (
    ToolArgumentsJSONError, ToolArgumentsValidationError, _parse_function_arguments, _submission_object_protocol,
    _strict_schema_preserves_contract,
)
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS


def verify(options):
    paths = [options.checkpoint, options.focused_inputs]
    originals = {str(path.resolve()): path.read_bytes() for path in paths}
    saved, focused = [json.loads(path.read_text("utf-8")) for path in paths]
    assert saved["run_id"] == focused["source_run_id"] == 41
    checkpoint = saved["checkpoint"]
    failed = focused["failed_child_diagnostic"]
    assert focused["items"][0]["historically_executed"]
    assert not focused["items"][1]["historically_executed"]
    assert action_state_hash(focused["items"][0]["input"]) == failed["input_hash"]
    assert len(failed["tool_observations"]) == 6 and failed["submissions"] == []
    reference = next(record for record in checkpoint["validated_agent_calls"].values()
                     if record["identity"]["agent_key"] == "test_planning_route_gap_reviewer"
                     and record["output"]["module_index"] == 8)
    raw = deepcopy(reference["raw_output"])
    spec = next(item for item in BUILTIN_AGENT_SPECS if item["agent_key"] == "test_planning_route_gap_reviewer")
    schema = spec["output_schema"]
    serialized = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    assert _parse_function_arguments("submit_result", schema, serialized) == raw
    protocol = _submission_object_protocol(schema)
    assert '"declared_fields":["decisions","module_index"]' in protocol
    assert '"additional_fields_allowed":false' in protocol
    assert spec["runtime_config"]["max_turns"] == 6
    assert _strict_schema_preserves_contract(schema)
    repair = next(item for item in BUILTIN_AGENT_SPECS if item["agent_key"] == "test_generation_batch_repairer")
    assert not _strict_schema_preserves_contract(repair["output_schema"])

    # 仅改变真实原候选的协议层级，不伪造业务事实；这不是历史坏响应全文重放。
    wrapped = json.dumps({"arguments": serialized}, ensure_ascii=False)
    try:
        _parse_function_arguments("submit_result", schema, wrapped)
    except ToolArgumentsValidationError as error:
        diagnostic = error.diagnostic
        shape = diagnostic["object_fields_at_instance_path"]
        assert diagnostic["instance_path"] == []
        assert set(diagnostic["missing_fields"]) == {"module_index", "decisions"}
        assert shape["declared_fields"] == ["decisions", "module_index"]
        assert shape["received_fields"] == ["arguments"]
        assert not shape["additional_fields_allowed"]
        assert diagnostic["misplaced_fields"] == [{"received_path": ["arguments"], "expected_path": [],
            "fields": ["module_index", "decisions"], "json_encoded": True}]
    else:
        raise AssertionError("额外调用包装不得被自动拆开或通过验收")

    strict_cases = []
    malformed = serialized + "}"
    duplicate = '{"module_index":' + json.dumps(raw["module_index"]) + ',' + serialized[1:]
    for label, value, expected_kind in (("extra_closing_brace", malformed, "invalid_json_syntax"),
                                        ("duplicate_real_module_index", duplicate, "duplicate_key")):
        try:
            _parse_function_arguments("submit_result", schema, value)
        except ToolArgumentsJSONError as error:
            assert error.diagnostic["error_kind"] == expected_kind
            strict_cases.append({"case": label, "diagnostic": error.diagnostic})
        else:
            raise AssertionError(label)
    assert all(Path(path).read_bytes() == value for path, value in originals.items())
    sdk_path = Path(__file__).resolve().parents[3] / "modules/agent_platform/sdk_adapter.py"
    return {"status": "passed", "source_run_id": 41, "source_node_id": saved["node_id"],
        "source_sha256": {path: hashlib.sha256(value).hexdigest() for path, value in originals.items()},
        "sdk_sha256": hashlib.sha256(sdk_path.read_bytes()).hexdigest(),
        "actual_initial_review_raw_output_sha256": action_state_hash(raw),
        "actual_initial_review_input_sha256": reference["identity"]["input_hash"],
        "valid_real_candidate_preserved": True, "submission_protocol": protocol,
        "wrapped_real_candidate_rejection": diagnostic, "strict_json_rejections": strict_cases,
        "historical_failure_input_sha256": failed["input_hash"],
        "historical_rejections": failed["tool_observations"],
        "historical_response_replay_limit": "6次真实坏JSON只保存sha256及截取文本，未保留全文；本QA负例来自真实已验收原候选的协议变形，不能声称逐字重放原6次响应",
        "successful_candidate_used_as_retry_seed": False,
        "specialist_max_turns": spec["runtime_config"]["max_turns"],
        "model_requests_sent": 0, "business_database_queries": 0, "online_database_writes": 0,
        "source_files_unchanged": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--focused-inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    assert not options.output.exists(), "请使用新报告路径"
    result = verify(options)
    options.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "valid_real_candidate_preserved",
        "specialist_max_turns", "model_requests_sent", "online_database_writes")}, ensure_ascii=False))
