#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Client Module (AI客户端模块)

This module provides the core functionality for interacting with AI models, including:
(该模块提供了与AI模型交互的核心功能，包括：)
1. Text Generation (Multi-model support) (文本生成 - 多模型支持)
2. Image Processing (OCR) (图像处理 - OCR)
3. Context Compression (上下文压缩)
4. RAG (检索增强生成)
5. Smart Caching (L4) (智能缓存)

It supports both DashScope (Aliyun) and OpenAI-compatible providers (Ollama, vLLM, etc.).
(支持 DashScope (阿里云) 和 OpenAI 兼容的提供商 (Ollama, vLLM 等)。)
"""

from http import HTTPStatus
import dashscope
import json
import httpx
import time
import asyncio
from typing import Optional, List, Dict, Any, Generator, AsyncGenerator
from abc import ABC, abstractmethod
from sqlalchemy.orm import Session
from core.config import settings
from core.cache import cache_service
from core.models import SystemConfig
from core.config_manager import config_manager
from core.utils import logger
from core.security import config_encryption

class BaseModelProvider(ABC):
    """
    大模型提供商抽象基类 (Base Model Provider)
    定义了所有 AI 模型提供商必须实现的通用接口。
    """
    @abstractmethod
    def generate(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None) -> str:
        """Generate text response (non-streaming) (生成文本响应 - 非流式)"""
        pass

    @abstractmethod
    def generate_stream(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None) -> Generator[str, None, None]:
        """Generate text response (streaming) (生成文本响应 - 流式)"""
        pass

    @abstractmethod
    def multimodal_generate(self, messages: List[Dict[str, Any]], model: str) -> str:
        """Generate response with multimodal input (images) (多模态生成 - 包含图片)"""
        pass
    
    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        """Test connection to the provider (测试连接)"""
        pass

    def get_balance(self) -> Dict[str, Any]:
        """
        Get account balance/quota information. (获取账户余额/额度信息)
        Returns dict with: total, used, remaining, currency (optional)
        Or error/unsupported message.
        """
        return {"supported": False, "message": "Not supported by this provider"}

class DashScopeProvider(BaseModelProvider):
    """
    阿里云 DashScope (通义千问) 提供商
    封装了阿里云 DashScope SDK 的调用逻辑。
    支持 Qwen-Turbo, Qwen-Plus, Qwen-Max 以及 Qwen-VL 等模型。
    """
    def __init__(self, api_key: str):
        self.api_key = api_key
        dashscope.api_key = api_key
        self._max_output_tokens_default = 4096

    def _clamp_max_tokens(self, model: str, max_tokens: Optional[int]) -> Optional[int]:
        """Ensure max_tokens is within valid range (确保 max_tokens 在有效范围内)"""
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
                'model': model,
                'messages': messages,
                'result_format': 'message',
            }
            if max_tokens:
                kwargs['max_tokens'] = max_tokens

            response = dashscope.Generation.call(**kwargs)
            
            if response.status_code == HTTPStatus.OK:
                return response.output.choices[0]['message']['content']
            else:
                # Automatic fallback to stream mode if model requires it (e.g. glm-4.5)
                # (如果模型强制要求流式模式（如 glm-4.5），自动回退到流式模式)
                if response.code == 'InvalidParameter' and 'stream mode' in str(response.message):
                    try:
                        full_text = ""
                        for chunk in self.generate_stream(messages, model, max_tokens):
                            # Check if the chunk is actually an error message from generate_stream
                            # (检查块是否实际上是来自 generate_stream 的错误消息)
                            if chunk.startswith("Error:") or chunk.startswith("Exception") or chunk.startswith("[额度耗尽]"):
                                return chunk
                            full_text += chunk
                        return full_text
                    except Exception as e:
                        return f"Exception during stream fallback: {str(e)}"

                if response.code == 'DataInspectionFailed':
                    return f"Error: Content blocked by safety filter. {response.message}"
                if response.code in ['Arrearage', 'QuotaExhausted', 'PaymentRequired', 'AllocationQuota.FreeTierOnly']:
                    return f"[额度耗尽] 模型 {model} 的免费额度已用完，请在控制台关闭'仅使用免费额度'模式或充值。"
                if response.code == 'InvalidParameter':
                    return f"Error: InvalidParameter - {response.message}（建议降低 MAX_TOKENS / 启用压缩 / 减少知识库上下文）"
                return f"Error: {response.code} - {response.message}"
        except Exception as e:
            return f"Exception occurred: {str(e)}"

    def generate_stream(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None):
        try:
            max_tokens = self._clamp_max_tokens(model, max_tokens)
            kwargs = {
                'model': model,
                'messages': messages,
                'result_format': 'message',
                'stream': True,
                'incremental_output': True 
            }
            if max_tokens:
                kwargs['max_tokens'] = max_tokens

            responses = dashscope.Generation.call(**kwargs)
            
            for response in responses:
                if response.status_code == HTTPStatus.OK:
                    content = None
                    try:
                        choice0 = response.output.choices[0] if response.output and response.output.choices else None
                        if choice0:
                            try:
                                delta = choice0['delta']
                                content = delta.get('content') if isinstance(delta, dict) else None
                            except Exception:
                                content = None
                            if not content:
                                try:
                                    content = choice0['message']['content']
                                except Exception:
                                    content = None
                            if not content:
                                try:
                                    content = choice0.get('text')
                                except Exception:
                                    content = None
                    except Exception:
                        content = None
                    if content:
                        yield content
                else:
                    if response.code in ['Arrearage', 'QuotaExhausted', 'PaymentRequired', 'AllocationQuota.FreeTierOnly']:
                        yield f"[额度耗尽] 模型 {model} 的免费额度已用完，请在控制台关闭'仅使用免费额度'模式或充值。"
                    else:
                        if response.code == 'InvalidParameter':
                            yield f"Error: InvalidParameter - {response.message}（建议降低 MAX_TOKENS / 启用压缩 / 减少知识库上下文）"
                        else:
                            yield f"Error: {response.code} - {response.message}"
        except Exception as e:
            yield f"Exception occurred: {str(e)}"

    def multimodal_generate(self, messages: List[Dict[str, Any]], model: str) -> str:
        try:
            response = dashscope.MultiModalConversation.call(
                model=model,
                messages=messages
            )
            
            if response.status_code == HTTPStatus.OK:
                return response.output.choices[0]['message']['content'][0]['text']
            else:
                if response.code in ['Arrearage', 'QuotaExhausted', 'PaymentRequired', 'AllocationQuota.FreeTierOnly']:
                    return f"[额度耗尽] 模型 {model} 的免费额度已用完，请在控制台关闭'仅使用免费额度'模式或充值。"
                return f"OCR Error: {response.code} - {response.message}"
        except Exception as e:
            return f"OCR Exception: {str(e)}"
            
    def test_connection(self) -> Dict[str, Any]:
        start_time = time.time()
        try:
            response = dashscope.Generation.call(
                model='qwen-turbo',
                messages=[{'role': 'user', 'content': 'hi'}],
                result_format='message',
                max_tokens=5
            )
            latency = (time.time() - start_time) * 1000
            if response.status_code == HTTPStatus.OK:
                return {
                    "success": True,
                    "latency": round(latency, 2),
                    "model_info": {"model": "qwen-turbo"},
                    "sample_response": response.output.choices[0]['message']['content']
                }
            else:
                error_msg = response.message
                if response.code in ['Arrearage', 'QuotaExhausted', 'PaymentRequired', 'AllocationQuota.FreeTierOnly']:
                    error_msg = f"[额度耗尽] 模型 qwen-turbo 的免费额度已用完，请在控制台关闭'仅使用免费额度'模式或充值。"
                
                return {
                    "success": False,
                    "error": {"message": error_msg, "code": response.code},
                    "latency": round(latency, 2)
                }
        except Exception as e:
            return {
                "success": False,
                "error": {"message": str(e)},
                "latency": round((time.time() - start_time) * 1000, 2)
            }

class OpenAICompatibleProvider(BaseModelProvider):
    """
    OpenAI 兼容协议提供商 (OpenAI Compatible Provider)
    
    用于连接任何支持 OpenAI API 格式的后端，包括：
    - 官方 OpenAI API
    - 本地部署的 LLM (如通过 vLLM, Ollama, LM Studio 部署)
    - 第三方聚合 API (如 DeepSeek, Moonshot 等)
    """
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip('/')
        if not self.base_url.endswith('/v1'):
            self.base_url += '/v1'
        self.api_key = api_key or "sk-placeholder"
        self.model = model

    def generate(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None) -> str:
        """
        生成文本响应 (非流式)
        
        Args:
            messages: 消息列表。
            model: 模型名称。
            max_tokens: 最大生成 Token 数。
            
        Returns:
            str: 生成的文本内容。
        """
        # Override model if specific one provided, else use configured
        target_model = model or self.model
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": target_model,
            "messages": messages,
            "temperature": 0.7
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(url, headers=headers, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    return data['choices'][0]['message']['content']
                else:
                    return f"Error: HTTP {resp.status_code} - {resp.text}"
        except Exception as e:
            return f"Exception occurred: {str(e)}"

    def generate_stream(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None):
        """
        生成文本响应 (流式)
        
        Args:
            messages: 消息列表。
            model: 模型名称。
            max_tokens: 最大生成 Token 数。
            
        Yields:
            str: 生成的文本片段。
        """
        target_model = model or self.model
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": target_model,
            "messages": messages,
            "stream": True,
            "temperature": 0.7
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
        """
        多模态生成 (图片理解)
        
        Args:
            messages: 包含图片和文本的消息列表。
            model: 模型名称。
            
        Returns:
            str: 模型对图片的描述或回答。
        """
        # Enhanced implementation for OpenAI compatible vision (e.g. GLM-4V, UITARS via vLLM)
        # We need to ensure the image format in messages is compatible with OpenAI API
        # messages usually come as:
        # [
        #   {
        #     "role": "user",
        #     "content": [
        #       {"image": "path_or_url"},
        #       {"text": "prompt"}
        #     ]
        #   }
        # ]
        
        target_model = model or self.model
        formatted_messages = []
        
        for msg in messages:
            if isinstance(msg.get("content"), list):
                new_content = []
                for item in msg["content"]:
                    if "image" in item:
                        image_url = item["image"]
                        if image_url.startswith("file://"):
                            # Read local file and convert to base64
                            local_path = image_url[7:]
                            try:
                                with open(local_path, "rb") as f:
                                    base64_image = base64.b64encode(f.read()).decode('utf-8')
                                image_url = f"data:image/png;base64,{base64_image}" # Assume PNG or detect type
                            except Exception as e:
                                return f"Error reading image: {str(e)}"
                        
                        new_content.append({
                            "type": "image_url",
                            "image_url": {
                                "url": image_url
                            }
                        })
                    elif "text" in item:
                        new_content.append({
                            "type": "text",
                            "text": item["text"]
                        })
                
                formatted_messages.append({
                    "role": msg["role"],
                    "content": new_content
                })
            else:
                formatted_messages.append(msg)

        return self.generate(formatted_messages, target_model)

    def get_balance(self) -> Dict[str, Any]:
        """
        获取账户余额 (Get Balance)
        
        尝试从 OpenAI 兼容接口获取余额信息 (通常是 /dashboard/billing/subscription)。
        """
        # Common endpoints for OneAPI/NewAPI/GoAmz proxies
        endpoints = [
            "/dashboard/billing/subscription",
            "/v1/dashboard/billing/subscription"
        ]
        
        # Try to infer root url from base_url
        # self.base_url usually is http://host:port/v1 or http://host:port
        # We want http://host:port
        
        base = self.base_url.rstrip('/')
        if base.endswith('/v1'):
            root = base[:-3]
        elif base.endswith('/chat/completions'):
             # Very specific?
             root = base.replace('/chat/completions', '')
        else:
            root = base

        # Try endpoints
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        try:
            with httpx.Client(timeout=5.0) as client:
                for ep in endpoints:
                    target_url = f"{root}{ep}"
                    # Try with base_url too if root inference failed or provider structure is weird
                    # Actually some providers put it under /v1/dashboard... so using base_url directly if it has /v1 is good.
                    
                    try:
                        resp = client.get(target_url, headers=headers)
                        if resp.status_code == 200:
                            data = resp.json()
                            # Common format: { "hard_limit_usd": x, "has_payment_method": bool, "soft_limit_usd": x, "system_hard_limit_usd": x, "access_until": x }
                            # OR { "object": "billing_subscription", "has_payment_method": false, "soft_limit_usd": 0, "hard_limit_usd": 0, "system_hard_limit_usd": 0, "access_until": 0 }
                            
                            # We also need usage? /dashboard/billing/usage is often separate.
                            # But subscription usually gives total quota.
                            # Some APIs return { "quota": x, "used": y, "balance": z } directly (non-standard)
                            
                            total = data.get("hard_limit_usd", 0) or data.get("total", 0)
                            
                            # Note: To get 'remaining', we often need usage. 
                            # But some proxies return 'balance' directly.
                            remaining = data.get("balance")
                            
                            if remaining is None:
                                # Try to calculate or look for usage?
                                # This is getting complicated. Let's return what we have.
                                pass
                            
                            return {
                                "supported": True,
                                "total": total,
                                "remaining": remaining, # Might be None
                                "raw": data
                            }
                    except Exception:
                        continue
        except Exception:
            pass
            
        return {"supported": False, "message": "Balance check not supported or failed"}

    def test_connection(self) -> Dict[str, Any]:
        """
        测试连接 (Test Connection)
        
        发送一个简单的 hello 请求来验证 API Key 和 Base URL 是否正确。
        """
        start_time = time.time()
        try:
            # Use max_tokens=1 for quick test
            result = self.generate([{"role": "user", "content": "hi"}], self.model, max_tokens=1)
            latency = (time.time() - start_time) * 1000
            
            if result.startswith("Error") or result.startswith("Exception"):
                 return {
                    "success": False,
                    "error": {"message": result},
                    "latency": round(latency, 2)
                }
            
            return {
                "success": True,
                "latency": round(latency, 2),
                "model_info": {"model": self.model},
                "sample_response": result
            }
        except Exception as e:
            return {
                "success": False,
                "error": {"message": str(e)},
                "latency": round((time.time() - start_time) * 1000, 2)
            }

class GLMProvider(OpenAICompatibleProvider):
    """
    Provider for GLM open source models (e.g. GLM-4V).
    Assumes deployment via OpenAI-compatible API (e.g. vLLM or ZhipuAI local/cloud).
    """
    def __init__(self, api_key: str, model: str = "glm-4v"):
        # Default to ZhipuAI endpoint if not specified, but usually passed via config
        base_url = "https://open.bigmodel.cn/api/paas/v4/" 
        super().__init__(base_url, api_key, model)

class UITARSProvider(OpenAICompatibleProvider):
    """
    Provider for ByteDance's UITARS (UI Transformer) models.
    Assumes deployment via OpenAI-compatible API.
    """
    def __init__(self, base_url: str, api_key: str, model: str = "uitars-7b"):
        super().__init__(base_url, api_key, model)


class AIClient:
    """
    AI 客户端门面类 (AI Client Facade)
    
    统一管理所有 AI 调用请求，功能包括：
    1. 动态切换提供商 (Provider Switching)。
    2. 智能模型选择 (Smart Model Selection)。
    3. 响应缓存 (L4 Cache) - 节省 Token 和加速响应。
    4. RAG 流程编排 (Retrieval-Augmented Generation)。
    5. 图像分析与 OCR (Image Analysis)。
    """
    
    def __init__(self, provider: BaseModelProvider = None):
        self._provider = provider
        self.model = settings.MODEL_NAME
        self.turbo_model = settings.TURBO_MODEL_NAME
        self.vl_model = settings.VL_MODEL_NAME
        self.max_tokens = getattr(settings, 'MAX_TOKENS', 2000)
        
        # Fallback initialization if no provider given
        if not self._provider:
            self._init_from_settings()

    def _init_from_settings(self):
        """Fallback to settings.py"""
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
        从系统配置创建客户端 (Factory from Config)
        根据用户在前端配置的 Provider 类型（DashScope / OpenAI / Ollama 等）
        实例化对应的 Provider 并返回 Client。
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
                model=config.model_name
            )
            
        client = cls(provider)
        client.model = config.model_name
        
        # Update other models from config if available
        if config.turbo_model_name:
            client.turbo_model = config.turbo_model_name
        if config.vl_model_name:
            client.vl_model = config.vl_model_name
            
        return client

    def update_provider(self, provider: BaseModelProvider, model_name: str = None):
        """Thread-safe provider update"""
        self._provider = provider
        if model_name:
            self.model = model_name

    def select_model(self, input_text: str, task_type: str = "general") -> str:
        """Dynamic model selection"""
        if not self._provider:
            return self.model
            
        # If using OpenAI/Local, usually just one model is configured
        if isinstance(self._provider, OpenAICompatibleProvider):
            return self._provider.model
            
        # DashScope logic
        if task_type in ["compression", "summary"]:
            return self.turbo_model
        if task_type == "ocr":
            return self.vl_model
        # Removed automatic downgrade to turbo for short text to respect user selection
        # if len(input_text) < 1000 and task_type == "general":
        #    return self.turbo_model
        return self.model

    def generate_response(self, user_input: str, system_prompt: str = None, db: Session = None, max_tokens: int = None, task_type: str = "general", model: str = None) -> str:
        """
        生成响应 (Generate Response)
        
        核心流程：
        1. 检查 Provider 是否配置。
        2. 构建 Prompt 消息。
        3. 选择合适的模型 (select_model)。
        4. 检查 L4 缓存 (Cache Hit?)。
        5. 调用 Provider 生成响应。
        6. 写入 L4 缓存。
        """
        if not self.provider:
            return "Error: AI Provider not configured."

        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        messages.append({'role': 'user', 'content': user_input})
        
        target_model = model
        if not target_model:
            target_model = self.select_model((system_prompt or "") + user_input, task_type)

        # Cache check
        if db:
            cache_key_content = f"{target_model}:{json.dumps(messages, ensure_ascii=False)}"
            cached = cache_service.get(cache_key_content, "L4", db)
            if cached:
                return cached

        result = self.provider.generate(messages, target_model, max_tokens or self.max_tokens)
        
        # Cache set
        if db and not result.startswith("Error") and not result.startswith("Exception"):
             cache_service.set(cache_key_content, result, "L4", db, metadata={"model": target_model})
             
        return result

    def generate_response_stream(self, user_input: str, system_prompt: str = None, max_tokens: int = None):
        """
        流式生成响应 (Stream Response)
        用于前端实时显示打字机效果。
        """
        if not self.provider:
            yield "Error: AI Provider not configured."
            return

        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        messages.append({'role': 'user', 'content': user_input})
        
        target_model = self.select_model((system_prompt or "") + user_input)
        
        yield from self.provider.generate_stream(messages, target_model, max_tokens or self.max_tokens)

    def analyze_image(self, image_path_or_url: str, prompt: str = "OCR: Extract all text from this image.", db: Session = None, model: str = None) -> str:
        """
        图像分析 / OCR (Image Analysis)
        支持本地路径 (file://) 和 URL。
        优先检查 L2 缓存。
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
                "content": [
                    {"image": image_path_or_url},
                    {"text": prompt}
                ]
            }
        ]
        
        # Note: OpenAI compatible provider might handle vision differently, 
        # but for now we assume standardized message format if supported.
        # DashScope uses vl_model.
        if model:
            target_model = model
        else:
            target_model = self.vl_model if isinstance(self.provider, DashScopeProvider) else self.model
        
        response = self.provider.multimodal_generate(messages, target_model)
        
        if db and not response.startswith("OCR Error") and not response.startswith("OCR Exception"):
             cache_service.set(cache_key, response, "L2", db, metadata={"type": "ocr", "model": target_model})
             
        return response

    def compress_context(self, context: str, prompt: str = "Summary:", db: Session = None) -> str:
        """
        上下文压缩 (Context Compression)
        使用轻量级模型 (Turbo) 对长文本进行摘要，减少 Token 消耗。
        """
        # Use turbo model if available, else default
        target_model = self.turbo_model if self.turbo_model else self.model
        return self.generate_response(
            f"{prompt}\n\n{context}", 
            "You are a summarization expert.", 
            db, 
            model=target_model,
            task_type="compression"
        )
    
    def rag_generate_response(self, query: str, retrieved_docs: list[str], system_prompt: str = None, db: Session = None) -> str:
        """
        RAG 生成 (Retrieval-Augmented Generation)
        将检索到的文档列表合并、压缩后，作为上下文输入给模型。
        """
        combined_docs = "\n\n".join([f"Doc {i+1}: {doc}" for i, doc in enumerate(retrieved_docs)])
        compressed_context = self.compress_context(
            combined_docs,
            "Summarize relevant info for query:",
            db
        )
        final_prompt = f"Query: {query}\n\nContext: {compressed_context}"
        return self.generate_response(final_prompt, system_prompt, db, task_type="rag")

    async def generate_response_async(self, prompt: str, system_prompt: str = None, db: Session = None, model: str = None, task_type: str = "general") -> str:
        # Backward compatibility wrapper
        # In a full async refactor, we would make the provider methods async.
        # For now, we wrap the synchronous generate in a thread or use the sync implementation.
        # Given the existing codebase used httpx async client in one place, we should ideally upgrade BaseProvider to be async.
        # But to minimize breakage in this refactor, we will delegate to the synchronous generate 
        # OR implement async methods in provider if needed.
        # For simplicity and stability in this "Phase 1", we will run the sync method in a thread
        # if the provider is sync.
        
        # However, OpenAICompatibleProvider uses httpx.Client (sync) for simplicity above.
        # Let's just call the sync method.
        return self.generate_response(prompt, system_prompt, db, max_tokens=self.max_tokens, task_type=task_type, model=model)


# Initialize global client
ai_client = AIClient()

def get_client_for_user(user_id: int, db: Session) -> AIClient:
    """
    获取用户专属 AI 客户端 (Get User AI Client)
    
    根据用户的 ID 查询数据库中的个性化配置 (SystemConfig)，
    如果存在，则返回配置了该用户 Key 的 AIClient 实例；
    否则，返回系统默认的全局 ai_client。
    这实现了多租户/多用户的模型配置隔离。
    """
    if not user_id or not db:
        return ai_client
        
    user_config = config_manager.get_active_config(db, user_id)
    if user_config:
        return AIClient.from_config(user_config)
    
    return ai_client

# Try to load active config from DB if possible (requires DB session)
# Since we are at module level, we can't easily get a session.
# Initialization will happen via main.py startup event or first request.
