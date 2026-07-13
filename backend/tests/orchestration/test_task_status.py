from __future__ import annotations

import asyncio
from types import SimpleNamespace

from modules.orchestration.task_status import (
    build_task_status_payload,
    get_runtime_task_status,
)
from routers.system import tasks as task_routes
from routers.system.common_responses import build_parse_status_response


class BrokenTaskRuntime:
    def get_status(self, *, task_id: str) -> dict:
        raise RuntimeError(f"redis unavailable for {task_id}")


def test_get_runtime_task_status_normalizes_lookup_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        "modules.orchestration.task_status.get_task_runtime",
        lambda: BrokenTaskRuntime(),
    )

    result = get_runtime_task_status("task-err")

    assert result["task_id"] == "task-err"
    assert result["status"] == "UNKNOWN"
    assert result["result"] is None
    assert "redis unavailable" in result["error"]


def test_build_parse_status_response_includes_business_task_status() -> None:
    doc = SimpleNamespace(
        id=99,
        project_specific_id=7,
        parse_status="parsing",
        parse_error=None,
        parsed_at=None,
        task_id="task-99",
        retry_count=1,
    )

    response = build_parse_status_response(
        doc,
        "STARTED",
        task_status={"task_id": "task-99", "status": "STARTED", "result": None},
    )

    assert response["task_state"] == "STARTED"
    assert response["parse_status"] == "parsing"
    assert response["task_status"] == {
        "task_id": "task-99",
        "status": "STARTED",
        "result": None,
        "queue": "celery",
        "business_type": "knowledge_document",
        "business_id": 99,
        "business_status": "parsing",
        "retry_count": 1,
    }


def test_build_parse_status_response_keeps_legacy_fields_without_task() -> None:
    doc = SimpleNamespace(
        id=100,
        project_specific_id=None,
        parse_status="pending",
        parse_error="",
        parsed_at=None,
        task_id=None,
        retry_count=0,
    )

    response = build_parse_status_response(doc, None)

    assert set(response) == {
        "id",
        "global_id",
        "parse_status",
        "parse_error",
        "parsed_at",
        "task_id",
        "retry_count",
        "task_state",
    }
    assert response["id"] == 100
    assert response["global_id"] == 100
    assert response["task_id"] is None
    assert response["task_state"] is None


def test_build_parse_status_response_preserves_unknown_runtime_error() -> None:
    doc = SimpleNamespace(
        id=101,
        project_specific_id=8,
        parse_status="parsing",
        parse_error=None,
        parsed_at=None,
        task_id="task-unknown",
        retry_count=2,
    )

    response = build_parse_status_response(
        doc,
        "UNKNOWN",
        task_status={
            "task_id": "task-unknown",
            "status": "UNKNOWN",
            "result": None,
            "error": "redis unavailable",
        },
    )

    assert set(response) == {
        "id",
        "global_id",
        "parse_status",
        "parse_error",
        "parsed_at",
        "task_id",
        "retry_count",
        "task_state",
        "task_status",
    }
    assert response["task_state"] == "UNKNOWN"
    assert response["task_status"]["error"] == "redis unavailable"
    assert response["task_status"]["business_type"] == "knowledge_document"
    assert response["task_status"]["business_id"] == 101
    assert response["task_status"]["business_status"] == "parsing"


def test_build_task_status_payload_keeps_runtime_fields() -> None:
    payload = build_task_status_payload(
        task_id="task-1",
        task_status={"task_id": "task-1", "status": "FAILURE", "error": "boom"},
        business_type="test_generation",
        business_id=3,
        business_status="failed",
    )

    assert payload["status"] == "FAILURE"
    assert payload["error"] == "boom"
    assert payload["business_type"] == "test_generation"
    assert payload["business_status"] == "failed"


def test_build_task_status_payload_without_task_id_is_not_queued() -> None:
    payload = build_task_status_payload(
        task_id=None,
        business_type="knowledge_document_parse",
        business_id=5,
        business_status="pending",
    )

    assert payload["status"] == "NOT_QUEUED"
    assert payload["queue"] == "celery"
    assert payload["business_id"] == 5


def test_get_task_status_route_keeps_runtime_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        task_routes,
        "get_runtime_task_status",
        lambda task_id: {
            "task_id": task_id,
            "status": "STARTED",
            "result": None,
            "status_text": "running",
        },
    )

    response = asyncio.run(task_routes.get_task_status("task-started"))

    assert response["task_id"] == "task-started"
    assert response["status"] == "STARTED"
    assert response["result"] is None
    assert response["status_text"] == "running"
    assert response["queue"] == "celery"
