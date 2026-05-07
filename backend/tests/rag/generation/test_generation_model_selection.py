from __future__ import annotations

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
