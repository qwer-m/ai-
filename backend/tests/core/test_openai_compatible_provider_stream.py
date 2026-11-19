"""兼容协议的离线构造/解析契约；不伪造 HTTP 客户端或模型业务产物。"""
from __future__ import annotations
from copy import deepcopy
import json
from core.ai.providers.base import STREAM_HEARTBEAT_CHUNK
from core.ai.providers.openai_compatible_provider import OpenAICompatibleProvider


def provider(*, responses=False, model="protocol-model"):
    return OpenAICompatibleProvider("https://example.test/v1/responses" if responses else "https://example.test/v1", "", model)


def sse(payload):
    return "data: " + json.dumps(payload, ensure_ascii=False)


def parse(instance, timed_lines, *, final_time=1.2):
    metadata=instance.last_response_metadata
    return list(instance._parse_stream_lines(timed_lines, request_started_at=0, stream_started_at=0,
        stream_attempt_timeout=metadata["stream_attempt_timeout_seconds"],
        heartbeat_interval=metadata["stream_heartbeat_interval_seconds"], clock=lambda: final_time))


def test_chat_stream_skips_reasoning_content():
    instance=provider()
    instance._prepare_request([], instance.model, stream=True)
    chunks=parse(instance, [(0.5,sse({"choices":[{"delta":{"reasoning_content":"private protocol text"}}]})),
        (1,sse({"choices":[{"delta":{"content":"visible text"}}]})),(1.1,"data: [DONE]")])
    assert chunks==["visible text"]


def test_chat_stream_refreshes_response_metadata_model_and_output_limit():
    instance=provider(model="current-model")
    instance.last_response_metadata={"model":"previous-model","max_tokens":1600}
    _,payload,_=instance._prepare_request([],"current-model",10000,stream=True)
    chunks=parse(instance,[(1,sse({"choices":[{"delta":{"content":"hello"}}]})),(1.1,"data: [DONE]")])
    assert chunks==["hello"]
    assert payload["max_tokens"]==instance.last_response_metadata["max_tokens"]==10000
    assert instance.last_response_metadata["model"]=="current-model"
    assert instance.last_response_metadata["stream"] is True
    assert instance.last_response_metadata["content_len"]==5


def test_chat_stream_stops_when_attempt_exceeds_hard_timeout(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPAT_STREAM_ATTEMPT_TIMEOUT_SECONDS","1")
    instance=provider();instance._prepare_request([],instance.model,stream=True)
    chunks=parse(instance,[(2,sse({"choices":[{"delta":{"content":"late text"}}]}))],final_time=2)
    assert chunks==["Exception occurred: stream_attempt_timeout_after_1s"]
    assert instance.last_response_metadata["exception_type"]=="StreamAttemptTimeout"
    assert instance.last_response_metadata["content_len"]==0


def test_chat_stream_heartbeat_is_out_of_band_and_timeout_override_preserved():
    instance=provider()
    _,_,timeout=instance._prepare_request([],instance.model,stream=True,
        request_timeout_seconds=180,heartbeat_interval_seconds=15)
    chunks=parse(instance,[(16,sse({"choices":[{"delta":{"reasoning_content":"private"}}]})),
        (16.1,sse({"choices":[{"delta":{"content":"hello"}}]})),(16.2,"data: [DONE]")],final_time=16.3)
    assert chunks==[STREAM_HEARTBEAT_CHUNK,"hello"]
    assert instance.last_response_metadata["content_len"]==5
    assert timeout.read==180
    assert instance.last_response_metadata["request_timeout_source"]=="call_override"
    assert instance.last_response_metadata["stream_attempt_timeout_seconds"]==180
    assert instance.last_response_metadata["stream_heartbeat_interval_seconds"]==15


def test_chat_stream_thinking_controls_and_hidden_reasoning_timings():
    instance=provider()
    _,payload,_=instance._prepare_request([],instance.model,stream=True,reasoning_effort="low",disable_thinking=True)
    hidden="private protocol text"
    chunks=parse(instance,[(0.5,sse({"choices":[{"delta":{"reasoning_content":hidden}}]})),
        (1,sse({"choices":[{"delta":{"reasoning_content":"x","content":"hello"}}]})),(1.1,"data: [DONE]")])
    assert payload["reasoning_effort"]=="low" and "thinking" not in payload
    assert chunks==["hello"] and hidden not in "".join(chunks)
    assert instance.last_response_metadata["reasoning_chars"]==len(hidden)+1
    assert instance.last_response_metadata["first_reasoning_ms"]==500
    assert instance.last_response_metadata["first_content_ms"]==1000
    assert instance.last_response_metadata["total_duration_ms"]==1200


def test_configured_reasoning_wins_only_for_its_model():
    instance=provider();instance.reasoning_effort="high"
    _,configured,_=instance._prepare_request([],instance.model,stream=True,reasoning_effort="low",disable_thinking=True)
    assert configured["reasoning_effort"]=="high" and "thinking" not in configured
    _,explicit,_=instance._prepare_request([],"another-model",stream=True,reasoning_effort="low")
    assert explicit["reasoning_effort"]=="low"


def test_responses_stream_does_not_duplicate_done_or_completed_text():
    instance=provider(responses=True);instance._prepare_request([],instance.model,stream=True)
    chunks=parse(instance,[(0.5,sse({"type":"response.output_text.delta","delta":"hello"})),
        (0.6,sse({"type":"response.output_text.done","text":"hello"})),
        (1,sse({"type":"response.completed","response":{"output_text":"hello"}})),(1.1,"data: [DONE]")])
    assert chunks==["hello"]


def test_json_chat_request_does_not_override_model_reasoning():
    instance=provider()
    _,payload,_=instance._prepare_request([{"role":"user","content":"只输出 JSON"}],instance.model,100)
    assert payload["temperature"]==0
    assert payload["response_format"]=={"type":"json_object"}
    assert "reasoning_effort" not in payload and "thinking" not in payload


def test_json_400_is_terminal_without_mutating_or_rebuilding_request():
    instance=provider()
    _,payload,_=instance._prepare_request([{"role":"user","content":"只输出 JSON"}],instance.model,100)
    original=deepcopy(payload)
    assert instance._parse_response(status_code=400,error_text="unsupported response_format")=="Error: HTTP 400 - unsupported response_format"
    assert payload==original and payload["response_format"]=={"type":"json_object"}
    assert instance.last_response_metadata["http_status"]==400
    assert not instance.last_response_metadata.get("json_compat_fallback")


def test_json_chat_request_can_disable_response_format_explicitly():
    instance=provider();instance.disable_json_response_format=True
    _,payload,_=instance._prepare_request([{"role":"user","content":"Return JSON only"}],instance.model,100)
    assert "response_format" not in payload
    assert "reasoning_effort" not in payload and "thinking" not in payload


def test_explicit_text_mode_does_not_enable_json_for_incidental_json_word():
    instance=provider()
    _,payload,_=instance._prepare_request([{"role":"user","content":"生成 Python，并打印 JSON 格式日志"}],
        instance.model,100,response_mode="text")
    assert "response_format" not in payload
    assert instance.last_response_metadata["response_mode"]=="text"
    assert instance.last_response_metadata["json_response"] is False


def test_explicit_json_mode_enables_structured_response_without_json_word():
    instance=provider()
    _,payload,_=instance._prepare_request([{"role":"user","content":"返回结构化协议值"}],instance.model,100,response_mode="json")
    assert payload["response_format"]=={"type":"json_object"}
    assert instance.last_response_metadata["response_mode"]=="json"
    assert instance.last_response_metadata["json_response"] is True


def test_non_stream_request_uses_call_level_timeout_override():
    instance=provider()
    _,_,timeout=instance._prepare_request([],instance.model,100,response_mode="text",request_timeout_seconds=180)
    assert (timeout.read,timeout.connect,timeout.write,timeout.pool)==(180,15,30,15)
    assert instance.last_response_metadata["request_timeout_source"]=="call_override"


def test_responses_incomplete_returns_partial_without_additional_request():
    instance=provider(responses=True);instance._prepare_request([],instance.model,100)
    partial='{"value":'
    result=instance._parse_response(status_code=200,data={"status":"incomplete", "incomplete_details":{"reason":"max_output_tokens"},
        "output":[{"type":"message","content":[{"type":"output_text","text":partial}]}]})
    assert result==partial
    assert instance.last_response_metadata["response_status"]=="incomplete"
    assert instance.last_response_metadata["finish_reason"]=="length"
    assert instance.last_response_metadata["content_len"]==len(partial)
    assert "stream_fallback_content_len" not in instance.last_response_metadata


def test_responses_incomplete_without_text_keeps_length_metadata():
    instance=provider(responses=True);instance._prepare_request([],instance.model,100)
    result=instance._parse_response(status_code=200,data={"status":"incomplete","incomplete_details":{"reason":"max_output_tokens"},"output":[]})
    assert result=="" and instance.last_response_metadata["finish_reason"]=="length"
    assert instance.last_response_metadata["content_len"]==0


def test_stream_usage_and_request_identity_are_preserved():
    instance=provider();instance._prepare_request([],instance.model,stream=True)
    usage={"prompt_tokens":12,"completion_tokens":3,"prompt_cache_hit_tokens":8,"prompt_cache_miss_tokens":4}
    assert parse(instance,[(1,sse({"choices":[],"usage":usage})),(1.1,"data: [DONE]")])==[]
    assert instance.last_response_metadata["usage"]==usage
    assert instance._parse_response(status_code=200,data={"usage":usage,"choices":[]},request_id="protocol-request-id")==""
    assert instance.last_response_metadata["provider_request_id"]=="protocol-request-id"
