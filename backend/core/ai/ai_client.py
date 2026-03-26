#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 瀹㈡埛绔棬闈㈠眰銆?

鏂囦欢瀹氫綅锛?
1. 浣嶄簬 core 灞傦紝璐熻矗缁熶竴瀵瑰鏆撮湶 AI 璋冪敤鍏ュ彛銆?
2. 缂栨帓妯″瀷閫夋嫨銆佺紦瀛樿鍐欎笌鐢ㄦ埛閰嶇疆瑁呴厤锛屼笉鐩存帴澶勭悊搴曞眰鍗忚缁嗚妭銆?
3. Provider 鍗忚瀹炵幇鎷嗗垎鍒?`core.ai.ai_providers`锛岄檷浣庡崟鏂囦欢澶嶆潅搴﹀苟淇濇寔琛屼负绛変环銆?
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
    """
    AI 璋冪敤闂ㄩ潰銆?

    鏍稿績鑱岃矗锛?
    1. 缁熶竴绠＄悊 provider 鍒囨崲涓庢ā鍨嬮€夋嫨銆?
    2. 缁存姢 L2/L4 缂撳瓨鍛戒腑涓庡洖濉涔夈€?
    3. 鎻愪緵鏂囨湰銆佹祦寮忋€丱CR銆丷AG 绛夌粺涓€鍏ュ彛锛屽噺灏戜笂灞傛ā鍧楄€﹀悎銆?
    """

    def __init__(self, provider: BaseModelProvider = None):
        self._provider = provider
        self.model = settings.MODEL_NAME
        self.turbo_model = settings.TURBO_MODEL_NAME
        self.vl_model = settings.VL_MODEL_NAME
        self.turbo_provider: BaseModelProvider | None = None
        self.vl_provider: BaseModelProvider | None = None
        self.max_tokens = getattr(settings, "MAX_TOKENS", 2000)

        # 鏈樉寮忎紶鍏?provider 鏃讹紝鎸夌郴缁熼厤缃繘琛屽厹搴曞垵濮嬪寲銆?
        if not self._provider:
            self._init_from_settings()

    def _init_from_settings(self):
        """浠?settings 鎵ц鍏滃簳鍒濆鍖栵紝淇濇寔鍘嗗彶鍚姩琛屼负涓€鑷淬€?"""
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

        # 淇濇寔鍘熸湁妯″瀷浼樺厛绾э細鏈夌嫭绔嬮厤缃垯瑕嗙洊榛樿鍊笺€?
        if config.turbo_model_name:
            client.turbo_model = config.turbo_model_name
        if config.vl_model_name:
            client.vl_model = config.vl_model_name

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
        """杩愯鏈熸洿鏂?provider锛屼繚鎸佺幇鏈夎皟鐢ㄦ柟鐨勫姩鎬佸垏鎹㈣兘鍔涖€?"""
        self._provider = provider
        if model_name:
            self.model = model_name

    def select_model(self, input_text: str, task_type: str = "general") -> str:
        """鎸変换鍔＄被鍨嬮€夋嫨妯″瀷锛屼繚鎸佸巻鍙插垎閰嶇瓥鐣ヤ笉鍙樸€?"""
        if not self._provider:
            return self.model

        # OpenAI 鍏煎鍦烘櫙閫氬父鐢卞崟妯″瀷鎵胯浇锛屼紭鍏堜娇鐢?provider 鍐呴厤缃€?
        if isinstance(self._provider, OpenAICompatibleProvider):
            return self._provider.model

        if task_type in ["compression", "summary"]:
            return self.turbo_model
        if task_type == "ocr":
            return self.vl_model

        # 淇濈暀鈥滈粯璁ゅ皧閲嶇敤鎴烽€夋嫨鈥濈殑琛屼负锛屼笉鍐嶆寜鐭枃鏈嚜鍔ㄩ檷绾?turbo銆?
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
        闈炴祦寮忔枃鏈敓鎴愬叆鍙ｃ€?

        娴佺▼璇存槑锛?
        1. 缁勮 messages銆?
        2. 纭畾鐩爣妯″瀷銆?
        3. 鑻ュ彲鐢ㄥ垯鏌ヨ L4 缂撳瓨銆?
        4. 璋冪敤 provider銆?
        5. 鎴愬姛缁撴灉鍥炲～ L4 缂撳瓨銆?
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
        """娴佸紡鏂囨湰鐢熸垚鍏ュ彛锛屼緵鍓嶇瀹炴椂杈撳嚭娑堣垂銆?"""
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
        OCR/澶氭ā鎬佸叆鍙ｃ€?
        缁存寔 L2 缂撳瓨鍛戒腑涓庡洖濉瓥鐣ワ紝閬垮厤閲嶅鍥惧儚鎺ㄧ悊寮€閿€銆?
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
        """
        RAG 鍏ュ彛銆?
        淇濇寔鈥滄绱㈢墖娈靛悎骞?-> 鍘嬬缉 -> 鏈€缁堥棶绛斺€濋摼璺『搴忎笉鍙樸€?
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
        寮傛鍏煎鍖呰銆?
        褰撳墠淇濇寔涓庡巻鍙蹭竴鑷达細鍐呴儴浠嶈皟鐢ㄥ悓姝ュ疄鐜帮紝閬垮厤 provider 寮傛鍖栧甫鏉ョ殑琛屼负鍙樺寲銆?
        """
        return self.generate_response(
            prompt,
            system_prompt,
            db,
            max_tokens=self.max_tokens,
            task_type=task_type,
            model=model,
        )


# 鍏ㄥ眬榛樿瀹㈡埛绔細鍦ㄦ棤鐢ㄦ埛绾ч厤缃椂浣滀负鍏滃簳瀹炰緥澶嶇敤銆?
ai_client = AIClient()


def get_client_for_user(user_id: int, db: Session) -> AIClient:
    """
    鑾峰彇鐢ㄦ埛绾?AIClient銆?

    璁捐鎰忓浘锛?
    1. 鏀寔澶氱鎴?澶氱敤鎴锋ā鍨嬮厤缃殧绂汇€?
    2. 鑻ユ棤鐢ㄦ埛閰嶇疆锛屽洖閫€鍒板叏灞€榛樿瀹㈡埛绔紝淇濇寔鎺ュ彛灞傝皟鐢ㄧǔ瀹氥€?
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



