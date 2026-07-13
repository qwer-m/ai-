from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Any, Literal

from core.authn.auth import get_current_user
from core.db.database import engine, get_db
from core.db.models import ProjectPipelineConfig, User
from schemas.base.project import ProjectCreate, ProjectUpdate
from modules.system_components.services.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["Projects"])


class ProjectAgentDefaults(BaseModel):
    enabled: bool = True
    planner_llm: bool = True
    reviewer_llm: bool = True
    executor_parallel: bool = True
    executor_workers: int = Field(default=3, ge=1, le=8)
    auto_retry_enabled: bool = True
    max_auto_retries: int = Field(default=1, ge=0, le=3)
    retry_policy: Literal["conservative", "balanced", "aggressive"] = "balanced"
    max_context_chars: int = Field(default=3500, ge=800, le=12000)


class ProjectAgentDefaultsUpsertRequest(BaseModel):
    agent: ProjectAgentDefaults


def _ensure_project_pipeline_config_table() -> None:
    try:
        ProjectPipelineConfig.__table__.create(bind=engine, checkfirst=True)
    except Exception:
        pass


def _normalize_agent_defaults(raw: Any) -> dict[str, Any]:
    try:
        return ProjectAgentDefaults.model_validate(raw or {}).model_dump()
    except Exception:
        return ProjectAgentDefaults().model_dump()


_ensure_project_pipeline_config_table()


@router.post("/", status_code=201)
def create_project(
    project: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return ProjectService(db).create_project(payload=project, user_id=current_user.id)


@router.get("", include_in_schema=False)
@router.get("/")
def list_projects(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return ProjectService(db).list_projects(user_id=current_user.id)


@router.get("/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    project = ProjectService(db).get_project(project_id=project_id, user_id=current_user.id)
    if not project:
        return {"error": "Project not found"}
    return project


@router.get("/{project_id}/pipeline-agent-defaults")
def get_project_pipeline_agent_defaults(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = ProjectService(db).get_pipeline_agent_defaults(
        project_id=project_id,
        user_id=current_user.id,
        defaults_cls=ProjectAgentDefaults,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if result.get("source") == "saved":
        result["agent"] = _normalize_agent_defaults(result.get("agent"))
    return result


@router.put("/{project_id}/pipeline-agent-defaults")
def upsert_project_pipeline_agent_defaults(
    project_id: int,
    req: ProjectAgentDefaultsUpsertRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = ProjectService(db).upsert_pipeline_agent_defaults(
        project_id=project_id,
        user_id=current_user.id,
        agent_defaults=req.agent.model_dump(),
        defaults_cls=ProjectAgentDefaults,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Project not found")
    result["agent"] = _normalize_agent_defaults(result.get("agent"))
    return result


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
