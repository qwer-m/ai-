from __future__ import annotations

import json

from core.ai.ai_client import AIClient
from core.ai.providers.base import STREAM_HEARTBEAT_CHUNK
from core.ai.providers.openai_compatible_provider import OpenAICompatibleProvider


class _FakeStreamResponse:
    status_code = 200

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def iter_lines(self):
        return iter(self._lines)


class _FakeHttpClient:
    def __init__(self, *, lines: list[str]) -> None:
        self._lines = lines
        self.stream_requests: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def stream(self, *args, **kwargs):
        self.stream_requests.append(
            {
                "args": args,
                "kwargs": kwargs,
            }
        )
        return _FakeStreamResponse(self._lines)


class _FakePostResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakePostClient:
    def __init__(self, responses: list[_FakePostResponse]) -> None:
        self.responses = list(responses)
        self.posts: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def post(self, url, headers=None, json=None):
        self.posts.append({"url": url, "headers": headers or {}, "json": json or {}})
        return self.responses.pop(0)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}"


def test_chat_stream_skips_reasoning_content(monkeypatch) -> None:
    lines = [
        _sse({"choices": [{"delta": {"reasoning_content": "internal reasoning"}}]}),
        _sse({"choices": [{"delta": {"content": "[{\"id\":\"TC-001\"}]"}}]}),
        "data: [DONE]",
    ]

    monkeypatch.setattr(
        "core.ai.providers.openai_compatible_provider.httpx.Client",
        lambda **kwargs: _FakeHttpClient(lines=lines),
    )

    provider = OpenAICompatibleProvider("https://example.test/v1", "sk-test", "model")
    chunks = list(provider.generate_stream([{"role": "user", "content": "hi"}], "model"))

    assert chunks == ['[{"id":"TC-001"}]']


def test_chat_stream_refreshes_response_metadata_model(monkeypatch) -> None:
    lines = [
        _sse({"choices": [{"delta": {"content": "hello"}}]}),
        "data: [DONE]",
    ]

    monkeypatch.setattr(
        "core.ai.providers.openai_compatible_provider.httpx.Client",
        lambda **kwargs: _FakeHttpClient(lines=lines),
    )

    provider = OpenAICompatibleProvider("https://example.test/v1", "sk-test", "glm-5.1")
    provider.last_response_metadata = {"model": "kimi-k2.5", "max_tokens": 1600}
    chunks = list(provider.generate_stream([{"role": "user", "content": "hi"}], "glm-5.1", max_tokens=10000))

    assert chunks == ["hello"]
    assert provider.last_response_metadata["model"] == "glm-5.1"
    assert provider.last_response_metadata["max_tokens"] == 10000
    assert provider.last_response_metadata["stream"] is True
    assert provider.last_response_metadata["content_len"] == len("hello")


def test_chat_stream_stops_when_attempt_exceeds_hard_timeout(monkeypatch) -> None:
    lines = [
        _sse({"choices": [{"delta": {"content": "late content"}}]}),
        "data: [DONE]",
    ]

    monkeypatch.setenv("OPENAI_COMPAT_STREAM_ATTEMPT_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(
        "core.ai.providers.openai_compatible_provider.httpx.Client",
        lambda **kwargs: _FakeHttpClient(lines=lines),
    )
    times = iter([0.0, 0.0, 2.0, 2.0])
    monkeypatch.setattr(
        "core.ai.providers.openai_compatible_provider.time.perf_counter",
        lambda: next(times),
    )

    provider = OpenAICompatibleProvider("https://example.test/v1", "sk-test", "glm-5.1")
    chunks = list(provider.generate_stream([{"role": "user", "content": "hi"}], "glm-5.1"))

    assert chunks == ["Exception occurred: stream_attempt_timeout_after_1s"]
    assert provider.last_response_metadata["exception_type"] == "StreamAttemptTimeout"
    assert provider.last_response_metadata["stream_attempt_timeout_seconds"] == 1


def test_chat_stream_emits_out_of_band_heartbeat_without_polluting_content(
    monkeypatch,
) -> None:
    lines = [
        _sse({"choices": [{"delta": {"reasoning_content": "internal reasoning"}}]}),
        _sse({"choices": [{"delta": {"content": "hello"}}]}),
        "data: [DONE]",
    ]

    monkeypatch.setattr(
        "core.ai.providers.openai_compatible_provider.httpx.Client",
        lambda **kwargs: _FakeHttpClient(lines=lines),
    )
    times = iter([0.0, 0.0, 16.0, 16.1, 16.2, 16.3])
    monkeypatch.setattr(
        "core.ai.providers.openai_compatible_provider.time.perf_counter",
        lambda: next(times),
    )

    provider = OpenAICompatibleProvider(
        "https://example.test/v1",
        "sk-test",
        "glm-5.1",
    )
    chunks = list(
        provider.generate_stream(
            [{"role": "user", "content": "hi"}],
            "glm-5.1",
            request_timeout_seconds=180,
            heartbeat_interval_seconds=15,
        )
    )

    assert chunks == [STREAM_HEARTBEAT_CHUNK, "hello"]
    assert provider.last_response_metadata["content_len"] == len("hello")
    assert provider.last_response_metadata["request_timeout_seconds"] == 180.0
    assert provider.last_response_metadata["request_timeout_source"] == "call_override"
    assert provider.last_response_metadata["stream_attempt_timeout_seconds"] == 180.0
    assert provider.last_response_metadata["stream_heartbeat_interval_seconds"] == 15.0


def test_chat_stream_sends_thinking_controls_and_observes_hidden_reasoning(
    monkeypatch,
) -> None:
    hidden_reasoning = "internal reasoning"
    lines = [
        _sse({"choices": [{"delta": {"reasoning_content": hidden_reasoning}}]}),
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "reasoning_content": "x",
                            "content": "hello",
                        }
                    }
                ]
            }
        ),
        "data: [DONE]",
    ]
    fake_client = _FakeHttpClient(lines=lines)
    monkeypatch.setattr(
        "core.ai.providers.openai_compatible_provider.httpx.Client",
        lambda **kwargs: fake_client,
    )
    times = iter([0.0, 0.1, 0.5, 1.0, 1.1, 1.2])
    monkeypatch.setattr(
        "core.ai.providers.openai_compatible_provider.time.perf_counter",
        lambda: next(times),
    )

    provider = OpenAICompatibleProvider(
        "https://example.test/v1",
        "sk-test",
        "glm-5.1",
    )
    chunks = list(
        provider.generate_stream(
            [{"role": "user", "content": "hi"}],
            "glm-5.1",
            reasoning_effort="low",
            disable_thinking=True,
        )
    )

    payload = fake_client.stream_requests[0]["kwargs"]["json"]
    assert payload["reasoning_effort"] == "low"
    assert payload["thinking"] == {"type": "disabled"}
    assert chunks == ["hello"]
    assert hidden_reasoning not in "".join(chunks)
    assert provider.last_response_metadata["reasoning_chars"] == (
        len(hidden_reasoning) + 1
    )
    assert provider.last_response_metadata["first_reasoning_ms"] == 500.0
    assert provider.last_response_metadata["first_content_ms"] == 1000.0
    assert provider.last_response_metadata["total_duration_ms"] == 1200.0


def test_ai_client_forwards_stream_thinking_controls(monkeypatch) -> None:
    provider = OpenAICompatibleProvider(
        "https://example.test/v1",
        "sk-test",
        "glm-5.1",
    )
    captured: dict = {}

    def _fake_generate_stream(messages, model, max_tokens, **kwargs):
        captured.update(kwargs)
        provider.last_response_metadata = {
            "model": model,
            "reasoning_chars": 0,
            "first_reasoning_ms": None,
            "first_content_ms": 12.5,
            "total_duration_ms": 20.0,
        }
        yield "[]"

    monkeypatch.setattr(provider, "generate_stream", _fake_generate_stream)
    client = AIClient(provider=provider, init_from_settings=False)

    chunks = list(
        client.generate_response_stream(
            "真实需求",
            "生成用例",
            task_type="generation",
            reasoning_effort="low",
            disable_thinking=True,
        )
    )

    assert chunks == ["[]"]
    assert captured["reasoning_effort"] == "low"
    assert captured["disable_thinking"] is True
    assert client.last_response_metadata["first_content_ms"] == 12.5


def test_responses_stream_does_not_duplicate_done_or_completed_text(monkeypatch) -> None:
    lines = [
        _sse({"type": "response.output_text.delta", "delta": "hello"}),
        _sse({"type": "response.output_text.done", "text": "hello"}),
        _sse({"type": "response.completed", "response": {"output_text": "hello"}}),
        "data: [DONE]",
    ]

    monkeypatch.setattr(
        "core.ai.providers.openai_compatible_provider.httpx.Client",
        lambda **kwargs: _FakeHttpClient(lines=lines),
    )

    provider = OpenAICompatibleProvider("https://example.test/v1/responses", "sk-test", "model")
    chunks = list(provider.generate_stream([{"role": "user", "content": "hi"}], "model"))

    assert chunks == ["hello"]


def test_json_chat_request_uses_low_reasoning_effort(monkeypatch) -> None:
    fake_client = _FakePostClient(
        [
            _FakePostResponse(
                200,
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": '{"metrics":{},"defect_analysis":{}}',
                            },
                        }
                    ]
                },
            )
        ]
    )
    monkeypatch.setattr(
        "core.ai.providers.openai_compatible_provider.httpx.Client",
        lambda **kwargs: fake_client,
    )

    provider = OpenAICompatibleProvider("https://example.test/v1", "sk-test", "model")
    result = provider.generate([{"role": "user", "content": "只输出 JSON"}], "model", max_tokens=100)

    assert result == '{"metrics":{},"defect_analysis":{}}'
    assert fake_client.posts[0]["json"]["temperature"] == 0.0
    assert fake_client.posts[0]["json"]["reasoning_effort"] == "low"
    assert fake_client.posts[0]["json"]["response_format"] == {"type": "json_object"}
    assert fake_client.posts[0]["json"]["thinking"] == {"type": "disabled"}


def test_json_chat_request_falls_back_when_reasoning_effort_rejected(monkeypatch) -> None:
    fake_client = _FakePostClient(
        [
            _FakePostResponse(400, text="unsupported reasoning_effort"),
            _FakePostResponse(
                200,
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": '{"metrics":{},"defect_analysis":{}}',
                            },
                        }
                    ]
                },
            ),
        ]
    )
    monkeypatch.setattr(
        "core.ai.providers.openai_compatible_provider.httpx.Client",
        lambda **kwargs: fake_client,
    )

    provider = OpenAICompatibleProvider("https://example.test/v1", "sk-test", "model")
    result = provider.generate([{"role": "user", "content": "只输出 JSON"}], "model", max_tokens=100)

    assert result == '{"metrics":{},"defect_analysis":{}}'
    assert fake_client.posts[0]["json"]["reasoning_effort"] == "low"
    assert fake_client.posts[0]["json"]["response_format"] == {"type": "json_object"}
    assert fake_client.posts[0]["json"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in fake_client.posts[1]["json"]
    assert "response_format" not in fake_client.posts[1]["json"]
    assert "thinking" not in fake_client.posts[1]["json"]
    assert provider.last_response_metadata["json_compat_fallback"] is True


def test_json_chat_request_can_disable_json_compat_fields(monkeypatch) -> None:
    fake_client = _FakePostClient(
        [
            _FakePostResponse(
                200,
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": '{"add_cases":[],"replace_cases":[],"drop_case_ids":[],"fix_notes":[]}',
                            },
                        }
                    ]
                },
            )
        ]
    )
    monkeypatch.setattr(
        "core.ai.providers.openai_compatible_provider.httpx.Client",
        lambda **kwargs: fake_client,
    )

    provider = OpenAICompatibleProvider("https://example.test/v1", "sk-test", "model")
    provider.disable_json_response_format = True
    provider.disable_json_reasoning_effort = True
    provider.disable_json_thinking = True
    result = provider.generate([{"role": "user", "content": "Return JSON only"}], "model", max_tokens=100)

    assert result.startswith('{"add_cases"')
    assert "reasoning_effort" not in fake_client.posts[0]["json"]
    assert "response_format" not in fake_client.posts[0]["json"]
    assert "thinking" not in fake_client.posts[0]["json"]


def test_explicit_text_mode_does_not_enable_json_for_incidental_json_word(monkeypatch) -> None:
    fake_client = _FakePostClient(
        [
            _FakePostResponse(
                200,
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": "class LoginPage:\n    pass",
                            },
                        }
                    ]
                },
            )
        ]
    )
    monkeypatch.setattr(
        "core.ai.providers.openai_compatible_provider.httpx.Client",
        lambda **kwargs: fake_client,
    )

    provider = OpenAICompatibleProvider("https://example.test/v1", "sk-test", "model")
    result = provider.generate(
        [{"role": "user", "content": "生成 Python，并打印 JSON 格式日志"}],
        "model",
        max_tokens=100,
        response_mode="text",
    )

    assert result.startswith("class LoginPage")
    assert "response_format" not in fake_client.posts[0]["json"]
    assert "reasoning_effort" not in fake_client.posts[0]["json"]
    assert "thinking" not in fake_client.posts[0]["json"]
    assert provider.last_response_metadata["response_mode"] == "text"
    assert provider.last_response_metadata["json_response"] is False


def test_explicit_json_mode_enables_structured_response_without_json_word(monkeypatch) -> None:
    fake_client = _FakePostClient(
        [
            _FakePostResponse(
                200,
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": '{"cases":[]}',
                            },
                        }
                    ]
                },
            )
        ]
    )
    monkeypatch.setattr(
        "core.ai.providers.openai_compatible_provider.httpx.Client",
        lambda **kwargs: fake_client,
    )

    provider = OpenAICompatibleProvider("https://example.test/v1", "sk-test", "model")
    result = provider.generate(
        [{"role": "user", "content": "生成测试用例"}],
        "model",
        max_tokens=100,
        response_mode="json",
    )

    assert result == '{"cases":[]}'
    assert fake_client.posts[0]["json"]["response_format"] == {"type": "json_object"}
    assert provider.last_response_metadata["response_mode"] == "json"
    assert provider.last_response_metadata["json_response"] is True


def test_non_stream_request_uses_call_level_timeout_override(monkeypatch) -> None:
    fake_client = _FakePostClient(
        [
            _FakePostResponse(
                200,
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "ok"},
                        }
                    ]
                },
            )
        ]
    )
    client_kwargs: dict = {}

    def _client_factory(**kwargs):
        client_kwargs.update(kwargs)
        return fake_client

    monkeypatch.setattr(
        "core.ai.providers.openai_compatible_provider.httpx.Client",
        _client_factory,
    )
    provider = OpenAICompatibleProvider(
        "https://example.test/v1", "sk-test", "model"
    )

    result = provider.generate(
        [{"role": "user", "content": "hi"}],
        "model",
        max_tokens=100,
        response_mode="text",
        request_timeout_seconds=180,
    )

    assert result == "ok"
    assert client_kwargs["timeout"].read == 180.0
    assert client_kwargs["timeout"].connect == 15.0
    assert client_kwargs["timeout"].write == 30.0
    assert client_kwargs["timeout"].pool == 15.0
    assert provider.last_response_metadata["request_timeout_seconds"] == 180.0
    assert provider.last_response_metadata["request_timeout_source"] == (
        "call_override"
    )


def test_responses_incomplete_max_output_tokens_returns_partial_without_stream_fallback(
    monkeypatch,
) -> None:
    partial = '{"evidence_facts":['
    fake_client = _FakePostClient(
        [
            _FakePostResponse(
                200,
                {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": partial}
                            ],
                        }
                    ],
                },
            )
        ]
    )
    monkeypatch.setattr(
        "core.ai.providers.openai_compatible_provider.httpx.Client",
        lambda **kwargs: fake_client,
    )
    provider = OpenAICompatibleProvider(
        "https://example.test/v1/responses",
        "sk-test",
        "model",
    )

    result = provider.generate(
        [{"role": "user", "content": "只输出 JSON"}],
        "model",
        max_tokens=100,
    )

    assert result == partial
    assert len(fake_client.posts) == 1
    assert provider.last_response_metadata["response_status"] == "incomplete"
    assert provider.last_response_metadata["incomplete_reason"] == (
        "max_output_tokens"
    )
    assert provider.last_response_metadata["finish_reason"] == "length"
    assert provider.last_response_metadata["content_len"] == len(partial)
    assert "stream_fallback_content_len" not in provider.last_response_metadata


def test_responses_incomplete_without_text_keeps_length_metadata_and_does_not_stream(
    monkeypatch,
) -> None:
    fake_client = _FakePostClient(
        [
            _FakePostResponse(
                200,
                {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "output": [],
                },
            )
        ]
    )
    monkeypatch.setattr(
        "core.ai.providers.openai_compatible_provider.httpx.Client",
        lambda **kwargs: fake_client,
    )
    provider = OpenAICompatibleProvider(
        "https://example.test/v1/responses",
        "sk-test",
        "model",
    )

    result = provider.generate(
        [{"role": "user", "content": "只输出 JSON"}],
        "model",
        max_tokens=100,
    )

    assert result == ""
    assert len(fake_client.posts) == 1
    assert provider.last_response_metadata["finish_reason"] == "length"
    assert provider.last_response_metadata["content_len"] == 0
    assert "stream_fallback_content_len" not in provider.last_response_metadata
