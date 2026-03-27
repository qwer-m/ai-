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
