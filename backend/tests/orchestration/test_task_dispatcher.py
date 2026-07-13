from __future__ import annotations

from modules.orchestration.task_dispatcher import enqueue_task
from modules.orchestration.task_names import TaskName


class FakeTaskRuntime:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def dispatch(self, *, task_name: str, kwargs: dict | None = None) -> str:
        self.calls.append({"task_name": task_name, "kwargs": kwargs or {}})
        return "task-123"


def test_enqueue_task_returns_unified_dispatch_result(monkeypatch) -> None:
    runtime = FakeTaskRuntime()
    monkeypatch.setattr(
        "modules.orchestration.task_dispatcher.get_task_runtime",
        lambda: runtime,
    )

    result = enqueue_task(
        TaskName.GENERATE_TEST_CASES,
        kwargs={"project_id": 7},
        business_type="test_generation",
        business_id=7,
        reason="generate_tests_async",
    )

    assert runtime.calls == [
        {
            "task_name": "modules.orchestration.tasks.generate_test_cases_task",
            "kwargs": {"project_id": 7},
        }
    ]
    assert result.id == "task-123"
    assert result.to_dict() == {
        "queued": True,
        "status": "PENDING",
        "task_id": "task-123",
        "task_name": "modules.orchestration.tasks.generate_test_cases_task",
        "reason": "generate_tests_async",
        "business_type": "test_generation",
        "business_id": 7,
        "queue": "celery",
        "error": "",
    }
