from __future__ import annotations

import base64
import json
import os
import time
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional

import httpx

from core.ai.providers.base import BaseModelProvider


class OpenAICompatibleProvider(BaseModelProvider):
    def __init__(self, base_url: str, api_key: str, model: str):
        raw_base_url = (base_url or "").strip().rstrip("/")
        self.wire_api = "chat_completions"
        if raw_base_url.endswith("/responses"):
            # Support API router configs that provide a full responses endpoint.
            self.wire_api = "responses"
            raw_base_url = raw_base_url[: -len("/responses")]

        self.base_url = raw_base_url.rstrip("/")
        if not self.base_url.endswith("/v1"):
            self.base_url += "/v1"
        self.api_key = api_key or "sk-placeholder"
        self.model = model
        self.last_response_metadata: Dict[str, Any] = {}

    def _http_timeout(self) -> httpx.Timeout:
        raw = str(os.getenv("OPENAI_COMPAT_HTTP_TIMEOUT_SECONDS", os.getenv("AI_HTTP_TIMEOUT_SECONDS", "90"))).strip()
        try:
            total = float(raw)
        except Exception:
            total = 90.0
        if total <= 0:
            total = 90.0
        connect_timeout = min(15.0, total)
        write_timeout = min(30.0, total)
        pool_timeout = min(15.0, total)
        return httpx.Timeout(total, connect=connect_timeout, write=write_timeout, pool=pool_timeout)

    def _http_trust_env(self) -> bool:
        raw = os.getenv("OPENAI_COMPAT_TRUST_ENV", os.getenv("AI_HTTP_TRUST_ENV", "true"))
        return str(raw).strip().lower() not in {"0", "false", "no", "off"}

    def _http_client_kwargs(self, *, timeout: httpx.Timeout | float | None = None) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "trust_env": self._http_trust_env(),
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        return kwargs

    def _resolve_max_token_cap(self, target_model: str) -> Optional[int]:
        host = (urlparse(self.base_url).hostname or "").lower()
        model = str(target_model or "").strip().lower()
        if "deepseek" in host or model.startswith("deepseek"):
            return 8192

        raw = str(os.getenv("OPENAI_COMPAT_MAX_TOKENS_CAP", "")).strip()
        if not raw:
            return None
        try:
            value = int(raw)
        except Exception:
            return None
        return value if value > 0 else None

    def _normalize_max_tokens(self, max_tokens: Optional[int], target_model: str) -> Optional[int]:
        if max_tokens is None:
            return None
        try:
            resolved = int(max_tokens)
        except Exception:
            return None
        if resolved <= 0:
            return None
        cap = self._resolve_max_token_cap(target_model)
        if cap is not None:
            resolved = min(resolved, int(cap))
        return max(1, resolved)

    def _messages_to_input(self, messages: List[Dict[str, Any]]) -> str:
        chunks: List[str] = []
        for msg in messages or []:
            role = str(msg.get("role") or "user")
            content = msg.get("content", "")

            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts: List[str] = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if isinstance(item.get("text"), str):
                        parts.append(str(item.get("text")))
                text = " ".join(p for p in parts if p)
            elif content is not None:
                text = str(content)

            if text:
                chunks.append(f"{role}: {text}")

        return "\n".join(chunks) if chunks else "user: hi"

    def _extract_responses_text(self, data: Dict[str, Any]) -> str:
        direct_text = data.get("output_text")
        if isinstance(direct_text, str) and direct_text:
            return direct_text

        output = data.get("output")
        if not isinstance(output, list):
            return ""

        pieces: List[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue

            if isinstance(item.get("text"), str):
                pieces.append(str(item.get("text")))

            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if isinstance(part.get("text"), str):
                    pieces.append(str(part.get("text")))

        return "".join(pieces)

    def _responses_stream_collect_text(self, payload: Dict[str, Any]) -> str:
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        pieces: List[str] = []

        with httpx.Client(**self._http_client_kwargs(timeout=self._http_timeout())) as client:
            with client.stream("POST", f"{self.base_url}/responses", headers=headers, json=stream_payload) as resp:
                if resp.status_code != 200:
                    return f"Error: HTTP {resp.status_code} - {resp.read().decode()}"

                for line in resp.iter_lines():
                    if not line or line.strip() == "":
                        continue
                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    event_type = str(data.get("type") or "")
                    if event_type == "response.output_text.delta":
                        delta = data.get("delta") or ""
                        if delta:
                            pieces.append(str(delta))
                    elif event_type == "response.output_text.done":
                        done_text = data.get("text") or ""
                        if done_text and not pieces:
                            pieces.append(str(done_text))
                    elif event_type in {"error", "response.error"}:
                        return f"Error: {json.dumps(data, ensure_ascii=False)}"

        return "".join(pieces)

    def _wants_json_response(self, messages: List[Dict[str, Any]]) -> bool:
        text = " ".join(str(msg.get("content") or "") for msg in messages or [])
        lowered = text.lower()
        return (
            "json" in lowered
            or "严格 json" in text
            or "只输出 json" in text
            or "只返回 json" in text
        )

    def _resolve_temperature(self, *, json_response: bool = False) -> float:
        if json_response:
            raw = os.getenv("AI_JSON_TEMPERATURE", "").strip()
            if raw == "":
                return 0.0
        else:
            raw = os.getenv("AI_TEMPERATURE", "").strip()
        if raw == "":
            return 0.7
        try:
            value = float(raw)
        except Exception:
            return 0.7
        if value < 0:
            return 0.0
        if value > 2:
            return 2.0
        return value

    def generate(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None) -> str:
        target_model = model or self.model
        resolved_max_tokens = self._normalize_max_tokens(max_tokens, target_model)
        wants_json_response = self._wants_json_response(messages)  # type: ignore[arg-type]
        self.last_response_metadata = {
            "model": target_model,
            "wire_api": self.wire_api,
            "max_tokens": resolved_max_tokens,
            "json_response": wants_json_response,
        }

        if self.wire_api == "responses":
            url = f"{self.base_url}/responses"
            payload: Dict[str, Any] = {
                "model": target_model,
                "input": self._messages_to_input(messages),  # type: ignore[arg-type]
                "temperature": self._resolve_temperature(json_response=wants_json_response),
            }
            if resolved_max_tokens:
                payload["max_output_tokens"] = resolved_max_tokens
        else:
            url = f"{self.base_url}/chat/completions"
            payload = {
                "model": target_model,
                "messages": messages,
                "temperature": self._resolve_temperature(json_response=wants_json_response),
            }
            if resolved_max_tokens:
                payload["max_tokens"] = resolved_max_tokens
            if wants_json_response:
                response_format_type = os.getenv("OPENAI_COMPAT_JSON_RESPONSE_FORMAT", "json_object").strip()
                if response_format_type:
                    payload["response_format"] = {"type": response_format_type}
                reasoning_effort = os.getenv("OPENAI_COMPAT_JSON_REASONING_EFFORT", "low").strip()
                if reasoning_effort:
                    payload["reasoning_effort"] = reasoning_effort

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(**self._http_client_kwargs(timeout=self._http_timeout())) as client:
                resp = client.post(url, headers=headers, json=payload)
                json_compat_fields = ("reasoning_effort", "response_format")
                if resp.status_code in {400, 422} and any(field in payload for field in json_compat_fields):
                    fallback_payload = dict(payload)
                    for field in json_compat_fields:
                        fallback_payload.pop(field, None)
                    fallback_resp = client.post(url, headers=headers, json=fallback_payload)
                    self.last_response_metadata["json_compat_fallback"] = True
                    self.last_response_metadata["reasoning_effort_rejected_status"] = resp.status_code
                    resp = fallback_resp
                self.last_response_metadata.update(
                    {
                        "http_status": resp.status_code,
                        "url_path": "/responses" if self.wire_api == "responses" else "/chat/completions",
                        "reasoning_effort": payload.get("reasoning_effort"),
                        "response_format": payload.get("response_format"),
                    }
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if self.wire_api == "responses":
                        text = self._extract_responses_text(data)
                        self.last_response_metadata.update(
                            {
                                "content_len": len(text or ""),
                                "raw_keys": list(data.keys())[:20],
                            }
                        )
                        if text:
                            return text
                        streamed_text = self._responses_stream_collect_text(payload)
                        self.last_response_metadata["stream_fallback_content_len"] = len(streamed_text or "")
                        return streamed_text
                    choice0 = (data.get("choices") or [{}])[0] or {}
                    message = choice0.get("message") or {}
                    content = message.get("content") or ""
                    reasoning_content = message.get("reasoning_content") or ""
                    self.last_response_metadata.update(
                        {
                            "finish_reason": choice0.get("finish_reason"),
                            "content_len": len(str(content or "")),
                            "reasoning_len": len(str(reasoning_content or "")),
                            "message_keys": list(message.keys())[:20],
                        }
                    )
                    return content
                self.last_response_metadata["error_preview"] = resp.text[:500]
                return f"Error: HTTP {resp.status_code} - {resp.text}"
        except Exception as e:
            self.last_response_metadata.update(
                {
                    "exception_type": type(e).__name__,
                    "exception": str(e)[:500],
                }
            )
            return f"Exception occurred: {str(e)}"

    def generate_stream(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None):
        target_model = model or self.model
        resolved_max_tokens = self._normalize_max_tokens(max_tokens, target_model)

        if self.wire_api == "responses":
            url = f"{self.base_url}/responses"
            payload: Dict[str, Any] = {
                "model": target_model,
                "input": self._messages_to_input(messages),  # type: ignore[arg-type]
                "stream": True,
                "temperature": self._resolve_temperature(),
            }
            if resolved_max_tokens:
                payload["max_output_tokens"] = resolved_max_tokens
        else:
            url = f"{self.base_url}/chat/completions"
            payload = {
                "model": target_model,
                "messages": messages,
                "stream": True,
                "temperature": self._resolve_temperature(),
            }
            if resolved_max_tokens:
                payload["max_tokens"] = resolved_max_tokens

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(**self._http_client_kwargs(timeout=self._http_timeout())) as client:
                with client.stream("POST", url, headers=headers, json=payload) as resp:
                    if resp.status_code != 200:
                        yield f"Error: HTTP {resp.status_code} - {resp.read().decode()}"
                        return

                    emitted_text = False
                    for line in resp.iter_lines():
                        if not line or line.strip() == "":
                            continue
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                if self.wire_api == "responses":
                                    event_type = str(data.get("type") or "")
                                    if event_type == "response.output_text.delta":
                                        delta = data.get("delta") or ""
                                        if delta:
                                            emitted_text = True
                                            yield str(delta)
                                    elif event_type == "response.output_text.done":
                                        done_text = data.get("text") or ""
                                        if done_text and not emitted_text:
                                            emitted_text = True
                                            yield str(done_text)
                                    elif event_type == "response.completed":
                                        text = self._extract_responses_text(
                                            data.get("response") if isinstance(data.get("response"), dict) else data
                                        )
                                        if text and not emitted_text:
                                            emitted_text = True
                                            yield text
                                    elif event_type in {"error", "response.error"}:
                                        yield f"Error: {json.dumps(data, ensure_ascii=False)}"
                                        return
                                    continue

                                if "choices" in data and len(data["choices"]) > 0:
                                    choice0 = data["choices"][0] or {}
                                    delta = choice0.get("delta", {}) or {}
                                    content = delta.get("content") or ""

                                    reasoning = delta.get("reasoning_content") or ""
                                    if reasoning:
                                        continue

                                    if content:
                                        emitted_text = True
                                        yield content
                                        continue

                                    msg = choice0.get("message", {}) or {}
                                    msg_content = msg.get("content") or ""
                                    if msg_content and not emitted_text:
                                        emitted_text = True
                                        yield msg_content
                                        continue

                                    text = choice0.get("text") or ""
                                    if text and not emitted_text:
                                        emitted_text = True
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
            with httpx.Client(**self._http_client_kwargs(timeout=5.0)) as client:
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
