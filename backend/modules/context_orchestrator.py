from typing import Optional

from sqlalchemy.orm import Session

from core.models import LogEntry, StandardInterface
from core.workflow import WorkflowKind
from modules.knowledge_base import knowledge_base


class ContextOrchestrator:
    def _collect_interface_context(
        self,
        project_id: int,
        user_id: Optional[int],
        db: Session,
        limit: int,
    ) -> tuple[str, int]:
        query = (
            db.query(StandardInterface)
            .filter(StandardInterface.project_id == project_id)
            .order_by(StandardInterface.updated_at.desc(), StandardInterface.id.desc())
        )
        if user_id is not None:
            query = query.filter(StandardInterface.user_id == user_id)

        items = query.limit(limit).all()
        if not items:
            return "", 0

        blocks: list[str] = []
        for item in items:
            if item.type == "folder":
                continue
            line = f"{item.method or 'GET'} {item.base_url or ''}{item.api_path or ''}".strip()
            desc = item.description or ""
            blocks.append(f"--- Interface: {item.name} ---\n{line}\n{desc}".strip())
        text = "\n\n".join(blocks).strip()
        return text, len(items)

    def _collect_log_context(
        self,
        project_id: int,
        user_id: Optional[int],
        db: Session,
        limit: int,
    ) -> tuple[str, int]:
        query = (
            db.query(LogEntry)
            .filter(LogEntry.project_id == project_id)
            .order_by(LogEntry.created_at.desc(), LogEntry.id.desc())
        )
        if user_id is not None:
            query = query.filter((LogEntry.user_id == user_id) | (LogEntry.user_id.is_(None)))

        items = list(reversed(query.limit(limit).all()))
        if not items:
            return "", 0

        lines = [f"[{item.log_type}] {item.message}" for item in items]
        return "\n".join(lines), len(items)

    def assemble_context(
        self,
        workflow_kind: str,
        project_id: int,
        db: Session,
        user_id: Optional[int] = None,
        query_text: str = "",
        requirement_text: str = "",
        include_knowledge: bool = True,
        include_interfaces: bool = False,
        include_logs: bool = False,
        knowledge_limit: int = 5,
        interface_limit: int = 8,
        log_limit: int = 12,
    ) -> dict:
        query_text = (query_text or "").strip()
        requirement_text = (requirement_text or "").strip()

        knowledge_context = ""
        knowledge_count = 0
        if include_knowledge:
            if query_text:
                knowledge_context = knowledge_base.get_relevant_context(
                    query=query_text,
                    project_id=project_id,
                    limit=knowledge_limit,
                    db=db,
                    user_id=user_id,
                )
                knowledge_count = knowledge_context.count("--- Relevant Knowledge:")
            else:
                knowledge_context = knowledge_base.get_all_context(
                    db,
                    project_id,
                    user_id=user_id,
                    max_docs=knowledge_limit,
                )
                knowledge_count = knowledge_context.count("--- Document:")

        interface_context = ""
        interface_count = 0
        if include_interfaces:
            interface_context, interface_count = self._collect_interface_context(
                project_id,
                user_id,
                db,
                interface_limit,
            )

        log_context = ""
        log_count = 0
        if include_logs:
            log_context, log_count = self._collect_log_context(
                project_id,
                user_id,
                db,
                log_limit,
            )

        combined_parts = []
        if knowledge_context:
            combined_parts.append(f"[Knowledge Context]\n{knowledge_context}")
        if interface_context:
            combined_parts.append(f"[Interface Context]\n{interface_context}")
        if log_context:
            combined_parts.append(f"[Execution History]\n{log_context}")
        if requirement_text and workflow_kind == WorkflowKind.EVALUATION:
            combined_parts.append(f"[Subject]\n{requirement_text[:2000]}")

        combined_context = "\n\n".join(part for part in combined_parts if part).strip()
        return {
            "workflow_kind": workflow_kind,
            "project_id": project_id,
            "combined_context": combined_context,
            "knowledge_context": knowledge_context,
            "interface_context": interface_context,
            "log_context": log_context,
            "diagnostics": {
                "knowledge_docs": knowledge_count,
                "interfaces": interface_count,
                "logs": log_count,
                "combined_length": len(combined_context),
                "query_length": len(query_text),
                "requirement_length": len(requirement_text),
            },
        }


context_orchestrator = ContextOrchestrator()
