from __future__ import annotations

import json
from types import SimpleNamespace

from modules.testing.test_generation_components.services.history_service import (
    TestGenerationHistoryService,
)


class _Repo:
    def __init__(self, entry: SimpleNamespace) -> None:
        self.entry = entry

    def get_generation(self, *, generation_id: int) -> SimpleNamespace | None:
        return self.entry if generation_id == self.entry.id else None

    def get_owned_project(self, *, project_id: int, user_id: int) -> object | None:
        return object()


def test_history_generation_preserves_final_case_contract_fields() -> None:
    assert TestGenerationHistoryService.__test__ is False

    cases = [
        {
            "id": "TC-001",
            "description": "create plan",
            "test_module": "schedule",
            "preconditions": ["teacher logged in"],
            "steps": ["open schedule", "save plan"],
            "test_input": "valid plan",
            "expected_result": "plan is saved",
            "priority": "P0",
            "priority_final": "P0",
            "workflow_id": "schedule-main",
            "source_state": "course_selected",
            "target_state": "plan_saved",
            "execution_group": "main_smoke",
            "role": "teacher",
            "session_key": "teacher_session",
        }
    ]
    entry = SimpleNamespace(
        id=473,
        project_id=8,
        user_id=9,
        requirement_text="schedule",
        generated_result=json.dumps(cases, ensure_ascii=False),
        created_at=None,
    )
    service = TestGenerationHistoryService(db=object())
    service.repo = _Repo(entry)

    status, payload = service.get_generation(generation_id=473, user_id=9)

    assert status == "ok"
    assert isinstance(payload, list)
    assert payload[0]["priority_final"] == "P0"
    assert payload[0]["workflow_id"] == "schedule-main"
    assert payload[0]["execution_group"] == "main_smoke"
    assert payload[0]["session_key"] == "teacher_session"
