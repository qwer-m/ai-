#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 客户端门面层。

文件定位：
1. 位于 core 层，负责统一对外暴露 AI 调用入口。
2. 编排模型选择、缓存读写与用户配置装配，不直接处理底层协议细节。
3. Provider 协议实现拆分到 `core.ai.ai_providers`，降低单文件复杂度并保持行为等价。
"""

import json
from typing import Optional

from sqlalchemy.orm import Session

from core.settings.config import settings
from core.cache_layer.cache import cache_service
from core.db.models import SystemConfig
from core.settings.config_manager import config_manager
from core.ai.ai_providers import (
    BaseModelProvider,
    DashScopeProvider,
    OpenAICompatibleProvider,
    GLMProvider,
    UITARSProvider,
)


class AIClient:
    """
    AI 调用门面。

    核心职责：
    1. 统一管理 provider 切换与模型选择。
    2. 维护 L2/L4 缓存命中与回填语义。
    3. 提供文本、流式、OCR、RAG 等统一入口，减少上层模块耦合。
    """

    def __init__(self, provider: BaseModelProvider = None):
        self._provider = provider
        self.model = settings.MODEL_NAME
        self.turbo_model = settings.TURBO_MODEL_NAME
        self.vl_model = settings.VL_MODEL_NAME
        self.max_tokens = getattr(settings, "MAX_TOKENS", 2000)

        # 未显式传入 provider 时，按系统配置进行兜底初始化。
        if not self._provider:
            self._init_from_settings()

    def _init_from_settings(self):
        """从 settings 执行兜底初始化，保持历史启动行为一致。"""
        if settings.DASHSCOPE_API_KEY:
            self._provider = DashScopeProvider(settings.DASHSCOPE_API_KEY)
            self.model = settings.MODEL_NAME
        else:
            self._provider = None

    @property
    def provider(self):
        return self._provider

    @classmethod
    def from_config(cls, config: SystemConfig):
        """
        从数据库配置创建客户端。
        仅做 provider 装配，不在此处引入业务策略，避免跨层职责混淆。
        """
        if not config:
            return cls()

        provider = None
        decrypted_key = config_manager.get_decrypted_api_key(config)

        if config.provider == "dashscope":
            provider = DashScopeProvider(decrypted_key)
        elif config.provider in ["openai", "ollama", "local"]:
            provider = OpenAICompatibleProvider(
                base_url=config.base_url,
                api_key=decrypted_key,
                model=config.model_name,
            )

        client = cls(provider)
        client.model = config.model_name

        # 保持原有模型优先级：有独立配置则覆盖默认值。
        if config.turbo_model_name:
            client.turbo_model = config.turbo_model_name
        if config.vl_model_name:
            client.vl_model = config.vl_model_name

        return client

    def update_provider(self, provider: BaseModelProvider, model_name: str = None):
        """运行期更新 provider，保持现有调用方的动态切换能力。"""
        self._provider = provider
        if model_name:
            self.model = model_name

    def select_model(self, input_text: str, task_type: str = "general") -> str:
        """按任务类型选择模型，保持历史分配策略不变。"""
        if not self._provider:
            return self.model

        # OpenAI 兼容场景通常由单模型承载，优先使用 provider 内配置。
        if isinstance(self._provider, OpenAICompatibleProvider):
            return self._provider.model

        if task_type in ["compression", "summary"]:
            return self.turbo_model
        if task_type == "ocr":
            return self.vl_model

        # 保留“默认尊重用户选择”的行为，不再按短文本自动降级 turbo。
        return self.model

    def generate_response(
        self,
        user_input: str,
        system_prompt: str = None,
        db: Session = None,
        max_tokens: int = None,
        task_type: str = "general",
        model: str = None,
    ) -> str:
        """
        非流式文本生成入口。

        流程说明：
        1. 组装 messages。
        2. 确定目标模型。
        3. 若可用则查询 L4 缓存。
        4. 调用 provider。
        5. 成功结果回填 L4 缓存。
        """
        if not self.provider:
            return "Error: AI Provider not configured."

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_input})

        target_model = model
        if not target_model:
            target_model = self.select_model((system_prompt or "") + user_input, task_type)

        if db:
            cache_key_content = f"{target_model}:{json.dumps(messages, ensure_ascii=False)}"
            cached = cache_service.get(cache_key_content, "L4", db)
            if cached:
                return cached

        result = self.provider.generate(messages, target_model, max_tokens or self.max_tokens)

        if db and not result.startswith("Error") and not result.startswith("Exception"):
            cache_service.set(cache_key_content, result, "L4", db, metadata={"model": target_model})

        return result

    def generate_response_stream(
        self, user_input: str, system_prompt: str = None, max_tokens: int = None
    ):
        """流式文本生成入口，供前端实时输出消费。"""
        if not self.provider:
            yield "Error: AI Provider not configured."
            return

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_input})

        target_model = self.select_model((system_prompt or "") + user_input)
        yield from self.provider.generate_stream(messages, target_model, max_tokens or self.max_tokens)

    def analyze_image(
        self,
        image_path_or_url: str,
        prompt: str = "OCR: Extract all text from this image.",
        db: Session = None,
        model: str = None,
    ) -> str:
        """
        OCR/多模态入口。
        维持 L2 缓存命中与回填策略，避免重复图像推理开销。
        """
        if not self.provider:
            return "Error: AI Provider not configured."

        cache_key = f"ocr:{prompt}:{image_path_or_url}:{model or 'default'}"
        if db:
            cached = cache_service.get(cache_key, "L2", db)
            if cached:
                return cached

        messages = [
            {
                "role": "user",
                "content": [{"image": image_path_or_url}, {"text": prompt}],
            }
        ]

        if model:
            target_model = model
        else:
            target_model = self.vl_model if isinstance(self.provider, DashScopeProvider) else self.model

        response = self.provider.multimodal_generate(messages, target_model)

        if db and not response.startswith("OCR Error") and not response.startswith("OCR Exception"):
            cache_service.set(cache_key, response, "L2", db, metadata={"type": "ocr", "model": target_model})

        return response

    def compress_context(self, context: str, prompt: str = "Summary:", db: Session = None) -> str:
        """上下文压缩入口，复用文本生成并固定 compression 任务类型。"""
        target_model = self.turbo_model if self.turbo_model else self.model
        return self.generate_response(
            f"{prompt}\n\n{context}",
            "You are a summarization expert.",
            db,
            model=target_model,
            task_type="compression",
        )

    def rag_generate_response(
        self, query: str, retrieved_docs: list[str], system_prompt: str = None, db: Session = None
    ) -> str:
        """
        RAG 入口。
        保持“检索片段合并 -> 压缩 -> 最终问答”链路顺序不变。
        """
        combined_docs = "\n\n".join([f"Doc {i + 1}: {doc}" for i, doc in enumerate(retrieved_docs)])
        compressed_context = self.compress_context(
            combined_docs,
            "Summarize relevant info for query:",
            db,
        )
        final_prompt = f"Query: {query}\n\nContext: {compressed_context}"
        return self.generate_response(final_prompt, system_prompt, db, task_type="rag")

    async def generate_response_async(
        self,
        prompt: str,
        system_prompt: str = None,
        db: Session = None,
        model: str = None,
        task_type: str = "general",
    ) -> str:
        """
        异步兼容包装。
        当前保持与历史一致：内部仍调用同步实现，避免 provider 异步化带来的行为变化。
        """
        return self.generate_response(
            prompt,
            system_prompt,
            db,
            max_tokens=self.max_tokens,
            task_type=task_type,
            model=model,
        )


# 全局默认客户端：在无用户级配置时作为兜底实例复用。
ai_client = AIClient()


def get_client_for_user(user_id: int, db: Session) -> AIClient:
    """
    获取用户级 AIClient。

    设计意图：
    1. 支持多租户/多用户模型配置隔离。
    2. 若无用户配置，回退到全局默认客户端，保持接口层调用稳定。
    """
    if not user_id or not db:
        return ai_client

    user_config = config_manager.get_active_config(db, user_id)
    if user_config:
        return AIClient.from_config(user_config)

    return ai_client


__all__ = [
    "BaseModelProvider",
    "DashScopeProvider",
    "OpenAICompatibleProvider",
    "GLMProvider",
    "UITARSProvider",
    "AIClient",
    "ai_client",
    "get_client_for_user",
]

