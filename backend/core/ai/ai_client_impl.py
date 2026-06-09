"""AI client orchestration helpers.

This module selects providers, handles cache integration, and assembles
per-user model configuration without coupling callers to provider details.
"""
import json
from typing import Any, Optional
from sqlalchemy.orm import Session
from core.settings.config import settings
from core.cache_layer.cache import cache_service
from core.db.models import SystemConfig
from core.authn.security import config_encryption
from core.settings.config_manager import config_manager
from core.ai.ai_providers import (
    BaseModelProvider,
    DashScopeProvider,
    OpenAICompatibleProvider,
    GLMProvider,
    UITARSProvider,
)
class AIClient:
    """Facade over configured model providers, cache, and target-model routing."""
    def __init__(self, provider: BaseModelProvider = None):
        self._provider = provider
        self.model = settings.MODEL_NAME
        self.turbo_model = settings.TURBO_MODEL_NAME
        self.vl_model = settings.VL_MODEL_NAME
        self.review_model = str(getattr(settings, "REVIEW_MODEL_NAME", "") or "").strip()
        self.turbo_provider: BaseModelProvider | None = None
        self.vl_provider: BaseModelProvider | None = None
        self.review_provider: BaseModelProvider | None = None
        self.last_response_metadata: dict[str, Any] = {}
        self.max_tokens = getattr(settings, "MAX_TOKENS", 2000)
        if not self._provider:
            self._init_from_settings()
    def _init_from_settings(self):
        """Initialize the default provider from global settings."""
        if settings.DASHSCOPE_API_KEY:
            self._provider = DashScopeProvider(settings.DASHSCOPE_API_KEY)
            self.model = settings.MODEL_NAME
        else:
            self._provider = None
    @property
    def provider(self):
        return self._provider
    @staticmethod
    def _default_base_url(provider_name: str) -> str | None:
        provider_name = (provider_name or "").strip().lower()
        if provider_name == "openai":
            return "https://api.openai.com/v1"
        if provider_name == "deepseek":
            return "https://api.deepseek.com/v1"
        if provider_name == "local":
            return "http://localhost:11434/v1"
        return None
    @staticmethod
    def _decrypt_metadata_key(raw_value: Any) -> str:
        if not raw_value:
            return ""
        value = str(raw_value)
        if value.startswith("gAAAA"):
            try:
                return config_encryption.decrypt(value)
            except Exception:
                return ""
        return value
    @classmethod
    def _build_provider_by_name(
        cls,
        provider_name: str,
        *,
        api_key: str,
        base_url: str | None,
        model_name: str,
    ) -> BaseModelProvider | None:
        name = (provider_name or "").strip().lower()
        if name == "dashscope":
            return DashScopeProvider(api_key or "")
        if name in {"openai", "deepseek", "ollama", "local"}:
            resolved_base = (base_url or "").strip() or cls._default_base_url(name)
            if not resolved_base:
                return None
            return OpenAICompatibleProvider(
                base_url=resolved_base,
                api_key=api_key or "",
                model=model_name or "",
            )
        return None
    @staticmethod
    def _read_target_meta(config: SystemConfig, target_key: str) -> dict[str, Any]:
        raw = config.metadata_info if isinstance(config.metadata_info, dict) else {}
        if not isinstance(raw, dict):
            return {}
        targets = raw.get("targets")
        node = targets.get(target_key) if isinstance(targets, dict) else raw.get(target_key)
        return node if isinstance(node, dict) else {}
    @classmethod
    def from_config(cls, config: SystemConfig):
        """Build client instance from persisted config."""
        if not config:
            return cls()
        provider = None
        decrypted_key = config_manager.get_decrypted_api_key(config)
        if config.provider == "dashscope":
            provider = DashScopeProvider(decrypted_key)
        elif config.provider in ["openai", "deepseek", "ollama", "local"]:
            provider = OpenAICompatibleProvider(
                base_url=config.base_url,
                api_key=decrypted_key,
                model=config.model_name,
            )
        client = cls(provider)
        client.model = config.model_name
        if config.turbo_model_name:
            client.turbo_model = config.turbo_model_name
        if config.vl_model_name:
            client.vl_model = config.vl_model_name
        review_meta = cls._read_target_meta(config, "review")
        if isinstance(review_meta, dict):
            review_model_name = str(review_meta.get("model_name") or "").strip()
            if review_model_name:
                client.review_model = review_model_name
            if (
                not bool(review_meta.get("follow_main", True))
                and client.review_model
            ):
                review_provider_name = str(review_meta.get("provider") or "").strip().lower()
                review_api_key = cls._decrypt_metadata_key(review_meta.get("api_key"))
                review_base_url = str(review_meta.get("base_url") or "").strip() or None
                review_provider = cls._build_provider_by_name(
                    review_provider_name,
                    api_key=review_api_key,
                    base_url=review_base_url,
                    model_name=client.review_model,
                )
                if review_provider:
                    client.review_provider = review_provider
        turbo_meta = cls._read_target_meta(config, "turbo")
        if (
            isinstance(turbo_meta, dict)
            and not bool(turbo_meta.get("follow_main", True))
            and client.turbo_model
        ):
            turbo_provider_name = str(turbo_meta.get("provider") or "").strip().lower()
            turbo_api_key = cls._decrypt_metadata_key(turbo_meta.get("api_key"))
            turbo_base_url = str(turbo_meta.get("base_url") or "").strip() or None
            turbo_provider = cls._build_provider_by_name(
                turbo_provider_name,
                api_key=turbo_api_key,
                base_url=turbo_base_url,
                model_name=client.turbo_model,
            )
            if turbo_provider:
                client.turbo_provider = turbo_provider
        vision_meta = cls._read_target_meta(config, "vision")
        if (
            isinstance(vision_meta, dict)
            and not bool(vision_meta.get("follow_main", True))
            and client.vl_model
        ):
            vision_provider_name = str(vision_meta.get("provider") or "").strip().lower()
            vision_api_key = cls._decrypt_metadata_key(vision_meta.get("api_key"))
            vision_base_url = str(vision_meta.get("base_url") or "").strip() or None
            vision_provider = cls._build_provider_by_name(
                vision_provider_name,
                api_key=vision_api_key,
                base_url=vision_base_url,
                model_name=client.vl_model,
            )
            if vision_provider:
                client.vl_provider = vision_provider
        return client
    def update_provider(self, provider: BaseModelProvider, model_name: str = None):
        """Replace the active provider and optionally update the model name."""
        self._provider = provider
        if model_name:
            self.model = model_name
    def select_model(self, input_text: str, task_type: str = "general") -> str:
        """Choose the target model for the requested task type."""
        if not self._provider:
            return self.model
        if task_type == "review" and self.review_model:
            return self.review_model
        if task_type in ["compression", "summary"] and self.turbo_model:
            return self.turbo_model
        if task_type == "ocr" and self.vl_model:
            return self.vl_model
        if isinstance(self._provider, OpenAICompatibleProvider):
            return self._provider.model
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
        """Generate a non-streaming model response with cache and metadata handling."""
        if not self.provider:
            return "Error: AI Provider not configured."
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_input})
        target_model = model
        if not target_model:
            target_model = self.select_model((system_prompt or "") + user_input, task_type)
        target_provider = self.provider
        if (
            task_type == "review"
            and self.review_provider
            and (not model or target_model == self.review_model)
        ):
            target_provider = self.review_provider
        elif (
            task_type in {"compression", "summary"}
            and self.turbo_provider
            and (not model or target_model == self.turbo_model)
        ):
            target_provider = self.turbo_provider
        if not target_provider:
            return "Error: AI Provider not configured."
        if db:
            cache_key_content = f"{target_model}:{json.dumps(messages, ensure_ascii=False)}"
            cached = cache_service.get(cache_key_content, "L4", db)
            if cached:
                self.last_response_metadata = {
                    "model": target_model,
                    "cached": True,
                    "input_tokens_estimated": max(1, len(json.dumps(messages, ensure_ascii=False)) // 4),
                    "output_tokens_estimated": max(0, len(str(cached or "")) // 4),
                    "token_estimate_method": "chars_div_4",
                }
                return cached
        result = target_provider.generate(messages, target_model, max_tokens or self.max_tokens)
        self.last_response_metadata = dict(getattr(target_provider, "last_response_metadata", {}) or {})
        self.last_response_metadata.setdefault("model", target_model)
        self.last_response_metadata.setdefault("input_tokens_estimated", max(1, len(json.dumps(messages, ensure_ascii=False)) // 4))
        self.last_response_metadata.setdefault("output_tokens_estimated", max(0, len(str(result or "")) // 4))
        self.last_response_metadata.setdefault("token_estimate_method", "chars_div_4")
        if not str(result or "").strip():
            return f"Error: Empty response from model {target_model}"
        if db and not result.startswith("Error") and not result.startswith("Exception"):
            cache_service.set(cache_key_content, result, "L4", db, metadata={"model": target_model})
        return result
    def generate_response_stream(
        self,
        user_input: str,
        system_prompt: str = None,
        max_tokens: int = None,
        task_type: str = "general",
        model: str = None,
    ):
        """Generate a streaming model response and collect response metadata."""
        if not self.provider:
            yield "Error: AI Provider not configured."
            return
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_input})
        target_model = model or self.select_model((system_prompt or "") + user_input, task_type)
        output_parts = []
        for chunk in self.provider.generate_stream(messages, target_model, max_tokens or self.max_tokens):
            output_parts.append(str(chunk or ""))
            yield chunk
        output_text = "".join(output_parts)
        self.last_response_metadata = dict(getattr(self.provider, "last_response_metadata", {}) or {})
        self.last_response_metadata.setdefault("model", target_model)
        self.last_response_metadata.setdefault("input_tokens_estimated", max(1, len(json.dumps(messages, ensure_ascii=False)) // 4))
        self.last_response_metadata.setdefault("output_tokens_estimated", max(0, len(output_text or "") // 4))
        self.last_response_metadata.setdefault("token_estimate_method", "chars_div_4")
    def analyze_image(
        self,
        image_path_or_url: str,
        prompt: str = "OCR: Extract all text from this image.",
        db: Session = None,
        model: str = None,
    ) -> str:
        """Run OCR or multimodal analysis with optional L2 cache reuse."""
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
        target_provider = self.provider
        if model:
            target_model = model
            if self.vl_provider and model == self.vl_model:
                target_provider = self.vl_provider
        elif self.vl_provider and self.vl_model:
            target_model = self.vl_model
            target_provider = self.vl_provider
        else:
            target_model = self.vl_model if isinstance(self.provider, DashScopeProvider) else self.model
        if not target_provider:
            return "Error: AI Provider not configured."
        response = target_provider.multimodal_generate(messages, target_model)
        if db and not response.startswith("OCR Error") and not response.startswith("OCR Exception"):
            cache_service.set(cache_key, response, "L2", db, metadata={"type": "ocr", "model": target_model})
        return response
    def compress_context(self, context: str, prompt: str = "Summary:", db: Session = None) -> str:
        """Context compression entrypoint."""
        target_model = self.turbo_model if self.turbo_model else self.model
        if self.turbo_provider:
            messages = [
                {"role": "system", "content": "You are a summarization expert."},
                {"role": "user", "content": f"{prompt}\n\n{context}"},
            ]
            cache_key_content = f"{target_model}:{json.dumps(messages, ensure_ascii=False)}"
            if db:
                cached = cache_service.get(cache_key_content, "L4", db)
                if cached:
                    return cached
            result = self.turbo_provider.generate(messages, target_model, self.max_tokens)
            if (
                str(result or "").startswith(("Error", "Exception"))
                and self.provider
                and self.provider is not self.turbo_provider
            ):
                fallback_model = self.model
                fallback_result = self.provider.generate(messages, fallback_model, self.max_tokens)
                if fallback_result and not str(fallback_result).startswith(("Error", "Exception")):
                    if db:
                        cache_service.set(
                            cache_key_content,
                            fallback_result,
                            "L4",
                            db,
                            metadata={
                                "model": fallback_model,
                                "task_type": "compression",
                                "fallback_from": target_model,
                            },
                        )
                    return fallback_result
            if db and not result.startswith("Error") and not result.startswith("Exception"):
                cache_service.set(
                    cache_key_content,
                    result,
                    "L4",
                    db,
                    metadata={"model": target_model, "task_type": "compression"},
                )
            return result
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
        """Compress retrieved documents and generate an answer for a RAG query."""
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
        """Async-compatible wrapper around the synchronous response path."""
        return self.generate_response(
            prompt,
            system_prompt,
            db,
            max_tokens=self.max_tokens,
            task_type=task_type,
            model=model,
        )
ai_client = AIClient()
def get_client_for_user(user_id: int, db: Session) -> AIClient:
    """Return the user-specific AI client, falling back to the global client."""
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
