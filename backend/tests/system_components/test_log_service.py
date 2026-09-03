from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from modules.system_components.services.log_service import LogService


def test_agent_events_only_include_entries_after_latest_clear_marker() -> None:
    marker = datetime.utcnow()
    event = SimpleNamespace(
        id=7,
        event_type="node_completed",
        payload={"node_key": "generation"},
        created_at=marker + timedelta(seconds=1),
    )

    class _ProjectRepository:
        @staticmethod
        def get_owned_project(**_: int) -> object:
            return object()

    class _LogRepository:
        @staticmethod
        def list_project_logs(**_: object) -> list[object]:
            return []

        @staticmethod
        def latest_agent_event_clear_id(**_: int) -> int:
            return 6

        @staticmethod
        def list_agent_run_events(**arguments: object) -> list[object]:
            assert arguments["id_after"] == 6
            return [event]

    service = LogService.__new__(LogService)
    service.project_repo = _ProjectRepository()
    service.log_repo = _LogRepository()

    result = service.get_project_logs(project_id=3, user_id=5, limit=20)

    assert result is not None
    assert len(result) == 1
    assert result[0]["id"] == -7
    assert result[0]["created_at"] > marker
