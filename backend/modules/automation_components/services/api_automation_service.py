"""Business service for API automation routes."""

from __future__ import annotations

from typing import Any

from core.processing.workflow import WorkflowKind, WorkflowStage, log_workflow_trace
from modules.automation_components.repositories.api_automation_repository import APIAutomationRepository
from modules.orchestration.context_orchestrator import context_orchestrator
from modules.testing.api_testing import api_tester


class APIAutomationService:
    """Use-case layer for API automation route operations."""

    def __init__(self, db):
        self.repo = APIAutomationRepository(db)
        self._db = db

    def has_owned_project(self, *, project_id: int, user_id: int) -> bool:
        return bool(self.repo.get_owned_project(project_id=project_id, user_id=user_id))

    def list_standard_interfaces(self, *, project_id: int, user_id: int, limit: int = 12) -> list[dict[str, Any]]:
        rows = self.repo.list_standard_interfaces(project_id=project_id, user_id=user_id, limit=limit)
        interfaces: list[dict[str, Any]] = []
        for row in rows:
            interfaces.append(
                {
                    "name": row.name,
                    "method": row.method or "GET",
                    "url": f"{row.base_url or ''}{row.api_path or ''}",
                    "params": row.params or [],
                    "headers": row.headers or [],
                    "body": row.body_content or "",
                }
            )
        return interfaces

    def generate_script(self, *, payload: dict[str, Any], user_id: int) -> tuple[str, dict[str, Any] | None]:
        project_id = int(payload.get("project_id") or 0)
        if not self.has_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None

        requirement = str(payload.get("requirement") or "")
        context_bundle = context_orchestrator.assemble_context(
            WorkflowKind.API_AUTOMATION,
            project_id,
            self._db,
            user_id=user_id,
            query_text=requirement[:600],
            requirement_text=requirement[:2000],
            include_knowledge=True,
            include_interfaces=True,
            include_logs=True,
            knowledge_limit=4,
            interface_limit=10,
            log_limit=10,
        )
        log_workflow_trace(
            self._db,
            project_id,
            user_id,
            WorkflowKind.API_AUTOMATION,
            WorkflowStage.CONTEXT,
            {"action": "generate_script", **context_bundle["diagnostics"]},
        )

        script = api_tester.generate_api_test_script(
            requirement=requirement,
            base_url=str(payload.get("base_url") or ""),
            api_path=str(payload.get("api_path") or ""),
            test_types=payload.get("test_types"),
            api_docs=context_bundle["combined_context"],
            db=self._db,
            mode=str(payload.get("mode") or "natural"),
            user_id=user_id,
        )
        log_workflow_trace(
            self._db,
            project_id,
            user_id,
            WorkflowKind.API_AUTOMATION,
            WorkflowStage.GENERATE,
            {"action": "generate_script", "script_length": len(script or "")},
        )
        return "ok", {"script": script, "context_diagnostics": context_bundle["diagnostics"]}

    def execute_script(self, *, payload: dict[str, Any], user_id: int) -> tuple[str, dict[str, Any] | None]:
        project_id = int(payload.get("project_id") or 0)
        if not self.has_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None

        script_content = str(payload.get("script_content") or "")
        requirement = str(payload.get("requirement") or "")
        base_url = str(payload.get("base_url") or "")
        log_workflow_trace(
            self._db,
            project_id,
            user_id,
            WorkflowKind.API_AUTOMATION,
            WorkflowStage.EXECUTE,
            {"action": "execute_script", "script_length": len(script_content)},
        )
        result = api_tester.execute_api_tests(
            script_content=script_content,
            requirement=requirement,
            base_url=base_url,
            db=self._db,
            project_id=project_id,
            user_id=user_id,
        )
        return "ok", result

    def generate_chain(self, *, payload: dict[str, Any], user_id: int) -> tuple[str, dict[str, Any] | None]:
        project_id = int(payload.get("project_id") or 0)
        if not self.has_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None

        interfaces = payload.get("interfaces") or self.list_standard_interfaces(project_id=project_id, user_id=user_id)
        if not interfaces:
            return "interfaces_missing", None

        log_workflow_trace(
            self._db,
            project_id,
            user_id,
            WorkflowKind.API_AUTOMATION,
            WorkflowStage.PLAN,
            {"action": "generate_chain", "interfaces": len(interfaces)},
        )
        script = api_tester.generate_chain_script(
            interfaces=interfaces,
            scenario_desc=str(payload.get("scenario_desc") or ""),
            db=self._db,
            user_id=user_id,
        )
        log_workflow_trace(
            self._db,
            project_id,
            user_id,
            WorkflowKind.API_AUTOMATION,
            WorkflowStage.GENERATE,
            {"action": "generate_chain", "script_length": len(script or "")},
        )
        return "ok", {"script": script, "interfaces_count": len(interfaces)}

    def generate_mock_data(self, *, payload: dict[str, Any], user_id: int) -> tuple[str, dict[str, Any] | None]:
        project_id = int(payload.get("project_id") or 0)
        if not self.has_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None

        data = api_tester.generate_mock_data(
            interface_info=dict(payload.get("interface_info") or {}),
            mock_type=str(payload.get("mock_type") or "single"),
            count=int(payload.get("count") or 5),
            db=self._db,
            user_id=user_id,
        )
        log_workflow_trace(
            self._db,
            project_id,
            user_id,
            WorkflowKind.API_AUTOMATION,
            WorkflowStage.GENERATE,
            {"action": "generate_mock_data", "count": len(data or [])},
        )
        return "ok", {"mock_data": data}

    def get_history(self, *, project_id: int, user_id: int) -> tuple[str, dict[str, Any] | None]:
        if not self.has_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None

        rows = self.repo.list_api_history(project_id=project_id, user_id=user_id, limit=50)
        items: list[dict[str, Any]] = []
        for row in rows:
            report = row.structured_report or {}
            failed = int(report.get("failed", 0)) if isinstance(report, dict) else 0
            total = int(report.get("total", 0)) if isinstance(report, dict) else 0
            status = "failed" if failed > 0 else ("success" if total > 0 else "unknown")
            items.append(
                {
                    "id": row.id,
                    "requirement": (row.requirement or "")[:120],
                    "status": status,
                    "total": total,
                    "failed": failed,
                    "created_at": row.created_at,
                }
            )
        return "ok", {"items": items}

