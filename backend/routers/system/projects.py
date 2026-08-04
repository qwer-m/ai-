from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.model_defs import User
from schemas.base.project import ProjectCreate, ProjectUpdate
from modules.system_components.services.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.post("", status_code=201)
def create_project(
    project: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return ProjectService(db).create_project(payload=project, user_id=current_user.id)


@router.get("")
def list_projects(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return ProjectService(db).list_projects(user_id=current_user.id)


@router.get("/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    project = ProjectService(db).get_project(project_id=project_id, user_id=current_user.id)
    if not project:
        return {"error": "Project not found"}
    return project


@router.put("/{project_id}")
def update_project(
    project_id: int,
    project: ProjectUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return ProjectService(db).update_project(
        project_id=project_id,
        payload=project,
        user_id=current_user.id,
    )


@router.delete("/{project_id}")
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ok, payload = ProjectService(db).delete_project(project_id=project_id, user_id=current_user.id)
    if ok:
        return payload
    return JSONResponse(status_code=int(payload["status_code"]), content=payload["body"])
