from pydantic import BaseModel
from typing import Optional

class UIRequest(BaseModel):
    url: str
    task: str
    project_id: int
    automation_type: str = "web"
    image_model: Optional[str] = None
    requirement_context: Optional[str] = None
    device_id: Optional[str] = None
    visual_asset_group: Optional[str] = None
    appium_server_url: Optional[str] = None
    reset_app_data: Optional[bool] = None

class UIAutoEvalRequest(BaseModel):
    script: str
    execution_result: str
    project_id: int
    journey_json: Optional[str] = None
