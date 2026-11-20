"""Run82 首次人工确认的真实协议 CPU 回放；不调用模型、不代答人工问题。"""

import asyncio
from copy import deepcopy
from datetime import timedelta
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

from agents import Agent, OpenAIChatCompletionsModel, RunConfig
from agents.run_config import CallModelData, ModelInputData
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionChunk
from openai.types.completion_usage import CompletionUsage
from openai.types.responses import Response
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.db import db_utc_now
from core.db.model_defs import (AgentRun, AgentNodeRun, AgentRunEvent, AgentArtifactBlob,
    AgentJournalBranch, AgentActionReceiptRecord, AgentValidatedCallRecord)
from modules.agent_platform.action_journal import AgentActionJournal, action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks
from modules.agent_platform.autonomous_tools import AutonomousToolLoop
from modules.agent_platform.conflict_confirmation import ConflictConfirmationRequired
from modules.agent_platform.lifecycle import new_claim_token
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.request_diagnostics import ModelRequestDiagnostics, ObservedRunHooks
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.sdk_adapter import (_PersistentStateInputFilter, _function_tool,
    _ProviderUsageModel, _model_settings, _resolve_stream_provider_usage, _run_sdk_agent_async)
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_TOOL_SPECS
from test_sdk_stream_usage_real_data import RecordedChunks


@pytest.fixture(scope="module")
def actual():
    path = Path(__file__).parent / "fixtures/run82_confirmation_pause_real.json.gz"
    original = path.read_bytes()
    fixture = json.loads(gzip.decompress(original))
    data = fixture["data"]
    assert fixture["provenance"]["new_model_responses"] is False
    assert action_state_hash(data) == fixture["provenance"]["data_sha256"]
    assert action_state_hash(data["before_state"]) == data["first_receipt"]["pre_state_hash"]
    assert action_state_hash(data["after_state"]) == data["first_receipt"]["post_state_hash"]
    assert data["budget_after"]["charged"]["total_tokens"] == 235360
    assert data["extra_request"]["usage"]["total_tokens"] == 21170
    assert data["second_confirmation"]["arguments"] == {"issues": []}
    yield data
    assert path.read_bytes() == original


@pytest.fixture
def resource(actual, monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for model in (AgentRun, AgentNodeRun, AgentRunEvent, AgentArtifactBlob,
                  AgentJournalBranch, AgentActionReceiptRecord, AgentValidatedCallRecord):
        model.__table__.create(engine)
    # 租约检查也连接同一个隔离库，禁止回放请求读取或修改线上 Run82 的当前租约。
    monkeypatch.setattr("core.db.database.SessionLocal", lambda: Session(engine, autoflush=False, expire_on_commit=False))
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        claim = new_claim_token()
        run = AgentRun(**deepcopy(actual["run"]), status="running", claim_token=claim,
            lease_expires_at=db_utc_now() + timedelta(hours=1))
        node = AgentNodeRun(id=447, run_id=82, node_key="agent_network", node_type="agent_network",
            status="running", attempt=1, input_payload=deepcopy(run.input_payload), output_payload={}, sdk_state={})
        db.add_all([run, node])
        db.commit()
        journal = AgentActionJournal(AgentRunRepository(db), run_id=82, node_run_id=447, expected_claim_token=claim)
        journal.init(state=deepcopy(actual["before_state"]), input_hash=action_state_hash(run.input_payload),
                     budget=deepcopy(actual["budget_before"]))
        context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
            run_id=82, node_key=node.node_key, run_input=deepcopy(run.input_payload),
            action_input=deepcopy(run.input_payload), action_state=deepcopy(actual["before_state"]),
            action_journal=journal, claim_token=claim, artifacts=deepcopy(run.run_context["artifacts"]))
        yield SimpleNamespace(context=context, journal=journal, node=node)
        db.rollback()
    engine.dispose()


def test_real_confirmation_stream_pauses_original_node_before_model_eleven(resource, actual):
    """按真实第10次调用及用量重建分片，原生 SDK 执行实际领域工具并保存 Journal。"""
    async def replay():
        context, journal = resource.context, resource.journal
        call, request = actual["first_confirmation"], actual["first_request"]
        model_name, usage = request["model"]["name"], request["usage"]
        args = json.dumps(call["arguments"], ensure_ascii=False, separators=(",", ":"))
        chunks = [ChatCompletionChunk(id=call["tool_call_id"], created=0, model=model_name,
            object="chat.completion.chunk", choices=[{"index": 0, "finish_reason": "tool_calls",
            "delta": {"tool_calls": [{"index": 0, "id": call["tool_call_id"], "type": "function",
                "function": {"name": call["tool_key"], "arguments": args}}]}}]),
            ChatCompletionChunk(id=call["tool_call_id"], created=0, model=model_name,
                object="chat.completion.chunk", choices=[], usage=CompletionUsage(
                    prompt_tokens=usage["input_tokens"], completion_tokens=usage["output_tokens"], total_tokens=usage["total_tokens"]))]
        fetches = []

        class RecordedChatFetch(OpenAIChatCompletionsModel):
            async def _fetch_response(self, *args, **kwargs):
                assert kwargs["stream"] is True
                fetches.append(request["sequence"])
                assert fetches == [10], "首次真实确认后不能准入第11次模型请求"
                # SDK 转换信封；原始记录没有 SSE 切片，内容及用量均取实际已保存记录。
                return Response.model_construct(id="__fake_id__", created_at=0, model=model_name,
                    object="response", output=[], tool_choice="auto", tools=[], parallel_tool_calls=False), RecordedChunks(chunks)

        class RecordedUsageChat(_ProviderUsageModel, RecordedChatFetch):
            pass

        budget = AutonomousBudgetHooks(actual["budget_before"]["limits"], checkpoint=deepcopy(actual["budget_before"]))
        loop = AutonomousToolLoop(terminal_tool_names={"finish_generation"}, validate_submission=lambda output: output,
            recoverable_errors=(OutputValidationError,), max_tool_calls=32, action_context=context, budget_snapshot=budget.snapshot)
        spec = next(item for item in BUILTIN_TOOL_SPECS if item["tool_key"] == call["tool_key"])
        tool = loop.wrap_tool(_function_tool(SimpleNamespace(**spec), context), allow_feedback=True)
        diagnostics = ModelRequestDiagnostics()
        async with AsyncOpenAI(api_key="offline-recorded-replay") as client:
            agent = Agent(name=request["agent_name"], model=RecordedUsageChat(model=model_name, openai_client=client),
                tools=[tool], tool_use_behavior=loop.tool_use_behavior,
                model_settings=_model_settings({"stream_response": True, "max_output_tokens": 10000}))
            with pytest.raises(ConflictConfirmationRequired) as paused:
                await _run_sdk_agent_async(agent=agent, runner_input=[], execution_context=context,
                    max_turns=3, run_config=RunConfig(tracing_disabled=True), request_timeout_seconds=10,
                    hooks=ObservedRunHooks(diagnostics, budget, stream_usage_resolver=_resolve_stream_provider_usage),
                    submission_loop=loop, stream_response=True)
        assert fetches == [10] and len(diagnostics.requests) == 1
        assert {item["id"] for item in paused.value.items} == set(actual["after_state"]["confirmation_requested"])
        assert action_state_hash(context.action_state) == actual["first_receipt"]["post_state_hash"]
        receipt = journal.receipts(include_payloads=True)[-1]
        assert receipt["status"] == "completed" and receipt["tool_call_id"] == call["tool_call_id"]
        assert receipt["post_state_hash"] == actual["first_receipt"]["post_state_hash"]
        assert budget.snapshot()["charged"] == actual["budget_after"]["charged"]
        assert journal.metadata()["budget"]["charged"] == actual["budget_after"]["charged"]
        assert resource.node.id == 447 and resource.node.attempt == 1
        assert not loop.has_submission
    asyncio.run(replay())


def test_real_pending_state_blocks_input_filter_before_budget_observer(resource, actual):
    context = resource.context
    context.action_state = deepcopy(actual["after_state"])
    state_filter = _PersistentStateInputFilter(context=context, runtime_config=actual["runtime_config"])
    state_filter.bind_initial_input("原始会话输入", state_filter.observe())
    observed = []
    state_filter.bind_budget_observer(lambda: observed.append("预算观察不能先于暂停"))
    before = deepcopy(resource.journal.metadata())
    with pytest.raises(ConflictConfirmationRequired):
        state_filter(CallModelData(context=context, agent=Agent(name="真实暂停状态过滤检查"),
            model_data=ModelInputData(input=[{"role": "user", "content": "原始会话输入"}], instructions=None)))
    assert not observed and resource.journal.metadata() == before


def test_existing_pending_state_pauses_before_any_runner_or_model_admission(resource, actual):
    resource.context.action_state = deepcopy(actual["after_state"])
    before = deepcopy(resource.journal.metadata())
    with pytest.raises(ConflictConfirmationRequired):
        asyncio.run(_run_sdk_agent_async(agent=Agent(name="恢复已有暂停状态"), runner_input=[],
            execution_context=resource.context, max_turns=3, run_config=RunConfig(tracing_disabled=True),
            request_timeout_seconds=10, stream_response=True))
    assert resource.journal.metadata() == before
