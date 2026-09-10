from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import base64
import io
from typing import Any, Dict, List, Optional

import httpx
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from core.ai.ai_client import ai_client
from core.ai.providers import DashScopeProvider, OpenAICompatibleProvider
from core.authn.security import config_encryption
from core.settings.config_manager import config_manager
from core.db.model_defs import SystemConfig
from core.processing.utils import logger

from routers.system.config_helpers_split_helpers import (
    normalize_provider,
    default_base_url,
    normalize_metadata,
    extract_target_nodes,
    normalize_tesseract_path,
    iter_tesseract_candidates,
    auto_detect_tesseract_path,
    validate_tesseract_path,
    decrypt_stored_key,
)

def resolve_target_saved_config(
    active_config: Optional[SystemConfig],
    target_key: str,
) -> dict[str, Any]:
    meta = extract_target_nodes(normalize_metadata(active_config))
    node = meta.get(target_key) if isinstance(meta, dict) else {}
    if not isinstance(node, dict):
        return {}
    return {
        "follow_main": bool(node.get("follow_main", True)),
        "provider": normalize_provider(str(node.get("provider") or "")),
        "api_key": decrypt_stored_key(node.get("api_key")),
        "base_url": str(node.get("base_url") or "").strip() or None,
    }


def resolve_api_key(
    provider: str,
    submitted_api_key: Optional[str],
    active_config: Optional[SystemConfig],
) -> str:
    if submitted_api_key and submitted_api_key != "******":
        return submitted_api_key

    if active_config and normalize_provider(active_config.provider) == provider and active_config.api_key:
        return config_manager.get_decrypted_api_key(active_config)

    return ""


def resolve_base_url(
    provider: str,
    submitted_base_url: Optional[str],
    active_config: Optional[SystemConfig],
) -> Optional[str]:
    if submitted_base_url:
        return submitted_base_url
    if active_config and normalize_provider(active_config.provider) == provider:
        if active_config.base_url:
            return active_config.base_url
    return default_base_url(provider)


def resolve_target_credentials(
    *,
    target_key: str,
    follow_main: bool,
    submitted_provider: Optional[str],
    submitted_api_key: Optional[str],
    submitted_base_url: Optional[str],
    main_provider: str,
    main_api_key: str,
    main_base_url: Optional[str],
    active_config: Optional[SystemConfig],
) -> tuple[str, str, Optional[str], bool]:
    if follow_main:
        return main_provider, main_api_key, main_base_url, True

    provider = normalize_provider(submitted_provider or "")
    if not provider:
        raise ValueError(f"{target_key} provider is required when not following main model")

    saved = resolve_target_saved_config(active_config, target_key)
    submitted = (submitted_api_key or "").strip()
    if submitted and submitted != "******":
        api_key = submitted
    elif saved.get("provider") == provider and saved.get("api_key"):
        api_key = str(saved.get("api_key"))
    elif provider == main_provider:
        api_key = main_api_key
    else:
        api_key = ""

    base_url = (
        (submitted_base_url or "").strip()
        or (str(saved.get("base_url") or "").strip() if saved.get("provider") == provider else "")
        or (main_base_url if provider == main_provider else "")
        or default_base_url(provider)
    )
    return provider, api_key, base_url or None, False


def encrypt_key_for_storage(raw_key: str) -> str:
    value = (raw_key or "").strip()
    if not value:
        return ""
    return config_encryption.encrypt(value)


def build_provider(
    provider: str,
    api_key: str,
    base_url: Optional[str],
    model_name: Optional[str],
):
    if provider == "dashscope":
        return DashScopeProvider(api_key or "")

    if provider in {"openai", "ollama", "local", "deepseek"}:
        if not base_url:
            raise ValueError("base_url is required for openai-compatible providers")
        return OpenAICompatibleProvider(
            base_url=base_url,
            api_key=api_key or "",
            model=model_name or "",
        )

    raise ValueError(f"Unknown provider: {provider}")


def extract_error_message(details: Dict[str, Any]) -> str:
    err = details.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err)
    if err:
        return str(err)
    return "Validation failed"


def localize_model_error(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "验证失败"

    replacements: list[tuple[str, str]] = [
        (r"(?i)OCR Error:", "OCR错误："),
        (r"(?i)OCR Exception:", "OCR异常："),
        (r"(?i)InvalidParameter", "参数错误"),
        (r"(?i)InternalError\.Algo\.InvalidParameter", "内部算法参数错误"),
        (r"(?i)The provided URL does not appear to be valid\.", "提供的URL看起来无效。"),
        (r"(?i)Ensure it is correctly formatted\.", "请确认URL格式正确。"),
        (r"(?i)url error,\s*please check url!?", "URL地址错误，请检查链接是否可访问。"),
        (r"(?i)For details,\s*see:\s*https?://\S+", "详情请参考阿里云错误码文档。"),
        (
            r"(?i)content parameter's length invalid,\s*please check the request parameters\.?",
            "content参数长度不合法，请检查请求参数。",
        ),
        (
            r"(?i)Requests rate limit exceeded,\s*please try again later\.?",
            "请求频率超限，请稍后重试。",
        ),
        (r"(?i)authentication failed", "鉴权失败"),
        (r"(?i)invalid api key", "API Key无效"),
        (r"(?i)model not found", "模型不存在"),
        (r"(?i)insufficient_quota", "余额不足"),
        (r"(?i)quota exceeded", "配额已超限"),
        (r"(?i)rate limit", "请求频率超限"),
        (r"<\s*400\s*>", "(HTTP 400)"),
    ]

    localized = text
    for pattern, target in replacements:
        localized = re.sub(pattern, target, localized)
    return localized


def is_failure_text(text: str) -> bool:
    value = (text or "").strip()
    if not value:
        return True
    lowered = value.lower()
    if lowered.startswith(("error", "exception", "ocr error", "ocr exception")):
        return True
    markers = (
        "额度耗尽",
        "免费额度已用完",
        "余额不足",
        "insufficient_quota",
        "quota exceeded",
        "rate limit",
        "authentication failed",
        "invalid api key",
        "model not found",
    )
    return any(marker in value or marker in lowered for marker in markers)


def validate_text_model(
    provider: str,
    api_key: str,
    base_url: Optional[str],
    model_name: str,
    label: str,
) -> Dict[str, Any]:
    started = time.time()
    try:
        client = build_provider(provider, api_key, base_url, model_name)
        if isinstance(client, DashScopeProvider):
            # Text input box validation strategy:
            # 1) validate as text model first
            # 2) if it fails, validate as multimodal with text-only payload
            details = client.test_connection_text_then_multimodal(model=model_name)
        else:
            details = client.test_connection()
        success = bool(details.get("success"))
        detected_mode = str((details.get("model_info") or {}).get("mode") or "")
        return {
            "type": "text",
            "label": label,
            "model": model_name,
            "success": success,
            "latency": round((time.time() - started) * 1000, 2),
            "error": None if success else extract_error_message(details),
            "mode": detected_mode,
            "details": details,
        }
    except Exception as e:
        return {
            "type": "text",
            "label": label,
            "model": model_name,
            "success": False,
            "latency": round((time.time() - started) * 1000, 2),
            "error": str(e),
            "details": {},
        }


def _build_vision_probe_data_uri() -> str:
    try:
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (260, 96), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((8, 8, 252, 88), outline="black", width=2)
        draw.text((24, 34), "OCR OK", fill="black")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        payload = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
            "/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
        )
    return f"data:image/png;base64,{payload}"


def validate_vision_model(
    provider: str,
    api_key: str,
    base_url: Optional[str],
    model_name: str,
    label: str,
) -> Dict[str, Any]:
    started = time.time()
    try:
        client = build_provider(provider, api_key, base_url, model_name)
        probe_messages = [
            {
                "role": "user",
                "content": [
                    {"image": _build_vision_probe_data_uri()},
                    {"text": "Read the text in this image. Reply with only the visible text."},
                ],
            }
        ]
        result = client.multimodal_generate(probe_messages, model_name)
        success = not is_failure_text(result)
        error = None if success else (result or "Vision model validation failed")
        return {
            "type": "vision",
            "label": label,
            "model": model_name,
            "success": success,
            "latency": round((time.time() - started) * 1000, 2),
            "error": error,
            "sample_response": (result or "")[:200],
            "mode": "multimodal_image_probe",
        }
    except Exception as e:
        return {
            "type": "vision",
            "label": label,
            "model": model_name,
            "success": False,
            "latency": round((time.time() - started) * 1000, 2),
            "error": str(e),
            "sample_response": "",
        }


async def probe_single_service(url: str) -> Dict[str, Any]:
    start = time.time()
    target = url.rstrip("/")
    if not target.endswith("/v1"):
        target += "/v1"

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            for endpoint in (f"{target}/models", target):
                try:
                    resp = await client.get(endpoint)
                    if 200 <= resp.status_code < 300:
                        models: List[Dict[str, Any]] = []
                        try:
                            data = resp.json()
                            if isinstance(data, dict) and isinstance(data.get("data"), list):
                                models = [m for m in data["data"] if isinstance(m, dict)]
                        except Exception:
                            pass
                        return {
                            "url": url,
                            "success": True,
                            "latency": round((time.time() - start) * 1000, 2),
                            "models": models,
                        }
                except Exception:
                    continue
    except Exception as e:
        return {"url": url, "success": False, "error": str(e)}

    return {"url": url, "success": False, "error": "Not reachable"}


def save_and_update_ai_client(db: Session, config) -> dict[str, Any]:
    new_client = ai_client.from_config(config)
    ai_client.replace_runtime_from(new_client)
    return {"provider": new_client.provider, "model": new_client.model}


def build_internal_error(e: Exception):
    logger.error(f"Config save error: {e}")
    return JSONResponse(status_code=500, content={"error": str(e)})
