"""Business service for UI test case routes."""

from __future__ import annotations

from typing import Any

from core.db.models import UITestCase
from modules.automation_components.repositories.ui_test_case_repository import UITestCaseRepository


class UITestCaseService:
    """Use-case layer for UI test case CRUD operations."""

    def __init__(self, db):
        self.repo = UITestCaseRepository(db)

    def has_owned_project(self, *, project_id: int, user_id: int) -> bool:
        return bool(self.repo.get_owned_project(project_id=project_id, user_id=user_id))

    def list_cases(self, *, project_id: int, user_id: int) -> tuple[str, list[UITestCase]]:
        if not self.has_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", []
        return "ok", self.repo.list_project_cases(project_id=project_id)

    def create_case(self, *, payload: dict[str, Any], user_id: int) -> tuple[str, UITestCase | None]:
        project_id = int(payload.get("project_id") or 0)
        if not self.has_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None
        row = UITestCase(**payload)
        self.repo.add(row)
        self.repo.commit()
        self.repo.refresh(row)
        return "created", row

    def update_case(self, *, item_id: int, payload: dict[str, Any], user_id: int) -> tuple[str, UITestCase | None]:
        row = self.repo.get_owned_case(item_id=item_id, user_id=user_id)
        if not row:
            return "not_found", None
        for key, value in payload.items():
            setattr(row, key, value)
        self.repo.commit()
        self.repo.refresh(row)
        return "updated", row

    def delete_case(self, *, item_id: int, user_id: int) -> bool:
        row = self.repo.get_owned_case(item_id=item_id, user_id=user_id)
        if not row:
            return False
        self.repo.delete(row)
        self.repo.commit()
        return True

