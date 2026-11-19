from __future__ import annotations

from http import HTTPStatus

from core.ai.providers.dashscope_provider import DashScopeProvider


class _Obj:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _multimodal_ok(text: str):
    return _Obj(
        status_code=HTTPStatus.OK,
        output=_Obj(choices=[{"message": {"content": [{"text": text}]}}]),
        code="",
        message="",
    )


def _generation_ok(text: str):
    return _Obj(
        status_code=HTTPStatus.OK,
        output=_Obj(choices=[{"message": {"content": text}}]),
        code="",
        message="",
    )


def test_qwen35_text_generate_routes_to_multimodal(monkeypatch):
    provider = DashScopeProvider(api_key="sk-test")
    calls = {"multi": 0, "gen": 0, "messages": None}

    def _fake_multi(*, model, messages, api_key, max_tokens):
        assert api_key == provider.api_key
        assert max_tokens == provider._max_output_tokens_default
        calls["multi"] += 1
        calls["messages"] = messages
        assert model == "qwen3.5-plus"
        return _multimodal_ok("mm ok")

    def _fake_generation(**kwargs):
        assert kwargs["api_key"] == provider.api_key
        calls["gen"] += 1
        return _generation_ok("gen ok")

    monkeypatch.setattr("core.ai.providers.dashscope_provider.dashscope.MultiModalConversation.call", _fake_multi)
    monkeypatch.setattr("core.ai.providers.dashscope_provider.dashscope.Generation.call", _fake_generation)

    result = provider.generate(
        [
            {"role": "system", "content": "sys prompt"},
            {"role": "user", "content": "hello"},
        ],
        "qwen3.5-plus",
    )

    assert result == "mm ok"
    assert calls["multi"] == 1
    assert calls["gen"] == 0
    assert calls["messages"][0]["content"] == [{"text": "sys prompt"}]
    assert calls["messages"][1]["content"] == [{"text": "hello"}]


def test_non_multimodal_model_still_uses_generation(monkeypatch):
    provider = DashScopeProvider(api_key="sk-test")
    calls = {"multi": 0, "gen": 0}

    def _fake_multi(*, model, messages, api_key, max_tokens):
        assert api_key == provider.api_key
        assert max_tokens == provider._max_output_tokens_default
        calls["multi"] += 1
        return _multimodal_ok("mm")

    def _fake_generation(**kwargs):
        assert kwargs["api_key"] == provider.api_key
        calls["gen"] += 1
        return _generation_ok("gen ok")

    monkeypatch.setattr("core.ai.providers.dashscope_provider.dashscope.MultiModalConversation.call", _fake_multi)
    monkeypatch.setattr("core.ai.providers.dashscope_provider.dashscope.Generation.call", _fake_generation)

    result = provider.generate([{"role": "user", "content": "hello"}], "qwen-plus")

    assert result == "gen ok"
    assert calls["gen"] == 1
    assert calls["multi"] == 0


def test_qwen35_stream_routes_to_multimodal_once(monkeypatch):
    provider = DashScopeProvider(api_key="sk-test")
    calls = {"multi": 0, "gen": 0}

    def _fake_multi(*, model, messages, api_key, max_tokens):
        assert api_key == provider.api_key
        assert max_tokens == provider._max_output_tokens_default
        calls["multi"] += 1
        return _multimodal_ok("stream ok")

    def _fake_generation(**kwargs):
        assert kwargs["api_key"] == provider.api_key
        calls["gen"] += 1
        return []

    monkeypatch.setattr("core.ai.providers.dashscope_provider.dashscope.MultiModalConversation.call", _fake_multi)
    monkeypatch.setattr("core.ai.providers.dashscope_provider.dashscope.Generation.call", _fake_generation)

    chunks = list(provider.generate_stream([{"role": "user", "content": "hello"}], "qwen3.5-plus-2026-02-15"))

    assert chunks == ["stream ok"]
    assert calls["multi"] == 1
    assert calls["gen"] == 0


def test_test_connection_routes_multimodal_models(monkeypatch):
    provider = DashScopeProvider(api_key="sk-test")
    calls = {"multi": 0, "gen": 0}

    def _fake_multi(*, model, messages, api_key, max_tokens):
        assert api_key == provider.api_key
        assert max_tokens == provider._max_output_tokens_default
        calls["multi"] += 1
        return _multimodal_ok("pong")

    def _fake_generation(**kwargs):
        assert kwargs["api_key"] == provider.api_key
        calls["gen"] += 1
        return _generation_ok("should not be called")

    monkeypatch.setattr("core.ai.providers.dashscope_provider.dashscope.MultiModalConversation.call", _fake_multi)
    monkeypatch.setattr("core.ai.providers.dashscope_provider.dashscope.Generation.call", _fake_generation)

    details = provider.test_connection(model="qwen3.5-plus")

    assert details.get("success") is True
    assert details.get("sample_response") == "pong"
    assert calls["multi"] == 1
    assert calls["gen"] == 0


def test_text_route_failure_does_not_retry_with_multimodal(monkeypatch):
    provider = DashScopeProvider(api_key="sk-test")
    calls = {"multi": 0, "gen": 0}

    def _fake_generation(**kwargs):
        assert kwargs["api_key"] == provider.api_key
        calls["gen"] += 1
        return _Obj(
            status_code=400,
            output=None,
            code="InvalidParameter",
            message="url error, please check url!",
        )

    def _fake_multi(*, model, messages, api_key, max_tokens):
        assert api_key == provider.api_key
        assert max_tokens == provider._max_output_tokens_default
        calls["multi"] += 1
        raise AssertionError("文本路由失败后不应发起第二次多模态请求")

    monkeypatch.setattr("core.ai.providers.dashscope_provider.dashscope.Generation.call", _fake_generation)
    monkeypatch.setattr("core.ai.providers.dashscope_provider.dashscope.MultiModalConversation.call", _fake_multi)

    details = provider.test_connection(model="unknown-model")

    assert details.get("success") is False
    assert details.get("error", {}).get("code") == "InvalidParameter"
    assert details.get("error", {}).get("message") == "url error, please check url!"
    assert calls["gen"] == 1
    assert calls["multi"] == 0


def test_dashscope_validation_requires_explicit_model(monkeypatch):
    provider = DashScopeProvider(api_key="sk-test")
    calls = {"multi": 0, "gen": 0}

    def _fake_generation(**kwargs):
        assert kwargs["api_key"] == provider.api_key
        calls["gen"] += 1
        return _generation_ok("unexpected")

    def _fake_multi(*, model, messages, api_key, max_tokens):
        assert api_key == provider.api_key
        assert max_tokens == provider._max_output_tokens_default
        calls["multi"] += 1
        return _multimodal_ok("unexpected")

    monkeypatch.setattr("core.ai.providers.dashscope_provider.settings.MODEL_NAME", "")
    monkeypatch.setattr("core.ai.providers.dashscope_provider.settings.TURBO_MODEL_NAME", "")
    monkeypatch.setattr("core.ai.providers.dashscope_provider.dashscope.Generation.call", _fake_generation)
    monkeypatch.setattr("core.ai.providers.dashscope_provider.dashscope.MultiModalConversation.call", _fake_multi)

    details = provider.test_connection(model="")

    assert details.get("success") is False
    assert details.get("error", {}).get("message") == "model_name is required"
    assert calls == {"multi": 0, "gen": 0}


def test_provider_construction_does_not_replace_sdk_global_credentials():
    import dashscope

    original_key = dashscope.api_key
    provider = DashScopeProvider(api_key="sk-test")
    assert provider.api_key == "sk-test"
    assert dashscope.api_key == original_key
