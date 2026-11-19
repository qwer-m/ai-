"""以 Run72 已持久化的真实输出和用量做 CPU 协议回放，不调用模型或宣称新生成成功。"""

import asyncio
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

from agents import Agent, FunctionTool, OpenAIChatCompletionsModel, RunConfig, RunHooks
from agents.items import ModelResponse
from agents.usage import Usage
from openai.types.completion_usage import CompletionUsage
from openai.types.chat import ChatCompletionChunk
from openai.types.responses import ResponseFunctionToolCall
from openai.types.responses import Response
from openai import AsyncOpenAI
import pytest

from modules.agent_platform.request_diagnostics import ModelRequestDiagnostics, ObservedRunHooks
from modules.agent_platform.sdk_adapter import (
    _ProviderUsageModel, _StreamUsageSession, _UsageObservedStream, _model_settings,
    _resolve_stream_provider_usage, _run_sdk_agent_async, _stream_usage_session,
)
from modules.agent_platform.registry import ToolExecutionContext


@pytest.fixture(scope="module")
def real():
    path = Path(__file__).parent / "fixtures/run72_stream_usage_real.json.gz"
    original = path.read_bytes()
    fixture = json.loads(gzip.decompress(original))
    data = fixture["data"]
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == fixture["provenance"]["data_sha256"]
    assert fixture["provenance"]["source_run_id"] == 72
    assert data["request"]["status"] == "response_received"
    yield data
    assert path.read_bytes() == original


def actual_output(real):
    """将真实结构化回执恢复成 SDK 输出对象，原记录没有原始 SSE 分片。"""
    call = real["call"]
    return [ResponseFunctionToolCall(
        type="function_call", call_id=call["tool_call_id"], name=call["tool_key"],
        arguments=json.dumps(call["arguments"], ensure_ascii=False, separators=(",", ":")),
        status="completed",
    )]


def actual_usage(real):
    usage = real["request"]["usage"]
    return CompletionUsage(prompt_tokens=usage["input_tokens"], completion_tokens=usage["output_tokens"],
                           total_tokens=usage["total_tokens"])


def actual_response(real, *, missing_usage=False):
    return ModelResponse(output=actual_output(real), response_id=None,
                         usage=Usage() if missing_usage else Usage(**real["request"]["usage"]))


class RecordedChunks:
    """只回放传入的真实记录投影；关闭计数检验 SDK 生命周期接口转发。"""

    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self.closed = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration

    async def close(self):
        self.closed += 1


def test_real_usage_chunk_is_retained_before_sdk_conversion_and_close_is_forwarded(real):
    async def replay():
        session = _StreamUsageSession()
        receipt = session.begin(real["request"]["model"]["name"])
        chunk = ChatCompletionChunk(id=real["call"]["tool_call_id"], choices=[], created=0,
                                    model=receipt.model, object="chat.completion.chunk", usage=actual_usage(real))
        source = RecordedChunks([chunk])
        stream = _UsageObservedStream(source, receipt)
        assert [value async for value in stream] == [chunk]
        assert receipt.raw_usage == actual_usage(real).model_dump(mode="json")
        await stream.close()
        assert source.closed == 1
        session.complete(receipt, actual_output(real))
        session.active = None
        result = session.consume(receipt.model, actual_response(real).output)
        assert result["usage_reported"] is True
        assert result["usage"]["total_tokens"] == real["request"]["usage"]["total_tokens"]
    asyncio.run(replay())


def test_shared_session_survives_child_task_context_and_matches_full_output(real):
    async def replay():
        session = _StreamUsageSession()
        token = _stream_usage_session.set(session)
        model = real["request"]["model"]["name"]
        try:
            async def sdk_child_task():
                inherited = _stream_usage_session.get()
                assert inherited is session
                receipt = inherited.begin(model)
                receipt.raw_usage = actual_usage(real).model_dump(mode="json")
                inherited.complete(receipt, actual_output(real))
                inherited.active = None
            await asyncio.create_task(sdk_child_task())
            agent = Agent(name=real["request"]["agent_name"], model=model)
            result = _resolve_stream_provider_usage(agent, actual_response(real))
            assert result["usage_reported"] is True
            with pytest.raises(RuntimeError, match="不一致"):
                _resolve_stream_provider_usage(agent, actual_response(real))
        finally:
            _stream_usage_session.reset(token)
    asyncio.run(replay())


def test_concurrency_and_mismatched_output_cannot_overwrite_or_consume_real_usage(real):
    session = _StreamUsageSession()
    model = real["request"]["model"]["name"]
    receipt = session.begin(model)
    with pytest.raises(RuntimeError, match="禁止并发"):
        session.begin(model)
    receipt.raw_usage = actual_usage(real).model_dump(mode="json")
    session.complete(receipt, actual_output(real))
    session.active = None
    with pytest.raises(RuntimeError, match="禁止并发"):
        session.begin(model)
    changed = actual_output(real)
    changed[0].call_id += "_不同调用"
    with pytest.raises(RuntimeError, match="不一致"):
        session.consume(model, changed)
    with pytest.raises(RuntimeError, match="不一致"):
        session.consume(model + "_不同模型", actual_output(real))
    assert session.consume(model, actual_output(real))["usage_reported"] is True


@pytest.mark.parametrize("missing_usage", [False, True])
def test_hook_attaches_receipt_before_budget_and_diagnostics_do_not_invent_zero(real, missing_usage):
    async def replay():
        model = real["request"]["model"]["name"]
        session = _StreamUsageSession()
        receipt = session.begin(model)
        receipt.raw_usage = None if missing_usage else actual_usage(real).model_dump(mode="json")
        session.complete(receipt, actual_output(real))
        session.active = None
        token = _stream_usage_session.set(session)
        diagnostics = ModelRequestDiagnostics()
        context = object()
        diagnostics._record_started(context, deepcopy(real["request"]))
        response = actual_response(real, missing_usage=missing_usage)
        observed = []

        class InspectBudgetBoundary:
            async def on_llm_end(self, current_context, agent, result):
                assert current_context is context
                observed.append((result.usage_reported, result.provider_usage))

        try:
            hooks = ObservedRunHooks(diagnostics, InspectBudgetBoundary(),
                                     stream_usage_resolver=_resolve_stream_provider_usage)
            await hooks.on_llm_end(context, Agent(name=real["request"]["agent_name"], model=model), response)
            record = diagnostics.requests[0]
            assert observed == [(not missing_usage, receipt.raw_usage)]
            assert record["usage_reported"] is not missing_usage
            if missing_usage:
                assert record["usage"] is None
                assert all(value is None for value in record["token_usage"].values())
            else:
                assert record["usage"] == real["request"]["usage"]
                assert record["token_usage"]["total_tokens"] == real["request"]["usage"]["total_tokens"]
        finally:
            _stream_usage_session.reset(token)
    asyncio.run(replay())


def test_only_explicit_stream_setting_requests_usage_and_preserves_zero_retries(real):
    settings = _model_settings({"stream_response": True, "max_output_tokens": 10000})
    assert settings.include_usage is True
    assert settings.retry.max_retries == 0
    assert _model_settings({"max_output_tokens": 10000}).include_usage is None


@pytest.mark.parametrize("with_action_catalog", [False, True])
def test_native_sdk_runner_completes_recorded_tool_after_terminal_stream_event(real, with_action_catalog):
    """真实回执转成分片进行离线 SDK 协议回放，测试不接数据库、不发送模型请求。"""
    async def replay():
        call = real["call"]
        model_name = real["request"]["model"]["name"]
        arguments = json.dumps(call["arguments"], ensure_ascii=False, separators=(",", ":"))
        chunks = [ChatCompletionChunk(
            id=call["tool_call_id"], created=0, model=model_name, object="chat.completion.chunk",
            choices=[{"index": 0, "finish_reason": "tool_calls", "delta": {"tool_calls": [{
                "index": 0, "id": call["tool_call_id"], "type": "function", "function": {
                    "name": call["tool_key"], "arguments": arguments,
                },
            }]}}], usage=None,
        ), ChatCompletionChunk(id=call["tool_call_id"], created=0, model=model_name,
                               object="chat.completion.chunk", choices=[], usage=actual_usage(real))]
        source = RecordedChunks(chunks)

        class RecordedChatFetch(OpenAIChatCompletionsModel):
            async def _fetch_response(self, *args, **kwargs):
                assert kwargs["stream"] is True
                # 占位 Response 是 SDK Chat→Responses 转换所需的信封，不代表原始 SSE。
                response = Response.model_construct(id="__fake_id__", created_at=0, model=model_name,
                    object="response", output=[], tool_choice="auto", tools=[], parallel_tool_calls=False)
                return response, source

        class RecordedUsageChat(_ProviderUsageModel, RecordedChatFetch):
            pass

        receipts, invocations = [], []

        class InspectNativeEnd(RunHooks):
            async def on_llm_end(self, context, agent, response):
                receipts.append(_resolve_stream_provider_usage(agent, response))

        async def replay_tool(context, raw_arguments):
            assert json.loads(raw_arguments) == call["arguments"]
            invocations.append(call["tool_call_id"])
            return json.dumps(call["result"], ensure_ascii=False)

        # 客户端仅满足 SDK 构造约定；取流方法已限定为上述持久化 CPU 回放。
        async with AsyncOpenAI(api_key="offline-recorded-replay") as client:
            from modules.agent_platform.sdk_adapter import _ActionCatalogModel
            model = RecordedUsageChat(model=model_name, openai_client=client)
            if with_action_catalog:
                model = _ActionCatalogModel(model, state_filter=None)
            agent = Agent(name=real["request"]["agent_name"],
                model=model,
                tools=[FunctionTool(name=call["tool_key"], description="回放真实已执行工具回执",
                    params_json_schema={"type": "object", "properties": {}, "additionalProperties": True},
                    on_invoke_tool=replay_tool, strict_json_schema=False)],
                tool_use_behavior={"stop_at_tool_names": [call["tool_key"]]},
                model_settings=_model_settings({"stream_response": True, "max_output_tokens": 10000}))
            context = ToolExecutionContext(db=None, user_id=0, project_id=0,
                run_id=real["request"]["run_id"], node_key=real["request"]["node_key"], run_input={})
            result = await _run_sdk_agent_async(agent=agent, runner_input=[], execution_context=context,
                max_turns=1, run_config=RunConfig(tracing_disabled=True), request_timeout_seconds=10,
                hooks=InspectNativeEnd(), stream_response=True)
            assert invocations == [call["tool_call_id"]]
            assert json.loads(result.final_output) == call["result"]
            assert len(result.raw_responses) == 1
            assert receipts[0]["usage_reported"] is True
            assert receipts[0]["usage"]["total_tokens"] == real["request"]["usage"]["total_tokens"]
            assert source.closed == 1
            assert _stream_usage_session.get() is None
    asyncio.run(replay())
