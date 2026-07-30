from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Literal, Mapping


EnvelopeCallStatus = Literal[
    "response",
    "transport_exhausted",
    "fatal_model_error",
]
EnvelopeAttemptStatus = Literal[
    "response",
    "transient_transport_failure",
    "fatal_model_error",
]
ResponseTermination = Literal[
    "complete",
    "truncated",
    "incomplete",
    "unknown",
]

MAX_TRANSPORT_REPLAYS_PER_ENVELOPE = 1
_TRANSIENT_TRANSPORT_HTTP_STATUSES = {408, 502, 503, 504}
_ERROR_SECRET_PATTERNS = (
    re.compile(
        r"(?i)([\"']?\bauthorization\b[\"']?\s*[:=]\s*[\"']?(?:(?:bearer|basic)\s+)?)([^\"'\s,;}\]]+)"
    ),
    re.compile(r"(?i)(\bbearer\s+)([a-z0-9._~+/=-]{8,})"),
    re.compile(
        r"(?i)([\"']?\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|password)\b[\"']?\s*[:=]\s*[\"']?)([^\"'\s,;&;}\]]+)"
    ),
)
_OPENAI_STYLE_SECRET_PATTERN = re.compile(r"\bsk-[a-zA-Z0-9_-]{8,}\b")
_EXCEPTION_TYPE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")
_DIAGNOSTIC_METADATA_KEYS = (
    "model",
    "wire_api",
    "http_status",
    "exception_type",
    "request_timeout_seconds",
    "request_timeout_source",
    "response_mode",
    "json_response",
    "response_format",
    "json_compat_fallback",
    "reasoning_effort",
    "thinking",
    "finish_reason",
    "response_status",
    "incomplete_reason",
    "content_len",
    "reasoning_len",
    "cached",
    "max_tokens",
)


def strict_json_output_contract_prompt() -> str:
    """返回所有结构化模型阶段共享的严格 JSON 序列化契约。"""

    return r"""
Strict JSON output contract:
- Return exactly one minified RFC 8259 JSON value matching the stage-declared response grammar. Do not add markdown, code fences, comments, explanations, or leading/trailing text.
- Serialize every string value as JSON, including text copied or transformed from the input. Use U+0022 only as a JSON string delimiter; inside string content, escape it as \" or use U+201C/U+201D for natural-language semantic quotation.
- Escape reverse solidus and every U+0000 through U+001F control character exactly as RFC 8259 requires. Never place a raw control character inside a string.
- Emit only finite JSON numbers. Never emit NaN, Infinity, or -Infinity.
- Immediately before returning, validate the exact final output as one complete RFC 8259 JSON value. Do not return JSON-looking text that fails parsing.
""".strip()


def _text(value: Any) -> str:
    return str(value or "").strip()


def safe_error_preview(value: Any, *, limit: int) -> str:
    """生成可落诊断的脱敏错误摘要。"""

    text = _text(value)
    for pattern in _ERROR_SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    text = _OPENAI_STYLE_SECRET_PATTERN.sub("[REDACTED]", text)
    return text[: max(0, int(limit))]


def is_transport_timeout(*, metadata: Mapping[str, Any], error: Any) -> bool:
    """识别 HTTP 网关超时和客户端传输超时。"""

    try:
        http_status = int(metadata.get("http_status") or 0)
    except (TypeError, ValueError):
        http_status = 0
    if http_status == 504:
        return True
    exception_type = _text(metadata.get("exception_type")).lower()
    if exception_type.endswith("timeout") or exception_type == "timeouterror":
        return True
    error_text = _text(error).lower()
    return any(
        marker in error_text
        for marker in (
            "read operation timed out",
            "readtimeout",
            "connecttimeout",
            "writetimeout",
            "pooltimeout",
            "gateway time-out",
            "gateway timeout",
        )
    )


def is_transient_transport_failure(
    *,
    metadata: Mapping[str, Any],
    error: Any,
) -> bool:
    """只识别可安全重放同一请求的传输失败。"""

    if is_transport_timeout(metadata=metadata, error=error):
        return True
    try:
        http_status = int(metadata.get("http_status") or 0)
    except (TypeError, ValueError):
        http_status = 0
    if http_status in _TRANSIENT_TRANSPORT_HTTP_STATUSES:
        return True
    error_text = _text(error).lower()
    return any(
        marker in error_text
        for marker in (
            "bad gateway",
            "service unavailable",
        )
    )


def _diagnostic_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    output = {
        key: metadata[key]
        for key in _DIAGNOSTIC_METADATA_KEYS
        if key not in {"http_status", "exception_type"}
        and metadata.get(key) not in (None, "")
    }
    raw_http_status = metadata.get("http_status")
    try:
        http_status = (
            0 if isinstance(raw_http_status, bool) else int(raw_http_status or 0)
        )
    except (TypeError, ValueError):
        http_status = 0
    if 100 <= http_status <= 599:
        output["http_status"] = http_status

    exception_type = _text(metadata.get("exception_type"))
    if _EXCEPTION_TYPE_PATTERN.fullmatch(exception_type):
        output["exception_type"] = exception_type
    return output


def _client_response_metadata(client: Any) -> dict[str, Any]:
    metadata = getattr(client, "last_response_metadata", {})
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def classify_response_termination(
    metadata: Mapping[str, Any] | None,
) -> ResponseTermination:
    """统一识别模型响应是否完整；显式不完整信号优先于完成信号。"""

    source = metadata if isinstance(metadata, Mapping) else {}
    finish_reason = _text(source.get("finish_reason")).lower()
    response_status = _text(source.get("response_status")).lower()
    incomplete_reason = _text(source.get("incomplete_reason")).lower()

    if finish_reason == "length" or incomplete_reason == "max_output_tokens":
        return "truncated"
    if (
        response_status == "incomplete"
        or bool(incomplete_reason)
        or (finish_reason and finish_reason != "stop")
        or (response_status and response_status != "completed")
    ):
        return "incomplete"
    if finish_reason == "stop" or response_status == "completed":
        return "complete"
    return "unknown"


def _is_model_error_response(raw_text: str) -> bool:
    return bool(
        not raw_text
        or raw_text.startswith("Error:")
        or raw_text.startswith("Exception")
    )


def _normalize_replay_limit(value: Any) -> int:
    try:
        replay_limit = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_transport_replays 必须是整数") from exc
    if replay_limit < 0 or replay_limit > MAX_TRANSPORT_REPLAYS_PER_ENVELOPE:
        raise ValueError(
            "每个模型请求 envelope 最多允许一次 transport replay"
        )
    return replay_limit


@dataclass(frozen=True)
class EnvelopeCallAttempt:
    call_index: int
    replay_index: int
    status: EnvelopeAttemptStatus
    duration_ms: int
    timed_out: bool
    transient_transport_failure: bool
    retry_scheduled: bool
    error_preview: str
    metadata: dict[str, Any]

    def to_diagnostic(self) -> dict[str, Any]:
        return {
            "call_index": int(self.call_index),
            "replay_index": int(self.replay_index),
            "status": self.status,
            "duration_ms": int(self.duration_ms),
            "timed_out": bool(self.timed_out),
            "transient_transport_failure": bool(
                self.transient_transport_failure
            ),
            "retry_scheduled": bool(self.retry_scheduled),
            "error_preview": self.error_preview,
            **dict(self.metadata),
        }


@dataclass(frozen=True)
class EnvelopeCallResult:
    envelope_id: str
    status: EnvelopeCallStatus
    raw_text: str
    response_metadata: dict[str, Any]
    attempts: tuple[EnvelopeCallAttempt, ...]
    cache_lookup_enabled: bool
    physical_call_count: int
    transport_failure_count: int
    transport_retry_count: int

    @property
    def cache_hit_count(self) -> int:
        if not self.cache_lookup_enabled:
            return 0
        return sum(
            int(attempt.metadata.get("cached") is True)
            for attempt in self.attempts
        )

    @property
    def cache_miss_count(self) -> int:
        if not self.cache_lookup_enabled:
            return 0
        return max(0, int(self.physical_call_count) - self.cache_hit_count)

    @property
    def cache_bypass_count(self) -> int:
        return 0 if self.cache_lookup_enabled else int(self.physical_call_count)

    @property
    def provider_call_count(self) -> int:
        """真实进入 provider 的调用数；缓存命中不计入。"""
        return max(0, int(self.physical_call_count) - self.cache_hit_count)

    def to_diagnostic(self) -> dict[str, Any]:
        return {
            "envelope_id": self.envelope_id,
            "status": self.status,
            "physical_call_count": int(self.physical_call_count),
            "provider_call_count": int(self.provider_call_count),
            "cache_hit_count": int(self.cache_hit_count),
            "cache_miss_count": int(self.cache_miss_count),
            "cache_bypass_count": int(self.cache_bypass_count),
            "transport_failure_count": int(self.transport_failure_count),
            "transport_retry_count": int(self.transport_retry_count),
            "attempts": [item.to_diagnostic() for item in self.attempts],
        }


def invoke_model_envelope(
    *,
    client: Any,
    envelope_id: str,
    user_input: str,
    system_prompt: str | None,
    db: Any,
    max_tokens: int,
    task_type: str,
    request_timeout_seconds: float,
    max_transport_replays: int = MAX_TRANSPORT_REPLAYS_PER_ENVELOPE,
) -> EnvelopeCallResult:
    """调用一份不可变模型请求，并在传输失败时原样重放一次。"""

    if client is None or not hasattr(client, "generate_response"):
        raise ValueError("client 必须提供 generate_response")
    stable_envelope_id = _text(envelope_id)
    if not stable_envelope_id:
        raise ValueError("envelope_id 不能为空")
    if not isinstance(user_input, str):
        raise TypeError("user_input 必须是字符串")
    if system_prompt is not None and not isinstance(system_prompt, str):
        raise TypeError("system_prompt 必须是字符串或 None")
    replay_limit = _normalize_replay_limit(max_transport_replays)

    # 调用参数只构造一次，transport replay 不允许改变 prompt 或调用策略。
    call_kwargs = {
        "db": db,
        "max_tokens": int(max_tokens),
        "task_type": str(task_type),
        "request_timeout_seconds": float(request_timeout_seconds),
    }
    attempts: list[EnvelopeCallAttempt] = []
    transport_failure_count = 0
    transport_retry_count = 0

    for replay_index in range(replay_limit + 1):
        if replay_index > 0:
            # 只统计已经实际开始的重放调用，不统计尚未执行的调度意图。
            transport_retry_count += 1
        call_started = time.perf_counter()
        caught_error: Exception | None = None
        raw_text = ""
        try:
            raw = client.generate_response(
                user_input,
                system_prompt,
                **call_kwargs,
            )
            raw_text = _text(raw)
        except Exception as exc:  # noqa: BLE001 - 统一模型客户端边界
            caught_error = exc
        duration_ms = int(max(0.0, time.perf_counter() - call_started) * 1000)

        metadata = _client_response_metadata(client)
        if caught_error is not None:
            metadata["exception_type"] = type(caught_error).__name__
        diagnostic_metadata = _diagnostic_metadata(metadata)
        response_termination = classify_response_termination(metadata)
        output_not_complete = response_termination in {
            "truncated",
            "incomplete",
        }
        is_model_error = caught_error is not None or (
            _is_model_error_response(raw_text) and not output_not_complete
        )
        if not is_model_error:
            attempts.append(
                EnvelopeCallAttempt(
                    call_index=len(attempts) + 1,
                    replay_index=replay_index,
                    status="response",
                    duration_ms=duration_ms,
                    timed_out=False,
                    transient_transport_failure=False,
                    retry_scheduled=False,
                    error_preview="",
                    metadata=diagnostic_metadata,
                )
            )
            return EnvelopeCallResult(
                envelope_id=stable_envelope_id,
                status="response",
                raw_text=raw_text,
                response_metadata=diagnostic_metadata,
                attempts=tuple(attempts),
                cache_lookup_enabled=db is not None,
                physical_call_count=len(attempts),
                transport_failure_count=transport_failure_count,
                transport_retry_count=transport_retry_count,
            )

        provider_error = _text(metadata.get("error_preview"))
        error: Any = caught_error or raw_text or provider_error or "empty_model_response"
        timed_out = is_transport_timeout(metadata=metadata, error=error)
        transient_failure = is_transient_transport_failure(
            metadata=metadata,
            error=error,
        )
        if transient_failure:
            transport_failure_count += 1
        retry_scheduled = bool(
            transient_failure and replay_index < replay_limit
        )
        attempts.append(
            EnvelopeCallAttempt(
                call_index=len(attempts) + 1,
                replay_index=replay_index,
                status=(
                    "transient_transport_failure"
                    if transient_failure
                    else "fatal_model_error"
                ),
                duration_ms=duration_ms,
                timed_out=timed_out,
                transient_transport_failure=transient_failure,
                retry_scheduled=retry_scheduled,
                # 错误正文可能回显请求或文档内容，诊断只保留状态机码和安全元数据。
                error_preview="",
                metadata=diagnostic_metadata,
            )
        )
        if retry_scheduled:
            continue
        return EnvelopeCallResult(
            envelope_id=stable_envelope_id,
            status=(
                "transport_exhausted"
                if transient_failure
                else "fatal_model_error"
            ),
            raw_text=raw_text,
            response_metadata=diagnostic_metadata,
            attempts=tuple(attempts),
            cache_lookup_enabled=db is not None,
            physical_call_count=len(attempts),
            transport_failure_count=transport_failure_count,
            transport_retry_count=transport_retry_count,
        )

    raise AssertionError("模型 envelope 调用状态机未返回结果")


__all__ = [
    "EnvelopeCallAttempt",
    "EnvelopeCallResult",
    "MAX_TRANSPORT_REPLAYS_PER_ENVELOPE",
    "ResponseTermination",
    "classify_response_termination",
    "invoke_model_envelope",
    "is_transient_transport_failure",
    "is_transport_timeout",
    "safe_error_preview",
    "strict_json_output_contract_prompt",
]
