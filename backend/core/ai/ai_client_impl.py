"""AI client orchestration helpers.

This module selects providers, handles cache integration, and assembles
per-user model configuration without coupling callers to provider details.
"""
import hashlib
import json
from collections.abc import Mapping
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

    _TASK_RESPONSE_MODES = {
        "generation": "json",
        "ui_automation": "text",
    }

    _L4_CACHE_KEY_CONTRACT = "ai-client-text-response-v2"
    _L4_CACHE_VALUE_CONTRACT = "ai-client-text-response-v2"
    _PROVIDER_CACHE_NAMESPACE_ATTR = "_ai_client_cache_namespace_v1"
    _EXPLICIT_COMPLETE_FINISH_REASONS = frozenset(
        {"stop", "tool_calls", "function_call"}
    )
    _L4_CACHE_METADATA_KEYS = (
        "wire_api",
        "http_status",
        "request_timeout_seconds",
        "request_timeout_source",
        "finish_reason",
        "response_status",
        "incomplete_reason",
        "max_tokens",
        "response_mode",
        "json_response",
        "response_format",
        "json_compat_fallback",
        "reasoning_effort",
        "thinking",
        "content_len",
        "reasoning_len",
    )
    _L4_CACHE_METADATA_MAPPING_KEYS = {
        "response_format": ("type",),
        "thinking": ("type",),
    }

    @classmethod
    def _cached_text_response(cls, value: Any) -> str:
        """缓存层会解析 JSON 外形的字符串，AI 客户端边界统一恢复为文本。"""
        if (
            isinstance(value, Mapping)
            and value.get("cache_contract") == cls._L4_CACHE_VALUE_CONTRACT
            and "text" in value
        ):
            value = value.get("text")
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _identity_sha256(value: Mapping[str, Any]) -> str:
        serialized = json.dumps(
            dict(value),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @classmethod
    def _default_cache_namespace(cls) -> str:
        return cls._identity_sha256(
            {
                "namespace_contract": "ai-client-runtime-default-v1",
            }
        )

    @classmethod
    def _config_cache_namespace(cls, config: SystemConfig) -> str:
        """仅使用配置归属与版本字段构造脱敏命名空间。"""
        return cls._identity_sha256(
            {
                "namespace_contract": "system-config-v1",
                "user_id": getattr(config, "user_id", None),
                "config_id": getattr(config, "id", None),
                "config_version": getattr(config, "version", None),
            }
        )

    @classmethod
    def _provider_cache_identity(cls, provider: BaseModelProvider | None) -> str:
        """只将 provider 类型与公开路由信息的摘要纳入缓存键。"""
        provider_class = (
            f"{type(provider).__module__}.{type(provider).__qualname__}"
            if provider is not None
            else ""
        )
        public_base_url = str(
            getattr(provider, "base_url", "") or ""
        ).strip().rstrip("/")
        wire_api = str(getattr(provider, "wire_api", "") or "").strip()
        return cls._identity_sha256(
            {
                "identity_contract": "provider-route-v1",
                "provider_class": provider_class,
                "base_url": public_base_url,
                "wire_api": wire_api,
            }
        )

    @classmethod
    def _provider_cache_namespace(
        cls,
        provider: BaseModelProvider | None,
    ) -> str:
        value = str(
            getattr(provider, cls._PROVIDER_CACHE_NAMESPACE_ATTR, "") or ""
        ).strip()
        return value

    @classmethod
    def _attach_provider_cache_namespace(
        cls,
        provider: BaseModelProvider | None,
        namespace: str,
    ) -> None:
        if provider is None:
            return
        try:
            setattr(provider, cls._PROVIDER_CACHE_NAMESPACE_ATTR, namespace)
        except (AttributeError, TypeError):
            return

    def _build_l4_cache_key(
        self,
        *,
        messages: list[dict[str, Any]],
        target_model: str,
        target_provider: BaseModelProvider,
        max_tokens: Any,
        task_type: str,
        response_mode: str,
        reasoning_effort: str | None,
        disable_thinking: bool,
    ) -> str:
        """构造不含原始 prompt 的 L4 缓存键，并隔离旧版缓存契约。"""
        serialized_messages = json.dumps(
            messages,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        request_contract = {
            "contract": self._L4_CACHE_KEY_CONTRACT,
            "prompt_sha256": hashlib.sha256(
                serialized_messages.encode("utf-8")
            ).hexdigest(),
            "cache_namespace_sha256": self._cache_namespace,
            "provider_identity_sha256": self._provider_cache_identity(
                target_provider
            ),
            "model": str(target_model or ""),
            "max_tokens": max_tokens,
            "task_type": str(task_type or "general"),
            "response_mode": str(response_mode or "auto"),
            "reasoning_effort": str(reasoning_effort or "").strip(),
            "disable_thinking": bool(disable_thinking),
        }
        return (
            f"{self._L4_CACHE_KEY_CONTRACT}:"
            + json.dumps(
                request_contract,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        )

    @classmethod
    def _cacheable_response_metadata(
        cls,
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """只保留恢复响应终止语义所需的小型、标量元数据。"""
        source = metadata if isinstance(metadata, Mapping) else {}
        sanitized: dict[str, Any] = {}
        for key in cls._L4_CACHE_METADATA_KEYS:
            if key not in source:
                continue
            value = source.get(key)
            allowed_mapping_keys = cls._L4_CACHE_METADATA_MAPPING_KEYS.get(key)
            if allowed_mapping_keys and isinstance(value, Mapping):
                compact_mapping: dict[str, Any] = {}
                for mapping_key in allowed_mapping_keys:
                    mapping_value = value.get(mapping_key)
                    if not isinstance(
                        mapping_value,
                        (str, int, float, bool),
                    ):
                        continue
                    if isinstance(mapping_value, str):
                        mapping_value = mapping_value[:64]
                    compact_mapping[mapping_key] = mapping_value
                if compact_mapping:
                    sanitized[key] = compact_mapping
                continue
            if value is None or not isinstance(
                value,
                (str, int, float, bool),
            ):
                continue
            if isinstance(value, str):
                value = value[:128]
            sanitized[key] = value
        return sanitized

    @classmethod
    def _build_l4_cache_value(
        cls,
        text: str,
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "cache_contract": cls._L4_CACHE_VALUE_CONTRACT,
            "text": text,
            "response_metadata": cls._cacheable_response_metadata(metadata),
        }

    @staticmethod
    def _is_incomplete_response(metadata: Mapping[str, Any] | None) -> bool:
        source = metadata if isinstance(metadata, Mapping) else {}
        finish_reason = str(source.get("finish_reason") or "").strip().lower()
        response_status = str(source.get("response_status") or "").strip().lower()
        return finish_reason == "length" or response_status == "incomplete"

    @classmethod
    def _is_explicitly_complete_response(
        cls,
        metadata: Mapping[str, Any] | None,
    ) -> bool:
        source = metadata if isinstance(metadata, Mapping) else {}
        if cls._is_incomplete_response(source):
            return False
        response_status = str(
            source.get("response_status") or ""
        ).strip().lower()
        finish_reason = str(
            source.get("finish_reason") or ""
        ).strip().lower()
        return (
            response_status == "completed"
            or finish_reason in cls._EXPLICIT_COMPLETE_FINISH_REASONS
        )

    @classmethod
    def _is_cacheable_text_response(
        cls,
        text: str,
        metadata: Mapping[str, Any] | None,
        *,
        expected_response_mode: str | None = None,
    ) -> bool:
        """只缓存终止完整且满足响应模式语法的文本。"""

        if not str(text or "").strip():
            return False
        if not cls._is_explicitly_complete_response(metadata):
            return False
        source = metadata if isinstance(metadata, Mapping) else {}
        response_mode = str(
            expected_response_mode or source.get("response_mode") or ""
        ).strip().lower()
        requires_json = response_mode == "json" or (
            response_mode == "auto" and source.get("json_response") is True
        )
        if requires_json:
            try:
                json.loads(
                    text,
                    parse_constant=cls._reject_non_finite_json_constant,
                )
            except (TypeError, ValueError):
                return False
        return True

    @staticmethod
    def _reject_non_finite_json_constant(value: str) -> None:
        """RFC 8259 不允许 NaN 或无穷数值。"""

        raise ValueError(f"JSON 常量非法: {value}")

    @classmethod
    def _read_l4_cache_value(
        cls,
        value: Any,
        *,
        expected_response_mode: str | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """只接受新版 wrapper，避免旧缓存的终止元数据缺失。"""
        if not isinstance(value, Mapping):
            return None
        if value.get("cache_contract") != cls._L4_CACHE_VALUE_CONTRACT:
            return None
        raw_metadata = value.get("response_metadata")
        if not isinstance(raw_metadata, Mapping):
            return None
        text = cls._cached_text_response(value)
        metadata = cls._cacheable_response_metadata(raw_metadata)
        if not cls._is_cacheable_text_response(
            text,
            metadata,
            expected_response_mode=expected_response_mode,
        ):
            return None
        return text, metadata

    def __init__(self, provider: BaseModelProvider = None, *, init_from_settings: bool = True):
        self._provider = provider
        self._cache_namespace = (
            self._provider_cache_namespace(provider)
            or self._default_cache_namespace()
        )
        self.model = settings.MODEL_NAME
        self.turbo_model = settings.TURBO_MODEL_NAME
        self.vl_model = settings.VL_MODEL_NAME
        self.review_model = str(getattr(settings, "REVIEW_MODEL_NAME", "") or "").strip()
        self.turbo_provider: BaseModelProvider | None = None
        self.vl_provider: BaseModelProvider | None = None
        self.review_provider: BaseModelProvider | None = None
        self.last_response_metadata: dict[str, Any] = {}
        self.max_tokens = getattr(settings, "MAX_TOKENS", 2000)
        if init_from_settings and not self._provider:
            self._init_from_settings()
        self._set_cache_namespace(self._cache_namespace)

    def _set_cache_namespace(self, namespace: str) -> None:
        resolved_namespace = str(namespace or "").strip()
        self._cache_namespace = (
            resolved_namespace or self._default_cache_namespace()
        )
        for provider in (
            self._provider,
            self.turbo_provider,
            self.vl_provider,
            self.review_provider,
        ):
            self._attach_provider_cache_namespace(
                provider,
                self._cache_namespace,
            )

    @classmethod
    def unconfigured(cls) -> "AIClient":
        """Build a client that exposes missing persisted configuration instead of using defaults."""
        return cls(provider=None, init_from_settings=False)
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
        client.turbo_model = config.turbo_model_name or ""
        client.vl_model = config.vl_model_name or ""
        client.review_model = ""
        review_meta = cls._read_target_meta(config, "review")
        if isinstance(review_meta, dict):
            review_follow_main = bool(review_meta.get("follow_main", True))
            review_model_name = str(review_meta.get("model_name") or "").strip()
            if not review_follow_main and review_model_name:
                client.review_model = review_model_name
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
        client._set_cache_namespace(cls._config_cache_namespace(config))
        return client
    def update_provider(self, provider: BaseModelProvider, model_name: str = None):
        """Replace the active provider and optionally update the model name."""
        incoming_namespace = self._provider_cache_namespace(provider)
        self._provider = provider
        self._set_cache_namespace(
            incoming_namespace or self._cache_namespace
        )
        if model_name:
            self.model = model_name

    def replace_runtime_from(self, other: "AIClient") -> None:
        """Replace all runtime model routes from another configured client."""
        self._provider = other.provider
        self.model = other.model
        self.turbo_model = other.turbo_model
        self.vl_model = other.vl_model
        self.review_model = other.review_model
        self.turbo_provider = other.turbo_provider
        self.vl_provider = other.vl_provider
        self.review_provider = other.review_provider
        self._set_cache_namespace(other._cache_namespace)
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
        response_mode: str | None = None,
        request_timeout_seconds: float | None = None,
        reasoning_effort: str | None = None,
        disable_thinking: bool = False,
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
        resolved_response_mode = str(
            response_mode or self._TASK_RESPONSE_MODES.get(task_type, "auto")
        ).strip().lower()
        if resolved_response_mode not in {"auto", "json", "text"}:
            raise ValueError(f"Unsupported AI response mode: {resolved_response_mode}")
        resolved_max_tokens = max_tokens or self.max_tokens
        serialized_messages = json.dumps(messages, ensure_ascii=False)
        if db:
            cache_key_content = self._build_l4_cache_key(
                messages=messages,
                target_model=target_model,
                target_provider=target_provider,
                max_tokens=resolved_max_tokens,
                task_type=task_type,
                response_mode=resolved_response_mode,
                reasoning_effort=reasoning_effort,
                disable_thinking=disable_thinking,
            )
            cached = cache_service.get(cache_key_content, "L4", db)
            if cached is not None:
                cached_response = self._read_l4_cache_value(
                    cached,
                    expected_response_mode=resolved_response_mode,
                )
                if cached_response is not None:
                    cached_text, cached_metadata = cached_response
                    self.last_response_metadata = {
                        **cached_metadata,
                        "model": target_model,
                        "cached": True,
                        "response_mode": resolved_response_mode,
                        "input_tokens_estimated": max(
                            1,
                            len(serialized_messages) // 4,
                        ),
                        "output_tokens_estimated": max(
                            0,
                            len(cached_text) // 4,
                        ),
                        "token_estimate_method": "chars_div_4",
                    }
                    return cached_text
        if isinstance(target_provider, OpenAICompatibleProvider):
            result = target_provider.generate(
                messages,
                target_model,
                resolved_max_tokens,
                response_mode=resolved_response_mode,
                request_timeout_seconds=request_timeout_seconds,
                reasoning_effort=reasoning_effort,
                disable_thinking=disable_thinking,
            )
        else:
            result = target_provider.generate(messages, target_model, resolved_max_tokens)
        self.last_response_metadata = dict(getattr(target_provider, "last_response_metadata", {}) or {})
        self.last_response_metadata.setdefault("model", target_model)
        # 当前调用参数是响应模式的唯一事实源，不能被 provider 元数据降级。
        self.last_response_metadata["response_mode"] = resolved_response_mode
        self.last_response_metadata.setdefault("input_tokens_estimated", max(1, len(serialized_messages) // 4))
        self.last_response_metadata.setdefault("output_tokens_estimated", max(0, len(str(result or "")) // 4))
        self.last_response_metadata.setdefault("token_estimate_method", "chars_div_4")
        if not str(result or "").strip():
            return f"Error: Empty response from model {target_model}"
        result_text = str(result)
        if (
            db
            and not result_text.startswith("Error")
            and not result_text.startswith("Exception")
            and self._is_cacheable_text_response(
                result_text,
                self.last_response_metadata,
                expected_response_mode=resolved_response_mode,
            )
        ):
            cache_service.set(
                cache_key_content,
                self._build_l4_cache_value(
                    result_text,
                    self.last_response_metadata,
                ),
                "L4",
                db,
                metadata={
                    "model": target_model,
                    "cache_contract": self._L4_CACHE_VALUE_CONTRACT,
                },
            )
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
            target_model = self.vl_model or self.model
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
    """Return the user-specific AI client; do not substitute global defaults for users."""
    if not user_id or not db:
        return ai_client
    user_config = config_manager.get_active_config(db, user_id)
    if user_config:
        return AIClient.from_config(user_config)
    return AIClient.unconfigured()
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
