from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from core.db.models import PipelineRun
from .schemas import RunStatus, STAGE_ORDER, StageKey

_UNSET = object()


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _default_stage_states() -> dict[str, dict[str, Any]]:
    return {
        stage: {"status": "idle", "message": "", "started_at": None, "ended_at": None}
        for stage in STAGE_ORDER
    }


def _serialize_run(run: PipelineRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "user_id": run.user_id,
        "status": run.status,
        "current_stage": run.current_stage,
        "task_id": getattr(run, "task_id", None),
        "heartbeat_at": getattr(run, "heartbeat_at", None),
        "lease_expires_at": getattr(run, "lease_expires_at", None),
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
    status: RunStatus | object = _UNSET,
    current_stage: str | None | object = _UNSET,
    stage_states: dict[str, Any] | object = _UNSET,
    artifacts: dict[str, Any] | object = _UNSET,
    error_message: str | object = _UNSET,
    started_at: datetime | None | object = _UNSET,
    finished_at: datetime | None | object = _UNSET,
    task_id: str | None | object = _UNSET,
    claim_token: str | None | object = _UNSET,
    heartbeat_at: datetime | None | object = _UNSET,
    lease_expires_at: datetime | None | object = _UNSET,
) -> None:
    if status is not _UNSET:
        run.status = status
    if current_stage is not _UNSET:
        run.current_stage = current_stage
    if stage_states is not _UNSET:
        run.stage_states = stage_states
    if artifacts is not _UNSET:
        run.artifacts = artifacts
    if error_message is not _UNSET:
        run.error_message = error_message
    if started_at is not _UNSET:
        run.started_at = started_at
    if finished_at is not _UNSET:
        run.finished_at = finished_at
    if task_id is not _UNSET:
        run.task_id = task_id
    if claim_token is not _UNSET:
        run.claim_token = claim_token
    if heartbeat_at is not _UNSET:
        run.heartbeat_at = heartbeat_at
    if lease_expires_at is not _UNSET:
        run.lease_expires_at = lease_expires_at
    db.add(run)
    db.commit()
    db.refresh(run)


def _mark_stage(
    stage_states: dict[str, Any],
    stage: StageKey,
    status: str,
    message: str,
) -> None:
    # 阶段状态字段会被前端轮询实时消费，统一在此更新可避免不同分支写出不一致结构。
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
    payload = message[len(prefix):].strip()
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


def _find_resume_stage(stage_states: dict[str, Any]) -> Optional[StageKey]:
    for stage in STAGE_ORDER:
        status = str((stage_states.get(stage) or {}).get("status") or "idle")
        if status in {"idle", "failed", "pending"}:
            return stage
    return None

