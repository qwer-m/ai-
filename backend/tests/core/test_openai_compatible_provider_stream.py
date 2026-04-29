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
