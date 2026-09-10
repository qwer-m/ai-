from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

class LogCreate(BaseModel):
    project_id: int
    log_type: str
    message: str

class LogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    log_type: str
    message: str
    created_at: Optional[datetime] = None
