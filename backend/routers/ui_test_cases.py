from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from core.database import get_db
from core.models import UITestCase, Project, User
from core.auth import get_current_user

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
    children: List['UITestCaseResponse'] = []

    class Config:
        orm_mode = True

UITestCaseResponse.update_forward_refs()

def _verify_project_access(project_id: int, db: Session, current_user: User):
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project

@router.get("/", response_model=List[UITestCaseResponse])
def get_test_cases(project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Get all test cases for a project in a flat list (frontend handles tree) 
    or just root nodes if we want to build tree recursively.
    For simplicity, we return flat list and let frontend build tree, 
    OR we can return tree structure if we use recursive query.
    Let's return the full flat list for the project, it's easier for drag-n-drop.
    """
    _verify_project_access(project_id, db, current_user)
    return db.query(UITestCase).filter(UITestCase.project_id == project_id).all()

@router.post("/", response_model=UITestCaseResponse)
def create_test_case(item: UITestCaseCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _verify_project_access(item.project_id, db, current_user)
    db_item = UITestCase(**item.dict())
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item

@router.put("/{item_id}", response_model=UITestCaseResponse)
def update_test_case(item_id: int, item: UITestCaseUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    db_item = (
        db.query(UITestCase)
        .join(Project, Project.id == UITestCase.project_id)
        .filter(UITestCase.id == item_id, Project.user_id == current_user.id)
        .first()
    )
    if not db_item:
        raise HTTPException(status_code=404, detail="Test case not found")
    
    update_data = item.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_item, key, value)
    
    db.commit()
    db.refresh(db_item)
    return db_item

@router.delete("/{item_id}")
def delete_test_case(item_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    db_item = (
        db.query(UITestCase)
        .join(Project, Project.id == UITestCase.project_id)
        .filter(UITestCase.id == item_id, Project.user_id == current_user.id)
        .first()
    )
    if not db_item:
        raise HTTPException(status_code=404, detail="Test case not found")
    
    db.delete(db_item)
    db.commit()
    return {"ok": True}
