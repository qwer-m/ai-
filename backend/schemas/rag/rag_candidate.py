from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RagCandidateGenerateFilters(BaseModel):
    """候选生成过滤条件。"""

    failure_reasons: list[str] = Field(default_factory=list)
    answer_correct_false: bool = True
    faithfulness_lt: float | None = None
    answer_correctness_lt: float | None = None


class RagCandidateGenerateRequest(BaseModel):
    """候选生成请求。"""

    run_id: int
    filters: RagCandidateGenerateFilters = Field(default_factory=RagCandidateGenerateFilters)
    target_dataset_type: str | None = None


class RagCandidateDraftUpdate(BaseModel):
    """候选草稿更新参数。"""

    gold_docs: list[Any] | None = None
    gold_chunks: list[str] | None = None
    gold_answer: str | None = None
    answer_points: list[Any] | None = None
    tags: list[str] | None = None
    difficulty: str | None = None
    metadata_filters: dict[str, Any] | None = None
    expected_doc_version: str | None = None
    notes: str | None = None


class RagCandidateApproveRequest(BaseModel):
    """候选审核通过请求。"""

    target_dataset_type: str | None = None
    draft: RagCandidateDraftUpdate | None = None


class RagCandidateRejectRequest(BaseModel):
    """候选拒绝请求。"""

    notes: str | None = None


class RagCandidateOut(BaseModel):
    """候选列表输出。"""

    id: int
    source_type: str
    source_id: int
    query: str
    retrieved_chunks: list[Any] = Field(default_factory=list)
    answer_text: str | None = None
    failure_reason: str | None = None
    judge_score_json: dict[str, Any] = Field(default_factory=dict)
    suggested_dataset_type: str
    status: str
    suggested_gold_docs: list[Any] = Field(default_factory=list)
    suggested_gold_chunks: list[str] = Field(default_factory=list)
    suggested_answer_points: list[Any] = Field(default_factory=list)
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class RagCandidateListPage(BaseModel):
    """候选分页输出。"""

    items: list[RagCandidateOut]
    page: int
    page_size: int
    total: int


class RagCandidateDraftResponse(BaseModel):
    """候选草稿输出。"""

    candidate_id: int
    draft: dict[str, Any]


class RagCandidateApproveResponse(BaseModel):
    """审核通过结果。"""

    success: bool
    candidate_id: int
    target_dataset_id: int
    target_sample_id: int
    created_new_sample: bool


class RagCandidateRejectResponse(BaseModel):
    """审核拒绝结果。"""

    success: bool
    candidate_id: int
    status: str
