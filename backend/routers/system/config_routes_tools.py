from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.concurrency import iterate_in_threadpool

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.model_defs import User
from core.processing.utils import logger
from core.settings.config_manager import config_manager
from routers.system.config_helpers import (
    build_provider,
    normalize_provider,
    probe_single_service,
    resolve_api_key,
    resolve_base_url,
)
from routers.system.config_models import (
    ConfigDetectRequest,
    ConfigQuotaRequest,
    ConfigTestStreamRequest,
)

router = APIRouter()


@router.post("/detect")
async def detect_local_services(
    req: ConfigDetectRequest,
    current_user: User = Depends(get_current_user),
):
    _ = current_user
    tasks = [probe_single_service(url) for url in req.candidates]
    results = await asyncio.gather(*tasks)
    return {"services": [r for r in results if r.get("success")]}


@router.post("/quota")
async def get_quota(
    req: ConfigQuotaRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    provider = normalize_provider(req.provider)
    active_config = config_manager.get_active_config(db, current_user.id)
    api_key = resolve_api_key(provider, req.api_key, active_config)
    base_url = resolve_base_url(provider, req.base_url, active_config)

    try:
        provider_client = build_provider(provider, api_key, base_url, req.model_name)
        result = provider_client.get_balance()
        if isinstance(result, dict):
            return result
        return {"supported": False, "message": "Balance API returned unexpected payload"}
    except Exception as e:
        logger.info(f"Quota check failed: {e}")
        return {"supported": False, "message": str(e)}


@router.post("/test-stream")
async def test_stream(
    req: ConfigTestStreamRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    provider_name = normalize_provider(req.provider)
    active_config = config_manager.get_active_config(db, current_user.id)
    resolved_api_key = resolve_api_key(provider_name, req.api_key, active_config)
    resolved_base_url = resolve_base_url(provider_name, req.base_url, active_config)

    async def event_generator():
        try:
            provider_client = build_provider(
                provider_name,
                resolved_api_key,
                resolved_base_url,
                req.model_name,
            )
        except Exception as e:
            yield json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False) + "\n"
            return

        try:
            iterator = provider_client.generate_stream(
                [{"role": "user", "content": req.prompt}],
                req.model_name,
                max_tokens=50,
            )
            received_content = False
            async for chunk in iterate_in_threadpool(iter(iterator)):
                if not isinstance(chunk, str):
                    raise TypeError("模型流响应必须为字符串")
                if chunk.startswith("Error:") or chunk.startswith("Exception"):
                    yield json.dumps({"type": "error", "message": chunk}, ensure_ascii=False) + "\n"
                    return
                received_content = received_content or bool(chunk)
                yield json.dumps({"type": "token", "token": chunk}, ensure_ascii=False) + "\n"
            if not received_content:
                yield json.dumps(
                    {"type": "error", "message": "模型流未返回可显示内容"},
                    ensure_ascii=False,
                ) + "\n"
                return
            yield json.dumps({"type": "done"}, ensure_ascii=False) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False) + "\n"

    return StreamingResponse(
        event_generator(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
