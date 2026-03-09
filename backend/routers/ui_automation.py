from fastapi import APIRouter, Depends, HTTPException, Form, File, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional, List
from pydantic import BaseModel
import subprocess
import requests
import re
import os

from core.database import get_db
from core.models import Project, User, UIExecution
from core.auth import get_current_user
from core.utils import log_to_db, logger
from schemas.ui_automation import UIRequest
from modules.ui_automation import ui_automator

from fastapi.security import OAuth2PasswordBearer
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

router = APIRouter(
    prefix="/ui-automation",
    tags=["UI Automation"]
)

class DetectRequest(BaseModel):
    type: str  # 'web' or 'app'
    target: str = "" # URL for web

class DetectResponse(BaseModel):
    success: bool
    message: str
    data: dict = {}

@router.post("/detect", response_model=DetectResponse)
async def detect_environment(request: DetectRequest):
    if request.type == 'web':
        if not request.target:
            return DetectResponse(success=False, message="请输入目标 URL")
        
        url = request.target
        if not url.startswith('http'):
            url = 'https://' + url
            
        try:
            # Simple connectivity check
            resp = requests.head(url, timeout=5)
            return DetectResponse(
                success=True, 
                message=f"URL 有效 (Status: {resp.status_code})", 
                data={"validated_url": url}
            )
        except Exception as e:
            return DetectResponse(success=False, message=f"无法连接到 URL: {str(e)}")

    elif request.type == 'app':
        # 1. Check for connected devices (ADB)
        try:
            # Check adb devices
            result = subprocess.run(["adb", "devices"], capture_output=True, text=True)
            output = result.stdout.strip().split('\n')
            devices = [line.split()[0] for line in output[1:] if line.strip() and 'device' in line]
            
            if not devices:
                return DetectResponse(success=False, message="未检测到连接的 Android 设备或模拟器")
            
            device_id = devices[0] # Pick first device
            
            # 2. Get current focused app
            # Command: adb shell dumpsys window | grep mCurrentFocus
            cmd = f"adb -s {device_id} shell dumpsys window | grep mCurrentFocus"
            # On Windows grep might not work in adb shell directly depending on environment, 
            # but usually 'adb shell "dumpsys window | grep mCurrentFocus"' works if grep is on device (Android usually has it).
            # Alternatively use 'dumpsys window displays' and parse in python.
            # Let's try the grep way first as it's standard on Android.
            
            res = subprocess.run(f"adb -s {device_id} shell \"dumpsys window | grep mCurrentFocus\"", shell=True, capture_output=True, text=True)
            focus_line = res.stdout.strip()
            
            # Expected format: mCurrentFocus=Window{... u0 com.package.name/com.package.name.ActivityName}
            # Regex to extract package and activity
            match = re.search(r'u0\s+([^\s/]+)/([^\s]+)}', focus_line)
            if match:
                pkg = match.group(1)
                activity = match.group(2)
                # If activity starts with ., prepend package
                if activity.startswith('.'):
                    activity = pkg + activity
                    
                return DetectResponse(
                    success=True, 
                    message=f"检测到设备 {device_id}，当前应用: {pkg}", 
                    data={
                        "app_id": pkg,
                        "activity": activity,
                        "device_id": device_id
                    }
                )
            else:
                return DetectResponse(success=True, message=f"检测到设备 {device_id}，但无法获取当前应用信息", data={"device_id": device_id})
                
        except FileNotFoundError:
            return DetectResponse(success=False, message="服务器未安装 ADB 工具")
        except Exception as e:
            return DetectResponse(success=False, message=f"检测失败: {str(e)}")
            
    return DetectResponse(success=False, message="未知的自动化类型")

@router.get("/app-info")
def get_current_app_info(
    device_id: Optional[str] = None,
    current_user: User = Depends(get_current_user)
):
    result = ui_automator.get_current_app_info(device_id)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result

@router.get("/history")
def list_ui_automation_history(
    project_id: int, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    history = db.query(UIExecution).filter(
        UIExecution.project_id == project_id,
        UIExecution.user_id == current_user.id
    ).order_by(desc(UIExecution.created_at)).limit(50).all()
    
    return [{
        "id": h.id,
        "task_description": h.task_description[:50] + "..." if h.task_description else "无描述",
        "status": h.status,
        "created_at": h.created_at,
        "automation_type": h.automation_type,
        "quality_score": h.quality_score
    } for h in history]

@router.get("/{execution_id}")
def get_ui_automation_detail(
    execution_id: int, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    execution = db.query(UIExecution).filter(
        UIExecution.id == execution_id,
        UIExecution.user_id == current_user.id
    ).first()
    
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
        
    return {
        "id": execution.id,
        "task_description": execution.task_description,
        "generated_script": execution.generated_script,
        "execution_result": execution.execution_result,
        "status": execution.status,
        "screenshot_paths": execution.screenshot_paths,
        "quality_score": execution.quality_score,
        "evaluation_result": execution.evaluation_result,
        "created_at": execution.created_at,
        "automation_type": execution.automation_type,
        "url": execution.url,
        "app_info": execution.app_info
    }

# Screenshots route (moved from root /api/screenshots to /api/ui-automation/screenshots)
@router.get("/screenshots/{execution_id}/{filename}")
def get_screenshot(
    execution_id: str, 
    filename: str,
    current_user: User = Depends(get_current_user)
):
    # Security check: filename should be just a name, not path
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
    token: str = Depends(oauth2_scheme)
):
    # Verify project access
    project = db.query(Project).filter(Project.id == req.project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Generate AI-driven image recognition script
    script = ui_automator.generate_ai_image_recognition_script(
        req.task, 
        req.url, 
        req.automation_type, 
        db=db, 
        user_id=current_user.id, 
        token=token, 
        image_model=req.image_model,
        requirement_context=req.requirement_context
    )
    return {"script": script}

@router.post("/execute")
def execute_ui_script_direct(
    script: str = Form(...),
    task: str = Form(...),
    url: str = Form(...),
    automation_type: str = Form("web"),
    project_id: int = Form(...),
    test_case_id: Optional[int] = Form(None),
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    # Verify project access
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    result = ui_automator.execute_script(
        script, 
        url, 
        task, 
        automation_type, 
        db, 
        project_id, 
        user_id=current_user.id,
        test_case_id=test_case_id
    )
    return result

@router.post("/")
def run_ui_automation(req: UIRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user), token: str = Depends(oauth2_scheme)):
    # Verify project access
    project = db.query(Project).filter(Project.id == req.project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    log_to_db(db, req.project_id, "system", f"开始执行UI自动化: {req.task}", user_id=current_user.id)
    # 1. Generate AI-driven image recognition script
    script = ui_automator.generate_ai_image_recognition_script(
        req.task, 
        req.url, 
        req.automation_type, 
        db=db, 
        user_id=current_user.id, 
        token=token, 
        image_model=req.image_model,
        requirement_context=req.requirement_context
    )
    # 2. Execute script
    result = ui_automator.execute_script(script, req.url, req.task, req.automation_type, db, req.project_id, user_id=current_user.id)
    log_to_db(db, req.project_id, "system", f"UI自动化执行完成，结果: {result.get('status', 'unknown')}", user_id=current_user.id)
    return {"script": script, "result": result}
