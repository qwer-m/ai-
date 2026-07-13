from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.orm import Session

from core.ai.ai_client import get_client_for_user
from core.db.models import PipelineRun
from modules.domain.knowledge_base import knowledge_base
from .agent_decision import _aggregate_reviewer_decision
from .schemas import STAGE_ORDER, StageKey
from .support import _now_iso, _truncate_text

def _build_stage_agent_context(
    stage: StageKey,
    payload: dict[str, Any],
    artifacts: dict[str, Any],
    max_context_chars: int,
) -> str:

    stage_cfg: dict[str, Any] = {}
    if stage == "test_generation":
        stage_cfg = {
            "expected_count": payload.get("expected_count"),
            "compress": payload.get("compress"),
        }
    elif stage == "ui_automation":
        stage_cfg = dict(payload.get("ui") or {})
    elif stage == "api_automation":
        stage_cfg = dict(payload.get("api") or {})
    elif stage == "evaluation":
        stage_cfg = dict(payload.get("evaluation") or {})

    artifact_preview = {
        key: _truncate_text(value, 600)
        for key, value in artifacts.items()
        if key in STAGE_ORDER or key == "agents"
    }
    context_payload = {
        "stage": stage,
        "requirement": str(payload.get("requirement") or "")[:1200],
        "stage_config": stage_cfg,
        "available_artifacts": list(artifacts.keys()),
        "artifact_preview": artifact_preview,
    }
    return _truncate_text(context_payload, max_context_chars)


def _run_agent_llm(
    db: Session,
    user_id: int,
    *,
    system_prompt: str,
    user_prompt: str,
) -> str:

    client = get_client_for_user(user_id, db)
    model_name = client.turbo_model or client.model
    text = client.generate_response(
        user_input=user_prompt,
        system_prompt=system_prompt,
        db=db,
        max_tokens=700,
        task_type="general",
        model=model_name,
    )
    if text.startswith("Error:") or text.startswith("Exception"):
        raise RuntimeError(text)
    return text


def _build_rule_planner(stage: StageKey, payload: dict[str, Any], artifacts: dict[str, Any]) -> dict[str, Any]:

    stage_goal_map: dict[StageKey, str] = {
        "test_generation": "Generate complete and non-duplicate test cases from requirement.",
        "ui_automation": "Create and run robust UI automation against target environment.",
        "api_automation": "Generate executable API tests and run with clear pass/fail report.",
        "evaluation": "Assess generated artifacts and execution quality with actionable findings.",
    }
    checklist_map: dict[StageKey, list[str]] = {
        "test_generation": [
            "Requirement is non-empty and clear.",
            "Expected count is realistic.",
            "Generated JSON can be parsed.",
        ],
        "ui_automation": [
            "Target URL/app is reachable.",
            "Script covers key journey and assertions.",
            "Execution stderr is empty or explainable.",
        ],
        "api_automation": [
            "Base URL and API path are valid.",
            "Script includes assertions and error cases.",
            "Structured report includes total/failed.",
        ],
        "evaluation": [
            "At least one evaluation branch is enabled.",
            "Input artifacts for selected branches are present.",
            "Output contains concrete quality findings.",
        ],
    }
    return {
        "status": "ok",
        "mode": "rule",
        "goal": stage_goal_map[stage],
        "dependencies": STAGE_ORDER[: STAGE_ORDER.index(stage)],
        "checklist": checklist_map[stage],
        "artifact_keys": list(artifacts.keys()),
        "timestamp": _now_iso(),
        "requirement_len": len(str(payload.get("requirement") or "")),
    }
