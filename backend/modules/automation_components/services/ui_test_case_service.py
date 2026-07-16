"""Business service for UI test case routes."""

from __future__ import annotations

from typing import Any

from core.db.models import UITestCase
from modules.automation_components.repositories.ui_test_case_repository import UITestCaseRepository
from modules.automation_components.services.ui_automation_export_service import UIAutomationExportService


class UITestCaseService:
    """Use-case layer for UI test case CRUD operations."""

    MAX_DEPTH = 3

    def __init__(self, db, exporter: UIAutomationExportService | None = None):
        self.repo = UITestCaseRepository(db)
        self.exporter = exporter or UIAutomationExportService()

    def has_owned_project(self, *, project_id: int, user_id: int) -> bool:
        return bool(self.repo.get_owned_project(project_id=project_id, user_id=user_id))

    def list_cases(self, *, project_id: int, user_id: int) -> tuple[str, list[dict[str, Any]]]:
        project = self.repo.get_owned_project(project_id=project_id, user_id=user_id)
        if not project:
            return "project_not_found", []
        rows = self.repo.list_project_cases(project_id=project_id)
        code_paths = self.exporter.get_project_code_paths(
            project_id=project_id,
            project_name=project.name,
            cases=rows,
        )
        return (
            "ok",
            [
                {
                    "id": row.id,
                    "project_id": row.project_id,
                    "name": row.name,
                    "type": row.type,
                    "parent_id": row.parent_id,
                    "description": row.description,
                    "script_content": row.script_content,
                    "requirements": row.requirements,
                    "automation_type": row.automation_type,
                    "target_config": row.target_config,
                    "code_path": code_paths.get(int(row.id)),
                    "children": [],
                }
                for row in rows
            ],
        )

    @classmethod
    def validate_hierarchy(
        cls,
        *,
        rows: list[UITestCase],
        moving_id: int | None = None,
        parent_id: int | None = None,
        node_type: str | None = None,
    ) -> None:
        """统一校验三级代码树：前两级为文件夹，第三级只允许脚本文件。"""
        by_id = {int(row.id): row for row in rows if row.id is not None}
        if parent_id is not None:
            parent = by_id.get(int(parent_id))
            if parent is None or parent.type != "folder":
                raise ValueError("目标父节点不存在或不是文件夹")
        if moving_id is not None and parent_id == moving_id:
            raise ValueError("节点不能移动到自己下面")

        parent_by_id = {int(row.id): row.parent_id for row in rows if row.id is not None}
        type_by_id = {int(row.id): str(row.type) for row in rows if row.id is not None}
        if moving_id is not None:
            parent_by_id[int(moving_id)] = parent_id
            if node_type:
                type_by_id[int(moving_id)] = node_type

        def depth(item_id: int) -> int:
            current: int | None = item_id
            visited: set[int] = set()
            result = 0
            while current is not None:
                if current in visited:
                    raise ValueError("文件夹不能移动到自己的子层级")
                visited.add(current)
                result += 1
                parent = parent_by_id.get(current)
                current = int(parent) if parent is not None else None
            return result

        for item_id, item_type in type_by_id.items():
            item_depth = depth(item_id)
            if item_depth > cls.MAX_DEPTH:
                raise ValueError("自动化层级最多三级")
            if item_type == "folder" and item_depth >= cls.MAX_DEPTH:
                raise ValueError("第三级只能是自动化脚本文件")

    @staticmethod
    def _validate_name_and_type(payload: dict[str, Any]) -> tuple[str, str]:
        name = str(payload.get("name") or "").strip()
        node_type = str(payload.get("type") or "file").strip().lower()
        if not name:
            raise ValueError("节点名称不能为空")
        if node_type not in {"folder", "file"}:
            raise ValueError("节点类型只能是 folder 或 file")
        return name, node_type

    def _sync_project(self, *, project_id: int, project_name: str) -> dict[int, str]:
        return self.exporter.sync_project_hierarchy(
            project_id=project_id,
            project_name=project_name,
            cases=self.repo.list_project_cases(project_id=project_id),
        )

    def create_case(self, *, payload: dict[str, Any], user_id: int) -> tuple[str, UITestCase | None]:
        project_id = int(payload.get("project_id") or 0)
        project = self.repo.get_owned_project(project_id=project_id, user_id=user_id)
        if not project:
            return "project_not_found", None
        name, node_type = self._validate_name_and_type(payload)
        parent_id = payload.get("parent_id")
        rows = self.repo.list_project_cases(project_id=project_id)
        self.validate_hierarchy(rows=rows, parent_id=parent_id)
        if parent_id is not None:
            probe = UITestCase(id=-1, type=node_type, parent_id=int(parent_id), name=name)
            self.validate_hierarchy(rows=[*rows, probe], moving_id=-1, parent_id=int(parent_id), node_type=node_type)
        if any(row.parent_id == parent_id and row.type == node_type and row.name == name for row in rows):
            raise ValueError("同一层级下已存在同名节点")
        row = UITestCase(**{**payload, "name": name, "type": node_type})
        try:
            self.repo.add(row)
            self.repo.flush()
            self._sync_project(project_id=project_id, project_name=project.name)
            self.repo.commit()
            self.repo.refresh(row)
        except Exception:
            self.repo.rollback()
            raise
        return "created", row

    def update_case(self, *, item_id: int, payload: dict[str, Any], user_id: int) -> tuple[str, UITestCase | None]:
        row = self.repo.get_owned_case(item_id=item_id, user_id=user_id)
        if not row:
            return "not_found", None
        project = self.repo.get_owned_project(project_id=int(row.project_id), user_id=user_id)
        if not project:
            return "not_found", None
        rows = self.repo.list_project_cases(project_id=int(row.project_id))
        parent_id = payload.get("parent_id", row.parent_id)
        name = str(payload.get("name", row.name) or "").strip()
        if not name:
            raise ValueError("节点名称不能为空")
        self.validate_hierarchy(rows=rows, moving_id=int(row.id), parent_id=parent_id, node_type=row.type)
        if any(
            other.id != row.id
            and other.parent_id == parent_id
            and other.type == row.type
            and other.name == name
            for other in rows
        ):
            raise ValueError("同一层级下已存在同名节点")
        try:
            for key, value in payload.items():
                setattr(row, key, value)
            row.name = name
            self.repo.flush()
            self._sync_project(project_id=int(row.project_id), project_name=project.name)
            self.repo.commit()
            self.repo.refresh(row)
        except Exception:
            self.repo.rollback()
            raise
        return "updated", row

    def delete_case(self, *, item_id: int, user_id: int) -> bool:
        row = self.repo.get_owned_case(item_id=item_id, user_id=user_id)
        if not row:
            return False
        self.repo.delete(row)
        self.repo.commit()
        return True

