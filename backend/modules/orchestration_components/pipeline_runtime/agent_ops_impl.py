from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.orm import Session

from core.ai.ai_client import get_client_for_user
from core.db.models import PipelineRun
from modules.domain.knowledge_base import knowledge_base
from modules.orchestration.background_task_governance import run_governed_threadpool_map
from .agent_decision import _aggregate_reviewer_decision
from .schemas import STAGE_ORDER, StageKey
from .support import _now_iso, _truncate_text

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
        results = run_governed_threadpool_map(
            profile_key="pipeline_agent_executor_threadpool",
            items=tasks,
            worker=lambda task: _evaluate_executor_task(stage, task, payload, artifacts),
            max_workers=max(1, workers),
            thread_name_prefix=f"executor-{stage}",
            business_id=stage,
        )
        for item in results:
            task = dict(item.item or {})
            task_id = str(task.get("id") or "")
            if item.exception is not None:
                result = {
                    "id": task_id,
                    "title": task.get("title") or task_id,
                    "status": "warning",
                    "note": f"executor task failed: {type(item.exception).__name__}: {item.exception}",
                }
            else:
                result = item.result
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
            lines.extend(["", "### reviewer_llm_review", _truncate_text(reviewer.get("llm_review"), 1800)])
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
        llm_text = _run_agent_llm(db, user_id, system_prompt=system_prompt, user_prompt=user_prompt)
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
        llm_text = _run_agent_llm(db, user_id, system_prompt=system_prompt, user_prompt=user_prompt)
        reviewer["llm_status"] = "ok"
        reviewer["llm_review"] = _truncate_text(llm_text, 2400)
    except Exception as e:
        reviewer["llm_status"] = "error"
        reviewer["llm_error"] = f"{type(e).__name__}: {e}"
    return reviewer

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
