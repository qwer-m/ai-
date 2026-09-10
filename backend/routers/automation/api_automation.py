from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.model_defs import User
from modules.automation_components.services.api_automation_service import APIAutomationService
from schemas.automation.api_testing import APIRequest

router = APIRouter(prefix="/api-automation", tags=["API Automation"])


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
    status, result = APIAutomationService(db).generate_script(
        payload=req.model_dump(),
        user_id=current_user.id,
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return result


@router.post("/execute-script")
def execute_script(
    req: APIExecuteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, result = APIAutomationService(db).execute_script(
        payload=req.model_dump(),
        user_id=current_user.id,
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return result


@router.post("/generate-chain")
def generate_chain(
    req: APIChainRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, result = APIAutomationService(db).generate_chain(
        payload=req.model_dump(),
        user_id=current_user.id,
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    if status == "interfaces_missing":
        raise HTTPException(status_code=400, detail="No interfaces available for chain generation")
    return result


@router.post("/generate-mock-data")
def generate_mock_data(
    req: APIMockRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, result = APIAutomationService(db).generate_mock_data(
        payload=req.model_dump(),
        user_id=current_user.id,
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return result


@router.get("/history")
def get_api_history(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    status, result = APIAutomationService(db).get_history(
        project_id=project_id,
        user_id=current_user.id,
    )
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return result
