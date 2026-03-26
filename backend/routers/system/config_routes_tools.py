from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import User
from core.processing.utils import logger
from core.settings.config_manager import config_manager
from routers.system.config_helpers import (
    build_provider,
    normalize_provider,
    probe_single_service,
    resolve_api_key,
    resolve_base_url,
)
from routers.system.config_models import ConfigDetectRequest, ConfigQuotaRequest

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


@router.get("/test-stream")
async def test_stream(
    provider: str,
    model: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    prompt: str = "Hi",
    db: Session = Depends(get_db),
):
    provider_name = normalize_provider(provider)
    active_config = config_manager.get_active_config(db)
    resolved_api_key = resolve_api_key(provider_name, api_key, active_config)
    resolved_base_url = resolve_base_url(provider_name, base_url, active_config)
    resolved_model = model or (active_config.model_name if active_config else "")

    async def event_generator():
        try:
            provider_client = build_provider(
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
