#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Client Module

This module provides the core functionality for interacting with AI models, including:
1. Text Generation (Multi-model support)
2. Image Processing (OCR)
3. Context Compression
4. RAG
5. Smart Caching (L4)

It supports both DashScope (Aliyun) and OpenAI-compatible providers (Ollama, vLLM, etc.).
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
    @abstractmethod
    def generate(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None) -> str:
        pass

    @abstractmethod
    def generate_stream(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None) -> Generator[str, None, None]:
        pass

    @abstractmethod
    def multimodal_generate(self, messages: List[Dict[str, Any]], model: str) -> str:
        pass
    
    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        pass

class DashScopeProvider(BaseModelProvider):
    def __init__(self, api_key: str):
        self.api_key = api_key
        dashscope.api_key = api_key

    def generate(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None) -> str:
        try:
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
                if response.code == 'DataInspectionFailed':
                    return f"Error: Content blocked by safety filter. {response.message}"
                if response.code in ['Arrearage', 'QuotaExhausted', 'PaymentRequired', 'AllocationQuota.FreeTierOnly']:
                    return f"[额度耗尽] 模型 {model} 的免费额度已用完，请在控制台关闭'仅使用免费额度'模式或充值。"
                return f"Error: {response.code} - {response.message}"
        except Exception as e:
            return f"Exception occurred: {str(e)}"

    def generate_stream(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None):
        try:
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
                    yield response.output.choices[0]['message']['content']
                else:
                    if response.code in ['Arrearage', 'QuotaExhausted', 'PaymentRequired', 'AllocationQuota.FreeTierOnly']:
                        yield f"[额度耗尽] 模型 {model} 的免费额度已用完，请在控制台关闭'仅使用免费额度'模式或充值。"
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
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip('/')
        if not self.base_url.endswith('/v1'):
            self.base_url += '/v1'
        self.api_key = api_key or "sk-placeholder"
        self.model = model

    def generate(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None) -> str:
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
                                    delta = data["choices"][0].get("delta", {})
                                    content = delta.get("content", "")
                                    if content:
                                        yield content
                            except json.JSONDecodeError:
                                pass
        except Exception as e:
            yield f"Exception occurred: {str(e)}"

    def multimodal_generate(self, messages: List[Dict[str, Any]], model: str) -> str:
        # Simplified implementation for OpenAI compatible vision
        # Assuming messages are already formatted for vision if supported
        return self.generate(messages, model or self.model)

    def test_connection(self) -> Dict[str, Any]:
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

class AIClient:
    """AI Client with support for dynamic provider switching"""
    
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
        """Factory method to create client from SystemConfig"""
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
        # Update other models if needed, or keep defaults
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
        if len(input_text) < 1000 and task_type == "general":
            return self.turbo_model
        return self.model

    def generate_response(self, user_input: str, system_prompt: str = None, db: Session = None, max_tokens: int = None, task_type: str = "general", model: str = None) -> str:
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
        if not self.provider:
            yield "Error: AI Provider not configured."
            return

        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        messages.append({'role': 'user', 'content': user_input})
        
        target_model = self.select_model((system_prompt or "") + user_input)
        
        yield from self.provider.generate_stream(messages, target_model, max_tokens or self.max_tokens)

    def analyze_image(self, image_path_or_url: str, prompt: str = "OCR: Extract all text from this image.", db: Session = None) -> str:
        if not self.provider:
            return "Error: AI Provider not configured."

        cache_key = f"ocr:{prompt}:{image_path_or_url}"
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
        target_model = self.vl_model if isinstance(self.provider, DashScopeProvider) else self.model
        
        response = self.provider.multimodal_generate(messages, target_model)
        
        if db and not response.startswith("OCR Error") and not response.startswith("OCR Exception"):
             cache_service.set(cache_key, response, "L2", db, metadata={"type": "ocr"})
             
        return response

    def compress_context(self, context: str, prompt: str = "Summary:", db: Session = None) -> str:
        # Use turbo model if available, else default
        target_model = self.turbo_model if isinstance(self.provider, DashScopeProvider) else self.model
        return self.generate_response(
            f"{prompt}\n\n{context}", 
            "You are a summarization expert.", 
            db, 
            model=target_model,
            task_type="compression"
        )
    
    def rag_generate_response(self, query: str, retrieved_docs: list[str], system_prompt: str = None, db: Session = None) -> str:
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
    Get AIClient instance for a specific user based on their active configuration.
    Falls back to global ai_client (System Default) if no user config found.
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
