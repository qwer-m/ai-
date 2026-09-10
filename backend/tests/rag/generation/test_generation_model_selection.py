from __future__ import annotations

import json

from core.ai.ai_client import AIClient


class _DummyProvider:
    """中文注释：记录调用模型，避免真实网络请求。"""

    def __init__(self) -> None:
        self.last_generate_model = ""
        self.last_stream_model = ""

    def generate(self, messages, model, max_tokens):
        self.last_generate_model = str(model or "")
        return "[]"

    def generate_stream(self, messages, model, max_tokens):
        self.last_stream_model = str(model or "")
        yield "[]"

    def multimodal_generate(self, messages, model):
        self.last_multimodal_model = str(model or "")
        return "OCR OK"


class _EmptyProvider(_DummyProvider):
    def generate(self, messages, model, max_tokens):
        self.last_generate_model = str(model or "")
        return ""


class _FailingProvider(_DummyProvider):
    def generate(self, messages, model, max_tokens):
        self.last_generate_model = str(model or "")
        return "Error: InvalidParameter - url error, please check url"


def test_generation_task_uses_main_model_without_auto_fallback():
    provider = _DummyProvider()
    client = AIClient(provider=provider)
    client.model = "tongyi-xiaomi-analysis-pro"
    client.turbo_model = "qwen-plus"

    _ = client.generate_response("req", "sys", db=None, task_type="generation")
    assert provider.last_generate_model == "tongyi-xiaomi-analysis-pro"

    list(client.generate_response_stream("req", "sys", task_type="generation"))
    assert provider.last_stream_model == "tongyi-xiaomi-analysis-pro"


def test_compression_task_still_uses_turbo_model():
    provider = _DummyProvider()
    client = AIClient(provider=provider)
    client.model = "tongyi-main"
    client.turbo_model = "tongyi-turbo"

    _ = client.generate_response("req", "sys", db=None, task_type="compression")
    assert provider.last_generate_model == "tongyi-turbo"


def test_review_task_uses_review_model():
    provider = _DummyProvider()
    client = AIClient(provider=provider)
    client.model = "deepseek-v4-flash"
    client.review_model = "deepseek-chat"

    _ = client.generate_response("req", "sys", db=None, task_type="review")
    assert provider.last_generate_model == "deepseek-chat"


def test_review_task_uses_independent_review_provider():
    main_provider = _DummyProvider()
    review_provider = _DummyProvider()
    client = AIClient(provider=main_provider)
    client.model = "main-model"
    client.review_model = "review-model"
    client.review_provider = review_provider

    _ = client.generate_response("req", "sys", db=None, task_type="review")

    assert main_provider.last_generate_model == ""
    assert review_provider.last_generate_model == "review-model"


def test_empty_provider_response_is_explicit_error_and_not_success():
    provider = _EmptyProvider()
    client = AIClient(provider=provider)
    client.model = "deepseek-v4-flash"

    result = client.generate_response("req", "sys", db=None, task_type="generation")

    assert result == "Error: Empty response from model deepseek-v4-flash"


def test_json_shaped_cache_hit_preserves_generate_response_text_contract(monkeypatch):
    provider = _DummyProvider()
    client = AIClient(provider=provider)
    client.model = "glm-5.1"
    cached_payload = {
        "semantic_contract_version": "requirement-semantic-v1",
        "workflow_blueprints": [],
    }
    cached_value = client._build_l4_cache_value(
        json.dumps(cached_payload, ensure_ascii=False),
        {
            "finish_reason": "stop",
            "response_status": "completed",
        },
    )
    monkeypatch.setattr(
        "core.ai.ai_client.cache_service.get",
        lambda *args, **kwargs: cached_value,
    )

    result = client.generate_response(
        "真实需求正文",
        "编译需求语义",
        db=object(),
        task_type="generation",
    )

    assert isinstance(result, str)
    assert json.loads(result) == cached_payload
    assert client.last_response_metadata["cached"] is True
    assert provider.last_generate_model == ""


def test_empty_json_array_cache_hit_is_not_treated_as_cache_miss(monkeypatch):
    provider = _DummyProvider()
    client = AIClient(provider=provider)
    client.model = "glm-5.1"
    cached_value = client._build_l4_cache_value(
        "[]",
        {
            "finish_reason": "stop",
            "response_status": "completed",
        },
    )
    monkeypatch.setattr(
        "core.ai.ai_client.cache_service.get",
        lambda *args, **kwargs: cached_value,
    )

    result = client.generate_response(
        "真实需求正文",
        "编译需求语义",
        db=object(),
        task_type="generation",
    )

    assert result == "[]"
    assert client.last_response_metadata["cached"] is True
    assert provider.last_generate_model == ""


def test_compression_falls_back_to_main_provider_when_turbo_provider_fails():
    main_provider = _DummyProvider()
    turbo_provider = _FailingProvider()
    client = AIClient(provider=main_provider)
    client.model = "deepseek-v4-flash"
    client.turbo_model = "kimi-k2.5"
    client.turbo_provider = turbo_provider

    result = client.compress_context("long requirement", prompt="summary", db=None)

    assert result == "[]"
    assert turbo_provider.last_generate_model == "kimi-k2.5"
    assert main_provider.last_generate_model == "deepseek-v4-flash"


def test_image_analysis_uses_vl_model_on_main_provider():
    provider = _DummyProvider()
    client = AIClient(provider=provider)
    client.model = "glm-5.1"
    client.vl_model = "doubao-seed-2-0-pro-260215"

    result = client.analyze_image("file://probe.png", prompt="OCR", db=None)

    assert result == "OCR OK"
    assert provider.last_multimodal_model == "doubao-seed-2-0-pro-260215"
