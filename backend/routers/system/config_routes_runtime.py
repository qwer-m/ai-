from __future__ import annotations
import time
from starlette.concurrency import run_in_threadpool
from core.ai.reasoning import (
    check_reasoning_selection, config_validation_fingerprint,
    read_config_validation, sign_config_validation,
)
from core.ai.providers import OpenAICompatibleProvider
from typing import Any
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.model_defs import User
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
    _build_vision_probe_data_uri,
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
    review_node = target_nodes.get("review") if isinstance(target_nodes.get("review"), dict) else {}
    vision_node = target_nodes.get("vision") if isinstance(target_nodes.get("vision"), dict) else {}
    turbo_follow_main = bool(turbo_node.get("follow_main", True))
    review_follow_main = bool(review_node.get("follow_main", True))
    vl_follow_main = bool(vision_node.get("follow_main", True))
    reasoning_view = {}
    for route, capability in (meta.get("reasoning") or {}).items():
        if not isinstance(capability, dict):
            continue
        if capability.get("status") == "verified":
            # 旧五档探测只证明参数被接受；不再向界面宣称五种独立行为已验证。
            capability = {**capability, "supported_efforts": [], "status": "request_accepted",
                          "message": "保留历史选定参数；兼容参数可能映射到相同强度，完整工具链尚未验证。"}
        reasoning_view[route] = capability
    return {
        "active": True,
        "reasoning": reasoning_view,
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
        "review_model_name": str(review_node.get("model_name") or ""),
        "review_provider": str(review_node.get("provider") or ""),
        "review_base_url": str(review_node.get("base_url") or ""),
        "review_follow_main": review_follow_main,
        "has_review_api_key": bool(review_node.get("api_key")) and not review_follow_main,
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
    return await run_in_threadpool(_validate_model_config, req, db, current_user.id)


def _validate_model_config(req: ConfigValidateRequest, db: Session, user_id: int,
                           *, for_save: bool = False) -> dict[str, Any]:
    """先核对绑定配置的验证证据；只有显式验证操作才允许付费请求。"""
    provider = normalize_provider(req.provider)
    active_config = config_manager.get_active_config(db, user_id)
    api_key = resolve_api_key(provider, req.api_key, active_config)
    base_url = resolve_base_url(provider, req.base_url, active_config)
    route_specs = [
        ("main", "文本模型", req.model_name, req.reasoning_effort, None),
        ("turbo", "压缩模型", req.turbo_model_name, req.turbo_reasoning_effort, "turbo"),
        ("review", "审查模型", req.review_model_name, req.review_reasoning_effort, "review"),
        ("vision", "图像模型", req.vl_model_name, req.vl_reasoning_effort, "vl"),
    ]
    routes, identities = [], []
    for route, label, raw_model, effort, prefix in route_specs:
        model = (raw_model or "").strip()
        if not model:
            continue
        try:
            if prefix:
                target_provider, key, url, _ = resolve_target_credentials(
                    target_key=route,
                    follow_main=getattr(req, prefix + "_follow_main") is not False,
                    submitted_provider=getattr(req, prefix + "_provider"),
                    submitted_api_key=getattr(req, prefix + "_api_key"),
                    submitted_base_url=getattr(req, prefix + "_base_url"),
                    main_provider=provider, main_api_key=api_key, main_base_url=base_url,
                    active_config=active_config,
                )
            else:
                target_provider, key, url = provider, api_key, base_url
            client = build_provider(target_provider, key, url, model)
            identity = {"route": route, "provider": target_provider,
                        "endpoint": str(getattr(client, "base_url", url) or "").rstrip("/"),
                        "credential": key, "model": model,
                        "protocol": str(getattr(client, "wire_api", type(client).__name__)),
                        "reasoning_effort": effort, "vision": route == "vision"}
            identities.append(identity)
            routes.append((route, label, model, effort, client, target_provider, key, url))
        except Exception as error:
            return {"valid": False, "error": f"{label}配置无效：{type(error).__name__}",
                    "details": {"checks": [], "reasoning": {}}}
    if not req.model_name.strip():
        return {"valid": False, "error": "请填写文本模型名称", "details": {"checks": [], "reasoning": {}}}

    fingerprint = config_validation_fingerprint(user_id, identities)
    saved_token = normalize_metadata(active_config).get("validation_token")
    tokens = [req.validation_token, saved_token]
    if for_save or not req.refresh_validation:
        for token in tokens:
            evidence = read_config_validation(token, user_id=user_id, fingerprint=fingerprint)
            if evidence is not None:
                return {"valid": True, "error": "", "details": {**evidence, "reused": True},
                        "validation_token": token}
    if for_save:
        return {"valid": False, "error": "当前配置尚无有效验证记录，请先验证连接；保存不会自动发起付费请求。",
                "details": {"checks": [], "reasoning": {}, "reused": False}}

    checks, reasoning, validated = [], {}, {}
    for route, label, model, effort, client, target_provider, key, url in routes:
        started = time.perf_counter()
        check = {"route": route, "label": label, "model": model, "success": False}
        try:
            identity = config_validation_fingerprint(user_id, [{
                "provider": target_provider, "endpoint": str(getattr(client, "base_url", url) or "").rstrip("/"),
                "credential": key, "model": model, "protocol": str(getattr(client, "wire_api", type(client).__name__)),
                "reasoning_effort": effort, "vision": route == "vision",
            }])
            if isinstance(client, OpenAICompatibleProvider):
                if identity not in validated:
                    validated[identity] = check_reasoning_selection(
                        client, model, effort,
                        vision_data_uri=_build_vision_probe_data_uri() if route == "vision" else None,
                    )
                check.update(validated[identity])
                reasoning[route] = {"effort": effort, "accepted_effort": effort,
                                    "supported_efforts": [], "status": "request_accepted",
                                    "message": "已验证所选请求参数；其他级别、实际推理强度及完整工具链尚未验证。"}
            else:
                reasoning[route] = {"effort": None, "supported_efforts": [], "status": "unavailable",
                                    "message": "该接口未提供可验证的思考级别，使用模型默认设置"}
                if effort:
                    check.update(success=False, error="该接口未提供可验证的思考级别")
                else:
                    if identity not in validated:
                        validated[identity] = (validate_vision_model(target_provider, key, url, model, label)
                                               if route == "vision" else
                                               validate_text_model(target_provider, key, url, model, label))
                    check.update(validated[identity])
        except Exception as error:
            # 不回传网络异常正文，避免凭据和网关地址进入页面或持久化诊断。
            check["error"] = f"模型验证失败：{type(error).__name__}"
            reasoning.setdefault(route, {"effort": effort, "supported_efforts": [], "status": "error",
                                         "message": "能力验证失败，请检查连接后重试"})
        check["latency"] = round((time.perf_counter() - started) * 1000, 2)
        checks.append(check)
    failed = [check for check in checks if not check["success"]]
    details = {"checks": checks, "reasoning": reasoning,
               "latency": round(sum(check.get("latency", 0) for check in checks), 2), "reused": False}
    return {"valid": not failed,
            "error": "；".join(f"{check['label']}: {check.get('error') or '验证失败'}" for check in failed),
            "details": details,
            "validation_token": (sign_config_validation(user_id=user_id, fingerprint=fingerprint, details=details)
                                 if not failed else None)}


@router.post("/save")
async def save_config(
    req: ConfigSaveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    validation = await run_in_threadpool(_validate_model_config, req, db, current_user.id, for_save=True)
    if not validation["valid"]:
        return {"status": "error", **validation}
    try:
        active_config = config_manager.get_active_config(db, current_user.id)
        normalized_main_provider = normalize_provider(req.provider)
        existing_meta = normalize_metadata(active_config)
        resolved_api_key = resolve_api_key(normalized_main_provider, req.api_key, active_config)
        resolved_base_url = resolve_base_url(normalized_main_provider, req.base_url, active_config)
        turbo_follow_main = bool(req.turbo_follow_main if req.turbo_follow_main is not None else True)
        review_follow_main = bool(req.review_follow_main if req.review_follow_main is not None else True)
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
        review_provider, review_key, review_base_url, _ = resolve_target_credentials(
            target_key="review",
            follow_main=review_follow_main,
            submitted_provider=req.review_provider,
            submitted_api_key=req.review_api_key,
            submitted_base_url=req.review_base_url,
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
            "reasoning": validation["details"]["reasoning"],
            "validation_token": validation["validation_token"],
            "targets": {
                "turbo": {
                    "follow_main": turbo_follow_main,
                    "provider": "" if turbo_follow_main else turbo_provider,
                    "api_key": "" if turbo_follow_main else encrypt_key_for_storage(turbo_key),
                    "base_url": "" if turbo_follow_main else (turbo_base_url or ""),
                },
                "review": {
                    "follow_main": review_follow_main,
                    "provider": "" if review_follow_main else review_provider,
                    "api_key": "" if review_follow_main else encrypt_key_for_storage(review_key),
                    "base_url": "" if review_follow_main else (review_base_url or ""),
                    "model_name": (req.review_model_name or "").strip(),
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
            "details": validation["details"],
            "ocr_auto_detected": bool(auto_detected_path),
            "tesseract_path": tesseract_path,
            "ocr_warning": ocr_warning,
        }
    except Exception as e:
        return build_internal_error(e)
