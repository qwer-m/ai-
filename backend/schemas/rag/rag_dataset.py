from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class RagDatasetCreate(BaseModel):
    """创建数据集请求。"""

    name: str = Field(..., min_length=1, max_length=120)
    type: str = Field(..., description="validation/test/challenge/regression")
    description: Optional[str] = None


class RagDatasetUpdate(BaseModel):
    """更新数据集请求。"""

    name: Optional[str] = Field(None, min_length=1, max_length=120)
    type: Optional[str] = Field(None, description="validation/test/challenge/regression")
    description: Optional[str] = None


class RagDatasetOut(BaseModel):
    """数据集响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: str
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    sample_count: int = 0

class RagSampleCreate(BaseModel):
    """创建样本请求。"""

    query: str = Field(..., min_length=1)
    gold_docs: list[Any] = Field(default_factory=list)
    gold_chunks: list[str] = Field(default_factory=list)
    gold_answer: Optional[str] = None
    answer_points: list[Any] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    difficulty: str = Field(default="medium", description="easy/medium/hard")
    metadata_filters: dict[str, Any] = Field(default_factory=dict)
    expected_doc_version: Optional[str] = None
    enabled: bool = True


class RagSampleUpdate(BaseModel):
    """更新样本请求。"""

    query: Optional[str] = None
    gold_docs: Optional[list[Any]] = None
    gold_chunks: Optional[list[str]] = None
    gold_answer: Optional[str] = None
    answer_points: Optional[list[Any]] = None
    tags: Optional[list[str]] = None
    difficulty: Optional[str] = None
    metadata_filters: Optional[dict[str, Any]] = None
    expected_doc_version: Optional[str] = None
    enabled: Optional[bool] = None


class RagSampleOut(BaseModel):
    """样本响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    dataset_id: int
    query: str
    gold_docs: list[Any] = Field(default_factory=list)
    gold_chunks: list[str] = Field(default_factory=list)
    gold_answer: Optional[str] = None
    answer_points: list[Any] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    difficulty: str = "medium"
    metadata_filters: dict[str, Any] = Field(default_factory=dict)
    expected_doc_version: Optional[str] = None
    enabled: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class RagDatasetImportResponse(BaseModel):
    """数据集导入响应。"""

    success: bool
    dataset_id: int
    imported_count: int
    skipped_count: int
    errors: list[str] = Field(default_factory=list)

