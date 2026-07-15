from typing import Optional

import json
import os
import requests
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen
from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.authn.diagnostic_access import ensure_diagnostic_routes_enabled, validate_outbound_http_url
from core.db.database import get_db
from core.db.models import User
from modules.automation_components.services.ui_automation_service import UIAutomationService
from modules.automation_components.services.ui_visual_asset_service import (
    capture_visual_asset,
    list_visual_asset_catalogs,
)
from modules.testing.ui_automation import ui_automator
from schemas.automation.ui_automation import UIRequest


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

router = APIRouter(
    prefix="/ui-automation",
    tags=["UI Automation"],
)


class DetectRequest(BaseModel):
    type: str
    target: str = ""
    device_id: Optional[str] = None
    appium_server_url: str = "http://127.0.0.1:4723"


class DetectResponse(BaseModel):
    success: bool
    message: str
    data: dict = {}


class VisualAssetCaptureRequest(BaseModel):
    project_id: int
    group_name: str
    asset_name: str
    element_description: str
    device_id: Optional[str] = None
    image_model: Optional[str] = None
    threshold: float = 0.82


def _detect_appium_server(configured_url: str) -> tuple[str | None, dict]:
    supplied = configured_url.rstrip("/")
    candidates = [supplied]
    candidates.append(supplied[: -len("/wd/hub")] if supplied.endswith("/wd/hub") else f"{supplied}/wd/hub")
    errors = []
    for candidate in dict.fromkeys(candidates):
        parsed = urlsplit(candidate)
        status_url = urlunsplit((parsed.scheme, parsed.netloc, f"{parsed.path.rstrip('/')}/status", "", ""))
        try:
            with urlopen(status_url, timeout=3) as response:  # noqa: S310 - 仅检测用户指定的本地 Appium 服务
                payload = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
                if 200 <= response.status < 300:
                    return candidate, payload
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
    return None, {"errors": errors}


def _detect_appium_inspector_process() -> bool:
    try:
        if os.name == "nt":
            result = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=5)
            return "appium inspector" in result.stdout.lower() or "appium-inspector" in result.stdout.lower()
        result = subprocess.run(["pgrep", "-af", "appium.*inspector"], capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


@router.post("/detect", response_model=DetectResponse)
async def detect_environment(request: DetectRequest, current_user: User = Depends(get_current_user)):
    _ = current_user
    ensure_diagnostic_routes_enabled()
    if request.type == "web":
        if not request.target:
            return DetectResponse(success=False, message="请输入目标URL")

        url = validate_outbound_http_url(request.target)
        try:
            resp = requests.head(url, timeout=5)
            return DetectResponse(success=True, message=f"URL 可访问 (Status: {resp.status_code})", data={"validated_url": url})
        except Exception as exc:
            return DetectResponse(success=False, message=f"无法连接URL: {exc}")

    if request.type == "app":
        try:
            result = subprocess.run(["adb", "devices"], capture_output=True, text=True)
            output = result.stdout.strip().split("\n")
            devices = [line.split()[0] for line in output[1:] if line.endswith("\tdevice")]
            if not devices:
                return DetectResponse(success=False, message="未检测到连接的 Android 设备或模拟器")

            device_id = request.device_id or devices[0]
            if device_id not in devices:
                return DetectResponse(success=False, message=f"指定设备不在线：{device_id}", data={"devices": devices})
            appium_url, appium_status = _detect_appium_server(request.appium_server_url)
            if not appium_url:
                return DetectResponse(
                    success=False,
                    message="ADB 设备在线，但 Appium 服务不可用",
                    data={"devices": devices, "device_id": device_id, "appium": appium_status},
                )
            app_info = ui_automator.get_current_app_info(device_id)
            if "error" not in app_info:
                pkg = app_info["package"]
                activity = app_info["activity"]
                return DetectResponse(
                    success=True,
                    message=f"设备、Appium 和当前应用均可用：{device_id} / {pkg}",
                    data={
                        "app_id": pkg,
                        "activity": activity,
                        "device_id": device_id,
                        "devices": devices,
                        "appium_server_url": appium_url,
                        "appium_status": appium_status,
                        "inspector_process_detected": _detect_appium_inspector_process(),
                    },
                )
            return DetectResponse(
                success=True,
                message=f"设备和 Appium 可用，但无法获取当前应用信息：{device_id}",
                data={
                    "device_id": device_id,
                    "devices": devices,
                    "appium_server_url": appium_url,
                    "appium_status": appium_status,
                    "inspector_process_detected": _detect_appium_inspector_process(),
                },
            )
        except FileNotFoundError:
            return DetectResponse(success=False, message="服务器未安装 ADB 工具")
        except Exception as exc:
            return DetectResponse(success=False, message=f"检测失败: {exc}")

    return DetectResponse(success=False, message="未知的自动化类型")


@router.get("/app-info")
def get_current_app_info(
    device_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
):
    _ = current_user
    result = ui_automator.get_current_app_info(device_id)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@router.get("/history")
def list_ui_automation_history(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, history = UIAutomationService(db).list_history(project_id=project_id, user_id=current_user.id)
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return history


@router.get("/visual-assets")
def list_ui_visual_assets(current_user: User = Depends(get_current_user)):
    _ = current_user
    return {"catalogs": list_visual_asset_catalogs()}


@router.post("/visual-assets/capture")
def capture_ui_visual_asset(
    req: VisualAssetCaptureRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = UIAutomationService(db)
    if not service.has_owned_project(project_id=req.project_id, user_id=current_user.id):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return capture_visual_asset(
            group_name=req.group_name,
            asset_name=req.asset_name,
            element_description=req.element_description,
            db=db,
            user_id=current_user.id,
            device_id=req.device_id,
            image_model=req.image_model,
            threshold=req.threshold,
        )
    except (ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{execution_id}")
def get_ui_automation_detail(
    execution_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, detail = UIAutomationService(db).get_execution_detail(execution_id=execution_id, user_id=current_user.id)
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Execution not found")
    return detail


@router.get("/screenshots/{execution_id}/{filename}")
def get_screenshot(
    execution_id: int,
    filename: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    status, detail = UIAutomationService(db).get_execution_detail(
        execution_id=execution_id,
        user_id=current_user.id,
    )
    if status == "not_found" or not detail:
        raise HTTPException(status_code=404, detail="Execution not found")
    candidates = [
        Path(path).expanduser().resolve()
        for path in (detail.get("screenshot_paths") or [])
        if Path(path).name == filename
    ]
    if not candidates or not candidates[0].is_file():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(candidates[0])


@router.post("/generate")
def generate_ui_script_only(
    req: UIRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    token: str = Depends(oauth2_scheme),
):
    try:
        status, result = UIAutomationService(db).generate_script(
            payload=req.model_dump(),
            user_id=current_user.id,
            token=token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return result


@router.post("/execute")
def execute_ui_script_direct(
    script: str = Form(...),
    task: str = Form(...),
    url: str = Form(...),
    automation_type: str = Form("web"),
    project_id: int = Form(...),
    test_case_id: Optional[int] = Form(None),
    device_id: Optional[str] = Form(None),
    visual_asset_group: Optional[str] = Form(None),
    appium_server_url: Optional[str] = Form(None),
    reset_app_data: Optional[bool] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    token: str = Depends(oauth2_scheme),
):
    status, result = UIAutomationService(db).execute_script_direct(
        payload={
            "script": script,
            "task": task,
            "url": url,
            "automation_type": automation_type,
            "project_id": project_id,
            "test_case_id": test_case_id,
            "device_id": device_id,
            "visual_asset_group": visual_asset_group,
            "appium_server_url": appium_server_url,
            "reset_app_data": reset_app_data,
        },
        user_id=current_user.id,
        token=token,
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return result


@router.post("/")
def run_ui_automation(
    req: UIRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    token: str = Depends(oauth2_scheme),
):
    try:
        status, result = UIAutomationService(db).run_ui_automation(
            payload=req.model_dump(),
            user_id=current_user.id,
            token=token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return result
