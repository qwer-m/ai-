from __future__ import annotations

import json
from types import SimpleNamespace

from modules.testing.test_generation_components.services.history_service import (
    TestGenerationHistoryService,
)
from modules.test_generation_components.services import history_service as history_service_module


class _Repo:
    def __init__(self, entry: SimpleNamespace) -> None:
        self.entry = entry

    def get_generation(self, *, generation_id: int) -> SimpleNamespace | None:
        return self.entry if generation_id == self.entry.id else None

    def get_owned_project(self, *, project_id: int, user_id: int) -> object | None:
        return object()


class _ListRepo:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows

    def get_owned_project(self, *, project_id: int, user_id: int) -> object | None:
        return object()

    def list_project_generations(self, *, project_id: int) -> list[SimpleNamespace]:
        return [row for row in self.rows if row.project_id == project_id]


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


def test_history_bundle_includes_execution_suite_without_changing_generation_payload(monkeypatch) -> None:
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
            "execution_group": "main_smoke",
            "execution_sequence": 1,
            "chain_id": "main_smoke_chain",
            "role": "teacher",
            "session_key": "teacher_session",
        },
        {
            "id": "TC-002",
            "description": "student sees plan",
            "test_module": "student home",
            "preconditions": ["plan saved"],
            "steps": ["open home"],
            "test_input": "student account",
            "expected_result": "plan is visible",
            "priority": "P0",
            "priority_final": "P0",
            "execution_group": "main_smoke",
            "execution_sequence": 2,
            "chain_id": "main_smoke_chain",
            "depends_on": ["TC-001"],
            "role": "student",
            "session_key": "student_session",
        },
    ]
    entry = SimpleNamespace(
        id=474,
        project_id=8,
        user_id=9,
        requirement_text="schedule",
        generated_result=json.dumps(cases, ensure_ascii=False),
        created_at=None,
    )
    service = TestGenerationHistoryService(db=object())
    service.repo = _Repo(entry)
    monkeypatch.setattr(history_service_module, "find_matching_comparison", lambda **_kwargs: None)
    monkeypatch.setattr(history_service_module, "load_compare_artifact_payload", lambda **_kwargs: None)

    status, payload = service.get_bundle(generation_id=474, user_id=9)

    assert status == "ok"
    assert payload is not None
    assert payload["generation"]["generated_result"] == entry.generated_result
    execution_suite = payload["execution_suite"]
    assert execution_suite["linear_executable"] is True
    assert execution_suite["suites"][0]["suite_id"] == "main_smoke_chain"
    assert [item["case_id"] for item in execution_suite["suites"][0]["cases"]] == ["TC-001", "TC-002"]


def test_history_list_includes_execution_suite_summary(monkeypatch) -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "create plan",
            "test_module": "schedule",
            "preconditions": ["teacher logged in"],
            "steps": ["save plan"],
            "test_input": "valid plan",
            "expected_result": "plan is saved",
            "priority": "P0",
            "priority_final": "P0",
            "execution_group": "main_smoke",
            "execution_sequence": 1,
            "chain_id": "main_smoke_chain",
            "role": "teacher",
            "session_key": "teacher_session",
        },
        {
            "id": "TC-002",
            "description": "student sees plan",
            "test_module": "student home",
            "preconditions": ["plan saved"],
            "steps": ["open home"],
            "test_input": "student account",
            "expected_result": "plan is visible",
            "priority": "P0",
            "priority_final": "P0",
            "execution_group": "main_smoke",
            "execution_sequence": 2,
            "chain_id": "main_smoke_chain",
            "depends_on": ["TC-001"],
            "role": "student",
            "session_key": "student_session",
        },
    ]
    row = SimpleNamespace(
        id=475,
        project_id=8,
        user_id=9,
        requirement_text="schedule",
        generated_result=json.dumps(cases, ensure_ascii=False),
        created_at=None,
    )
    service = TestGenerationHistoryService(db=object())
    service.repo = _ListRepo([row])
    monkeypatch.setattr(history_service_module, "find_matching_comparison", lambda **_kwargs: None)
    monkeypatch.setattr(history_service_module, "load_compare_artifact_payload", lambda **_kwargs: None)

    status, rows = service.list_generations(project_id=8, user_id=9)

    assert status == "ok"
    assert len(rows) == 1
    summary = rows[0]["execution_suite_summary"]
    assert summary["case_count"] == 2
    assert summary["suite_count"] == 1
    assert summary["runnable_suite_count"] == 1
    assert summary["linear_executable"] is True
    assert summary["execution_readiness"] == "ready"
    assert summary["warning_count"] == 0
    assert summary["main_suite_id"] == "main_smoke_chain"
