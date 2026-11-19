"""上下文生命周期 API 的请求契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ContextSourceKind = Literal[
    "user_preference",
    "long_term_memory",
    "validated_feedback",
]
ContextScope = Literal["global", "project", "user"]


class ContextRecordCreateRequest(BaseModel):
    """创建待确认长期上下文候选；生效必须另行确认。"""

    project_id: int | None = Field(default=None, ge=1)
    source_kind: ContextSourceKind
    scope: ContextScope
    scope_id: str | None = Field(default=None, min_length=1, max_length=120)
    revision: str = Field(min_length=1, max_length=120)
    content: dict[str, Any]
    evidence_refs: list[dict[str, Any]] = Field(min_length=1)
    origin_run_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_scope(self) -> "ContextRecordCreateRequest":
        if self.source_kind == "validated_feedback":
            if self.scope != "project" or self.project_id is None:
                raise ValueError("validated_feedback 只能按项目作用域保存")
            if str(self.scope_id or "").strip() != str(self.project_id):
                raise ValueError("validated_feedback 的 scope_id 必须等于 project_id")
        if self.scope == "project" and self.project_id is None:
            raise ValueError("project 作用域必须提供 project_id")
        if self.scope == "global" and self.scope_id is not None:
            raise ValueError("global 作用域不能提供 scope_id")
        if self.scope != "global" and not str(self.scope_id or "").strip():
            raise ValueError("非 global 作用域必须提供 scope_id")
        if not self.content:
            raise ValueError("长期上下文内容不能为空")
        return self


class ContextRecordDecisionRequest(BaseModel):
    """撤销或替代记录时使用的版本门禁。"""

    revision: str = Field(min_length=1, max_length=120)
    reason: str = Field(default="", max_length=1000)


class ContextRecordConfirmationRequest(BaseModel):
    """对具体评测差异确认可复用结论、适用范围和失效条件。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    revision: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=4000)
    applicability: str = Field(min_length=1, max_length=2000)
    invalidation_condition: str = Field(min_length=1, max_length=2000)


class ContextRecordReplaceRequest(ContextRecordDecisionRequest):
    """用新版本替代一条 active 记录。"""

    new_revision: str = Field(min_length=1, max_length=120)
    content: dict[str, Any]
    evidence_refs: list[dict[str, Any]] = Field(min_length=1)


class ContextRetrievalFeedbackRequest(BaseModel):
    """人工或独立评估器对一次检索采用结果的反馈。"""

    retrieval_index: int = Field(ge=0)
    source_id: str = Field(min_length=1, max_length=240)
    outcome: Literal["supported", "partially_supported", "unsupported", "unused"]
    notes: str = Field(default="", max_length=2000)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "ContextRecordCreateRequest",
    "ContextRecordDecisionRequest",
    "ContextRecordConfirmationRequest",
    "ContextRecordReplaceRequest",
    "ContextRetrievalFeedbackRequest",
]
