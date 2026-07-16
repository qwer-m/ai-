from pathlib import Path
from typing import Optional

import os
import re
import requests
import subprocess
import tempfile
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.authn.diagnostic_access import validate_outbound_http_url
from core.db.database import get_db
from core.db.models import User
from modules.automation_components.services.ui_automation_service import UIAutomationService
from modules.automation_components.services.ui_test_case_import_service import UITestCaseImportService
from modules.testing.ui_automation import ui_automator
from schemas.automation.ui_automation import UIRequest, UIScriptConvertRequest


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
    data: dict = Field(default_factory=dict)


def _detect_appium_server() -> str | None:
    configured = os.environ.get("APPIUM_SERVER_URL", "http://127.0.0.1:4723").rstrip("/")
    roots = [configured]
    if configured.endswith("/wd/hub"):
        roots.append(configured[: -len("/wd/hub")])
    else:
        roots.append(f"{configured}/wd/hub")
    for root in dict.fromkeys(roots):
        try:
            response = requests.get(f"{root}/status", timeout=2)
            if response.ok:
                return root
        except requests.RequestException:
            continue
    return None


@router.post("/detect", response_model=DetectResponse)
async def detect_environment(request: DetectRequest, current_user: User = Depends(get_current_user)):
    _ = current_user
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
            result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=5)
            output = result.stdout.strip().split("\n")
            devices = [
                columns[0]
                for line in output[1:]
                if len(columns := line.split()) >= 2 and columns[1] == "device"
            ]
            if not devices:
                return DetectResponse(success=False, message="未检测到连接的 Android 设备或模拟器")

            appium_url = _detect_appium_server()
            if not appium_url:
                return DetectResponse(
                    success=False,
                    message="Android 设备已连接，但 Appium 服务未启动或地址不可访问",
                    data={"device_id": devices[0]},
                )

            device_id = devices[0]
            res = subprocess.run(
                ["adb", "-s", device_id, "shell", "dumpsys", "window"],
                capture_output=True,
                text=True,
                timeout=5,
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
                    message=f"设备 {device_id} 与 Appium 均已就绪，当前应用 {pkg}",
                    data={"app_id": pkg, "activity": activity, "device_id": device_id, "appium_url": appium_url},
                )
            return DetectResponse(
                success=True,
                message=f"设备 {device_id} 与 Appium 均已就绪，但无法获取当前应用信息",
                data={"device_id": device_id, "appium_url": appium_url},
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


@router.get("/device-screenshot")
def get_android_device_screenshot(
    device_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
):
    """返回 Android 设备当前真实画面，用于 AI 执行期间的中心预览。"""
    _ = current_user
    try:
        if not device_id:
            devices = subprocess.run(
                ["adb", "devices"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            device_id = next(
                (
                    columns[0]
                    for line in devices.stdout.splitlines()[1:]
                    if len(columns := line.split()) >= 2 and columns[1] == "device"
                ),
                None,
            )
        if not device_id:
            raise HTTPException(status_code=409, detail="未检测到已连接的 Android 设备")
        result = subprocess.run(
            ["adb", "-s", device_id, "exec-out", "screencap", "-p"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
            detail = result.stderr.decode("utf-8", errors="replace").strip() or "ADB 未返回有效 PNG 画面"
            raise HTTPException(status_code=502, detail=detail)
        return Response(
            content=result.stdout,
            media_type="image/png",
            headers={"Cache-Control": "no-store, max-age=0", "X-Android-Device": device_id},
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="本地未安装 ADB 工具") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="获取 Android 设备画面超时") from exc


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
        Path(str(path)).resolve()
        for path in (detail.get("screenshot_paths") or [])
        if Path(str(path)).name == filename
    ]
    file_path = next((path for path in candidates if path.is_file()), None)
    if not file_path:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(str(file_path))


@router.post("/import-test-cases")
async def import_ui_test_cases(
    file: UploadFile = File(...),
    project_id: int = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    automation_service = UIAutomationService(db)
    if not automation_service.has_owned_project(project_id=project_id, user_id=current_user.id):
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return UITestCaseImportService(db).parse(
            filename=file.filename or "uploaded_cases",
            content=await file.read(),
            user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/ai-locate-element")
async def ai_locate_element(
    image: UploadFile = File(...),
    element_description: str = Form(...),
    image_model: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    suffix = os.path.splitext(image.filename or "")[1] or ".png"
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = tmp.name
            tmp.write(await image.read())
        coords = ui_automator.ai_locate_element(
            tmp_path,
            element_description,
            db=db,
            user_id=current_user.id,
            image_model=image_model,
        )
        if isinstance(coords, str):
            raise HTTPException(status_code=502, detail=coords)
        if not isinstance(coords, (list, tuple)) or len(coords) < 2:
            raise HTTPException(status_code=502, detail="AI locator returned invalid coordinates")
        return {"coordinates": [int(coords[0]), int(coords[1])]}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


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


@router.post("/natural-run")
def run_ui_from_natural_language(
    req: UIRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    token: str = Depends(oauth2_scheme),
):
    try:
        status, result = UIAutomationService(db).run_natural_language(
            payload=req.model_dump(),
            user_id=current_user.id,
            token=token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return result


@router.post("/convert")
def convert_verified_ui_script(
    req: UIScriptConvertRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        status, result = UIAutomationService(db).save_script(
            payload=req.model_dump(),
            user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    operation_name: Optional[str] = Form(None),
    operation_steps: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    token: str = Depends(oauth2_scheme),
):
    try:
        status, result = UIAutomationService(db).execute_script_direct(
            payload={
                "script": script,
                "task": task,
                "url": url,
                "automation_type": automation_type,
                "project_id": project_id,
                "operation_name": operation_name,
                "operation_steps": operation_steps,
            },
            user_id=current_user.id,
            token=token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
