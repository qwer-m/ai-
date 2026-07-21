"""Business service for UI automation routes."""

from __future__ import annotations

import json
from typing import Any

from core.processing.utils import log_to_db
from core.db.models import UITestCase
from core.processing.workflow import WorkflowKind, WorkflowStage, log_workflow_trace
from modules.automation_components.repositories.ui_automation_repository import UIAutomationRepository
from modules.automation_components.services.ui_automation_export_service import UIAutomationExportService
from modules.testing.ui_automation import ui_automator


class UIAutomationService:
    """Use-case layer for UI automation route operations."""

    def __init__(self, db):
        self.repo = UIAutomationRepository(db)
        self._db = db
        self.exporter = UIAutomationExportService()

    def has_owned_project(self, *, project_id: int, user_id: int) -> bool:
        return bool(self.repo.get_owned_project(project_id=project_id, user_id=user_id))

    @staticmethod
    def _operation(payload: dict[str, Any]) -> dict[str, Any]:
        task = str(payload.get("task") or "").strip()
        name = str(payload.get("operation_name") or "").strip()
        if not name:
            name = next((line.strip() for line in task.splitlines() if line.strip()), "UI 自动化操作")[:100]
        raw_steps = payload.get("operation_steps") or []
        if isinstance(raw_steps, str):
            try:
                raw_steps = json.loads(raw_steps)
            except ValueError:
                raw_steps = raw_steps.splitlines()
        steps = UIAutomationExportService.normalize_steps(task, raw_steps)
        return {"name": name[:100], "description": task, "steps": steps}

    def _persist_operation(
        self,
        *,
        project_id: int,
        operation: dict[str, Any],
        script: str,
        automation_type: str,
        target: str,
        parent_id: int | None = None,
    ) -> UITestCase:
        row = self.repo.get_operation_case(
            project_id=project_id,
            operation_name=operation["name"],
            automation_type=automation_type,
        )
        if row is None:
            row = UITestCase(
                project_id=project_id,
                name=operation["name"],
                type="file",
                automation_type=automation_type,
                parent_id=parent_id,
            )
        elif parent_id is not None:
            row.parent_id = parent_id
        row.description = operation["description"][:255] or None
        row.requirements = json.dumps(operation, ensure_ascii=False)
        row.script_content = script
        row.target_config = target[:255] or None
        self.repo.save(row)
        return row

    def _generate_ai_script(self, *, payload: dict[str, Any], user_id: int, token: str) -> str:
        requirement_context = self._build_requirement_context(payload=payload, user_id=user_id)
        return ui_automator.generate_ai_image_recognition_script(
            str(payload.get("task") or ""),
            str(payload.get("url") or ""),
            str(payload.get("automation_type") or "web"),
            db=self._db,
            user_id=user_id,
            token=token,
            image_model=payload.get("image_model"),
            requirement_context=requirement_context,
        )

    def _build_requirement_context(self, *, payload: dict[str, Any], user_id: int) -> str | None:
        requirement_context = payload.get("requirement_context")
        if requirement_context:
            return str(requirement_context)

        project_id = int(payload.get("project_id") or 0)
        operation = self._operation(payload)
        steps = operation.get("steps") or []
        context = ""
        if steps:
            context = "[当前 UI 操作步骤]\n" + "\n".join(
                f"{index}. {step}" for index, step in enumerate(steps, start=1)
            )
        log_workflow_trace(
            self._db,
            project_id,
            user_id,
            WorkflowKind.UI_AUTOMATION,
            WorkflowStage.CONTEXT,
            {
                "action": "assemble_ui_context",
                "auto_context": False,
                "source": "operation_steps",
                "step_count": len(steps),
                "combined_length": len(context),
            },
        )
        return context or None

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
        project = self.repo.get_owned_project(project_id=project_id, user_id=user_id)
        if not project:
            return "project_not_found", None
        script = self._generate_ai_script(payload=payload, user_id=user_id, token=token)
        return self.save_script(payload={**payload, "script": script}, user_id=user_id)

    def save_script(self, *, payload: dict[str, Any], user_id: int) -> tuple[str, dict[str, Any] | None]:
        """将已验证成功或模型生成的隐藏脚本固化到桌面项目和用例库。"""
        project_id = int(payload.get("project_id") or 0)
        project = self.repo.get_owned_project(project_id=project_id, user_id=user_id)
        if not project:
            return "project_not_found", None
        script = str(payload.get("script") or "").strip()
        if not script:
            raise ValueError("待转化的自动化脚本为空")
        operation = self._operation(payload)
        automation_type = str(payload.get("automation_type") or "web")
        target = str(payload.get("url") or "")
        parent_id = payload.get("parent_id")
        if parent_id is not None:
            parent = self.repo.get_project_case(project_id=project_id, item_id=int(parent_id))
            if parent is None or parent.type != "folder":
                raise ValueError("转化目标目录不存在或不是文件夹")
        export = self.exporter.export_operation(
            project_id=project_id,
            project_name=project.name,
            operation_name=operation["name"],
            description=operation["description"],
            steps=operation["steps"],
            script=script,
            automation_type=automation_type,
            target=target,
        )
        test_case = self._persist_operation(
            project_id=project_id,
            operation=operation,
            script=script,
            automation_type=automation_type,
            target=target,
            parent_id=int(parent_id) if parent_id is not None else None,
        )
        self.exporter.sync_project_hierarchy(
            project_id=project_id,
            project_name=project.name,
            cases=self.repo.list_project_cases(project_id=project_id),
        )
        resolved = self.exporter.resolve_operation(
            project_id=project_id,
            project_name=project.name,
            operation_name=operation["name"],
        )
        return "ok", {
            "script": script,
            "operation": operation,
            "export": resolved or export,
            "test_case_id": test_case.id,
        }

    def run_natural_language(
        self,
        *,
        payload: dict[str, Any],
        user_id: int,
        token: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """自然语言先执行验证；成功前不写入桌面自动化项目。"""
        project_id = int(payload.get("project_id") or 0)
        if not self.has_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None
        operation = self._operation(payload)
        script = self._generate_ai_script(payload=payload, user_id=user_id, token=token)
        result = ui_automator.execute_script(
            script,
            str(payload.get("url") or ""),
            operation["description"],
            str(payload.get("automation_type") or "web"),
            self._db,
            project_id,
            user_id=user_id,
            test_case_id=None,
            auth_token=token,
            script_path=None,
            image_model=payload.get("image_model"),
            require_semantic_verification=True,
        )
        return "ok", {"script": script, "operation": operation, "result": result}

    def execute_script_direct(
        self,
        *,
        payload: dict[str, Any],
        user_id: int,
        token: str | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        project_id = int(payload.get("project_id") or 0)
        project = self.repo.get_owned_project(project_id=project_id, user_id=user_id)
        if not project:
            return "project_not_found", None
        operation = self._operation(payload)
        script = str(payload.get("script") or "")
        automation_type = str(payload.get("automation_type") or "web")
        target = str(payload.get("url") or "")
        export = self.exporter.prepare_operation_for_execution(
            project_id=project_id,
            project_name=project.name,
            operation_name=operation["name"],
            description=operation["description"],
            steps=operation["steps"],
            script=script,
            automation_type=automation_type,
            target=target,
        )
        self._persist_operation(
            project_id=project_id,
            operation=operation,
            script=script,
            automation_type=automation_type,
            target=target,
        )
        result = ui_automator.execute_script(
            script,
            target,
            operation["description"],
            automation_type,
            self._db,
            project_id,
            user_id=user_id,
            test_case_id=None,
            auth_token=token,
            script_path=export["script_path"],
        )
        result["operation"] = operation
        result["export"] = export
        return "ok", result

    def run_ui_automation(self, *, payload: dict[str, Any], user_id: int, token: str) -> tuple[str, dict[str, Any] | None]:
        project_id = int(payload.get("project_id") or 0)
        if not self.has_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None

        operation = self._operation(payload)
        log_to_db(self._db, project_id, "system", f"开始执行UI自动化: {operation['name']}", user_id=user_id)
        status, generated = self.generate_script(payload=payload, user_id=user_id, token=token)
        if status != "ok" or not generated:
            return status, generated
        execute_payload = {
            **payload,
            "script": generated["script"],
            "operation_name": operation["name"],
            "operation_steps": operation["steps"],
        }
        status, result = self.execute_script_direct(payload=execute_payload, user_id=user_id, token=token)
        if status != "ok" or not result:
            return status, result
        log_to_db(
            self._db,
            project_id,
            "system",
            f"UI自动化执行完成，结果: {result.get('status', 'unknown')}",
            user_id=user_id,
        )
        return "ok", {"script": generated["script"], "operation": operation, "export": generated["export"], "result": result}

