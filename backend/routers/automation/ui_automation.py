from typing import Optional

import os
import re
import requests
import subprocess
from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import User
from modules.automation_components.services.ui_automation_service import UIAutomationService
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


class DetectResponse(BaseModel):
    success: bool
    message: str
    data: dict = {}


@router.post("/detect", response_model=DetectResponse)
async def detect_environment(request: DetectRequest):
    if request.type == "web":
        if not request.target:
            return DetectResponse(success=False, message="请输入目标URL")

        url = request.target if request.target.startswith("http") else f"https://{request.target}"
        try:
            resp = requests.head(url, timeout=5)
            return DetectResponse(success=True, message=f"URL 可访问 (Status: {resp.status_code})", data={"validated_url": url})
        except Exception as exc:
            return DetectResponse(success=False, message=f"无法连接URL: {exc}")

    if request.type == "app":
        try:
            result = subprocess.run(["adb", "devices"], capture_output=True, text=True)
            output = result.stdout.strip().split("\n")
            devices = [line.split()[0] for line in output[1:] if line.strip() and "device" in line]
            if not devices:
                return DetectResponse(success=False, message="未检测到连接的 Android 设备或模拟器")

            device_id = devices[0]
            res = subprocess.run(
                f'adb -s {device_id} shell "dumpsys window | grep mCurrentFocus"',
                shell=True,
                capture_output=True,
                text=True,
            )
            focus_line = res.stdout.strip()
            match = re.search(r"u0\s+([^\s/]+)/([^\s]+)}", focus_line)
            if match:
                pkg = match.group(1)
                activity = match.group(2)
                if activity.startswith("."):
                    activity = pkg + activity
                return DetectResponse(
                    success=True,
                    message=f"检测到设备 {device_id}，当前应用 {pkg}",
                    data={"app_id": pkg, "activity": activity, "device_id": device_id},
                )
            return DetectResponse(
                success=True,
                message=f"检测到设备 {device_id}，但无法获取当前应用信息",
                data={"device_id": device_id},
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
    execution_id: str,
    filename: str,
    current_user: User = Depends(get_current_user),
):
    _ = current_user
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = os.path.join(os.getcwd(), "screenshots", execution_id, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(file_path)


@router.post("/generate")
def generate_ui_script_only(
    req: UIRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    token: str = Depends(oauth2_scheme),
):
    status, result = UIAutomationService(db).generate_script(
        payload=req.model_dump(),
        user_id=current_user.id,
        token=token,
    )
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, result = UIAutomationService(db).execute_script_direct(
        payload={
            "script": script,
            "task": task,
            "url": url,
            "automation_type": automation_type,
            "project_id": project_id,
            "test_case_id": test_case_id,
        },
        user_id=current_user.id,
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
    status, result = UIAutomationService(db).run_ui_automation(
        payload=req.model_dump(),
        user_id=current_user.id,
        token=token,
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return result
