from __future__ import annotations

from typing import Any

import pytest

from modules.test_generation_components.control.model_envelope_call import (
    EnvelopeCallResult,
    classify_response_termination,
    invoke_model_envelope,
    strict_json_output_contract_prompt,
)


_DB_SENTINEL = object()
_SUCCESS_RESPONSE = '{"evidence_facts":[]}'


def test_strict_json_output_contract_is_shared_generic_and_parse_complete() -> None:
    contract = strict_json_output_contract_prompt()

    assert "exactly one minified RFC 8259 JSON value" in contract
    assert "matching the stage-declared response grammar" in contract
    assert "including text copied or transformed from the input" in contract
    assert r'escape it as \"' in contract
    assert "U+201C/U+201D" in contract
    assert "U+0000 through U+001F" in contract
    assert "NaN, Infinity, or -Infinity" in contract
    assert "validate the exact final output" in contract
    assert "JSON-looking text that fails parsing" in contract
    for business_term in ("论坛", "作文", "官方区", "反馈区"):
        assert business_term not in contract


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({}, "unknown"),
        ({"response_status": "completed"}, "complete"),
        ({"finish_reason": "stop"}, "complete"),
        ({"finish_reason": "length"}, "truncated"),
        (
            {
                "response_status": "completed",
                "incomplete_reason": "max_output_tokens",
            },
            "truncated",
        ),
        ({"finish_reason": "content_filter"}, "incomplete"),
        ({"response_status": "failed"}, "incomplete"),
        (
            {
                "response_status": "incomplete",
                "incomplete_reason": "content_filter",
                "finish_reason": "stop",
            },
            "incomplete",
        ),
    ],
)
def test_response_termination_classification_is_shared_and_fail_closed(
    metadata: dict[str, Any],
    expected: str,
) -> None:
    assert classify_response_termination(metadata) == expected


class _ScriptedClient:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.last_response_metadata: dict[str, Any] = {}

    def generate_response(
        self,
        user_input: str,
        system_prompt: str | None,
        **kwargs: Any,
    ) -> str:
        self.calls.append(
            {
                "user_input": user_input,
                "system_prompt": system_prompt,
                **dict(kwargs),
            }
        )
        response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        self.last_response_metadata = {}
        if isinstance(response, tuple) and len(response) == 2:
            response, metadata = response
            self.last_response_metadata = dict(metadata or {})
        if isinstance(response, Exception):
            raise response
        return str(response)


def _invoke(
    client: _ScriptedClient,
    *,
    max_transport_replays: int = 1,
) -> EnvelopeCallResult:
    return invoke_model_envelope(
        client=client,
        envelope_id="ledger-initial-1",
        user_input='{"compilation_mode":"initial"}',
        system_prompt="输出结构化事实账本",
        db=_DB_SENTINEL,
        max_tokens=4096,
        task_type="generation",
        request_timeout_seconds=180,
        max_transport_replays=max_transport_replays,
    )


def test_model_envelope_returns_first_non_error_response_without_retry() -> None:
    client = _ScriptedClient(
        (
            _SUCCESS_RESPONSE,
            {
                "http_status": 200,
                "model": "contract-model",
                "content_len": len(_SUCCESS_RESPONSE),
            },
        )
    )

    result = _invoke(client)

    assert result.status == "response"
    assert result.raw_text == _SUCCESS_RESPONSE
    assert result.physical_call_count == 1
    assert result.transport_failure_count == 0
    assert result.transport_retry_count == 0
    assert result.attempts[0].status == "response"
    assert result.attempts[0].replay_index == 0
    assert result.response_metadata["http_status"] == 200


@pytest.mark.parametrize("partial_text", ['{"evidence_facts":[', ""])
def test_model_envelope_returns_length_response_even_without_partial_text(
    partial_text: str,
) -> None:
    client = _ScriptedClient(
        (
            partial_text,
            {
                "http_status": 200,
                "response_status": "incomplete",
                "incomplete_reason": "max_output_tokens",
                "finish_reason": "length",
                "content_len": len(partial_text),
            },
        ),
        _SUCCESS_RESPONSE,
    )

    result = _invoke(client)

    assert result.status == "response"
    assert result.raw_text == partial_text
    assert result.response_metadata["finish_reason"] == "length"
    assert result.response_metadata["response_status"] == "incomplete"
    assert result.response_metadata["incomplete_reason"] == "max_output_tokens"
    assert result.physical_call_count == 1
    assert len(client.calls) == 1


def test_model_envelope_returns_explicit_incomplete_even_without_text() -> None:
    client = _ScriptedClient(
        (
            "",
            {
                "http_status": 200,
                "response_status": "incomplete",
                "incomplete_reason": "content_filter",
                "finish_reason": "stop",
            },
        ),
        _SUCCESS_RESPONSE,
    )

    result = _invoke(client)

    assert result.status == "response"
    assert result.raw_text == ""
    assert result.physical_call_count == 1
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "transport_failure",
    [
        (
            "Error: HTTP 504 - Gateway Time-out",
            {
                "http_status": 504,
                "wire_api": "chat_completions",
            },
        ),
        (
            TimeoutError("The read operation timed out"),
            {
                "exception_type": "TimeoutError",
                "wire_api": "chat_completions",
            },
        ),
    ],
    ids=["error_response", "raised_timeout"],
)
def test_model_envelope_replays_same_request_once_after_transport_failure(
    transport_failure: Any,
) -> None:
    client = _ScriptedClient(
        transport_failure,
        (_SUCCESS_RESPONSE, {"http_status": 200}),
    )

    result = _invoke(client)

    assert result.status == "response"
    assert result.physical_call_count == 2
    assert result.transport_failure_count == 1
    assert result.transport_retry_count == 1
    assert client.calls[0] == client.calls[1]
    assert [item.replay_index for item in result.attempts] == [0, 1]
    assert result.attempts[0].status == "transient_transport_failure"
    assert result.attempts[0].retry_scheduled is True
    assert result.attempts[1].status == "response"


def test_model_envelope_stops_after_second_transport_failure() -> None:
    gateway_timeout = (
        "Error: HTTP 504 - Gateway Time-out",
        {
            "http_status": 504,
            "wire_api": "chat_completions",
        },
    )
    client = _ScriptedClient(gateway_timeout, gateway_timeout, _SUCCESS_RESPONSE)

    result = _invoke(client)

    assert result.status == "transport_exhausted"
    assert result.physical_call_count == 2
    assert result.transport_failure_count == 2
    assert result.transport_retry_count == 1
    assert len(client.calls) == 2
    assert client.calls[0] == client.calls[1]
    assert [item.retry_scheduled for item in result.attempts] == [True, False]
    assert all(item.timed_out is True for item in result.attempts)


def test_model_envelope_treats_authentication_error_as_fatal_without_retry() -> None:
    secret = "sk-sensitive-auth-token"
    client = _ScriptedClient(
        (
            f"Error: HTTP 401 - api_key={secret}",
            {
                "http_status": 401,
                "error_preview": f"api_key={secret}",
            },
        ),
        _SUCCESS_RESPONSE,
    )

    result = _invoke(client)

    assert result.status == "fatal_model_error"
    assert result.physical_call_count == 1
    assert result.transport_failure_count == 0
    assert result.transport_retry_count == 0
    assert len(client.calls) == 1
    assert result.attempts[0].retry_scheduled is False
    assert result.attempts[0].error_preview == ""
    assert secret not in result.attempts[0].error_preview
    assert result.attempts[0].metadata["http_status"] == 401
    assert "error_preview" not in result.response_metadata


@pytest.mark.parametrize(
    "response",
    [
        RuntimeError("异常正文回显了用户的原始需求内容"),
        (
            "Error: HTTP 400 - 原始需求内容不应进入诊断",
            {
                "http_status": "400",
                "exception_type": "原始需求内容不应进入诊断",
            },
        ),
    ],
    ids=["raised_exception", "provider_error_body"],
)
def test_model_envelope_diagnostic_never_keeps_arbitrary_error_body(
    response: Any,
) -> None:
    client = _ScriptedClient(response)

    result = _invoke(client)
    diagnostic = result.to_diagnostic()
    diagnostic_text = str(diagnostic)

    assert result.status == "fatal_model_error"
    assert result.attempts[0].error_preview == ""
    assert "原始需求" not in diagnostic_text
    assert "用户的原始需求" not in diagnostic_text
    if isinstance(response, Exception):
        assert result.attempts[0].metadata["exception_type"] == "RuntimeError"
    else:
        assert result.attempts[0].metadata["http_status"] == 400
        assert "exception_type" not in result.attempts[0].metadata


def test_model_envelope_rejects_more_than_one_transport_replay() -> None:
    client = _ScriptedClient(_SUCCESS_RESPONSE)

    with pytest.raises(ValueError, match="最多允许一次"):
        _invoke(client, max_transport_replays=2)

    assert client.calls == []
