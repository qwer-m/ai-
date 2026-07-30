from __future__ import annotations

import json
from typing import Any

import pytest

from core.ai.ai_client import AIClient
from core.ai.providers.base import BaseModelProvider
from core.db.models import SystemConfig
from modules.test_generation_components.control.model_envelope_call import (
    EnvelopeCallResult,
    invoke_model_envelope,
)


_DB_SENTINEL = object()
_SYSTEM_PROMPT = "编译结构化需求语义"
_USER_INPUT = "原始需求内容-sensitive-probe"


class _MemoryCacheService:
    """使用真实内存字典执行完整缓存读写语义。"""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], Any] = {}
        self.get_keys: list[tuple[str, str]] = []
        self.set_calls: list[dict[str, Any]] = []

    def get(self, key_content: str, level: str, db: Any = None) -> Any:
        _ = db
        key = (level, key_content)
        self.get_keys.append(key)
        return self.store.get(key)

    def set(
        self,
        key_content: str,
        value: Any,
        level: str,
        db: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        _ = db
        key = (level, key_content)
        self.store[key] = value
        self.set_calls.append(
            {
                "key": key,
                "value": value,
                "metadata": dict(metadata or {}),
            }
        )


class _MetadataProvider(BaseModelProvider):
    """按真实 provider 边界产生文本与终止元数据。"""

    def __init__(self, *responses: tuple[str, dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.last_response_metadata: dict[str, Any] = {}

    def generate(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
    ) -> str:
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "max_tokens": max_tokens,
            }
        )
        response_index = min(len(self.calls) - 1, len(self.responses) - 1)
        text, metadata = self.responses[response_index]
        self.last_response_metadata = dict(metadata)
        self.last_response_metadata.setdefault("max_tokens", max_tokens)
        return text

    def generate_stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
    ):
        yield self.generate(messages, model, max_tokens)

    def multimodal_generate(
        self,
        messages: list[dict[str, Any]],
        model: str,
    ) -> str:
        _ = (messages, model)
        return ""

    def test_connection(self) -> dict[str, Any]:
        return {"success": True}


def _new_client(provider: BaseModelProvider) -> AIClient:
    client = AIClient(provider=provider)
    client.model = "contract-model"
    return client


def _invoke(client: AIClient) -> EnvelopeCallResult:
    return invoke_model_envelope(
        client=client,
        envelope_id="l4-cache-contract-probe",
        user_input=_USER_INPUT,
        system_prompt=_SYSTEM_PROMPT,
        db=_DB_SENTINEL,
        max_tokens=4096,
        task_type="generation",
        request_timeout_seconds=180,
        max_transport_replays=0,
    )


def test_truncated_response_is_never_written_to_l4_and_each_envelope_keeps_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partial_text = '{"evidence_facts":['
    truncated_metadata = {
        "http_status": 200,
        "wire_api": "responses",
        "finish_reason": "length",
        "response_status": "incomplete",
        "incomplete_reason": "max_output_tokens",
        "content_len": len(partial_text),
    }
    provider = _MetadataProvider(
        (partial_text, truncated_metadata),
        (partial_text, truncated_metadata),
    )
    cache = _MemoryCacheService()
    monkeypatch.setattr("core.ai.ai_client_impl.cache_service", cache)
    client = _new_client(provider)

    first = _invoke(client)
    second = _invoke(client)

    assert len(provider.calls) == 2
    assert cache.store == {}
    assert cache.set_calls == []
    for envelope in (first, second):
        assert envelope.status == "response"
        assert envelope.response_metadata["finish_reason"] == "length"
        assert envelope.response_metadata["response_status"] == "incomplete"
        assert (
            envelope.response_metadata["incomplete_reason"]
            == "max_output_tokens"
        )


@pytest.mark.parametrize(
    "terminal_metadata",
    [
        {
            "finish_reason": "length",
            "response_status": "completed",
        },
        {
            "finish_reason": "stop",
            "response_status": "incomplete",
        },
    ],
    ids=["finish_reason_length", "response_status_incomplete"],
)
def test_each_incomplete_terminal_condition_independently_blocks_l4_write(
    monkeypatch: pytest.MonkeyPatch,
    terminal_metadata: dict[str, Any],
) -> None:
    provider = _MetadataProvider(
        ('{"partial":true}', terminal_metadata),
        ('{"partial":true}', terminal_metadata),
    )
    cache = _MemoryCacheService()
    monkeypatch.setattr("core.ai.ai_client_impl.cache_service", cache)
    client = _new_client(provider)

    first = client.generate_response(
        _USER_INPUT,
        _SYSTEM_PROMPT,
        db=_DB_SENTINEL,
        max_tokens=4096,
        task_type="generation",
    )
    second = client.generate_response(
        _USER_INPUT,
        _SYSTEM_PROMPT,
        db=_DB_SENTINEL,
        max_tokens=4096,
        task_type="generation",
    )

    assert first == '{"partial":true}'
    assert second == '{"partial":true}'
    assert len(provider.calls) == 2
    assert cache.set_calls == []


def test_complete_response_hits_l4_and_restores_sanitized_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_text = '{"evidence_facts":[]}'
    provider = _MetadataProvider(
        (
            response_text,
            {
                "http_status": 200,
                "wire_api": "responses",
                "request_timeout_seconds": 180.0,
                "request_timeout_source": "call_override",
                "finish_reason": "stop",
                "response_status": "completed",
                "incomplete_reason": "",
                "response_format": {
                    "type": "json_object",
                    "api_key": "response-format-secret",
                    "nested": {"unsafe": True},
                },
                "json_compat_fallback": True,
                "thinking": {
                    "type": "disabled",
                    "access_token": "thinking-secret",
                },
                "content_len": len(response_text),
                "raw_keys": ["output", "usage"],
                "error_preview": "api_key=must-not-be-cached",
            },
        )
    )
    cache = _MemoryCacheService()
    monkeypatch.setattr("core.ai.ai_client_impl.cache_service", cache)
    client = _new_client(provider)

    first = _invoke(client)
    first_client_metadata = dict(client.last_response_metadata)
    second = _invoke(client)

    assert first.raw_text == response_text
    assert second.raw_text == response_text
    assert len(provider.calls) == 1
    assert len(cache.set_calls) == 1
    assert second.response_metadata["cached"] is True
    assert first.physical_call_count == 1
    assert first.provider_call_count == 1
    assert first.cache_hit_count == 0
    assert first.cache_miss_count == 1
    assert second.physical_call_count == 1
    assert second.provider_call_count == 0
    assert second.cache_hit_count == 1
    assert second.cache_miss_count == 0
    for key in (
        "finish_reason",
        "response_status",
        "incomplete_reason",
        "wire_api",
        "http_status",
        "request_timeout_seconds",
        "request_timeout_source",
        "json_compat_fallback",
        "content_len",
    ):
        assert client.last_response_metadata[key] == first_client_metadata[key]
    assert client.last_response_metadata["response_format"] == {
        "type": "json_object"
    }
    assert client.last_response_metadata["thinking"] == {
        "type": "disabled"
    }

    stored_key = cache.set_calls[0]["key"][1]
    stored_value = cache.set_calls[0]["value"]
    assert stored_key.startswith("ai-client-text-response-v2:")
    assert _USER_INPUT not in stored_key
    assert _SYSTEM_PROMPT not in stored_key
    assert set(stored_value) == {
        "cache_contract",
        "text",
        "response_metadata",
    }
    assert "raw_keys" not in stored_value["response_metadata"]
    assert "error_preview" not in stored_value["response_metadata"]
    assert stored_value["response_metadata"]["response_format"] == {
        "type": "json_object"
    }
    assert stored_value["response_metadata"]["thinking"] == {
        "type": "disabled"
    }
    assert "must-not-be-cached" not in json.dumps(
        stored_value,
        ensure_ascii=False,
    )
    assert "response-format-secret" not in json.dumps(
        stored_value,
        ensure_ascii=False,
    )
    assert "thinking-secret" not in json.dumps(
        stored_value,
        ensure_ascii=False,
    )
    assert AIClient._cached_text_response(stored_value) == response_text


@pytest.mark.parametrize(
    "malformed",
    [
        '{"statement":"包含"未转义术语"区域"}',
        '{"confidence":NaN}',
        '{"confidence":Infinity}',
        '{"confidence":-Infinity}',
    ],
    ids=["unescaped_quote", "nan", "positive_infinity", "negative_infinity"],
)
def test_complete_but_malformed_json_is_never_written_to_l4(
    monkeypatch: pytest.MonkeyPatch,
    malformed: str,
) -> None:
    terminal_metadata = {
        "finish_reason": "stop",
        "response_status": "completed",
        # provider 不能把本次 generation/json 调用降级成 text 后污染缓存。
        "response_mode": "text",
    }
    provider = _MetadataProvider(
        (malformed, terminal_metadata),
        (malformed, terminal_metadata),
    )
    cache = _MemoryCacheService()
    monkeypatch.setattr("core.ai.ai_client_impl.cache_service", cache)
    client = _new_client(provider)

    first = client.generate_response(
        _USER_INPUT,
        _SYSTEM_PROMPT,
        db=_DB_SENTINEL,
        max_tokens=4096,
        task_type="generation",
    )
    second = client.generate_response(
        _USER_INPUT,
        _SYSTEM_PROMPT,
        db=_DB_SENTINEL,
        max_tokens=4096,
        task_type="generation",
    )

    assert first == malformed
    assert second == malformed
    assert len(provider.calls) == 2
    assert cache.store == {}
    assert cache.set_calls == []


def test_malformed_json_from_existing_l4_is_ignored_and_overwritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    malformed = '{"statement":"包含"未转义术语"区域"}'
    valid = '{"statement":"包含“已编码术语”区域"}'
    provider = _MetadataProvider(
        (
            valid,
            {
                "finish_reason": "stop",
                "response_status": "completed",
            },
        )
    )
    cache = _MemoryCacheService()
    monkeypatch.setattr("core.ai.ai_client_impl.cache_service", cache)
    client = _new_client(provider)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _USER_INPUT},
    ]
    cache_key = client._build_l4_cache_key(
        messages=messages,
        target_model="contract-model",
        target_provider=provider,
        max_tokens=4096,
        task_type="generation",
        response_mode="json",
        reasoning_effort=None,
        disable_thinking=False,
    )
    cache.store[("L4", cache_key)] = client._build_l4_cache_value(
        malformed,
        {
            "finish_reason": "stop",
            "response_status": "completed",
            "response_mode": "text",
        },
    )

    result = client.generate_response(
        _USER_INPUT,
        _SYSTEM_PROMPT,
        db=_DB_SENTINEL,
        max_tokens=4096,
        task_type="generation",
    )

    assert result == valid
    assert len(provider.calls) == 1
    assert len(cache.set_calls) == 1
    assert cache.store[("L4", cache_key)]["text"] == valid


def test_auto_mode_with_provider_json_signal_rejects_malformed_l4_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    malformed = '{"statement":"包含"未转义术语"区域"}'
    provider = _MetadataProvider(
        (
            malformed,
            {
                "finish_reason": "stop",
                "response_status": "completed",
                "json_response": True,
            },
        )
    )
    cache = _MemoryCacheService()
    monkeypatch.setattr("core.ai.ai_client_impl.cache_service", cache)
    client = _new_client(provider)

    result = client.generate_response(
        _USER_INPUT,
        _SYSTEM_PROMPT,
        db=_DB_SENTINEL,
        max_tokens=4096,
        task_type="general",
        response_mode="auto",
    )

    assert result == malformed
    assert len(provider.calls) == 1
    assert cache.set_calls == []


def test_legacy_l4_key_cannot_hit_versioned_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_text = '{"source":"provider"}'
    provider = _MetadataProvider(
        (
            provider_text,
            {
                "finish_reason": "stop",
                "response_status": "completed",
            },
        )
    )
    cache = _MemoryCacheService()
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _USER_INPUT},
    ]
    legacy_key = (
        "contract-model:json:"
        + json.dumps(messages, ensure_ascii=False)
    )
    cache.store[("L4", legacy_key)] = '{"source":"legacy-cache"}'
    monkeypatch.setattr("core.ai.ai_client_impl.cache_service", cache)
    client = _new_client(provider)

    result = client.generate_response(
        _USER_INPUT,
        _SYSTEM_PROMPT,
        db=_DB_SENTINEL,
        max_tokens=4096,
        task_type="generation",
    )

    assert result == provider_text
    assert len(provider.calls) == 1
    assert cache.get_keys[0][1] != legacy_key
    assert cache.get_keys[0][1].startswith("ai-client-text-response-v2:")


def test_provider_without_explicit_completion_metadata_is_never_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _MetadataProvider(
        ('{"unknown_terminal_state":true}', {"http_status": 200}),
        ('{"unknown_terminal_state":true}', {"http_status": 200}),
    )
    cache = _MemoryCacheService()
    monkeypatch.setattr("core.ai.ai_client_impl.cache_service", cache)
    client = _new_client(provider)

    first = client.generate_response(
        _USER_INPUT,
        _SYSTEM_PROMPT,
        db=_DB_SENTINEL,
        max_tokens=4096,
        task_type="generation",
    )
    second = client.generate_response(
        _USER_INPUT,
        _SYSTEM_PROMPT,
        db=_DB_SENTINEL,
        max_tokens=4096,
        task_type="generation",
    )

    assert first == '{"unknown_terminal_state":true}'
    assert second == '{"unknown_terminal_state":true}'
    assert len(provider.calls) == 2
    assert cache.set_calls == []


def _system_config(
    *,
    config_id: int,
    user_id: int,
    version: int,
    base_url: str,
) -> SystemConfig:
    return SystemConfig(
        id=config_id,
        user_id=user_id,
        version=version,
        is_active=1,
        provider="openai",
        api_key=None,
        base_url=base_url,
        model_name="shared-model",
        vl_model_name=None,
        turbo_model_name=None,
        metadata_info={},
    )


def _complete_provider(text: str, *, base_url: str) -> _MetadataProvider:
    provider = _MetadataProvider(
        (
            text,
            {
                "finish_reason": "stop",
                "response_status": "completed",
            },
        )
    )
    provider.base_url = base_url
    provider.wire_api = "chat_completions"
    return provider


def _cache_key_contract(cache_key: str) -> dict[str, Any]:
    _, serialized_contract = cache_key.split(":", 1)
    return json.loads(serialized_contract)


def test_same_model_and_prompt_are_isolated_by_provider_route_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_provider = _complete_provider(
        '{"provider":"first"}',
        base_url="https://provider-one.example/v1",
    )
    second_provider = _complete_provider(
        '{"provider":"second"}',
        base_url="https://provider-two.example/v1",
    )
    first_provider.api_key = "sk-first-provider-secret"
    second_provider.api_key = "sk-second-provider-secret"
    first_client = _new_client(first_provider)
    second_client = _new_client(second_provider)
    cache = _MemoryCacheService()
    monkeypatch.setattr("core.ai.ai_client_impl.cache_service", cache)

    first_result = first_client.generate_response(
        _USER_INPUT,
        _SYSTEM_PROMPT,
        db=_DB_SENTINEL,
        max_tokens=4096,
        task_type="generation",
    )
    second_result = second_client.generate_response(
        _USER_INPUT,
        _SYSTEM_PROMPT,
        db=_DB_SENTINEL,
        max_tokens=4096,
        task_type="generation",
    )

    assert first_result == '{"provider":"first"}'
    assert second_result == '{"provider":"second"}'
    assert len(first_provider.calls) == 1
    assert len(second_provider.calls) == 1
    contracts = [
        _cache_key_contract(item["key"][1])
        for item in cache.set_calls
    ]
    assert len({item["provider_identity_sha256"] for item in contracts}) == 2
    assert len({item["cache_namespace_sha256"] for item in contracts}) == 1
    serialized_keys = "\n".join(item["key"][1] for item in cache.set_calls)
    assert "provider-one.example" not in serialized_keys
    assert "provider-two.example" not in serialized_keys
    assert "sk-first-provider-secret" not in serialized_keys
    assert "sk-second-provider-secret" not in serialized_keys


def test_same_provider_model_and_prompt_are_isolated_by_system_config_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_endpoint = "https://shared-provider.example/v1"
    first_client = AIClient.from_config(
        _system_config(
            config_id=101,
            user_id=11,
            version=3,
            base_url=shared_endpoint,
        )
    )
    second_client = AIClient.from_config(
        _system_config(
            config_id=202,
            user_id=22,
            version=7,
            base_url=shared_endpoint,
        )
    )
    first_provider = _complete_provider(
        '{"config":"first"}',
        base_url=shared_endpoint,
    )
    second_provider = _complete_provider(
        '{"config":"second"}',
        base_url=shared_endpoint,
    )
    first_client.update_provider(first_provider, "shared-model")
    second_client.update_provider(second_provider, "shared-model")
    cache = _MemoryCacheService()
    monkeypatch.setattr("core.ai.ai_client_impl.cache_service", cache)

    first_result = first_client.generate_response(
        _USER_INPUT,
        _SYSTEM_PROMPT,
        db=_DB_SENTINEL,
        max_tokens=4096,
        task_type="generation",
    )
    second_result = second_client.generate_response(
        _USER_INPUT,
        _SYSTEM_PROMPT,
        db=_DB_SENTINEL,
        max_tokens=4096,
        task_type="generation",
    )

    assert first_result == '{"config":"first"}'
    assert second_result == '{"config":"second"}'
    assert len(first_provider.calls) == 1
    assert len(second_provider.calls) == 1
    contracts = [
        _cache_key_contract(item["key"][1])
        for item in cache.set_calls
    ]
    assert len({item["cache_namespace_sha256"] for item in contracts}) == 2
    assert len({item["provider_identity_sha256"] for item in contracts}) == 1
    assert shared_endpoint not in "\n".join(
        item["key"][1] for item in cache.set_calls
    )


@pytest.mark.parametrize(
    "changed_field",
    ["config_id", "user_id", "version"],
)
def test_each_system_config_identity_field_changes_cache_namespace(
    changed_field: str,
) -> None:
    base_values = {
        "config_id": 401,
        "user_id": 44,
        "version": 2,
        "base_url": "https://same-provider.example/v1",
    }
    changed_values = dict(base_values)
    changed_values[changed_field] = int(changed_values[changed_field]) + 1

    base_namespace = AIClient._config_cache_namespace(
        _system_config(**base_values)
    )
    changed_namespace = AIClient._config_cache_namespace(
        _system_config(**changed_values)
    )

    assert base_namespace != changed_namespace


@pytest.mark.parametrize(
    "task_type",
    ["review", "compression"],
)
def test_l4_key_uses_actual_review_or_turbo_provider_identity(
    monkeypatch: pytest.MonkeyPatch,
    task_type: str,
) -> None:
    main_provider = _complete_provider(
        '{"route":"main"}',
        base_url="https://main-provider.example/v1",
    )
    route_provider = _complete_provider(
        f'{{"route":"{task_type}"}}',
        base_url=f"https://{task_type}-provider.example/v1",
    )
    client = _new_client(main_provider)
    if task_type == "review":
        client.review_model = "route-model"
        client.review_provider = route_provider
    else:
        client.turbo_model = "route-model"
        client.turbo_provider = route_provider
    cache = _MemoryCacheService()
    monkeypatch.setattr("core.ai.ai_client_impl.cache_service", cache)

    result = client.generate_response(
        _USER_INPUT,
        _SYSTEM_PROMPT,
        db=_DB_SENTINEL,
        max_tokens=4096,
        task_type=task_type,
        response_mode="json",
    )

    assert result == f'{{"route":"{task_type}"}}'
    assert main_provider.calls == []
    assert len(route_provider.calls) == 1
    contract = _cache_key_contract(cache.set_calls[0]["key"][1])
    assert contract["provider_identity_sha256"] == (
        AIClient._provider_cache_identity(route_provider)
    )
    assert contract["provider_identity_sha256"] != (
        AIClient._provider_cache_identity(main_provider)
    )


def test_update_and_replace_runtime_synchronize_config_cache_namespace() -> None:
    configured = AIClient.from_config(
        _system_config(
            config_id=303,
            user_id=33,
            version=9,
            base_url="https://configured-provider.example/v1",
        )
    )
    updated = _new_client(
        _complete_provider(
            '{"runtime":"updated"}',
            base_url="https://temporary-provider.example/v1",
        )
    )
    replaced = _new_client(
        _complete_provider(
            '{"runtime":"replaced"}',
            base_url="https://temporary-provider.example/v1",
        )
    )

    updated.update_provider(configured.provider, configured.model)
    replaced.replace_runtime_from(configured)

    assert updated._cache_namespace == configured._cache_namespace
    assert replaced._cache_namespace == configured._cache_namespace


@pytest.mark.parametrize(
    "changed_argument",
    [
        {"max_tokens": 2048},
        {"task_type": "review"},
        {"response_mode": "text"},
        {"reasoning_effort": "high"},
        {"disable_thinking": True},
    ],
    ids=[
        "max_tokens",
        "task_type",
        "response_mode",
        "reasoning_effort",
        "disable_thinking",
    ],
)
def test_response_affecting_arguments_are_part_of_l4_key(
    monkeypatch: pytest.MonkeyPatch,
    changed_argument: dict[str, Any],
) -> None:
    provider = _MetadataProvider(
        (
            '{"ok":true}',
            {
                "finish_reason": "stop",
                "response_status": "completed",
            },
        )
    )
    cache = _MemoryCacheService()
    monkeypatch.setattr("core.ai.ai_client_impl.cache_service", cache)
    client = _new_client(provider)
    base_arguments: dict[str, Any] = {
        "db": _DB_SENTINEL,
        "max_tokens": 4096,
        "task_type": "generation",
        "response_mode": "json",
        "reasoning_effort": "low",
        "disable_thinking": False,
    }

    first = client.generate_response(
        _USER_INPUT,
        _SYSTEM_PROMPT,
        **base_arguments,
    )
    second = client.generate_response(
        _USER_INPUT,
        _SYSTEM_PROMPT,
        **{**base_arguments, **changed_argument},
    )

    assert first == '{"ok":true}'
    assert second == '{"ok":true}'
    assert len(provider.calls) == 2
    assert len(cache.store) == 2
