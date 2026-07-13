from __future__ import annotations

import json

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

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def stream(self, *args, **kwargs):
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
    times = iter([0.0, 2.0])
    monkeypatch.setattr(
        "core.ai.providers.openai_compatible_provider.time.perf_counter",
        lambda: next(times),
    )

    provider = OpenAICompatibleProvider("https://example.test/v1", "sk-test", "glm-5.1")
    chunks = list(provider.generate_stream([{"role": "user", "content": "hi"}], "glm-5.1"))

    assert chunks == ["Exception occurred: stream_attempt_timeout_after_1s"]
    assert provider.last_response_metadata["exception_type"] == "StreamAttemptTimeout"
    assert provider.last_response_metadata["stream_attempt_timeout_seconds"] == 1


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
