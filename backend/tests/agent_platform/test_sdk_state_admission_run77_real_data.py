"""Run77真实超批次提案与待人工状态的SDK准入回归，不请求或模拟模型。"""

import asyncio
from copy import deepcopy
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from agents import FunctionTool, ModelSettings
from jsonschema import ValidationError, validate
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.db.model_defs import AgentRun
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.conflict_confirmation import ConflictConfirmationRequired, pending_conflicts
from modules.agent_platform.context_agent_tools import BUILTIN_TOOL_SPECS as CONTEXT_TOOL_SPECS
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.sdk_adapter import _ActionCatalogModel, _PersistentStateInputFilter, _function_tool
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS


@pytest.fixture(scope="module")
def real():
    path = Path(__file__).parent / "fixtures/run77_sdk_state_admission_real.json.gz"
    original = path.read_bytes()
    fixture = json.loads(gzip.decompress(original))
    encoded = json.dumps(fixture["data"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == fixture["provenance"]["data_sha256"]
    assert fixture["provenance"]["run_id"] == 77 and fixture["provenance"]["status"] == "failed"
    yield fixture["data"]
    assert path.read_bytes() == original


@pytest.fixture
def contexts(real):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AgentRun.__table__.create(engine)
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        columns = {column.name for column in AgentRun.__table__.columns}
        values = {key: deepcopy(value) for key, value in real["run"].items() if key in columns}
        for key, value in list(values.items()):
            if value is not None and key.endswith("_at"):
                values[key] = datetime.fromisoformat(value)
        db.add(AgentRun(**values))
        db.commit()
        config = deepcopy(next(spec for spec in BUILTIN_AGENT_SPECS
            if spec["agent_key"] == "test_generation_coordinator")["runtime_config"])

        def make(key):
            state = deepcopy(real[key])
            # 仅重算本次CPU观察派生摘要；不改真实来源、正文、裁决及人工请求集合。
            generation.refresh_generation_state(state)
            context = ToolExecutionContext(db=db, user_id=values["user_id"], project_id=values["project_id"],
                run_id=values["id"], node_key="agent_network", run_input=deepcopy(values["input_payload"]),
                action_input=deepcopy(values["input_payload"]), action_state=state,
                artifacts=deepcopy(values["run_context"]["artifacts"]))
            return context, _PersistentStateInputFilter(context=context, runtime_config=deepcopy(config))

        yield make
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    engine.dispose()


def request(state_filter, tools):
    # 只调用同步协议投影；底层模型未创建、也不会被调用。
    return _ActionCatalogModel(None, state_filter)._request({"tools": tools, "model_settings": ModelSettings()})


def test_actual_eighteen_case_proposal_is_excluded_by_current_fifteen_capacity_schema(real, contexts):
    context, state_filter = contexts("first_state")
    original = next(spec for spec in BUILTIN_TOOL_SPECS if spec["tool_key"] == "append_cases")
    tool = _function_tool(SimpleNamespace(**original), context)
    before = deepcopy(tool.params_json_schema)
    # request仍核对整份合法目录，不能为了测试只注册一个合法工具。
    tools = [_function_tool(SimpleNamespace(**spec), context) for spec in (*BUILTIN_TOOL_SPECS, *CONTEXT_TOOL_SPECS)]
    projected = {tool.name: tool for tool in request(state_filter, tools)["tools"]}["append_cases"]
    arguments = json.loads(real["oversized_receipt"]["arguments"]["arguments_json"])
    assert len(arguments["cases"]) == 18
    assert state_filter.observe()["available_actions"]["append_cases"]["max_items"] == 15
    assert projected.params_json_schema["properties"]["cases"]["maxItems"] == 15
    with pytest.raises(ValidationError) as error:
        validate(arguments, projected.params_json_schema)
    assert error.value.validator == "maxItems"
    assert before == tool.params_json_schema
    assert before["properties"]["cases"]["maxItems"] == 20
    assert "x-state-max-items" in before["properties"]["cases"]
    assert "x-state-max-items" not in json.dumps(projected.params_json_schema)
    assert projected.strict_json_schema is False
    assert not context.executed_tools


def test_projection_uses_annotation_without_tool_names_or_mutating_registered_validator(real, contexts):
    context, state_filter = contexts("first_state")
    registered = [_function_tool(SimpleNamespace(**spec), context) for spec in (*BUILTIN_TOOL_SPECS, *CONTEXT_TOOL_SPECS)]
    before = {tool.name: deepcopy(tool.params_json_schema) for tool in registered}
    sent = request(state_filter, registered)["tools"]
    for tool in sent:
        assert "x-state-max-items" not in json.dumps(tool.params_json_schema)
    assert {tool.name: tool.params_json_schema for tool in registered} == before
    schema = before["append_cases"]
    narrowed = _ActionCatalogModel._project_tool_schema(schema, {"max_items": 30})
    assert narrowed["properties"]["cases"]["maxItems"] == 20
    with pytest.raises(ValueError, match="缺少有效数组容量"):
        _ActionCatalogModel._project_tool_schema(schema, {})


def test_actual_pending_human_state_pauses_before_any_next_model_or_terminal_tool(real, contexts):
    context, state_filter = contexts("pending_human_state")
    before = deepcopy(context.action_state)
    assert context.action_state["review"]["result"]["verdict"] == "needs_human"
    assert len(pending_conflicts(context.action_state)) == 2
    with pytest.raises(ConflictConfirmationRequired) as paused:
        asyncio.run(state_filter.complete_if_ready([], None))
    assert paused.value.items == pending_conflicts(context.action_state)
    assert {item["id"] for item in paused.value.items} == set(real["pending_human_state"]["confirmation_requested"])
    assert context.action_state == before and not context.executed_tools


def test_actual_independent_review_replay_pauses_immediately_after_automatic_action(real, contexts):
    context, state_filter = contexts("generated_state")
    assert not context.action_state["confirmation_requested"]
    assert state_filter.observe()["automatic_action"] == "request_review"
    raw = deepcopy(real["pending_human_state"]["review"]["raw_result"])
    # 只移除已由代码编译的旧wire索引；历史fixture及真实业务判断保持原值。
    for question in raw["business_questions"]:
        question.pop("coverage_ids")

    async def apply_saved_review(tool_context, arguments):
        # 重放真实模型已经提交的回执，经现有编译器应用；没有构造模型或伪造模型响应。
        return json.dumps(generation._apply_review(context, generation._review_input(context.action_state), raw),
                          ensure_ascii=False)

    replay = FunctionTool(name="request_review", description="真实Run77审查回执CPU重放",
        params_json_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        on_invoke_tool=apply_saved_review, strict_json_schema=False)
    with pytest.raises(ConflictConfirmationRequired) as paused:
        asyncio.run(state_filter.complete_if_ready([replay], None))
    assert len(paused.value.items) == 2
    assert context.action_state["review"]["result"]["verdict"] == "needs_human"
    assert set(context.action_state["confirmation_requested"]) == set(real["pending_human_state"]["confirmation_requested"])
    assert not context.executed_tools
