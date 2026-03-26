from __future__ import annotations

import base64
import mimetypes
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

    def _normalize_multimodal_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                normalized.append(msg)
                continue

            new_content: List[Dict[str, Any]] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if "image" in item:
                    image_value = str(item.get("image") or "")
                    if image_value.startswith("file://"):
                        local_path = image_value[7:]
                        try:
                            with open(local_path, "rb") as f:
                                image_bytes = f.read()
                            mime_type, _ = mimetypes.guess_type(local_path)
                            mime_type = mime_type or "image/png"
                            base64_image = base64.b64encode(image_bytes).decode("utf-8")
                            image_value = f"data:{mime_type};base64,{base64_image}"
                        except Exception as e:
                            raise RuntimeError(f"Error reading image: {e}") from e
                    new_content.append({"image": image_value})
                    continue

                if "text" in item:
                    new_content.append({"text": str(item.get("text") or "")})

            normalized.append({"role": msg.get("role", "user"), "content": new_content})
        return normalized

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

    def generate(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None) -> str:
        try:
            max_tokens = self._clamp_max_tokens(model, max_tokens)
            kwargs = {
                "model": model,
                "messages": messages,
                "result_format": "message",
            }
            if max_tokens:
                kwargs["max_tokens"] = max_tokens

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
                return f"[额度耗尽] 模型 {model} 的免费额度已用完，请在控制台关闭'仅使用免费额度'模式或充值。"
            if response.code == "InvalidParameter":
                return f"Error: InvalidParameter - {response.message}（建议降低MAX_TOKENS / 启用压缩 / 减少知识库上下文）"
            return f"Error: {response.code} - {response.message}"
        except Exception as e:
            return f"Exception occurred: {str(e)}"

    def generate_stream(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None):
        try:
            max_tokens = self._clamp_max_tokens(model, max_tokens)
            kwargs = {
                "model": model,
                "messages": messages,
                "result_format": "message",
                "stream": True,
                "incremental_output": True,
            }
            if max_tokens:
                kwargs["max_tokens"] = max_tokens

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
                        yield f"[额度耗尽] 模型 {model} 的免费额度已用完，请在控制台关闭'仅使用免费额度'模式或充值。"
                    else:
                        if response.code == "InvalidParameter":
                            yield f"Error: InvalidParameter - {response.message}（建议降低MAX_TOKENS / 启用压缩 / 减少知识库上下文）"
                        else:
                            yield f"Error: {response.code} - {response.message}"
        except Exception as e:
            yield f"Exception occurred: {str(e)}"

    def multimodal_generate(self, messages: List[Dict[str, Any]], model: str) -> str:
        try:
            normalized_messages = self._normalize_multimodal_messages(messages)
            response = dashscope.MultiModalConversation.call(model=model, messages=normalized_messages)

            if response.status_code == HTTPStatus.OK:
                return response.output.choices[0]["message"]["content"][0]["text"]
            if response.code in [
                "Arrearage",
                "QuotaExhausted",
                "PaymentRequired",
                "AllocationQuota.FreeTierOnly",
            ]:
                return f"[额度耗尽] 模型 {model} 的免费额度已用完，请在控制台关闭'仅使用免费额度'模式或充值。"
            return f"OCR Error: {response.code} - {response.message}"
        except Exception as e:
            return f"OCR Exception: {str(e)}"

    def test_connection(self, model: Optional[str] = None) -> Dict[str, Any]:
        start_time = time.time()
        test_model = (model or "").strip() or settings.TURBO_MODEL_NAME or settings.MODEL_NAME or "qwen-plus"
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
                error_msg = f"[额度耗尽] 模型 {test_model} 的免费额度已用完，请在控制台关闭'仅使用免费额度'模式或充值。"

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
