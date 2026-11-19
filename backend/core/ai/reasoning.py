"""思考级别的服务端能力发现与请求参数，所有路由共用同一份契约。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from typing import Any, Literal

import httpx
from jose import JWTError, jwt

from core.ai.providers.openai_compatible_provider import OpenAICompatibleProvider
from core.ai.usage import normalized_usage

ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]
REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")


def _validation_signing_key() -> str:
    """从已有部署加密密钥派生专用凭证密钥，不采用开发环境公开的 JWT 默认值。"""

    secret = str(os.getenv("CONFIG_ENCRYPTION_KEY") or "").strip().strip('"\'')
    if not secret:
        raise RuntimeError("缺少部署加密密钥，不能签发模型配置验证凭证")
    return hmac.new(secret.encode("utf-8"), b"model-config-validation-v1", hashlib.sha256).hexdigest()


def reasoning_parameters(effort: str | None, wire_api: str) -> dict[str, Any]:
    """只发送用户选择且经过验证的级别，不叠加其他关闭思考参数。"""
    if not effort:
        return {}
    if effort not in REASONING_EFFORTS:
        raise ValueError("无效的思考级别")
    return {"reasoning": {"effort": effort}} if wire_api == "responses" else {"reasoning_effort": effort}


def config_validation_fingerprint(user_id: int, routes: list[dict[str, Any]]) -> str:
    """绑定真实请求配置与凭据，不将 API Key 放进响应或验证凭证。"""

    payload = json.dumps({"version": 1, "user_id": user_id, "routes": routes},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hmac.new(_validation_signing_key().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def sign_config_validation(*, user_id: int, fingerprint: str, details: dict[str, Any]) -> str:
    """验证与保存共享一小时有效的签名证据；保存配置不再次发起付费请求。"""

    now = datetime.now(timezone.utc)
    return jwt.encode({"purpose": "model-config-validation", "version": 1,
                       "user_id": user_id, "fingerprint": fingerprint, "details": details,
                       "iat": now, "exp": now + timedelta(hours=1)}, _validation_signing_key(), algorithm="HS256")


def read_config_validation(token: str | None, *, user_id: int,
                           fingerprint: str) -> dict[str, Any] | None:
    """只复用同用户、同配置且未过期的证据；失败不触发自动探测。"""

    if not isinstance(token, str) or not token:
        return None
    try:
        payload = jwt.decode(token, _validation_signing_key(), algorithms=["HS256"],
                             options={"require_exp": True, "require_iat": True})
    except JWTError:
        return None
    if (payload.get("purpose") != "model-config-validation" or payload.get("version") != 1
            or payload.get("user_id") != user_id
            or not hmac.compare_digest(str(payload.get("fingerprint") or ""), fingerprint)
            or not isinstance(payload.get("details"), dict)):
        return None
    return payload["details"]


def check_reasoning_selection(provider: OpenAICompatibleProvider, model: str,
                              effort: str | None, *,
                              vision_data_uri: str | None = None) -> dict[str, Any]:
    """仅验证选定参数一次；HTTP 接受不等同完整工具链或独立思考强度已验证。"""
    wire_api = provider.wire_api
    payload: dict[str, Any] = {"model": model, **reasoning_parameters(effort, wire_api)}
    content: Any = "hi"
    if wire_api == "responses":
        if vision_data_uri:
            content = [{"role": "user", "content": [
                {"type": "input_text", "text": "Read the visible text in this image."},
                {"type": "input_image", "image_url": vision_data_uri},
            ]}]
        payload.update(input=content, max_output_tokens=16)
        endpoint = "/responses"
    else:
        if vision_data_uri:
            content = [{"type": "text", "text": "Read the visible text in this image."},
                       {"type": "image_url", "image_url": {"url": vision_data_uri}}]
        payload.update(messages=[{"role": "user", "content": content}], max_tokens=16)
        endpoint = "/chat/completions"
    with httpx.Client(**provider._http_client_kwargs(timeout=provider._http_timeout(30))) as client:
        response = client.post(provider.base_url + endpoint,
                               headers={"Authorization": f"Bearer {provider.api_key}"}, json=payload)
    response.raise_for_status()
    try:
        body = response.json()
    except ValueError:
        body = {}
    return {"success": True,
            "error": None if response.is_success else f"模型请求失败（HTTP {response.status_code}）",
            "http_status": response.status_code, "request_id": response.headers.get("x-request-id"),
            "token_usage": normalized_usage(body.get("usage") if isinstance(body, dict) else None),
            "mode": "vision_request_accepted" if vision_data_uri else "text_request_accepted"}
