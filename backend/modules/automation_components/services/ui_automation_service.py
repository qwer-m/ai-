"""Business service for UI automation routes."""

from __future__ import annotations

from typing import Any

from core.processing.utils import log_to_db
from core.processing.workflow import WorkflowKind, WorkflowStage, log_workflow_trace
from modules.automation_components.repositories.ui_automation_repository import UIAutomationRepository
from modules.orchestration.context_orchestrator import context_orchestrator
from modules.testing.ui_automation import ui_automator


class UIAutomationService:
    """Use-case layer for UI automation route operations."""

    def __init__(self, db):
        self.repo = UIAutomationRepository(db)
        self._db = db

    def has_owned_project(self, *, project_id: int, user_id: int) -> bool:
        return bool(self.repo.get_owned_project(project_id=project_id, user_id=user_id))

    def _build_requirement_context(self, *, payload: dict[str, Any], user_id: int) -> str | None:
        requirement_context = payload.get("requirement_context")
        if requirement_context:
            return str(requirement_context)

        project_id = int(payload.get("project_id") or 0)
        task = str(payload.get("task") or "")
        context_bundle = context_orchestrator.assemble_context(
            WorkflowKind.UI_AUTOMATION,
            project_id,
            self._db,
            user_id=user_id,
            query_text=task[:500],
            requirement_text=task[:1000],
            include_knowledge=True,
            include_logs=True,
            knowledge_limit=4,
            log_limit=10,
        )
        log_workflow_trace(
            self._db,
            project_id,
            user_id,
            WorkflowKind.UI_AUTOMATION,
            WorkflowStage.CONTEXT,
            {"action": "assemble_ui_context", "auto_context": True, **context_bundle["diagnostics"]},
        )
        return context_bundle["combined_context"] or None

    def list_history(self, *, project_id: int, user_id: int) -> tuple[str, list[dict[str, Any]]]:
        if not self.has_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", []
        rows = self.repo.list_history(project_id=project_id, user_id=user_id, limit=50)
        return (
            "ok",
            [
                {
                    "id": row.id,
                    "task_description": f"{(row.task_description or '')[:50]}..." if row.task_description else "无描述",
                    "status": row.status,
                    "created_at": row.created_at,
                    "automation_type": row.automation_type,
                    "quality_score": row.quality_score,
                }
                for row in rows
            ],
        )

    def get_execution_detail(self, *, execution_id: int, user_id: int) -> tuple[str, dict[str, Any] | None]:
        row = self.repo.get_execution(execution_id=execution_id, user_id=user_id)
        if not row:
            return "not_found", None
        return (
            "ok",
            {
                "id": row.id,
                "task_description": row.task_description,
                "generated_script": row.generated_script,
                "execution_result": row.execution_result,
                "status": row.status,
                "screenshot_paths": row.screenshot_paths,
                "quality_score": row.quality_score,
                "evaluation_result": row.evaluation_result,
                "created_at": row.created_at,
                "automation_type": row.automation_type,
                "url": row.url,
                "app_info": row.app_info,
            },
        )

    def generate_script(self, *, payload: dict[str, Any], user_id: int, token: str) -> tuple[str, dict[str, Any] | None]:
        project_id = int(payload.get("project_id") or 0)
        if not self.has_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None
        requirement_context = self._build_requirement_context(payload=payload, user_id=user_id)
        script = ui_automator.generate_ai_image_recognition_script(
            str(payload.get("task") or ""),
            str(payload.get("url") or ""),
            str(payload.get("automation_type") or "web"),
            db=self._db,
            user_id=user_id,
            token=token,
            image_model=payload.get("image_model"),
            requirement_context=requirement_context,
        )
        return "ok", {"script": script}

    def execute_script_direct(self, *, payload: dict[str, Any], user_id: int) -> tuple[str, dict[str, Any] | None]:
        project_id = int(payload.get("project_id") or 0)
        if not self.has_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None
        result = ui_automator.execute_script(
            str(payload.get("script") or ""),
            str(payload.get("url") or ""),
            str(payload.get("task") or ""),
            str(payload.get("automation_type") or "web"),
            self._db,
            project_id,
            user_id=user_id,
            test_case_id=payload.get("test_case_id"),
        )
        return "ok", result

    def run_ui_automation(self, *, payload: dict[str, Any], user_id: int, token: str) -> tuple[str, dict[str, Any] | None]:
        project_id = int(payload.get("project_id") or 0)
        if not self.has_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None

        requirement_context = self._build_requirement_context(payload=payload, user_id=user_id)
        task = str(payload.get("task") or "")
        url = str(payload.get("url") or "")
        automation_type = str(payload.get("automation_type") or "web")

        log_to_db(self._db, project_id, "system", f"开始执行UI自动化: {task}", user_id=user_id)
        script = ui_automator.generate_ai_image_recognition_script(
            task,
            url,
            automation_type,
            db=self._db,
            user_id=user_id,
            token=token,
            image_model=payload.get("image_model"),
            requirement_context=requirement_context,
        )
        result = ui_automator.execute_script(script, url, task, automation_type, self._db, project_id, user_id=user_id)
        log_to_db(
            self._db,
            project_id,
            "system",
            f"UI自动化执行完成，结果: {result.get('status', 'unknown')}",
            user_id=user_id,
        )
        return "ok", {"script": script, "result": result}

