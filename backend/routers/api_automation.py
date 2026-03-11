from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from core.auth import get_current_user
from core.database import get_db
from core.models import APIExecution, Project, StandardInterface, User
from core.workflow import WorkflowKind, WorkflowStage, log_workflow_trace
from modules.api_testing import api_tester
from modules.context_orchestrator import context_orchestrator
from schemas.api_testing import APIRequest

router = APIRouter(prefix="/api-automation", tags=["API Automation"])


def _get_owned_project(project_id: int, db: Session, user_id: int) -> Project:
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == user_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _list_standard_interfaces(db: Session, project_id: int, user_id: int, limit: int = 12) -> list[dict[str, Any]]:
    rows = (
        db.query(StandardInterface)
        .filter(
            StandardInterface.project_id == project_id,
            StandardInterface.user_id == user_id,
            StandardInterface.type == "request",
        )
        .order_by(desc(StandardInterface.updated_at), desc(StandardInterface.id))
        .limit(limit)
        .all()
    )
    interfaces: list[dict[str, Any]] = []
    for row in rows:
        interfaces.append(
            {
                "name": row.name,
                "method": row.method or "GET",
                "url": f"{row.base_url or ''}{row.api_path or ''}",
                "params": row.params or [],
                "headers": row.headers or [],
                "body": row.body_content or "",
            }
        )
    return interfaces


class APIExecuteRequest(BaseModel):
    project_id: int
    script_content: str
    requirement: str = ""
    base_url: str = ""


class APIChainRequest(BaseModel):
    project_id: int
    scenario_desc: str
    interfaces: Optional[list[dict[str, Any]]] = None


class APIMockRequest(BaseModel):
    project_id: int
    interface_info: dict[str, Any]
    mock_type: str = "single"
    count: int = Field(default=5, ge=1, le=50)


@router.post("/generate-script")
def generate_script(
    req: APIRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_project(req.project_id, db, current_user.id)

    context_bundle = context_orchestrator.assemble_context(
        WorkflowKind.API_AUTOMATION,
        req.project_id,
        db,
        user_id=current_user.id,
        query_text=(req.requirement or "")[:600],
        requirement_text=(req.requirement or "")[:2000],
        include_knowledge=True,
        include_interfaces=True,
        include_logs=True,
        knowledge_limit=4,
        interface_limit=10,
        log_limit=10,
    )
    log_workflow_trace(
        db,
        req.project_id,
        current_user.id,
        WorkflowKind.API_AUTOMATION,
        WorkflowStage.CONTEXT,
        {"action": "generate_script", **context_bundle["diagnostics"]},
    )

    script = api_tester.generate_api_test_script(
        requirement=req.requirement,
        base_url=req.base_url or "",
        api_path=req.api_path or "",
        test_types=req.test_types,
        api_docs=context_bundle["combined_context"],
        db=db,
        mode=req.mode,
        user_id=current_user.id,
    )
    log_workflow_trace(
        db,
        req.project_id,
        current_user.id,
        WorkflowKind.API_AUTOMATION,
        WorkflowStage.GENERATE,
        {"action": "generate_script", "script_length": len(script or "")},
    )
    return {"script": script, "context_diagnostics": context_bundle["diagnostics"]}


@router.post("/execute-script")
def execute_script(
    req: APIExecuteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_project(req.project_id, db, current_user.id)

    log_workflow_trace(
        db,
        req.project_id,
        current_user.id,
        WorkflowKind.API_AUTOMATION,
        WorkflowStage.EXECUTE,
        {"action": "execute_script", "script_length": len(req.script_content or "")},
    )
    result = api_tester.execute_api_tests(
        script_content=req.script_content,
        requirement=req.requirement,
        base_url=req.base_url,
        db=db,
        project_id=req.project_id,
        user_id=current_user.id,
    )
    return result


@router.post("/generate-chain")
def generate_chain(
    req: APIChainRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_project(req.project_id, db, current_user.id)

    interfaces = req.interfaces or _list_standard_interfaces(db, req.project_id, current_user.id)
    if not interfaces:
        raise HTTPException(status_code=400, detail="No interfaces available for chain generation")

    log_workflow_trace(
        db,
        req.project_id,
        current_user.id,
        WorkflowKind.API_AUTOMATION,
        WorkflowStage.PLAN,
        {"action": "generate_chain", "interfaces": len(interfaces)},
    )
    script = api_tester.generate_chain_script(
        interfaces=interfaces,
        scenario_desc=req.scenario_desc,
        db=db,
        user_id=current_user.id,
    )
    log_workflow_trace(
        db,
        req.project_id,
        current_user.id,
        WorkflowKind.API_AUTOMATION,
        WorkflowStage.GENERATE,
        {"action": "generate_chain", "script_length": len(script or "")},
    )
    return {"script": script, "interfaces_count": len(interfaces)}


@router.post("/generate-mock-data")
def generate_mock_data(
    req: APIMockRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_project(req.project_id, db, current_user.id)

    data = api_tester.generate_mock_data(
        interface_info=req.interface_info,
        mock_type=req.mock_type,
        count=req.count,
        db=db,
        user_id=current_user.id,
    )
    log_workflow_trace(
        db,
        req.project_id,
        current_user.id,
        WorkflowKind.API_AUTOMATION,
        WorkflowStage.GENERATE,
        {"action": "generate_mock_data", "count": len(data or [])},
    )
    return {"mock_data": data}


@router.get("/history")
def get_api_history(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_project(project_id, db, current_user.id)

    rows = (
        db.query(APIExecution)
        .filter(APIExecution.project_id == project_id, APIExecution.user_id == current_user.id)
        .order_by(desc(APIExecution.created_at), desc(APIExecution.id))
        .limit(50)
        .all()
    )
    items = []
    for row in rows:
        report = row.structured_report or {}
        failed = int(report.get("failed", 0)) if isinstance(report, dict) else 0
        total = int(report.get("total", 0)) if isinstance(report, dict) else 0
        status = "failed" if failed > 0 else ("success" if total > 0 else "unknown")
        items.append(
            {
                "id": row.id,
                "requirement": (row.requirement or "")[:120],
                "status": status,
                "total": total,
                "failed": failed,
                "created_at": row.created_at,
            }
        )
    return {"items": items}
