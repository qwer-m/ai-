"""Run69真实同值重复字段的工具边界回放，不调用模型或修改线上记录。"""

import asyncio
from copy import deepcopy
from datetime import datetime
import gzip
import json
from pathlib import Path
import re
from types import SimpleNamespace

from agents.tool_context import ToolContext
import pytest
from sqlalchemy import DateTime, create_engine
from sqlalchemy.orm import Session

from core.db.model_defs import AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.sdk_adapter import (
    _function_tool, _parse_function_arguments, _tool_arguments_json,
    ToolArgumentsJSONError, ToolArgumentsValidationError,
)
from modules.agent_platform.serialization import DuplicateJSONKeyError, strict_json_loads
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_TOOL_SPECS
from test_single_agent_direct_protocol_real_data import model_input, scope_state_from_real


@pytest.fixture(scope="module")
def actual():
    path = Path(__file__).parent / "fixtures/run69_identical_duplicate_input_real.json.gz"
    original = path.read_bytes()
    data = json.loads(gzip.decompress(original))
    assert action_state_hash({k: v for k, v in data.items() if k != "data_sha256"}) == data["data_sha256"]
    assert action_state_hash(data["state"]) == data["state_hash"]
    assert action_state_hash({"arguments_json": data["raw_arguments"]}) == data["arguments_hash"]
    assert data["provenance"]["run_id"] == 69
    assert data["provenance"]["historical_error_type"] == "ToolArgumentsJSONError"
    yield data
    assert path.read_bytes() == original


@pytest.fixture
def context(actual):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AgentRun.__table__.create(engine)
    values = deepcopy(actual["run_metadata"])
    for column in AgentRun.__table__.columns:
        if isinstance(column.type, DateTime) and isinstance(values.get(column.name), str):
            values[column.name] = datetime.fromisoformat(values[column.name])
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        db.add(AgentRun(**values, input_payload=deepcopy(actual["run_input"]),
                        run_context=deepcopy(actual["run_context"]), output_payload={}))
        db.commit()
        result = ToolExecutionContext(db=db, user_id=values["user_id"], project_id=values["project_id"],
            run_id=values["id"], node_key="agent_network", run_input=deepcopy(actual["run_input"]),
            action_input=deepcopy(actual["run_input"]), action_state=scope_state_from_real(actual["state"]),
            artifacts={"requirement_source": deepcopy(actual["state"]["source_ref"])})
        yield result
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    engine.dispose()


def append_spec():
    return next(item for item in BUILTIN_TOOL_SPECS if item["tool_key"] == "append_cases")


def duplicate_value_spans(raw):
    spans = list(re.finditer(r'"test_input"\s*:\s*("(?:[^"\\]|\\.)*")', raw))
    assert len(spans) == 11
    assert json.loads(spans[0][1]) == json.loads(spans[1][1])
    return spans


def test_real_identical_duplicate_batch_saves_ten_cases_with_original_audit(actual, context):
    tool = _function_tool(SimpleNamespace(**append_spec()), context)
    raw = actual["raw_arguments"]
    # 仅投影scope和来源wire；直接替换原始片段，真实重复test_input及正文不能被JSON重编码抹除。
    submitted, _ = _tool_arguments_json(raw)
    projected = model_input(submitted)
    start = re.search(r'"coverage_updates"\s*:\s*', raw).end()
    _, size = json.JSONDecoder().raw_decode(raw[start:])
    raw = raw[:start] + json.dumps(projected["coverage_updates"], ensure_ascii=False) + raw[start + size:]
    decoder = json.JSONDecoder()
    for match in reversed(list(re.finditer(r'"source_refs"\s*:\s*', raw))):
        start = match.end()
        references, size = decoder.raw_decode(raw[start:])
        identities = [ref["evidence_id"] if isinstance(ref, dict) else ref for ref in references]
        raw = raw[:start] + json.dumps(identities, ensure_ascii=False) + raw[start + size:]
    submitted, duplicates = _tool_arguments_json(raw)
    assert submitted == projected
    assert [match[1] for match in duplicate_value_spans(raw)] == [
        match[1] for match in duplicate_value_spans(actual["raw_arguments"])]
    assert duplicates[0]["path"] == "/cases/0/test_input"
    invocation = ToolContext(context=context, tool_name="append_cases",
        tool_call_id="cpu-real69-original-first-proposal", tool_arguments=raw)
    result = json.loads(asyncio.run(tool.on_invoke_tool(invocation, raw)))
    assert result["case_count"] == len(submitted["cases"]) == 10
    assert context.action_state["completed"] is False
    assert context.action_state["review"] is None and "test_generation" not in context.artifacts
    call = context.tool_calls[-1]
    audit = call["argument_normalization"]
    assert audit["kind"] == "identical_duplicate_fields"
    assert audit["original_arguments_sha256"] != audit["normalized_arguments_sha256"]
    assert audit["duplicate_fields"] == duplicates
    assert call["arguments"] == submitted
    assert invocation.tool_arguments == raw
    assert duplicate_value_spans(raw)[0][1] == duplicate_value_spans(actual["raw_arguments"])[0][1]
    for identity, proposal in zip(result["created_case_ids"], submitted["cases"]):
        saved = context.action_state["cases"]["items"][identity]["content"]
        assert {k: v for k, v in saved.items() if k != "source_refs"} == {
            k: v for k, v in proposal.items() if k != "source_refs"}


def test_different_actual_values_still_reject_whole_batch_without_state_change(actual, context):
    raw = actual["raw_arguments"]
    spans = duplicate_value_spans(raw)
    replacement = next(match[1] for match in spans[2:] if match[1] != spans[0][1])
    start, end = spans[1].span(1)
    conflicting = raw[:start] + replacement + raw[end:]
    tool = _function_tool(SimpleNamespace(**append_spec()), context)
    before = action_state_hash(context.action_state)
    with pytest.raises(ToolArgumentsJSONError) as error:
        asyncio.run(tool.on_invoke_tool(None, conflicting))
    assert error.value.diagnostic["duplicate_key"] == "test_input"
    assert action_state_hash(context.action_state) == before
    assert context.tool_calls == []


@pytest.mark.parametrize("values", [("true", "1"), ("1", "1.0"), ("false", "0")])
def test_equal_python_coercions_are_not_equal_json_values(actual, values):
    raw = actual["raw_arguments"]
    spans = duplicate_value_spans(raw)
    for match, value in reversed(list(zip(spans[:2], values))):
        start, end = match.span(1)
        raw = raw[:start] + value + raw[end:]
    with pytest.raises(ToolArgumentsJSONError) as error:
        _parse_function_arguments("append_cases", append_spec()["input_schema"], raw)
    assert error.value.diagnostic["error_kind"] == "duplicate_key"


def test_nested_arrays_and_objects_use_full_type_preserving_value_identity(actual):
    proposal, _ = _tool_arguments_json(actual["raw_arguments"])
    # 只重复真实候选数组，验证递归等价；不添加业务记录或构造成功回执。
    cases = json.dumps(proposal["cases"], ensure_ascii=False)
    raw = '{"cases":' + cases + ',"cases":' + cases + '}'
    parsed, duplicates = _tool_arguments_json(raw)
    assert parsed == {"cases": proposal["cases"]}
    assert len(duplicates) == 1 and duplicates[0]["path"] == "/cases"


def test_existing_source_and_final_output_json_boundary_remains_strict(actual):
    with pytest.raises(DuplicateJSONKeyError):
        strict_json_loads(actual["raw_arguments"])
    with pytest.raises(ToolArgumentsJSONError) as error:
        _parse_function_arguments("append_cases", append_spec()["input_schema"], actual["raw_arguments"][:-4])
    assert error.value.diagnostic["error_kind"] == "invalid_json_syntax"


def test_normalization_does_not_bypass_full_tool_schema(actual):
    proposal, _ = _tool_arguments_json(actual["raw_arguments"])
    del proposal["cases"][0]["priority"]
    audit = {}
    with pytest.raises(ToolArgumentsValidationError):
        _parse_function_arguments("append_cases", append_spec()["input_schema"],
                                  json.dumps(proposal, ensure_ascii=False), normalization_audit=audit)
    assert audit == {}
