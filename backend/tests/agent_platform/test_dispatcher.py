from types import SimpleNamespace

from modules.agent_platform.dispatcher import (
    _can_fail_unclaimed_run,
    _can_record_dispatched_task,
)


def test_dispatch_failure_only_terminates_unclaimed_pending_run() -> None:
    assert _can_fail_unclaimed_run(
        SimpleNamespace(status="pending", task_id=None, claim_token=None)
    )
    assert not _can_fail_unclaimed_run(
        SimpleNamespace(status="running", task_id=None, claim_token="direct-run-35")
    )
    assert not _can_fail_unclaimed_run(
        SimpleNamespace(status="pending", task_id="celery-task-35", claim_token=None)
    )
    assert not _can_fail_unclaimed_run(
        SimpleNamespace(status="success", task_id=None, claim_token=None)
    )


def test_dispatch_result_does_not_replace_another_executor_claim() -> None:
    assert _can_record_dispatched_task(
        SimpleNamespace(status="pending", claim_token=None),
        "celery-task-35",
    )
    assert _can_record_dispatched_task(
        SimpleNamespace(status="running", claim_token="celery-task-35"),
        "celery-task-35",
    )
    assert not _can_record_dispatched_task(
        SimpleNamespace(status="running", claim_token="direct-run-35"),
        "celery-task-35",
    )
    assert not _can_record_dispatched_task(
        SimpleNamespace(status="success", claim_token=None),
        "celery-task-35",
    )
