from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class RagEvalRunCreate(BaseModel):
    """启动评测请求。"""

    dataset_id: int
    config: dict[str, Any] = Field(default_factory=dict)
    run_name: Optional[str] = None


class RagEvalRunOut(BaseModel):
    """评测运行响应。"""

    id: int
    project_id: int
    dataset_id: int
    run_name: Optional[str] = None
    status: str
    total_samples: int
    finished_samples: int
    cursor: int
    stop_requested: bool
    metrics_json: dict[str, Any] | None = None
    config_json: dict[str, Any]
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class RagEvalRunStatusResponse(BaseModel):
    """运行状态详情。"""

    run: RagEvalRunOut
    progress: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    sample_stats: dict[str, Any] = Field(default_factory=dict)


class RagEvalSampleResultOut(BaseModel):
    """单样本评测结果。"""

    id: int
    run_id: int
    sample_id: int
    first_hit_rank: Optional[int] = None
    recall_hit: bool = False
    answer_text: Optional[str] = None
    answer_correct: bool = False
    answer_correctness_score: Optional[float] = None
    faithfulness_score: Optional[float] = None
    context_precision: Optional[float] = None
    context_recall: Optional[float] = None
    failure_reason: Optional[str] = None
    failure_detail: Optional[str] = None
    latency_ms: Optional[float] = None
    retrieval_latency_ms: Optional[float] = None
    generation_latency_ms: Optional[float] = None
    token_usage_json: dict[str, Any] | None = None
    cost_json: dict[str, Any] | None = None
    detail_json: dict[str, Any] | None = None
    created_at: Optional[datetime] = None
    sample_query: Optional[str] = None
    gold_docs: list[Any] = Field(default_factory=list)
    gold_chunks: list[str] = Field(default_factory=list)
    expected_answer: Optional[str] = None
    answer_points: list[Any] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    difficulty: Optional[str] = None

    class Config:
        from_attributes = True


class RagEvalSamplesPage(BaseModel):
    """样本结果分页。"""

    items: list[RagEvalSampleResultOut]
    page: int
    page_size: int
    total: int


class RagSamplePromoteRequest(BaseModel):
    """失败样本提升请求。"""

    target_dataset_type: str = Field(..., description="challenge/regression")


class RagSamplePromoteResponse(BaseModel):
    """失败样本提升响应。"""

    success: bool
    target_dataset_id: int
    target_sample_id: int
