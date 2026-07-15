"""Business service for UI automation routes."""

from __future__ import annotations

from typing import Any

from core.processing.utils import log_to_db
from core.processing.workflow import WorkflowKind, WorkflowStage, log_workflow_trace
from modules.automation_components.repositories.ui_automation_repository import UIAutomationRepository
from modules.automation_components.services.ui_automation_export_service import export_standalone_ui_script
from modules.automation_components.services.ui_visual_asset_service import list_visual_asset_catalogs
from modules.orchestration.context_orchestrator import context_orchestrator
from modules.testing.ui_automation import ui_automator


class UIAutomationService:
    """Use-case layer for UI automation route operations."""

    def __init__(self, db):
        self.repo = UIAutomationRepository(db)
        self._db = db

    def has_owned_project(self, *, project_id: int, user_id: int) -> bool:
        return bool(self.repo.get_owned_project(project_id=project_id, user_id=user_id))

    @staticmethod
    def _runtime_env(payload: dict[str, Any]) -> dict[str, str]:
        mapping = {
            "device_id": "APPIUM_UDID",
            "appium_server_url": "APPIUM_SERVER_URL",
        }
        result = {
            env_name: str(payload.get(field)).strip()
            for field, env_name in mapping.items()
            if payload.get(field) is not None and str(payload.get(field)).strip()
        }
        if payload.get("reset_app_data") is not None:
            result["RESET_APP_DATA"] = "true" if bool(payload["reset_app_data"]) else "false"
        return result

    @staticmethod
    def _visual_asset_group(payload: dict[str, Any], automation_type: str) -> str | None:
        configured = str(payload.get("visual_asset_group") or "").strip()
        if configured:
            return configured
        if automation_type != "app":
            return None
        catalogs = list_visual_asset_catalogs()
        return str(catalogs[0]["group"]) if len(catalogs) == 1 else None

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
        automation_type = str(payload.get("automation_type") or "web")
        task = str(payload.get("task") or "")
        target = str(payload.get("url") or "")
        visual_asset_group = self._visual_asset_group(payload, automation_type)
        script = ui_automator.generate_executable_script(
            task,
            target,
            automation_type,
            db=self._db,
            user_id=user_id,
            requirement_context=requirement_context,
            device_id=payload.get("device_id"),
            visual_asset_group=visual_asset_group,
        )
        export = export_standalone_ui_script(
            script=script,
            task=task,
            target=target,
            automation_type=automation_type,
            project_id=project_id,
            visual_asset_group=visual_asset_group,
        )
        return "ok", {"script": script, "export": export}

    def execute_script_direct(
        self,
        *,
        payload: dict[str, Any],
        user_id: int,
        token: str | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        project_id = int(payload.get("project_id") or 0)
        if not self.has_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None
        task = str(payload.get("task") or "")
        target = str(payload.get("url") or "")
        automation_type = str(payload.get("automation_type") or "web")
        script = str(payload.get("script") or "")
        visual_asset_group = self._visual_asset_group(payload, automation_type)
        export = export_standalone_ui_script(
            script=script,
            task=task,
            target=target,
            automation_type=automation_type,
            project_id=project_id,
            visual_asset_group=visual_asset_group,
        )
        result = ui_automator.execute_script(
            script,
            target,
            task,
            automation_type,
            self._db,
            project_id,
            user_id=user_id,
            test_case_id=payload.get("test_case_id"),
            auth_token=token,
            script_path=export["script_path"],
            working_directory=export["root_dir"],
            execution_env=self._runtime_env(payload),
        )
        result["export"] = export
        return "ok", result

    def run_ui_automation(self, *, payload: dict[str, Any], user_id: int, token: str) -> tuple[str, dict[str, Any] | None]:
        project_id = int(payload.get("project_id") or 0)
        if not self.has_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None

        requirement_context = self._build_requirement_context(payload=payload, user_id=user_id)
        task = str(payload.get("task") or "")
        url = str(payload.get("url") or "")
        automation_type = str(payload.get("automation_type") or "web")
        visual_asset_group = self._visual_asset_group(payload, automation_type)

        log_to_db(self._db, project_id, "system", f"开始执行UI自动化: {task}", user_id=user_id)
        script = ui_automator.generate_executable_script(
            task,
            url,
            automation_type,
            db=self._db,
            user_id=user_id,
            requirement_context=requirement_context,
            device_id=payload.get("device_id"),
            visual_asset_group=visual_asset_group,
        )
        export = export_standalone_ui_script(
            script=script,
            task=task,
            target=url,
            automation_type=automation_type,
            project_id=project_id,
            visual_asset_group=visual_asset_group,
        )
        result = ui_automator.execute_script(
            script,
            url,
            task,
            automation_type,
            self._db,
            project_id,
            user_id=user_id,
            auth_token=token,
            script_path=export["script_path"],
            working_directory=export["root_dir"],
            execution_env=self._runtime_env(payload),
        )
        log_to_db(
            self._db,
            project_id,
            "system",
            f"UI自动化执行完成，结果: {result.get('status', 'unknown')}",
            user_id=user_id,
        )
        return "ok", {"script": script, "result": result, "export": export}

