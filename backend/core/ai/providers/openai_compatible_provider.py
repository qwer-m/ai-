from __future__ import annotations

import base64
import json
import time
from typing import Any, Dict, List, Optional

import httpx

from core.ai.providers.base import BaseModelProvider


class OpenAICompatibleProvider(BaseModelProvider):
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/v1"):
            self.base_url += "/v1"
        self.api_key = api_key or "sk-placeholder"
        self.model = model

    def generate(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None) -> str:
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
                return f"Error: HTTP {resp.status_code} - {resp.text}"
        except Exception as e:
            return f"Exception occurred: {str(e)}"

    def generate_stream(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None):
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
