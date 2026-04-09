from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import User
from schemas.base.logs import LogCreate, LogRead
from modules.system_components.services.log_service import LogService

router = APIRouter(prefix="/logs", tags=["Logs"])


@router.get("/{project_id}", response_model=List[LogRead])
def get_project_logs(
    project_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    logs = LogService(db).get_project_logs(
        project_id=project_id,
        user_id=current_user.id,
        limit=limit,
    )
    if logs is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return logs


@router.post("")
def create_log(
    payload: LogCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = LogService(db).create_log(
        project_id=payload.project_id,
        user_id=current_user.id,
        log_type=payload.log_type,
        message=payload.message,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"status": "success", "id": row.id}


@router.delete("/{project_id}")
def delete_project_logs(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = LogService(db).delete_project_logs(project_id=project_id, user_id=current_user.id)
    if result is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return result
