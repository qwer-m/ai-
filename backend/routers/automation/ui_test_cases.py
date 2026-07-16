from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, Field
from core.db.database import get_db
from core.db.models import User
from core.authn.auth import get_current_user
from modules.automation_components.services.ui_test_case_service import UITestCaseService

router = APIRouter(
    prefix="/ui-test-cases",
    tags=["UI Test Cases"]
)

# Pydantic Schemas
class UITestCaseCreate(BaseModel):
    project_id: int
    name: str
    type: str = "file" # folder or file
    parent_id: Optional[int] = None
    description: Optional[str] = None
    script_content: Optional[str] = None
    requirements: Optional[str] = None
    automation_type: Optional[str] = "web"
    target_config: Optional[str] = None

class UITestCaseUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None
    description: Optional[str] = None
    script_content: Optional[str] = None
    requirements: Optional[str] = None
    automation_type: Optional[str] = None
    target_config: Optional[str] = None

class UITestCaseResponse(BaseModel):
    id: int
    project_id: int
    name: str
    type: str
    parent_id: Optional[int]
    description: Optional[str]
    script_content: Optional[str]
    requirements: Optional[str]
    automation_type: Optional[str]
    target_config: Optional[str]
    code_path: Optional[str] = None
    children: List['UITestCaseResponse'] = Field(default_factory=list)

    class Config:
        from_attributes = True

UITestCaseResponse.update_forward_refs()

@router.get("", response_model=List[UITestCaseResponse])
@router.get("/", response_model=List[UITestCaseResponse], include_in_schema=False)
def get_test_cases(project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Get all test cases for a project in a flat list (frontend handles tree) 
    or just root nodes if we want to build tree recursively.
    For simplicity, we return flat list and let frontend build tree, 
    OR we can return tree structure if we use recursive query.
    Let's return the full flat list for the project, it's easier for drag-n-drop.
    """
    status, rows = UITestCaseService(db).list_cases(project_id=project_id, user_id=current_user.id)
    if status == "project_not_found":
        raise HTTPException(status_code=404, detail="Project not found")
    return rows

@router.post("", response_model=UITestCaseResponse)
@router.post("/", response_model=UITestCaseResponse, include_in_schema=False)
def create_test_case(item: UITestCaseCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        status, db_item = UITestCaseService(db).create_case(
            payload=item.model_dump(),
            user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if status == "project_not_found" or not db_item:
        raise HTTPException(status_code=404, detail="Project not found")
    return db_item

@router.put("/{item_id}", response_model=UITestCaseResponse)
def update_test_case(item_id: int, item: UITestCaseUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        status, db_item = UITestCaseService(db).update_case(
            item_id=item_id,
            payload=item.model_dump(exclude_unset=True),
            user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if status == "not_found" or not db_item:
        raise HTTPException(status_code=404, detail="Test case not found")
    return db_item

@router.delete("/{item_id}")
def delete_test_case(item_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    deleted = UITestCaseService(db).delete_case(item_id=item_id, user_id=current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Test case not found")
    return {"ok": True}
