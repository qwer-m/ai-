"""59/62真实元数据的异源CPU队列测试，不生成新SDK/业务/模型记录。"""

from copy import deepcopy
import gzip
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.request_diagnostics import ModelRequestDiagnostics


@pytest.fixture(scope="module")
def real():
    path = Path(__file__).with_name("fixtures") / "request_boundary_retention_cross_history_real.json.gz"
    raw = path.read_bytes()
    value = json.loads(gzip.decompress(raw))
    assert action_state_hash({key: part for key, part in value.items() if key != "data_sha256"}) == value["data_sha256"]
    assert len(value["records62"]) == 83 and len(value["records59"]) == 200
    assert value["provenance"]["not_a_new_business_run_or_new_model_trace"] is True
    assert value["provenance"]["run63_missing_early_boundaries_not_recreated"] is True
    yield value
    assert hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(raw).hexdigest()


def retained(real):
    records = deepcopy([*real["records62"], *real["records59"]])
    diagnostics = ModelRequestDiagnostics()
    for item in records:
        # 仅回放现存字典的保留边界；保留原run/node/sequence/时间，不调用start或生成模型响应。
        context = (item["run_id"], item["node_run_id"], item["sequence"])
        diagnostics._record_started(context, item)
        diagnostics._active.pop(context)
    return diagnostics, records


def test_all_283_actual_light_boundaries_survive_the_old_200_window_exactly(real):
    diagnostics, records = retained(real)
    assert diagnostics.requests == records
    assert len(diagnostics.requests) == 283
    assert diagnostics.requests[0]["run_id"] == 62 and diagnostics.requests[-1]["run_id"] == 59
    assert [(row["node_run_id"], row["sequence"]) for row in diagnostics.requests] == [
        (row["node_run_id"], row["sequence"]) for row in records]


def test_early_real_failed_requests_keep_the_complete_original_fault_evidence(real):
    diagnostics, records = retained(real)
    expected = [row for row in records if row["status"] == "ended_without_model_response"]
    assert len(expected) == 9
    assert diagnostics.failure_evidence() == expected
    assert all(row["run_id"] == 62 for row in diagnostics.failure_evidence())
    assert not any(row["status"] == "ended_without_model_response" for row in records[-200:])
    assert all(row["exception_chain"] for row in expected)
    assert all("response_body_sha256" in row["exception_chain"][0] for row in expected)


def test_actual_usage_and_timing_fields_are_neither_removed_nor_synthesized(real):
    diagnostics, records = retained(real)
    actual = diagnostics.requests
    fields = ("run_id", "node_run_id", "sequence", "agent_key", "model", "input_hash", "started_at",
        "finished_at", "elapsed_seconds", "status", "usage", "token_usage", "exception_chain",
        "action_tool", "action_call_id", "measured_context_bytes", "model_settings")
    for before, after in zip(records, actual, strict=True):
        for key in fields:
            assert (key in before) == (key in after)
            if key in before:
                assert before[key] == after[key]
        if before["status"] == "ended_without_model_response":
            assert "usage" not in after and "token_usage" not in after
    before_known = [row["usage"] for row in records if "usage" in row]
    after_known = [row["usage"] for row in actual if "usage" in row]
    assert before_known == after_known and len(after_known) == 274
    assert all(sum(usage[key] for usage in before_known) == sum(usage[key] for usage in after_known)
               for key in ("requests", "input_tokens", "output_tokens", "total_tokens"))


def test_requests_and_failure_evidence_remain_detached_consumer_snapshots(real):
    diagnostics, records = retained(real)
    original = action_state_hash(diagnostics.requests)
    copied = diagnostics.requests
    copied[0].clear()
    copied.clear()
    failed = diagnostics.failure_evidence()
    failed[0]["exception_chain"].clear()
    assert action_state_hash(diagnostics.requests) == original == action_state_hash(records)


def test_early_active_boundary_can_receive_its_actual_completion_after_200_later_rows(real):
    records = deepcopy([*real["records62"], *real["records59"]])
    first = deepcopy(records[0])
    for field in ("status", "finished_at", "elapsed_seconds", "usage", "token_usage",
                  "sdk_response_output_bytes", "sdk_response_reasoning_item_count"):
        first.pop(field, None)
    diagnostics = ModelRequestDiagnostics()
    context = (records[0]["run_id"], records[0]["node_run_id"], records[0]["sequence"])
    diagnostics._record_started(context, first)
    for index, record in enumerate(records[1:], 1):
        later = (record["run_id"], record["node_run_id"], record["sequence"])
        diagnostics._record_started(later, record)
        diagnostics._active.pop(later)
    assert diagnostics._active[context][0] is first
    # 只回填该真实字典原有的完成字段，不构造SDK响应或新用量。
    first.update(records[0])
    diagnostics._active.pop(context)
    assert diagnostics.requests == records


def test_json_roundtrip_keeps_every_original_scalar_and_safe_hash(real):
    diagnostics, records = retained(real)
    assert json.loads(json.dumps(diagnostics.requests, ensure_ascii=False)) == records
    assert not any(set(row).intersection({"input_context", "input_items", "system_prompt", "raw_output", "response_body"})
                   for row in records)
    # 当前所有行均无富正文；不创建虚构的200详情层，也不补造59原缺32条。
    assert real["provenance"]["source59_original_missing_first_32_not_recreated"] is True
    assert [real["records59"][0]["sequence"], real["records59"][-1]["sequence"]] == [33, 232]


def test_unused_record_cap_argument_is_removed_instead_of_becoming_a_compatibility_noop():
    assert "max_records" not in inspect.signature(ModelRequestDiagnostics).parameters
    with pytest.raises(TypeError):
        ModelRequestDiagnostics(max_records=200)
