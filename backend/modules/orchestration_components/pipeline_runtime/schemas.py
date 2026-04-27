from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# 流水线固定阶段顺序会影响重试、恢复与阶段状态展示语义，必须保持稳定。
StageKey = Literal["test_generation", "ui_automation", "api_automation", "evaluation"]
RunStatus = Literal["pending", "running", "success", "failed"]

STAGE_ORDER: list[StageKey] = [
    "test_generation",
    "ui_automation",
    "api_automation",
    "evaluation",
]


class PipelineUIConfig(BaseModel):
    task: str = ""
    target: str = "http://localhost:5173"
    automation_type: Literal["web", "app"] = "web"


class PipelineAPIConfig(BaseModel):
    requirement: str = ""
    base_url: str = "http://127.0.0.1:8000"
    api_path: str = "/api/health"
    mode: Literal["structured", "natural"] = "structured"
    test_types: list[str] = Field(default_factory=lambda: ["Functional"])


class PipelineEvalConfig(BaseModel):
    run_testcase_eval: bool = False
    run_ui_eval: bool = True
    run_api_eval: bool = True
    baseline_test_cases: str = ""


class PipelineAgentConfig(BaseModel):
    enabled: bool = True
    planner_llm: bool = True
    reviewer_llm: bool = True
    executor_parallel: bool = True
    executor_workers: int = Field(default=3, ge=1, le=8)
    auto_retry_enabled: bool = True
    max_auto_retries: int = Field(default=1, ge=0, le=3)
    retry_policy: Literal["conservative", "balanced", "aggressive"] = "balanced"
    max_context_chars: int = Field(default=3500, ge=800, le=12000)


class PipelineRunRequest(BaseModel):
    project_id: int
    requirement: str
    expected_count: int = Field(default=20, ge=1, le=200)
    compress: bool = False
    ui: PipelineUIConfig = Field(default_factory=PipelineUIConfig)
    api: PipelineAPIConfig = Field(default_factory=PipelineAPIConfig)
    evaluation: PipelineEvalConfig = Field(default_factory=PipelineEvalConfig)
    agent: PipelineAgentConfig = Field(default_factory=PipelineAgentConfig)


class PipelineRetryRequest(BaseModel):
    from_stage: Optional[StageKey] = None


class WorkflowTraceItem(BaseModel):
    id: int
    created_at: Any
    kind: str
    stage: str
    action: str
    details: dict[str, Any]

