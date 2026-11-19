from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Callable, Iterable
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional

import httpx

from core.ai.providers.base import BaseModelProvider, STREAM_HEARTBEAT_CHUNK


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
        self.reasoning_effort: str | None = None
        self.last_response_metadata: Dict[str, Any] = {}

    def _http_timeout(self, total_override: float | None = None) -> httpx.Timeout:
        override = getattr(self, "request_timeout_seconds", None)
        resolved_override = total_override if total_override not in (None, "") else override
        configured_timeout = os.getenv(
            "OPENAI_COMPAT_HTTP_TIMEOUT_SECONDS",
            os.getenv("AI_HTTP_TIMEOUT_SECONDS", "90"),
        )
        raw = str(resolved_override if resolved_override not in (None, "") else configured_timeout).strip()
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

    def _http_timeout_source(self, total_override: float | None = None) -> str:
        if total_override not in (None, ""):
            return "call_override"
        if getattr(self, "request_timeout_seconds", None) not in (None, ""):
            return "provider_override"
        if (
            "OPENAI_COMPAT_HTTP_TIMEOUT_SECONDS" in os.environ
            or "AI_HTTP_TIMEOUT_SECONDS" in os.environ
        ):
            return "environment"
        return "default"

    def _stream_attempt_timeout_seconds(
        self,
        total_override: float | None = None,
    ) -> float:
        override = getattr(self, "stream_attempt_timeout_seconds", None)
        raw = str(
            total_override
            if total_override not in (None, "")
            else (
                override
                if override not in (None, "")
                else os.getenv(
                    "OPENAI_COMPAT_STREAM_ATTEMPT_TIMEOUT_SECONDS",
                    os.getenv("AI_STREAM_ATTEMPT_TIMEOUT_SECONDS", "360"),
                )
            )
        ).strip()
        try:
            value = float(raw)
        except Exception:
            value = 360.0
        return max(0.0, value)

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


    def _wants_json_response(
        self,
        messages: List[Dict[str, Any]],
        response_mode: str = "auto",
    ) -> bool:
        normalized_mode = str(response_mode or "auto").strip().lower()
        if normalized_mode == "json":
            return True
        if normalized_mode == "text":
            return False
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

    def _prepare_request(
        self, messages: List[Dict[str, Any]], model: str, max_tokens: int | None = None,
        *, stream: bool = False, response_mode: str = "auto", request_timeout_seconds: float | None = None,
        heartbeat_interval_seconds: float | None = None, reasoning_effort: str | None = None,
        disable_thinking: bool = False,
    ) -> tuple[str, dict[str, Any], httpx.Timeout]:
        """只构造请求协议和诊断元数据，不发送网络请求。"""
        target_model = model or self.model
        configured_effort = getattr(self, "reasoning_effort", None) if target_model == self.model else None
        reasoning_effort = configured_effort or reasoning_effort
        if configured_effort:
            disable_thinking = False
        resolved_max_tokens = self._normalize_max_tokens(max_tokens, target_model)
        mode = str(response_mode or "auto").strip().lower()
        if mode not in {"auto", "json", "text"}:
            mode = "auto"
        wants_json = not stream and self._wants_json_response(messages, mode)
        payload: dict[str, Any] = {"model": target_model,
            "temperature": self._resolve_temperature(json_response=wants_json)}
        if stream:
            payload["stream"] = True
        if self.wire_api == "responses":
            path = "/responses"
            payload["input"] = self._messages_to_input(messages)
            if resolved_max_tokens:
                payload["max_output_tokens"] = resolved_max_tokens
        else:
            path = "/chat/completions"
            payload["messages"] = messages
            if resolved_max_tokens:
                payload["max_tokens"] = resolved_max_tokens
            if disable_thinking:
                payload["thinking"] = {"type": "disabled"}
            if wants_json:
                format_type = os.getenv("OPENAI_COMPAT_JSON_RESPONSE_FORMAT", "json_object").strip()
                if format_type and not getattr(self, "disable_json_response_format", False):
                    payload["response_format"] = {"type": format_type}
        if reasoning_effort:
            from core.ai.reasoning import reasoning_parameters
            payload.update(reasoning_parameters(reasoning_effort, self.wire_api))
            payload.pop("thinking", None)
        timeout = self._http_timeout(request_timeout_seconds)
        self.last_response_metadata = {"model": target_model, "wire_api": self.wire_api,
            "max_tokens": resolved_max_tokens, "stream": stream, "url_path": path,
            "response_mode": mode, "json_response": wants_json,
            "request_timeout_seconds": float(timeout.read or 0),
            "request_timeout_source": self._http_timeout_source(request_timeout_seconds),
            "reasoning_effort": payload.get("reasoning_effort"),
            "thinking": payload.get("thinking"), "response_format": payload.get("response_format")}
        if stream:
            try:
                heartbeat = max(0.0, float(heartbeat_interval_seconds or 0))
            except (TypeError, ValueError):
                heartbeat = 0.0
            self.last_response_metadata.update({
                "stream_attempt_timeout_seconds": self._stream_attempt_timeout_seconds(request_timeout_seconds),
                "stream_heartbeat_interval_seconds": heartbeat, "reasoning_chars": 0,
                "first_reasoning_ms": None, "first_content_ms": None, "total_duration_ms": 0,
                "content_len": 0})
        return self.base_url + path, payload, timeout

    def _parse_response(self, *, status_code: int, data: dict[str, Any] | None = None,
                        error_text: str = "", request_id: str | None = None) -> str:
        """只解释本次已结束的响应；失败或空结果都不能触发另一次请求。"""
        self.last_response_metadata["http_status"] = status_code
        if status_code == 200:
            data = data or {}
            self.last_response_metadata["usage"] = data.get("usage")
            self.last_response_metadata["provider_request_id"] = request_id
            self.last_response_metadata["provider_response_id"] = data.get("id")
            if self.wire_api == "responses":
                text = self._extract_responses_text(data)
                response_status = str(
                    data.get("status") or ""
                ).strip().lower()
                incomplete_details = data.get("incomplete_details")
                incomplete_reason = (
                    str(incomplete_details.get("reason") or "")
                    .strip()
                    .lower()
                    if isinstance(incomplete_details, dict)
                    else ""
                )
                finish_reason = (
                    "length"
                    if response_status == "incomplete"
                    and incomplete_reason == "max_output_tokens"
                    else ""
                )
                self.last_response_metadata.update(
                    {
                        "content_len": len(text or ""),
                        "raw_keys": list(data.keys())[:20],
                        "response_status": response_status,
                        "incomplete_reason": incomplete_reason,
                        "finish_reason": finish_reason,
                    }
                )
                # incomplete 是服务端已经结束的本次响应。即使没有局部文本，
                # 也必须把终止原因交给上层，不能再发起一份流式重复请求。
                if text or response_status == "incomplete":
                    return text
                # 空输出仍是本次真实响应，不能隐式再付费请求同一内容。
                return text
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
        self.last_response_metadata["error_preview"] = error_text[:500]
        return f"Error: HTTP {status_code} - {error_text}"

    def _parse_stream_lines(
        self, timed_lines: Iterable[tuple[float, str]], *, request_started_at: float, stream_started_at: float,
        stream_attempt_timeout: float, heartbeat_interval: float,
        clock: Callable[[], float] = time.perf_counter,
    ):
        """消费带时间戳的 SSE 行；解析、心跳和超时均可脱离 HTTP 独立验证。"""
        emitted_chars = 0
        reasoning_chars = 0
        first_reasoning_ms: float | None = None
        first_content_ms: float | None = None
        emitted_text = False
        last_heartbeat_at = stream_started_at
        try:
            for now, line in timed_lines:
                if stream_attempt_timeout > 0 and (now - stream_started_at) > stream_attempt_timeout:
                    timeout_seconds = int(stream_attempt_timeout)
                    self.last_response_metadata.update(
                        {
                            "exception_type": "StreamAttemptTimeout",
                            "exception": f"stream_attempt_timeout_after_{timeout_seconds}s",
                            "content_len": emitted_chars,
                            "stream_attempt_timeout_seconds": timeout_seconds,
                        }
                    )
                    yield f"Exception occurred: stream_attempt_timeout_after_{timeout_seconds}s"
                    return
                if (
                    heartbeat_interval > 0
                    and (now - last_heartbeat_at) >= heartbeat_interval
                ):
                    last_heartbeat_at = now
                    yield STREAM_HEARTBEAT_CHUNK
                if not line or line.strip() == "":
                    continue
                if line.startswith("data:"):
                    data_str = line[5:].lstrip()
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        usage = data.get("usage") or (data.get("response") or {}).get("usage")
                        if usage is not None:
                            self.last_response_metadata["usage"] = usage
                        response_id = (data.get("response") or {}).get("id") or data.get("id")
                        if response_id:
                            self.last_response_metadata["provider_response_id"] = response_id
                        if self.wire_api == "responses":
                            event_type = str(data.get("type") or "")
                            if "reasoning" in event_type:
                                reasoning = data.get("delta") or data.get("text") or ""
                                if reasoning:
                                    if first_reasoning_ms is None:
                                        first_reasoning_ms = max(
                                            0.0,
                                            (now - request_started_at) * 1000,
                                        )
                                    reasoning_chars += len(str(reasoning))
                                continue
                            if event_type == "response.output_text.delta":
                                delta = data.get("delta") or ""
                                if delta:
                                    if first_content_ms is None:
                                        first_content_ms = max(
                                            0.0,
                                            (now - request_started_at) * 1000,
                                        )
                                    emitted_text = True
                                    emitted_chars += len(str(delta))
                                    yield str(delta)
                            elif event_type == "response.output_text.done":
                                done_text = data.get("text") or ""
                                if done_text and not emitted_text:
                                    if first_content_ms is None:
                                        first_content_ms = max(
                                            0.0,
                                            (now - request_started_at) * 1000,
                                        )
                                    emitted_text = True
                                    emitted_chars += len(str(done_text))
                                    yield str(done_text)
                            elif event_type == "response.completed":
                                text = self._extract_responses_text(
                                    data.get("response") if isinstance(data.get("response"), dict) else data
                                )
                                if text and not emitted_text:
                                    if first_content_ms is None:
                                        first_content_ms = max(
                                            0.0,
                                            (now - request_started_at) * 1000,
                                        )
                                    emitted_text = True
                                    emitted_chars += len(str(text))
                                    yield text
                            elif event_type in {"error", "response.error"}:
                                self.last_response_metadata.update(
                                    {
                                        "error_preview": json.dumps(data, ensure_ascii=False)[:500],
                                        "content_len": emitted_chars,
                                    }
                                )
                                yield f"Error: {json.dumps(data, ensure_ascii=False)}"
                                return
                            continue

                        if "choices" in data and len(data["choices"]) > 0:
                            choice0 = data["choices"][0] or {}
                            delta = choice0.get("delta", {}) or {}
                            content = delta.get("content") or ""

                            reasoning = delta.get("reasoning_content") or ""
                            if reasoning:
                                if first_reasoning_ms is None:
                                    first_reasoning_ms = max(
                                        0.0,
                                        (now - request_started_at) * 1000,
                                    )
                                reasoning_chars += len(str(reasoning))

                            if content:
                                if first_content_ms is None:
                                    first_content_ms = max(
                                        0.0,
                                        (now - request_started_at) * 1000,
                                    )
                                emitted_text = True
                                emitted_chars += len(str(content))
                                yield content
                                continue

                            msg = choice0.get("message", {}) or {}
                            message_reasoning = msg.get("reasoning_content") or ""
                            if message_reasoning:
                                if first_reasoning_ms is None:
                                    first_reasoning_ms = max(
                                        0.0,
                                        (now - request_started_at) * 1000,
                                    )
                                reasoning_chars += len(str(message_reasoning))
                            msg_content = msg.get("content") or ""
                            if msg_content and not emitted_text:
                                if first_content_ms is None:
                                    first_content_ms = max(
                                        0.0,
                                        (now - request_started_at) * 1000,
                                    )
                                emitted_text = True
                                emitted_chars += len(str(msg_content))
                                yield msg_content
                                continue

                            text = choice0.get("text") or ""
                            if text and not emitted_text:
                                if first_content_ms is None:
                                    first_content_ms = max(
                                        0.0,
                                        (now - request_started_at) * 1000,
                                    )
                                emitted_text = True
                                emitted_chars += len(str(text))
                                yield text
                    except json.JSONDecodeError:
                        pass
        finally:
            self.last_response_metadata.update({
                "content_len": emitted_chars, "reasoning_chars": reasoning_chars,
                "first_reasoning_ms": first_reasoning_ms, "first_content_ms": first_content_ms,
                "total_duration_ms": max(0.0, (clock() - request_started_at) * 1000)})

    def generate(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None,
                 response_mode: str = "auto", request_timeout_seconds: float | None = None,
                 reasoning_effort: str | None = None, disable_thinking: bool = False) -> str:
        url, payload, timeout = self._prepare_request(messages, model, max_tokens,
            response_mode=response_mode, request_timeout_seconds=request_timeout_seconds,
            reasoning_effort=reasoning_effort, disable_thinking=disable_thinking)
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            with httpx.Client(**self._http_client_kwargs(timeout=timeout)) as client:
                response = client.post(url, headers=headers, json=payload)
                return self._parse_response(status_code=response.status_code,
                    data=response.json() if response.status_code == 200 else None,
                    error_text=response.text if response.status_code != 200 else "",
                    request_id=response.headers.get("x-request-id"))
        except Exception as error:
            self.last_response_metadata.update({"exception_type": type(error).__name__, "exception": str(error)[:500]})
            return f"Exception occurred: {error}"

    def generate_stream(self, messages: List[Dict[str, str]], model: str, max_tokens: Optional[int] = None,
                        request_timeout_seconds: Optional[float] = None,
                        heartbeat_interval_seconds: Optional[float] = None,
                        reasoning_effort: Optional[str] = None, disable_thinking: bool = False):
        request_started_at = time.perf_counter()
        url, payload, timeout = self._prepare_request(messages, model, max_tokens, stream=True,
            request_timeout_seconds=request_timeout_seconds, heartbeat_interval_seconds=heartbeat_interval_seconds,
            reasoning_effort=reasoning_effort, disable_thinking=disable_thinking)
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            with httpx.Client(**self._http_client_kwargs(timeout=timeout)) as client:
                with client.stream("POST", url, headers=headers, json=payload) as response:
                    self.last_response_metadata["http_status"] = response.status_code
                    self.last_response_metadata["provider_request_id"] = response.headers.get("x-request-id")
                    if response.status_code != 200:
                        yield self._parse_response(status_code=response.status_code,
                            error_text=response.read().decode(), request_id=response.headers.get("x-request-id"))
                        return
                    yield from self._parse_stream_lines(
                        ((time.perf_counter(), line) for line in response.iter_lines()),
                        request_started_at=request_started_at, stream_started_at=time.perf_counter(),
                        stream_attempt_timeout=self.last_response_metadata["stream_attempt_timeout_seconds"],
                        heartbeat_interval=self.last_response_metadata["stream_heartbeat_interval_seconds"])
        except Exception as error:
            self.last_response_metadata.update({"exception_type": type(error).__name__, "exception": str(error)[:500]})
            yield f"Exception occurred: {error}"
        finally:
            self.last_response_metadata["total_duration_ms"] = max(0.0, (time.perf_counter() - request_started_at) * 1000)

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
