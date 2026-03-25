#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Provider 实现层。

职责边界：
1. 仅封装各模型供应商的调用协议与错误语义。
2. 不承载业务编排、缓存策略与用户配置解析。
3. 保持与原有 provider 行为、返回格式和错误文案一致。
"""

from http import HTTPStatus
import dashscope
import json
import httpx
import time
import base64
from typing import Optional, List, Dict, Any, Generator
from abc import ABC, abstractmethod

from core.settings.config import settings


class BaseModelProvider(ABC):
    """
    大模型提供商抽象基类。
    所有 provider 必须遵循同一调用接口，便于 AIClient 做统一编排。
    """

    @abstractmethod
    def generate(
        self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None
    ) -> str:
        """非流式文本生成。"""
        pass

    @abstractmethod
    def generate_stream(
        self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None
    ) -> Generator[str, None, None]:
        """流式文本生成。"""
        pass

    @abstractmethod
    def multimodal_generate(self, messages: List[Dict[str, Any]], model: str) -> str:
        """多模态生成（含图像输入）。"""
        pass

    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        """连通性探测。"""
        pass

    def get_balance(self) -> Dict[str, Any]:
        """账户余额查询，默认不支持。"""
        return {"supported": False, "message": "Not supported by this provider"}


class DashScopeProvider(BaseModelProvider):
    """
    阿里云 DashScope 提供商封装。
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        dashscope.api_key = api_key
        self._max_output_tokens_default = 4096

    def _clamp_max_tokens(self, model: str, max_tokens: Optional[int]) -> Optional[int]:
        """保证 max_tokens 在可接受范围内，避免请求侧参数错误。"""
        if not max_tokens:
            return None
        try:
            max_tokens_i = int(max_tokens)
        except Exception:
            return None
        if max_tokens_i <= 0:
            return None
        return min(max_tokens_i, self._max_output_tokens_default)

    def generate(
        self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None
    ) -> str:
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
            else:
                # 某些模型只允许流式输出时，自动回退到流式拼接以保持原有可用性。
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
                    return (
                        f"[额度耗尽] 模型 {model} 的免费额度已用完，请在控制台关闭'仅使用免费额度'模式或充值。"
                    )
                if response.code == "InvalidParameter":
                    return (
                        f"Error: InvalidParameter - {response.message}（建议降低 MAX_TOKENS / "
                        "启用压缩 / 减少知识库上下文）"
                    )
                return f"Error: {response.code} - {response.message}"
        except Exception as e:
            return f"Exception occurred: {str(e)}"

    def generate_stream(
        self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None
    ):
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
                        yield (
                            f"[额度耗尽] 模型 {model} 的免费额度已用完，请在控制台关闭'仅使用免费额度'模式或充值。"
                        )
                    else:
                        if response.code == "InvalidParameter":
                            yield (
                                f"Error: InvalidParameter - {response.message}（建议降低 MAX_TOKENS / "
                                "启用压缩 / 减少知识库上下文）"
                            )
                        else:
                            yield f"Error: {response.code} - {response.message}"
        except Exception as e:
            yield f"Exception occurred: {str(e)}"

    def multimodal_generate(self, messages: List[Dict[str, Any]], model: str) -> str:
        try:
            response = dashscope.MultiModalConversation.call(model=model, messages=messages)

            if response.status_code == HTTPStatus.OK:
                return response.output.choices[0]["message"]["content"][0]["text"]
            else:
                if response.code in [
                    "Arrearage",
                    "QuotaExhausted",
                    "PaymentRequired",
                    "AllocationQuota.FreeTierOnly",
                ]:
                    return (
                        f"[额度耗尽] 模型 {model} 的免费额度已用完，请在控制台关闭'仅使用免费额度'模式或充值。"
                    )
                return f"OCR Error: {response.code} - {response.message}"
        except Exception as e:
            return f"OCR Exception: {str(e)}"

    def test_connection(self, model: Optional[str] = None) -> Dict[str, Any]:
        start_time = time.time()
        # 优先使用传入模型，避免配置校验时被固定模型误导。
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
            else:
                error_msg = response.message
                if response.code in [
                    "Arrearage",
                    "QuotaExhausted",
                    "PaymentRequired",
                    "AllocationQuota.FreeTierOnly",
                ]:
                    error_msg = (
                        f"[额度耗尽] 模型 {test_model} 的免费额度已用完，请在控制台关闭'仅使用免费额度'模式或充值。"
                    )

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


class OpenAICompatibleProvider(BaseModelProvider):
    """
    OpenAI 兼容协议提供商。
    可连接官方 OpenAI、Ollama/vLLM/LM Studio 或第三方兼容网关。
    """

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/v1"):
            self.base_url += "/v1"
        self.api_key = api_key or "sk-placeholder"
        self.model = model

    def generate(
        self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None
    ) -> str:
        target_model = model or self.model
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": target_model,
            "messages": messages,
            "temperature": 0.7,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(url, headers=headers, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
                else:
                    return f"Error: HTTP {resp.status_code} - {resp.text}"
        except Exception as e:
            return f"Exception occurred: {str(e)}"

    def generate_stream(
        self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None
    ):
        target_model = model or self.model
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": target_model,
            "messages": messages,
            "stream": True,
            "temperature": 0.7,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        try:
            with httpx.Client(timeout=30.0) as client:
                with client.stream("POST", url, headers=headers, json=payload) as resp:
                    if resp.status_code != 200:
                        yield f"Error: HTTP {resp.status_code} - {resp.read().decode()}"
                        return

                    for line in resp.iter_lines():
                        if not line or line.strip() == "":
                            continue
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                if "choices" in data and len(data["choices"]) > 0:
                                    choice0 = data["choices"][0] or {}
                                    delta = choice0.get("delta", {}) or {}
                                    content = delta.get("content") or ""

                                    # 兼容 DeepSeek R1 的 reasoning_content 字段。
                                    reasoning = delta.get("reasoning_content") or ""
                                    if reasoning:
                                        yield reasoning
                                        continue

                                    if content:
                                        yield content
                                        continue

                                    msg = choice0.get("message", {}) or {}
                                    msg_content = msg.get("content") or ""
                                    if msg_content:
                                        yield msg_content
                                        continue

                                    text = choice0.get("text") or ""
                                    if text:
                                        yield text
                            except json.JSONDecodeError:
                                pass
        except Exception as e:
            yield f"Exception occurred: {str(e)}"

    def multimodal_generate(self, messages: List[Dict[str, Any]], model: str) -> str:
        # 保持原有多模态输入规范化流程，避免影响已有视觉模型接入。
        target_model = model or self.model
        formatted_messages = []

        for msg in messages:
            if isinstance(msg.get("content"), list):
                new_content = []
                for item in msg["content"]:
                    if "image" in item:
                        image_url = item["image"]
                        if image_url.startswith("file://"):
                            local_path = image_url[7:]
                            try:
                                with open(local_path, "rb") as f:
                                    base64_image = base64.b64encode(f.read()).decode("utf-8")
                                image_url = f"data:image/png;base64,{base64_image}"
                            except Exception as e:
                                return f"Error reading image: {str(e)}"

                        new_content.append({"type": "image_url", "image_url": {"url": image_url}})
                    elif "text" in item:
                        new_content.append({"type": "text", "text": item["text"]})

                formatted_messages.append({"role": msg["role"], "content": new_content})
            else:
                formatted_messages.append(msg)

        return self.generate(formatted_messages, target_model)

    def get_balance(self) -> Dict[str, Any]:
        # 尽量兼容 OneAPI/NewAPI/GoAmz 等代理的常见余额端点。
        endpoints = ["/dashboard/billing/subscription", "/v1/dashboard/billing/subscription"]

        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            root = base[:-3]
        elif base.endswith("/chat/completions"):
            root = base.replace("/chat/completions", "")
        else:
            root = base

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=5.0) as client:
                for ep in endpoints:
                    target_url = f"{root}{ep}"
                    try:
                        resp = client.get(target_url, headers=headers)
                        if resp.status_code == 200:
                            data = resp.json()
                            total = data.get("hard_limit_usd", 0) or data.get("total", 0)
                            remaining = data.get("balance")
                            return {
                                "supported": True,
                                "total": total,
                                "remaining": remaining,
                                "raw": data,
                            }
                    except Exception:
                        continue
        except Exception:
            pass

        return {"supported": False, "message": "Balance check not supported or failed"}

    def test_connection(self) -> Dict[str, Any]:
        start_time = time.time()
        try:
            result = self.generate([{"role": "user", "content": "hi"}], self.model, max_tokens=1)
            latency = (time.time() - start_time) * 1000

            if result.startswith("Error") or result.startswith("Exception"):
                return {
                    "success": False,
                    "error": {"message": result},
                    "latency": round(latency, 2),
                }

            return {
                "success": True,
                "latency": round(latency, 2),
                "model_info": {"model": self.model},
                "sample_response": result,
            }
        except Exception as e:
            return {
                "success": False,
                "error": {"message": str(e)},
                "latency": round((time.time() - start_time) * 1000, 2),
            }


class GLMProvider(OpenAICompatibleProvider):
    """GLM 系列 provider（通过 OpenAI 兼容协议访问）。"""

    def __init__(self, api_key: str, model: str = "glm-4v"):
        base_url = "https://open.bigmodel.cn/api/paas/v4/"
        super().__init__(base_url, api_key, model)


class UITARSProvider(OpenAICompatibleProvider):
    """UITARS provider（通过 OpenAI 兼容协议访问）。"""

    def __init__(self, base_url: str, api_key: str, model: str = "uitars-7b"):
        super().__init__(base_url, api_key, model)
