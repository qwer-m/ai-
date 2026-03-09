"""
配置中心路由。

职责：
1. 提供前端 API 配置弹窗所需的接口（当前配置、校验、保存、探测、流式测试、额度查询）。
2. 兼容历史前端仍在调用的 `/api/config/quota`，避免重构后出现 404。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.ai_client import DashScopeProvider, OpenAICompatibleProvider, ai_client
from core.auth import get_current_user
from core.config_manager import config_manager
from core.database import get_db
from core.models import SystemConfig, User
from core.utils import logger

router = APIRouter(prefix="/config", tags=["Config"])


class ConfigValidateRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model_name: str
    vl_model_name: Optional[str] = None
    turbo_model_name: Optional[str] = None


class ConfigSaveRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model_name: str
    vl_model_name: Optional[str] = None
    turbo_model_name: Optional[str] = None


class ConfigDetectRequest(BaseModel):
    candidates: List[str]


class ConfigQuotaRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model_name: Optional[str] = None


def _normalize_provider(provider: str) -> str:
    return (provider or "").strip().lower()


def _resolve_api_key(
    provider: str,
    submitted_api_key: Optional[str],
    active_config: Optional[SystemConfig],
) -> str:
    # 前端把“已保存密钥”显示成 ******，这里需要回退到数据库里的真实密钥。
    if submitted_api_key and submitted_api_key != "******":
        return submitted_api_key

    if active_config and _normalize_provider(active_config.provider) == provider and active_config.api_key:
        return config_manager.get_decrypted_api_key(active_config)

    return ""


def _resolve_base_url(
    provider: str,
    submitted_base_url: Optional[str],
    active_config: Optional[SystemConfig],
) -> Optional[str]:
    if submitted_base_url:
        return submitted_base_url
    if active_config and _normalize_provider(active_config.provider) == provider:
        return active_config.base_url
    if provider == "local":
        return "http://localhost:11434/v1"
    return submitted_base_url


def _build_provider(
    provider: str,
    api_key: str,
    base_url: Optional[str],
    model_name: Optional[str],
):
    if provider == "dashscope":
        return DashScopeProvider(api_key or "")

    if provider in {"openai", "ollama", "local"}:
        if not base_url:
            raise ValueError("base_url is required for openai-compatible providers")
        return OpenAICompatibleProvider(
            base_url=base_url,
            api_key=api_key or "",
            model=model_name or "",
        )

    raise ValueError(f"Unknown provider: {provider}")


def _extract_error_message(details: Dict[str, Any]) -> str:
    err = details.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err)
    if err:
        return str(err)
    return "Validation failed"


@router.get("/current")
async def get_current_config(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    config = config_manager.get_active_config(db, current_user.id)
    if not config:
        return {"active": False}

    return {
        "active": True,
        "provider": config.provider,
        "model_name": config.model_name,
        "vl_model_name": config.vl_model_name or "",
        "turbo_model_name": config.turbo_model_name or "",
        "base_url": config.base_url,
        "has_api_key": bool(config.api_key),
    }


@router.post("/validate")
async def validate_config(
    req: ConfigValidateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    provider = _normalize_provider(req.provider)
    active_config = config_manager.get_active_config(db, current_user.id)
    api_key = _resolve_api_key(provider, req.api_key, active_config)
    base_url = _resolve_base_url(provider, req.base_url, active_config)

    try:
        provider_client = _build_provider(provider, api_key, base_url, req.model_name)
    except Exception as e:
        return {"valid": False, "error": str(e)}

    try:
        if isinstance(provider_client, DashScopeProvider):
            details = provider_client.test_connection(model=req.model_name)
        else:
            details = provider_client.test_connection()
    except Exception as e:
        logger.error(f"Config validation exception: {e}")
        return {"valid": False, "error": f"Validation exception: {str(e)}"}

    if details.get("success"):
        return {"valid": True, "details": details}
    return {"valid": False, "error": _extract_error_message(details), "details": details}


@router.post("/save")
async def save_config(
    req: ConfigSaveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        active_config = config_manager.get_active_config(db, current_user.id)

        # api_key 为 None 表示前端未改动密钥，此时沿用已保存密钥。
        if req.api_key is None:
            resolved_api_key = _resolve_api_key(_normalize_provider(req.provider), None, active_config)
        else:
            resolved_api_key = req.api_key

        new_config = config_manager.create_config(
            db,
            provider=_normalize_provider(req.provider),
            model_name=req.model_name,
            vl_model_name=req.vl_model_name,
            turbo_model_name=req.turbo_model_name,
            api_key=resolved_api_key,
            base_url=req.base_url,
            activate=True,
            user_id=current_user.id,
        )

        # 保持与现有逻辑一致：更新全局客户端，避免旧模块读取到过期配置。
        new_client = ai_client.from_config(new_config)
        ai_client.update_provider(new_client.provider, new_client.model)

        return {"status": "success", "id": new_config.id}
    except Exception as e:
        logger.error(f"Config save error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


async def _probe_single_service(url: str) -> Dict[str, Any]:
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


@router.post("/detect")
async def detect_local_services(
    req: ConfigDetectRequest,
    current_user: User = Depends(get_current_user),
):
    _ = current_user  # 显式保留依赖：该接口需登录后使用。
    tasks = [_probe_single_service(url) for url in req.candidates]
    results = await asyncio.gather(*tasks)
    return {"services": [r for r in results if r.get("success")]}


@router.post("/quota")
async def get_quota(
    req: ConfigQuotaRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    历史兼容接口：前端额度环形图轮询用。
    若供应商不支持余额查询，返回 supported=false，避免前端出现 404 报红。
    """
    provider = _normalize_provider(req.provider)
    active_config = config_manager.get_active_config(db, current_user.id)
    api_key = _resolve_api_key(provider, req.api_key, active_config)
    base_url = _resolve_base_url(provider, req.base_url, active_config)

    try:
        provider_client = _build_provider(provider, api_key, base_url, req.model_name)
        result = provider_client.get_balance()
        if isinstance(result, dict):
            return result
        return {"supported": False, "message": "Balance API returned unexpected payload"}
    except Exception as e:
        logger.info(f"Quota check failed: {e}")
        return {"supported": False, "message": str(e)}


@router.get("/test-stream")
async def test_stream(
    provider: str,
    model: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    prompt: str = "Hi",
    db: Session = Depends(get_db),
):
    """
    SSE 流式测试接口。
    保持免认证，兼容前端 EventSource（其默认无法携带 Authorization 头）。
    """
    provider_name = _normalize_provider(provider)
    active_config = config_manager.get_active_config(db)
    resolved_api_key = _resolve_api_key(provider_name, api_key, active_config)
    resolved_base_url = _resolve_base_url(provider_name, base_url, active_config)
    resolved_model = model or (active_config.model_name if active_config else "")

    async def event_generator():
        try:
            provider_client = _build_provider(
                provider_name,
                resolved_api_key,
                resolved_base_url,
                resolved_model,
            )
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return

        try:
            iterator = provider_client.generate_stream(
                [{"role": "user", "content": prompt}],
                resolved_model,
                max_tokens=50,
            )
            for chunk in iterator:
                if chunk.startswith("Error:") or chunk.startswith("Exception"):
                    yield f"data: {json.dumps({'error': chunk})}\n\n"
                    return
                yield f"data: {json.dumps({'token': chunk})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
