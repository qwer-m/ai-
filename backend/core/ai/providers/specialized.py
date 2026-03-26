from __future__ import annotations

from core.ai.providers.openai_compatible_provider import OpenAICompatibleProvider


class GLMProvider(OpenAICompatibleProvider):
    def __init__(self, api_key: str, model: str = "glm-4v"):
        base_url = "https://open.bigmodel.cn/api/paas/v4/"
        super().__init__(base_url, api_key, model)


class UITARSProvider(OpenAICompatibleProvider):
    def __init__(self, base_url: str, api_key: str, model: str = "uitars-7b"):
        super().__init__(base_url, api_key, model)
