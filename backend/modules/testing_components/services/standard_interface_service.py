"""Business service for standard interface routes."""

from __future__ import annotations

from typing import Any, Optional

from core.ai.ai_client import get_client_for_user
from core.db.model_defs import StandardInterface
from modules.testing_components.repositories.standard_interface_repository import (
    StandardInterfaceRepository,
)


class StandardInterfaceService:
    """Use-case layer for standard interface CRUD and AI analysis."""

    def __init__(self, db):
        self.repo = StandardInterfaceRepository(db)
        self._db = db

    def list_interfaces(self, *, user_id: int, project_id: Optional[int] = None) -> list[StandardInterface]:
        return self.repo.list_interfaces(user_id=user_id, project_id=project_id)

    def create_interface(
        self,
        *,
        payload: dict[str, Any],
        user_id: int,
    ) -> tuple[Optional[StandardInterface], str]:
        project_id = payload.get("project_id")
        if project_id and not self.repo.get_owned_project(project_id=project_id, user_id=user_id):
            return None, "project_not_found"

        row = StandardInterface(**payload, user_id=user_id)
        self.repo.add(row)
        self.repo.commit()
        self.repo.refresh(row)
        return row, "created"

    def update_interface(
        self,
        *,
        interface_id: int,
        payload: dict[str, Any],
        user_id: int,
    ) -> tuple[Optional[StandardInterface], str]:
        row = self.repo.get_owned_interface(interface_id=interface_id, user_id=user_id)
        if not row:
            return None, "not_found"

        if "project_id" in payload:
            project_id = payload.get("project_id")
            if project_id and not self.repo.get_owned_project(project_id=project_id, user_id=user_id):
                return None, "project_not_found"

        for key, value in payload.items():
            setattr(row, key, value)

        self.repo.add(row)
        self.repo.commit()
        self.repo.refresh(row)
        return row, "updated"

    def delete_interface(self, *, interface_id: int, user_id: int) -> bool:
        row = self.repo.get_owned_interface(interface_id=interface_id, user_id=user_id)
        if not row:
            return False
        self.repo.delete(row)
        self.repo.commit()
        return True

    def analyze_response(self, *, transaction: dict[str, Any], user_id: int) -> str:
        client = get_client_for_user(user_id, self._db)
        prompt = f"""
    Please analyze the following HTTP API transaction and provide a brief report.

    Request:
    {transaction.get("method")} {transaction.get("url")}
    Headers: {transaction.get("headers")}
    Body: {(transaction.get("body") or "None")[:1000] if isinstance(transaction.get("body"), str) else transaction.get("body") or "None"}

    Response:
    Status: {transaction.get("response_status")}
    Headers: {transaction.get("response_headers")}
    Body: {(transaction.get("response_body") or "None")[:2000] if isinstance(transaction.get("response_body"), str) else transaction.get("response_body") or "None"}
    Error: {transaction.get("error") or "None"}

    Analysis Requirements:
    1. Summarize what happened.
    2. Identify any errors or potential issues (Status code, format, security headers, performance).
    3. If there is an error, explain the likely cause and how to fix it.
    4. Provide suggestions for improvement.

    Output Format: Markdown.
    """
        return client.generate_response(
            prompt,
            system_prompt="You are an expert API Testing Assistant.",
            db=self._db,
        )

