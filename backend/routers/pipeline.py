from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

from core.auth import get_current_user
from core.ai_client import get_client_for_user
from core.database import SessionLocal, engine, get_db
from core.models import LogEntry, PipelineRun, Project, User
from core.workflow import WorkflowKind, WorkflowStage, log_workflow_trace
from modules.api_testing import api_tester
from modules.evaluation import evaluator
from modules.knowledge_base import knowledge_base
from modules.test_generation import test_generator
from modules.ui_automation import ui_automator

router = APIRouter(prefix="/pipeline", tags=["Pipeline"])

StageKey = Literal["test_generation", "ui_automation", "api_automation", "evaluation"]
RunStatus = Literal["pending", "running", "success", "failed"]

STAGE_ORDER: list[StageKey] = [
    "test_generation",
    "ui_automation",
    "api_automation",
    "evaluation",
]

STAGE_WORKFLOW_KIND: dict[StageKey, WorkflowKind] = {
    "test_generation": WorkflowKind.TEST_GENERATION,
    "ui_automation": WorkflowKind.UI_AUTOMATION,
    "api_automation": WorkflowKind.API_AUTOMATION,
    "evaluation": WorkflowKind.EVALUATION,
}

STAGE_WORKFLOW_STAGE: dict[StageKey, WorkflowStage] = {
    "test_generation": WorkflowStage.GENERATE,
    "ui_automation": WorkflowStage.EXECUTE,
    "api_automation": WorkflowStage.EXECUTE,
    "evaluation": WorkflowStage.EVALUATE,
}

_worker_lock = threading.Lock()
_worker_threads: dict[int, threading.Thread] = {}


def _ensure_pipeline_table() -> None:
    try:
        PipelineRun.__table__.create(bind=engine, checkfirst=True)
    except Exception:
        # Do not block app startup for DDL errors; endpoint will fail explicitly on write.
        pass


_ensure_pipeline_table()


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


def _get_owned_project(project_id: int, db: Session, user_id: int) -> Project:
    project = db.query(Project).filter(Project.id == project_id, Project.user_id == user_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _default_stage_states() -> dict[str, dict[str, Any]]:
    return {stage: {"status": "idle", "message": "", "started_at": None, "ended_at": None} for stage in STAGE_ORDER}


def _serialize_run(run: PipelineRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "user_id": run.user_id,
        "status": run.status,
        "current_stage": run.current_stage,
        "request_payload": run.request_payload or {},
        "stage_states": run.stage_states or _default_stage_states(),
        "artifacts": run.artifacts or {},
        "error_message": run.error_message or "",
        "retry_of_run_id": run.retry_of_run_id,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "updated_at": run.updated_at,
    }


def _persist_run(
    db: Session,
    run: PipelineRun,
    *,
    status: Optional[RunStatus] = None,
    current_stage: Optional[str] = None,
    stage_states: Optional[dict[str, Any]] = None,
    artifacts: Optional[dict[str, Any]] = None,
    error_message: Optional[str] = None,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
) -> None:
    if status is not None:
        run.status = status
    if current_stage is not None:
        run.current_stage = current_stage
    if stage_states is not None:
        run.stage_states = stage_states
    if artifacts is not None:
        run.artifacts = artifacts
    if error_message is not None:
        run.error_message = error_message
    if started_at is not None:
        run.started_at = started_at
    if finished_at is not None:
        run.finished_at = finished_at
    db.add(run)
    db.commit()
    db.refresh(run)


def _mark_stage(
    stage_states: dict[str, Any],
    stage: StageKey,
    status: Literal["idle", "running", "success", "failed", "skipped"],
    message: str,
) -> None:
    row = dict(stage_states.get(stage) or {})
    row["status"] = status
    row["message"] = message
    if status == "running":
        row["started_at"] = row.get("started_at") or _now_iso()
        row["ended_at"] = None
    elif status in {"success", "failed", "skipped"}:
        row["ended_at"] = _now_iso()
        row["started_at"] = row.get("started_at") or row["ended_at"]
    stage_states[stage] = row


def _parse_workflow_trace(message: str) -> Optional[dict[str, Any]]:
    prefix = "WORKFLOW_TRACE:"
    if not message or not message.startswith(prefix):
        return None
    payload = message[len(prefix) :].strip()
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    details = data.get("details")
    data["details"] = details if isinstance(details, dict) else {}
    return data


def _to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _truncate_text(value: Any, limit: int) -> str:
    text = _to_text(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...(truncated)"


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


def _build_rule_reviewer(
    stage: StageKey,
    stage_status: str,
    stage_message: str,
    artifacts: dict[str, Any],
) -> dict[str, Any]:
    verdict = "pass" if stage_status in {"success", "skipped"} else "needs_attention"
    return {
        "status": "ok",
        "mode": "rule",
        "verdict": verdict,
        "stage_status": stage_status,
        "stage_message": stage_message,
        "artifact_present": stage in artifacts,
        "timestamp": _now_iso(),
    }


def _build_executor_tasks(stage: StageKey) -> list[dict[str, str]]:
    if stage == "test_generation":
        return [
            {"id": "tg_requirement", "title": "Validate requirement input"},
            {"id": "tg_params", "title": "Check generation parameters"},
            {"id": "tg_json", "title": "Prepare JSON parse guard"},
        ]
    if stage == "ui_automation":
        return [
            {"id": "ui_target", "title": "Validate UI target"},
            {"id": "ui_task", "title": "Resolve UI task description"},
            {"id": "ui_exec", "title": "Prepare execution fallback"},
        ]
    if stage == "api_automation":
        return [
            {"id": "api_base", "title": "Validate API base URL"},
            {"id": "api_types", "title": "Validate API test types"},
            {"id": "api_report", "title": "Prepare report parsing"},
        ]
    return [
        {"id": "eval_switch", "title": "Validate evaluation switches"},
        {"id": "eval_inputs", "title": "Check required evaluation artifacts"},
        {"id": "eval_output", "title": "Prepare output merge"},
    ]


def _evaluate_executor_task(
    stage: StageKey,
    task: dict[str, str],
    payload: dict[str, Any],
    artifacts: dict[str, Any],
) -> dict[str, Any]:
    requirement = str(payload.get("requirement") or "").strip()
    ui_cfg = dict(payload.get("ui") or {})
    api_cfg = dict(payload.get("api") or {})
    eval_cfg = dict(payload.get("evaluation") or {})

    status = "ready"
    note = "ok"
    if task["id"] == "tg_requirement" and not requirement:
        status = "warning"
        note = "Global requirement is empty."
    elif task["id"] == "tg_params" and int(payload.get("expected_count") or 0) <= 0:
        status = "warning"
        note = "expected_count must be > 0."
    elif task["id"] == "ui_target" and not str(ui_cfg.get("target") or "").strip():
        status = "warning"
        note = "UI target is empty."
    elif task["id"] == "ui_task" and not (str(ui_cfg.get("task") or "").strip() or requirement):
        status = "warning"
        note = "No UI task and no global requirement fallback."
    elif task["id"] == "api_base" and not str(api_cfg.get("base_url") or "").strip():
        status = "warning"
        note = "API base_url is empty."
    elif task["id"] == "api_types" and not list(api_cfg.get("test_types") or []):
        status = "warning"
        note = "API test_types is empty."
    elif task["id"] == "eval_inputs":
        wants_tc = bool(eval_cfg.get("run_testcase_eval"))
        baseline = str(eval_cfg.get("baseline_test_cases") or "").strip()
        if wants_tc and not baseline:
            status = "warning"
            note = "Testcase eval enabled but baseline_test_cases missing."
    elif task["id"] == "eval_switch":
        if not (
            bool(eval_cfg.get("run_testcase_eval"))
            or bool(eval_cfg.get("run_ui_eval", True))
            or bool(eval_cfg.get("run_api_eval", True))
        ):
            status = "warning"
            note = "No evaluation switch is enabled."
    elif task["id"] == "eval_output" and not artifacts:
        status = "warning"
        note = "No prior artifacts found."

    return {
        "id": task["id"],
        "title": task["title"],
        "status": status,
        "note": note,
    }


def _run_stage_executor_agent(
    stage: StageKey,
    payload: dict[str, Any],
    artifacts: dict[str, Any],
    agent_cfg: dict[str, Any],
) -> dict[str, Any]:
    tasks = _build_executor_tasks(stage)
    task_results: list[dict[str, Any]] = []
    parallel = bool(agent_cfg.get("executor_parallel", True))
    workers = int(agent_cfg.get("executor_workers") or 3)

    if parallel and len(tasks) > 1:
        indexed: dict[str, int] = {task["id"]: idx for idx, task in enumerate(tasks)}
        futures = {}
        with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix=f"executor-{stage}") as pool:
            for task in tasks:
                future = pool.submit(_evaluate_executor_task, stage, task, payload, artifacts)
                futures[future] = task["id"]
            for future in as_completed(futures):
                task_id = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = {
                        "id": task_id,
                        "title": next((t["title"] for t in tasks if t["id"] == task_id), task_id),
                        "status": "warning",
                        "note": f"executor task failed: {type(e).__name__}: {e}",
                    }
                task_results.append(result)
        task_results.sort(key=lambda item: indexed.get(str(item.get("id") or ""), 999))
    else:
        for task in tasks:
            task_results.append(_evaluate_executor_task(stage, task, payload, artifacts))

    warnings = sum(1 for item in task_results if item.get("status") != "ready")
    return {
        "status": "ok",
        "mode": "rule_parallel" if parallel else "rule",
        "tasks": task_results,
        "warnings": warnings,
        "workers": max(1, workers) if parallel else 1,
        "timestamp": _now_iso(),
    }


def _classify_failure_retryability(
    stage: StageKey,
    stage_message: str,
    stage_meta: dict[str, Any],
) -> dict[str, str]:
    message = (stage_message or "").lower()
    exception_type = str(stage_meta.get("exception_type") or "").lower()
    failed_count = int(stage_meta.get("failed") or 0)

    non_retryable_patterns = [
        r"missing pipeline requirement",
        r"missing .*baseline",
        r"saved ai api key cannot be decrypted",
        r"invalid token",
        r"api key",
        r"permission denied",
        r"not found",
        r"invalid parameter",
        r"validation",
        r"syntax",
    ]
    retryable_patterns = [
        r"timeout",
        r"timed out",
        r"temporarily unavailable",
        r"connection reset",
        r"connection aborted",
        r"connection refused",
        r"network",
        r"429",
        r"rate limit",
        r"too many requests",
        r"service unavailable",
        r"\b5\d\d\b",
        r"redis",
    ]

    if exception_type in {"valueerror", "keyerror", "permissionerror"}:
        return {"retryability": "non_retryable", "reason": f"exception_type:{exception_type}"}
    if stage == "api_automation" and failed_count > 0:
        return {"retryability": "non_retryable", "reason": "api_assertion_failures"}

    for pattern in non_retryable_patterns:
        if re.search(pattern, message):
            return {"retryability": "non_retryable", "reason": f"pattern:{pattern}"}
    for pattern in retryable_patterns:
        if re.search(pattern, message):
            return {"retryability": "retryable", "reason": f"pattern:{pattern}"}

    if exception_type in {"timeouterror", "connectionerror"}:
        return {"retryability": "retryable", "reason": f"exception_type:{exception_type}"}

    return {"retryability": "unknown", "reason": "no_match"}


def _aggregate_reviewer_decision(
    stage: StageKey,
    stage_status: str,
    stage_message: str,
    stage_meta: dict[str, Any],
    reviewer_result: dict[str, Any],
    *,
    attempt_index: int,
    max_auto_retries: int,
    auto_retry_enabled: bool,
    retry_policy: Literal["conservative", "balanced", "aggressive"] = "balanced",
) -> dict[str, Any]:
    verdict = str(reviewer_result.get("verdict") or "")
    llm_review = str(reviewer_result.get("llm_review") or "").lower()
    llm_force_retry = "force retry" in llm_review
    llm_no_retry = "do not retry" in llm_review or "no retry" in llm_review
    llm_retry_hint = "retry" in llm_review
    can_retry = auto_retry_enabled and attempt_index <= max_auto_retries
    should_retry = False
    reason = "no_retry"
    classification = _classify_failure_retryability(stage, stage_message, stage_meta)

    if stage_status != "failed":
        should_retry = False
        reason = "stage_not_failed"
    elif not can_retry:
        should_retry = False
        reason = "retry_budget_exhausted_or_disabled"
    elif classification["retryability"] == "non_retryable":
        should_retry = False
        reason = "non_retryable_failure"
    else:
        # classification == retryable / unknown
        if retry_policy == "conservative":
            should_retry = classification["retryability"] == "retryable" and (verdict == "needs_attention" or llm_retry_hint)
            reason = "conservative_retryable_only" if should_retry else "conservative_blocked"
        elif retry_policy == "aggressive":
            should_retry = verdict == "needs_attention" or llm_retry_hint or llm_force_retry
            reason = "aggressive_policy_retry" if should_retry else "aggressive_blocked"
        else:
            # balanced
            if classification["retryability"] == "retryable":
                should_retry = verdict == "needs_attention" or llm_retry_hint
                reason = "balanced_retryable" if should_retry else "balanced_retryable_but_blocked"
            else:
                # unknown: require stronger signal from reviewer
                should_retry = llm_force_retry or (verdict == "needs_attention" and llm_retry_hint)
                reason = "balanced_unknown_with_signal" if should_retry else "balanced_unknown_blocked"

        if llm_no_retry:
            should_retry = False
            reason = "llm_forbid_retry"

    return {
        "should_retry": should_retry,
        "reason": reason,
        "retryability": classification["retryability"],
        "retryability_reason": classification["reason"],
        "retry_policy": retry_policy,
        "attempt_index": attempt_index,
        "max_auto_retries": max_auto_retries,
    }


def _upsert_agent_artifact(
    artifacts: dict[str, Any],
    stage: StageKey,
    role: Literal["planner", "executor", "reviewer"],
    data: dict[str, Any],
) -> dict[str, Any]:
    next_artifacts = dict(artifacts or {})
    agent_root = dict(next_artifacts.get("agents") or {})
    stage_agents = dict(agent_root.get(stage) or {})
    stage_agents[role] = data
    agent_root[stage] = stage_agents
    next_artifacts["agents"] = agent_root
    return next_artifacts


def _build_agent_learning_content(run: PipelineRun, artifacts: dict[str, Any]) -> str:
    agent_root = dict((artifacts or {}).get("agents") or {})
    lines: list[str] = [
        "# Agent Learning Snapshot",
        f"- run_id: {run.id}",
        f"- project_id: {run.project_id}",
        f"- status: {run.status}",
        f"- created_at: {run.created_at}",
        f"- finished_at: {run.finished_at}",
    ]

    for stage in STAGE_ORDER:
        stage_agents = dict(agent_root.get(stage) or {})
        planner = dict(stage_agents.get("planner") or {})
        executor = dict(stage_agents.get("executor") or {})
        reviewer = dict(stage_agents.get("reviewer") or {})
        decision = dict(reviewer.get("decision") or {})
        lines.extend(
            [
                "",
                f"## {stage}",
                f"- planner_llm_status: {planner.get('llm_status', 'n/a')}",
                f"- executor_warnings: {executor.get('warnings', 'n/a')}",
                f"- executor_workers: {executor.get('workers', 'n/a')}",
                f"- reviewer_verdict: {reviewer.get('verdict', 'n/a')}",
                f"- reviewer_llm_status: {reviewer.get('llm_status', 'n/a')}",
                f"- decision_should_retry: {decision.get('should_retry', 'n/a')}",
                f"- decision_reason: {decision.get('reason', 'n/a')}",
                f"- decision_retryability: {decision.get('retryability', 'n/a')}",
                f"- decision_retry_policy: {decision.get('retry_policy', 'n/a')}",
            ]
        )
        if reviewer.get("llm_review"):
            lines.extend(
                [
                    "",
                    "### reviewer_llm_review",
                    _truncate_text(reviewer.get("llm_review"), 1800),
                ]
            )
    return "\n".join(lines)


def _save_agent_learning_snapshot(
    db: Session,
    run: PipelineRun,
    artifacts: dict[str, Any],
) -> tuple[bool, str]:
    try:
        content = _build_agent_learning_content(run, artifacts)
        filename = f"agent_learning_run_{run.id}.md"
        created = knowledge_base.add_document(
            filename=filename,
            content=content,
            doc_type="agent_learning",
            project_id=run.project_id,
            db=db,
            force=False,
            user_id=run.user_id,
        )
        if isinstance(created, dict) and created.get("status") == "duplicate":
            return True, "duplicate"
        return True, "saved"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _run_stage_planner_agent(
    db: Session,
    user_id: int,
    stage: StageKey,
    payload: dict[str, Any],
    artifacts: dict[str, Any],
    agent_cfg: dict[str, Any],
) -> dict[str, Any]:
    planner = _build_rule_planner(stage, payload, artifacts)
    if not bool(agent_cfg.get("planner_llm", True)):
        planner["llm_status"] = "disabled"
        return planner

    max_context_chars = int(agent_cfg.get("max_context_chars") or 3500)
    context_text = _build_stage_agent_context(stage, payload, artifacts, max_context_chars=max_context_chars)
    system_prompt = (
        "You are PlannerAgent in a multi-agent QA orchestration system. "
        "Return concise execution plan and risk notes for current stage."
    )
    user_prompt = (
        f"Stage: {stage}\n"
        "Provide:\n"
        "1) 3-5 concrete execution steps\n"
        "2) top 3 risks\n"
        "3) go/no-go decision\n\n"
        f"Context:\n{context_text}"
    )
    try:
        llm_text = _run_agent_llm(
            db,
            user_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        planner["llm_status"] = "ok"
        planner["llm_plan"] = _truncate_text(llm_text, 2400)
    except Exception as e:
        planner["llm_status"] = "error"
        planner["llm_error"] = f"{type(e).__name__}: {e}"
    return planner


def _run_stage_reviewer_agent(
    db: Session,
    user_id: int,
    stage: StageKey,
    payload: dict[str, Any],
    artifacts: dict[str, Any],
    stage_status: str,
    stage_message: str,
    agent_cfg: dict[str, Any],
) -> dict[str, Any]:
    reviewer = _build_rule_reviewer(stage, stage_status, stage_message, artifacts)
    if not bool(agent_cfg.get("reviewer_llm", True)):
        reviewer["llm_status"] = "disabled"
        return reviewer

    max_context_chars = int(agent_cfg.get("max_context_chars") or 3500)
    context_text = _build_stage_agent_context(stage, payload, artifacts, max_context_chars=max_context_chars)
    system_prompt = (
        "You are ReviewerAgent in a multi-agent QA orchestration system. "
        "Evaluate stage quality and propose next action."
    )
    user_prompt = (
        f"Stage: {stage}\n"
        f"Stage status: {stage_status}\n"
        f"Stage message: {stage_message}\n"
        "Provide:\n"
        "1) verdict(pass/needs_attention)\n"
        "2) top issues\n"
        "3) next_action\n\n"
        f"Context:\n{context_text}"
    )
    try:
        llm_text = _run_agent_llm(
            db,
            user_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        reviewer["llm_status"] = "ok"
        reviewer["llm_review"] = _truncate_text(llm_text, 2400)
    except Exception as e:
        reviewer["llm_status"] = "error"
        reviewer["llm_error"] = f"{type(e).__name__}: {e}"
    return reviewer


def _execute_stage_once(
    stage: StageKey,
    payload: dict[str, Any],
    artifacts: dict[str, Any],
    db: Session,
    project_id: int,
    user_id: int,
) -> dict[str, Any]:
    stage_artifacts = dict(artifacts or {})
    try:
        if stage == "test_generation":
            requirement = str(payload.get("requirement") or "").strip()
            if not requirement:
                raise ValueError("Missing pipeline requirement.")
            expected_count = int(payload.get("expected_count") or 20)
            compress = bool(payload.get("compress") or False)

            test_cases = test_generator.generate_test_cases_json(
                requirement=requirement,
                project_id=project_id,
                db=db,
                doc_type="requirement",
                compress=compress,
                expected_count=max(1, expected_count),
                batch_size=20,
                batch_index=0,
                user_id=user_id,
            )
            if isinstance(test_cases, dict) and test_cases.get("error"):
                raise RuntimeError(str(test_cases.get("error")))

            generated_text = test_cases if isinstance(test_cases, str) else json.dumps(test_cases, ensure_ascii=False)
            generated_count = len(test_cases) if isinstance(test_cases, list) else 1
            stage_artifacts["test_generation"] = {
                "generated_cases": generated_text,
                "generated_count": generated_count,
            }
            return {
                "status": "success",
                "message": f"Generated {generated_count} cases.",
                "artifacts": stage_artifacts,
                "meta": {"generated_count": generated_count},
            }

        if stage == "ui_automation":
            ui_cfg = dict(payload.get("ui") or {})
            task = str(ui_cfg.get("task") or "").strip() or str(payload.get("requirement") or "").strip()
            target = str(ui_cfg.get("target") or "http://localhost:5173")
            automation_type = str(ui_cfg.get("automation_type") or "web")

            script = ui_automator.generate_ai_image_recognition_script(
                task_description=task,
                url=target,
                automation_type=automation_type,
                db=db,
                user_id=user_id,
                token=None,
                image_model=None,
                requirement_context=None,
            )
            exec_result = ui_automator.execute_script(
                script=script,
                url=target,
                task_description=task,
                automation_type=automation_type,
                db=db,
                project_id=project_id,
                user_id=user_id,
            )
            exec_status = str(exec_result.get("status") or "failed")
            output = (
                f"status: {exec_status}\n\nstdout:\n{exec_result.get('stdout') or ''}\n\n"
                f"stderr:\n{exec_result.get('stderr') or exec_result.get('error') or ''}"
            )
            stage_artifacts["ui_automation"] = {
                "script": script or "",
                "execution_result": output,
                "raw_result": exec_result,
            }
            if exec_status == "failed":
                return {
                    "status": "failed",
                    "message": "UI execution returned failed status.",
                    "artifacts": stage_artifacts,
                    "meta": {"exec_status": exec_status},
                }
            return {
                "status": "success",
                "message": "UI automation completed.",
                "artifacts": stage_artifacts,
                "meta": {"exec_status": exec_status},
            }

        if stage == "api_automation":
            api_cfg = dict(payload.get("api") or {})
            api_requirement = str(api_cfg.get("requirement") or "").strip() or str(payload.get("requirement") or "").strip()
            base_url = str(api_cfg.get("base_url") or "")
            api_path = str(api_cfg.get("api_path") or "")
            mode = str(api_cfg.get("mode") or "structured")
            test_types = list(api_cfg.get("test_types") or ["Functional"])

            script = api_tester.generate_api_test_script(
                requirement=api_requirement,
                base_url=base_url,
                api_path=api_path,
                test_types=test_types,
                api_docs="",
                db=db,
                mode=mode,
                user_id=user_id,
            )
            exec_result = api_tester.execute_api_tests(
                script_content=script,
                requirement=api_requirement,
                base_url=base_url,
                db=db,
                project_id=project_id,
                user_id=user_id,
            )
            failed = int(((exec_result.get("structured_report") or {}).get("failed") or 0))
            output = (
                f"result:\n{exec_result.get('result') or ''}\n\nstructured_report:\n"
                f"{json.dumps(exec_result.get('structured_report') or {}, ensure_ascii=False)}"
            )
            stage_artifacts["api_automation"] = {
                "script": script or "",
                "execution_result": output,
                "raw_result": exec_result,
            }
            if failed > 0:
                return {
                    "status": "failed",
                    "message": f"API tests completed with {failed} failures.",
                    "artifacts": stage_artifacts,
                    "meta": {"failed": failed},
                }
            return {
                "status": "success",
                "message": "API automation completed.",
                "artifacts": stage_artifacts,
                "meta": {"failed": failed},
            }

        eval_cfg = dict(payload.get("evaluation") or {})
        run_testcase_eval = bool(eval_cfg.get("run_testcase_eval"))
        run_ui_eval = bool(eval_cfg.get("run_ui_eval", True))
        run_api_eval = bool(eval_cfg.get("run_api_eval", True))
        baseline = str(eval_cfg.get("baseline_test_cases") or "")

        sections: list[str] = []
        warnings: list[str] = []
        selected_any = run_testcase_eval or run_ui_eval or run_api_eval

        if run_testcase_eval:
            generated_cases = str((stage_artifacts.get("test_generation") or {}).get("generated_cases") or "")
            if generated_cases.strip() and baseline.strip():
                result = evaluator.compare_test_cases(
                    generated_test_case=generated_cases,
                    modified_test_case=baseline,
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                )
                sections.append(f"## Test Case Evaluation\n{result}")
            else:
                warnings.append("Test case evaluation skipped: missing generated cases or baseline.")

        if run_ui_eval:
            ui_art = dict(stage_artifacts.get("ui_automation") or {})
            script = str(ui_art.get("script") or "")
            execution_result = str(ui_art.get("execution_result") or "")
            if script.strip() and execution_result.strip():
                result = evaluator.evaluate_ui_automation(
                    ui_script=script,
                    execution_result=execution_result,
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                    journey_json=None,
                )
                sections.append(f"## UI Evaluation\n{result}")
            else:
                warnings.append("UI evaluation skipped: missing script or execution result.")

        if run_api_eval:
            api_art = dict(stage_artifacts.get("api_automation") or {})
            script = str(api_art.get("script") or "")
            execution_result = str(api_art.get("execution_result") or "")
            if script.strip() and execution_result.strip():
                result = evaluator.evaluate_api_test(
                    api_script=script,
                    execution_result=execution_result,
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                    openapi_spec=None,
                )
                sections.append(f"## API Evaluation\n{result}")
            else:
                warnings.append("API evaluation skipped: missing script or execution result.")

        if not selected_any:
            return {
                "status": "skipped",
                "message": "No evaluation selected.",
                "artifacts": stage_artifacts,
                "meta": {"warnings": warnings},
            }

        output = "\n\n".join([*sections, *(["## Evaluation Warnings", *warnings] if warnings else [])])
        stage_artifacts["evaluation"] = {"output": output, "warnings": warnings}
        if sections:
            return {
                "status": "success",
                "message": "Evaluation completed.",
                "artifacts": stage_artifacts,
                "meta": {"sections": len(sections), "warnings": len(warnings)},
            }
        return {
            "status": "failed",
            "message": "; ".join(warnings) or "Evaluation failed.",
            "artifacts": stage_artifacts,
            "meta": {"warnings": warnings},
        }
    except Exception as e:
        return {
            "status": "failed",
            "message": f"{type(e).__name__}: {e}",
            "artifacts": stage_artifacts,
            "meta": {"exception_type": type(e).__name__},
        }


def _log_stage_trace(
    db: Session,
    project_id: int,
    user_id: int,
    run_id: int,
    stage: StageKey,
    action: str,
    **extra: Any,
) -> None:
    details = {"action": action, "stage": stage, "run_id": run_id, **extra}
    log_workflow_trace(
        db,
        project_id,
        user_id,
        STAGE_WORKFLOW_KIND[stage],
        STAGE_WORKFLOW_STAGE[stage],
        details,
    )


def _find_resume_stage(stage_states: dict[str, Any]) -> Optional[StageKey]:
    for stage in STAGE_ORDER:
        status = str((stage_states.get(stage) or {}).get("status") or "idle")
        if status in {"idle", "failed", "pending"}:
            return stage
    return None


def _start_worker(run_id: int, start_stage: StageKey) -> None:
    with _worker_lock:
        worker = _worker_threads.get(run_id)
        if worker and worker.is_alive():
            return
        thread = threading.Thread(
            target=_run_pipeline_worker,
            args=(run_id, start_stage),
            daemon=True,
            name=f"pipeline-run-{run_id}",
        )
        _worker_threads[run_id] = thread
        thread.start()


def _run_pipeline_worker(run_id: int, start_stage: StageKey) -> None:
    db = SessionLocal()
    try:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if not run:
            return

        payload = dict(run.request_payload or {})
        stage_states = dict(run.stage_states or _default_stage_states())
        artifacts = dict(run.artifacts or {})
        user_id = run.user_id
        project_id = run.project_id
        agent_cfg = dict(payload.get("agent") or {})
        agent_enabled = bool(agent_cfg.get("enabled", True))
        auto_retry_enabled = agent_enabled and bool(agent_cfg.get("auto_retry_enabled", True))
        max_auto_retries = int(agent_cfg.get("max_auto_retries") or 1) if auto_retry_enabled else 0

        _persist_run(
            db,
            run,
            status="running",
            current_stage=start_stage,
            stage_states=stage_states,
            artifacts=artifacts,
            error_message="",
            started_at=run.started_at or datetime.utcnow(),
            finished_at=None,
        )

        start_index = STAGE_ORDER.index(start_stage)
        any_stage_failed = False

        for stage in STAGE_ORDER[start_index:]:
            run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
            if not run:
                return
            stage_states = dict(run.stage_states or _default_stage_states())
            artifacts = dict(run.artifacts or {})

            _mark_stage(stage_states, stage, "running", "Stage started.")
            _persist_run(db, run, status="running", current_stage=stage, stage_states=stage_states, artifacts=artifacts)
            _log_stage_trace(db, project_id, user_id, run_id, stage, "pipeline_stage_start")

            if agent_enabled:
                planner_result = _run_stage_planner_agent(db, user_id, stage, payload, artifacts, agent_cfg)
                artifacts = _upsert_agent_artifact(artifacts, stage, "planner", planner_result)
                _log_stage_trace(
                    db,
                    project_id,
                    user_id,
                    run_id,
                    stage,
                    "agent_planner_ready",
                    llm_status=str(planner_result.get("llm_status") or ""),
                )

                executor_result = _run_stage_executor_agent(stage, payload, artifacts, agent_cfg)
                artifacts = _upsert_agent_artifact(artifacts, stage, "executor", executor_result)
                _log_stage_trace(
                    db,
                    project_id,
                    user_id,
                    run_id,
                    stage,
                    "agent_executor_ready",
                    warnings=int(executor_result.get("warnings") or 0),
                    workers=int(executor_result.get("workers") or 1),
                )
                _persist_run(db, run, status="running", current_stage=stage, stage_states=stage_states, artifacts=artifacts)

            attempt_index = 0
            while True:
                attempt_index += 1
                stage_result = _execute_stage_once(stage, payload, artifacts, db, project_id, user_id)
                stage_status = str(stage_result.get("status") or "failed")
                stage_message = str(stage_result.get("message") or "Unknown stage error.")
                stage_meta = dict(stage_result.get("meta") or {})
                artifacts = dict(stage_result.get("artifacts") or artifacts)

                if stage_status == "success":
                    _mark_stage(stage_states, stage, "success", stage_message)
                    _log_stage_trace(
                        db,
                        project_id,
                        user_id,
                        run_id,
                        stage,
                        "pipeline_stage_success",
                        attempt=attempt_index,
                        **stage_meta,
                    )
                elif stage_status == "skipped":
                    _mark_stage(stage_states, stage, "skipped", stage_message)
                    _log_stage_trace(
                        db,
                        project_id,
                        user_id,
                        run_id,
                        stage,
                        "pipeline_stage_skipped",
                        attempt=attempt_index,
                        reason=stage_message,
                    )
                else:
                    _mark_stage(stage_states, stage, "failed", stage_message)
                    _log_stage_trace(
                        db,
                        project_id,
                        user_id,
                        run_id,
                        stage,
                        "pipeline_stage_failed",
                        attempt=attempt_index,
                        reason=stage_message,
                        **stage_meta,
                    )

                decision = {"should_retry": False, "reason": "agent_disabled"}
                if agent_enabled:
                    reviewer_result = _run_stage_reviewer_agent(
                        db,
                        user_id,
                        stage,
                        payload,
                        artifacts,
                        stage_status,
                        stage_message,
                        agent_cfg,
                    )
                    decision = _aggregate_reviewer_decision(
                        stage,
                        stage_status,
                        stage_message,
                        stage_meta,
                        reviewer_result,
                        attempt_index=attempt_index,
                        max_auto_retries=max_auto_retries,
                        auto_retry_enabled=auto_retry_enabled,
                        retry_policy=str(agent_cfg.get("retry_policy") or "balanced"),
                    )
                    reviewer_result["decision"] = decision
                    artifacts = _upsert_agent_artifact(artifacts, stage, "reviewer", reviewer_result)
                    _log_stage_trace(
                        db,
                        project_id,
                        user_id,
                        run_id,
                        stage,
                        "agent_reviewer_ready",
                        verdict=str(reviewer_result.get("verdict") or ""),
                        llm_status=str(reviewer_result.get("llm_status") or ""),
                        should_retry=bool(decision.get("should_retry")),
                        decision_reason=str(decision.get("reason") or ""),
                        retryability=str(decision.get("retryability") or ""),
                        retry_policy=str(decision.get("retry_policy") or ""),
                    )

                if stage_status == "failed" and bool(decision.get("should_retry")):
                    _log_stage_trace(
                        db,
                        project_id,
                        user_id,
                        run_id,
                        stage,
                        "pipeline_stage_auto_retry",
                        attempt=attempt_index,
                        reason=str(decision.get("reason") or ""),
                    )
                    _mark_stage(
                        stage_states,
                        stage,
                        "running",
                        f"Auto retry {attempt_index}/{max_auto_retries} triggered: {decision.get('reason')}",
                    )
                    _persist_run(
                        db,
                        run,
                        status="running",
                        current_stage=stage,
                        stage_states=stage_states,
                        artifacts=artifacts,
                    )
                    continue

                if stage_status == "failed":
                    any_stage_failed = True
                    _persist_run(
                        db,
                        run,
                        stage_states=stage_states,
                        artifacts=artifacts,
                        error_message=stage_message,
                    )
                    if stage == "test_generation":
                        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
                        if run:
                            _persist_run(
                                db,
                                run,
                                status="failed",
                                current_stage=stage,
                                stage_states=stage_states,
                                artifacts=artifacts,
                                error_message=stage_message,
                                finished_at=datetime.utcnow(),
                            )
                        return
                else:
                    _persist_run(
                        db,
                        run,
                        stage_states=stage_states,
                        artifacts=artifacts,
                        error_message="",
                    )
                break

        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if run:
            final_status: RunStatus = "failed" if any_stage_failed else "success"
            _persist_run(
                db,
                run,
                status=final_status,
                current_stage=None,
                error_message=run.error_message if final_status == "failed" else "",
                finished_at=datetime.utcnow(),
            )
            run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
            if run and agent_enabled:
                ok, status = _save_agent_learning_snapshot(db, run, dict(run.artifacts or {}))
                _log_stage_trace(
                    db,
                    project_id,
                    user_id,
                    run_id,
                    "evaluation",
                    "agent_learning_saved" if ok else "agent_learning_failed",
                    detail=status,
                )
    except Exception as worker_error:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if run:
            _persist_run(
                db,
                run,
                status="failed",
                current_stage=run.current_stage,
                error_message=f"{type(worker_error).__name__}: {worker_error}",
                finished_at=datetime.utcnow(),
            )
    finally:
        db.close()
        with _worker_lock:
            _worker_threads.pop(run_id, None)

@router.post("/runs")
def create_pipeline_run(
    req: PipelineRunRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    payload = req.model_dump()
    project_id = req.project_id
    _get_owned_project(project_id, db, current_user.id)

    stage_states = _default_stage_states()
    run = PipelineRun(
        user_id=current_user.id,
        project_id=project_id,
        status="pending",
        current_stage="test_generation",
        request_payload=payload,
        stage_states=stage_states,
        artifacts={},
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    _start_worker(run.id, "test_generation")
    return {"run": _serialize_run(run)}


@router.get("/runs")
def list_pipeline_runs(
    project_id: int = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_project(project_id, db, current_user.id)
    rows = (
        db.query(PipelineRun)
        .filter(PipelineRun.project_id == project_id, PipelineRun.user_id == current_user.id)
        .order_by(desc(PipelineRun.created_at), desc(PipelineRun.id))
        .limit(limit)
        .all()
    )
    return {"items": [_serialize_run(row) for row in rows]}


@router.get("/runs/{run_id}")
def get_pipeline_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id, PipelineRun.user_id == current_user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run": _serialize_run(run)}


@router.post("/runs/{run_id}/resume")
def resume_pipeline_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id, PipelineRun.user_id == current_user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status == "running":
        raise HTTPException(status_code=409, detail="Run is already running")

    stage_states = dict(run.stage_states or _default_stage_states())
    resume_stage = _find_resume_stage(stage_states)
    if not resume_stage:
        return {"run": _serialize_run(run), "message": "No resumable stage found."}

    run.status = "pending"
    run.current_stage = resume_stage
    run.error_message = ""
    run.finished_at = None
    db.add(run)
    db.commit()
    db.refresh(run)

    _start_worker(run.id, resume_stage)
    return {"run": _serialize_run(run), "message": f"Resumed from stage {resume_stage}."}


@router.post("/runs/{run_id}/retry")
def retry_pipeline_run(
    run_id: int,
    req: PipelineRetryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    base_run = db.query(PipelineRun).filter(PipelineRun.id == run_id, PipelineRun.user_id == current_user.id).first()
    if not base_run:
        raise HTTPException(status_code=404, detail="Run not found")

    start_stage: StageKey = req.from_stage or "test_generation"
    start_index = STAGE_ORDER.index(start_stage)

    new_stage_states = _default_stage_states()
    new_artifacts: dict[str, Any] = {}
    if start_index > 0:
        old_states = dict(base_run.stage_states or {})
        old_artifacts = dict(base_run.artifacts or {})
        for stage in STAGE_ORDER[:start_index]:
            prev = dict(old_states.get(stage) or {})
            prev["status"] = "success"
            prev["message"] = f"Reused from run #{base_run.id}"
            prev["started_at"] = prev.get("started_at") or _now_iso()
            prev["ended_at"] = prev.get("ended_at") or _now_iso()
            new_stage_states[stage] = prev
            if stage in old_artifacts:
                new_artifacts[stage] = old_artifacts[stage]

    new_run = PipelineRun(
        user_id=current_user.id,
        project_id=base_run.project_id,
        status="pending",
        current_stage=start_stage,
        request_payload=base_run.request_payload or {},
        stage_states=new_stage_states,
        artifacts=new_artifacts,
        retry_of_run_id=base_run.id,
    )
    db.add(new_run)
    db.commit()
    db.refresh(new_run)

    _start_worker(new_run.id, start_stage)
    return {"run": _serialize_run(new_run), "message": f"Retry started from stage {start_stage}."}


@router.get("/runs/{run_id}/traces")
def get_pipeline_run_traces(
    run_id: int,
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id, PipelineRun.user_id == current_user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    rows = (
        db.query(LogEntry)
        .filter(
            LogEntry.project_id == run.project_id,
            or_(LogEntry.user_id == current_user.id, LogEntry.user_id.is_(None)),
            LogEntry.message.like("WORKFLOW_TRACE:%"),
        )
        .order_by(desc(LogEntry.created_at), desc(LogEntry.id))
        .limit(limit)
        .all()
    )

    items: list[dict[str, Any]] = []
    for row in rows:
        payload = _parse_workflow_trace(row.message or "")
        if not payload:
            continue

        details = payload.get("details") or {}
        if int(details.get("run_id") or 0) != run_id:
            continue

        items.append(
            {
                "id": row.id,
                "created_at": row.created_at,
                "kind": str(payload.get("kind") or ""),
                "stage": str(payload.get("stage") or ""),
                "action": str(details.get("action") or ""),
                "details": details,
            }
        )

    items.sort(key=lambda item: (item.get("created_at"), item.get("id")))
    return {"items": items}
