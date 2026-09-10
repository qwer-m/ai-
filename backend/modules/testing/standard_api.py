import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict
from typing import Any, Dict, List, Optional

from datetime import datetime

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.model_defs import User
from modules.testing_components.services.standard_interface_service import StandardInterfaceService
from modules.testing_components.services.api_request_execution_service import ApiRequestExecutionService
from schemas.automation.api_testing import ProxyRequest

"""
标准接口管理模块 (Standard API Management)

此模块提供类似 Postman 的接口管理功能。
支持创建、读取、更新、删除 (CRUD) 接口定义和文件夹结构。
数据存储在 `standard_interfaces` 表中。

主要功能：
1. 接口/目录管理：支持无限层级的目录结构 (通过 parent_id)。
2. 请求详情：存储 Method, URL, Headers, Params, Body 等详细信息。
3. 权限控制：基于 Project 和 User 进行隔离。
"""

router = APIRouter(prefix="/standard", tags=["Standard API Testing"])


@router.post("/request")
async def execute_request(
    request: ProxyRequest,
    current_user: User = Depends(get_current_user),
):
    _ = current_user
    try:
        return await ApiRequestExecutionService().execute(request)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"接口请求失败: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

class InterfaceBase(BaseModel):
    """接口基础模型"""
    name: str
    description: Optional[str] = None
    project_id: Optional[int] = None
    parent_id: Optional[int] = None
    type: str = "request" # request (接口) or folder (目录)
    
    # Request details (请求详情)
    method: Optional[str] = "GET"
    base_url: Optional[str] = None
    api_path: Optional[str] = None
    headers: Optional[List[Dict[str, Any]]] = None
    params: Optional[List[Dict[str, Any]]] = None
    body_mode: Optional[str] = "none" # none, form-data, x-www-form-urlencoded, raw, binary
    raw_type: Optional[str] = "JSON" # JSON, Text, JavaScript, HTML, XML
    body_content: Optional[str] = None
    test_config: Optional[Dict[str, Any]] = None # 测试配置 (断言等)

class InterfaceCreate(InterfaceBase):
    pass

class InterfaceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    project_id: Optional[int] = None
    parent_id: Optional[int] = None
    type: Optional[str] = None
    
    method: Optional[str] = None
    base_url: Optional[str] = None
    api_path: Optional[str] = None
    headers: Optional[List[Dict[str, Any]]] = None
    params: Optional[List[Dict[str, Any]]] = None
    body_mode: Optional[str] = None
    raw_type: Optional[str] = None
    body_content: Optional[str] = None
    test_config: Optional[Dict[str, Any]] = None

class InterfaceResponse(InterfaceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: Optional[int]
    created_at: datetime
    updated_at: datetime
    
@router.get("/interfaces", response_model=List[InterfaceResponse])
def get_interfaces(project_id: Optional[int] = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    获取接口列表 (Get Interfaces)
    
    查询当前用户在指定项目下的所有接口和目录。
    前端通常会将返回的扁平列表转换为树形结构展示。
    """
    return StandardInterfaceService(db).list_interfaces(
        user_id=current_user.id,
        project_id=project_id,
    )

@router.post("/interfaces", response_model=InterfaceResponse)
def create_interface(item: InterfaceCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    创建接口/目录 (Create Interface/Folder)
    
    验证项目归属权后，创建新的接口或目录节点。
    """
    db_item, status = StandardInterfaceService(db).create_interface(
        payload=item.model_dump(),
        user_id=current_user.id,
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail=f"Project with ID {item.project_id} not found")
    return db_item

@router.put("/interfaces/{interface_id}", response_model=InterfaceResponse)
def update_interface(interface_id: int, item: InterfaceUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    更新接口/目录 (Update Interface/Folder)
    """
    db_item, status = StandardInterfaceService(db).update_interface(
        interface_id=interface_id,
        payload=item.model_dump(exclude_unset=True),
        user_id=current_user.id,
    )
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Interface not found")
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail=f"Project with ID {item.project_id} not found")
    return db_item

@router.delete("/interfaces/{interface_id}")
def delete_interface(interface_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    删除接口/目录 (Delete Interface/Folder)
    """
    deleted = StandardInterfaceService(db).delete_interface(
        interface_id=interface_id,
        user_id=current_user.id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Interface not found")
    return {"message": "Interface deleted"}

class AnalysisRequest(BaseModel):
    method: str
    url: str
    headers: Dict[str, Any]
    body: Optional[str] = None
    response_status: int
    response_headers: Dict[str, Any]
    response_body: Optional[str] = None
    error: Optional[str] = None

@router.post("/analyze_response")
def analyze_response(req: AnalysisRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Analyze API Response using AI"""
    response = StandardInterfaceService(db).analyze_response(
        transaction=req.model_dump(),
        user_id=current_user.id,
    )
    return {"analysis": response}
