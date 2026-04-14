"""Business service for pipeline run routes."""

from __future__ import annotations

from typing import Any, Callable

from core.db.models import PipelineRun
from modules.orchestration_components.pipeline_runtime.schemas import STAGE_ORDER, StageKey
from modules.orchestration_components.pipeline_runtime.support import (
    _default_stage_states,
    _find_resume_stage,
    _now_iso,
    _parse_workflow_trace,
)
from modules.orchestration_components.repositories.pipeline_repository import PipelineRepository


class PipelineRunService:
    """Use-case layer for create/list/get/resume/retry pipeline runs."""

    def __init__(self, db, worker_starter: Callable[[int, StageKey], None]):
        self.repo = PipelineRepository(db)
        self._start_worker = worker_starter

    def create_run(self, *, payload: dict[str, Any], project_id: int, user_id: int) -> PipelineRun | None:
        if not self.repo.get_owned_project(project_id=project_id, user_id=user_id):
            return None
        run = PipelineRun(
            user_id=user_id,
            project_id=project_id,
            status="pending",
            current_stage="test_generation",
            request_payload=payload,
            stage_states=_default_stage_states(),
            artifacts={},
        )
        self.repo.add(run)
        self.repo.commit()
        self.repo.refresh(run)
        self._start_worker(run.id, "test_generation")
        return run

    def list_runs(self, *, project_id: int, user_id: int, limit: int) -> list[PipelineRun] | None:
        if not self.repo.get_owned_project(project_id=project_id, user_id=user_id):
            return None
        return self.repo.list_runs(project_id=project_id, user_id=user_id, limit=limit)

    def get_run(self, *, run_id: int, user_id: int) -> PipelineRun | None:
        return self.repo.get_owned_run(run_id=run_id, user_id=user_id)

    def resume_run(self, *, run_id: int, user_id: int) -> tuple[PipelineRun | None, str]:
        run = self.repo.get_owned_run(run_id=run_id, user_id=user_id)
        if not run:
            return None, "not_found"
        if run.status == "running":
            return run, "already_running"

        stage_states = dict(run.stage_states or _default_stage_states())
        resume_stage = _find_resume_stage(stage_states)
        if not resume_stage:
            return run, "no_resumable_stage"

        run.status = "pending"
        run.current_stage = resume_stage
        run.error_message = ""
        run.finished_at = None
        self.repo.add(run)
        self.repo.commit()
        self.repo.refresh(run)
        self._start_worker(run.id, resume_stage)
        return run, f"resumed:{resume_stage}"

    def retry_run(self, *, run_id: int, user_id: int, start_stage: StageKey) -> tuple[PipelineRun | None, str]:
        base_run = self.repo.get_owned_run(run_id=run_id, user_id=user_id)
        if not base_run:
            return None, "not_found"

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
            user_id=user_id,
            project_id=base_run.project_id,
            status="pending",
            current_stage=start_stage,
            request_payload=base_run.request_payload or {},
            stage_states=new_stage_states,
            artifacts=new_artifacts,
            retry_of_run_id=base_run.id,
        )
        self.repo.add(new_run)
        self.repo.commit()
        self.repo.refresh(new_run)
        self._start_worker(new_run.id, start_stage)
        return new_run, f"retry_started:{start_stage}"

    def list_run_traces(self, *, run_id: int, user_id: int, limit: int) -> list[dict[str, Any]] | None:
        run = self.repo.get_owned_run(run_id=run_id, user_id=user_id)
        if not run:
            return None

        rows = self.repo.list_workflow_trace_rows(
            project_id=run.project_id,
            user_id=user_id,
            limit=limit,
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
        return items
