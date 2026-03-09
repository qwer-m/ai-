from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class LogCreate(BaseModel):
    project_id: int
    log_type: str
    message: str

class LogRead(BaseModel):
    id: int
    project_id: int
    log_type: str
    message: str
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
