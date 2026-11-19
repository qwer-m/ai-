"""当前直接生成协议；只用真实Run63候选作字段投影，不冒称新模型已交付。"""

from copy import deepcopy
import gzip
import json
from pathlib import Path

import pytest
from jsonschema import ValidationError, validate

from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.registry import tool_registry
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS
from modules.agent_platform.test_generation_schemas import CASE_CONTENT_SCHEMA, GENERATION_INPUT_SCHEMA
from test_single_agent_direct_protocol_real_data import model_input


@pytest.fixture(scope="module")
def real_protocol():
    path = Path(__file__).with_name("fixtures") / "run63_direct_protocol_cpu_real.json.gz"
    value = json.loads(gzip.decompress(path.read_bytes()))
    assert action_state_hash({key: item for key, item in value.items() if key != "data_sha256"}) == value["data_sha256"]
    assert value["provenance"]["historical_status"] == "failed"
    assert value["provenance"]["new_model_review_performed"] is False
    return value


def test_current_case_contract_accepts_all_seventy_real_candidate_projections(real_protocol):
    assert len(real_protocol["current_cases_before"]) == 70
    for record in real_protocol["current_cases_before"]:
        validate(model_input(record["content"]), CASE_CONTENT_SCHEMA)


def test_current_case_contract_rejects_model_supplied_quote(real_protocol):
    content = model_input(real_protocol["current_cases_before"][0]["content"])
    content["source_refs"][0] = deepcopy(real_protocol["current_cases_before"][0]["content"]["source_refs"][0])
    with pytest.raises(ValidationError, match="not of type 'string'"):
        validate(content, CASE_CONTENT_SCHEMA)


def test_current_case_contract_rejects_legacy_detached_bindings(real_protocol):
    content = model_input(real_protocol["current_cases_before"][0]["content"])
    content["case_fact_bindings"] = real_protocol["historical_generation_before"]["case_fact_bindings"][0]
    with pytest.raises(ValidationError, match="Additional properties"):
        validate(content, CASE_CONTENT_SCHEMA)


def test_current_input_contract_accepts_real_source_without_removed_execution_options(real_protocol):
    validate(real_protocol["current_input"], GENERATION_INPUT_SCHEMA)
    legacy = set(real_protocol["original_run_input"]) - set(GENERATION_INPUT_SCHEMA["properties"])
    assert legacy
    for key in legacy:
        payload = {**deepcopy(real_protocol["current_input"]), key: real_protocol["original_run_input"][key]}
        with pytest.raises(ValidationError, match="Additional properties"):
            validate(payload, GENERATION_INPUT_SCHEMA)


def test_registry_exposes_current_two_agents_and_ten_direct_state_actions():
    assert {item["agent_key"] for item in BUILTIN_AGENT_SPECS} == {
        "test_generation_coordinator", "test_generation_reviewer",
    }
    assert len(BUILTIN_TOOL_SPECS) == 10
    for item in BUILTIN_TOOL_SPECS:
        assert tool_registry.is_state_only(item["handler_key"])
        assert tool_registry.resolve(item["handler_key"])
    assert "testing.submit_source_semantics" not in tool_registry.keys()
    assert "testing.submit_generation_batch" not in tool_registry.keys()


@pytest.fixture(scope="module")
def run64_recorded_coverage_attempts():
    """读取实际模型调用与失败回执，不把新合同CPU投影写回历史Run。"""
    path = Path(__file__).with_name("fixtures") / "run64_coverage_attempts_real.json"
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    assert snapshot["run"]["id"] == 64
    receipts = sorted((item for item in snapshot["checkpoint"]["receipts"].values()
                       if item["tool_key"] == "commit_coverage_plan"), key=lambda item: item["started_sequence"])
    assert [item["status"] for item in receipts] == ["failed", "failed", "completed"]
    return [json.loads(item["arguments"]["arguments_json"]) for item in receipts]


def test_real_run64_invalid_number_is_rejected_by_public_coverage_schema(run64_recorded_coverage_attempts):
    schema = next(item["input_schema"] for item in BUILTIN_TOOL_SPECS if item["tool_key"] == "commit_coverage_plan")
    # 新字段是CPU协议转换，原始错误编号及业务目标保留，不伪称再次调用模型。
    actual = {**model_input(run64_recorded_coverage_attempts[0]), "remove_coverage_ids": []}
    assert actual["items"][0]["coverage_id"] == "COV-001"
    with pytest.raises(ValidationError, match="does not match"):
        validate(actual, schema)


def test_real_valid_plan_can_omit_deletions_but_explicit_null_is_rejected(run64_recorded_coverage_attempts):
    schema = next(item["input_schema"] for item in BUILTIN_TOOL_SPECS if item["tool_key"] == "commit_coverage_plan")
    actual = model_input(run64_recorded_coverage_attempts[-1])
    validate(actual, schema)
    with pytest.raises(ValidationError):
        validate({**actual, "remove_coverage_ids": None}, schema)
    validate({**actual, "remove_coverage_ids": []}, schema)
    assert "remove_coverage_ids" not in run64_recorded_coverage_attempts[-1]
