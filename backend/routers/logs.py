from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import List, Optional
from datetime import datetime

from core.database import get_db
from core.models import LogEntry, Project, User
from core.auth import get_current_user
from schemas.logs import LogCreate, LogRead

router = APIRouter(
    prefix="/logs",
    tags=["Logs"]
)

@router.get("/{project_id}", response_model=List[LogRead])
def get_project_logs(
    project_id: int, 
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取项目日志 (Get Project Logs)
    """
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    logs = db.query(LogEntry).filter(
        LogEntry.project_id == project_id,
        LogEntry.user_id == current_user.id
    ).order_by(LogEntry.created_at.desc()).limit(limit).all()
    return logs


@router.post("")
def create_log(
    payload: LogCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = db.query(Project).filter(Project.id == payload.project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    log_entry = LogEntry(
        project_id=payload.project_id,
        log_type=payload.log_type,
        message=payload.message,
        user_id=current_user.id,
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)
    return {"status": "success", "id": log_entry.id}


@router.delete("/{project_id}")
def delete_project_logs(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    deleted = db.query(LogEntry).filter(
        LogEntry.project_id == project_id,
        LogEntry.user_id == current_user.id
    ).delete(synchronize_session=False)
    db.commit()
    return {"status": "success", "deleted_logs": int(deleted)}
