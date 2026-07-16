from pydantic import BaseModel, Field
from typing import Optional

class UIRequest(BaseModel):
    url: str
    task: str
    project_id: int
    automation_type: str = "web"
    image_model: Optional[str] = None
    requirement_context: Optional[str] = None
    operation_name: Optional[str] = None
    operation_steps: list[str] = Field(default_factory=list)
    parent_id: Optional[int] = None


class UIScriptConvertRequest(UIRequest):
    script: str

class UIAutoEvalRequest(BaseModel):
    script: str
    execution_result: str
    project_id: int
    journey_json: Optional[str] = None
