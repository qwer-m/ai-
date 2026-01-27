from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional, Any, Dict
from core.database import get_db
from core.models import StandardInterface, User, Project
from core.auth import get_current_user
from datetime import datetime

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
    id: int
    user_id: Optional[int]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

@router.get("/interfaces", response_model=List[InterfaceResponse])
def get_interfaces(project_id: Optional[int] = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    获取接口列表 (Get Interfaces)
    
    查询当前用户在指定项目下的所有接口和目录。
    前端通常会将返回的扁平列表转换为树形结构展示。
    """
    query = db.query(StandardInterface).filter(StandardInterface.user_id == current_user.id)
    if project_id:
        query = query.filter(StandardInterface.project_id == project_id)
    return query.all()

@router.post("/interfaces", response_model=InterfaceResponse)
def create_interface(item: InterfaceCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    创建接口/目录 (Create Interface/Folder)
    
    验证项目归属权后，创建新的接口或目录节点。
    """
    # Validate Project
    if item.project_id:
        project = db.query(Project).filter(Project.id == item.project_id, Project.user_id == current_user.id).first()
        if not project:
            raise HTTPException(status_code=404, detail=f"Project with ID {item.project_id} not found")

    db_item = StandardInterface(**item.dict(), user_id=current_user.id)
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item

@router.put("/interfaces/{id}", response_model=InterfaceResponse)
def update_interface(id: int, item: InterfaceUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    更新接口详情 (Update Interface)
    
    支持部分更新 (PATCH 语义)。
    """
    db_item = db.query(StandardInterface).filter(StandardInterface.id == id, StandardInterface.user_id == current_user.id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Interface not found")
    
    for key, value in item.dict(exclude_unset=True).items():
        setattr(db_item, key, value)
    
    db.commit()
    db.refresh(db_item)
    return db_item

@router.delete("/interfaces/{id}")
def delete_interface(id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    删除接口/目录 (Delete Interface)
    
    如果删除的是目录 (folder)，会递归删除其下的所有子节点 (Recursive Deletion)。
    """
    db_item = db.query(StandardInterface).filter(StandardInterface.id == id, StandardInterface.user_id == current_user.id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Interface not found")
    
    # Optional: Delete children recursively if folder
    if db_item.type == 'folder':
        def delete_recursive(parent_id):
            children = db.query(StandardInterface).filter(StandardInterface.parent_id == parent_id).all()
            for child in children:
                delete_recursive(child.id)
                db.delete(child)
        
        delete_recursive(id)

    db.delete(db_item)
    db.commit()
    return {"status": "success"}
