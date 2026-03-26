from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import User
from core.settings.config_manager import config_manager
from routers.system.config_helpers import (
    auto_detect_tesseract_path,
    build_internal_error,
    build_provider,
    encrypt_key_for_storage,
    extract_target_nodes,
    localize_model_error,
    normalize_metadata,
    normalize_provider,
    normalize_tesseract_path,
    resolve_api_key,
    resolve_base_url,
    resolve_target_credentials,
    save_and_update_ai_client,
    validate_text_model,
    validate_tesseract_path,
    validate_vision_model,
)
from routers.system.config_models import ConfigSaveRequest, ConfigValidateRequest

router = APIRouter()


@router.get("/current")
async def get_current_config(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    config = config_manager.get_active_config(db, current_user.id)
    if not config:
        return {"active": False}

    meta = normalize_metadata(config)
    tesseract_manual_override = bool(meta.get("tesseract_manual_override", False))
    tesseract_path = normalize_tesseract_path(meta.get("tesseract_path"))
    ocr_auto_detected = False
    ocr_auto_detect_message = ""
    if not tesseract_manual_override and not tesseract_path:
        detected_path = auto_detect_tesseract_path()
        if detected_path:
            tesseract_path = detected_path
            ocr_auto_detected = True
            meta = {
                **meta,
                "tesseract_path": tesseract_path,
                "tesseract_manual_override": False,
                "tesseract_auto_detected_at": int(time.time()),
            }
            try:
                config.metadata_info = meta
                db.add(config)
                db.commit()
                db.refresh(config)
            except Exception:
                db.rollback()
        else:
            ocr_auto_detect_message = "未检测到本地 OCR 引擎，建议使用云端 OCR 或安装本地 OCR 模块后重试。"

    target_nodes = extract_target_nodes(meta)
    turbo_node = target_nodes.get("turbo") if isinstance(target_nodes.get("turbo"), dict) else {}
    vision_node = target_nodes.get("vision") if isinstance(target_nodes.get("vision"), dict) else {}
    turbo_follow_main = bool(turbo_node.get("follow_main", True))
    vl_follow_main = bool(vision_node.get("follow_main", True))

    return {
        "active": True,
        "provider": config.provider,
        "model_name": config.model_name,
        "vl_model_name": config.vl_model_name or "",
        "turbo_model_name": config.turbo_model_name or "",
        "base_url": config.base_url,
        "has_api_key": bool(config.api_key),
        "turbo_provider": str(turbo_node.get("provider") or ""),
        "turbo_base_url": str(turbo_node.get("base_url") or ""),
        "turbo_follow_main": turbo_follow_main,
        "has_turbo_api_key": bool(turbo_node.get("api_key")) and not turbo_follow_main,
        "vl_provider": str(vision_node.get("provider") or ""),
        "vl_base_url": str(vision_node.get("base_url") or ""),
        "vl_follow_main": vl_follow_main,
        "has_vl_api_key": bool(vision_node.get("api_key")) and not vl_follow_main,
        "tesseract_path": tesseract_path,
        "tesseract_manual_override": tesseract_manual_override,
        "ocr_auto_detected": ocr_auto_detected,
        "ocr_auto_detect_message": ocr_auto_detect_message,
    }


@router.post("/ocr/auto-detect")
async def auto_detect_ocr_path(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    detected_path = auto_detect_tesseract_path()
    if not detected_path:
        return {
            "found": False,
            "path": "",
            "validated": False,
            "persisted": False,
            "message": "未检测到本地 OCR 引擎，建议改用云端 OCR 或先安装本地 OCR 模块（Tesseract）。",
        }

    check = validate_tesseract_path(detected_path)
    if not check.get("success"):
        return {
            "found": False,
            "path": detected_path,
            "validated": False,
            "persisted": False,
            "message": localize_model_error(str(check.get("error") or "OCR路径验证失败")),
            "check": check,
        }

    persisted = False
    config = config_manager.get_active_config(db, current_user.id)
    if config:
        meta = normalize_metadata(config)
        meta = {
            **meta,
            "tesseract_path": detected_path,
            "tesseract_manual_override": False,
            "tesseract_auto_detected_at": int(time.time()),
        }
        try:
            config.metadata_info = meta
            db.add(config)
            db.commit()
            db.refresh(config)
            persisted = True
        except Exception:
            db.rollback()

    return {
        "found": True,
        "path": detected_path,
        "validated": True,
        "persisted": persisted,
        "message": "已自动检索并通过本地 OCR 路径验证。",
        "check": check,
    }


@router.post("/validate")
async def validate_config(
    req: ConfigValidateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    provider = normalize_provider(req.provider)
    active_config = config_manager.get_active_config(db, current_user.id)
    api_key = resolve_api_key(provider, req.api_key, active_config)
    base_url = resolve_base_url(provider, req.base_url, active_config)

    try:
        build_provider(provider, api_key, base_url, req.model_name)
    except Exception as e:
        return {"valid": False, "error": str(e)}

    checks: list[dict[str, Any]] = []
    checks.append(validate_text_model(provider, api_key, base_url, req.model_name, "文本模型"))

    tesseract_manual_override = bool(req.tesseract_manual_override)
    effective_tesseract_path = normalize_tesseract_path(req.tesseract_path)
    ocr_auto_detected_path = ""
    if not tesseract_manual_override and not effective_tesseract_path:
        ocr_auto_detected_path = auto_detect_tesseract_path()
        if ocr_auto_detected_path:
            effective_tesseract_path = ocr_auto_detected_path

    ocr_check = validate_tesseract_path(effective_tesseract_path)
    if not ocr_check.get("success") and not effective_tesseract_path:
        ocr_check["error"] = "未检测到本地 OCR 引擎，请使用云端 OCR 或安装本地 OCR 模块（Tesseract）。"
    checks.append(ocr_check)

    turbo_name = (req.turbo_model_name or "").strip()
    if turbo_name:
        try:
            turbo_provider, turbo_key, turbo_base_url, turbo_follow_main = resolve_target_credentials(
                target_key="turbo",
                follow_main=bool(req.turbo_follow_main if req.turbo_follow_main is not None else True),
                submitted_provider=req.turbo_provider,
                submitted_api_key=req.turbo_api_key,
                submitted_base_url=req.turbo_base_url,
                main_provider=provider,
                main_api_key=api_key,
                main_base_url=base_url,
                active_config=active_config,
            )
            turbo_check = validate_text_model(
                turbo_provider,
                turbo_key,
                turbo_base_url,
                turbo_name,
                "压缩模型",
            )
            turbo_check["follow_main"] = turbo_follow_main
            turbo_check["provider"] = turbo_provider
            checks.append(turbo_check)
        except Exception as e:
            checks.append(
                {
                    "type": "text",
                    "label": "压缩模型",
                    "model": turbo_name,
                    "success": False,
                    "latency": 0,
                    "error": str(e),
                }
            )

    vl_name = (req.vl_model_name or "").strip()
    if vl_name:
        try:
            vl_provider, vl_key, vl_base_url, vl_follow_main = resolve_target_credentials(
                target_key="vision",
                follow_main=bool(req.vl_follow_main if req.vl_follow_main is not None else True),
                submitted_provider=req.vl_provider,
                submitted_api_key=req.vl_api_key,
                submitted_base_url=req.vl_base_url,
                main_provider=provider,
                main_api_key=api_key,
                main_base_url=base_url,
                active_config=active_config,
            )
            vl_check = validate_vision_model(
                vl_provider,
                vl_key,
                vl_base_url,
                vl_name,
                "图像模型",
            )
            vl_check["follow_main"] = vl_follow_main
            vl_check["provider"] = vl_provider
            checks.append(vl_check)
        except Exception as e:
            checks.append(
                {
                    "type": "vision",
                    "label": "图像模型",
                    "model": vl_name,
                    "success": False,
                    "latency": 0,
                    "error": str(e),
                }
            )

    failed_checks = [c for c in checks if not c.get("success")]
    for check in failed_checks:
        if check.get("error"):
            check["error"] = localize_model_error(str(check.get("error")))
    details = {
        "checks": checks,
        "latency": round(sum(float(c.get("latency") or 0) for c in checks), 2),
        "ocr_auto_detected": bool(ocr_auto_detected_path),
        "ocr_auto_detected_path": ocr_auto_detected_path,
    }
    if not failed_checks:
        return {"valid": True, "details": details}

    error = "；".join(
        f"{c.get('label')}({c.get('model')}): {c.get('error') or '验证失败'}"
        for c in failed_checks
    )
    return {"valid": False, "error": error, "details": details}


@router.post("/save")
async def save_config(
    req: ConfigSaveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        active_config = config_manager.get_active_config(db, current_user.id)
        normalized_main_provider = normalize_provider(req.provider)
        existing_meta = normalize_metadata(active_config)

        if req.api_key is None:
            resolved_api_key = resolve_api_key(normalized_main_provider, None, active_config)
        else:
            resolved_api_key = req.api_key

        resolved_base_url = resolve_base_url(normalized_main_provider, req.base_url, active_config)
        turbo_follow_main = bool(req.turbo_follow_main if req.turbo_follow_main is not None else True)
        vl_follow_main = bool(req.vl_follow_main if req.vl_follow_main is not None else True)

        turbo_provider, turbo_key, turbo_base_url, _ = resolve_target_credentials(
            target_key="turbo",
            follow_main=turbo_follow_main,
            submitted_provider=req.turbo_provider,
            submitted_api_key=req.turbo_api_key,
            submitted_base_url=req.turbo_base_url,
            main_provider=normalized_main_provider,
            main_api_key=resolved_api_key,
            main_base_url=resolved_base_url,
            active_config=active_config,
        )
        vl_provider, vl_key, vl_base_url, _ = resolve_target_credentials(
            target_key="vision",
            follow_main=vl_follow_main,
            submitted_provider=req.vl_provider,
            submitted_api_key=req.vl_api_key,
            submitted_base_url=req.vl_base_url,
            main_provider=normalized_main_provider,
            main_api_key=resolved_api_key,
            main_base_url=resolved_base_url,
            active_config=active_config,
        )

        saved_manual_override = bool(existing_meta.get("tesseract_manual_override", False))
        manual_override = (
            saved_manual_override
            if req.tesseract_manual_override is None
            else bool(req.tesseract_manual_override)
        )
        auto_detected_path = ""
        ocr_warning = ""
        if req.tesseract_path is None:
            tesseract_path = normalize_tesseract_path(existing_meta.get("tesseract_path"))
        else:
            tesseract_path = normalize_tesseract_path(req.tesseract_path)

        if tesseract_path:
            manual_override = True if req.tesseract_manual_override is None else manual_override
        elif not manual_override:
            auto_detected_path = auto_detect_tesseract_path()
            if auto_detected_path:
                tesseract_path = auto_detected_path
            else:
                ocr_warning = "未检测到本地 OCR 引擎，建议改用云端 OCR 或安装本地 OCR 模块（Tesseract）。"

        metadata_info = {
            **existing_meta,
            "targets": {
                "turbo": {
                    "follow_main": turbo_follow_main,
                    "provider": "" if turbo_follow_main else turbo_provider,
                    "api_key": "" if turbo_follow_main else encrypt_key_for_storage(turbo_key),
                    "base_url": "" if turbo_follow_main else (turbo_base_url or ""),
                },
                "vision": {
                    "follow_main": vl_follow_main,
                    "provider": "" if vl_follow_main else vl_provider,
                    "api_key": "" if vl_follow_main else encrypt_key_for_storage(vl_key),
                    "base_url": "" if vl_follow_main else (vl_base_url or ""),
                },
            },
            "tesseract_path": tesseract_path,
            "tesseract_manual_override": manual_override,
            "tesseract_auto_detected_at": int(time.time())
            if auto_detected_path
            else existing_meta.get("tesseract_auto_detected_at"),
        }

        new_config = config_manager.create_config(
            db,
            provider=normalized_main_provider,
            model_name=req.model_name,
            vl_model_name=req.vl_model_name,
            turbo_model_name=req.turbo_model_name,
            api_key=resolved_api_key,
            base_url=resolved_base_url,
            activate=True,
            user_id=current_user.id,
            metadata_info=metadata_info,
        )

        save_and_update_ai_client(db, new_config)

        return {
            "status": "success",
            "id": new_config.id,
            "ocr_auto_detected": bool(auto_detected_path),
            "tesseract_path": tesseract_path,
            "ocr_warning": ocr_warning,
        }
    except Exception as e:
        return build_internal_error(e)
