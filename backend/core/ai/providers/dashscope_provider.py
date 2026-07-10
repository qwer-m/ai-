from __future__ import annotations

import base64
import mimetypes
import os
import time
from http import HTTPStatus
from typing import Any, Dict, List, Optional

import dashscope

from core.settings.config import settings
from core.ai.providers.base import BaseModelProvider


class DashScopeProvider(BaseModelProvider):
    def __init__(self, api_key: str):
        self.api_key = api_key
        dashscope.api_key = api_key
        dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"
        self._max_output_tokens_default = 4096

    def _is_multimodal_model(self, model: str) -> bool:
        normalized = str(model or "").strip().lower()
        if not normalized:
            return False
        # Qwen3.5 series uses DashScope multimodal API even for text-only requests.
        if normalized.startswith("qwen3.5"):
            return True
        # Broad multimodal model naming coverage.
        return any(token in normalized for token in ("-vl", "_vl", "vision", "multimodal", "omni"))

    def _normalize_multimodal_image(self, image_value: str) -> str:
        value = str(image_value or "")
        if value.startswith("file://"):
            local_path = value[7:]
            try:
                with open(local_path, "rb") as f:
                    image_bytes = f.read()
                mime_type, _ = mimetypes.guess_type(local_path)
                mime_type = mime_type or "image/png"
                base64_image = base64.b64encode(image_bytes).decode("utf-8")
                value = f"data:{mime_type};base64,{base64_image}"
            except Exception as e:
                raise RuntimeError(f"Error reading image: {e}") from e
        return value

    def _normalize_multimodal_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for msg in messages:
            role = str(msg.get("role") or "user")
            raw_content = msg.get("content")
            content_items = raw_content if isinstance(raw_content, list) else [{"text": str(raw_content or "")}]

            new_content: List[Dict[str, Any]] = []
            for item in content_items:
                if not isinstance(item, dict):
                    if item is not None:
                        new_content.append({"text": str(item)})
                    continue
                if "image" in item:
                    image_value = self._normalize_multimodal_image(str(item.get("image") or ""))
                    new_content.append({"image": image_value})
                    continue

                if "text" in item:
                    new_content.append({"text": str(item.get("text") or "")})
                    continue

                item_type = str(item.get("type") or "").lower()
                if item_type == "text":
                    new_content.append({"text": str(item.get("text") or "")})
                    continue
                if item_type == "image_url":
                    image_url = item.get("image_url")
                    if isinstance(image_url, dict):
                        image_value = str(image_url.get("url") or "")
                    else:
                        image_value = str(image_url or "")
                    if image_value:
                        new_content.append({"image": self._normalize_multimodal_image(image_value)})

            if not new_content:
                new_content = [{"text": ""}]

            normalized.append({"role": role, "content": new_content})
        return normalized

    def _extract_multimodal_text(self, response: Any) -> Optional[str]:
        try:
            choices = response.output.choices if response.output else None
            if not choices:
                return None
            message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("text"):
                        return str(item["text"])
            if isinstance(content, str):
                return content
            return None
        except Exception:
            return None

    def _multimodal_call(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        *,
        error_prefix: str = "Error",
        exception_prefix: str = "Exception occurred",
    ) -> str:
        try:
            normalized_messages = self._normalize_multimodal_messages(messages)
            response = dashscope.MultiModalConversation.call(model=model, messages=normalized_messages)

            if response.status_code == HTTPStatus.OK:
                text = self._extract_multimodal_text(response)
                return text or ""

            if response.code in [
                "Arrearage",
                "QuotaExhausted",
                "PaymentRequired",
                "AllocationQuota.FreeTierOnly",
            ]:
                return self._format_quota_message(model)
            return f"{error_prefix}: {response.code} - {response.message}"
        except Exception as e:
            return f"{exception_prefix}: {str(e)}"

    def _is_failure_text_output(self, text: str) -> bool:
        value = str(text or "").strip().lower()
        if not value:
            return True
        if value.startswith("error") or value.startswith("exception"):
            return True
        return value.startswith("[额度耗尽]")

    def _is_quota_or_payment_code(self, code: str) -> bool:
        return code in [
            "Arrearage",
            "QuotaExhausted",
            "PaymentRequired",
            "AllocationQuota.FreeTierOnly",
        ]

    def _format_quota_message(self, model: str) -> str:
        return f"[额度耗尽] 模型 {model} 的免费额度已用完，请在控制台关闭'仅使用免费额度'模式或充值。"

    def test_connection_text_then_multimodal(self, model: str) -> Dict[str, Any]:
        """
        Validation path for the text-model input box:
        first try text API, and only if that fails, try multimodal API with text-only content.
        """
        start_time = time.time()
        target_model = (model or "").strip() or settings.MODEL_NAME
        if not target_model:
            latency = (time.time() - start_time) * 1000
            return {
                "success": False,
                "error": {"message": "model_name is required"},
                "latency": round(latency, 2),
            }

        try:
            response = dashscope.Generation.call(
                model=target_model,
                messages=[{"role": "user", "content": "hi"}],
                result_format="message",
                max_tokens=5,
            )
            if response.status_code == HTTPStatus.OK:
                latency = (time.time() - start_time) * 1000
                return {
                    "success": True,
                    "latency": round(latency, 2),
                    "model_info": {"model": target_model, "mode": "text"},
                    "sample_response": response.output.choices[0]["message"]["content"],
                }

            if self._is_quota_or_payment_code(str(response.code or "")):
                latency = (time.time() - start_time) * 1000
                return {
                    "success": False,
                    "error": {"message": self._format_quota_message(target_model), "code": response.code},
                    "latency": round(latency, 2),
                    "model_info": {"model": target_model, "mode": "text"},
                }

            first_error = f"Error: {response.code} - {response.message}"
        except Exception as e:
            first_error = f"Exception occurred: {str(e)}"

        second_result = self._multimodal_call(
            [{"role": "user", "content": [{"text": "hi"}]}],
            target_model,
        )
        latency = (time.time() - start_time) * 1000
        if not self._is_failure_text_output(second_result):
            return {
                "success": True,
                "latency": round(latency, 2),
                "model_info": {"model": target_model, "mode": "multimodal_text"},
                "sample_response": second_result,
                "detection": {"fallback_from_text": True, "text_error": first_error},
            }

        return {
            "success": False,
            "error": {"message": first_error},
            "latency": round(latency, 2),
            "model_info": {"model": target_model, "mode": "text_then_multimodal"},
            "detection": {"fallback_from_text": True, "multimodal_error": second_result},
        }

    def _clamp_max_tokens(self, model: str, max_tokens: Optional[int]) -> Optional[int]:
        _ = model
        if not max_tokens:
            return None
        try:
            max_tokens_i = int(max_tokens)
        except Exception:
            return None
        if max_tokens_i <= 0:
            return None
        return min(max_tokens_i, self._max_output_tokens_default)

    def _resolve_temperature(self) -> Optional[float]:
        raw = os.getenv("AI_TEMPERATURE", "").strip()
        if raw == "":
            return None
        try:
            value = float(raw)
        except Exception:
            return None
        if value < 0:
            return 0.0
        if value > 2:
            return 2.0
        return value

    def generate(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None) -> str:
        try:
            if self._is_multimodal_model(model):
                return self._multimodal_call(messages, model)

            max_tokens = self._clamp_max_tokens(model, max_tokens)
            temperature = self._resolve_temperature()
            kwargs = {
                "model": model,
                "messages": messages,
                "result_format": "message",
            }
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            if temperature is not None:
                kwargs["temperature"] = temperature

            response = dashscope.Generation.call(**kwargs)

            if response.status_code == HTTPStatus.OK:
                return response.output.choices[0]["message"]["content"]

            if response.code == "InvalidParameter" and "stream mode" in str(response.message):
                try:
                    full_text = ""
                    for chunk in self.generate_stream(messages, model, max_tokens):
                        if (
                            chunk.startswith("Error:")
                            or chunk.startswith("Exception")
                            or chunk.startswith("[额度耗尽]")
                        ):
                            return chunk
                        full_text += chunk
                    return full_text
                except Exception as e:
                    return f"Exception during stream fallback: {str(e)}"

            if response.code == "DataInspectionFailed":
                return f"Error: Content blocked by safety filter. {response.message}"
            if response.code in [
                "Arrearage",
                "QuotaExhausted",
                "PaymentRequired",
                "AllocationQuota.FreeTierOnly",
            ]:
                return self._format_quota_message(model)
            if response.code == "InvalidParameter":
                return f"Error: InvalidParameter - {response.message}（建议降低MAX_TOKENS / 启用压缩 / 减少知识库上下文）"
            return f"Error: {response.code} - {response.message}"
        except Exception as e:
            return f"Exception occurred: {str(e)}"

    def generate_stream(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None):
        try:
            if self._is_multimodal_model(model):
                yield self._multimodal_call(messages, model)
                return

            max_tokens = self._clamp_max_tokens(model, max_tokens)
            temperature = self._resolve_temperature()
            kwargs = {
                "model": model,
                "messages": messages,
                "result_format": "message",
                "stream": True,
                "incremental_output": True,
            }
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            if temperature is not None:
                kwargs["temperature"] = temperature

            responses = dashscope.Generation.call(**kwargs)

            for response in responses:
                if response.status_code == HTTPStatus.OK:
                    content = None
                    try:
                        choice0 = response.output.choices[0] if response.output and response.output.choices else None
                        if choice0:
                            try:
                                delta = choice0["delta"]
                                content = delta.get("content") if isinstance(delta, dict) else None
                            except Exception:
                                content = None
                            if not content:
                                try:
                                    content = choice0["message"]["content"]
                                except Exception:
                                    content = None
                            if not content:
                                try:
                                    content = choice0.get("text")
                                except Exception:
                                    content = None
                    except Exception:
                        content = None
                    if content:
                        yield content
                else:
                    if response.code in [
                        "Arrearage",
                        "QuotaExhausted",
                        "PaymentRequired",
                        "AllocationQuota.FreeTierOnly",
                    ]:
                        yield self._format_quota_message(model)
                    else:
                        if response.code == "InvalidParameter":
                            yield f"Error: InvalidParameter - {response.message}（建议降低MAX_TOKENS / 启用压缩 / 减少知识库上下文）"
                        else:
                            yield f"Error: {response.code} - {response.message}"
        except Exception as e:
            yield f"Exception occurred: {str(e)}"

    def multimodal_generate(self, messages: List[Dict[str, Any]], model: str) -> str:
        return self._multimodal_call(
            messages,
            model,
            error_prefix="OCR Error",
            exception_prefix="OCR Exception",
        )

    def test_connection(self, model: Optional[str] = None) -> Dict[str, Any]:
        start_time = time.time()
        test_model = (model or "").strip() or settings.TURBO_MODEL_NAME or settings.MODEL_NAME
        if not test_model:
            latency = (time.time() - start_time) * 1000
            return {
                "success": False,
                "error": {"message": "model_name is required"},
                "latency": round(latency, 2),
            }
        if self._is_multimodal_model(test_model):
            result = self._multimodal_call(
                [{"role": "user", "content": [{"text": "hi"}]}],
                test_model,
            )
            latency = (time.time() - start_time) * 1000
            if self._is_failure_text_output(result):
                return {
                    "success": False,
                    "error": {"message": result},
                    "latency": round(latency, 2),
                }
            return {
                "success": True,
                "latency": round(latency, 2),
                "model_info": {"model": test_model},
                "sample_response": result,
            }

        try:
            response = dashscope.Generation.call(
                model=test_model,
                messages=[{"role": "user", "content": "hi"}],
                result_format="message",
                max_tokens=5,
            )
            latency = (time.time() - start_time) * 1000
            if response.status_code == HTTPStatus.OK:
                return {
                    "success": True,
                    "latency": round(latency, 2),
                    "model_info": {"model": test_model},
                    "sample_response": response.output.choices[0]["message"]["content"],
                }

            error_msg = response.message
            if response.code in [
                "Arrearage",
                "QuotaExhausted",
                "PaymentRequired",
                "AllocationQuota.FreeTierOnly",
            ]:
                error_msg = self._format_quota_message(test_model)

            return {
                "success": False,
                "error": {"message": error_msg, "code": response.code},
                "latency": round(latency, 2),
            }
        except Exception as e:
            return {
                "success": False,
                "error": {"message": str(e)},
                "latency": round((time.time() - start_time) * 1000, 2),
            }
