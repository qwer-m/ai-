from pydantic import BaseModel
from typing import Optional, List

class TestGenRequest(BaseModel):
    requirement: str
    project_id: int
    compress: bool = False
    expected_count: int = 20
    batch_index: int = 0
    batch_size: int = 20

class TestComparisonRequest(BaseModel):
    generated_test_case: str
    modified_test_case: str
    project_id: int

class RecallRequest(BaseModel):
    retrieved: List[str]
    relevant: List[str]
    project_id: int
