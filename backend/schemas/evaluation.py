from pydantic import BaseModel

class EvalRequest(BaseModel):
    content: str
    project_id: int
