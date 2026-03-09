from pydantic import BaseModel
from typing import Optional

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    parent_id: Optional[int] = None

class ProjectUpdate(BaseModel):
    name: str
    description: Optional[str] = None
    parent_id: Optional[int] = None
