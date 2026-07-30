from pydantic import BaseModel
from typing import ClassVar, List

from core.settings.config import settings

class TestGenRequest(BaseModel):
    __test__: ClassVar[bool] = False

    requirement: str
    project_id: int
    compress: bool = False
    expected_count: int = 20
    enable_sample_pool_feedback: bool = True
    batch_index: int = 0
    batch_size: int = settings.TEST_GENERATION_BATCH_SIZE
    current_biz_key: str = ""
    only_current_biz: bool = False
    multi_pass: bool = True
    generation_mode: str = ""

class TestComparisonRequest(BaseModel):
    __test__: ClassVar[bool] = False

    generated_test_case: str
    modified_test_case: str
    project_id: int

class RecallRequest(BaseModel):
    retrieved: List[str]
    relevant: List[str]
    project_id: int
