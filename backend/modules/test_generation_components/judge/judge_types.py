from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JudgeStatus(str, Enum):
    PASS = "PASS"
    REPAIRABLE = "REPAIRABLE"
    REJECT = "REJECT"
    PENDING = "PENDING"


class RepairActionType(str, Enum):
    NONE = "NONE"
    PATCH_EXPECTED_RESULT = "PATCH_EXPECTED_RESULT"
    PATCH_STEPS = "PATCH_STEPS"
    APPEND_CORE_FLOW_CASE = "APPEND_CORE_FLOW_CASE"
    APPEND_REUSE_RISK_CASE = "APPEND_REUSE_RISK_CASE"
    REGENERATE_PARTIAL = "REGENERATE_PARTIAL"
    DROP_CASE = "DROP_CASE"
    ISOLATE_PENDING = "ISOLATE_PENDING"


class JudgeSignalSet(BaseModel):
    violates_confirmed_fact: bool = False
    missing_core_flow: bool = False
    missing_reuse_risk: bool = False
    contains_pending_logic: bool = False
    is_semantic_duplicate: bool = False

    confirmed_fact_hits: list[str] = Field(default_factory=list)
    confirmed_fact_violations: list[str] = Field(default_factory=list)

    reuse_risk_hits: list[str] = Field(default_factory=list)
    missing_reuse_risk_items: list[str] = Field(default_factory=list)
    duplicate_of_case_id: str = ""
    duplicate_similarity: float = 0.0

    pending_hits: list[str] = Field(default_factory=list)
    vague_or_unconfirmed_hits: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RepairAction(BaseModel):
    action_type: RepairActionType
    reason: str
    payload: dict[str, Any] = Field(default_factory=dict)
    target_case_id: str | None = None


class JudgeResult(BaseModel):
    case_id: str
    status: JudgeStatus
    signals: JudgeSignalSet
    suggested_actions: list[RepairAction] = Field(default_factory=list)

    repaired: bool = False
    repaired_pass: bool = False
    reject_reason: str = ""
    pending_reason: str = ""

    before_case: dict[str, Any] = Field(default_factory=dict)
    after_case: dict[str, Any] = Field(default_factory=dict)


class JudgeBatchResult(BaseModel):
    cases: list[JudgeResult] = Field(default_factory=list)

    core_flow_covered: bool = False
    reuse_risk_covered: bool = False

    pass_count: int = 0
    repairable_count: int = 0
    reject_count: int = 0
    pending_count: int = 0

    appended_case_count: int = 0
    repaired_case_count: int = 0

    notes: list[str] = Field(default_factory=list)
