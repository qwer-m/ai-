from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Any, Literal, List

from core.database import get_db, engine
from core.models import (
    Project, User, KnowledgeDocument, LogEntry, TestGeneration,
    UIExecution, UIErrorOperation, APIExecution, Evaluation,
    TestGenerationComparison, RecallMetric, ProjectPipelineConfig
)
from core.auth import get_current_user
from core.utils import logger
from schemas.project import ProjectCreate, ProjectUpdate

router = APIRouter(
    prefix="/projects",
    tags=["Projects"]
)


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
        # Non-blocking: endpoint will fail with explicit DB error if DDL is unavailable.
        pass


def _normalize_agent_defaults(raw: Any) -> dict[str, Any]:
    try:
        return ProjectAgentDefaults.model_validate(raw or {}).model_dump()
    except Exception:
        return ProjectAgentDefaults().model_dump()


_ensure_project_pipeline_config_table()

@router.post("/", status_code=201)
def create_project(project: ProjectCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    创建新项目 (Create Project)
    
    逻辑：
    1. 计算层级 (Level)：如果是子项目，层级为父项目层级+1。最大支持3级。
    2. 检查重名：同一父项目下不允许重名。
    3. 创建项目记录。
    """
    # Calculate level
    level = 1
    if project.parent_id:
        parent = db.query(Project).filter(Project.id == project.parent_id, Project.user_id == current_user.id).first()
        if not parent:
            return {"error": "Parent project not found"}
        level = parent.level + 1
        if level > 3:
            return {"error": "Maximum project nesting level (3) reached."}
    
    # Check duplicate name under same parent
    existing = db.query(Project).filter(
        Project.name == project.name,
        Project.parent_id == project.parent_id,
        Project.user_id == current_user.id
    ).first()
    
    if existing:
        return {"error": "Project name already exists in this level"}
    
    new_project = Project(
        name=project.name, 
        description=project.description,
        parent_id=project.parent_id,
        level=level,
        user_id=current_user.id
    )
    db.add(new_project)
    db.commit()
    db.refresh(new_project)
    return new_project

@router.get("", include_in_schema=False)
@router.get("/")
def list_projects(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(Project).filter(Project.user_id == current_user.id).order_by(Project.created_at.desc()).all()

@router.get("/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not project:
        return {"error": "Project not found"}
    return project


@router.get("/{project_id}/pipeline-agent-defaults")
def get_project_pipeline_agent_defaults(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    row = (
        db.query(ProjectPipelineConfig)
        .filter(
            ProjectPipelineConfig.project_id == project_id,
            ProjectPipelineConfig.user_id == current_user.id,
        )
        .first()
    )

    if not row:
        return {
            "project_id": project_id,
            "agent": ProjectAgentDefaults().model_dump(),
            "source": "default",
        }

    return {
        "project_id": project_id,
        "agent": _normalize_agent_defaults(row.agent_defaults),
        "source": "saved",
        "updated_at": row.updated_at,
    }


@router.put("/{project_id}/pipeline-agent-defaults")
def upsert_project_pipeline_agent_defaults(
    project_id: int,
    req: ProjectAgentDefaultsUpsertRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    row = (
        db.query(ProjectPipelineConfig)
        .filter(
            ProjectPipelineConfig.project_id == project_id,
            ProjectPipelineConfig.user_id == current_user.id,
        )
        .first()
    )

    if not row:
        row = ProjectPipelineConfig(
            project_id=project_id,
            user_id=current_user.id,
            agent_defaults=req.agent.model_dump(),
        )
    else:
        row.agent_defaults = req.agent.model_dump()

    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "project_id": project_id,
        "agent": _normalize_agent_defaults(row.agent_defaults),
        "source": "saved",
        "updated_at": row.updated_at,
    }

@router.put("/{project_id}")
def update_project(project_id: int, project: ProjectUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Get the project to update
    db_project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not db_project:
        return {"error": "Project not found"}
    
    # Calculate new level if parent is changing
    new_level = db_project.level
    if project.parent_id != db_project.parent_id:
        if project.parent_id:
            parent = db.query(Project).filter(Project.id == project.parent_id, Project.user_id == current_user.id).first()
            if not parent:
                return {"error": "Parent project not found"}
            new_level = parent.level + 1
            if new_level > 3:
                return {"error": "Maximum project nesting level (3) reached."}
        else:
            new_level = 1
    
    # Check duplicate name under same parent
    existing = db.query(Project).filter(
        Project.name == project.name,
        Project.parent_id == project.parent_id,
        Project.id != project_id,
        Project.user_id == current_user.id
    ).first()
    
    if existing:
        return {"error": "Project name already exists in this level"}
    
    # Update project fields
    db_project.name = project.name
    db_project.description = project.description
    db_project.parent_id = project.parent_id
    db_project.level = new_level
    
    # Update child levels if parent changed
    if project.parent_id != db_project.parent_id:
        # Recursively update child levels
        def update_child_levels(current_project, new_parent_level):
            current_project.level = new_parent_level + 1
            for child in current_project.children:
                update_child_levels(child, current_project.level)
        
        for child in db_project.children:
            update_child_levels(child, db_project.level)
    
    db.commit()
    db.refresh(db_project)
    return db_project

@router.delete("/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        # Check if project exists
        project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
        if not project:
            return JSONResponse(status_code=404, content={"error": "Project not found"})
        
        # Check if project has children
        if project.children:
            return JSONResponse(status_code=400, content={"error": "Cannot delete project with child projects. Please delete children first."})
        
        # Delete all related knowledge documents
        db.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == project_id).delete()
        
        # Delete other related records
        db.query(LogEntry).filter(LogEntry.project_id == project_id).delete()
        db.query(TestGeneration).filter(TestGeneration.project_id == project_id).delete()
        
        # Delete UI executions and related errors
        # First delete UIErrorOperation that link to UIExecution of this project
        # Since UIErrorOperation also has project_id, we can delete by project_id
        db.query(UIErrorOperation).filter(UIErrorOperation.project_id == project_id).delete()
        db.query(UIExecution).filter(UIExecution.project_id == project_id).delete()
        
        db.query(APIExecution).filter(APIExecution.project_id == project_id).delete()
        db.query(Evaluation).filter(Evaluation.project_id == project_id).delete()
        db.query(TestGenerationComparison).filter(TestGenerationComparison.project_id == project_id).delete()
        db.query(RecallMetric).filter(RecallMetric.project_id == project_id).delete()
        
        # Delete the project
        db.delete(project)
        db.commit()
        
        return {"message": "Project deleted successfully"}
    except Exception as e:
        logger.error(f"Error deleting project {project_id}: {str(e)}")
        db.rollback()
        return JSONResponse(status_code=500, content={"error": f"Failed to delete project: {str(e)}"})
